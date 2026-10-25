"""预览解析线程：优先 yt-dlp，B站 412 时回退公开接口。"""

import time

import yt_dlp
from PyQt5.QtCore import QThread, pyqtSignal

from ..bilibili import bili_view, resolve_bili_login, resolve_bilibili_url
from ..errors import format_bili_error, format_site_error
from ..logs import write_crash_log
from ..net import (
    QuietYtdlpLogger,
    apply_cookie_and_proxy_options,
    js_runtime_options,
    std_headers,
)
from ..sites import SITE_DOUYIN, detect_site
from ..urls import extract_video_id, is_bilibili_url, normalize_input


class PreviewWorker(QThread):
    info_ready = pyqtSignal(int, dict)
    failed = pyqtSignal(int, str)

    def __init__(self, request_id, url, settings, parent=None):
        super().__init__(parent)
        self.request_id = request_id
        self.url = normalize_input(url)
        self.settings = settings
        self._bili_logged_in = False
        self._bili_login_source = ""

    def run(self):
        if not self.url:
            self.failed.emit(self.request_id, "没有可解析的链接。")
            return
        try:
            if detect_site(self.url) == SITE_DOUYIN:
                try:
                    from ..douyin_browser import (
                        build_douyin_info,
                        capture_douyin_media,
                    )

                    data = capture_douyin_media(
                        self.url,
                        self.settings,
                        quality=self.settings.get("quality"),
                    )
                    self.info_ready.emit(
                        self.request_id, build_douyin_info(data, self.url)
                    )
                    return
                except Exception as exc:
                    self.failed.emit(
                        self.request_id,
                        f"抖音浏览器解析失败：{exc}\n"
                        "→ 请确认已点「一键登录抖音」完成登录，"
                        "并且浏览器里能正常播放该视频。",
                    )
                    return
            if is_bilibili_url(self.url):
                try:
                    logged_in, source = resolve_bili_login(
                        self.settings,
                        allow_browser=bool(
                            self.settings.get("auto_browser_login", True)
                        ),
                    )
                except Exception as exc:
                    logged_in, source = False, ""
                    write_crash_log(
                        type(exc), exc, exc.__traceback__,
                        source="PreviewWorker.bili_login",
                    )
                self._bili_logged_in = logged_in
                self._bili_login_source = source
                if not logged_in:
                    # 没有有效登录时不浪费一次 yt-dlp 412 请求，直接用公开接口预览；
                    # 高清登录提示由主窗口根据 login_ok 展示。
                    info = self.fetch_bili_legacy_preview("未登录")
                    info["login_ok"] = False
                    info["source"] = "B站公开接口（未登录）"
                    info["note"] = (
                        "当前未登录，B站公开接口最高约 720P；"
                        "登录后可尝试 1080P+（4K/高码率需大会员）。"
                    )
                    self.info_ready.emit(self.request_id, info)
                    return
            info = self.fetch_with_ytdlp()
            if is_bilibili_url(self.url):
                info["login_ok"] = bool(self._bili_logged_in)
                info["login_source"] = self._bili_login_source
            self.info_ready.emit(self.request_id, info)
        except Exception as exc:
            try:
                if is_bilibili_url(self.url) and (
                    "HTTP Error 412" in str(exc) or "Precondition Failed" in str(exc)
                ):
                    try:
                        info = self.fetch_bili_legacy_preview(str(exc))
                        info["login_ok"] = bool(self._bili_logged_in)
                        info["login_source"] = self._bili_login_source
                        self.info_ready.emit(self.request_id, info)
                        return
                    except Exception as fallback_exc:
                        self.failed.emit(self.request_id, format_bili_error(fallback_exc))
                        return
                self.failed.emit(self.request_id, format_site_error(exc, self.url))
            except Exception as top_exc:
                write_crash_log(type(top_exc), top_exc, top_exc.__traceback__,
                                source="PreviewWorker")
                try:
                    self.failed.emit(self.request_id, format_bili_error(top_exc))
                except Exception:
                    pass

    def ytdlp_options(self):
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": 15,
            "logger": QuietYtdlpLogger(),
            "http_headers": std_headers(self.url),
        }
        runtimes = js_runtime_options()
        if runtimes:
            opts["js_runtimes"] = runtimes
        return apply_cookie_and_proxy_options(opts, self.settings, self.url)

    def fetch_with_ytdlp(self):
        with yt_dlp.YoutubeDL(self.ytdlp_options()) as ydl:
            info = ydl.extract_info(self.url, download=False)
        return self.normalize_ytdlp_info(info)

    def normalize_ytdlp_info(self, info):
        formats = []
        for f in info.get("formats", []):
            formats.append({
                "format_id": f.get("format_id", ""),
                "ext": f.get("ext", ""),
                "vcodec": f.get("vcodec") or "",
                "acodec": f.get("acodec") or "",
                "width": f.get("width") or 0,
                "height": f.get("height") or 0,
                "fps": f.get("fps") or 0,
                "filesize": f.get("filesize") or f.get("filesize_approx") or 0,
                "tbr": f.get("tbr") or 0,
                "note": "",
            })
        pages = []
        entries = info.get("entries") or []
        if entries:
            for i, e in enumerate(entries, 1):
                pages.append({
                    "page": i,
                    "title": e.get("title") or f"P{i}",
                    "duration": e.get("duration") or 0,
                    "url": e.get("webpage_url") or self.url,
                })
        return {
            "title": info.get("title") or "",
            "uploader": info.get("uploader") or info.get("channel") or "",
            "duration": info.get("duration") or 0,
            "thumbnail": info.get("thumbnail") or "",
            "view_count": info.get("view_count") or 0,
            "like_count": info.get("like_count") or 0,
            "upload_date": info.get("upload_date") or "",
            "tags": [str(tag) for tag in (info.get("tags") or [])][:6],
            "pages": pages,
            "formats": formats,
            "source": "yt-dlp",
            "url": self.url,
            "note": "yt-dlp 解析成功" if formats else "未获取到格式列表",
        }

    def fetch_bili_legacy_preview(self, err_msg=""):
        resolved_url = resolve_bilibili_url(self.url, self.settings)
        id_type, id_value = extract_video_id(resolved_url)
        if not id_value:
            raise RuntimeError("无法从链接中识别 BV 号或 av 号。")
        data = bili_view(resolved_url, self.settings)
        pages = []
        for p in data.get("pages", []):
            pages.append({
                "page": p.get("page") or 1,
                "title": p.get("part") or f"P{p.get('page') or 1}",
                "duration": p.get("duration") or 0,
                "url": f"https://www.bilibili.com/video/{id_value}?p={p.get('page') or 1}" if id_type == "bvid" else f"https://www.bilibili.com/video/av{id_value}?p={p.get('page') or 1}",
            })
        if not pages:
            raise RuntimeError("没有找到可预览的分 P。")
        return {
            "title": data.get("title") or "",
            "uploader": data.get("owner", {}).get("name") or "",
            "duration": data.get("duration") or 0,
            "thumbnail": data.get("pic") or "",
            "view_count": data.get("stat", {}).get("view") or 0,
            "like_count": data.get("stat", {}).get("like") or 0,
            "upload_date": time.strftime("%Y%m%d", time.localtime(data.get("pubdate"))) if data.get("pubdate") else "",
            "pages": pages,
            "formats": [],
            "source": "B站公开接口（兜底）",
            "url": self.url,
            "note": f"yt-dlp 失败({err_msg[:40]})，已用兜底接口。无 Cookie 时通常仅 720P/360P。",
        }
