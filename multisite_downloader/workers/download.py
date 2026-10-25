"""下载线程：yt-dlp / B站兜底、进度上报、取消与临时文件清理。"""

import concurrent.futures
import re
import subprocess
import threading
import time
from pathlib import Path

import requests
import yt_dlp
from PyQt5.QtCore import QThread, pyqtSignal

from ..bilibili import (
    _build_bili_session,
    bili_playurl,
    bili_view,
    download_bili_subtitles,
    download_bili_thumbnail,
    fetch_bili_danmaku_for_url,
    resolve_bili_login,
    resolve_bilibili_url,
)
from ..config import QUALITY_HEIGHTS, QUALITY_QN, ffmpeg_path, has_ffmpeg
from ..cookies import has_cookie_for_url
from ..errors import format_bili_error, format_error, format_site_error
from ..logs import write_crash_log
from ..media import has_no_audio, media_info_for_file
from ..net import (
    QuietYtdlpLogger,
    apply_cookie_and_proxy_options,
    js_runtime_options,
    std_headers,
)
from ..sites import SITE_DOUYIN, detect_site
from ..urls import (
    extract_video_id,
    is_bilibili_url,
    normalize_input,
    selected_page_number,
)
from ..utils import (
    extract_output_path,
    render_filename_template,
    sanitize_filename,
    unique_path,
)


class DownloadWorker(QThread):
    item_started = pyqtSignal(int, str)
    item_progress = pyqtSignal(int, float, str)
    item_finished = pyqtSignal(int, str, str)
    item_failed = pyqtSignal(int, str)
    item_skipped = pyqtSignal(int, str)
    item_cancelled = pyqtSignal(int)
    item_history = pyqtSignal(dict)
    log = pyqtSignal(str)
    all_done = pyqtSignal(bool)
    paused_changed = pyqtSignal(bool)

    def __init__(self, inputs, settings, parent=None, task_settings=None, active_indices=None):
        super().__init__(parent)
        self.inputs = inputs
        self.settings = settings
        self.task_settings = list(task_settings) if task_settings else [settings] * len(inputs)
        if len(self.task_settings) < len(inputs):
            self.task_settings.extend(
                [settings] * (len(inputs) - len(self.task_settings))
            )
        self.active_indices = (
            list(active_indices)
            if active_indices is not None
            else list(range(len(inputs)))
        )
        self.cancelled = False
        self.paused = False
        self.skip_indices = set()
        self.current_titles = {}
        # 每个下载线程各自的当前输出文件名（并发下载时不能共用同一个属性，否则会串号）
        self._local = threading.local()
        # 本次任务产生的临时文件与已完成的最终文件，用于取消后精确清理
        self._temp_files = set()
        self._temp_lock = threading.Lock()
        self._finished_outputs = set()
        # 已经进入处理流程的任务索引，取消时用于标记真正没开始的任务
        self._started_indices = set()
        self._state_lock = threading.Lock()
        # B站登录按任务设置缓存；不同任务可能有不同 Cookie 来源
        self._bili_login_cache = {}
        self._bili_login_lock = threading.Lock()

    def current_settings(self):
        """当前下载线程使用的设置；未设置时回退到创建 worker 时的全局设置。"""
        settings = getattr(self._local, "settings", None)
        return settings if settings is not None else self.settings

    def _track_temp(self, path):
        """登记临时文件路径（含 .part/.ytdl/.temp 变体）。"""
        if not path:
            return
        base = str(path)
        with self._temp_lock:
            self._temp_files.add(base)
            for suffix in (".part", ".ytdl", ".temp"):
                self._temp_files.add(base + suffix)

    def cancel(self):
        self.cancelled = True
        self.paused = False

    def pause(self):
        if not self.cancelled and not self.paused:
            self.paused = True
            self.paused_changed.emit(True)

    def resume(self):
        if self.paused:
            self.paused = False
            self.paused_changed.emit(False)

    def skip_index(self, index):
        self.skip_indices.add(index)

    def wait_while_paused(self, index):
        while self.paused and not self.cancelled:
            time.sleep(0.2)
        return not self.cancelled and index not in self.skip_indices

    def wait_for_resume(self):
        """下载过程中暂停：阻塞当前下载线程，取消时抛出异常。"""
        while self.paused and not self.cancelled:
            time.sleep(0.2)
        if self.cancelled:
            raise RuntimeError("用户取消下载")

    def run(self):
        ok = True
        with self._state_lock:
            self._started_indices.clear()
        try:
            workers = max(1, int(self.current_settings().get("concurrent_downloads", 1)))
            if workers == 1:
                for index in self.active_indices:
                    raw = self.inputs[index]
                    status = self._process_one(index, raw)
                    if status == "cancelled":
                        ok = False
                        break
                    if status == "failed":
                        ok = False
            else:
                ok = self._run_concurrent(workers)
        except Exception as exc:
            write_crash_log(type(exc), exc, exc.__traceback__, source="DownloadWorker")
            ok = False
            try:
                self.log.emit(f"下载线程异常: {format_error(exc)}")
            except Exception:
                pass

        try:
            if self.cancelled:
                # 取消时把还没开始的任务也明确标成已取消，
                # 否则队列里会永远剩下“等待”的行，进度也永远算不完。
                with self._state_lock:
                    started = set(self._started_indices)
                for index in self.active_indices:
                    if index in started:
                        continue
                    try:
                        self.item_cancelled.emit(index)
                    except Exception:
                        pass
                self.cleanup_temp_files()
            self.all_done.emit(ok and not self.cancelled)
        except Exception as exc:
            write_crash_log(type(exc), exc, exc.__traceback__, source="DownloadWorker.all_done")

    def _process_one(self, index, raw):
        """处理单个下载任务。返回: 'ok' | 'failed' | 'cancelled' | 'skipped'。"""
        if self.cancelled:
            return "cancelled"
        settings = self.task_settings[index] if index < len(self.task_settings) else self.settings
        self._local.settings = settings
        try:
            Path(settings.get("download_dir") or ".").mkdir(
                parents=True, exist_ok=True
            )
        except Exception as exc:
            self.item_failed.emit(index, f"无法创建下载目录：{format_error(exc)}")
            return "failed"
        with self._state_lock:
            self._started_indices.add(index)
        if index in self.skip_indices:
            self.item_skipped.emit(index, "已从队列删除")
            return "skipped"
        if not self.wait_while_paused(index):
            if self.cancelled:
                self.item_cancelled.emit(index)
                return "cancelled"
            self.item_skipped.emit(index, "已从队列删除")
            return "skipped"

        url = normalize_input(raw)
        if not url:
            self.item_skipped.emit(index, "链接为空")
            return "skipped"

        self._local.actual_quality = ""
        self.current_titles[index] = url
        self.item_started.emit(index, url)
        self.log.emit(f"开始处理: {url}")
        started_at = int(time.time())
        try:
            if detect_site(url) == SITE_DOUYIN:
                outputs = self.download_douyin_browser(index, url)
                output_detail = self.output_detail(outputs)
            elif self.should_use_bili_legacy(url, validate=True):
                outputs = self.download_bili_legacy(index, url)
                output_detail = self.output_detail(outputs)
            else:
                output = self.download_with_ytdlp(index, url)
                output_detail = self.output_detail([output])
            # 下载弹幕
            if self.current_settings().get("download_danmaku") and is_bilibili_url(url):
                self.item_progress.emit(index, 100, "正在下载弹幕...")
                output_base = self._output_base_for_danmaku(output_detail)
                if output_base:
                    if fetch_bili_danmaku_for_url(url, output_base, self.current_settings()):
                        self.log.emit(f"弹幕已保存: {output_base}.danmaku.xml")
                    else:
                        self.log.emit("弹幕下载失败或无弹幕")
            self.item_finished.emit(index, output_detail, "完成")
            self.log.emit(f"完成: {output_detail}")
            self.emit_history(index, url, output_detail, "completed", "", started_at)
            return "ok"
        except Exception as exc:
            if self.cancelled:
                self.item_cancelled.emit(index)
                self.emit_history(index, url, "", "cancelled", "已取消", started_at)
                return "cancelled"
            if self.should_try_bili_fallback(url, exc):
                self.log.emit("yt-dlp 未能完成下载，回退到公开视频兜底接口...")
                self._cleanup_current_task_temp()
                try:
                    outputs = self.download_bili_legacy(index, url)
                    output_text = self.output_detail(outputs)
                    if self.current_settings().get("download_danmaku"):
                        self.item_progress.emit(index, 100, "正在下载弹幕...")
                        output_base = self._output_base_for_danmaku(output_text)
                        if output_base and fetch_bili_danmaku_for_url(url, output_base, self.current_settings()):
                            self.log.emit(f"弹幕已保存: {output_base}.danmaku.xml")
                    self.item_finished.emit(index, output_text, "完成（公开视频兜底）")
                    self.log.emit(f"兜底完成: {output_text}")
                    self.emit_history(index, url, output_text, "completed", "", started_at)
                    return "ok"
                except Exception as fallback_exc:
                    if self.cancelled:
                        self.item_cancelled.emit(index)
                        self.emit_history(index, url, "", "cancelled", "已取消", started_at)
                        return "cancelled"
                    err_text = format_site_error(fallback_exc, url)
                    self.item_failed.emit(index, err_text)
                    self.log.emit(f"兜底失败: {err_text}")
                    self.emit_history(index, url, "", "failed", err_text, started_at)
                    return "failed"
            err_text = format_site_error(exc, url)
            self.item_failed.emit(index, err_text)
            self.log.emit(f"失败: {err_text}")
            self.emit_history(index, url, "", "failed", err_text, started_at)
            return "failed"

    def _run_concurrent(self, max_workers):
        """并发下载多个任务。返回 True 表示全部成功。"""
        ok = True
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._process_one, index, self.inputs[index]): index
                for index in self.active_indices
            }
            for future in concurrent.futures.as_completed(futures):
                try:
                    status = future.result()
                    if status in ("failed", "cancelled"):
                        ok = False
                        if status == "cancelled":
                            for f in futures:
                                f.cancel()
                except Exception as exc:
                    write_crash_log(type(exc), exc, exc.__traceback__, source="DownloadWorker.concurrent")
                    ok = False
                    try:
                        self.log.emit(f"并发下载异常: {format_error(exc)}")
                    except Exception:
                        pass
        return ok

    def emit_history(self, index, url, output_detail, status, error, started_at):
        title = self.current_titles.get(index) or url
        record = {
            "title": title,
            "url": url,
            "output_detail": output_detail,
            "output_path": extract_output_path(output_detail),
            "status": status,
            "error": error,
            "format_id": (self.current_settings().get("custom_format") or "").strip(),
            "quality": getattr(self._local, "actual_quality", "")
            or self.current_settings().get("quality", ""),
            "created_at": started_at,
            "finished_at": int(time.time()),
        }
        output_path = record["output_path"]
        if output_path and Path(output_path).exists():
            info = media_info_for_file(output_path)
            record.update({
                "file_size": info.get("file_size", 0),
                "duration": info.get("duration", ""),
                "resolution": f"{info.get('width', '')}x{info.get('height', '')}" if info.get("width") else "",
                "fps": info.get("fps", ""),
                "video_codec": info.get("video_codec", ""),
                "audio_codec": info.get("audio_codec", ""),
            })
            if has_no_audio(info):
                # 兜底提醒：任何路径下出现无声视频都能被发现
                record["warning"] = "无音轨"
                self.log.emit(
                    "⚠ 该文件没有音轨（所选格式可能是纯视频流）。"
                    "重新选择带「推荐」的「DASH组合」行再下载即可带上声音。"
                )
        self.item_history.emit(record)

    def output_detail(self, outputs):
        return "\n".join(str(o) for o in outputs if o)

    def _output_base_for_danmaku(self, output_detail):
        """从输出详情中提取弹幕文件的基础路径（去掉扩展名）。"""
        path = extract_output_path(output_detail)
        if not path:
            return ""
        p = Path(path)
        return str(p.parent / p.stem)

    def _check_bili_login(self):
        """验证当前任务的 B站登录（并静默尝试浏览器 Cookie）。"""
        settings = self.current_settings()
        key = (
            settings.get("cookie_mode") or "none",
            settings.get("cookie_file") or "",
            settings.get("site_cookie_bilibili") or "",
            settings.get("proxy") or "",
            bool(settings.get("auto_browser_login", True)),
        )
        with self._bili_login_lock:
            if key in self._bili_login_cache:
                return self._bili_login_cache[key]
            try:
                ok, source = resolve_bili_login(
                    settings,
                    allow_browser=bool(
                        settings.get("auto_browser_login", True)
                    ),
                )
            except Exception as exc:
                ok, source = False, ""
                self.log.emit(f"B站登录检测失败: {format_error(exc)}")
            self._bili_login_cache[key] = ok
        if ok:
            self.log.emit(f"B站登录有效（{source}），按高清格式下载")
        else:
            self.log.emit(
                "B站未登录：本次使用公开接口，最高约 720P。"
                "登录后可在预览区重新解析获取 1080P+。"
            )
        return ok

    def should_use_bili_legacy(self, url, validate=False):
        """是否直接用公开视频兜底接口。

        无 Cookie 时 yt-dlp 请求 B站 API 经常被 412 拦截，且 quiet 模式下错误被
        吞掉（download() 返回成功但没生成文件），所以无 Cookie 直接走兜底；
        配置了 Cookie 且 ffmpeg 可用时优先走 yt-dlp（DASH 合并、封面、字幕、
        文件名模板等能力只有 yt-dlp 路径才有），失败后再回退兜底接口。

        validate=True 时不只看“有没有 Cookie”，而是实际请求 nav 校验登录，
        并静默尝试浏览器 Cookie；这样失效 Cookie 不会再把任务带进 yt-dlp 失败路径。
        """
        if not is_bilibili_url(url):
            return False
        if not has_ffmpeg():
            return True
        if validate:
            return not self._check_bili_login()
        # 与下载/接口使用同一套 Cookie 来源：站点专用、Cookie 库、全局文件/浏览器。
        has_cookie = has_cookie_for_url(url, self.current_settings())
        return not (has_cookie and has_ffmpeg())

    @staticmethod
    def _is_intermediate_file(path):
        name = Path(str(path)).name
        return bool(re.search(r"\.f\d+\.", name)) or name.endswith(
            (".part", ".ytdl", ".temp")
        )

    def should_try_bili_fallback(self, url, exc):
        """B站任务失败时是否回退到公开视频兜底接口。

        只有「还没拿到成品文件」的失败才回退。yt-dlp 已经合并/产出文件后，
        再回退会额外下载一份不同清晰度的文件，造成同一个视频出现两个文件。
        """
        if not is_bilibili_url(url) or self.cancelled:
            return False
        # 兜底接口只返回 durl 单文件直链，不支持仅音频
        if (self.current_settings().get("quality") or "") == "audio":
            return False
        final_candidate = getattr(self._local, "final_candidate", "")
        if (
            final_candidate
            and Path(final_candidate).is_file()
            and not self._is_intermediate_file(final_candidate)
        ):
            # 已经有正常成品：带音轨就不再回退；纯视频流才需要兜底重新下载
            info = media_info_for_file(final_candidate)
            if not has_no_audio(info):
                return False
            # 无音轨的中间成品先删掉，否则兜底保存时会变成同名 (1).mp4
            try:
                Path(final_candidate).unlink()
            except OSError:
                pass
        message = str(exc).lower()
        if any(
            key in message
            for key in ("ffmpeg", "merge", "postprocess", "postprocessing", "合并")
        ):
            return False
        return True

    def _cleanup_current_task_temp(self):
        """回退前清掉当前任务由 yt-dlp 写下的中间流，避免留下第二份文件。"""
        names = set(getattr(self._local, "files", set()) or set())
        current = getattr(self._local, "filename", "")
        if current:
            names.add(current)
        final_candidate = getattr(self._local, "final_candidate", "")
        for name in names:
            if not name:
                continue
            if name == final_candidate and not self._is_intermediate_file(name):
                continue
            for suffix in ("", ".part", ".ytdl", ".temp"):
                path = Path(str(name) + suffix)
                if not path.is_file() or str(path) in self._finished_outputs:
                    continue
                try:
                    path.unlink()
                except OSError:
                    pass

    def build_ytdlp_options(self, index):
        opts = {
            "quiet": True,
            "no_warnings": True,
            "continuedl": True,
            "noprogress": True,
            "concurrent_fragment_downloads": int(self.current_settings().get("fragment_threads", 4)),
            "outtmpl": str(Path(self.current_settings()["download_dir"]) / self.current_settings()["filename_template"]),
            "logger": QuietYtdlpLogger(),
            "http_headers": std_headers(self.current_titles.get(index, "")),
            "progress_hooks": [lambda d: self.on_ytdlp_progress(index, d)],
            "postprocessor_hooks": [self.on_ytdlp_postprocess],
        }
        custom_format = (self.current_settings().get("custom_format") or "").strip()
        if custom_format:
            opts["format"] = custom_format
        else:
            quality = self.current_settings().get("quality", "best")
            audio_q = self.current_settings().get("audio_quality", "auto")
            if quality == "audio":
                if audio_q == "high":
                    opts["format"] = "bestaudio[abr>=192]/bestaudio/best"
                elif audio_q == "medium":
                    opts["format"] = "bestaudio[abr>=128]/bestaudio/best"
                else:
                    opts["format"] = "bestaudio/best"
                # 界面上的「MP3」必须是真正的 MP3；bestaudio 往往会得到 m4a/webm。
                if has_ffmpeg():
                    opts["postprocessors"] = [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }]
            elif quality == "best":
                opts["format"] = "bestvideo+bestaudio/best"
            else:
                height = QUALITY_HEIGHTS.get(quality)
                if height is None and str(quality).isdigit():
                    height = int(quality)
                height = height or 1080
                opts["format"] = (
                    f"bestvideo[height<={height}]+bestaudio/"
                    f"best[height<={height}]/best"
                )
            codec = self.current_settings().get("codec_preference", "auto")
            if codec == "h264":
                opts["format_sort"] = ["vcodec:h264"]
            elif codec == "hevc":
                opts["format_sort"] = ["vcodec:hevc"]
            elif codec == "av1":
                opts["format_sort"] = ["vcodec:av1"]
            # 音频质量偏好（非仅音频模式也应用）
            if quality != "audio" and audio_q != "auto":
                if audio_q == "high":
                    opts["format_sort"] = (opts.get("format_sort") or []) + ["abr:192"]
                elif audio_q == "medium":
                    opts["format_sort"] = (opts.get("format_sort") or []) + ["abr:128"]
        if self.current_settings().get("download_thumbnail"):
            opts["writethumbnail"] = True
        if self.current_settings().get("download_subtitle"):
            opts["writesubtitles"] = True
            opts["writeautomaticsub"] = True
            opts["subtitleslangs"] = ["zh-Hans", "zh", "en"]
        speed_limit = int(self.current_settings().get("speed_limit_kb", 0) or 0)
        if speed_limit > 0:
            # yt-dlp 的 ratelimit 单位是字节/秒
            opts["ratelimit"] = speed_limit * 1024
        if has_ffmpeg():
            opts["ffmpeg_location"] = str(ffmpeg_path().parent)
        runtimes = js_runtime_options()
        if runtimes:
            opts["js_runtimes"] = runtimes
        return apply_cookie_and_proxy_options(
            opts, self.current_settings(), self.current_titles.get(index, "")
        )

    def on_ytdlp_progress(self, index, data):
        if self.cancelled:
            raise RuntimeError("用户取消下载")
        if self.paused:
            self.wait_for_resume()
        status = data.get("status")
        filename = data.get("filename") or data.get("tmpfilename") or ""
        if filename:
            self._local.filename = filename
            files = getattr(self._local, "files", None)
            if files is None:
                files = set()
                self._local.files = files
            files.add(filename)
            tmpfilename = data.get("tmpfilename")
            if tmpfilename:
                files.add(tmpfilename)
            self._track_temp(data.get("filename"))
            self._track_temp(tmpfilename)
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            downloaded = data.get("downloaded_bytes") or 0
            percent = downloaded / total * 100 if total else 0
            speed = data.get("speed") or 0
            eta = data.get("eta")
            detail = f"{percent:.1f}%"
            if speed:
                detail += f"  {speed / 1024 / 1024:.2f} MB/s"
            if eta is not None:
                detail += f"  ETA {eta}s"
            self.item_progress.emit(index, percent, detail)
        elif status == "finished":
            self.item_progress.emit(index, 100, "下载完成，正在合并/整理")
        elif status == "error":
            self.item_progress.emit(index, 0, "下载错误")

    def on_ytdlp_postprocess(self, d):
        """后处理钩子：合并/转码完成后更新最终文件名。
        DASH 流下载时 progress_hook 记录的是临时分片文件，合并后会被删除，
        这里用 postprocessor 的 filepath 覆盖为最终输出文件，确保历史记录能取到真实路径。"""
        try:
            filepath = d.get("filepath") or ""
            if filepath:
                self._local.filename = filepath
                self._local.final_candidate = filepath
                self._local.postprocessed = True
        except Exception:
            pass

    def download_with_ytdlp(self, index, url):
        self._local.filename = ""
        self._local.files = set()
        self._local.final_candidate = ""
        self._local.postprocessed = False

        extract_error = None
        info = None
        final = ""
        with yt_dlp.YoutubeDL(self.build_ytdlp_options(index)) as ydl:
            try:
                info = ydl.extract_info(url, download=True)
            except Exception as exc:
                extract_error = exc

            # 优先用 yt-dlp 给出的最终路径，避免 progress 回调停在分片文件上
            if info:
                title = str(info.get("title") or "").strip()
                if title:
                    self.current_titles[index] = title
                try:
                    prepared = ydl.prepare_filename(info)
                except Exception:
                    prepared = ""
                if (
                    prepared
                    and Path(prepared).is_file()
                    and not self._is_intermediate_file(prepared)
                ):
                    final = prepared
                if not final:
                    downloads = info.get("requested_downloads") or []
                    if downloads:
                        filepath = downloads[0].get("filepath") or ""
                        if (
                            filepath
                            and Path(filepath).is_file()
                            and not self._is_intermediate_file(filepath)
                        ):
                            final = filepath

        # 后处理回调/进度回调里记录的路径作为兜底
        if not final:
            candidate = getattr(self._local, "final_candidate", "") or getattr(
                self._local, "filename", ""
            )
            if (
                candidate
                and Path(candidate).is_file()
                and not self._is_intermediate_file(candidate)
            ):
                final = candidate
        if not final:
            candidates = [
                name
                for name in getattr(self._local, "files", set())
                if name
                and Path(name).is_file()
                and not self._is_intermediate_file(name)
            ]
            if candidates:
                final = max(candidates, key=lambda name: Path(name).stat().st_mtime)

        if extract_error is not None:
            # yt-dlp 有时已经产出成品，但某个后处理步骤报错。
            # 文件存在且 B 站成品带音轨时按成功处理，避免再走兜底下载第二份。
            if self.cancelled:
                raise RuntimeError("用户取消下载")
            if final and Path(final).is_file():
                media = media_info_for_file(final) if is_bilibili_url(url) else {}
                if not (is_bilibili_url(url) and has_no_audio(media)):
                    self.log.emit("yt-dlp 报告错误，但已生成可用成品，按成功处理")
                    self._finished_outputs.add(str(final))
                    return final
            raise extract_error

        # 验证：如果最终文件不存在（可能是 yt-dlp 静默失败，如 412 风控），抛错而非返回目录
        if self.cancelled:
            raise RuntimeError("用户取消下载")
        if not final or not Path(final).is_file():
            raise RuntimeError(
                "yt-dlp 未生成有效文件（可能被 B站 412 风控拦截或格式无效）。\n"
                "建议：清除选定的格式（留空）后重试，或配置 Cookie 后重试。"
            )
        if self._is_intermediate_file(final):
            raise RuntimeError(
                "yt-dlp 只完成了中间流，音视频没有合并成功。\n"
                "请检查 ffmpeg 是否可用，或清除选定格式后重试。"
            )
        if is_bilibili_url(url):
            media = media_info_for_file(final)
            if media and has_no_audio(media):
                raise RuntimeError(
                    "yt-dlp 只下载到纯视频流，没有声音。\n"
                    "将尝试 B站兜底接口重新获取带音频的版本。"
                )
        self._finished_outputs.add(str(final))
        return final

    def request_session(self):
        """构建带 Cookie 和代理的下载 session。"""
        session = _build_bili_session(self.current_settings())
        return session

    def _throttle_transfer(self, started_at, transferred):
        """按平均速率限制直链下载，分小段 sleep 以保证暂停/取消能及时响应。"""
        limit_kb = int(self.current_settings().get("speed_limit_kb", 0) or 0)
        if limit_kb <= 0 or transferred <= 0:
            return
        target_seconds = transferred / (limit_kb * 1024)
        # 分小段 sleep，保证取消/暂停能及时响应
        while True:
            remaining = target_seconds - (time.monotonic() - started_at)
            if remaining <= 0:
                return
            self.wait_for_resume()
            time.sleep(min(remaining, 0.2))

    def _download_stream(self, session, url, path, index, start_pct, end_pct, label):
        self.wait_for_resume()
        response = session.get(url, stream=True, timeout=30)
        response.raise_for_status()
        total = int(response.headers.get("Content-Length") or 0)
        downloaded = 0
        rate_started = time.monotonic()
        try:
            with open(path, "wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    self.wait_for_resume()
                    if not chunk:
                        continue
                    handle.write(chunk)
                    downloaded += len(chunk)
                    self._throttle_transfer(rate_started, downloaded)
                    if total:
                        ratio = min(downloaded / total, 1.0)
                        percent = start_pct + (end_pct - start_pct) * ratio
                        detail = (
                            f"{label} {downloaded / 1024 / 1024:.1f}/"
                            f"{total / 1024 / 1024:.1f} MB"
                        )
                    else:
                        percent = -1
                        detail = f"{label} {downloaded / 1024 / 1024:.1f} MB"
                    self.item_progress.emit(index, percent, detail)
        except Exception:
            try:
                Path(path).unlink()
            except OSError:
                pass
            raise
        finally:
            try:
                response.close()
            except Exception:
                pass
        return downloaded

    def _merge_douyin(self, index, output_path, video_path, audio_path):
        self.item_progress.emit(index, 97, "合并音视频...")
        result = subprocess.run(
            [
                str(ffmpeg_path()),
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(video_path),
                "-i",
                str(audio_path),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            message = (result.stderr or "").strip().splitlines()
            detail = message[-1] if message else "未知错误"
            raise RuntimeError(f"ffmpeg 合并音视频失败：{detail}")

    def download_douyin_browser(self, index, url):
        """抖音专用下载：浏览器捕获直链，再下载并合并视频/音频。"""
        if not has_ffmpeg():
            raise RuntimeError(
                "抖音浏览器下载需要 ffmpeg 合并视频和音频。\n"
                "请把 ffmpeg.exe 放到程序目录后重试。"
            )

        self.log.emit("抖音浏览器模式：正在获取播放地址...")
        from ..douyin_browser import capture_douyin_media, get_cached_douyin

        quality = self.current_settings().get("quality")
        target_height = (
            int(str(quality)) if str(quality or "").strip().isdigit() else 0
        )
        data = get_cached_douyin(url, quality=quality) or capture_douyin_media(
            url, self.current_settings(), quality=quality
        )
        if target_height:
            selected = int(data.get("selected_height") or 0)
            if selected != target_height:
                reason = data.get("quality_result") or "未知原因"
                if reason == "login_required":
                    raise RuntimeError(
                        f"抖音 {target_height}P 需要登录后才能选择。\n"
                        "请在预览区点「登录抖音」登录一次，再重新下载。"
                    )
                raise RuntimeError(
                    f"抖音页面没有成功切换到 {target_height}P（{reason}）。\n"
                    "请重新解析后再试。"
                )
        video_url = data.get("video_url") or ""
        audio_url = data.get("audio_url") or ""
        if not video_url:
            raise RuntimeError("没有捕获到抖音视频地址，请重新解析后重试。")

        title = data.get("title") or "抖音视频"
        author = data.get("author") or ""
        self.current_titles[index] = title
        audio_only = (self.current_settings().get("quality") or "") == "audio"
        final_ext = "mp3" if audio_only else "mp4"
        match = re.search(r"/video/(\d+)", url)
        video_id = match.group(1) if match else ""
        filename = render_filename_template(
            self.current_settings().get("filename_template"),
            {
                "title": title,
                "id": video_id,
                "ext": final_ext,
                "uploader": author,
                "page": 1,
                "part": "",
            },
        )
        if not Path(filename).suffix:
            filename = f"{filename}.{final_ext}"
        elif audio_only and Path(filename).suffix.lower() != ".mp3":
            filename = f"{Path(filename).stem}.mp3"
        output_path = unique_path(
            Path(self.current_settings().get("download_dir") or ".") / filename
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)

        headers = std_headers("https://www.douyin.com/")
        headers["Referer"] = "https://www.douyin.com/"
        cookie_header = data.get("cookie_header") or ""
        if cookie_header:
            headers["Cookie"] = cookie_header
        session = requests.Session()
        session.headers.update(headers)
        proxy = (self.current_settings().get("proxy") or "").strip()
        if proxy:
            session.proxies.update({"http": proxy, "https": proxy})

        video_part = output_path.with_suffix(".video.part")
        audio_part = output_path.with_suffix(".audio.part")
        merged_path = (
            output_path.with_suffix(".video.merged.mp4")
            if audio_only
            else output_path
        )
        self._track_temp(str(video_part))
        if merged_path != output_path:
            self._track_temp(str(merged_path))

        try:
            self.item_progress.emit(index, 0, "下载视频流")
            self._download_stream(
                session,
                video_url,
                video_part,
                index,
                0,
                85 if audio_url else 96,
                "视频",
            )

            if audio_url:
                self._track_temp(str(audio_part))
                self._download_stream(
                    session, audio_url, audio_part, index, 85, 94, "音频"
                )
                self._merge_douyin(index, merged_path, video_part, audio_part)
            else:
                video_part.replace(merged_path)

            for path in (video_part, audio_part):
                try:
                    path.unlink()
                except OSError:
                    pass

            if audio_only:
                self._extract_douyin_audio(index, merged_path, output_path)
                try:
                    merged_path.unlink()
                except OSError:
                    pass
        except Exception:
            for path in {video_part, audio_part, merged_path, output_path}:
                try:
                    Path(path).unlink()
                except OSError:
                    pass
            raise
        finally:
            try:
                session.close()
            except Exception:
                pass

        self._finished_outputs.add(str(output_path))
        self.item_progress.emit(index, 100, "完成")
        return [str(output_path)]

    def _extract_douyin_audio(self, index, source_path, output_path):
        """从合并好的抖音视频里提取 MP3。"""
        self.item_progress.emit(index, 97, "提取音频...")
        result = subprocess.run(
            [
                str(ffmpeg_path()),
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source_path),
                "-vn",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "192k",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            lines = (result.stderr or "").strip().splitlines()
            detail = lines[-1] if lines else "未知错误"
            raise RuntimeError(f"ffmpeg 提取音频失败：{detail}")

    def download_bili_legacy(self, index, url):
        resolved_url = resolve_bilibili_url(url, self.current_settings())
        id_type, id_value = extract_video_id(resolved_url)
        if not id_value:
            raise RuntimeError("无法从链接中识别 BV 号或 av 号。")
        video_id = resolved_url  # bili_view/bili_playurl 内部会解析
        page_num = selected_page_number(resolved_url)
        data = bili_view(video_id, self.current_settings())
        pages = data.get("pages") or []
        if not pages:
            raise RuntimeError("没有找到可下载的分 P。")
        cid = None
        for p in pages:
            if (p.get("page") or 1) == page_num:
                cid = p.get("cid")
                break
        if cid is None:
            cid = pages[0].get("cid")
            page_num = pages[0].get("page") or 1
        quality = self.current_settings().get("quality", "best")
        if quality == "audio":
            raise RuntimeError(
                "B站公开视频兜底接口不支持仅音频。\n"
                "请配置 Cookie，并确认 ffmpeg 可用后重试。"
            )
        has_cookie = has_cookie_for_url(resolved_url, self.current_settings())
        # 如果用户在预览表选了具体格式（如 30080 / 30080+30280），从中解析出视频清晰度 qn
        # B站 DASH 流 format_id：视频 300xx，音频 302xx；qn = format_id - 30000
        custom_format = (self.current_settings().get("custom_format") or "").strip()
        if custom_format:
            format_parts = [
                part.strip() for part in custom_format.split("+") if part.strip()
            ]
            if format_parts and all(part.startswith("302") for part in format_parts):
                raise RuntimeError(
                    "B站兜底接口只能下载音视频一体的 MP4，不能只取音频。\n"
                    "请配置 Cookie 并使用 yt-dlp，或先下载 MP4 后用 ffmpeg 提取音频。"
                )
        qn_from_format = None
        if custom_format:
            for part in custom_format.split("+"):
                part = part.strip()
                if part.isdigit() and part.startswith("300"):
                    qn_from_format = int(part) - 30000
                    break
        if qn_from_format:
            qn = qn_from_format
            self.log.emit(f"按选定格式解析清晰度 qn={qn}")
        elif not has_cookie:
            # 无 Cookie 时 B站公开直链通常最高到 720P；仍尊重用户主动选的 360/480。
            if str(quality).isdigit():
                height = int(quality)
                preferred_qn = 16 if height <= 360 else 32 if height <= 480 else 64
            else:
                preferred_qn = QUALITY_QN.get(quality, 64)
            qn = min(max(int(preferred_qn or 64), 16), 64)
            self.log.emit(
                f"未配置 Cookie，兜底接口尝试 qn={qn}（公开视频最高约 720P）..."
            )
        else:
            qn = QUALITY_QN.get(quality, 80) if quality != "best" else 80
        play_data = bili_playurl(video_id, cid, qn, self.current_settings(), page=page_num)
        durl = play_data.get("durl") or []
        if not durl:
            # 如果有 Cookie 且返回了 dash，尝试用 fnval=0 重新请求 durl
            dash = play_data.get("dash")
            if dash:
                self.log.emit("返回了 DASH 格式，尝试请求 durl 直链...")
                play_data = bili_playurl(video_id, cid, qn, self.current_settings(), page=page_num, fnval=0)
                durl = play_data.get("durl") or []
            if not durl:
                raise RuntimeError(
                    "兜底接口没有返回可下载直链。可能原因：\n"
                    "1. 视频需要登录/大会员 → 请配置 Cookie\n"
                    "2. 视频被删除/审核中\n"
                    "3. B站风控 → 请稍后再试或配置 Cookie\n"
                    "建议：设置页 → 扫码登录配置 Cookie 后重试。"
                )
        session = self.request_session()
        actual_qn = 0
        try:
            actual_qn = int(play_data.get("quality") or 0)
        except (TypeError, ValueError):
            actual_qn = 0
        if actual_qn and qn and actual_qn < qn:
            self.log.emit(
                f"兜底接口实际返回清晰度 qn={actual_qn}（请求 qn={qn}），"
                "可能受登录/会员/片源限制。"
            )
        if actual_qn:
            self._local.actual_quality = str(actual_qn)
        download_dir = Path(self.current_settings()["download_dir"])
        title = data.get("title") or id_value
        part_title = ""
        if len(pages) > 1:
            page_part = pages[0]
            for p in pages:
                if (p.get("page") or 1) == page_num:
                    page_part = p
                    break
            part_title = page_part.get("part") or f"P{page_num}"
        self.current_titles[index] = (
            f"{title} P{page_num} {part_title}".strip()
            if len(pages) > 1
            else title
        )
        # 按用户设置的文件名模板生成，与 yt-dlp 路径保持一致的命名风格
        filename = render_filename_template(
            self.current_settings().get("filename_template"),
            {
                "title": title,
                "id": id_value,
                "ext": "mp4",
                "uploader": (data.get("owner") or {}).get("name") or "",
                "page": page_num,
                "part": part_title,
            },
        )
        if not Path(filename).suffix:
            filename = f"{filename}.mp4"
        if len(pages) > 1:
            filename = f"{Path(filename).stem}_P{page_num}_{sanitize_filename(part_title)}.mp4"
        output_path = unique_path(download_dir / filename)
        outputs = []
        part_paths = []
        try:
            for i, d in enumerate(durl):
                video_url = d.get("url")
                if not video_url:
                    continue
                self.wait_for_resume()
                self.item_progress.emit(index, 0, f"下载分段 {i+1}/{len(durl)}")
                resp = session.get(video_url, headers={"Referer": "https://www.bilibili.com/"}, stream=True, timeout=30)
                try:
                    resp.raise_for_status()
                    size = int(d.get("size") or 0)
                    downloaded = 0
                    part_path = output_path.with_suffix(f".part{i}") if len(durl) > 1 else output_path.with_suffix(".part")
                    self._track_temp(str(part_path))
                    part_paths.append(part_path)
                    rate_started = time.monotonic()
                    with open(part_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=1024 * 256):
                            self.wait_for_resume()
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                                self._throttle_transfer(rate_started, downloaded)
                                if size:
                                    pct = downloaded / size * 100
                                    self.item_progress.emit(index, pct, f"分段 {i+1}/{len(durl)}  {pct:.1f}%")
                    if len(durl) > 1:
                        outputs.append(str(part_path))
                    else:
                        part_path.replace(output_path)
                        outputs.append(str(output_path))
                finally:
                    try:
                        resp.close()
                    except Exception:
                        pass
        except Exception:
            for part_path in part_paths:
                try:
                    part_path.unlink()
                except OSError:
                    pass
            raise
        finally:
            try:
                session.close()
            except Exception:
                pass

        if len(durl) > 1 and len(outputs) != len(durl):
            for part_path in part_paths:
                try:
                    part_path.unlink()
                except OSError:
                    pass
            raise RuntimeError("有分段没有可用的下载直链，已取消本次下载。")

        if len(durl) > 1:
            if not has_ffmpeg():
                for part_path in part_paths:
                    try:
                        part_path.unlink()
                    except OSError:
                        pass
                raise RuntimeError(
                    f"该视频被分成 {len(durl)} 段，需要 ffmpeg 才能合并成一个文件。\n"
                    "请把 ffmpeg.exe 放到程序目录后重试。"
                )
            concat_path = output_path.with_suffix(".concat.txt")
            self._track_temp(str(concat_path))
            with open(concat_path, "w", encoding="utf-8") as f:
                for o in outputs:
                    escaped = str(o).replace("'", r"'\''")
                    f.write(f"file '{escaped}'\n")
            try:
                result = subprocess.run(
                    [str(ffmpeg_path()), "-y", "-f", "concat", "-safe", "0", "-i", str(concat_path),
                     "-c", "copy", str(output_path)],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if result.returncode != 0:
                    lines = (result.stderr or "").strip().splitlines()
                    detail = lines[-1] if lines else "未知错误"
                    raise RuntimeError(f"ffmpeg 合并分段失败：{detail}")
            except Exception as exc:
                for path in list(part_paths) + [concat_path]:
                    try:
                        Path(path).unlink()
                    except OSError:
                        pass
                if isinstance(exc, RuntimeError) and "ffmpeg 合并分段失败" in str(exc):
                    raise
                raise RuntimeError(f"ffmpeg 合并分段失败：{format_error(exc)}") from exc
            for path in list(part_paths) + [concat_path]:
                try:
                    Path(path).unlink()
                except OSError:
                    pass
            outputs = [str(output_path)]

        if not outputs or not Path(outputs[0]).is_file():
            raise RuntimeError("下载完成但未找到有效的成品文件。")
        self._finished_outputs.add(str(output_path))

        output_base = str(Path(outputs[0]).with_suffix(""))
        if not self.cancelled and self.current_settings().get("download_thumbnail"):
            try:
                cover = download_bili_thumbnail(
                    data.get("pic"), output_base, self.current_settings()
                )
                if cover:
                    self.log.emit(f"封面已保存: {cover}")
                else:
                    self.log.emit("封面下载失败：接口没有返回封面地址")
            except Exception as exc:
                self.log.emit(f"封面下载失败: {format_error(exc)}")

        if not self.cancelled and self.current_settings().get("download_subtitle"):
            try:
                subtitle_paths = download_bili_subtitles(
                    resolved_url, cid, output_base, self.current_settings()
                )
                if subtitle_paths:
                    self.log.emit(
                        "字幕已保存: " + "、".join(subtitle_paths)
                    )
                else:
                    self.log.emit("未获取到字幕（视频无字幕，或需要登录 Cookie）")
            except Exception as exc:
                self.log.emit(f"字幕下载失败: {format_error(exc)}")

        self.item_progress.emit(index, 100, "完成")
        return outputs

    def cleanup_temp_files(self):
        """清理本次任务产生的临时文件。

        只删除本 worker 登记过的 .part/.ytdl/.temp/分段文件，不再扫描整个下载目录，
        避免误删用户自己放进下载目录的同名文件；已完成的成品文件永不删除。
        """
        with self._temp_lock:
            targets = list(self._temp_files)
            self._temp_files.clear()
        for target in targets:
            if target in self._finished_outputs:
                continue
            try:
                p = Path(target)
                if p.is_file():
                    p.unlink()
            except Exception:
                pass
