"""多站点视频下载器 - 启动入口。

实现已按职责拆分到 `multisite_downloader/` 包：

    multisite_downloader/config.py     路径、常量、默认设置
    multisite_downloader/utils.py      纯函数工具
    multisite_downloader/urls.py       链接 / BV 号解析
    multisite_downloader/media.py      ffmpeg 媒体信息探测
    multisite_downloader/history.py    下载历史
    multisite_downloader/bilibili.py   B站 Web API
    multisite_downloader/workers/      预览、下载、Cookie 检测、扫码登录、封面加载线程
    multisite_downloader/ui/           主窗口、扫码对话框、特效控件

运行方式不变：`py gui_download_qt.py`
"""

import os
import subprocess
import sys

from PyQt5.QtCore import QLockFile
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QMessageBox

from multisite_downloader.config import ASSETS_DIR, BASE_DIR, DEFAULT_DOWNLOAD_DIR, RESOURCE_DIR
from multisite_downloader.logs import install_excepthooks
from multisite_downloader.ui.main_window import MainWindow

# 兼容旧的导入路径：单元测试 / 外部脚本仍可从本模块导入这些工具函数
from multisite_downloader.errors import format_bili_error, format_error  # noqa: F401
from multisite_downloader.media import parse_media_info_text  # noqa: F401
from multisite_downloader.urls import (  # noqa: F401
    extract_aid,
    extract_bvid,
    extract_video_id,
    is_bilibili_url,
    normalize_input,
    selected_page_number,
)
from multisite_downloader.utils import (  # noqa: F401
    format_bytes,
    format_duration,
    render_filename_template,
    sanitize_filename,
    split_inputs,
)

def _set_windows_app_user_model_id():
    """让 Windows 把本程序当作独立应用，而不是 python.exe 的子进程。

    不设置时，任务栏会按 python.exe 分组，并显示 Python 的默认图标，
    即使窗口本身已经 setWindowIcon 也一样。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        shell32 = ctypes.windll.shell32
        shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
        shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
        shell32.SetCurrentProcessExplicitAppUserModelID(
            ctypes.c_wchar_p("multisite.video.downloader")
        )
    except Exception:
        pass


def _run_self_test():
    """`exe --self-test`：检查打包后的依赖、资源与界面构建，写入 self_test.log。"""
    from multisite_downloader import __version__
    from multisite_downloader.config import ffmpeg_path, has_ffmpeg

    lines = [f"多站点视频下载器 v{__version__} self-test"]
    ok = True

    def check(name, func):
        nonlocal ok
        try:
            detail = func()
            lines.append(f"[OK] {name}" + (f": {detail}" if detail else ""))
        except Exception as exc:
            ok = False
            lines.append(f"[FAIL] {name}: {type(exc).__name__}: {exc}")

    def check_imports():
        import browser_cookie3  # noqa: F401
        import qrcode  # noqa: F401
        import requests
        import websocket  # noqa: F401
        import yt_dlp

        return (
            f"yt-dlp {yt_dlp.version.__version__}, "
            f"requests {requests.__version__}"
        )

    def check_ffmpeg():
        if not has_ffmpeg():
            raise RuntimeError("未找到 ffmpeg.exe")
        result = subprocess.run(
            [str(ffmpeg_path()), "-version"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or "").strip()[:200])
        return str(ffmpeg_path())

    def check_browser():
        from multisite_downloader.douyin_browser import find_browser_executable

        label, path = find_browser_executable()
        if not path:
            raise RuntimeError("未找到 Chrome/Edge")
        return f"{label}: {path}"

    def check_network():
        url = os.environ.get("MULTISITE_SELF_TEST_URL", "").strip()
        if not url:
            return "跳过（未设置 MULTISITE_SELF_TEST_URL）"
        import requests

        from multisite_downloader.net import std_headers

        response = requests.get(url, headers=std_headers(url), timeout=20)
        response.raise_for_status()
        return f"HTTP {response.status_code}, {len(response.content)} bytes"

    def check_ui():
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        app = QApplication.instance() or QApplication(sys.argv[:1])
        window = MainWindow()
        window.deleteLater()
        app.processEvents()
        return "主窗口构建成功"

    check("依赖导入", check_imports)
    check("ffmpeg", check_ffmpeg)
    check("Chromium 浏览器", check_browser)
    check("网络请求", check_network)
    check("界面", check_ui)

    from multisite_downloader.ui.preview_player import find_mpv_executable

    lines.append(f"[可选] mpv: {find_mpv_executable() or '未找到（内置播放不可用）'}")
    lines.append(f"程序目录: {BASE_DIR}")
    lines.append("结果: " + ("PASS" if ok else "FAIL"))
    report = "\n".join(lines) + "\n"
    try:
        (BASE_DIR / "self_test.log").write_text(report, encoding="utf-8")
    except OSError:
        pass
    try:
        print(report)
    except Exception:
        pass
    return 0 if ok else 1


def main():
    if "--self-test" in sys.argv:
        sys.exit(_run_self_test())
    _set_windows_app_user_model_id()
    install_excepthooks()
    DEFAULT_DOWNLOAD_DIR.mkdir(exist_ok=True)

    # 必须先创建 QApplication，否则下面的 QMessageBox 会因为“QWidget before QApplication”直接崩溃
    app = QApplication(sys.argv)
    app.setApplicationName("多站点视频下载器")

    # 统一窗口、任务栏、最小化按钮和托盘的应用图标
    for icon_path in (
        ASSETS_DIR / "logo.png",
        RESOURCE_DIR / "assets" / "logo.png",
        RESOURCE_DIR / "icon.png",
        RESOURCE_DIR / "icon.ico",
    ):
        if icon_path.exists():
            app.setWindowIcon(QIcon(str(icon_path)))
            break

    # 单实例锁：防止重复打开多个窗口抢占资源（下载目录/设置/history 是进程共享的）
    lock_path = BASE_DIR / "app.lock"
    lock = QLockFile(str(lock_path))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(
            None, "多站点视频下载器",
            "程序已在运行中。\n如果确实打不开窗口，请在系统托盘找到图标单击恢复。",
        )
        return

    # 崩溃遗留的预览片段/封面截图不应长期堆在用户临时目录
    from multisite_downloader.ui.preview_player import cleanup_stale_media_temp

    cleanup_stale_media_temp()

    window = MainWindow()
    window.show()
    code = app.exec_()
    lock.unlock()
    sys.exit(code)


if __name__ == "__main__":
    main()
