"""界面与线程装配的冒烟测试（离屏模式，不弹窗、不联网）。

重构后用它守住底线：主窗口能构建、三个页面都在、各 Worker 能正常实例化、
B站 下载路径选择符合预期。

运行方式:
    pytest test_smoke.py -v
"""

import os
import subprocess
import sys
import threading
from pathlib import Path

# 必须在导入 PyQt5 之前设置，保证无显示器环境也能跑
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402
from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QLabel,
    QPushButton,
    QTableWidgetItem,
)

import multisite_downloader.bilibili as bilibili_module  # noqa: E402
import multisite_downloader.workers.download as download_module  # noqa: E402
import multisite_downloader.workers.preview as preview_module  # noqa: E402
from multisite_downloader.bilibili import (  # noqa: E402
    _build_bili_session,
    resolve_bili_login,
    resolve_bilibili_url,
)
from multisite_downloader.config import DEFAULT_SETTINGS, has_ffmpeg  # noqa: E402
from multisite_downloader.ui.main_window import MainWindow  # noqa: E402
from multisite_downloader.workers.cookie import CookieCheckWorker  # noqa: E402
from multisite_downloader.workers.download import DownloadWorker  # noqa: E402
from multisite_downloader.workers.preview import PreviewWorker  # noqa: E402
from multisite_downloader.workers.thumbnail import ThumbnailWorker  # noqa: E402

BILI_URL = "https://www.bilibili.com/video/BV1xx411c7mD"


@pytest.fixture(scope="module")
def qt_app():
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture(autouse=True)
def silence_runtime_log(monkeypatch):
    """测试不应把操作日志写进用户正在使用的 download/runtime.log。"""
    monkeypatch.setattr(
        "multisite_downloader.ui.main_window.write_runtime_log", lambda message: None
    )
    monkeypatch.setattr("multisite_downloader.logs.write_runtime_log", lambda message: None)
    monkeypatch.setattr("multisite_downloader.bilibili.write_runtime_log", lambda message: None)


@pytest.fixture(autouse=True)
def isolate_queue_io(monkeypatch):
    """默认隔离真实 queue.json，避免测试恢复/覆盖用户队列。"""
    monkeypatch.setattr("multisite_downloader.ui.main_window.load_queue", lambda: [])
    monkeypatch.setattr(
        "multisite_downloader.ui.main_window.save_queue", lambda tasks: (True, None)
    )
    monkeypatch.setattr(
        "multisite_downloader.ui.main_window.save_settings", lambda settings: (True, None)
    )


def test_paths_point_to_project_root():
    """BASE_DIR 必须落在项目根目录，不能跑进 multisite_downloader/ 包里。

    这是包结构重构最容易踩的坑：__file__ 从根目录变成了 multisite_downloader/config.py。
    """
    from multisite_downloader import config

    root = Path(__file__).resolve().parent
    assert config.BASE_DIR == root
    assert config.SETTINGS_PATH == root / "settings.json"
    assert config.DEFAULT_DOWNLOAD_DIR == root / "download"
    assert config.HISTORY_PATH == root / "download" / "history.json"
    assert config.CRASH_LOG_PATH == root / "crash.log"
    assert config.RESOURCE_DIR == root
    assert not (root / "multisite_downloader" / "settings.json").exists()


def test_main_window_builds(qt_app):
    window = MainWindow()
    try:
        assert window.content_stack.count() == 3
        window.switch_page(1)
        assert window.content_stack.currentIndex() == 1
        window.switch_page(0)
        window.refresh_history()
        window.clear_preview()
        window.update_window_title()
        assert window.formats_table.rowCount() == 0
    finally:
        # 不调用 close()：closeEvent 会回写 settings.json，测试不应改动用户配置
        window.deleteLater()


def show_formats(window, formats=None):
    """模拟预览回调：format 列表与表格一起就绪（真实流程见 on_preview_ready）。"""
    window.preview_formats = [dict(f) for f in (formats or FORMATS_SAMPLE)]
    window.fill_formats_table(window.preview_formats)


def bili_stream_sample():
    """模拟真实 B站 DASH 返回：4 分辨率 × 3 编码 + 3 条音频，没有任何自带声音的流。"""
    formats = []
    for audio_id, tbr in (("30216", 66), ("30232", 96), ("30280", 165)):
        formats.append({"format_id": audio_id, "ext": "m4a", "vcodec": "none",
                        "acodec": "mp4a.40.2", "width": 0, "height": 0, "fps": 0,
                        "filesize": tbr * 1000, "tbr": tbr, "note": ""})
    codecs = ("avc1.640033", "hvc1.1.6.L120.90", "av01.0.08M.08")
    for height, ids in ((360, ("30016", "30011", "100022")),
                        (480, ("30032", "30033", "100023")),
                        (720, ("30064", "30066", "100024")),
                        (1080, ("30080", "30077", "100026"))):
        for format_id, vcodec in zip(ids, codecs):
            formats.append({"format_id": format_id, "ext": "mp4", "vcodec": vcodec,
                            "acodec": "none", "width": int(height * 16 / 9), "height": height,
                            "fps": 30, "filesize": 1000000, "tbr": 1000, "note": ""})
    return formats


def test_grouped_format_view_has_sound_for_every_row(qt_app):
    """默认视图按分辨率合并：每一行都是「视频+音频」，不会再列出无声的纯视频流。"""
    window = MainWindow()
    try:
        window.custom_format_edit.clear()
        show_formats(window, bili_stream_sample())
        table = window.formats_table
        types = [table.item(row, 2).text() for row in range(table.rowCount())]
        assert types.count("视频+音频") == 4, types
        assert types.count("音频") == 1, types
        assert all(t != "视频" for t in types), types
        for row in range(table.rowCount()):
            format_id = table.item(row, 1).text()
            assert format_id == "30280" or format_id.endswith("+30280"), format_id
        assert table.item(0, 3).text() == "1920x1080"
        assert table.item(0, 0).text() == "推荐"

        # 勾选后可以看到并单独选择原始流
        window.show_streams_check.setChecked(True)
        raw_types = [table.item(row, 2).text() for row in range(table.rowCount())]
        assert raw_types.count("视频") == 12, raw_types
        assert raw_types.count("音频") == 3, raw_types
    finally:
        window.deleteLater()


def test_no_clipped_text_or_orphan_widgets(qt_app):
    """界面体检：文字不能被裁、设置项不能脱离布局（曾是"文字显示不全"的根因）。

    回归点：
    1. 侧边栏宽度不足时，项目名称会被裁切；
    2. 在已有布局的卡片上再 QGridLayout(card)，布局安装失败，
       下载设置/网络与文件名两张卡片的控件全部没有父控件、不显示。
    """
    window = MainWindow()
    try:
        window.resize(1280, 860)
        window.show()
        problems = []
        for page in range(3):
            window.switch_page(page)
            for _ in range(4):
                qt_app.processEvents()
            for cls in (QLabel, QPushButton, QCheckBox):
                for widget in window.findChildren(cls):
                    if not widget.isVisible():
                        continue
                    if isinstance(widget, QLabel) and widget.pixmap() and not widget.pixmap().isNull():
                        continue
                    text = widget.text()
                    if not text:
                        continue
                    if isinstance(widget, QLabel) and widget.wordWrap():
                        needed = widget.heightForWidth(max(widget.width(), 1))
                        if widget.height() < needed - 2:
                            problems.append(f"高度不足 {text[:16]!r}")
                    else:
                        if widget.width() < widget.sizeHint().width() - 2:
                            problems.append(f"宽度不足 {text[:16]!r}")
                        if widget.height() < widget.sizeHint().height() - 2:
                            problems.append(f"高度不足 {text[:16]!r}")
        assert not problems, "有控件文字被裁切: " + "; ".join(problems[:6])

        # 设置页的控件必须真正挂进布局并可见
        window.switch_page(2)
        for _ in range(3):
            qt_app.processEvents()
        for name, widget in (
            ("quality_combo", window.quality_combo),
            ("codec_combo", window.codec_combo),
            ("audio_quality_combo", window.audio_quality_combo),
            ("concurrent_spin", window.concurrent_spin),
            ("custom_format_edit", window.custom_format_edit),
            ("dir_edit", window.dir_edit),
            ("proxy_edit", window.proxy_edit),
            ("template_edit", window.template_edit),
        ):
            assert widget.parentWidget() is not None, f"{name} 脱离了布局"
            assert widget.isVisible(), f"{name} 不可见"
    finally:
        window.deleteLater()


def test_fill_formats_table_renders_rows(qt_app):
    """预览拿到格式后，表格必须真的填出行。

    回归点：重构时把 FFMPEG_EXE.exists() 批量替换成 has_ffmpeg()，
    撞上函数内原有局部变量 has_ffmpeg，导致整张格式表空白。
    """
    window = MainWindow()
    try:
        formats = [
            {"format_id": "30080", "ext": "mp4", "vcodec": "avc1.640032", "acodec": "none",
             "width": 1920, "height": 1080, "fps": 30, "filesize": 1000000, "tbr": 2000, "note": ""},
            {"format_id": "30280", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2",
             "width": 0, "height": 0, "fps": 0, "filesize": 200000, "tbr": 128, "note": ""},
        ]
        show_formats(window, formats)
        # 默认分组视图：1 个「1080P 视频+音频」行 + 1 个「仅音频」行
        assert window.formats_table.rowCount() == 2
        expected_format = "30080+30280" if has_ffmpeg() else "30080"
        assert window.formats_table.item(0, 1).text() == expected_format
        assert window.formats_table.item(0, 3).text() == "1920x1080"
        assert window.formats_table.item(1, 2).text() == "音频"
        window.fill_formats_table([])
        assert window.formats_table.rowCount() == 0
    finally:
        window.deleteLater()


FORMATS_SAMPLE = [
    {"format_id": "30080", "ext": "mp4", "vcodec": "avc1.640032", "acodec": "none",
     "width": 1920, "height": 1080, "fps": 30, "filesize": 1000000, "tbr": 2000, "note": ""},
    {"format_id": "30280", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2",
     "width": 0, "height": 0, "fps": 0, "filesize": 200000, "tbr": 128, "note": ""},
]


def test_intermediate_file_detection():
    """yt-dlp 的分片/临时文件不能被当成成品，否则会多留一份无声视频。"""
    assert DownloadWorker._is_intermediate_file("video.f30080.mp4")
    assert DownloadWorker._is_intermediate_file("video.mp4.part")
    assert DownloadWorker._is_intermediate_file("video.mp4.ytdl")
    assert not DownloadWorker._is_intermediate_file("video.mp4")


def test_dedupe_input_urls():
    """同一个视频/同一个分 P 只能保留一条，避免一次点击下载两次。"""
    urls = [
        "BV1xx411c7mD?t=1",
        "https://www.bilibili.com/video/BV1xx411c7mD?t=2",
        "BV1xx411c7mD?p=2",
        "https://youtu.be/abc",
        "https://youtu.be/abc",
    ]
    result = MainWindow._dedupe_input_urls(urls)
    assert result == [
        "https://www.bilibili.com/video/BV1xx411c7mD",
        "https://www.bilibili.com/video/BV1xx411c7mD?p=2",
        "https://youtu.be/abc",
    ], result


def test_format_selection_is_visible(qt_app):
    """选中格式后必须在下载页看到：提示条变绿 + 表格出现「✔ 已选」，清除后复原。"""
    window = MainWindow()
    try:
        window.custom_format_edit.clear()
        show_formats(window)
        assert "自动" in window.selected_format_label.text()

        window.formats_table.selectRow(0)
        window.use_selected_format()
        expected = "30080+30280" if has_ffmpeg() else "30080"
        assert window.custom_format_edit.text() == expected
        assert "自定义格式：" in window.selected_format_label.text()
        assert "1920x1080" in window.selected_format_label.text()
        marked = window.formats_table.item(0, 0).text()
        assert marked.startswith("✔ 已选")

        # 只允许当前真正使用的那一行带「✔ 已选」，不能把所有包含同一音频流的行都标上
        marked_rows = [
            row for row in range(window.formats_table.rowCount())
            if window.formats_table.item(row, 0).text().startswith("✔ 已选")
        ]
        assert marked_rows == [0], marked_rows

        window.clear_selected_format()
        assert window.custom_format_edit.text() == ""
        assert "自动" in window.selected_format_label.text()
        assert not window.formats_table.item(0, 0).text().startswith("✔ 已选")

        # 选了一个当前链接没有的格式时给出警告态
        window.custom_format_edit.setText("99999")
        window.refresh_selected_format_label()
        assert "当前视频没有这个格式" in window.selected_format_label.text()

        # 点画质卡片 = 退出自定义格式，交还给画质自动选择
        window.select_quality("1080")
        assert window.custom_format_edit.text() == ""
        assert "自动" in window.selected_format_label.text()
        assert not any(
            window.formats_table.item(row, 0).text().startswith("✔ 已选")
            for row in range(window.formats_table.rowCount())
        )
    finally:
        window.deleteLater()


def test_format_choice_resolution(qt_app):
    """纯视频流必须自动补音频流，否则下载出来是没有声音的视频。"""
    window = MainWindow()
    try:
        # 原始流视图下才有单独的纯视频流/纯音频流行
        window.custom_format_edit.clear()
        window.show_streams_check.setChecked(True)
        show_formats(window)
        assert window.formats_table.item(0, 0).text() == "视频流"
        assert window.formats_table.item(1, 0).text() == "音频流"

        chosen, hint = window._resolve_format_choice(0, "30080")
        if has_ffmpeg():
            assert chosen == "30080+30280"
            assert "补上音频流" in hint
        else:
            assert chosen == "30080"
            assert "没有声音" in hint

        chosen, hint = window._resolve_format_choice(1, "30280")
        assert chosen == "30280"
        assert "仅音频" in hint

        # 下载前兜底：设置里残留的纯视频流也会被自动补上音频
        if has_ffmpeg():
            assert window._ensure_audio_track("30080") == "30080+30280"
        assert window._ensure_audio_track("30080+30280") == "30080+30280"
        assert window._ensure_audio_track("") == ""

        if has_ffmpeg():
            combo_row = window._format_row_for("30080+30280")
            assert combo_row is not None
            chosen, hint = window._resolve_format_choice(combo_row, "30080+30280")
            assert chosen == "30080+30280"
            assert "需要 ffmpeg 合并" in hint
    finally:
        window.deleteLater()


def test_queue_counts_ignore_deleted(qt_app):
    """「已从队列删除」的任务不应计入进度分母。"""
    window = MainWindow()
    try:
        window.table.setSortingEnabled(False)
        window.table.setRowCount(3)
        for row in range(3):
            window.table.setItem(row, 0, QTableWidgetItem(f"url{row}"))
            window.table.setItem(row, 1, QTableWidgetItem("等待"))
        assert window._queue_counts() == (3, 0)
        window.table.item(1, 1).setText("已删除")
        assert window._queue_counts() == (2, 0)
        window.table.item(0, 1).setText("完成")
        window.table.item(2, 1).setText("失败")
        assert window._queue_counts() == (2, 2)
        window._update_progress_bar()
        assert window.progress_bar.value() == 100
    finally:
        window.deleteLater()


def test_bilibili_path_selection(qt_app, tmp_path, monkeypatch):
    import multisite_downloader.cookies as cookies_module

    monkeypatch.setattr(cookies_module, "_STORE_OVERRIDE", tmp_path / "cookie_store")
    settings = dict(DEFAULT_SETTINGS)
    settings["cookie_mode"] = "none"
    worker = DownloadWorker([BILI_URL], settings)
    assert worker.should_use_bili_legacy(BILI_URL) is True, "无 Cookie 时应走兜底接口"
    assert worker.should_use_bili_legacy("https://youtube.com/watch?v=a") is False

    # 只把模式改成 file 但没有文件：不应误判为“已有 Cookie”
    settings["cookie_mode"] = "file"
    settings["cookie_file"] = ""
    worker_without_file = DownloadWorker([BILI_URL], settings)
    assert worker_without_file.should_use_bili_legacy(BILI_URL) is True

    cookie_file = tmp_path / "bilibili_cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n"
        ".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\ttest-value\n",
        encoding="utf-8",
    )
    settings["cookie_mode"] = "none"
    settings["site_cookie_bilibili"] = str(cookie_file)
    settings["cookie_file"] = ""
    monkeypatch.setattr(download_module, "has_ffmpeg", lambda: True)
    worker_with_cookie = DownloadWorker([BILI_URL], settings)
    assert worker_with_cookie.should_use_bili_legacy(BILI_URL) is False, (
        "站点专用 Cookie 存在时，B站应走 yt-dlp 而不是 360P 兜底"
    )

    session = _build_bili_session(settings)
    assert session.cookies.get("SESSDATA") == "test-value"


def test_history_actions_follow_sorted_rows(qt_app, monkeypatch):
    records = [
        {"title": "C title", "status": "completed", "output_path": "c.mp4"},
        {"title": "A title", "status": "failed", "output_path": "a.mp4"},
        {"title": "B title", "status": "cancelled", "output_path": "b.mp4"},
    ]
    monkeypatch.setattr(
        "multisite_downloader.ui.main_window.load_history",
        lambda: [dict(record) for record in records],
    )
    window = MainWindow()
    try:
        window.refresh_history()
        window.history_table.sortItems(0, Qt.AscendingOrder)
        window.history_table.selectRow(0)
        record, index = window.current_history_record()
        assert window.history_table.item(0, 0).text() == "A title"
        assert record["title"] == "A title"
        assert index == 1

        window.history_table.selectRow(1)
        record, index = window.current_history_record()
        assert record["title"] == "B title"
        assert index == 2
    finally:
        window.deleteLater()


def test_history_search_and_status_filter(qt_app, monkeypatch, tmp_path):
    existing = tmp_path / "kept.mp4"
    existing.write_bytes(b"x")
    records = [
        {"title": "Alpha tutorial", "url": "https://a", "status": "completed",
         "output_path": str(existing)},
        {"title": "Beta clip", "url": "https://b", "status": "failed",
         "output_path": str(tmp_path / "missing.mp4")},
        {"title": "Gamma", "url": "https://c", "status": "cancelled",
         "output_path": ""},
    ]
    monkeypatch.setattr(
        "multisite_downloader.ui.main_window.load_history",
        lambda: [dict(record) for record in records],
    )
    window = MainWindow()
    try:
        window.refresh_history()
        assert window.history_table.rowCount() == 3

        window.history_search_edit.setText("alpha")
        assert window.history_table.rowCount() == 1
        assert window.history_table.item(0, 0).text() == "Alpha tutorial"
        assert "1 / 3" in window.history_match_label.text()

        window.history_search_edit.clear()
        missing_index = window.history_status_combo.findData("missing")
        window.history_status_combo.setCurrentIndex(missing_index)
        assert window.history_table.rowCount() == 1
        assert window.history_table.item(0, 0).text() == "Beta clip"
        window.history_table.selectRow(0)
        record, original_index = window.current_history_record()
        assert original_index == 1

        failed_index = window.history_status_combo.findData("failed")
        window.history_status_combo.setCurrentIndex(failed_index)
        window.history_search_edit.setText("https://b")
        assert window.history_table.rowCount() == 1
    finally:
        window.deleteLater()


def test_first_existing_media_skips_sidecar_lines(qt_app, tmp_path):
    missing = tmp_path / "cover.jpg"
    media = tmp_path / "video.mp4"
    media.write_bytes(b"x")
    text = f"{missing}\n{media}\n"
    assert MainWindow._first_existing_media(text) == str(media)
    assert MainWindow._first_existing_media("") == ""


def test_task_settings_dialog_updates_task(qt_app, tmp_path):
    from multisite_downloader.ui.task_settings_dialog import TaskSettingsDialog

    settings = dict(DEFAULT_SETTINGS)
    settings["custom_format"] = "30080+30280"
    task = {"url": BILI_URL, "settings": settings}
    dialog = TaskSettingsDialog(task)
    try:
        index = dialog.quality_combo.findData("720")
        dialog.quality_combo.setCurrentIndex(index)
        dialog.dir_edit.setText(str(tmp_path))
        dialog.thumbnail_check.setChecked(True)
        dialog._save()
        result = dialog.result_settings()
        assert result["quality"] == "720"
        assert result["download_dir"] == str(tmp_path)
        assert result["custom_format"] == ""
        assert result["download_thumbnail"] is True
    finally:
        dialog.deleteLater()


def test_move_task_swaps_queue_order(qt_app):
    window = MainWindow()
    try:
        tasks = [
            window._task_from_url(url, dict(DEFAULT_SETTINGS))
            for url in ("https://example.com/1", "https://example.com/2", "https://example.com/3")
        ]
        window._populate_queue_table(tasks)
        window.move_task(0, 1)
        urls = [task["url"] for task in window.queue_tasks]
        assert urls[:2] == ["https://example.com/2", "https://example.com/1"]
        window.move_task(2, -1)
        urls = [task["url"] for task in window.queue_tasks]
        assert urls == [
            "https://example.com/2",
            "https://example.com/3",
            "https://example.com/1",
        ]
        assert window.table.rowCount() == 3
    finally:
        window.deleteLater()


def test_cleanup_stale_media_temp(qt_app, tmp_path, monkeypatch):
    import tempfile
    import time as time_module

    from multisite_downloader.ui import preview_player

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    preview_dir = tmp_path / "multisite-downloader_previews"
    cover_dir = tmp_path / "multisite-downloader_covers"
    preview_dir.mkdir()
    cover_dir.mkdir()
    old_preview = preview_dir / "old.mp4"
    new_preview = preview_dir / "new.mp4"
    old_cover = cover_dir / "old.png"
    for path in (old_preview, new_preview, old_cover):
        path.write_bytes(b"x")
    old_time = time_module.time() - 3 * 24 * 3600
    os.utime(old_preview, (old_time, old_time))
    os.utime(old_cover, (old_time, old_time))

    preview_player.cleanup_stale_media_temp()

    assert not old_preview.exists()
    assert not old_cover.exists()
    assert new_preview.exists()


def test_proxy_test_worker_reports_success(qt_app, monkeypatch):
    import multisite_downloader.workers.proxy as proxy_module

    captured = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

    class FakeSession:
        def __init__(self):
            self.proxies = {}

        def get(self, url, **kwargs):
            captured["url"] = url
            captured["proxies"] = dict(self.proxies)
            return FakeResponse()

        def close(self):
            pass

    monkeypatch.setattr(proxy_module.requests, "Session", FakeSession)
    worker = proxy_module.ProxyTestWorker("http://127.0.0.1:7890")
    results = []
    worker.result.connect(lambda ok, message: results.append((ok, message)))
    worker.run()
    assert results and results[0][0] is True
    assert "HTTP 200" in results[0][1]
    assert captured["proxies"]["https"] == "http://127.0.0.1:7890"


def test_clear_finished_queue_tasks_keeps_failed(qt_app):
    window = MainWindow()
    try:
        tasks = [
            window._task_from_url("https://example.com/1", dict(DEFAULT_SETTINGS)),
            window._task_from_url("https://example.com/2", dict(DEFAULT_SETTINGS)),
            window._task_from_url("https://example.com/3", dict(DEFAULT_SETTINGS)),
        ]
        tasks[0]["status"] = "完成"
        tasks[1]["status"] = "失败"
        tasks[2]["status"] = "已取消"
        window._populate_queue_table(tasks)
        window.clear_finished_queue_tasks()
        assert len(window.queue_tasks) == 1
        assert window.queue_tasks[0]["url"] == "https://example.com/2"
        assert window.table.rowCount() == 1
    finally:
        window.deleteLater()


def test_quality_cards_show_all_douyin_page_qualities(qt_app):
    window = MainWindow()
    try:
        formats = [
            {"height": height, "width": round(height * 16 / 9)}
            for height in (2160, 1440, 1080, 720, 540)
        ]
        options = window._quality_options_from_formats(formats)
        window._last_preview_info = {
            "url": "https://www.douyin.com/video/1",
            "qualities": [{"label": "智能", "height": 0}],
        }
        window._rebuild_quality_cards(options)
        assert list(window.quality_cards) == [
            "best", "2160", "1440", "1080", "720", "540",
        ]
        assert window.quality_cards["best"].title_label.text() == "智能"
        assert window.quality_cards["2160"].title_label.text() == "4K"
        assert window.quality_cards["1440"].title_label.text() == "2K"
        assert window.quality_cards["1080"].title_label.text() == "1080P"
        assert window.quality_cards["540"].title_label.text() == "540P"
    finally:
        window.deleteLater()


def test_worker_skip_emits_skipped(qt_app):
    worker = DownloadWorker([BILI_URL], dict(DEFAULT_SETTINGS))
    skipped, failed = [], []
    worker.item_skipped.connect(lambda index, reason: skipped.append((index, reason)))
    worker.item_failed.connect(lambda index, error: failed.append((index, error)))
    worker.skip_index(0)
    assert worker._process_one(0, BILI_URL) == "skipped"
    assert skipped == [(0, "已从队列删除")]
    assert failed == []


def test_cancelled_worker_marks_unstarted_tasks(qt_app, tmp_path):
    settings = dict(DEFAULT_SETTINGS)
    settings["download_dir"] = str(tmp_path)
    worker = DownloadWorker(["u1", "u2"], settings)
    cancelled, done = [], []
    worker.item_cancelled.connect(cancelled.append)
    worker.all_done.connect(done.append)

    def fake_process(index, raw):
        with worker._state_lock:
            worker._started_indices.add(index)
        worker.cancelled = True
        return "cancelled"

    worker._process_one = fake_process
    worker.run()
    assert cancelled == [1]
    assert done == [False]


def test_skipped_and_cancelled_rows_are_not_failures(qt_app):
    window = MainWindow()
    try:
        window.table.setSortingEnabled(False)
        window.table.setRowCount(2)
        for row in range(2):
            window.table.setItem(row, 0, QTableWidgetItem(f"url{row}"))
            window.table.setItem(row, 1, QTableWidgetItem("等待"))
            window.table.setItem(row, 2, QTableWidgetItem("0%"))

        window.on_item_skipped(0, "已从队列删除")
        assert window.table.item(0, 1).text() == "已删除"
        assert window._queue_counts() == (1, 0)

        window.on_item_cancelled(1)
        assert window.table.item(1, 1).text() == "已取消"
        assert window._queue_counts() == (1, 1)
        assert window.progress_bar.value() == 100
    finally:
        window.deleteLater()


def test_overall_progress_sums_concurrent_tasks(qt_app):
    window = MainWindow()
    try:
        window.table.setSortingEnabled(False)
        window.table.setRowCount(2)
        for row in range(2):
            window.table.setItem(row, 1, QTableWidgetItem("下载中"))
        window._task_progress = {0: 50.0, 1: 50.0}
        assert window._overall_progress() == 50

        window.table.item(0, 1).setText("完成")
        window._task_progress = {1: 50.0}
        assert window._overall_progress() == 75
    finally:
        window.deleteLater()


def test_bilibili_legacy_merge_failure_cleans_temp(qt_app, tmp_path, monkeypatch):
    settings = dict(DEFAULT_SETTINGS)
    settings["download_dir"] = str(tmp_path)
    settings["cookie_mode"] = "none"
    worker = DownloadWorker([], settings)

    class FakeResponse:
        headers = {}

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=262144):
            yield b"x" * 100

    class FakeSession:
        def get(self, *args, **kwargs):
            return FakeResponse()

    worker.request_session = lambda: FakeSession()
    monkeypatch.setattr(
        download_module,
        "bili_view",
        lambda url, settings: {
            "title": "T",
            "pages": [{"page": 1, "cid": 1}],
            "owner": {"name": "U"},
        },
    )
    monkeypatch.setattr(
        download_module,
        "bili_playurl",
        lambda *args, **kwargs: {
            "quality": 16,
            "durl": [{"url": "http://x/1", "size": 100}, {"url": "http://x/2", "size": 100}],
        },
    )
    monkeypatch.setattr(download_module, "has_ffmpeg", lambda: True)
    monkeypatch.setattr(
        download_module, "ffmpeg_path", lambda: tmp_path / "ffmpeg.exe"
    )
    monkeypatch.setattr(
        download_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=1, stdout="", stderr="boom"
        ),
    )

    with pytest.raises(RuntimeError, match="ffmpeg 合并分段失败"):
        worker.download_bili_legacy(0, BILI_URL)
    assert sorted(path.name for path in tmp_path.iterdir()) == []


def test_audio_quality_requests_mp3(qt_app, monkeypatch):
    settings = dict(DEFAULT_SETTINGS)
    settings["quality"] = "audio"
    monkeypatch.setattr(download_module, "has_ffmpeg", lambda: True)
    options = DownloadWorker([], settings).build_ytdlp_options(0)
    assert options["format"].startswith("bestaudio")
    assert options["postprocessors"][0]["preferredcodec"] == "mp3"


def test_speed_limit_sets_ytdlp_ratelimit(qt_app):
    settings = dict(DEFAULT_SETTINGS)
    settings["speed_limit_kb"] = 800
    options = DownloadWorker([], settings).build_ytdlp_options(0)
    assert options["ratelimit"] == 800 * 1024


def test_speed_limit_throttles_direct_download(monkeypatch):
    settings = dict(DEFAULT_SETTINGS)
    settings["speed_limit_kb"] = 100
    worker = DownloadWorker([], settings)
    clock = [0.0]
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(download_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(download_module.time, "sleep", fake_sleep)
    worker._throttle_transfer(clock[0], 50 * 1024)
    assert 0.49 <= sum(sleeps) <= 0.51


def test_speed_limit_zero_does_not_throttle(monkeypatch):
    worker = DownloadWorker([], dict(DEFAULT_SETTINGS))
    sleeps = []
    monkeypatch.setattr(download_module.time, "sleep", sleeps.append)
    worker._throttle_transfer(0.0, 10 * 1024 * 1024)
    assert sleeps == []


def test_cookie_check_worker_loads_netscape_file(tmp_path):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n"
        ".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\ttest-value\n",
        encoding="utf-8",
    )
    settings = dict(DEFAULT_SETTINGS)
    settings["cookie_mode"] = "file"
    settings["cookie_file"] = str(cookie_file)
    cookies = CookieCheckWorker(settings).load_cookies()
    assert cookies["SESSDATA"] == "test-value"


def test_history_record_uses_real_title(qt_app):
    worker = DownloadWorker([], dict(DEFAULT_SETTINGS))
    records = []
    worker.item_history.connect(records.append)
    worker.current_titles[0] = "真实视频标题"
    worker.emit_history(0, BILI_URL, "", "failed", "boom", 0)
    assert records[0]["title"] == "真实视频标题"


def test_clear_input_resets_format_and_tags(qt_app):
    window = MainWindow()
    try:
        show_formats(window)
        window.custom_format_edit.setText("30080+30280")
        window._render_tags(["测试标签"])
        window.clear_input()
        assert window.custom_format_edit.text() == ""
        assert "自动" in window.selected_format_label.text()
        assert not window.tags_widget.isVisible()
    finally:
        window.deleteLater()


def test_douyin_format_choice_does_not_leak_custom_format(qt_app):
    window = MainWindow()
    try:
        formats = [{
            "format_id": "douyin-browser",
            "ext": "mp4",
            "vcodec": "h264",
            "acodec": "aac",
            "width": 1024,
            "height": 576,
            "fps": 0,
            "filesize": 0,
            "tbr": 0,
            "note": "浏览器捕获",
        }]
        window.custom_format_edit.setText("30080+30280")
        window.preview_formats = formats
        window.fill_formats_table(formats)
        window.formats_table.selectRow(0)
        window.use_selected_format()
        assert window.custom_format_edit.text() == ""
        assert window._format_source_url == ""
    finally:
        window.deleteLater()


def test_custom_format_site_isolation(qt_app):
    window = MainWindow()
    try:
        window.preview_url_label.setText(BILI_URL)
        window.custom_format_edit.setText("30080+30280")
        window._format_source_url = BILI_URL
        assert window._custom_format_compatible([BILI_URL])
        assert not window._custom_format_compatible(["https://youtu.be/abc"])
        assert not window._custom_format_compatible(
            [BILI_URL, "https://youtu.be/abc"]
        )
        assert window._custom_format_site_hint("30080+30280") == "bilibili"
        assert window._custom_format_site_hint("douyin-browser") == "douyin"
        assert window._custom_format_site_hint("137+140") == ""
    finally:
        window.deleteLater()


def test_douyin_audio_only_writes_mp3(qt_app, tmp_path, monkeypatch):
    import multisite_downloader.douyin_browser as douyin_module

    settings = dict(DEFAULT_SETTINGS)
    settings["download_dir"] = str(tmp_path)
    settings["quality"] = "audio"
    worker = DownloadWorker([], settings)
    data = {
        "title": "测试视频",
        "author": "作者",
        "video_url": "http://example.com/video",
        "audio_url": "http://example.com/audio",
        "cookie_header": "",
        "width": 1024,
        "height": 576,
    }
    monkeypatch.setattr(download_module, "has_ffmpeg", lambda: True)
    monkeypatch.setattr(
        douyin_module, "get_cached_douyin", lambda url, quality=None: data
    )
    monkeypatch.setattr(douyin_module, "capture_douyin_media", lambda *a, **k: data)

    def fake_download(session, url, path, index, start_pct, end_pct, label):
        Path(path).write_bytes(b"part")

    def fake_merge(index, output_path, video_path, audio_path):
        Path(output_path).write_bytes(b"merged")

    def fake_extract(index, source_path, output_path):
        assert Path(source_path).is_file()
        Path(output_path).write_bytes(b"mp3")

    worker._download_stream = fake_download
    worker._merge_douyin = fake_merge
    worker._extract_douyin_audio = fake_extract

    outputs = worker.download_douyin_browser(
        0, "https://www.douyin.com/video/123"
    )
    assert len(outputs) == 1
    output = Path(outputs[0])
    assert output.name == "测试视频 [123].mp3"
    assert output.read_bytes() == b"mp3"
    assert not list(tmp_path.glob("*.part"))
    assert not list(tmp_path.glob("*.merged.mp4"))


def test_wait_for_resume(qt_app):
    worker = DownloadWorker([], dict(DEFAULT_SETTINGS))
    worker.paused = True
    timer = threading.Timer(0.1, worker.resume)
    timer.start()
    worker.wait_for_resume()
    timer.join()
    assert worker.paused is False

    worker.paused = True
    worker.cancelled = True
    with pytest.raises(RuntimeError):
        worker.wait_for_resume()


def test_resolve_bili_login_configured(qt_app, monkeypatch):
    bilibili_module.invalidate_bili_login_cache()
    settings = dict(DEFAULT_SETTINGS)
    settings["cookie_mode"] = "file"
    settings["cookie_file"] = "qoder-test-cookie-file"

    class LoggedInResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"code": 0, "data": {"isLogin": True, "uname": "tester"}}

    class LoggedInSession:
        def get(self, *args, **kwargs):
            return LoggedInResponse()

    monkeypatch.setattr(
        bilibili_module, "_build_bili_session", lambda settings: LoggedInSession()
    )
    assert bilibili_module.resolve_bili_login(
        settings, allow_browser=False
    ) == (True, "当前配置")

    bilibili_module.invalidate_bili_login_cache()
    settings["cookie_file"] = "qoder-test-cookie-file-invalid"

    class LoggedOutResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"code": -101, "data": {}}

    class LoggedOutSession:
        def get(self, *args, **kwargs):
            return LoggedOutResponse()

    monkeypatch.setattr(
        bilibili_module, "_build_bili_session", lambda settings: LoggedOutSession()
    )
    assert bilibili_module.resolve_bili_login(
        settings, allow_browser=False
    ) == (False, "")
    bilibili_module.invalidate_bili_login_cache()


def test_downloader_uses_validated_bili_login(qt_app, monkeypatch):
    monkeypatch.setattr(download_module, "has_ffmpeg", lambda: True)
    worker = DownloadWorker([BILI_URL], dict(DEFAULT_SETTINGS))
    worker._check_bili_login = lambda: True
    assert worker.should_use_bili_legacy(BILI_URL, validate=True) is False
    worker._check_bili_login = lambda: False
    assert worker.should_use_bili_legacy(BILI_URL, validate=True) is True


def test_bili_login_hint_visibility(qt_app):
    window = MainWindow()
    try:
        window.settings["quality"] = "best"
        window.custom_format_edit.clear()
        window._last_preview_info = {"url": BILI_URL, "login_ok": False}
        window._update_bili_login_hint()
        assert not window.bili_login_hint.isHidden()

        window.settings["quality"] = "720"
        window._update_bili_login_hint()
        assert window.bili_login_hint.isHidden()

        window.settings["quality"] = "1080"
        window._update_bili_login_hint()
        assert not window.bili_login_hint.isHidden()

        window._last_preview_info = {"url": BILI_URL, "login_ok": True}
        window._update_bili_login_hint()
        assert window.bili_login_hint.isHidden()
    finally:
        window.deleteLater()


def test_douyin_quality_cards_are_locked(qt_app):
    window = MainWindow()
    try:
        window._quality_locked = True
        window._rebuild_quality_cards([(720, 1280)])
        assert window.quality_cards
        assert all(not card.isEnabled() for card in window.quality_cards.values())

        window._quality_locked = False
        window._rebuild_quality_cards([(720, 1280)])
        assert all(card.isEnabled() for card in window.quality_cards.values())
    finally:
        window.deleteLater()


def test_douyin_format_rows_set_quality(qt_app):
    window = MainWindow()
    try:
        formats = [{
            "format_id": "douyin-1080",
            "ext": "mp4",
            "vcodec": "hevc",
            "acodec": "aac",
            "width": 1920,
            "height": 1080,
            "fps": 0,
            "filesize": 0,
            "tbr": 0,
            "note": "页面画质",
        }]
        window.preview_formats = formats
        window.fill_formats_table(formats)
        window.formats_table.selectRow(0)
        window.use_selected_format()
        assert window.custom_format_edit.text() == ""
        assert window.settings["quality"] == "1080"
    finally:
        window.deleteLater()


def test_worker_uses_per_task_settings_and_active_indices(
    qt_app, monkeypatch, tmp_path
):
    base = dict(DEFAULT_SETTINGS)
    base["download_dir"] = str(tmp_path)
    base["concurrent_downloads"] = 1
    task_1 = dict(base)
    task_1["quality"] = "best"
    task_2 = dict(base)
    task_2["quality"] = "720"
    worker = DownloadWorker(
        ["https://example.com/1", "https://example.com/2"],
        base,
        task_settings=[task_1, task_2],
        active_indices=[1],
    )
    seen = []

    def fake_download(index, url):
        seen.append((index, worker.current_settings().get("quality")))
        return str(tmp_path / "out.mp4")

    worker.download_with_ytdlp = fake_download
    worker.run()
    assert seen == [(1, "720")]


def test_restore_queue_and_single_retry(qt_app, monkeypatch):
    task_record = {
        "url": BILI_URL,
        "status": "失败",
        "settings": dict(DEFAULT_SETTINGS),
        "error": "boom",
        "detail": "boom",
        "format_full": "自动（最高画质）",
        "quality_label": "最高画质",
    }
    monkeypatch.setattr(
        "multisite_downloader.ui.main_window.load_queue", lambda: [dict(task_record)]
    )
    window = MainWindow()
    try:
        assert window.table.rowCount() == 1
        assert window.table.item(0, 1).text() == "失败"
        assert len(window.queue_tasks) == 1
        assert window._pending_queue_indices() == [0]

        started = []
        monkeypatch.setattr(
            window, "_start_worker", lambda indices: started.extend(indices) or True
        )
        window.retry_task(0)
        assert started == [0]

        started.clear()
        window.input_edit.clear()
        window.start_downloads()
        assert started == [0], "空输入时应继续恢复的队列"
    finally:
        window.deleteLater()


def test_start_downloads_creates_persisted_queue_tasks(qt_app, monkeypatch):
    window = MainWindow()
    try:
        url = "https://example.com/video"
        window.input_edit.setPlainText(url)
        started = []
        monkeypatch.setattr(
            window, "_start_worker", lambda indices: started.extend(indices) or True
        )
        window.start_downloads()
        assert started == [0]
        assert len(window.queue_tasks) == 1
        assert window.queue_tasks[0]["url"] == url
        assert window.queue_tasks[0]["status"] == "等待"
        assert window.queue_tasks[0]["settings"]["quality"]
        assert window.input_edit.toPlainText() == url
    finally:
        window.deleteLater()


def test_preview_without_bili_login_uses_public_api(qt_app, monkeypatch):
    worker = PreviewWorker(1, BILI_URL, dict(DEFAULT_SETTINGS))
    monkeypatch.setattr(
        preview_module,
        "resolve_bili_login",
        lambda settings, allow_browser=True: (False, ""),
    )
    monkeypatch.setattr(
        worker,
        "fetch_bili_legacy_preview",
        lambda reason: {"url": BILI_URL, "formats": [], "pages": []},
    )
    monkeypatch.setattr(
        worker,
        "fetch_with_ytdlp",
        lambda: (_ for _ in ()).throw(AssertionError("不应调用 yt-dlp")),
    )
    infos = []
    worker.info_ready.connect(lambda request_id, info: infos.append((request_id, info)))
    worker.run()
    assert infos
    assert infos[0][1]["login_ok"] is False
    assert "720P" in infos[0][1]["note"]


def test_resolve_bili_login_reuses_browser_cookies(qt_app, monkeypatch):
    import types

    bilibili_module.invalidate_bili_login_cache()
    settings = dict(DEFAULT_SETTINGS)
    settings["cookie_mode"] = "none"
    settings["cookie_file"] = "qoder-browser-test"
    configured_session = object()
    browser_session = object()

    monkeypatch.setattr(
        bilibili_module,
        "_build_bili_session",
        lambda settings: configured_session,
    )
    monkeypatch.setattr(
        bilibili_module,
        "_bili_session_with_jar",
        lambda settings, jar: browser_session,
    )
    monkeypatch.setattr(
        bilibili_module,
        "bili_login_status",
        lambda session: (session is browser_session, {}),
    )
    saved = []
    monkeypatch.setattr(
        bilibili_module,
        "save_cookie_jar",
        lambda domain, jar: saved.append((domain, list(jar))) or "saved-path",
    )
    monkeypatch.setattr(bilibili_module, "write_runtime_log", lambda message: None)
    fake_browser_cookie3 = types.SimpleNamespace(
        firefox=lambda domain_name=None: ["cookie"],
        edge=lambda domain_name=None: [],
        chrome=lambda domain_name=None: [],
    )
    monkeypatch.setitem(sys.modules, "browser_cookie3", fake_browser_cookie3)

    ok, source = bilibili_module.resolve_bili_login(settings, allow_browser=True)
    assert ok is True
    assert source == "浏览器 Firefox"
    assert saved and saved[0][0] == "bilibili.com"
    bilibili_module.invalidate_bili_login_cache()


def test_b23_short_link_resolution(qt_app, monkeypatch):
    class FakeResponse:
        url = "https://www.bilibili.com/video/BV1xx411c7mD?p=2"

    class FakeSession:
        def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(
        bilibili_module, "_build_bili_session", lambda settings: FakeSession()
    )
    resolved = resolve_bilibili_url("https://b23.tv/abcdefg", {})
    assert resolved == "https://www.bilibili.com/video/BV1xx411c7mD?p=2"


def test_workers_can_be_constructed(qt_app):
    settings = dict(DEFAULT_SETTINGS)
    preview = PreviewWorker(1, "BV1xx411c7mD", settings)
    assert preview.ytdlp_options()["skip_download"] is True
    assert preview.url == BILI_URL  # BV 号会被补全成完整链接

    assert DownloadWorker([BILI_URL], settings).build_ytdlp_options(0)["outtmpl"]
    assert CookieCheckWorker(settings).load_cookies() == {}
    assert ThumbnailWorker("https://example.com/a.png").url.endswith("a.png")
