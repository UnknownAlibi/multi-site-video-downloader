"""内置视频预览：ffmpeg 生成短预览，mpv 嵌入 Qt 窗口播放。"""

import re
import shutil
import subprocess
import tempfile
import time
import webbrowser
from pathlib import Path

from PyQt5.QtCore import QThread, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import BASE_DIR, RESOURCE_DIR, ffmpeg_path, has_ffmpeg
from ..errors import format_error
from ..net import std_headers

PREVIEW_SECONDS = 300


def cleanup_stale_media_temp(max_age_seconds=2 * 24 * 3600):
    """清理崩溃后遗留的预览/封面临时文件，正常退出时这些文件已主动删除。"""
    now = time.time()
    for dirname in ("multisite-downloader_previews", "multisite-downloader_covers"):
        directory = Path(tempfile.gettempdir()) / dirname
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            try:
                if path.is_file() and now - path.stat().st_mtime > max_age_seconds:
                    path.unlink()
            except OSError:
                continue


def find_mpv_executable():
    """优先程序目录，其次 PATH；兼容 WinGet 安装的 mpv.com + mpv.exe。"""
    candidates = [
        BASE_DIR / "mpv.exe",
        RESOURCE_DIR / "mpv.exe",
    ]
    for name in ("mpv.exe", "mpv"):
        found = shutil.which(name)
        if not found:
            continue
        path = Path(found)
        if path.suffix.lower() == ".com":
            exe = path.with_suffix(".exe")
            if exe.is_file():
                path = exe
        candidates.append(path)
    for path in candidates:
        if path.is_file():
            return path
    return None


def preview_headers(data):
    """返回 ffmpeg 需要的 User-Agent 与 headers 字符串。"""
    ua = std_headers("https://www.douyin.com/")["User-Agent"]
    cookie = (data.get("cookie_header") or "").strip()
    headers = "Referer: https://www.douyin.com/\r\n"
    if cookie:
        headers += f"Cookie: {cookie}\r\n"
    return ua, headers


class DouyinPreviewWorker(QThread):
    status = pyqtSignal(str)
    ready = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, info, url, parent=None):
        super().__init__(parent)
        self.info = info
        self.url = url
        self._proc = None
        self.cancelled = False
        self.output_path = ""

    def cancel(self):
        self.cancelled = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()

    def run(self):
        try:
            if not has_ffmpeg():
                self.failed.emit("内置预览需要 ffmpeg，请把 ffmpeg.exe 放到程序目录。")
                return
            stream = self.info.get("stream") or {}
            video_url = stream.get("video_url") or ""
            audio_url = stream.get("audio_url") or ""
            if not video_url:
                self.failed.emit("没有可用的视频地址，请重新解析。")
                return
            ua, headers = preview_headers(stream)
            match = re.search(r"/video/(\d+)", self.url)
            video_id = match.group(1) if match else str(int(time.time()))
            target = (
                Path(tempfile.gettempdir())
                / "multisite-downloader_previews"
                / f"{video_id}_{int(time.time())}.mp4"
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            self.output_path = str(target)
            args = [
                str(ffmpeg_path()),
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-user_agent",
                ua,
                "-headers",
                headers,
                "-i",
                video_url,
            ]
            if audio_url:
                args.extend(
                    [
                        "-user_agent",
                        ua,
                        "-headers",
                        headers,
                        "-i",
                        audio_url,
                    ]
                )
            args.extend(
                [
                    "-t",
                    str(PREVIEW_SECONDS),
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    str(target),
                ]
            )
            self.status.emit("正在准备内置预览（前 5 分钟）...")
            self._proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            _, stderr = self._proc.communicate()
            if self.cancelled:
                try:
                    target.unlink()
                except OSError:
                    pass
                return
            if self._proc.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
                try:
                    target.unlink()
                except OSError:
                    pass
                detail = (stderr or "").strip().splitlines()
                detail = detail[-1] if detail else "未知错误"
                self.failed.emit(f"生成预览失败：{detail}")
                return
            self.ready.emit(str(target))
        except Exception as exc:
            self.failed.emit(f"生成预览失败：{format_error(exc)}")


class LocalMediaDialog(QDialog):
    """用 mpv 在软件内播放已下载的本地文件。"""

    def __init__(self, parent, media_path, title=""):
        super().__init__(parent)
        self.media_path = Path(media_path)
        self.mpv_proc = None
        self.mpv_path = find_mpv_executable()
        self.setWindowTitle(title or "内置播放")
        self.setFixedSize(960, 620)
        self._build_ui(title)
        QTimer.singleShot(0, self._launch)

    def _build_ui(self, title):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        title_label = QLabel(title or self.media_path.name)
        title_label.setObjectName("previewTitle")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)

        path_label = QLabel(str(self.media_path))
        path_label.setObjectName("hintText")
        path_label.setWordWrap(True)
        layout.addWidget(path_label)

        self.video_host = QWidget()
        self.video_host.setAttribute(Qt.WA_NativeWindow, True)
        self.video_host.setAutoFillBackground(True)
        palette = self.video_host.palette()
        palette.setColor(QPalette.Window, QColor("#000000"))
        self.video_host.setPalette(palette)
        self.video_host.setStyleSheet("background: #000000;")
        layout.addWidget(self.video_host, 1)

        footer = QHBoxLayout()
        self.status_label = QLabel("播放开始后点击画面，空格暂停，方向键快进。")
        self.status_label.setObjectName("hintText")
        self.status_label.setWordWrap(True)
        footer.addWidget(self.status_label, 1)
        self.dir_btn = QPushButton("打开所在目录")
        self.dir_btn.setObjectName("secondaryBtn")
        self.dir_btn.clicked.connect(self._open_dir)
        footer.addWidget(self.dir_btn)
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("secondaryBtn")
        close_btn.clicked.connect(self.close)
        footer.addWidget(close_btn)
        layout.addLayout(footer)

    def _open_dir(self):
        from ..shell import open_path_in_explorer

        open_path_in_explorer(str(self.media_path.parent))

    def _launch(self):
        if not self.mpv_path:
            self.status_label.setText(
                "未检测到 mpv.exe，无法内置播放。请改用系统播放器打开。"
            )
            return
        if not self.media_path.is_file():
            self.status_label.setText("文件不存在，可能已被移动或删除。")
            return
        host_id = int(self.video_host.winId())
        args = [
            str(self.mpv_path),
            f"--wid={host_id}",
            "--no-border",
            "--force-window=yes",
            "--no-terminal",
            "--no-config",
            "--osc=yes",
            "--input-default-bindings=yes",
            "--keep-open=no",
            "--volume=70",
            str(self.media_path),
        ]
        try:
            self.mpv_proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            self.status_label.setText(f"启动内置播放器失败：{format_error(exc)}")
            return
        self.status_label.setText("播放中 · 点击画面后空格暂停，方向键快进")
        QTimer.singleShot(1500, self._check_mpv)

    def _check_mpv(self):
        if self.mpv_proc is not None and self.mpv_proc.poll() is not None:
            self.status_label.setText("播放器已退出。")

    def closeEvent(self, event):
        proc = self.mpv_proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()
        self.mpv_proc = None
        event.accept()


class DouyinPreviewDialog(QDialog):
    """mpv 内嵌播放器，避免把用户跳到浏览器。"""

    def __init__(self, parent, info, url):
        super().__init__(parent)
        self.info = info
        self.page_url = url
        self.preview_path = ""
        self.worker = None
        self.mpv_proc = None
        self.mpv_path = find_mpv_executable()
        self.setWindowTitle("抖音内置预览")
        self.setFixedSize(960, 620)
        self._build_ui()
        QTimer.singleShot(0, self._start_prepare)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        title = QLabel(self.info.get("title") or "抖音视频")
        title.setObjectName("previewTitle")
        title.setWordWrap(True)
        layout.addWidget(title)

        self.hint_label = QLabel(
            "内置预览前 5 分钟；完整视频请关闭预览后使用「开始下载」。"
            "播放开始后点击画面，空格暂停，方向键快进。"
        )
        self.hint_label.setObjectName("hintText")
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

        self.stack = QStackedWidget()
        self.prepare_page = QWidget()
        prepare_layout = QVBoxLayout(self.prepare_page)
        prepare_layout.addStretch()
        self.prepare_label = QLabel("正在准备内置预览...")
        self.prepare_label.setObjectName("previewMeta")
        self.prepare_label.setAlignment(Qt.AlignCenter)
        self.prepare_label.setWordWrap(True)
        prepare_layout.addWidget(self.prepare_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(8)
        self.progress.setMaximumWidth(360)
        prepare_layout.addWidget(self.progress, 0, Qt.AlignCenter)
        prepare_layout.addStretch()
        self.stack.addWidget(self.prepare_page)

        self.video_host = QWidget()
        self.video_host.setAttribute(Qt.WA_NativeWindow, True)
        self.video_host.setAutoFillBackground(True)
        palette = self.video_host.palette()
        palette.setColor(QPalette.Window, QColor("#000000"))
        self.video_host.setPalette(palette)
        self.video_host.setStyleSheet("background: #000000;")
        self.stack.addWidget(self.video_host)
        layout.addWidget(self.stack, 1)

        footer = QHBoxLayout()
        self.status_label = QLabel("")
        self.status_label.setObjectName("hintText")
        self.status_label.setWordWrap(True)
        footer.addWidget(self.status_label, 1)
        self.open_btn = QPushButton("在浏览器打开")
        self.open_btn.setObjectName("secondaryBtn")
        self.open_btn.clicked.connect(self._open_browser)
        footer.addWidget(self.open_btn)
        self.close_btn = QPushButton("关闭")
        self.close_btn.setObjectName("secondaryBtn")
        self.close_btn.clicked.connect(self.close)
        footer.addWidget(self.close_btn)
        layout.addLayout(footer)

    def _start_prepare(self):
        if not self.mpv_path:
            self._show_error(
                "未检测到 mpv.exe，无法内置播放。\n"
                "可以把 mpv.exe 放到程序目录后重试，或点「在浏览器打开」。"
            )
            return
        if not (self.info.get("stream") or {}).get("video_url"):
            self._show_error("没有可用的播放地址，请重新解析后再试。")
            return
        self.worker = DouyinPreviewWorker(self.info, self.page_url, self)
        self.worker.status.connect(self.prepare_label.setText)
        self.worker.ready.connect(self._on_ready)
        self.worker.failed.connect(self._show_error)
        self.worker.start()

    def _show_error(self, message):
        self.progress.setVisible(False)
        self.prepare_label.setText(message)
        self.status_label.setText(message)

    def _on_ready(self, path):
        self.preview_path = path
        self.stack.setCurrentWidget(self.video_host)
        self.hint_label.setText(
            "内置预览前 5 分钟；点击画面后空格暂停、方向键快进。"
            "完整视频请关闭预览后开始下载。"
        )
        self.status_label.setText("")
        QApplication.processEvents()
        self._launch_mpv(path)

    def _launch_mpv(self, path):
        if not self.mpv_path:
            self._show_error("未检测到 mpv.exe。")
            return
        host_id = int(self.video_host.winId())
        args = [
            str(self.mpv_path),
            f"--wid={host_id}",
            "--no-border",
            "--force-window=yes",
            "--no-terminal",
            "--no-config",
            "--osc=yes",
            "--input-default-bindings=yes",
            "--keep-open=no",
            "--volume=70",
            path,
        ]
        try:
            self.mpv_proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            self._show_error(f"启动内置播放器失败：{format_error(exc)}")
            return
        QTimer.singleShot(1500, self._check_mpv)

    def _check_mpv(self):
        if self.mpv_proc is None:
            return
        code = self.mpv_proc.poll()
        if code is not None:
            self.status_label.setText(
                "播放器已退出。预览结束后请关闭窗口，或重新解析再试。"
            )

    def _open_browser(self):
        webbrowser.open(self.page_url)

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            if not self.worker.wait(5000):
                self.worker.terminate()
                self.worker.wait(1000)
        proc = self.mpv_proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()
        self.mpv_proc = None
        if self.preview_path:
            try:
                Path(self.preview_path).unlink()
            except OSError:
                pass
            self.preview_path = ""
        event.accept()
