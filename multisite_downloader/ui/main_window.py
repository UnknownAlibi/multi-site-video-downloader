"""主窗口：标题栏、侧边栏、下载/历史/设置三页与全部交互逻辑。"""

import csv
import json
import platform
import random
import shutil
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

from PyQt5.QtCore import QSize, QTimer, Qt
from PyQt5.QtGui import QColor, QIcon, QKeySequence, QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QShortcut, QSizeGrip, QSpinBox, QStackedWidget, QSystemTrayIcon,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
)

from ..bilibili import invalidate_bili_login_cache
from ..config import (
    ASSETS_DIR,
    AUDIO_QUALITY_LABELS,
    BASE_DIR,
    CODEC_LABELS,
    DEFAULT_DOWNLOAD_DIR,
    DEFAULT_SETTINGS,
    QUALITY_LABELS,
    RESOURCE_DIR,
    ffmpeg_path,
    has_ffmpeg,
)
from ..errors import format_error
from ..history import (
    append_history_record,
    load_history,
    remove_history_record,
    save_history,
)
from ..logs import write_runtime_log
from ..mascot import generate_mascot_image
from ..queue import load_queue, save_queue
from ..settings import load_settings, save_settings
from ..shell import open_file_default, open_path_in_explorer
from ..cookies import (
    cookie_store_dir,
    cookie_store_path,
    has_cookie_for_url,
    list_domain_cookies,
)
from ..sites import (
    COOKIE_REQUIRED_SITES,
    SITE_BILIBILI,
    SITE_DOUYIN,
    cookie_domain_for_url,
    cookie_domains_for_url,
    cookie_file_contains_domain,
    detect_site,
    site_label,
)
from ..urls import (
    extract_aid,
    extract_bvid,
    normalize_input,
    selected_page_number,
)
from ..utils import (
    format_bytes,
    format_count,
    format_duration,
    format_fps,
    format_upload_date,
    split_inputs,
)
from ..workers.cookie import CookieCheckWorker
from ..workers.download import DownloadWorker
from ..workers.preview import PreviewWorker
from ..workers.thumbnail import ThumbnailWorker
from .effects import (
    DroppablePlainTextEdit,
    SakuraOverlay,
    SoundPlayer,
)
from .qrlogin_dialog import QrLoginDialog
from .theme import (
    build_stylesheet,
    get as theme_get,
    is_dark,
    set_mode as set_theme_mode,
)
from .widgets import (
    ChoiceCard,
    CoverLabel,
    ImageSlot,
    QueueProgress,
    SkyBackground,
    SoftCard,
    StatusCell,
    ToggleSwitch,
    make_media_tile,
    make_nav_icon,
    make_tool_icon,
)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings, load_err = load_settings()
        if load_err:
            print(f"设置加载失败，使用默认设置: {load_err}")
        set_theme_mode(self.settings.get("theme", "light"))
        self.worker = None
        self.preview_worker = None
        self.cookie_check_worker = None
        self.proxy_test_worker = None
        self.preview_request_id = 0
        self._force_quit = False
        self.preview_pending = False
        self.preview_formats = []
        self.history_records = load_history()
        self.queue_tasks = []
        self._queue_source_text = ""
        self._history_dirty = False
        self._thumb_workers = []
        self._mascot_state = None
        self._syncing_quality = False
        self._loading_settings = False
        self._quality_options_loaded = False
        self._last_video_quality = "best"
        self._task_progress = {}
        self._preview_was_manual = False
        self._format_source_url = ""
        self._last_preview_info = None
        self._quality_locked = False
        self.quality_cards = {}
        self.format_cards = {}
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self.start_preview)
        self.base_window_title = "多站点视频下载器"
        self.setWindowTitle(self.base_window_title)
        self.resize(1280, 860)
        self.setMinimumSize(1024, 680)
        self._drag_pos = None
        self._is_maximized = False
        # 只设 FramelessWindowHint 时窗口没有 WS_MINIMIZEBOX，
        # Windows 任务栏点击图标不会最小化，Alt+F4 也不可用。
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )
        app_icon = self._app_icon_path()
        if app_icon:
            self.setWindowIcon(QIcon(str(app_icon)))
        self.sound_player = SoundPlayer(RESOURCE_DIR / "sounds")
        self.sakura_overlay = None
        self._overlay_resume_timer = QTimer(self)
        self._overlay_resume_timer.setSingleShot(True)
        self._overlay_resume_timer.setInterval(220)
        self._overlay_resume_timer.timeout.connect(self._resume_overlay_animation)
        # 确保素材目录存在，方便用户直接往里放图片
        try:
            ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._init_mascot_image()
        self.init_ui()
        self.apply_settings_to_ui()
        self.refresh_history()
        self.restore_queue()
        self.init_tray_icon()
        self._setup_shortcuts()
        self.statusBar().showMessage("就绪")
        self.sound_player.play("click")

    def _app_icon_path(self):
        """返回应用图标路径：优先用户提供的 logo，其次内置 icon。"""
        candidates = (
            ASSETS_DIR / "logo.png",
            RESOURCE_DIR / "assets" / "logo.png",
            RESOURCE_DIR / "icon.png",
            RESOURCE_DIR / "icon.ico",
        )
        for path in candidates:
            if path.exists():
                return path
        return None

    def _logo_path(self):
        """侧边栏品牌图标：优先 assets/logo.png，其次应用图标。"""
        for candidate in (ASSETS_DIR / "logo.png", ASSETS_DIR / "logo.ico"):
            if candidate.exists():
                return candidate
        return self._app_icon_path()

    def _init_mascot_image(self):
        """确认侧边栏看板娘图片：优先使用 icon.png，缺失时生成 mascot.png。"""
        icon_path = self._app_icon_path()
        if icon_path and icon_path.suffix.lower() == ".png":
            self.mascot_image_path = icon_path
        else:
            self.mascot_image_path = RESOURCE_DIR / "mascot.png"
            if not self.mascot_image_path.exists():
                try:
                    generate_mascot_image(self.mascot_image_path, size=200)
                except Exception as exc:
                    print("生成看板娘图片失败:", exc)

    def init_tray_icon(self):
        self.tray_icon = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        app_icon = self._app_icon_path()
        icon = QIcon(str(app_icon)) if app_icon else QIcon()
        if icon.isNull():
            return
        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip(self.base_window_title)
        # 右键菜单
        menu = QMenu(self)
        act_show = menu.addAction("显示主窗口")
        act_show.triggered.connect(self._tray_show_window)
        act_pause = menu.addAction("暂停 / 继续")
        act_pause.triggered.connect(self.toggle_pause_queue)
        menu.addSeparator()
        act_quit = menu.addAction("退出")
        act_quit.triggered.connect(self._tray_quit)
        self.tray_icon.setContextMenu(menu)
        # 单击/双击还原
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._tray_show_window()

    def _tray_show_window(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _tray_quit(self):
        self._force_quit = True
        self.close()

    def _setup_shortcuts(self):
        """注册全局键盘快捷键。"""
        # Ctrl+Enter: 开始下载
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.start_downloads)
        # Ctrl+D: 清空输入
        QShortcut(QKeySequence("Ctrl+D"), self, activated=self.clear_input)
        # Ctrl+Shift+V: 粘贴并解析
        QShortcut(QKeySequence("Ctrl+Shift+V"), self, activated=lambda: self.start_preview(force=True))
        # Space: 暂停/继续（焦点在输入框里时不拦截，否则打不出空格）
        QShortcut(QKeySequence("Space"), self, activated=self._shortcut_toggle_pause)
        # Esc: 取消下载（同理，在输入框里按 Esc 不应触发取消）
        QShortcut(QKeySequence("Escape"), self, activated=self._shortcut_cancel)
        # F5: 刷新历史
        QShortcut(QKeySequence("F5"), self, activated=self.refresh_history)
        # Ctrl+1/2/3: 切换页面
        QShortcut(QKeySequence("Ctrl+1"), self, activated=lambda: self.switch_page(0))
        QShortcut(QKeySequence("Ctrl+2"), self, activated=lambda: self.switch_page(1))
        QShortcut(QKeySequence("Ctrl+3"), self, activated=lambda: self.switch_page(2))
        # Ctrl+W: 最小化到托盘
        QShortcut(QKeySequence("Ctrl+W"), self, activated=self._minimize_to_tray)

    @staticmethod
    def _set_roomy_rows(table):
        """把表格行高设足。

        样式表里 QTableWidget::item 的 padding 不计入默认行高，
        默认 31px 会让 14px 文字上下各裁掉一点，这里按字体高度重新给出行高。
        """
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSortIndicatorShown(False)
        table.verticalHeader().setDefaultSectionSize(table.fontMetrics().height() + 18)

    def _input_has_focus(self):
        """当前焦点是否落在文本/数值输入控件上。"""
        return isinstance(
            QApplication.focusWidget(), (QLineEdit, QPlainTextEdit, QComboBox, QSpinBox)
        )

    def _shortcut_toggle_pause(self):
        if self._input_has_focus():
            return
        self.toggle_pause_queue()

    def _shortcut_cancel(self):
        if self._input_has_focus():
            return
        self.cancel_downloads()

    def _minimize_to_tray(self):
        if self.tray_icon:
            self.hide()
            self.tray_icon.showMessage(
                self.base_window_title,
                "已最小化到托盘，单击图标恢复",
                QSystemTrayIcon.Information, 2000,
            )
        else:
            self.showMinimized()

    def create_card(self, title, glow=True, glow_color=None, shadow=True):
        """创建一个带标题的圆角卡片容器。"""
        card = SoftCard(title=title, shadow=shadow)
        card.set_glow_enabled(bool(glow and self.settings.get("fx_neon", False)))
        return card, card.body_layout

    def switch_page(self, index):
        """切换右侧页面，同步侧边栏按钮状态。"""
        self.content_stack.setCurrentIndex(index)
        self.nav_download_btn.setChecked(index == 0)
        self.nav_history_btn.setChecked(index == 1)
        self.nav_settings_btn.setChecked(index == 2)
        if index == 1 and self._history_dirty:
            self.refresh_history()

    def init_ui(self):
        self.sky = SkyBackground(self)
        self.setCentralWidget(self.sky)
        root_layout = QVBoxLayout(self.sky)
        root_layout.setContentsMargins(1, 1, 1, 1)
        root_layout.setSpacing(0)

        # ===== 自定义标题栏 =====
        title_bar = self._build_title_bar()
        root_layout.addWidget(title_bar)

        # ===== 主内容区 =====
        self.body_widget = QWidget()
        self.body_widget.setObjectName("bodyWidget")
        body_layout = QHBoxLayout(self.body_widget)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        # 侧边栏
        sidebar = self._build_sidebar()
        body_layout.addWidget(sidebar)

        # 右侧内容区（含缩放抓手）
        content_container = QWidget()
        content_container.setObjectName("pageRoot")
        content_layout = QVBoxLayout(content_container)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        self.content_stack = QStackedWidget()
        self.content_stack.setObjectName("contentStack")
        content_layout.addWidget(self.content_stack, 1)
        grip_layout = QHBoxLayout()
        grip_layout.setContentsMargins(0, 0, 0, 0)
        grip_layout.addStretch()
        grip_layout.addWidget(QSizeGrip(content_container))
        content_layout.addLayout(grip_layout)
        body_layout.addWidget(content_container, 1)

        root_layout.addWidget(self.body_widget, 1)

        # 樱花 / 光点层：放在内容之后，作为背景装饰，避免盖住文字
        self.sakura_overlay = SakuraOverlay(self.sky, count=22)
        self.sakura_overlay.setGeometry(self.sky.rect())
        self.sakura_overlay.lower()
        self.sakura_overlay.setVisible(bool(self.settings.get("fx_sakura", True)))

        self._build_download_page()
        self._build_history_page()
        self._build_settings_page()
        self._apply_theme()

        self.quality_combo.currentIndexChanged.connect(self._on_quality_combo_changed)
        for checkbox in (self.subtitle_check, self.thumbnail_check, self.danmaku_check):
            checkbox.stateChanged.connect(self._sync_quick_toggles)
        self._sync_quick_toggles()
        self._sync_quality_cards()
        self._refresh_side_path()

    def _resume_overlay_animation(self):
        if (
            self.sakura_overlay is not None
            and self.sakura_overlay.isVisible()
            and self.settings.get("fx_sakura", True)
        ):
            self.sakura_overlay.timer.start(50)

    def _build_title_bar(self):
        """轻量标题栏：只保留右侧标语与窗口控制。"""
        bar = QFrame()
        bar.setObjectName("titleBar")
        bar.setFixedHeight(44)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(18, 0, 12, 0)
        layout.setSpacing(8)
        layout.addStretch()

        slogan = QLabel("把喜欢的世界，下载到身边")
        slogan.setObjectName("titleSlogan")
        layout.addWidget(slogan)

        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("windowCtrl")
        self.theme_btn.setFixedSize(30, 30)
        self.theme_btn.setIcon(make_tool_icon("moon"))
        self.theme_btn.setIconSize(QSize(14, 14))
        self.theme_btn.setToolTip("切换夜间模式")
        self.theme_btn.clicked.connect(self._toggle_theme)
        layout.addWidget(self.theme_btn)

        self.min_btn = QPushButton()
        self.min_btn.setObjectName("windowCtrl")
        self.min_btn.setFixedSize(30, 30)
        self.min_btn.setIcon(make_tool_icon("min"))
        self.min_btn.setIconSize(QSize(14, 14))
        self.min_btn.setToolTip("最小化")
        self.min_btn.clicked.connect(self.showMinimized)

        self.max_btn = QPushButton()
        self.max_btn.setObjectName("windowCtrl")
        self.max_btn.setFixedSize(30, 30)
        self.max_btn.setIcon(make_tool_icon("max"))
        self.max_btn.setIconSize(QSize(14, 14))
        self.max_btn.setToolTip("最大化/还原")
        self.max_btn.clicked.connect(self.toggle_maximize)

        self.close_btn = QPushButton()
        self.close_btn.setObjectName("windowCtrlClose")
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.setIcon(make_tool_icon("close"))
        self.close_btn.setIconSize(QSize(14, 14))
        self.close_btn.setToolTip("关闭")
        self.close_btn.clicked.connect(self.close)

        layout.addWidget(self.min_btn)
        layout.addWidget(self.max_btn)
        layout.addWidget(self.close_btn)

        # 标题栏拖拽
        bar.mousePressEvent = self._title_bar_mouse_press
        bar.mouseMoveEvent = self._title_bar_mouse_move
        bar.mouseReleaseEvent = self._title_bar_mouse_release
        bar.mouseDoubleClickEvent = lambda event: self.toggle_maximize()

        return bar

    def _build_sidebar(self):
        """左侧导航栏：品牌、三个功能页、看板娘。"""
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(204)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(16, 18, 16, 18)
        sidebar_layout.setSpacing(8)

        # 品牌
        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        logo_icon = QLabel()
        logo_icon.setFixedSize(40, 40)
        logo_icon.setAlignment(Qt.AlignCenter)
        logo_icon.setStyleSheet("background: transparent;")
        app_icon = self._logo_path()
        if app_icon:
            logo_icon.setPixmap(
                QPixmap(str(app_icon)).scaled(
                    34, 34, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        logo = QLabel("多站点")
        logo.setObjectName("logoTitle")
        subtitle = QLabel("视频下载器")
        subtitle.setObjectName("logoSubtitle")
        brand_text.addWidget(logo)
        brand_text.addWidget(subtitle)
        brand_row.addWidget(logo_icon)
        brand_row.addLayout(brand_text, 1)
        sidebar_layout.addLayout(brand_row)
        sidebar_layout.addSpacing(20)

        self.nav_download_btn = QPushButton("视频下载")
        self.nav_download_btn.setObjectName("navBtn")
        self.nav_download_btn.setCheckable(True)
        self.nav_download_btn.setChecked(True)
        self.nav_download_btn.setIcon(make_nav_icon("download"))
        self.nav_download_btn.setIconSize(QSize(20, 20))
        self.nav_download_btn.clicked.connect(lambda: self.switch_page(0))
        sidebar_layout.addWidget(self.nav_download_btn)

        self.nav_history_btn = QPushButton("下载历史")
        self.nav_history_btn.setObjectName("navBtn")
        self.nav_history_btn.setCheckable(True)
        self.nav_history_btn.setIcon(make_nav_icon("history"))
        self.nav_history_btn.setIconSize(QSize(18, 18))
        self.nav_history_btn.clicked.connect(lambda: self.switch_page(1))
        sidebar_layout.addWidget(self.nav_history_btn)

        self.nav_settings_btn = QPushButton("偏好设置")
        self.nav_settings_btn.setObjectName("navBtn")
        self.nav_settings_btn.setCheckable(True)
        self.nav_settings_btn.setIcon(make_nav_icon("settings"))
        self.nav_settings_btn.setIconSize(QSize(20, 20))
        self.nav_settings_btn.clicked.connect(lambda: self.switch_page(2))
        sidebar_layout.addWidget(self.nav_settings_btn)

        sidebar_layout.addStretch()

        self.mascot_bubble = QLabel("粘贴链接，开始下载")
        self.mascot_bubble.setObjectName("mascotBubble")
        self.mascot_bubble.setWordWrap(True)
        self.mascot_bubble.setAlignment(Qt.AlignCenter)
        sidebar_layout.addWidget(self.mascot_bubble)

        mascot_paths = [
            BASE_DIR / "assets" / "mascot_left.png",
            RESOURCE_DIR / "assets" / "mascot_left.png",
            BASE_DIR / "mascot_left.png",
            RESOURCE_DIR / "mascot_left.png",
            BASE_DIR / "mascot.png",
            RESOURCE_DIR / "mascot.png",
            self.mascot_image_path,
        ]
        self.left_mascot = ImageSlot(
            mascot_paths,
            "侧边栏立绘\nassets/mascot_left.png\n（透明背景 PNG）",
            min_height=190,
            radius=18,
        )
        sidebar_layout.addWidget(self.left_mascot, 1)

        quote = QLabel("喜欢的视频，就永远留下来吧\n— ( ˘ω˘ )")
        quote.setObjectName("sidebarQuote")
        quote.setAlignment(Qt.AlignCenter)
        sidebar_layout.addWidget(quote)

        return sidebar

    def _title_bar_mouse_press(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def _title_bar_mouse_move(self, event):
        if self._drag_pos is not None and event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos)
            event.accept()

    def _title_bar_mouse_release(self, event):
        self._drag_pos = None
        event.accept()

    def toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def resizeEvent(self, event):
        if self.sakura_overlay and getattr(self, "sky", None):
            self.sakura_overlay.setGeometry(self.sky.rect())
            # 拖动缩放时暂停粒子绘制，停下来后再恢复
            self.sakura_overlay.timer.stop()
            if hasattr(self, "_overlay_resume_timer"):
                self._overlay_resume_timer.start()
        super().resizeEvent(event)
        # 窗口较窄时收起右侧看板栏，保证主操作区不被挤压
        if hasattr(self, "right_panel"):
            visible = self.width() >= 1180
            if self.right_panel.isVisible() != visible:
                self.right_panel.setVisible(visible)

    # ---------- 下载页 ----------

    def _build_download_page(self):
        page = QWidget()
        page.setObjectName("pageRoot")
        outer = QHBoxLayout(page)
        outer.setContentsMargins(16, 10, 16, 14)
        outer.setSpacing(14)

        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget()
        container.setObjectName("pageContainer")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(14)

        # ===== 链接输入（参考设计：整条白色输入胶囊 + 解析按钮） =====
        input_row = QHBoxLayout()
        input_row.setSpacing(10)

        input_shell = QFrame()
        input_shell.setObjectName("inputShell")
        shell_layout = QHBoxLayout(input_shell)
        shell_layout.setContentsMargins(14, 0, 8, 0)
        shell_layout.setSpacing(6)

        link_icon = QLabel()
        link_icon.setFixedSize(18, 18)
        link_icon.setPixmap(make_tool_icon("globe").pixmap(18, 18))
        link_icon.setStyleSheet("background: transparent;")
        shell_layout.addWidget(link_icon, 0, Qt.AlignVCenter)

        self.input_edit = DroppablePlainTextEdit()
        self.input_edit.setObjectName("linkInputInline")
        self.input_edit.setPlaceholderText(
            "粘贴视频链接，或输入 BV 号；支持多行批量下载"
        )
        self.input_edit.setFixedHeight(50)
        self.input_edit.document().setDocumentMargin(15)
        self.input_edit.textChanged.connect(self.schedule_preview)
        self.input_edit.textChanged.connect(self._update_input_clear)
        shell_layout.addWidget(self.input_edit, 1)

        self.import_links_btn = QPushButton()
        self.import_links_btn.setObjectName("iconBtn")
        self.import_links_btn.setFixedSize(28, 28)
        self.import_links_btn.setIcon(make_tool_icon("import"))
        self.import_links_btn.setIconSize(QSize(16, 16))
        self.import_links_btn.setToolTip("从文件导入链接")
        self.import_links_btn.clicked.connect(self.import_links_from_file)
        shell_layout.addWidget(self.import_links_btn, 0, Qt.AlignVCenter)

        self.clear_input_btn = QPushButton()
        self.clear_input_btn.setObjectName("iconBtn")
        self.clear_input_btn.setFixedSize(28, 28)
        self.clear_input_btn.setIcon(make_tool_icon("close"))
        self.clear_input_btn.setIconSize(QSize(15, 15))
        self.clear_input_btn.setToolTip("清空输入")
        self.clear_input_btn.clicked.connect(self.clear_input)
        self.clear_input_btn.setVisible(False)
        shell_layout.addWidget(self.clear_input_btn, 0, Qt.AlignVCenter)

        input_row.addWidget(input_shell, 1)

        self.preview_btn = QPushButton("解析视频")
        self.preview_btn.setObjectName("primaryBtn")
        self.preview_btn.setIcon(make_tool_icon("play", "#FFFFFF"))
        self.preview_btn.setIconSize(QSize(13, 13))
        self.preview_btn.setFixedHeight(50)
        self.preview_btn.setMinimumWidth(132)
        self.preview_btn.clicked.connect(lambda: self.start_preview(force=True))
        input_row.addWidget(self.preview_btn)
        layout.addLayout(input_row)

        # ===== 预览 =====
        preview_card = SoftCard(shadow=True)
        preview_layout = preview_card.body_layout
        preview_layout.setContentsMargins(16, 14, 16, 14)
        preview_top = QHBoxLayout()
        preview_top.setSpacing(16)
        self.cover_label = CoverLabel(300, 168)
        self.cover_label.setToolTip("点击预览；抖音站点支持软件内播放，其他站点在浏览器打开")
        self.cover_label.clicked.connect(self._open_preview_page)
        preview_top.addWidget(self.cover_label, 0, Qt.AlignTop)

        meta_column = QVBoxLayout()
        meta_column.setSpacing(5)
        self.preview_title_label = QLabel("粘贴链接后自动解析")
        self.preview_title_label.setObjectName("previewTitle")
        self.preview_title_label.setWordWrap(True)
        self.preview_title_label.setMinimumHeight(26)
        meta_column.addWidget(self.preview_title_label)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)
        self.preview_uploader_label = QLabel("—")
        self.preview_uploader_label.setObjectName("previewUploader")
        self.preview_duration_label = QLabel("—")
        self.preview_duration_label.setObjectName("previewMeta")
        self.preview_pages_label = QLabel("—")
        self.preview_pages_label.setObjectName("previewMeta")
        self.preview_source_label = QLabel("—")
        self.preview_source_label.setObjectName("pill")
        self.preview_stats_label = QLabel("—")
        self.preview_stats_label.setObjectName("previewMeta")
        meta_row.addWidget(self.preview_uploader_label)
        meta_row.addWidget(self._dot_label())
        meta_row.addWidget(self.preview_duration_label)
        meta_row.addWidget(self._dot_label())
        meta_row.addWidget(self.preview_pages_label)
        meta_row.addWidget(self._dot_label())
        meta_row.addWidget(self.preview_source_label)
        meta_row.addStretch()
        meta_column.addLayout(meta_row)

        meta_column.addWidget(self.preview_stats_label)
        self.preview_note_label = QLabel("等待解析…")
        self.preview_note_label.setObjectName("previewNote")
        self.preview_note_label.setWordWrap(True)
        meta_column.addWidget(self.preview_note_label)

        self.bili_login_hint = QWidget()
        self.bili_login_hint.setStyleSheet("background: transparent;")
        bili_hint_row = QHBoxLayout(self.bili_login_hint)
        bili_hint_row.setContentsMargins(0, 0, 0, 0)
        bili_hint_row.setSpacing(8)
        self.bili_login_hint_label = QLabel("未登录：B站最高约 720P")
        self.bili_login_hint_label.setObjectName("hintText")
        self.bili_login_hint_label.setToolTip(
            "登录后可尝试 1080P+；4K/高码率需要大会员。不登录也可以继续下载约 720P。"
        )
        self.bili_login_hint_btn = QPushButton("登录获取更高画质")
        self.bili_login_hint_btn.setObjectName("secondaryBtn")
        self.bili_login_hint_btn.setFixedHeight(26)
        self.bili_login_hint_btn.setToolTip(
            "登录一次后保存 Cookie，后续自动复用，不再重复弹登录"
        )
        self.bili_login_hint_btn.clicked.connect(
            lambda: self.start_qr_login(reparse=True)
        )
        bili_hint_row.addWidget(self.bili_login_hint_label, 1)
        bili_hint_row.addWidget(self.bili_login_hint_btn, 0, Qt.AlignVCenter)
        meta_column.addWidget(self.bili_login_hint)
        self.bili_login_hint.setVisible(False)

        self.douyin_login_hint = QWidget()
        self.douyin_login_hint.setStyleSheet("background: transparent;")
        douyin_hint_row = QHBoxLayout(self.douyin_login_hint)
        douyin_hint_row.setContentsMargins(0, 0, 0, 0)
        douyin_hint_row.setSpacing(8)
        self.douyin_login_hint_label = QLabel("抖音未登录：1080P 需要登录")
        self.douyin_login_hint_label.setObjectName("hintText")
        self.douyin_login_hint_label.setToolTip(
            "登录一次后浏览器配置目录会保留登录状态，之后解析可尝试 1080P。"
        )
        self.douyin_login_hint_btn = QPushButton("登录抖音")
        self.douyin_login_hint_btn.setObjectName("secondaryBtn")
        self.douyin_login_hint_btn.setFixedHeight(26)
        self.douyin_login_hint_btn.setToolTip(
            "登录一次后浏览器配置目录会保留登录，不会每次都要求登录"
        )
        self.douyin_login_hint_btn.clicked.connect(
            lambda: self._start_douyin_login(reparse=True)
        )
        douyin_hint_row.addWidget(self.douyin_login_hint_label, 1)
        douyin_hint_row.addWidget(
            self.douyin_login_hint_btn, 0, Qt.AlignVCenter
        )
        meta_column.addWidget(self.douyin_login_hint)
        self.douyin_login_hint.setVisible(False)

        self.tags_widget = QWidget()
        self.tags_widget.setStyleSheet("background: transparent;")
        self.tags_layout = QHBoxLayout(self.tags_widget)
        self.tags_layout.setContentsMargins(0, 0, 0, 0)
        self.tags_layout.setSpacing(6)
        meta_column.addWidget(self.tags_widget)
        self.tags_widget.setVisible(False)

        self.preview_url_label = QLabel("—")
        self.preview_url_label.setObjectName("hintText")
        self.preview_url_label.setWordWrap(True)
        self.preview_url_label.setMaximumHeight(30)
        meta_column.addWidget(self.preview_url_label)
        self.toggle_formats_btn = QPushButton("查看全部格式")
        self.toggle_formats_btn.setObjectName("ghostBtn")
        self.toggle_formats_btn.setEnabled(False)
        self.toggle_formats_btn.setFixedHeight(26)
        self.toggle_formats_btn.clicked.connect(self._toggle_formats)
        meta_column.addWidget(self.toggle_formats_btn, 0, Qt.AlignLeft)
        preview_top.addLayout(meta_column, 1)
        preview_layout.addLayout(preview_top)

        format_header = QHBoxLayout()
        format_header.setSpacing(8)
        format_title = QLabel("可用格式")
        format_title.setObjectName("sectionLabel")
        format_hint = QLabel("选中一行会作为自定义格式；不选则按左侧画质自动下载")
        format_hint.setObjectName("hintText")
        self.show_streams_check = QCheckBox("全部原始流")
        self.show_streams_check.setToolTip(
            "B站 1080P 及以上是 DASH 流：画面和声音在服务器上就是分开的两条流，\n"
            "没有任何一条流自带声音。默认列表按分辨率合并显示，\n"
            "选中任意一行都会自动配上最佳音频（需要 ffmpeg 合并）。\n"
            "勾选后可看到并单独选择每一条原始流。"
        )
        self.show_streams_check.stateChanged.connect(self.on_show_streams_changed)
        self.use_format_btn = QPushButton("使用这个格式")
        self.use_format_btn.setObjectName("secondaryBtn")
        self.use_format_btn.setEnabled(False)
        self.use_format_btn.clicked.connect(self.use_selected_format)
        format_header.addWidget(format_title)
        format_header.addWidget(format_hint)
        format_header.addStretch()
        format_header.addWidget(self.show_streams_check)
        format_header.addWidget(self.use_format_btn)

        self.formats_block = QWidget()
        formats_block_layout = QVBoxLayout(self.formats_block)
        formats_block_layout.setContentsMargins(0, 0, 0, 0)
        formats_block_layout.setSpacing(8)
        formats_block_layout.addLayout(format_header)

        self.formats_table = QTableWidget(0, 8)
        self.formats_table.setHorizontalHeaderLabels(
            ["选择", "格式ID", "类型", "分辨率", "FPS", "编码", "大小", "说明"]
        )
        for column in range(5):
            self.formats_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.formats_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.formats_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.formats_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        self.formats_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.formats_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.formats_table.itemSelectionChanged.connect(self.on_format_selection_changed)
        self.formats_table.setToolTip("单击选中一行，双击（或点「使用这个格式」）确认使用该格式")
        self.formats_table.doubleClicked.connect(self.on_format_double_clicked)
        self.formats_table.setMinimumHeight(140)
        self.formats_table.setMaximumHeight(190)
        self._set_roomy_rows(self.formats_table)
        formats_block_layout.addWidget(self.formats_table)
        preview_layout.addWidget(self.formats_block)
        self.formats_block.setVisible(False)

        self.pages_actions = QWidget()
        pages_actions_layout = QHBoxLayout(self.pages_actions)
        pages_actions_layout.setContentsMargins(0, 0, 0, 0)
        pages_actions_layout.setSpacing(8)
        self.pages_label = QLabel("分 P 列表")
        self.pages_label.setObjectName("sectionLabel")
        self.select_all_pages_btn = QPushButton("全选")
        self.select_all_pages_btn.setObjectName("ghostBtn")
        self.select_all_pages_btn.setEnabled(False)
        self.select_all_pages_btn.clicked.connect(lambda: self.pages_table.selectAll())
        self.add_pages_btn = QPushButton("添加选中的分 P")
        self.add_pages_btn.setObjectName("secondaryBtn")
        self.add_pages_btn.setEnabled(False)
        self.add_pages_btn.clicked.connect(self.add_selected_pages_to_input)
        pages_actions_layout.addWidget(self.pages_label)
        pages_actions_layout.addStretch()
        pages_actions_layout.addWidget(self.select_all_pages_btn)
        pages_actions_layout.addWidget(self.add_pages_btn)
        preview_layout.addWidget(self.pages_actions)

        self.pages_table = QTableWidget(0, 4)
        self.pages_table.setHorizontalHeaderLabels(["分P", "标题", "时长", "链接"])
        self.pages_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.pages_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.pages_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.pages_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.pages_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.pages_table.setSelectionMode(QAbstractItemView.MultiSelection)
        self.pages_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.pages_table.setMinimumHeight(80)
        self._set_roomy_rows(self.pages_table)
        preview_layout.addWidget(self.pages_table)
        self.pages_actions.setVisible(False)
        self.pages_table.setVisible(False)
        layout.addWidget(preview_card)

        # ===== 下载设置 =====
        # 画质卡片会动态增删，卡片本身不加投影，避免 QGraphicsEffect 缓存旧内容
        settings_card = SoftCard("下载设置", shadow=False)
        settings_layout = settings_card.body_layout
        settings_layout.setContentsMargins(16, 6, 16, 6)
        settings_layout.setSpacing(2)

        quality_group = QVBoxLayout()
        quality_group.setSpacing(6)
        quality_heading = QHBoxLayout()
        quality_heading.setSpacing(6)
        quality_icon = QLabel()
        quality_icon.setFixedSize(16, 16)
        quality_icon.setPixmap(make_tool_icon("monitor").pixmap(16, 16))
        quality_icon.setStyleSheet("background: transparent;")
        quality_title = QLabel("画质选择")
        quality_title.setObjectName("sectionLabel")
        quality_title.setToolTip("解析后按该视频实际提供的画质显示")
        quality_heading.addWidget(quality_icon)
        quality_heading.addWidget(quality_title)
        quality_heading.addStretch()
        quality_group.addLayout(quality_heading)
        self.quality_grid = QGridLayout()
        self.quality_grid.setContentsMargins(0, 0, 0, 0)
        self.quality_grid.setHorizontalSpacing(6)
        self.quality_grid.setVerticalSpacing(6)
        quality_group.addLayout(self.quality_grid)

        separator = QFrame()
        separator.setObjectName("vSep")
        separator.setFixedSize(1, 84)
        separator.setStyleSheet("background: rgba(220, 233, 249, 0.95);")

        format_group = QVBoxLayout()
        format_group.setSpacing(6)
        format_heading = QHBoxLayout()
        format_heading.setSpacing(6)
        format_icon = QLabel()
        format_icon.setFixedSize(16, 16)
        format_icon.setPixmap(make_tool_icon("headphones").pixmap(16, 16))
        format_icon.setStyleSheet("background: transparent;")
        format_title = QLabel("格式选择")
        format_title.setObjectName("sectionLabel")
        format_heading.addWidget(format_icon)
        format_heading.addWidget(format_title)
        format_heading.addStretch()
        format_group.addLayout(format_heading)
        format_row = QHBoxLayout()
        format_row.setSpacing(7)
        for value, title, subtitle, detail, icon in (
            ("video", "MP4", "（视频）", "兼容性好", "video"),
            ("audio", "MP3", "（音频）", "仅音频", "audio"),
        ):
            card = ChoiceCard(
                value, title, subtitle, detail,
                width=96, height=84, icon_kind=icon,
            )
            card.clicked.connect(self.select_format)
            card.cleared.connect(lambda _value: self.select_format("video"))
            self.format_cards[value] = card
            format_row.addWidget(card)
        format_group.addLayout(format_row)

        format_container = QWidget()
        format_container.setStyleSheet("background: transparent;")
        format_container_layout = QVBoxLayout(format_container)
        format_container_layout.setContentsMargins(0, 0, 0, 0)
        format_container_layout.setSpacing(0)
        format_container_layout.addLayout(format_group)
        format_container_layout.addStretch()

        groups_row = QHBoxLayout()
        groups_row.setSpacing(10)
        groups_row.addLayout(quality_group)
        groups_row.addWidget(separator, 0, Qt.AlignVCenter)
        groups_row.addWidget(format_container)
        groups_row.addStretch()

        options_row = QHBoxLayout()
        options_row.setSpacing(16)
        options_title = QLabel("其他选项")
        options_title.setObjectName("sectionLabel")
        options_row.addWidget(options_title)
        self.quick_subtitle_switch = self._toggle_inline(options_row, "下载字幕", "subtitle")
        self.quick_thumbnail_switch = self._toggle_inline(options_row, "下载封面", "thumbnail")
        self.quick_danmaku_switch = self._toggle_inline(options_row, "下载弹幕", "danmaku")
        options_row.addStretch()

        format_status_row = QHBoxLayout()
        format_status_row.setSpacing(8)
        format_hint = QLabel("当前格式")
        format_hint.setObjectName("sectionLabel")
        self.selected_format_label = QLabel("自动选择最佳格式")
        self.selected_format_label.setObjectName("hintText")
        self.selected_format_label.setWordWrap(False)
        self.selected_format_label.setMaximumWidth(460)
        self.clear_format_btn = QPushButton("清除")
        self.clear_format_btn.setObjectName("ghostBtn")
        self.clear_format_btn.clicked.connect(self.clear_selected_format)
        format_status_row.addWidget(format_hint)
        format_status_row.addWidget(self.selected_format_label, 1)
        format_status_row.addWidget(self.clear_format_btn)

        self.settings_body = QWidget()
        self.settings_body.setStyleSheet("background: transparent;")
        settings_body_layout = QVBoxLayout(self.settings_body)
        settings_body_layout.setContentsMargins(0, 0, 0, 0)
        settings_body_layout.setSpacing(2)
        settings_body_layout.addLayout(groups_row)
        settings_body_layout.addLayout(options_row)
        settings_body_layout.addLayout(format_status_row)
        settings_layout.addWidget(self.settings_body)

        self.settings_placeholder = QFrame()
        self.settings_placeholder.setObjectName("settingsPlaceholder")
        placeholder_layout = QVBoxLayout(self.settings_placeholder)
        placeholder_layout.setContentsMargins(16, 16, 16, 16)
        placeholder_layout.setSpacing(6)
        placeholder_icon = QLabel()
        placeholder_icon.setPixmap(make_tool_icon("monitor").pixmap(26, 26))
        placeholder_icon.setAlignment(Qt.AlignCenter)
        placeholder_icon.setStyleSheet("background: transparent;")
        placeholder_title = QLabel("解析视频后显示可选项")
        placeholder_title.setObjectName("placeholderTitle")
        placeholder_title.setAlignment(Qt.AlignCenter)
        placeholder_hint = QLabel(
            "粘贴视频链接并解析，画质和格式会根据该视频实际支持的内容出现"
        )
        placeholder_hint.setObjectName("hintText")
        placeholder_hint.setAlignment(Qt.AlignCenter)
        placeholder_hint.setWordWrap(True)
        placeholder_layout.addStretch()
        placeholder_layout.addWidget(placeholder_icon)
        placeholder_layout.addWidget(placeholder_title)
        placeholder_layout.addWidget(placeholder_hint)
        placeholder_layout.addStretch()
        self.settings_placeholder.setMinimumHeight(150)
        settings_layout.addWidget(self.settings_placeholder)
        layout.addWidget(settings_card)

        self._rebuild_quality_cards([])
        self._set_video_settings_visible(False)

        # ===== 队列 =====
        queue_card = SoftCard(shadow=False)
        queue_layout = queue_card.body_layout
        queue_layout.setContentsMargins(16, 14, 16, 14)
        queue_header = QHBoxLayout()
        queue_header.setSpacing(8)
        queue_title = QLabel("下载队列")
        queue_title.setObjectName("cardTitle")
        self.queue_count_label = QLabel("0")
        self.queue_count_label.setObjectName("pillPink")
        self.pause_btn = QPushButton("暂停")
        self.pause_btn.setObjectName("secondaryBtn")
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self.toggle_pause_queue)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("secondaryBtn")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_downloads)
        self.start_btn = QPushButton("开始下载")
        self.start_btn.setObjectName("primaryBtn")
        self.start_btn.clicked.connect(self.start_downloads)
        queue_header.addWidget(queue_title)
        queue_header.addWidget(self.queue_count_label)
        queue_header.addStretch()
        queue_header.addWidget(self.pause_btn)
        queue_header.addWidget(self.cancel_btn)
        queue_header.addWidget(self.start_btn)
        queue_layout.addLayout(queue_header)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFixedHeight(10)
        queue_layout.addWidget(self.progress_bar)

        self.table = QTableWidget(0, 4)
        self.table.setObjectName("queueTable")
        self.table.setHorizontalHeaderLabels(["任务", "状态", "进度", "输出"])
        self.table.horizontalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 92)
        self.table.setColumnWidth(2, 176)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.setIconSize(QSize(30, 20))
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(False)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_task_context_menu)
        self.table.doubleClicked.connect(self.on_task_double_clicked)
        self.table.setMinimumHeight(108)
        self.table.setMaximumHeight(158)
        self._set_roomy_rows(self.table)
        self.table.verticalHeader().setDefaultSectionSize(44)
        queue_layout.addWidget(self.table)

        log_header = QHBoxLayout()
        self.log_toggle_btn = QPushButton("运行日志")
        self.log_toggle_btn.setObjectName("ghostBtn")
        self.log_toggle_btn.setCheckable(True)
        self.log_toggle_btn.setChecked(False)
        self.log_toggle_btn.toggled.connect(self._toggle_log)
        self.clear_log_btn = QPushButton("清空日志")
        self.clear_log_btn.setObjectName("ghostBtn")
        self.clear_log_btn.setVisible(False)
        self.clear_log_btn.clicked.connect(self.log_edit_clear)
        log_header.addWidget(self.log_toggle_btn)
        log_header.addStretch()
        log_header.addWidget(self.clear_log_btn)
        queue_layout.addLayout(log_header)

        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMinimumHeight(54)
        self.log_edit.setMaximumHeight(84)
        self.log_edit.setVisible(False)
        queue_layout.addWidget(self.log_edit)
        queue_card.setMaximumHeight(258)

        layout.addStretch()
        scroll.setWidget(container)

        # 队列固定在底部，滚动只作用于输入 / 预览 / 设置，保证进度始终可见
        left = QWidget()
        left.setObjectName("pageRoot")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)
        left_layout.addWidget(scroll, 1)
        left_layout.addWidget(queue_card)
        outer.addWidget(left, 1)
        outer.addWidget(self._build_right_panel())
        self.content_stack.addWidget(page)

    @staticmethod
    def _dot_label():
        dot = QLabel("·")
        dot.setStyleSheet("color: #C3D2E6;")
        return dot

    def _open_preview_page(self):
        url = self.preview_url_label.text().strip()
        if not url.startswith("http"):
            return
        if detect_site(url) == SITE_DOUYIN:
            info = self._last_preview_info or {}
            stream = info.get("stream") or {}
            if stream.get("video_url") and has_ffmpeg():
                try:
                    from .preview_player import (
                        DouyinPreviewDialog,
                        find_mpv_executable,
                    )

                    if find_mpv_executable():
                        dialog = DouyinPreviewDialog(self, info, url)
                        dialog.exec_()
                        return
                    self.statusBar().showMessage(
                        "未检测到 mpv.exe，已用浏览器打开；把 mpv.exe 放到程序目录可内置播放",
                        6000,
                    )
                except Exception as exc:
                    self.append_log(f"启动内置预览失败: {format_error(exc)}")
            try:
                from ..browser_login import open_in_login_browser

                if open_in_login_browser(url, "douyin"):
                    return
            except Exception:
                pass
        webbrowser.open(url)

    def _render_tags(self, tags):
        while self.tags_layout.count():
            item = self.tags_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for tag in list(tags)[:5]:
            text = tag if len(tag) <= 12 else f"{tag[:12]}…"
            chip = QLabel(f"# {text}")
            chip.setObjectName("tagChip")
            chip.setToolTip(tag)
            self.tags_layout.addWidget(chip)
        self.tags_layout.addStretch()
        self.tags_widget.setVisible(bool(tags))

    def _toggle_inline(self, parent_layout, text, key):
        row = QHBoxLayout()
        row.setSpacing(6)
        label = QLabel(text)
        label.setStyleSheet("font-size: 11px; color: #5E6D8A;")
        switch = ToggleSwitch(False)
        switch.toggled.connect(lambda checked, k=key: self._set_quick_option(k, checked))
        row.addWidget(label)
        row.addWidget(switch)
        parent_layout.addLayout(row)
        return switch

    def _build_right_panel(self):
        """右侧看板与快捷操作。窗口较窄时由 resizeEvent 隐藏。"""
        panel = QFrame()
        panel.setObjectName("rightPanel")
        panel.setFixedWidth(264)
        self.right_panel = panel
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        mascot_card = SoftCard(shadow=True)
        mascot_paths = [
            ASSETS_DIR / "mascot_right.png",
            RESOURCE_DIR / "assets" / "mascot_right.png",
            BASE_DIR / "mascot_right.png",
            RESOURCE_DIR / "mascot_right.png",
        ]
        mascot_slot = ImageSlot(
            mascot_paths,
            "右侧立绘\nassets/mascot_right.png\n（透明背景 PNG）",
            min_height=220,
            radius=16,
        )
        mascot_card.body_layout.addWidget(mascot_slot, 1)
        quote = QLabel("让美好的视频，\n一直在你身边 ♡")
        quote.setObjectName("rightQuote")
        quote.setAlignment(Qt.AlignCenter)
        mascot_card.body_layout.addWidget(quote)
        layout.addWidget(mascot_card, 1)

        action_card = SoftCard(shadow=True)
        action_layout = action_card.body_layout
        action_layout.setSpacing(9)
        self.hero_start_btn = QPushButton("立即下载")
        self.hero_start_btn.setObjectName("heroBtn")
        self.hero_start_btn.setFixedHeight(52)
        self.hero_start_btn.setIcon(make_tool_icon("download", "#FFFFFF"))
        self.hero_start_btn.setIconSize(QSize(20, 20))
        self.hero_start_btn.clicked.connect(self.start_downloads)
        action_layout.addWidget(self.hero_start_btn)
        hero_hint = QLabel("Ctrl + Enter 也可以开始")
        hero_hint.setObjectName("hintText")
        hero_hint.setAlignment(Qt.AlignCenter)
        action_layout.addWidget(hero_hint)

        divider = QFrame()
        divider.setObjectName("hSep")
        divider.setFixedHeight(1)
        action_layout.addWidget(divider)

        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        folder_icon = QLabel()
        folder_icon.setFixedSize(16, 16)
        folder_icon.setPixmap(make_tool_icon("folder").pixmap(16, 16))
        folder_icon.setStyleSheet("background: transparent;")
        self.side_dir_label = QLabel()
        self.side_dir_label.setWordWrap(False)
        self.side_dir_label.setFixedWidth(150)
        self.side_dir_label.setMaximumHeight(16)
        self.side_dir_label.setObjectName("sidePath")
        self.side_dir_btn = QPushButton("更改")
        self.side_dir_btn.setObjectName("ghostBtn")
        self.side_dir_btn.clicked.connect(self.choose_download_dir)
        path_row.addWidget(folder_icon, 0, Qt.AlignTop)
        path_row.addWidget(self.side_dir_label, 1)
        path_row.addWidget(self.side_dir_btn, 0, Qt.AlignTop)
        action_layout.addLayout(path_row)

        space_row = QHBoxLayout()
        space_row.setSpacing(6)
        disk_icon = QLabel()
        disk_icon.setFixedSize(16, 16)
        disk_icon.setPixmap(make_tool_icon("disk").pixmap(16, 16))
        disk_icon.setStyleSheet("background: transparent;")
        space_label = QLabel("可用空间")
        space_label.setObjectName("hintText")
        self.disk_label = QLabel("—")
        self.disk_label.setObjectName("hintText")
        self.side_open_btn = QPushButton("打开")
        self.side_open_btn.setObjectName("ghostBtn")
        self.side_open_btn.clicked.connect(self.open_download_dir)
        space_row.addWidget(disk_icon, 0, Qt.AlignVCenter)
        space_row.addWidget(space_label)
        space_row.addStretch()
        space_row.addWidget(self.disk_label)
        space_row.addWidget(self.side_open_btn)
        action_layout.addLayout(space_row)
        layout.addWidget(action_card)
        return panel

    # ---------- 画质 / 格式卡片 ----------

    QUALITY_CARD_META = {
        "best": ("最高", "自动选择", "最佳画质", "MAX"),
        "2160": ("4K", "超清画质", "3840×2160", "4K"),
        "1440": ("2K", "超清画质", "2560×1440", "2K"),
        "1080": ("1080P", "超清画质", "1920×1080", "HD"),
        "720": ("720P", "高清画质", "1280×720", "HD"),
        "480": ("480P", "标准画质", "854×480", "SD"),
        "360": ("360P", "标准画质", "640×360", "SD"),
    }

    def _quality_options_from_formats(self, formats):
        """从当前视频的格式列表里提取所有实际高度（不限制标准档位）。"""
        widths = {}
        for item in formats or []:
            try:
                height = int(item.get("height") or 0)
                width = int(item.get("width") or 0)
            except (TypeError, ValueError):
                continue
            if height >= 100:
                widths[height] = max(widths.get(height, 0), width)
        return sorted(widths.items(), reverse=True)

    @staticmethod
    def _quality_text(quality):
        if quality == "best":
            return "最高画质"
        if quality == "audio":
            return "仅音频"
        if str(quality).isdigit():
            return f"{quality}P"
        return QUALITY_LABELS.get(quality, str(quality))

    def _quality_card_meta(self, height, width):
        """按实际高度生成卡片文字，支持 540P、960P 等非标准档位。"""
        if height >= 4320:
            title, badge, subtitle = "8K", "8K", "超清画质"
        elif height >= 2160:
            title = "4K" if height == 2160 else f"{height}P"
            badge, subtitle = "4K", "超清画质"
        elif height >= 1440:
            title = "2K" if height == 1440 else f"{height}P"
            badge, subtitle = "2K", "超清画质"
        elif height >= 1080:
            title, badge, subtitle = f"{height}P", "HD", "超清画质"
        elif height >= 720:
            title, badge, subtitle = f"{height}P", "HD", "高清画质"
        elif height >= 480:
            title, badge, subtitle = f"{height}P", "SD", "标清画质"
        else:
            title, badge, subtitle = f"{height}P", "SD", "流畅画质"
        detail = f"{width}×{height}" if width else f"{height}P"
        return title, subtitle, detail, badge

    def _set_video_settings_visible(self, visible):
        """没有视频时用占位提示，不显示画质 / 格式按钮。"""
        if hasattr(self, "settings_body"):
            self.settings_body.setVisible(visible)
        if hasattr(self, "settings_placeholder"):
            self.settings_placeholder.setVisible(not visible)

    def _rebuild_quality_cards(self, options):
        """按视频实际画质重建卡片；没有视频信息时只保留「最高」。"""
        while self.quality_grid.count():
            entry = self.quality_grid.takeAt(0)
            widget = entry.widget()
            if widget is not None:
                # 先摘出父控件再 deleteLater：否则删除会推迟到事件循环，
                # 旧卡片会和新卡片同时绘制，出现文字残影。
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self.quality_cards = {}

        normalized = {}
        for item in options or []:
            if isinstance(item, (tuple, list)):
                height = int(item[0] or 0)
                width = int(item[1] or 0) if len(item) > 1 else 0
            else:
                height = int(item or 0)
                width = 0
            if height > 0:
                normalized[height] = max(normalized.get(height, 0), width)

        heights = sorted(normalized, reverse=True)
        values = ["best"] + [str(height) for height in heights]

        for index, value in enumerate(values):
            if value == "best":
                if (
                    isinstance(self._last_preview_info, dict)
                    and detect_site(self._last_preview_info.get("url") or "")
                    == SITE_DOUYIN
                    and self._last_preview_info.get("qualities")
                ):
                    title, subtitle, detail, badge = (
                        "智能", "页面自动", "自动选择", "AUTO",
                    )
                else:
                    title, subtitle, detail, badge = self.QUALITY_CARD_META["best"]
            else:
                height = int(value)
                title, subtitle, detail, badge = self._quality_card_meta(
                    height, normalized.get(height, 0)
                )
            card = ChoiceCard(
                value, title, subtitle, detail,
                width=96, height=84, badge=badge,
            )
            card.clicked.connect(self.select_quality)
            card.cleared.connect(lambda _value: self.select_quality("best"))
            if self._quality_locked:
                card.setEnabled(False)
            self.quality_cards[value] = card
            self.quality_grid.addWidget(card, index // 5, index % 5)

        # 让设置页下拉框也能承载 540P / 960P 这类非标准高度
        if hasattr(self, "quality_combo"):
            self.quality_combo.blockSignals(True)
            for height in heights:
                value = str(height)
                if self.quality_combo.findData(value) < 0:
                    self.quality_combo.addItem(f"{height}P", value)
            self.quality_combo.blockSignals(False)

        self._quality_options_loaded = bool(heights)
        self._sync_quality_cards()

    def _sync_quality_cards(self):
        custom_format = (
            self.custom_format_edit.text().strip()
            if hasattr(self, "custom_format_edit")
            else ""
        )
        if custom_format:
            # 自定义格式优先：画质/格式卡片都不选中，避免误导
            for card in self.quality_cards.values():
                card.set_selected(False)
            for card in self.format_cards.values():
                card.set_selected(False)
            return

        quality = self.settings.get("quality", "best")
        if quality == "audio":
            for card in self.quality_cards.values():
                card.set_selected(False)
            self._sync_format_cards()
            return
        if quality not in self.quality_cards:
            # 还没解析视频时不要重置用户设置；解析后若该画质不存在则回退到「最高」
            if not self._quality_options_loaded:
                for card in self.quality_cards.values():
                    card.set_selected(False)
                self._sync_format_cards()
                return
            quality = "best" if "best" in self.quality_cards else next(
                iter(self.quality_cards), "best"
            )
            self.settings["quality"] = quality
            self._last_video_quality = quality
            if hasattr(self, "quality_combo"):
                self._syncing_quality = True
                self.set_combo_value(self.quality_combo, quality)
                self._syncing_quality = False
        for value, card in self.quality_cards.items():
            card.set_selected(value == quality)
        self._sync_format_cards()

    def _sync_format_cards(self):
        audio_only = (self.settings.get("quality") or "") == "audio"
        for value, card in self.format_cards.items():
            card.set_selected((value == "audio") == audio_only)

    def _clear_custom_format(self):
        """退出自定义格式，交还给画质卡片控制。"""
        self._format_source_url = ""
        if not (self.custom_format_edit.text() or "").strip():
            return
        self.custom_format_edit.clear()
        self.formats_table.clearSelection()
        self.refresh_format_selection_marks()
        self.append_log("已退出自定义格式，改为按画质自动选择")

    def select_quality(self, value):
        """点击画质卡片：退出自定义格式，并同步设置页下拉框。"""
        if not hasattr(self, "quality_combo"):
            return
        if value not in self.quality_cards and value not in QUALITY_LABELS:
            return
        if self.quality_combo.findData(value) < 0:
            label = f"{value}P" if str(value).isdigit() else str(value)
            self.quality_combo.blockSignals(True)
            self.quality_combo.addItem(label, value)
            self.quality_combo.blockSignals(False)
        self._clear_custom_format()
        self.settings["quality"] = value
        if value != "audio":
            self._last_video_quality = value
        self._syncing_quality = True
        self.set_combo_value(self.quality_combo, value)
        self._syncing_quality = False
        self._sync_quality_cards()
        self.refresh_selected_format_label()
        self._update_bili_login_hint()

    def select_format(self, value):
        """点击 MP4 / MP3 卡片。"""
        if value == "audio":
            self.select_quality("audio")
        else:
            quality = self._last_video_quality or "best"
            if quality == "audio":
                quality = "best"
            self.select_quality(quality)

    def _on_quality_combo_changed(self):
        if self._syncing_quality:
            return
        value = self.quality_combo.currentData()
        if value:
            self._clear_custom_format()
            self.settings["quality"] = value
            if value != "audio":
                self._last_video_quality = value
            self._sync_quality_cards()
            self.refresh_selected_format_label()
            self._update_bili_login_hint()

    # ---------- 快捷选项 ----------

    def _set_quick_option(self, key, checked):
        mapping = {
            "subtitle": getattr(self, "subtitle_check", None),
            "thumbnail": getattr(self, "thumbnail_check", None),
            "danmaku": getattr(self, "danmaku_check", None),
        }
        checkbox = mapping.get(key)
        if checkbox is None:
            return
        checkbox.blockSignals(True)
        checkbox.setChecked(bool(checked))
        checkbox.blockSignals(False)

    def _sync_quick_toggles(self):
        mapping = (
            (getattr(self, "quick_subtitle_switch", None), getattr(self, "subtitle_check", None)),
            (getattr(self, "quick_thumbnail_switch", None), getattr(self, "thumbnail_check", None)),
            (getattr(self, "quick_danmaku_switch", None), getattr(self, "danmaku_check", None)),
        )
        for switch, checkbox in mapping:
            if switch is None or checkbox is None:
                continue
            switch.blockSignals(True)
            switch.setChecked(checkbox.isChecked())
            switch.blockSignals(False)

    # ---------- 右侧信息 ----------

    def _refresh_side_path(self):
        if not hasattr(self, "side_dir_label"):
            return
        path = ""
        if hasattr(self, "dir_edit"):
            path = self.dir_edit.text().strip()
        path = path or self.settings.get("download_dir") or str(DEFAULT_DOWNLOAD_DIR)
        if hasattr(self, "side_dir_label"):
            shown = self.side_dir_label.fontMetrics().elidedText(path, Qt.ElideMiddle, 148)
            self.side_dir_label.setText(shown)
            self.side_dir_label.setToolTip(path)
        self._refresh_disk_space(path)

    def _refresh_disk_space(self, path=None):
        if not hasattr(self, "disk_label"):
            return
        candidate = Path(path or self.settings.get("download_dir") or DEFAULT_DOWNLOAD_DIR)
        try:
            while not candidate.exists() and candidate.parent != candidate:
                candidate = candidate.parent
            usage = shutil.disk_usage(str(candidate))
            self.disk_label.setText(format_bytes(usage.free))
            self.disk_label.setToolTip(f"可用空间：{format_bytes(usage.free)}")
        except Exception:
            self.disk_label.setText("—")

    def _refresh_queue_count(self):
        if hasattr(self, "queue_count_label"):
            self.queue_count_label.setText(str(self.table.rowCount()))

    # ---------- 历史页 ----------

    def _build_history_page(self):
        page = QWidget()
        page.setObjectName("pageRoot")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(14)

        header = QLabel("下载历史")
        header.setObjectName("pageTitle")
        sub = QLabel("查看、重新下载或管理已完成任务")
        sub.setObjectName("pageSubtitle")
        layout.addWidget(header)
        layout.addWidget(sub)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self.history_search_edit = QLineEdit()
        self.history_search_edit.setPlaceholderText("搜索标题、链接或文件路径…")
        self.history_search_edit.setClearButtonEnabled(True)
        self.history_search_edit.textChanged.connect(self._apply_history_filter)
        self.history_status_combo = QComboBox()
        self.history_status_combo.addItem("全部状态", "all")
        self.history_status_combo.addItem("完成", "completed")
        self.history_status_combo.addItem("失败", "failed")
        self.history_status_combo.addItem("已取消", "cancelled")
        self.history_status_combo.addItem("文件缺失", "missing")
        self.history_status_combo.currentIndexChanged.connect(
            self._apply_history_filter
        )
        self.history_match_label = QLabel("")
        self.history_match_label.setObjectName("hintText")
        filter_row.addWidget(self.history_search_edit, 1)
        filter_row.addWidget(self.history_status_combo)
        filter_row.addWidget(self.history_match_label)
        layout.addLayout(filter_row)

        # 分两行排布：窗口变窄时按钮不会被压到文字显示不全
        action_row = QHBoxLayout()
        action_row2 = QHBoxLayout()
        self.history_refresh_btn = QPushButton("刷新")
        self.history_refresh_btn.setObjectName("secondaryBtn")
        self.history_refresh_btn.clicked.connect(self.refresh_history)
        self.history_redownload_btn = QPushButton("重新下载")
        self.history_redownload_btn.clicked.connect(self.redownload_history)
        self.history_open_file_btn = QPushButton("打开文件")
        self.history_open_file_btn.setObjectName("secondaryBtn")
        self.history_open_file_btn.clicked.connect(self.open_history_file)
        self.history_open_dir_btn = QPushButton("打开目录")
        self.history_open_dir_btn.setObjectName("secondaryBtn")
        self.history_open_dir_btn.clicked.connect(self.open_history_dir)
        self.history_delete_btn = QPushButton("删除记录")
        self.history_delete_btn.setObjectName("secondaryBtn")
        self.history_delete_btn.clicked.connect(self.delete_history)
        self.history_clear_btn = QPushButton("清空全部")
        self.history_clear_btn.setObjectName("secondaryBtn")
        self.history_clear_btn.clicked.connect(self.clear_all_history)
        self.history_export_csv_btn = QPushButton("导出 CSV")
        self.history_export_csv_btn.setObjectName("secondaryBtn")
        self.history_export_csv_btn.clicked.connect(lambda: self.export_history("csv"))
        self.history_export_json_btn = QPushButton("导出 JSON")
        self.history_export_json_btn.setObjectName("secondaryBtn")
        self.history_export_json_btn.clicked.connect(lambda: self.export_history("json"))
        action_row.addWidget(self.history_refresh_btn)
        action_row.addWidget(self.history_redownload_btn)
        action_row.addWidget(self.history_open_file_btn)
        action_row.addWidget(self.history_open_dir_btn)
        action_row.addStretch()
        self.history_play_btn = QPushButton("内置播放")
        self.history_play_btn.setObjectName("secondaryBtn")
        self.history_play_btn.setToolTip(
            "在软件内播放已下载的文件（需要 mpv.exe）；没有 mpv 时可改用系统播放器"
        )
        self.history_play_btn.clicked.connect(self.play_history_media)
        action_row2.addWidget(self.history_play_btn)
        action_row2.addWidget(self.history_delete_btn)
        action_row2.addWidget(self.history_clear_btn)
        action_row2.addWidget(self.history_export_csv_btn)
        action_row2.addWidget(self.history_export_json_btn)
        action_row2.addStretch()
        layout.addLayout(action_row)
        layout.addLayout(action_row2)

        self.history_table = QTableWidget(0, 7)
        self.history_table.setHorizontalHeaderLabels(
            ["标题", "状态", "大小", "时长", "分辨率", "完成时间", "路径"]
        )
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setSortingEnabled(True)
        self.history_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.history_table.customContextMenuRequested.connect(self.show_history_context_menu)
        self.history_table.doubleClicked.connect(self.on_history_double_clicked)
        self._set_roomy_rows(self.history_table)
        layout.addWidget(self.history_table, 1)
        self.content_stack.addWidget(page)

    # ---------- 设置页 ----------

    def _build_settings_page(self):
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(14)

        header = QLabel("偏好设置")
        header.setObjectName("pageTitle")
        sub = QLabel("按自己的习惯配置下载方式")
        sub.setObjectName("pageSubtitle")
        layout.addWidget(header)
        layout.addWidget(sub)

        # Cookie 卡片
        cookie_card, cookie_layout = self.create_card("Cookie 设置")
        cookie_mode_row = QHBoxLayout()
        cookie_mode_row.addWidget(QLabel("Cookie 来源"))
        self.cookie_mode_combo = QComboBox()
        self.cookie_mode_combo.addItem("不使用 Cookie（公开视频自动兜底）", "none")
        self.cookie_mode_combo.addItem("cookies.txt 文件", "file")
        self.cookie_mode_combo.addItem("读取 Chrome Cookie", "chrome")
        self.cookie_mode_combo.addItem("读取 Edge Cookie", "edge")
        self.cookie_mode_combo.addItem("读取 Firefox Cookie", "firefox")
        self.cookie_mode_combo.currentIndexChanged.connect(self.update_cookie_controls)
        cookie_mode_row.addWidget(self.cookie_mode_combo, 1)
        self.qr_login_btn = QPushButton("扫码登录")
        self.qr_login_btn.setObjectName("secondaryBtn")
        self.qr_login_btn.clicked.connect(self.start_qr_login)
        self.check_cookie_btn = QPushButton("检测一下")
        self.check_cookie_btn.setObjectName("secondaryBtn")
        self.check_cookie_btn.clicked.connect(self.check_cookie_status)
        cookie_mode_row.addWidget(self.qr_login_btn)
        cookie_mode_row.addWidget(self.check_cookie_btn)
        cookie_layout.addLayout(cookie_mode_row)

        cookie_file_row = QHBoxLayout()
        cookie_file_row.addWidget(QLabel("Cookie 文件"))
        self.cookie_file_edit = QLineEdit()
        self.cookie_file_edit.setPlaceholderText("选择从已登录 B站 浏览器导出的 cookies.txt")
        self.cookie_file_btn = QPushButton("浏览")
        self.cookie_file_btn.setObjectName("secondaryBtn")
        self.cookie_file_btn.clicked.connect(self.choose_cookie_file)
        cookie_file_row.addWidget(self.cookie_file_edit, 1)
        cookie_file_row.addWidget(self.cookie_file_btn)
        cookie_layout.addLayout(cookie_file_row)
        cookie_hint = QLabel(
            "检测按钮只验证 B 站；抖音 / YouTube 的 Cookie 会在实际解析时使用。"
            "Chrome / Edge 新版建议改用 cookies.txt 或 Firefox。"
        )
        cookie_hint.setObjectName("hintText")
        cookie_hint.setWordWrap(True)
        cookie_hint.setMinimumHeight(34)
        cookie_layout.addWidget(cookie_hint)
        self.auto_browser_login_check = QCheckBox(
            "自动复用本机浏览器登录（B站 / YouTube，不弹窗）"
        )
        self.auto_browser_login_check.setToolTip(
            "B站：当前 Cookie 无效时静默读取 Firefox/Edge/Chrome 的 bilibili.com "
            "登录 Cookie，验证有效后保存到 Cookie 库。\n"
            "YouTube：没有配置 Cookie 时静默尝试复用浏览器登录，失败则继续无 Cookie 解析。"
        )
        cookie_layout.addWidget(self.auto_browser_login_check)
        layout.addWidget(cookie_card)

        # 站点专用 Cookie（覆盖全局设置）
        site_card, site_layout = self.create_card("站点专用 Cookie（可选）")
        site_grid = QGridLayout()
        site_grid.setColumnStretch(1, 1)
        site_grid.setHorizontalSpacing(12)
        site_grid.setVerticalSpacing(8)
        site_rows = (
            ("B站", "cookie_bilibili_edit", "留空则使用上面的全局 Cookie"),
            ("抖音", "cookie_douyin_edit", "可一键登录获取，或选择导出的 cookies.txt"),
            ("YouTube", "cookie_youtube_edit", "部分视频需要 YouTube Cookie"),
        )
        for row, (label, attr, placeholder) in enumerate(site_rows):
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            button = QPushButton("浏览")
            button.setObjectName("secondaryBtn")
            button.clicked.connect(
                lambda _checked=False, target=edit: self._choose_site_cookie_file(target)
            )
            setattr(self, attr, edit)
            site_grid.addWidget(QLabel(label), row, 0)
            site_grid.addWidget(edit, row, 1)
            site_grid.addWidget(button, row, 2)
        site_hint = QLabel(
            "站点专用文件优先于全局 Cookie。留空时继续使用上面的全局设置；"
            "B 站扫码登录会自动写入 B 站文件。"
        )
        site_hint.setObjectName("hintText")
        site_hint.setWordWrap(True)
        site_hint.setMinimumHeight(24)
        site_layout.addLayout(site_grid)
        self.douyin_login_btn = QPushButton("一键登录抖音（自动导入 Cookie）")
        self.douyin_login_btn.setObjectName("secondaryBtn")
        self.douyin_login_btn.clicked.connect(self._start_douyin_login)
        site_layout.addWidget(self.douyin_login_btn, 0, Qt.AlignLeft)
        self.cookie_douyin_status = QLabel("抖音 Cookie：未配置")
        self.cookie_douyin_status.setObjectName("hintText")
        self.cookie_douyin_status.setWordWrap(True)
        self.cookie_douyin_edit.textChanged.connect(self._update_site_cookie_status)
        site_layout.addWidget(self.cookie_douyin_status)
        site_layout.addWidget(site_hint)
        layout.addWidget(site_card)

        # 域名 Cookie 库：任何 yt-dlp 站点都可以用
        store_card, store_layout = self.create_card("Cookie 库")
        store_hint = QLabel(
            "按域名保存为 cookies/<域名>.txt，解析时自动匹配，不需要在设置里逐站配置。\n"
            "例如 cookies/vimeo.com.txt、cookies/xiaohongshu.com.txt。"
        )
        store_hint.setObjectName("hintText")
        store_hint.setWordWrap(True)
        store_hint.setMinimumHeight(34)
        store_layout.addWidget(store_hint)
        self.cookie_store_label = QLabel("Cookie 库：空")
        self.cookie_store_label.setObjectName("previewMeta")
        self.cookie_store_label.setWordWrap(True)
        self.cookie_store_label.setMinimumHeight(22)
        store_layout.addWidget(self.cookie_store_label)
        store_buttons = QHBoxLayout()
        store_buttons.setSpacing(8)
        self.any_cookie_btn = QPushButton("为任意站点获取 Cookie")
        self.any_cookie_btn.setObjectName("secondaryBtn")
        self.any_cookie_btn.clicked.connect(self._start_any_site_cookie_login)
        self.open_cookie_store_btn = QPushButton("打开 Cookie 目录")
        self.open_cookie_store_btn.setObjectName("secondaryBtn")
        self.open_cookie_store_btn.clicked.connect(
            lambda: open_path_in_explorer(str(cookie_store_dir()))
        )
        self.manage_cookie_store_btn = QPushButton("管理…")
        self.manage_cookie_store_btn.setObjectName("secondaryBtn")
        self.manage_cookie_store_btn.clicked.connect(self._manage_cookie_store)
        self.refresh_cookie_store_btn = QPushButton("刷新")
        self.refresh_cookie_store_btn.setObjectName("ghostBtn")
        self.refresh_cookie_store_btn.clicked.connect(self._refresh_cookie_store)
        store_buttons.addWidget(self.any_cookie_btn)
        store_buttons.addWidget(self.open_cookie_store_btn)
        store_buttons.addWidget(self.manage_cookie_store_btn)
        store_buttons.addWidget(self.refresh_cookie_store_btn)
        store_buttons.addStretch()
        store_layout.addLayout(store_buttons)
        layout.addWidget(store_card)
        self._refresh_cookie_store()

        # 下载设置卡片
        dl_card, dl_layout = self.create_card("下载设置")
        # 注意：必须用 QGridLayout() 再 addLayout 到卡片已有布局上。
        # 直接 QGridLayout(dl_card) 会因为卡片已有布局而安装失败，
        # 里面的控件会没有父控件、完全不显示。
        dl_grid = QGridLayout()
        dl_layout.addLayout(dl_grid)
        dl_grid.setColumnStretch(1, 1)
        dl_grid.setColumnStretch(3, 1)
        dl_grid.setHorizontalSpacing(12)
        dl_grid.setVerticalSpacing(10)

        self.quality_combo = QComboBox()
        for value, label in QUALITY_LABELS.items():
            self.quality_combo.addItem(label, value)
        dl_grid.addWidget(QLabel("清晰度"), 0, 0)
        dl_grid.addWidget(self.quality_combo, 0, 1)

        self.codec_combo = QComboBox()
        for value, label in CODEC_LABELS.items():
            self.codec_combo.addItem(label, value)
        dl_grid.addWidget(QLabel("编码偏好"), 0, 2)
        dl_grid.addWidget(self.codec_combo, 0, 3)

        self.audio_quality_combo = QComboBox()
        for value, label in AUDIO_QUALITY_LABELS.items():
            self.audio_quality_combo.addItem(label, value)
        dl_grid.addWidget(QLabel("音频质量"), 1, 0)
        dl_grid.addWidget(self.audio_quality_combo, 1, 1)

        self.thread_combo = QComboBox()
        for value in range(1, 9):
            self.thread_combo.addItem(str(value), value)
        dl_grid.addWidget(QLabel("分片并发"), 1, 2)
        dl_grid.addWidget(self.thread_combo, 1, 3)

        self.concurrent_spin = QSpinBox()
        self.concurrent_spin.setRange(1, 8)
        self.concurrent_spin.setToolTip("同时下载多个视频的任务数（1=顺序下载，建议 2~4）")
        dl_grid.addWidget(QLabel("任务并发"), 4, 0)
        dl_grid.addWidget(self.concurrent_spin, 4, 1)

        self.speed_limit_spin = QSpinBox()
        self.speed_limit_spin.setRange(0, 102400)
        self.speed_limit_spin.setSingleStep(128)
        self.speed_limit_spin.setSuffix(" KB/s")
        self.speed_limit_spin.setSpecialValueText("不限速")
        self.speed_limit_spin.setToolTip("0 表示不限速；设置后对 yt-dlp 和直链下载生效")
        dl_grid.addWidget(QLabel("下载限速"), 4, 2)
        dl_grid.addWidget(self.speed_limit_spin, 4, 3)

        self.custom_format_edit = QLineEdit()
        self.custom_format_edit.setReadOnly(True)
        self.custom_format_edit.setPlaceholderText("在预览格式表选中一行后点击\"使用这个格式\"")
        dl_grid.addWidget(QLabel("选中格式"), 2, 0)
        dl_grid.addWidget(self.custom_format_edit, 2, 1, 1, 3)

        dl_grid.addWidget(QLabel("保存位置"), 3, 0)
        dir_row = QHBoxLayout()
        self.dir_edit = QLineEdit()
        self.dir_edit.setReadOnly(True)
        self.dir_btn = QPushButton("浏览")
        self.dir_btn.setObjectName("secondaryBtn")
        if (RESOURCE_DIR / "folder.png").exists():
            self.dir_btn.setIcon(QIcon(str(RESOURCE_DIR / "folder.png")))
        self.dir_btn.clicked.connect(self.choose_download_dir)
        self.open_dir_btn = QPushButton("打开")
        self.open_dir_btn.setObjectName("secondaryBtn")
        if (RESOURCE_DIR / "folder.png").exists():
            self.open_dir_btn.setIcon(QIcon(str(RESOURCE_DIR / "folder.png")))
        self.open_dir_btn.clicked.connect(self.open_download_dir)
        dir_row.addWidget(self.dir_edit, 1)
        dir_row.addWidget(self.dir_btn)
        dir_row.addWidget(self.open_dir_btn)
        dl_grid.addLayout(dir_row, 3, 1, 1, 3)
        layout.addWidget(dl_card)

        # 网络与文件名卡片
        net_card, net_layout = self.create_card("网络与文件名")
        net_grid = QGridLayout()
        net_layout.addLayout(net_grid)
        net_grid.setColumnStretch(1, 1)
        net_grid.setHorizontalSpacing(12)
        net_grid.setVerticalSpacing(10)
        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("可选，例如 http://127.0.0.1:7890 或 socks5://127.0.0.1:1080")
        self.proxy_test_btn = QPushButton("测试")
        self.proxy_test_btn.setObjectName("secondaryBtn")
        self.proxy_test_btn.setToolTip("通过当前代理访问 B站，检查网络是否连通")
        self.proxy_test_btn.clicked.connect(self._test_proxy)
        net_grid.addWidget(QLabel("代理"), 0, 0)
        net_grid.addWidget(self.proxy_edit, 0, 1, 1, 2)
        net_grid.addWidget(self.proxy_test_btn, 0, 3)
        self.proxy_status_label = QLabel("")
        self.proxy_status_label.setObjectName("hintText")
        self.proxy_status_label.setWordWrap(True)
        net_grid.addWidget(self.proxy_status_label, 1, 1, 1, 3)
        self.template_edit = QLineEdit()
        self.template_edit.setPlaceholderText("%(title).180B [%(id)s].%(ext)s")
        net_grid.addWidget(QLabel("文件名"), 2, 0)
        net_grid.addWidget(self.template_edit, 2, 1, 1, 3)

        self.runtime_status_label = QLabel(self._runtime_status_text())
        self.runtime_status_label.setObjectName("hintText")
        self.runtime_status_label.setWordWrap(True)
        self.runtime_status_label.setMinimumHeight(34)
        self.runtime_status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        net_grid.addWidget(QLabel("运行环境"), 3, 0)
        net_grid.addWidget(self.runtime_status_label, 3, 1, 1, 3)
        self.proxy_edit.editingFinished.connect(self._refresh_runtime_status)
        layout.addWidget(net_card)

        # 附加内容卡片
        extra_card, extra_layout = self.create_card("附加内容")
        extra_row = QHBoxLayout()
        self.thumbnail_check = QCheckBox("下载封面")
        self.subtitle_check = QCheckBox("下载字幕")
        self.danmaku_check = QCheckBox("下载弹幕(B站)")
        extra_row.addWidget(self.thumbnail_check)
        extra_row.addWidget(self.subtitle_check)
        extra_row.addWidget(self.danmaku_check)
        extra_row.addStretch()
        extra_layout.addLayout(extra_row)
        layout.addWidget(extra_card)

        # 外观 / 主题
        theme_card, theme_layout = self.create_card("外观")
        theme_row = QHBoxLayout()
        theme_label = QLabel("主题")
        theme_label.setObjectName("sectionLabel")
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("浅色（白天）", "light")
        self.theme_combo.addItem("深色（夜间）", "dark")
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        theme_hint = QLabel("夜间模式会同步调整窗口背景、卡片和文字颜色")
        theme_hint.setObjectName("hintText")
        theme_row.addWidget(theme_label)
        theme_row.addWidget(self.theme_combo)
        theme_row.addWidget(theme_hint)
        theme_row.addStretch()
        theme_layout.addLayout(theme_row)
        layout.addWidget(theme_card)

        # 视觉特效开关卡片
        fx_card, fx_layout = self.create_card("视觉特效", glow=True, glow_color="#a78bfa")
        fx_row = QHBoxLayout()
        self.sakura_check = QCheckBox("粒子效果")
        self.neon_check = QCheckBox("卡片发光")
        self.sound_check = QCheckBox("音效反馈")
        self.sakura_check.stateChanged.connect(self.toggle_sakura_overlay)
        self.neon_check.stateChanged.connect(self.apply_fx_settings)
        self.sound_check.stateChanged.connect(self.apply_fx_settings)
        fx_row.addWidget(self.sakura_check)
        fx_row.addWidget(self.neon_check)
        fx_row.addWidget(self.sound_check)
        fx_row.addStretch()
        fx_layout.addLayout(fx_row)
        layout.addWidget(fx_card)

        # 关于与诊断
        about_card, about_layout = self.create_card("关于")
        from .. import __version__

        about_title = QLabel(f"多站点视频下载器 v{__version__}")
        about_title.setObjectName("sectionLabel")
        about_desc = QLabel(
            "基于 PyQt5、yt-dlp 与 ffmpeg 的桌面视频下载器。"
            "内置播放为可选功能，需要程序目录或 PATH 中存在 mpv.exe。"
        )
        about_desc.setObjectName("hintText")
        about_desc.setWordWrap(True)
        about_buttons = QHBoxLayout()
        self.copy_diag_btn = QPushButton("复制诊断信息")
        self.copy_diag_btn.setObjectName("secondaryBtn")
        self.copy_diag_btn.clicked.connect(self._copy_diagnostics)
        self.open_base_btn = QPushButton("打开程序目录")
        self.open_base_btn.setObjectName("secondaryBtn")
        self.open_base_btn.clicked.connect(
            lambda: open_path_in_explorer(str(BASE_DIR))
        )
        about_buttons.addWidget(self.copy_diag_btn)
        about_buttons.addWidget(self.open_base_btn)
        notices_path = next(
            (
                path
                for path in (
                    BASE_DIR / "THIRD_PARTY_NOTICES.md",
                    RESOURCE_DIR / "THIRD_PARTY_NOTICES.md",
                )
                if path.is_file()
            ),
            None,
        )
        if notices_path is not None:
            notices_btn = QPushButton("第三方许可")
            notices_btn.setObjectName("secondaryBtn")
            notices_btn.clicked.connect(
                lambda: open_file_default(str(notices_path))
            )
            about_buttons.addWidget(notices_btn)
        about_buttons.addStretch()
        about_layout.addWidget(about_title)
        about_layout.addWidget(about_desc)
        about_layout.addLayout(about_buttons)
        layout.addWidget(about_card)

        # 恢复默认设置按钮
        reset_layout = QHBoxLayout()
        reset_layout.addStretch()
        self.export_settings_btn = QPushButton("导出设置")
        self.export_settings_btn.setObjectName("secondaryBtn")
        self.export_settings_btn.clicked.connect(self.export_settings)
        reset_layout.addWidget(self.export_settings_btn)
        self.import_settings_btn = QPushButton("导入设置")
        self.import_settings_btn.setObjectName("secondaryBtn")
        self.import_settings_btn.clicked.connect(self.import_settings)
        reset_layout.addWidget(self.import_settings_btn)
        self.reset_settings_btn = QPushButton("恢复默认设置")
        self.reset_settings_btn.setObjectName("secondaryBtn")
        self.reset_settings_btn.clicked.connect(self.reset_settings)
        reset_layout.addWidget(self.reset_settings_btn)
        layout.addLayout(reset_layout)

        layout.addStretch()
        scroll.setWidget(container)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(scroll)
        self.content_stack.addWidget(page)

    # ---------- 主题样式 ----------

    def _apply_theme(self):
        self.setStyleSheet(build_stylesheet())
        for widget in self.findChildren(QWidget):
            apply = getattr(widget, "apply_theme", None)
            if callable(apply):
                try:
                    apply()
                except Exception:
                    pass
        self._apply_nav_icons()
        self._apply_window_control_icons()
        if hasattr(self, "theme_btn"):
            icon_kind = "sun" if is_dark() else "moon"
            self.theme_btn.setIcon(make_tool_icon(icon_kind, theme_get("text_soft")))
            self.theme_btn.setToolTip(
                "切换为白天模式" if is_dark() else "切换为夜间模式"
            )
        if hasattr(self, "selected_format_label"):
            self.refresh_selected_format_label()

    def _apply_nav_icons(self):
        color = theme_get("text_soft")
        for button, kind in (
            (getattr(self, "nav_download_btn", None), "download"),
            (getattr(self, "nav_history_btn", None), "history"),
            (getattr(self, "nav_settings_btn", None), "settings"),
        ):
            if button is not None:
                button.setIcon(make_nav_icon(kind, color_off=color))

    def _apply_window_control_icons(self):
        color = theme_get("text_soft")
        for button, kind in (
            (getattr(self, "min_btn", None), "min"),
            (getattr(self, "max_btn", None), "max"),
            (getattr(self, "close_btn", None), "close"),
        ):
            if button is not None:
                button.setIcon(make_tool_icon(kind, color))

    def _toggle_theme(self):
        mode = "light" if is_dark() else "dark"
        if hasattr(self, "theme_combo"):
            index = self.theme_combo.findData(mode)
            if index >= 0:
                self.theme_combo.setCurrentIndex(index)
                return
        set_theme_mode(mode)
        self.settings["theme"] = mode
        self._apply_theme()

    def _on_theme_changed(self):
        if getattr(self, "_loading_settings", False):
            return
        mode = self.theme_combo.currentData()
        if not mode or mode == self.settings.get("theme"):
            return
        self.settings["theme"] = mode
        set_theme_mode(mode)
        self._apply_theme()
        ok, err = save_settings(self.collect_settings())
        if not ok:
            self.statusBar().showMessage(f"设置保存失败: {err}", 5000)
        self.append_log(f"已切换为{'夜间模式' if mode == 'dark' else '白天模式'}")
        self.statusBar().showMessage(
            "已切换为夜间模式" if mode == "dark" else "已切换为白天模式", 3000
        )

    def _runtime_status_text(self):
        try:
            from importlib.metadata import version as pkg_version

            ytdlp_ver = pkg_version("yt-dlp")
        except Exception:
            ytdlp_ver = "未安装"
        js_runtime = next(
            (name for name in ("deno", "node", "bun", "quickjs") if shutil.which(name)),
            "",
        )
        js_text = f"已检测到 {js_runtime}" if js_runtime else "未检测到（YouTube 建议安装 Deno）"
        proxy = ""
        if hasattr(self, "proxy_edit"):
            proxy = self.proxy_edit.text().strip()
        proxy = proxy or (self.settings.get("proxy") or "").strip()
        proxy_text = proxy or "未配置（访问 YouTube 需单独填写）"
        from .. import __version__
        from .preview_player import find_mpv_executable

        ffmpeg_text = "ffmpeg 可用" if has_ffmpeg() else "ffmpeg 未找到"
        mpv_text = "mpv 可用" if find_mpv_executable() else "mpv 未安装（内置播放不可用）"
        return (
            f"v{__version__} · yt-dlp {ytdlp_ver} · {ffmpeg_text} · {mpv_text}\n"
            f"JS 运行时：{js_text} · 代理：{proxy_text}"
        )

    def _refresh_runtime_status(self):
        if hasattr(self, "runtime_status_label"):
            self.runtime_status_label.setText(self._runtime_status_text())

    def _copy_diagnostics(self):
        """复制排障信息，不包含 Cookie 内容或代理账号密码。"""
        from .. import __version__
        from .preview_player import find_mpv_executable

        lines = [
            f"多站点视频下载器 v{__version__}",
            f"Python: {sys.version.split()[0]}",
            f"系统: {platform.platform()}",
            self._runtime_status_text(),
            f"ffmpeg: {ffmpeg_path() if has_ffmpeg() else '未找到'}",
            f"mpv: {find_mpv_executable() or '未找到'}",
            f"程序目录: {BASE_DIR}",
            f"下载目录: {self.dir_edit.text().strip() or DEFAULT_DOWNLOAD_DIR}",
        ]
        QApplication.clipboard().setText("\n".join(lines))
        self.statusBar().showMessage("诊断信息已复制", 4000)
        self.append_log("已复制诊断信息")

    def _test_proxy(self):
        if self.proxy_test_worker is not None and self.proxy_test_worker.isRunning():
            return
        from ..workers.proxy import ProxyTestWorker

        proxy = self.proxy_edit.text().strip()
        self.proxy_status_label.setText(
            "正在通过代理连接 B站..." if proxy else "正在测试直连..."
        )
        self.proxy_test_btn.setEnabled(False)
        worker = ProxyTestWorker(proxy, self)
        worker.result.connect(self._on_proxy_test_result)
        worker.finished.connect(self._on_proxy_test_finished)
        self.proxy_test_worker = worker
        worker.start()

    def _on_proxy_test_result(self, ok, message):
        self.proxy_status_label.setText(("✓ " if ok else "✗ ") + message)
        self.statusBar().showMessage(message, 6000)
        self.append_log(f"代理测试: {message}")

    def _on_proxy_test_finished(self):
        self.proxy_test_btn.setEnabled(True)
        if self.sender() is self.proxy_test_worker:
            self.proxy_test_worker = None

    # ---------- 设置读写 ----------

    def apply_settings_to_ui(self):
        # 加载设置时下拉框会触发 currentIndexChanged，不要因此清掉已保存的自定义格式
        self._loading_settings = True
        try:
            theme_mode = self.settings.get("theme", "light")
            set_theme_mode(theme_mode)
            self.set_combo_value(self.theme_combo, theme_mode)
            self.dir_edit.setText(self.settings["download_dir"])
            self.proxy_edit.setText(self.settings.get("proxy", ""))
            self.cookie_file_edit.setText(self.settings.get("cookie_file", ""))
            self.auto_browser_login_check.setChecked(
                bool(self.settings.get("auto_browser_login", True))
            )
            self.template_edit.setText(self.settings.get("filename_template", DEFAULT_SETTINGS["filename_template"]))
            self.custom_format_edit.setText(self.settings.get("custom_format", ""))
            self._format_source_url = self.settings.get("custom_format_source", "")
            quality_value = self.settings.get("quality", "best")
            if (
                str(quality_value).isdigit()
                and self.quality_combo.findData(quality_value) < 0
            ):
                self.quality_combo.blockSignals(True)
                self.quality_combo.addItem(f"{quality_value}P", quality_value)
                self.quality_combo.blockSignals(False)
            self.set_combo_value(self.quality_combo, quality_value)
            self.set_combo_value(self.cookie_mode_combo, self.settings.get("cookie_mode", "none"))
            self.set_combo_value(self.thread_combo, int(self.settings.get("fragment_threads", 4)))
            self.concurrent_spin.setValue(int(self.settings.get("concurrent_downloads", 1) or 1))
            self.speed_limit_spin.setValue(
                int(self.settings.get("speed_limit_kb", 0) or 0)
            )
            self.set_combo_value(self.codec_combo, self.settings.get("codec_preference", "auto"))
            self.set_combo_value(self.audio_quality_combo, self.settings.get("audio_quality", "auto"))
            self.thumbnail_check.setChecked(bool(self.settings.get("download_thumbnail", False)))
            self.subtitle_check.setChecked(bool(self.settings.get("download_subtitle", False)))
            self.danmaku_check.setChecked(bool(self.settings.get("download_danmaku", False)))
            self.sakura_check.setChecked(bool(self.settings.get("fx_sakura", True)))
            self.neon_check.setChecked(bool(self.settings.get("fx_neon", True)))
            self.sound_check.setChecked(bool(self.settings.get("fx_sound", True)))
        finally:
            self._loading_settings = False
        self.apply_fx_settings()
        self.update_cookie_controls()
        self.refresh_selected_format_label()
        self._sync_quality_cards()
        self._update_bili_login_hint()
        self._sync_quick_toggles()
        self._refresh_side_path()
        self._refresh_queue_count()
        self._refresh_runtime_status()
        self._update_site_cookie_status()
        self._refresh_cookie_store()
        self._apply_theme()

    def set_combo_value(self, combo, value):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def collect_settings(self):
        settings = dict(self.settings)
        settings.update({
            "download_dir": self.dir_edit.text().strip() or str(DEFAULT_DOWNLOAD_DIR),
            "cookie_file": self.cookie_file_edit.text().strip(),
            "site_cookie_bilibili": self.cookie_bilibili_edit.text().strip(),
            "site_cookie_douyin": self.cookie_douyin_edit.text().strip(),
            "site_cookie_youtube": self.cookie_youtube_edit.text().strip(),
            "quality": self.quality_combo.currentData(),
            "custom_format": self.custom_format_edit.text().strip(),
            "custom_format_source": (
                self._format_source_url
                if self.custom_format_edit.text().strip()
                else ""
            ),
            "cookie_mode": self.cookie_mode_combo.currentData(),
            "cookie_file": self.cookie_file_edit.text().strip(),
            "auto_browser_login": self.auto_browser_login_check.isChecked(),
            "proxy": self.proxy_edit.text().strip(),
            "filename_template": self.template_edit.text().strip() or DEFAULT_SETTINGS["filename_template"],
            "fragment_threads": self.thread_combo.currentData(),
            "concurrent_downloads": self.concurrent_spin.value(),
            "speed_limit_kb": self.speed_limit_spin.value(),
            "codec_preference": self.codec_combo.currentData(),
            "audio_quality": self.audio_quality_combo.currentData(),
            "download_thumbnail": self.thumbnail_check.isChecked(),
            "download_subtitle": self.subtitle_check.isChecked(),
            "download_danmaku": self.danmaku_check.isChecked(),
            "fx_sakura": self.sakura_check.isChecked(),
            "fx_neon": self.neon_check.isChecked(),
            "fx_sound": self.sound_check.isChecked(),
            "theme": self.theme_combo.currentData() or "light",
        })
        return settings

    def reset_settings(self):
        """恢复默认设置并更新 UI。"""
        ret = QMessageBox.question(
            self, "恢复默认设置",
            "确定要恢复默认设置吗？\n当前自定义的下载目录、Cookie 等配置将被重置。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        self.settings = dict(DEFAULT_SETTINGS)
        ok, err = save_settings(self.settings)
        if not ok:
            QMessageBox.warning(self, "保存失败", f"恢复默认设置后保存失败：\n{err}")
            return
        self.apply_settings_to_ui()
        self.statusBar().showMessage("已恢复默认设置", 5000)

    def toggle_sakura_overlay(self, state):
        """开关樱花飘落层。"""
        if self.sakura_overlay is None:
            return
        self.sakura_overlay.setVisible(state == Qt.Checked)

    def apply_fx_settings(self):
        """应用外观开关到运行态。"""
        if self.sound_player:
            self.sound_player.enabled = self.sound_check.isChecked()
        neon_enabled = self.neon_check.isChecked()
        for card in self.findChildren(SoftCard):
            card.set_glow_enabled(neon_enabled)
        if self.sakura_overlay is not None:
            self.sakura_overlay.setVisible(self.sakura_check.isChecked())

    # ---------- 文件/Cookie 选择 ----------

    def choose_download_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "选择下载文件夹", self.dir_edit.text())
        if folder:
            self.dir_edit.setText(folder)
            self._refresh_side_path()

    def choose_cookie_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 cookies.txt", "",
            "Cookie Files (cookies.txt *.txt);;All Files (*.*)",
        )
        if path:
            self.cookie_file_edit.setText(path)
            self.set_combo_value(self.cookie_mode_combo, "file")

    def _update_site_cookie_status(self):
        if not hasattr(self, "cookie_douyin_status"):
            return
        path = self.cookie_douyin_edit.text().strip()
        if not path:
            text = "抖音 Cookie：未配置（可点上方「一键登录」获取）"
        elif not Path(path).is_file():
            text = "抖音 Cookie：文件不存在"
        elif cookie_file_contains_domain(path, "douyin.com"):
            text = "抖音 Cookie：已检测到 douyin.com"
        else:
            text = "抖音 Cookie：文件中没有 douyin.com，可能选错了文件"
        self.cookie_douyin_status.setText(text)

    def _refresh_cookie_store(self):
        if not hasattr(self, "cookie_store_label"):
            return
        items = list_domain_cookies()
        if not items:
            self.cookie_store_label.setText("Cookie 库：空（还没有 cookies/<域名>.txt）")
            self.cookie_store_label.setToolTip("")
            return
        names = [domain for domain, _path in items]
        shown = "、".join(names[:8])
        if len(names) > 8:
            shown += f" 等 {len(names)} 个站点"
        self.cookie_store_label.setText(f"Cookie 库：{shown}")
        self.cookie_store_label.setToolTip(
            "\n".join(str(path) for _domain, path in items)
        )

    def _manage_cookie_store(self):
        from .cookie_manager import CookieManagerDialog

        dialog = CookieManagerDialog(self)
        dialog.exec_()
        if dialog.deleted:
            deleted = set(dialog.deleted_domains)
            path_fields = (
                ("bilibili.com", self.cookie_bilibili_edit),
                ("youtube.com", self.cookie_youtube_edit),
                ("douyin.com", self.cookie_douyin_edit),
            )
            changed = False
            for domain, edit in path_fields:
                if domain not in deleted or not edit.text().strip():
                    continue
                try:
                    same = Path(edit.text().strip()).resolve() == cookie_store_path(
                        domain
                    ).resolve()
                except OSError:
                    same = False
                if same:
                    edit.clear()
                    changed = True
            invalidate_bili_login_cache()
            if changed:
                self.settings = self.collect_settings()
                ok, err = save_settings(self.settings)
                if not ok:
                    self.statusBar().showMessage(f"设置保存失败: {err}", 5000)
            self._refresh_cookie_store()
            self._update_site_cookie_status()
            self.append_log("已从 Cookie 库删除登录 Cookie")
            self.statusBar().showMessage("Cookie 已删除", 4000)

    def _open_cookie_login(self, url):
        """打开指定 URL 的 Cookie 登录流程，成功返回 True。"""
        domains = cookie_domains_for_url(url)
        if not domains:
            return False
        if detect_site(url) == SITE_DOUYIN:
            return self._start_douyin_login()

        try:
            from .douyin_login_dialog import CookieLoginDialog
        except ImportError as exc:
            QMessageBox.warning(
                self,
                "缺少依赖",
                "该功能需要 websocket-client。\n"
                f"请执行 py -m pip install websocket-client 后重试。\n\n{exc}",
            )
            return False

        domain = domains[0]
        dialog = CookieLoginDialog(
            self,
            site_label=site_label(url),
            start_url=url,
            cookie_domains=domains,
            output_path=str(cookie_store_path(domain)),
            profile_key=domain.replace(".", "_"),
        )
        if dialog.exec_() != QDialog.Accepted or not dialog.cookie_path:
            return False

        self._refresh_cookie_store()
        if domain == "bilibili.com":
            self.cookie_bilibili_edit.setText(dialog.cookie_path)
        elif domain == "youtube.com":
            self.cookie_youtube_edit.setText(dialog.cookie_path)
        self._update_site_cookie_status()
        self.settings = self.collect_settings()
        save_settings(self.settings)
        invalidate_bili_login_cache()
        self.append_log(f"已为 {site_label(url)} 导入 Cookie: {dialog.cookie_path}")
        self.statusBar().showMessage("Cookie 已导入 Cookie 库", 5000)
        return True

    def _start_any_site_cookie_login(self):
        lines = [
            line.strip()
            for line in self.input_edit.toPlainText().splitlines()
            if line.strip()
        ]
        default_url = lines[0] if lines else ""
        url, accepted = QInputDialog.getText(
            self,
            "为任意站点获取 Cookie",
            "输入要登录的站点或视频链接：\n"
            "程序会打开浏览器，登录后把 Cookie 保存到 cookies/<域名>.txt",
            text=default_url,
        )
        if not accepted or not url.strip():
            return
        if not self._open_cookie_login(url.strip()):
            domains = cookie_domains_for_url(url.strip())
            if not domains:
                QMessageBox.warning(
                    self,
                    "无法识别域名",
                    "这个链接没有可识别的域名。\n"
                    "请粘贴完整的视频页面链接。",
                )

    def _start_douyin_login(self, reparse=False):
        try:
            from .douyin_login_dialog import DouyinLoginDialog
        except ImportError as exc:
            QMessageBox.warning(
                self,
                "缺少依赖",
                "一键登录需要 websocket-client。\n"
                f"请执行 py -m pip install websocket-client 后重试。\n\n{exc}",
            )
            return False

        dialog = DouyinLoginDialog(self)
        if dialog.exec_() == QDialog.Accepted and dialog.cookie_path:
            self.cookie_douyin_edit.setText(dialog.cookie_path)
            self._update_site_cookie_status()
            self._refresh_cookie_store()
            self.settings = self.collect_settings()
            ok, err = save_settings(self.settings)
            if not ok:
                self.statusBar().showMessage(f"设置保存失败: {err}", 5000)
            from ..douyin_browser import clear_douyin_cache

            preview_url = self.preview_url_label.text().strip()
            if preview_url.startswith("http"):
                clear_douyin_cache(preview_url)
            self.append_log(f"抖音 Cookie 已导入: {dialog.cookie_path}")
            self.statusBar().showMessage("抖音 Cookie 已导入", 5000)
            if reparse:
                self.append_log("抖音登录成功，正在重新解析以获取更高画质")
                self.start_preview(force=True)
            return True
        return False

    def _choose_site_cookie_file(self, edit):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择该站点的 cookies.txt",
            edit.text() or "",
            "Cookie Files (cookies.txt *.txt);;All Files (*.*)",
        )
        if path:
            edit.setText(path)

    def update_cookie_controls(self):
        use_file = self.cookie_mode_combo.currentData() == "file"
        self.cookie_file_edit.setEnabled(use_file)
        self.cookie_file_btn.setEnabled(use_file)

    def open_download_dir(self):
        open_path_in_explorer(self.dir_edit.text() or str(DEFAULT_DOWNLOAD_DIR))

    # ---------- Cookie 检测 ----------

    def check_cookie_status(self):
        if self.cookie_check_worker and self.cookie_check_worker.isRunning():
            return
        self.settings = self.collect_settings()
        self.check_cookie_btn.setEnabled(False)
        self.statusBar().showMessage("正在检测 Cookie...")
        self.cookie_check_worker = CookieCheckWorker(self.settings, self)
        self.cookie_check_worker.result_ready.connect(self.on_cookie_check_result)
        self.cookie_check_worker.failed.connect(self.on_cookie_check_failed)
        self.cookie_check_worker.finished.connect(self.on_cookie_check_finished)
        self.cookie_check_worker.start()

    def on_cookie_check_result(self, result):
        if result.get("reason"):
            QMessageBox.warning(self, "Cookie 检测", result["reason"])
            return
        if not result.get("logged_in"):
            QMessageBox.information(
                self, "Cookie 检测",
                "Cookie 未登录或已失效。\n请重新导出 cookies.txt 或关闭浏览器后重试。",
            )
            return
        vip_text = ""
        if result.get("vip_status"):
            vip_type = result.get("vip_type")
            if vip_type == 2:
                vip_text = "（年度大会员）"
            elif vip_type == 1:
                vip_text = "（月度大会员）"
        msg = (
            f"Cookie 可用\n\n"
            f"用户名: {result.get('username') or '-'}\n"
            f"UID: {result.get('mid') or '-'}\n"
            f"大会员: {'是' + vip_text if result.get('vip_status') else '否'}\n"
            f"SESSDATA: {'已获取' if result.get('sessdata_found') else '未获取'}"
        )
        QMessageBox.information(self, "Cookie 检测", msg)

    def on_cookie_check_failed(self, msg):
        QMessageBox.warning(self, "Cookie 检测失败", msg)

    def on_cookie_check_finished(self):
        self.check_cookie_btn.setEnabled(True)
        self.statusBar().showMessage("Cookie 检测完成", 5000)

    def start_qr_login(self, reparse=False):
        """小白一键配置：扫码登录 B站，自动保存 cookies.txt。"""
        self.settings = self.collect_settings()
        dialog = QrLoginDialog(self.settings, self)
        dialog.start()
        if dialog.exec_() == QDialog.Accepted and dialog.cookie_file_path:
            self.cookie_file_edit.setText(dialog.cookie_file_path)
            self.cookie_bilibili_edit.setText(dialog.cookie_file_path)
            self.set_combo_value(self.cookie_mode_combo, "file")
            self.update_cookie_controls()
            self.settings = self.collect_settings()
            ok, err = save_settings(self.settings)
            if not ok:
                self.statusBar().showMessage(f"设置保存失败: {err}", 5000)
            invalidate_bili_login_cache()
            self.append_log(f"扫码登录成功，Cookie 已保存: {dialog.cookie_file_path}")
            self.statusBar().showMessage("扫码登录成功", 5000)
            if reparse:
                self.append_log("登录成功，正在重新解析以获取高清格式")
                self.start_preview(force=True)
            else:
                self.check_cookie_status()

    # ---------- 预览 ----------

    def schedule_preview(self):
        if self.worker and self.worker.isRunning():
            return
        self.preview_pending = True
        self.preview_timer.start(800)

    def start_preview(self, force=False):
        if self.worker and self.worker.isRunning():
            return
        if self.preview_worker and self.preview_worker.isRunning():
            if not force:
                return
            self.statusBar().showMessage("正在解析，请稍候...", 3000)
            return
        if force:
            # 手动点击解析时取消待触发的自动预览，避免解析两次
            self.preview_timer.stop()
            self.preview_pending = False
        if self.sound_player:
            self.sound_player.play("click")
        text = self.input_edit.toPlainText()
        urls = split_inputs(text)
        if not urls:
            self.clear_preview()
            return
        url = normalize_input(urls[0])
        if not force and not self.preview_pending:
            return
        self.preview_pending = False

        # 这类站点没有 Cookie 基本解析不了：手动解析时直接弹登录，
        # 自动预览时只给提示，避免用户一粘贴链接就被浏览器打断。
        site = detect_site(url)
        if site in COOKIE_REQUIRED_SITES and not has_cookie_for_url(
            url, self.collect_settings()
        ):
            if force:
                if not self._open_cookie_login(url):
                    self.clear_preview()
                    self.preview_title_label.setText("需要登录后才能解析")
                    self.preview_note_label.setText(
                        f"{site_label(url)} 需要 Cookie，已取消登录。"
                    )
                    return
            else:
                self.clear_preview()
                self.preview_title_label.setText("需要登录后才能解析")
                self.preview_note_label.setText(
                    f"{site_label(url)} 需要 Cookie，点击「解析视频」登录并保存。"
                )
                return

        self.preview_request_id += 1
        rid = self.preview_request_id
        self._preview_was_manual = bool(force)
        self.preview_btn.setEnabled(False)
        self.preview_title_label.setText("解析中...")
        self.preview_note_label.setText("如果是批量链接，这里预览第一个。")
        if site == SITE_DOUYIN:
            self.preview_note_label.setText(
                "抖音使用浏览器解析，可能会打开一个窗口，请稍等..."
            )
        self.formats_table.setRowCount(0)
        self.formats_block.setVisible(False)
        self.toggle_formats_btn.setEnabled(False)
        self.toggle_formats_btn.setText("查看全部格式")
        # 清掉上一个视频的格式信息，避免「补音频流」用到旧链接的音频
        self.preview_formats = []
        self._last_preview_info = None
        self._quality_locked = False
        self.bili_login_hint.setVisible(False)
        self.douyin_login_hint.setVisible(False)
        self.use_format_btn.setEnabled(False)
        self.preview_worker = PreviewWorker(rid, url, self.collect_settings(), self)
        self.preview_worker.info_ready.connect(self.on_preview_ready)
        self.preview_worker.failed.connect(self.on_preview_failed)
        self.preview_worker.finished.connect(self.on_preview_finished)
        self.preview_worker.start()

    def _wants_hd_quality(self):
        """当前设置是否期望 1080P+/仅音频/特定流（登录才可能拿到）。"""
        custom = (self.custom_format_edit.text() or "").strip()
        if custom:
            parts = [part.strip() for part in custom.split("+") if part.strip()]
            if any(part.startswith("302") for part in parts):
                return True
            video_qns = []
            for part in parts:
                if part.isdigit() and part.startswith("300"):
                    try:
                        video_qns.append(int(part) - 30000)
                    except ValueError:
                        pass
            if video_qns:
                return max(video_qns) >= 80
            return custom != "douyin-browser"
        quality = str(self.settings.get("quality") or "best")
        if quality in ("best", "audio"):
            return True
        if quality.isdigit():
            return int(quality) >= 1080
        return quality in ("1080", "1440", "2160")

    def _update_bili_login_hint(self, info=None):
        if not hasattr(self, "bili_login_hint"):
            return
        info = self._last_preview_info if info is None else info
        show = (
            isinstance(info, dict)
            and detect_site(info.get("url") or "") == SITE_BILIBILI
            and info.get("login_ok") is False
            and self._wants_hd_quality()
        )
        self.bili_login_hint.setVisible(bool(show))

    def _update_douyin_login_hint(self, info=None):
        if not hasattr(self, "douyin_login_hint"):
            return
        info = self._last_preview_info if info is None else info
        show = (
            isinstance(info, dict)
            and detect_site(info.get("url") or "") == SITE_DOUYIN
            and info.get("logged_in") is False
        )
        self.douyin_login_hint.setVisible(bool(show))

    def on_preview_ready(self, request_id, info):
        if request_id != self.preview_request_id:
            return
        self._last_preview_info = info
        is_douyin = detect_site(info.get("url") or "") == SITE_DOUYIN
        self._quality_locked = is_douyin and not info.get("qualities")
        if self.sound_player:
            self.sound_player.play("success")
        self.preview_formats = info.get("formats") or []
        self._set_video_settings_visible(True)
        self._rebuild_quality_cards(self._quality_options_from_formats(self.preview_formats))
        self.preview_title_label.setText(info.get("title") or "-")
        self.preview_uploader_label.setText(info.get("uploader") or "-")
        self.preview_duration_label.setText(format_duration(info.get("duration")))
        stats = f"▶ {format_count(info.get('view_count'))}    ♥ {format_count(info.get('like_count'))}"
        upload_date = format_upload_date(info.get("upload_date"))
        if upload_date != "-":
            stats += f"    · {upload_date}"
        self.preview_stats_label.setText(stats)
        pages = info.get("pages") or []
        self.preview_pages_label.setText(f"{len(pages)} P" if pages else "单 P")
        self.preview_source_label.setText(info.get("source") or "-")
        self.preview_url_label.setText(info.get("url") or "-")
        note = info.get("note") or "-"
        if detect_site(info.get("url") or "") == SITE_DOUYIN:
            note += "；已显示页面提供的全部画质，选择 MP3 可只保存音频"
            if info.get("logged_in") is False:
                note += "；当前抖音浏览器未登录，部分清晰度需要登录"
        elif info.get("login_ok") and info.get("login_source"):
            note += f"；登录来源：{info['login_source']}"
        self.preview_note_label.setText(note)
        self._update_bili_login_hint(info)
        self._update_douyin_login_hint(info)
        self._render_tags(info.get("tags") or [])
        self.cover_label.set_badge(format_duration(info.get("duration")))
        thumb_url = info.get("thumbnail") or ""
        cover_path = info.get("cover_path") or ""
        cover_pixmap = QPixmap()
        if cover_path and Path(cover_path).is_file():
            cover_pixmap = QPixmap(cover_path)
        if not cover_pixmap.isNull():
            self.cover_label.setPixmap(cover_pixmap)
        elif thumb_url:
            self._load_thumbnail(thumb_url)
        else:
            self.cover_label.setText("暂无封面")
            self.cover_label.setPixmap(QPixmap())
        self.fill_formats_table(self.preview_formats)
        self._reconcile_custom_format()
        if len(pages) > 1:
            self.fill_pages_table(pages)
            self.pages_actions.setVisible(True)
        else:
            self.pages_table.setRowCount(0)
            self.pages_table.setVisible(False)
            self.pages_actions.setVisible(False)
            self.add_pages_btn.setEnabled(False)
            self.select_all_pages_btn.setEnabled(False)

    def on_preview_failed(self, request_id, msg):
        if request_id != self.preview_request_id:
            return
        if self.sound_player:
            self.sound_player.play("fail")
        self.preview_formats = []
        self._quality_locked = False
        self._rebuild_quality_cards([])
        self.preview_title_label.setText("解析失败")
        self.preview_note_label.setText(msg)
        self._last_preview_info = None
        self._update_bili_login_hint()
        self._update_douyin_login_hint()

        if not self._preview_was_manual:
            # 自动解析是输入防抖触发的，失败时不能弹模态框打断用户输入。
            self.statusBar().showMessage("自动解析失败，点「解析视频」重试", 5000)
            return

        current_url = ""
        for line in self.input_edit.toPlainText().splitlines():
            if line.strip():
                current_url = normalize_input(line.strip())
                break
        needs_douyin_login = (
            current_url
            and detect_site(current_url) == SITE_DOUYIN
            and (
                "cookie" in msg.lower()
                or "fresh" in msg.lower()
                or "403" in msg.lower()
                or "登录" in msg
            )
        )
        if needs_douyin_login:
            box = QMessageBox(self)
            box.setWindowTitle("解析失败")
            box.setIcon(QMessageBox.Warning)
            box.setText(
                f"无法解析该链接：\n\n{msg}\n\n"
                "可以直接点「去登录抖音」，在打开的浏览器里扫码登录，"
                "完成后会自动重新解析。"
            )
            login_btn = box.addButton("去登录抖音", QMessageBox.AcceptRole)
            box.addButton("关闭", QMessageBox.RejectRole)
            box.exec_()
            if box.clickedButton() is login_btn and self._start_douyin_login():
                self.start_preview(force=True)
            return

        QMessageBox.warning(
            self,
            "解析失败",
            f"无法解析该链接：\n\n{msg}\n\n请检查：\n"
            "1. 链接是否正确\n2. 是否需要配置 Cookie\n3. 网络是否连接正常",
        )

    def on_preview_finished(self):
        self.preview_btn.setEnabled(True)

    def _load_thumbnail(self, url):
        """异步加载封面，避免主线程网络请求阻塞界面。"""
        proxy = (self.settings.get("proxy") or "").strip()
        self.cover_label.setPixmap(QPixmap())
        self.cover_label.setText("封面加载中...")
        rid = self.preview_request_id
        # 保留所有 worker 引用，防止线程运行中被 GC 回收导致崩溃
        self._thumb_workers = [w for w in self._thumb_workers if w.isRunning()]
        worker = ThumbnailWorker(url, proxy, self)
        worker.loaded.connect(lambda data, r=rid: self._on_thumbnail_loaded(r, data))
        worker.failed.connect(lambda msg, r=rid: self._on_thumbnail_failed(r, msg))
        self._thumb_workers.append(worker)
        worker.start()

    def _on_thumbnail_loaded(self, request_id, data):
        if request_id != self.preview_request_id:
            return
        pixmap = QPixmap()
        pixmap.loadFromData(data)
        if pixmap.isNull():
            self.cover_label.setText("封面加载失败")
            return
        self.cover_label.setPixmap(pixmap)

    def _on_thumbnail_failed(self, request_id, msg):
        if request_id != self.preview_request_id:
            return
        self.cover_label.setPixmap(QPixmap())
        self.cover_label.setText("封面加载失败")

    def fill_formats_table(self, formats):
        """渲染格式表：默认按分辨率合并，勾选后显示全部原始流。"""
        expanded = self.formats_block.isVisible()
        self.formats_table.setRowCount(0)
        self.formats_block.setVisible(bool(formats) and expanded)
        self.toggle_formats_btn.setEnabled(bool(formats))
        self.toggle_formats_btn.setText(
            ("收起格式列表" if expanded else f"查看全部格式 ({len(formats)})")
            if formats else "查看全部格式"
        )
        if not formats:
            self.refresh_selected_format_label()
            return
        if self._show_raw_streams():
            self._fill_raw_stream_table(formats)
        else:
            self._fill_grouped_format_table(formats)
        self.refresh_selected_format_label()

    def _toggle_formats(self):
        """展开 / 收起可用格式列表。"""
        if not self.preview_formats:
            return
        will_show = not self.formats_block.isVisible()
        self.formats_block.setVisible(will_show)
        self.toggle_formats_btn.setText(
            "收起格式列表" if will_show else f"查看全部格式 ({len(self.preview_formats)})"
        )

    def _show_raw_streams(self):
        check = getattr(self, "show_streams_check", None)
        return bool(check and check.isChecked())

    def on_show_streams_changed(self, state):
        """切换「按分辨率合并 / 全部原始流」视图后重画表格。"""
        if self.preview_formats:
            self.fill_formats_table(self.preview_formats)
        else:
            self.formats_table.setRowCount(0)
            self.formats_block.setVisible(False)
            self.refresh_selected_format_label()

    def _pick_video_for_height(self, candidates):
        """同一分辨率下按「编码偏好」挑一条视频流；auto 时优先 H.264（兼容性最好）。"""
        preference = (self.settings.get("codec_preference") or "auto").lower()
        orders = {
            "h264": ("avc", "hvc", "hev", "av0"),
            "hevc": ("hvc", "hev", "avc", "av0"),
            "av1": ("av0", "hvc", "hev", "avc"),
        }
        for key in orders.get(preference, ("avc", "hvc", "hev", "av0")):
            matched = [f for f in candidates if key in str(f.get("vcodec") or "").lower()]
            if matched:
                return max(matched, key=lambda item: item.get("tbr") or 0)
        return candidates[0] if candidates else None

    def _fill_grouped_format_table(self, formats):
        """按分辨率合并渲染：每行 = 该分辨率的视频 + 最佳音频，选中即自动合并成带声音的成品。

        B站 1080P 起走 DASH，服务端音视频本来就是两条独立流
        （实测某个视频 15 条流 = 12 条纯视频 + 3 条纯音频 + 0 条自带声音），
        所以「一个分辨率一行」才是普通用户真正需要看到的列表。
        """
        video_only, audio_only, muxed = [], [], []
        for f in formats:
            vcodec = f.get("vcodec") or ""
            acodec = f.get("acodec") or ""
            is_video = bool(vcodec and vcodec != "none")
            is_audio = bool(acodec and acodec != "none")
            if is_video and is_audio:
                muxed.append(f)
            elif is_video:
                video_only.append(f)
            elif is_audio:
                audio_only.append(f)

        best_audio = max(audio_only, key=lambda item: item.get("tbr") or 0, default=None)
        audio_id = str(best_audio.get("format_id") or "") if best_audio else ""
        ffmpeg_ready = has_ffmpeg()

        def resolution_of(f):
            if f.get("width") and f.get("height"):
                return f"{f['width']}x{f['height']}"
            if f.get("height"):
                return f"{f['height']}p"
            return ""

        def add_row(f, ftype, format_id, note, recommend="", size=None):
            row = self.formats_table.rowCount()
            self.formats_table.insertRow(row)
            mark_item = QTableWidgetItem(recommend)
            mark_item.setData(Qt.UserRole, recommend)
            self.formats_table.setItem(row, 0, mark_item)
            self.formats_table.setItem(row, 1, QTableWidgetItem(format_id))
            self.formats_table.setItem(row, 2, QTableWidgetItem(ftype))
            self.formats_table.setItem(row, 3, QTableWidgetItem(resolution_of(f)))
            self.formats_table.setItem(row, 4, QTableWidgetItem(format_fps(f.get("fps"))))
            self.formats_table.setItem(row, 5, QTableWidgetItem(f.get("vcodec") or "-"))
            self.formats_table.setItem(
                row, 6, QTableWidgetItem(format_bytes(size if size is not None else f.get("filesize")))
            )
            self.formats_table.setItem(row, 7, QTableWidgetItem(note))

        if not video_only:
            # 站点直接提供音视频一体的成品（例如 YouTube 的单文件格式）
            ordered = sorted(muxed, key=lambda item: item.get("height") or 0, reverse=True)
            for index, f in enumerate(ordered):
                note = f.get("note") or "可直接下载"
                add_row(f, "音视频", str(f.get("format_id", "")), note,
                        recommend="推荐" if index == 0 else "")
            if best_audio:
                add_row(best_audio, "音频", audio_id, "仅音频，没有画面")
            return

        groups = {}
        for f in video_only:
            groups.setdefault(f.get("height") or 0, []).append(f)
        for index, height in enumerate(sorted(groups, reverse=True)):
            video = self._pick_video_for_height(groups[height])
            if video is None:
                continue
            video_id = str(video.get("format_id", ""))
            if audio_id and ffmpeg_ready:
                size = (video.get("filesize") or 0) + (best_audio.get("filesize") or 0)
                add_row(video, "视频+音频", f"{video_id}+{audio_id}",
                        "视频+最佳音频，下载时自动合并",
                        recommend="推荐" if index == 0 else "", size=size)
            else:
                note = "纯视频流，没有声音" if ffmpeg_ready else "纯视频流，缺 ffmpeg 无法合并音频"
                add_row(video, "视频", video_id, note)
        if best_audio:
            add_row(best_audio, "音频", audio_id, "仅音频，没有画面")

    def _fill_raw_stream_table(self, formats):
        # 找出最高分辨率的视频流和最高码率的音频流，用于推荐标记
        best_video = None
        best_audio = None
        for f in formats:
            vcodec = f.get("vcodec") or ""
            acodec = f.get("acodec") or ""
            if vcodec and vcodec != "none":
                if best_video is None or (f.get("height") or 0) > (best_video.get("height") or 0):
                    best_video = f
            if acodec and acodec != "none" and (not vcodec or vcodec == "none"):
                if best_audio is None or (f.get("tbr") or 0) > (best_audio.get("tbr") or 0):
                    best_audio = f

        has_cookie = self.settings.get("cookie_mode") != "none"
        ffmpeg_ready = has_ffmpeg()

        for f in formats:
            row = self.formats_table.rowCount()
            self.formats_table.insertRow(row)
            vcodec = f.get("vcodec") or ""
            acodec = f.get("acodec") or ""
            is_video = bool(vcodec and vcodec != "none")
            is_audio = bool(acodec and acodec != "none")
            if is_video and is_audio:
                ftype = "音视频"
            elif is_video:
                ftype = "视频"
            elif is_audio:
                ftype = "音频"
            else:
                ftype = "?"
            resolution = ""
            if f.get("width") and f.get("height"):
                resolution = f"{f['width']}x{f['height']}"
            elif f.get("height"):
                resolution = f"{f['height']}p"

            # 标记：纯视频流/纯音频流绝不标「推荐」，否则用户会选中没有声音的格式
            recommend = ""
            if is_video and not is_audio:
                recommend = "视频流"
            elif is_audio and not is_video:
                recommend = "音频流"

            # 可用性说明
            notes = []
            if f.get("note"):
                notes.append(f["note"])
            # DASH 视频+音频需要 ffmpeg 合并
            if is_video and not is_audio:
                notes.append("需合并音频")
                if not ffmpeg_ready:
                    notes.append("缺ffmpeg")
            # 高清格式可能需要 Cookie
            if is_video and (f.get("height") or 0) >= 1080 and not has_cookie:
                notes.append("需Cookie")
            note_text = " | ".join(notes) if notes else ("可直接下载" if (is_video and is_audio) else "")

            mark_item = QTableWidgetItem(recommend)
            # 原始标记存进 UserRole，方便在其上叠加「已选」而不丢「推荐」
            mark_item.setData(Qt.UserRole, recommend)
            self.formats_table.setItem(row, 0, mark_item)
            self.formats_table.setItem(row, 1, QTableWidgetItem(str(f.get("format_id", ""))))
            self.formats_table.setItem(row, 2, QTableWidgetItem(ftype))
            self.formats_table.setItem(row, 3, QTableWidgetItem(resolution))
            self.formats_table.setItem(row, 4, QTableWidgetItem(format_fps(f.get("fps"))))
            self.formats_table.setItem(row, 5, QTableWidgetItem(vcodec or "-"))
            self.formats_table.setItem(row, 6, QTableWidgetItem(format_bytes(f.get("filesize"))))
            self.formats_table.setItem(row, 7, QTableWidgetItem(note_text))

        # 增加 DASH 组合推荐行
        if best_video and best_audio and ffmpeg_ready:
            row = self.formats_table.rowCount()
            self.formats_table.insertRow(row)
            combined_size = (best_video.get("filesize") or 0) + (best_audio.get("filesize") or 0)
            vres = ""
            if best_video.get("width") and best_video.get("height"):
                vres = f"{best_video['width']}x{best_video['height']}"
            elif best_video.get("height"):
                vres = f"{best_video['height']}p"
            combo_item = QTableWidgetItem("推荐")
            combo_item.setData(Qt.UserRole, "推荐")
            self.formats_table.setItem(row, 0, combo_item)
            self.formats_table.setItem(row, 1, QTableWidgetItem(f"{best_video.get('format_id','')}+{best_audio.get('format_id','')}"))
            self.formats_table.setItem(row, 2, QTableWidgetItem("DASH组合"))
            self.formats_table.setItem(row, 3, QTableWidgetItem(vres))
            self.formats_table.setItem(row, 4, QTableWidgetItem(format_fps(best_video.get("fps"))))
            self.formats_table.setItem(row, 5, QTableWidgetItem(best_video.get("vcodec") or "-"))
            self.formats_table.setItem(row, 6, QTableWidgetItem(format_bytes(combined_size)))
            self.formats_table.setItem(row, 7, QTableWidgetItem("视频+音频合并 | 需ffmpeg"))

        # 没有可拆分的 DASH 流时（例如已经有音视频一体的格式），退回推荐最佳单文件
        if not (best_video and best_audio and ffmpeg_ready):
            row = self._best_muxed_row()
            if row is not None:
                item = self.formats_table.item(row, 0)
                if item is not None:
                    item.setData(Qt.UserRole, "推荐")
                    item.setText("推荐")

    def fill_pages_table(self, pages):
        self.pages_table.setRowCount(0)
        for p in pages:
            row = self.pages_table.rowCount()
            self.pages_table.insertRow(row)
            self.pages_table.setItem(row, 0, QTableWidgetItem(str(p.get("page", 1))))
            self.pages_table.setItem(row, 1, QTableWidgetItem(p.get("title") or "-"))
            self.pages_table.setItem(row, 2, QTableWidgetItem(format_duration(p.get("duration"))))
            self.pages_table.setItem(row, 3, QTableWidgetItem(p.get("url") or "-"))
        self.pages_table.setVisible(True)
        self.pages_actions.setVisible(True)
        self.pages_label.setVisible(True)
        self.add_pages_btn.setEnabled(True)
        self.select_all_pages_btn.setEnabled(True)

    def clear_preview(self):
        self.preview_title_label.setText("粘贴链接后自动解析")
        self.preview_uploader_label.setText("-")
        self.preview_duration_label.setText("-")
        self.preview_stats_label.setText("-")
        self.preview_pages_label.setText("-")
        self.preview_source_label.setText("-")
        self.preview_url_label.setText("-")
        self.preview_note_label.setText("-")
        self.cover_label.setText("暂无封面")
        self.cover_label.setPixmap(QPixmap())
        self.formats_table.setRowCount(0)
        self.formats_block.setVisible(False)
        self.toggle_formats_btn.setEnabled(False)
        self.toggle_formats_btn.setText("查看全部格式")
        self._quality_locked = False
        self._rebuild_quality_cards([])
        self._set_video_settings_visible(False)
        self.pages_table.setRowCount(0)
        self.pages_table.setVisible(False)
        self.pages_actions.setVisible(False)
        self.pages_label.setVisible(False)
        self.use_format_btn.setEnabled(False)
        self.add_pages_btn.setEnabled(False)
        self.select_all_pages_btn.setEnabled(False)
        self._render_tags([])
        self._last_preview_info = None
        self._update_bili_login_hint()
        self._update_douyin_login_hint()
        self.refresh_selected_format_label()

    def on_format_selection_changed(self):
        selected = self.formats_table.selectionModel().selectedRows()
        self.use_format_btn.setEnabled(bool(selected))
        if selected:
            row = selected[0].row()
            item = self.formats_table.item(row, 1)
            self.statusBar().showMessage(
                f"已选中格式 {item.text() if item else ''}，双击或点「使用这个格式」确认", 4000
            )

    def on_format_double_clicked(self, index):
        """双击格式行 = 使用这个格式。"""
        self.formats_table.selectRow(index.row())
        self.use_selected_format()

    def _best_muxed_row(self):
        """返回分辨率最高的「音视频一体」格式所在行（没有则 None）。"""
        best_row, best_height = None, -1
        for row in range(self.formats_table.rowCount()):
            type_item = self.formats_table.item(row, 2)
            if not type_item or type_item.text() != "音视频":
                continue
            res_item = self.formats_table.item(row, 3)
            height = 0
            if res_item and "x" in res_item.text():
                try:
                    height = int(res_item.text().split("x")[1])
                except ValueError:
                    height = 0
            if height >= best_height:
                best_row, best_height = row, height
        return best_row

    def _best_audio_format_id(self):
        """从预览到的格式里挑码率最高的纯音频流。"""
        best_id, best_tbr = "", -1
        for f in self.preview_formats:
            vcodec = f.get("vcodec") or ""
            acodec = f.get("acodec") or ""
            if acodec and acodec != "none" and (not vcodec or vcodec == "none"):
                tbr = f.get("tbr") or 0
                if tbr > best_tbr:
                    best_id, best_tbr = str(f.get("format_id") or ""), tbr
        return best_id

    def _resolve_format_choice(self, row, format_id):
        """把表格里选中的格式转换成实际使用的格式字符串，并给出提示。

        纯视频流必须自动补音频流：B站 DASH 的视频和音频是分开的，
        只下视频流会得到没有声音的视频（历史记录里的 format_id=30080 就是这样来的）。
        """
        type_item = self.formats_table.item(row, 2)
        ftype = type_item.text() if type_item else ""
        if ftype == "视频":
            audio_id = self._best_audio_format_id()
            if audio_id and has_ffmpeg():
                return f"{format_id}+{audio_id}", f"（纯视频流，已自动补上音频流 {audio_id}）"
            if not has_ffmpeg():
                return format_id, "（纯视频流，未检测到 ffmpeg 无法合并音频，下载结果没有声音）"
            return format_id, "（纯视频流，没有可用的音频流，下载结果可能没有声音）"
        if ftype == "音频":
            return format_id, "（仅音频，没有画面）"
        if ftype == "DASH组合":
            return format_id, "（视频+音频组合，需要 ffmpeg 合并）"
        return format_id, ""

    def _ensure_audio_track(self, format_id):
        """下载前的兜底：设置里残留在纯视频流时自动补上音频流，避免下出无声视频。"""
        if not format_id or "+" in format_id:
            return format_id
        for f in self.preview_formats:
            if str(f.get("format_id") or "") != format_id:
                continue
            vcodec = f.get("vcodec") or ""
            acodec = f.get("acodec") or ""
            if vcodec and vcodec != "none" and (not acodec or acodec == "none"):
                audio_id = self._best_audio_format_id()
                if audio_id and has_ffmpeg():
                    return f"{format_id}+{audio_id}"
            return format_id
        return format_id

    def _describe_format(self, format_id):
        """给「正在使用的格式」加一句可读说明，用于日志与悬停提示。"""
        if not format_id:
            return ""
        if "+" in format_id:
            return "视频+音频组合"
        for f in self.preview_formats:
            if str(f.get("format_id") or "") != format_id:
                continue
            vcodec = f.get("vcodec") or ""
            acodec = f.get("acodec") or ""
            is_video = bool(vcodec and vcodec != "none")
            is_audio = bool(acodec and acodec != "none")
            if is_video and not is_audio:
                return "纯视频流，下载后没有声音"
            if is_audio and not is_video:
                return "纯音频流，没有画面"
        return ""

    def _format_row_for(self, format_id):
        """查找格式对应的行。

        先找完全相等的行（例如音频行 30280），再退而匹配包含该流的组合行。
        不能先做包含匹配，否则选音频 30280 会错误命中第一条「视频+音频」行。
        """
        if not format_id:
            return None
        candidates = [format_id]
        if "+" in format_id:
            candidates.append(format_id.split("+")[0].strip())

        for candidate in candidates:
            for row in range(self.formats_table.rowCount()):
                item = self.formats_table.item(row, 1)
                if item and item.text() == candidate:
                    return row

        for candidate in candidates:
            for row in range(self.formats_table.rowCount()):
                item = self.formats_table.item(row, 1)
                if not item:
                    continue
                row_ids = {part.strip() for part in item.text().split("+") if part.strip()}
                if candidate in row_ids:
                    return row
        return None

    def refresh_format_selection_marks(self):
        """只标记当前真正使用的那一行，不再把所有包含同一音频流的行都标成已选。"""
        selected = (self.custom_format_edit.text() or "").strip()
        target_row = self._format_row_for(selected) if selected else None
        matched = False
        for row in range(self.formats_table.rowCount()):
            item = self.formats_table.item(row, 0)
            if item is None:
                continue
            base = item.data(Qt.UserRole)
            if base is None:
                base = item.text()
                item.setData(Qt.UserRole, base)
            if row == target_row:
                item.setText(f"✔ 已选 {base}".strip())
                matched = True
            else:
                item.setText(base)
        return matched

    def _set_format_chip(self, text, state="idle"):
        """更新下载页「下载格式」提示条的文案与配色。"""
        styles = {
            "idle": f"color:{theme_get('text_soft')};",
            "active": f"color:{theme_get('blue_text')}; font-weight:600;",
            "warn": f"color:{theme_get('pink_text')}; font-weight:600;",
        }
        self.selected_format_label.setText(text)
        self.selected_format_label.setToolTip(text)
        self.selected_format_label.setStyleSheet(styles.get(state, styles["idle"]))

    def _reconcile_custom_format(self):
        """新视频解析完成后，校验旧的自定义格式是否还存在。"""
        selected = ""
        if hasattr(self, "custom_format_edit"):
            selected = self.custom_format_edit.text().strip()
        if selected and self._format_row_for(selected) is None:
            self.custom_format_edit.clear()
            self._format_source_url = ""
            self.formats_table.clearSelection()
            self.append_log("新视频没有该自定义格式，已恢复按画质自动选择")
        self._sync_quality_cards()
        self.refresh_selected_format_label()
        self._update_bili_login_hint()

    def refresh_selected_format_label(self):
        """刷新「当前使用格式」提示，并同步格式表里的已选标记。

        三种状态：未选择（跟随清晰度）/ 已选且在当前格式表中找到 / 已选但表中没有匹配项。
        """
        if not hasattr(self, "selected_format_label") or not hasattr(self, "custom_format_edit"):
            return
        selected = (self.custom_format_edit.text() or "").strip()
        if not selected:
            self.refresh_format_selection_marks()
            quality_text = self._quality_text(self.settings.get("quality", "best"))
            self._set_format_chip(f"自动 · {quality_text}", "idle")
            return
        if not self.preview_formats:
            self.refresh_format_selection_marks()
            self._set_format_chip(f"自定义格式 {selected} · 解析后校验", "idle")
            return
        if self.refresh_format_selection_marks():
            row = self._format_row_for(selected)
            details = []
            for col in (2, 3):
                item = self.formats_table.item(row, col) if row is not None else None
                if item and item.text() and item.text() != "-":
                    details.append(item.text())
            suffix = f"（{' · '.join(details)}）" if details else ""
            self._set_format_chip(f"自定义格式：{selected}{suffix}", "active")
        else:
            self._set_format_chip(
                f"自定义格式：{selected} · 当前视频没有这个格式", "warn"
            )

    @staticmethod
    def _custom_format_site_hint(format_id):
        """从格式字符串粗略判断它来自哪个站点，用于清理跨站点残留格式。"""
        format_id = (format_id or "").strip()
        if not format_id:
            return ""
        if format_id == "douyin-browser":
            return SITE_DOUYIN
        parts = [part.strip() for part in format_id.split("+") if part.strip()]
        if parts and all(part.isdigit() for part in parts):
            # B站 DASH 视频流 300xx / 音频 302xx，部分 AV1 流以 1000 开头
            if any(part.startswith(("300", "302", "1000")) for part in parts):
                return SITE_BILIBILI
        return ""

    def _custom_format_compatible(self, urls):
        """当前格式是否适用于这一批下载链接。"""
        format_id = (self.custom_format_edit.text() or "").strip()
        if not format_id:
            return True
        sites = {detect_site(url) for url in urls if url}
        if len(sites) > 1:
            return False
        source_site = detect_site(self._format_source_url) if self._format_source_url else ""
        expected = source_site or self._custom_format_site_hint(format_id)
        if expected and sites and expected not in sites:
            return False
        return True

    def use_selected_format(self):
        selected = self.formats_table.selectionModel().selectedRows()
        if not selected:
            return
        row = selected[0].row()
        format_item = self.formats_table.item(row, 1)
        if format_item is None:
            return
        format_id = format_item.text()
        if format_id.startswith("douyin-"):
            quality = format_id.split("-", 1)[1]
            chosen_quality = quality if quality.isdigit() else "best"
            self.custom_format_edit.clear()
            self._format_source_url = ""
            self.select_quality(chosen_quality)
            self.append_log(
                f"抖音画质已切换为 {chosen_quality if chosen_quality != 'best' else '智能'}"
            )
            self.statusBar().showMessage(
                f"抖音画质：{chosen_quality if chosen_quality != 'best' else '智能'}"
                "（下载时按页面该画质重新捕获）",
                6000,
            )
            return
        if format_id == "douyin-browser":
            # 兼容旧缓存里的格式 ID
            self.custom_format_edit.clear()
            self._format_source_url = ""
            self.select_quality("best")
            self.append_log("抖音浏览器模式使用页面智能画质")
            self.statusBar().showMessage(
                "抖音按页面智能画质下载；选择 MP3 可只保存音频", 6000
            )
            return
        chosen, hint = self._resolve_format_choice(row, format_id)
        self.custom_format_edit.setText(chosen)
        source_url = self.preview_url_label.text().strip()
        self._format_source_url = source_url if source_url.startswith("http") else ""
        self._sync_quality_cards()
        self.refresh_selected_format_label()
        self._update_bili_login_hint()
        if self.sound_player:
            self.sound_player.play("click")
        self.append_log(f"已选择格式: {chosen}{hint}")
        self.statusBar().showMessage(
            f"已选择格式 {chosen}{hint}，点「开始下载吧」即按此格式下载", 6000
        )

    def clear_selected_format(self):
        """清除已选格式，回到「按清晰度自动选择」。"""
        if not (self.custom_format_edit.text() or "").strip():
            self.statusBar().showMessage("当前没有选择格式", 3000)
            return
        self.custom_format_edit.clear()
        self._format_source_url = ""
        self.refresh_selected_format_label()
        self._update_bili_login_hint()
        self._sync_quality_cards()
        self.append_log("已清除格式选择，改为按「清晰度」设置自动选择")
        self.statusBar().showMessage("已清除格式选择", 3000)

    def add_selected_pages_to_input(self):
        selected_rows = sorted({idx.row() for idx in self.pages_table.selectionModel().selectedRows()})
        if not selected_rows:
            QMessageBox.information(self, "提示", "请先在分 P 列表中选择要下载的分 P。")
            return
        urls = []
        for r in selected_rows:
            url_item = self.pages_table.item(r, 3)
            if url_item and url_item.text().strip():
                urls.append(url_item.text().strip())
        if not urls:
            return
        existing = self.input_edit.toPlainText().strip()
        existing_urls = split_inputs(existing)
        before = len(self._dedupe_input_urls(existing_urls))
        combined = self._dedupe_input_urls(existing_urls + urls)
        self.input_edit.setPlainText("\n".join(combined))
        added = len(combined) - before
        if added:
            self.statusBar().showMessage(f"已添加 {added} 个分 P（重复项已忽略）", 3000)
        else:
            self.statusBar().showMessage("选中的分 P 已经在输入框中", 3000)

    def _update_input_clear(self):
        has_text = bool(self.input_edit.toPlainText().strip())
        if hasattr(self, "clear_input_btn"):
            self.clear_input_btn.setVisible(has_text)
        if not has_text:
            self._set_video_settings_visible(False)

    def clear_input(self):
        self.custom_format_edit.clear()
        self._format_source_url = ""
        self.input_edit.clear()
        self.clear_preview()
        self.statusBar().showMessage("已清空输入", 2000)

    def import_links_from_file(self):
        """从 .txt 文件导入链接列表。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "导入链接文件", "", "文本文件 (*.txt);;所有文件 (*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            urls = self._dedupe_input_urls(split_inputs(content))
            if not urls:
                QMessageBox.information(self, "提示", "文件中未找到有效链接。")
                return
            existing_urls = split_inputs(self.input_edit.toPlainText())
            before = len(self._dedupe_input_urls(existing_urls))
            combined = self._dedupe_input_urls(existing_urls + urls)
            self.input_edit.setPlainText("\n".join(combined))
            added = len(combined) - before
            self.statusBar().showMessage(f"已导入 {added} 条新链接（重复项已忽略）", 3000)
            self.append_log(f"从文件导入 {added} 条新链接: {path}")
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", f"读取文件失败：\n{format_error(exc)}")

    # ---------- 下载 ----------

    def check_cookie_settings(self):
        mode = self.settings.get("cookie_mode") or "none"
        if mode == "file":
            cookie_file = (self.settings.get("cookie_file") or "").strip()
            if not cookie_file:
                return "Cookie 模式为 cookies.txt，但未选择文件。"
            if not Path(cookie_file).exists():
                return f"Cookie 文件不存在: {cookie_file}"
        elif mode in ("chrome", "edge", "firefox"):
            pass
        return ""

    @staticmethod
    def _dedupe_input_urls(urls):
        """按视频维度去重，避免同一个链接/同一个分 P 被下载两次。"""
        unique = []
        seen = set()
        for raw in urls:
            url = normalize_input(raw)
            if not url:
                continue
            bvid = extract_bvid(url)
            aid = extract_aid(url)
            if bvid:
                page = selected_page_number(url)
                key = f"bili:bv:{bvid}:p{page}"
                url = f"https://www.bilibili.com/video/{bvid}"
                if page > 1:
                    url += f"?p={page}"
            elif aid:
                page = selected_page_number(url)
                key = f"bili:av:{aid}:p{page}"
                url = f"https://www.bilibili.com/video/av{aid}"
                if page > 1:
                    url += f"?p={page}"
            else:
                parsed = urlparse(url)
                key = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}?{parsed.query}"
            if key in seen:
                continue
            seen.add(key)
            unique.append(url)
        return unique

    # ---------- 队列持久化 ----------

    def _task_for_index(self, index):
        if 0 <= index < len(self.queue_tasks):
            return self.queue_tasks[index]
        return None

    def _task_labels(self, settings):
        custom_format = (settings.get("custom_format") or "").strip()
        quality_label = self._quality_text(settings.get("quality", "best"))
        format_desc = custom_format or f"自动（{quality_label}）"
        format_note = self._describe_format(custom_format)
        format_full = (
            f"{format_desc}（{format_note}）" if format_note else format_desc
        )
        return format_full, quality_label

    def _task_from_url(self, url, base_settings, status="等待", **extra):
        settings = dict(base_settings)
        custom_format = (settings.get("custom_format") or "").strip()
        if custom_format and not self._custom_format_compatible([url]):
            settings["custom_format"] = ""
            settings["custom_format_source"] = ""
        format_full, quality_label = self._task_labels(settings)
        task = {
            "url": url,
            "settings": settings,
            "status": status,
            "progress": 0.0,
            "detail": "等待开始" if status == "等待" else "",
            "output": "",
            "error": "",
            "format_full": format_full,
            "quality_label": quality_label,
            "created_at": int(time.time()),
        }
        task.update(extra)
        return task

    def _apply_task_to_row(self, row, task):
        status = task.get("status") or "等待"
        url = task.get("url") or ""
        output = task.get("output") or ""
        error = task.get("error") or ""
        if status in self.QUEUE_FINISHED_STATES:
            detail = "已完成" if status.startswith("完成") else (
                "已取消" if status == "已取消" else (error or status)
            )
        elif status in self.QUEUE_EXCLUDED_STATES:
            detail = task.get("detail") or status
        else:
            detail = task.get("detail") or "等待开始"
        self.set_cell(row, 0, url)
        self.set_cell(row, 1, status)
        self.set_cell(row, 2, detail)
        self.set_cell(row, 3, output)
        self._install_queue_visuals(row, status)
        self._set_row_status(row, status)
        if status.startswith("完成"):
            percent = 100
        elif status in ("失败", "已取消", "已删除", "已跳过"):
            percent = -1
        else:
            percent = float(task.get("progress") or 0)
        self._set_row_progress(row, percent, detail)
        head_item = self.table.item(row, 0)
        if head_item:
            head_item.setIcon(make_media_tile())
            head_item.setToolTip(
                f"格式: {task.get('format_full') or '-'}\n"
                f"清晰度: {task.get('quality_label') or '-'}\n"
                f"链接: {url}"
            )

    def _populate_queue_table(self, tasks):
        self.queue_tasks = list(tasks)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for row, task in enumerate(self.queue_tasks):
            self.table.insertRow(row)
            self.set_cell(row, 0, task.get("url") or "")
            self.set_cell(row, 1, "等待")
            self.set_cell(row, 2, "")
            self.set_cell(row, 3, "")
            self._apply_task_to_row(row, task)
        self._refresh_queue_count()
        self._update_progress_bar()
        self.update_window_title()

    def _save_queue_state(self):
        pending = [
            task
            for task in self.queue_tasks
            if task.get("status") not in (
                "完成",
                "完成（公开视频兜底）",
                "已删除",
                "已跳过",
            )
        ]
        ok, err = save_queue(pending)
        if not ok:
            self.append_log(f"队列保存失败: {err}")

    def restore_queue(self):
        records = load_queue()
        restored = []
        for record in records:
            url = normalize_input(str(record.get("url") or ""))
            if not url:
                continue
            status = str(record.get("status") or "等待")
            if status in (
                "完成",
                "完成（公开视频兜底）",
                "已删除",
                "已跳过",
            ):
                continue
            if status in ("下载中", "已暂停"):
                status = "等待"
            progress = float(record.get("progress") or 0)
            if status == "等待":
                progress = 0.0
            settings = dict(DEFAULT_SETTINGS)
            if isinstance(record.get("settings"), dict):
                settings.update(record["settings"])
            error = str(record.get("error") or "")
            if status == "失败" and not error:
                error = "上次下载失败"
            task = {
                "url": url,
                "settings": settings,
                "status": status,
                "progress": progress,
                "detail": str(record.get("detail") or ""),
                "output": str(record.get("output") or ""),
                "error": error,
                "format_full": str(record.get("format_full") or ""),
                "quality_label": str(
                    record.get("quality_label")
                    or self._quality_text(settings.get("quality", "best"))
                ),
                "created_at": int(record.get("created_at") or time.time()),
            }
            if not task["format_full"]:
                task["format_full"], task["quality_label"] = self._task_labels(
                    settings
                )
            restored.append(task)
        if not restored:
            return
        self._populate_queue_table(restored)
        self._task_progress.clear()
        self.append_log(f"已恢复 {len(restored)} 个队列任务")
        self.statusBar().showMessage(f"已恢复 {len(restored)} 个队列任务", 5000)
        self._save_queue_state()

    def _connect_worker(self, worker):
        worker.item_started.connect(self.on_item_started)
        worker.item_progress.connect(self.on_item_progress)
        worker.item_finished.connect(self.on_item_finished)
        worker.item_failed.connect(self.on_item_failed)
        worker.item_skipped.connect(self.on_item_skipped)
        worker.item_cancelled.connect(self.on_item_cancelled)
        worker.item_history.connect(self.on_item_history)
        worker.log.connect(self.append_log)
        worker.all_done.connect(self.on_all_done)
        worker.paused_changed.connect(self.on_queue_paused_changed)
        worker.finished.connect(self._on_download_thread_finished)

    def _start_worker(self, active_indices):
        if self.worker is not None:
            QMessageBox.warning(self, "提示", "当前已有下载任务在进行。")
            return False
        indices = sorted({i for i in active_indices if self._task_for_index(i)})
        if not indices:
            QMessageBox.information(self, "提示", "当前没有待下载任务。")
            return False
        for index in indices:
            task = self.queue_tasks[index]
            download_dir = (
                task["settings"].get("download_dir") or DEFAULT_DOWNLOAD_DIR
            )
            try:
                Path(download_dir).mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                QMessageBox.warning(
                    self,
                    "目录创建失败",
                    f"无法创建下载目录：\n{download_dir}\n\n{exc}",
                )
                return False
        task_settings = [task["settings"] for task in self.queue_tasks]
        self._task_progress.clear()
        for index in indices:
            task = self.queue_tasks[index]
            task["status"] = "等待"
            task["progress"] = 0.0
            task["detail"] = "等待开始"
            task["output"] = ""
            task["error"] = ""
            self._apply_task_to_row(index, task)
        self._refresh_queue_count()
        self._update_progress_bar()
        self.start_btn.setEnabled(False)
        self.hero_start_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.pause_btn.setText("暂停")
        self.cancel_btn.setEnabled(True)
        self.set_controls_enabled(False)
        self.statusBar().showMessage("下载中...")
        self.settings = self.collect_settings()
        self.worker = DownloadWorker(
            [task["url"] for task in self.queue_tasks],
            self.settings,
            self,
            task_settings=task_settings,
            active_indices=indices,
        )
        self._connect_worker(self.worker)
        self._save_queue_state()
        self.worker.start()
        return True

    def _pending_queue_indices(self):
        return [
            index
            for index, task in enumerate(self.queue_tasks)
            if task.get("status") in ("等待", "失败", "已取消")
        ]

    def start_downloads(self):
        # 不能只看 isRunning()：刚 start() 的瞬间线程可能还没起来，双击会启动两个 worker
        if self.worker is not None:
            QMessageBox.warning(self, "提示", "当前已有下载任务在进行。")
            return
        if self.sound_player:
            self.sound_player.play("start")
        text = self.input_edit.toPlainText()
        raw_urls = split_inputs(text)
        urls = self._dedupe_input_urls(raw_urls)
        if len(urls) < len(raw_urls):
            self.append_log(f"已忽略 {len(raw_urls) - len(urls)} 个重复链接")

        if urls:
            same_as_queue = (
                bool(self.queue_tasks)
                and text.strip() == self._queue_source_text.strip()
                and bool(self._pending_queue_indices())
            )
            if same_as_queue:
                pending = self._pending_queue_indices()
                self.append_log(f"继续队列中的 {len(pending)} 个任务")
                self._start_worker(pending)
                return
            existing_pending = self._pending_queue_indices()
            if existing_pending:
                ret = QMessageBox.question(
                    self,
                    "替换当前队列",
                    f"当前队列还有 {len(existing_pending)} 个待处理任务。\n"
                    "使用输入框的新链接会替换当前队列，是否继续？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if ret != QMessageBox.Yes:
                    return
            for url in urls:
                if (
                    detect_site(url) in COOKIE_REQUIRED_SITES
                    and not has_cookie_for_url(url, self.collect_settings())
                ):
                    if not self._open_cookie_login(url):
                        return
            self.settings = self.collect_settings()
            custom_format = (self.settings.get("custom_format") or "").strip()
            fixed_format = self._ensure_audio_track(custom_format)
            if fixed_format != custom_format:
                self.custom_format_edit.setText(fixed_format)
                self.settings["custom_format"] = fixed_format
                self.refresh_selected_format_label()
                self.append_log(f"所选格式是纯视频流，已自动补上音频流 → {fixed_format}")
            self.settings = self.collect_settings()
            ok, err = save_settings(self.settings)
            if not ok:
                self.statusBar().showMessage(f"设置保存失败: {err}", 5000)
            cookie_err = self.check_cookie_settings()
            if cookie_err:
                ret = QMessageBox.question(
                    self, "Cookie 提示",
                    f"{cookie_err}\n\n是否仍然继续下载？（公开视频可走兜底接口）",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
                )
                if ret != QMessageBox.Yes:
                    return
            base_settings = dict(self.settings)
            tasks = [
                self._task_from_url(url, base_settings)
                for url in urls
            ]
            self._queue_source_text = text.strip()
            self._populate_queue_table(tasks)
            self._save_queue_state()
            labels = sorted({task.get("format_full") or "-" for task in tasks})
            self.append_log(
                f"开始下载 {len(tasks)} 个任务 · 格式: {' / '.join(labels)}"
            )
            self._start_worker(list(range(len(tasks))))
            return

        pending = self._pending_queue_indices()
        if not pending:
            QMessageBox.information(
                self, "提示", "请输入要下载的链接，或先往队列里添加任务。"
            )
            return
        self.append_log(f"继续队列中的 {len(pending)} 个任务")
        self._start_worker(pending)

    def _on_download_thread_finished(self):
        """线程真正结束后才释放引用，避免 QThread 还在运行时被销毁。"""
        if self.sender() is self.worker:
            self.worker = None

    def set_controls_enabled(self, enabled):
        self.input_edit.setEnabled(enabled)
        self.preview_btn.setEnabled(enabled)
        self.use_format_btn.setEnabled(enabled and bool(self.formats_table.selectionModel().selectedRows()))
        self.clear_format_btn.setEnabled(enabled)
        self.add_pages_btn.setEnabled(enabled and self.pages_table.isVisible())
        self.select_all_pages_btn.setEnabled(enabled and self.pages_table.isVisible())
        self.dir_btn.setEnabled(enabled)
        self.cookie_mode_combo.setEnabled(enabled)
        self.cookie_file_btn.setEnabled(enabled)
        self.auto_browser_login_check.setEnabled(enabled)
        self.quality_combo.setEnabled(enabled)
        self.codec_combo.setEnabled(enabled)
        self.audio_quality_combo.setEnabled(enabled)
        self.thread_combo.setEnabled(enabled)
        self.concurrent_spin.setEnabled(enabled)
        self.speed_limit_spin.setEnabled(enabled)
        self.proxy_edit.setEnabled(enabled)
        self.proxy_test_btn.setEnabled(
            enabled
            and not (
                self.proxy_test_worker is not None
                and self.proxy_test_worker.isRunning()
            )
        )
        self.manage_cookie_store_btn.setEnabled(enabled)
        self.template_edit.setEnabled(enabled)
        self.thumbnail_check.setEnabled(enabled)
        self.subtitle_check.setEnabled(enabled)
        self.danmaku_check.setEnabled(enabled)
        self.qr_login_btn.setEnabled(enabled)
        self.check_cookie_btn.setEnabled(enabled)
        self.side_dir_btn.setEnabled(enabled)
        self.quick_subtitle_switch.setEnabled(enabled)
        self.quick_thumbnail_switch.setEnabled(enabled)
        self.quick_danmaku_switch.setEnabled(enabled)
        self.import_links_btn.setEnabled(enabled)
        if hasattr(self, "douyin_login_btn"):
            self.douyin_login_btn.setEnabled(enabled)
        if hasattr(self, "any_cookie_btn"):
            self.any_cookie_btn.setEnabled(enabled)
        for card in self.quality_cards.values():
            card.setEnabled(enabled and not self._quality_locked)
        for card in self.format_cards.values():
            card.setEnabled(enabled)

    def set_cell(self, row, col, text):
        item = self.table.item(row, col)
        if item:
            item.setText(text)
        else:
            self.table.setItem(row, col, QTableWidgetItem(text))

    def _install_queue_visuals(self, row, status="等待"):
        status_cell = StatusCell(status)
        self.table.setCellWidget(row, 1, status_cell)
        progress = QueueProgress()
        progress.set_value(0, "等待开始")
        self.table.setCellWidget(row, 2, progress)
        # 这两列由自定义控件绘制，底层 item 文字必须透明，否则会重影
        for column in (1, 2):
            item = self.table.item(row, column)
            if item is not None:
                item.setForeground(QColor(0, 0, 0, 0))

    def _set_row_status(self, row, status):
        cell = self.table.cellWidget(row, 1)
        if isinstance(cell, StatusCell):
            cell.set_status(status)

    def _set_row_progress(self, row, percent, detail):
        cell = self.table.cellWidget(row, 2)
        if isinstance(cell, QueueProgress):
            cell.set_value(percent, detail)

    def on_item_started(self, index, url):
        if index >= self.table.rowCount():
            return
        task = self._task_for_index(index)
        if task is not None:
            task.update(
                status="下载中",
                progress=0.0,
                detail="准备中...",
            )
            self._save_queue_state()
        self._task_progress[index] = 0.0
        self.set_cell(index, 0, url)
        item = self.table.item(index, 0)
        if item is not None and item.icon().isNull():
            item.setIcon(make_media_tile())
        self.set_cell(index, 1, "下载中")
        self.set_cell(index, 2, "0%")
        self._set_row_status(index, "下载中")
        self._set_row_progress(index, 0, "准备中...")
        self._refresh_queue_count()
        self.statusBar().showMessage(f"下载中: {url}")
        self.update_window_title()

    QUEUE_FINISHED_STATES = ("完成", "完成（公开视频兜底）", "失败", "已取消")
    QUEUE_EXCLUDED_STATES = ("已删除", "已跳过")

    def _queue_counts(self):
        """返回 (参与统计的任务数, 已结束任务数)。

        删除/跳过的任务不再计入分母，否则进度条永远到不了 100%。
        """
        total = done = 0
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            state = item.text() if item else "等待"
            if state in self.QUEUE_EXCLUDED_STATES:
                continue
            total += 1
            if state in self.QUEUE_FINISHED_STATES:
                done += 1
        return total, done

    def _overall_progress(self):
        """所有运行中任务的进度之和，避免并发下载时进度条只跟最后一个任务走。"""
        total, done = self._queue_counts()
        if total <= 0:
            return 0
        active = sum(
            max(0.0, min(100.0, float(percent)))
            for percent in self._task_progress.values()
        ) / 100.0
        return int(min(100.0, (done + active) / total * 100))

    def on_item_progress(self, index, percent, detail):
        if index >= self.table.rowCount():
            return
        task = self._task_for_index(index)
        if task is not None:
            task["detail"] = detail
            if percent >= 0:
                task["progress"] = float(percent)
        self.set_cell(index, 2, detail)
        self._set_row_progress(index, percent, detail)
        if percent >= 0:
            self._task_progress[index] = max(0.0, min(100.0, float(percent)))
            self.progress_bar.setValue(self._overall_progress())
            self.update_window_title()

    def on_item_finished(self, index, output_detail, status_text):
        if index >= self.table.rowCount():
            return
        if self.sound_player and (status_text or "完成").startswith("完成"):
            self.sound_player.play("success2")
        self._task_progress.pop(index, None)
        status = status_text or "完成"
        task = self._task_for_index(index)
        if task is not None:
            task.update(
                status=status,
                progress=100.0,
                detail="已完成",
                output=output_detail,
                error="",
            )
            self._save_queue_state()
        self.set_cell(index, 1, status)
        self.set_cell(index, 2, "100%")
        self.set_cell(index, 3, output_detail)
        self._set_row_status(index, status)
        self._set_row_progress(index, 100, "已完成")
        self._update_progress_bar()
        self.update_window_title()

    def on_item_failed(self, index, error):
        if index >= self.table.rowCount():
            return
        if self.sound_player:
            self.sound_player.play("fail")
        self._task_progress.pop(index, None)
        task = self._task_for_index(index)
        if task is not None:
            task.update(
                status="失败",
                progress=0.0,
                detail=error,
                error=error,
            )
            self._save_queue_state()
        self.set_cell(index, 1, "失败")
        self.set_cell(index, 2, error)
        self._set_row_status(index, "失败")
        self._set_row_progress(index, -1, error)
        self._update_progress_bar()
        self.update_window_title()

    def on_item_skipped(self, index, reason):
        """任务被用户删除或链接为空：保留已删除状态，不当作失败。"""
        if index >= self.table.rowCount():
            return
        self._task_progress.pop(index, None)
        item = self.table.item(index, 1)
        current = item.text() if item else "等待"
        status = "已删除" if "删除" in (reason or "") else "已跳过"
        if current in self.QUEUE_EXCLUDED_STATES:
            status = current
        task = self._task_for_index(index)
        if task is not None:
            task.update(status=status, detail=reason or status)
            self._save_queue_state()
        self.set_cell(index, 1, status)
        self._set_row_status(index, status)
        self.set_cell(index, 2, reason or "已跳过")
        self._set_row_progress(index, -1, reason or "已跳过")
        self._update_progress_bar()
        self.update_window_title()

    def on_item_cancelled(self, index):
        """取消时把未开始/正在处理的任务明确标成已取消，而不是失败。"""
        if index >= self.table.rowCount():
            return
        self._task_progress.pop(index, None)
        item = self.table.item(index, 1)
        current = item.text() if item else "等待"
        if current not in self.QUEUE_FINISHED_STATES and current not in self.QUEUE_EXCLUDED_STATES:
            self.set_cell(index, 1, "已取消")
            self.set_cell(index, 2, "已取消")
            self._set_row_status(index, "已取消")
            self._set_row_progress(index, -1, "已取消")
            task = self._task_for_index(index)
            if task is not None:
                task.update(status="已取消", detail="已取消")
                self._save_queue_state()
        self._update_progress_bar()
        self.update_window_title()

    def _update_progress_bar(self):
        self.progress_bar.setValue(self._overall_progress())

    def update_window_title(self):
        """根据当前下载状态更新任务栏窗口标题。"""
        total, done = self._queue_counts()
        if not total:
            title = self.base_window_title
            self.setWindowTitle(title)
            self._update_mascot_by_state("idle")
            return
        running = self.worker and self.worker.isRunning() and not self.worker.cancelled
        if running:
            if self.worker.paused:
                title = f"{self.base_window_title} - 已暂停 ({done}/{total})"
                self._update_mascot_by_state("paused")
            else:
                title = f"{self.base_window_title} - 下载中 ({done}/{total})"
                self._update_mascot_by_state("downloading")
            self.setWindowTitle(title)
            return
        failed = sum(1 for r in range(self.table.rowCount()) if self.table.item(r, 1) and self.table.item(r, 1).text() == "失败")
        cancelled = sum(1 for r in range(self.table.rowCount()) if self.table.item(r, 1) and self.table.item(r, 1).text() == "已取消")
        waiting = sum(1 for r in range(self.table.rowCount()) if self.table.item(r, 1) and self.table.item(r, 1).text() == "等待")
        if waiting:
            title = f"{self.base_window_title} - 待下载 ({done}/{total})"
            self._update_mascot_by_state("idle")
        elif cancelled:
            title = f"{self.base_window_title} - 已取消"
            self._update_mascot_by_state("cancelled")
        elif failed:
            title = f"{self.base_window_title} - 完成（有失败）"
            self._update_mascot_by_state("failed")
        else:
            title = f"{self.base_window_title} - 全部完成"
            self._update_mascot_by_state("completed")
        self.setWindowTitle(title)

    def _update_mascot_by_state(self, state):
        """根据状态切换看板娘气泡台词。

        只有状态真正变化时才换台词：进度回调每秒会调用多次，
        否则气泡文字会持续随机跳动。
        """
        if not hasattr(self, "mascot_bubble"):
            return
        if self._mascot_state == state:
            return
        self._mascot_state = state
        lines = {
            "idle": ["粘贴链接，开始下载", "支持 B站、yt-dlp 源", "准备就绪"],
            "downloading": ["下载进行中...", "稍等片刻", "正在处理"],
            "paused": ["已暂停", "随时可以继续", "休息一下"],
            "completed": ["全部完成", "可以去查看了", "下载结束"],
            "failed": ["有任务失败", "检查链接或 Cookie", "可尝试重试"],
            "cancelled": ["已取消", "欢迎再次使用", "操作已中止"],
        }
        self.mascot_bubble.setText(random.choice(lines.get(state, lines["idle"])))

    def on_item_history(self, record):
        append_history_record(record)
        self.history_records = load_history()
        # 批量下载时不要每个任务都重建整张历史表（否则是 O(n²) 的刷新开销），
        # 先标记为待刷新，等全部结束或切到历史页时再刷新一次。
        self._history_dirty = True
        if self.content_stack.currentIndex() == 1:
            self.refresh_history()

    def on_all_done(self, ok):
        if self.sound_player:
            self.sound_player.play("complete" if ok else "fail")
        if self._history_dirty:
            self.refresh_history()
        self._task_progress.clear()
        self._save_queue_state()
        self._update_progress_bar()
        self.start_btn.setEnabled(True)
        self.hero_start_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.pause_btn.setText("暂停")
        self.set_controls_enabled(True)
        self.update_window_title()
        remaining_total, _ = self._queue_counts()
        if remaining_total == 0:
            self.statusBar().showMessage("队列任务已全部删除", 5000)
            return
        failed_rows = [
            r for r in range(self.table.rowCount())
            if self.table.item(r, 1) and self.table.item(r, 1).text() == "失败"
        ]
        cancelled_rows = [
            r for r in range(self.table.rowCount())
            if self.table.item(r, 1) and self.table.item(r, 1).text() == "已取消"
        ]
        waiting_rows = [
            r for r in range(self.table.rowCount())
            if self.table.item(r, 1) and self.table.item(r, 1).text() == "等待"
        ]
        all_finished = not failed_rows and not cancelled_rows and not waiting_rows
        if ok and all_finished:
            self.progress_bar.setValue(100)
            self.statusBar().showMessage("全部任务完成")
            self.show_tray_message("下载完成", "全部任务已完成")
            output_dir = ""
            for r in range(self.table.rowCount()):
                detail = self.table.item(r, 3)
                if detail and detail.text():
                    p = Path(detail.text())
                    if p.exists():
                        output_dir = str(p.parent)
                        break
            if not output_dir:
                output_dir = self.settings.get("download_dir") or DEFAULT_DOWNLOAD_DIR
            reply = QMessageBox.question(
                self, "下载完成",
                f"全部任务已完成！\n下载目录：{output_dir}\n\n是否打开下载目录？",
                QMessageBox.Open | QMessageBox.No,
                QMessageBox.Open,
            )
            if reply == QMessageBox.Open:
                open_path_in_explorer(output_dir)
            return
        parts = []
        if failed_rows:
            parts.append(f"失败 {len(failed_rows)} 个")
        if cancelled_rows:
            parts.append(f"取消 {len(cancelled_rows)} 个")
        if waiting_rows:
            parts.append(f"待下载 {len(waiting_rows)} 个")
        msg = "本次任务结束"
        if parts:
            msg += "：" + "，".join(parts)
        self.statusBar().showMessage(msg, 6000)
        self.show_tray_message("下载结束", msg)

    def show_tray_message(self, title, message):
        if self.tray_icon:
            try:
                self.tray_icon.showMessage(title, message, QSystemTrayIcon.Information, 3000)
            except Exception:
                pass

    def cancel_downloads(self):
        if self.sound_player:
            self.sound_player.play("click")
        if self.worker and self.worker.isRunning():
            self.statusBar().showMessage("正在取消...")
            self.worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.update_window_title()

    def toggle_pause_queue(self):
        if self.sound_player:
            self.sound_player.play("click")
        if not self.worker:
            return
        if self.worker.paused:
            self.worker.resume()
            self.pause_btn.setText("暂停")
            self.statusBar().showMessage("下载中...")
        else:
            self.worker.pause()
            self.pause_btn.setText("继续")
            self.statusBar().showMessage("队列已暂停")
        self.update_window_title()

    def on_queue_paused_changed(self, paused):
        if paused:
            self.pause_btn.setText("继续")
        else:
            self.pause_btn.setText("暂停")
        self.update_window_title()

    def append_log(self, msg):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self.log_edit.appendPlainText(line)
        write_runtime_log(msg)

    def _toggle_log(self, checked):
        self.log_edit.setVisible(checked)
        self.clear_log_btn.setVisible(checked)

    def log_edit_clear(self):
        self.log_edit.clear()

    # ---------- 任务表右键 ----------

    def show_task_context_menu(self, pos):
        row = self.table.indexAt(pos).row()
        if row < 0:
            menu = QMenu(self.table)
            act_clear = menu.addAction("清理已完成/已取消任务")
            act_retry_failed = menu.addAction("重试全部失败任务")
            act_clear.setEnabled(
                self.worker is None
                and any(
                    task.get("status")
                    in ("完成", "完成（公开视频兜底）", "已取消", "已删除", "已跳过")
                    for task in self.queue_tasks
                )
            )
            act_retry_failed.setEnabled(
                self.worker is None
                and any(task.get("status") == "失败" for task in self.queue_tasks)
            )
            action = menu.exec_(self.table.viewport().mapToGlobal(pos))
            if action == act_clear:
                self.clear_finished_queue_tasks()
            elif action == act_retry_failed:
                self.retry_all_failed_tasks()
            return
        menu = QMenu(self.table)
        act_play = menu.addAction("在软件内播放")
        act_open = menu.addAction("打开文件")
        act_dir = menu.addAction("打开目录")
        menu.addSeparator()
        act_edit = menu.addAction("修改任务设置…")
        act_up = menu.addAction("上移")
        act_down = menu.addAction("下移")
        can_edit = self._can_edit_queue_task(row)
        busy = self.worker is not None
        act_edit.setEnabled(can_edit)
        act_up.setEnabled(not busy and row > 0)
        act_down.setEnabled(not busy and row < self.table.rowCount() - 1)
        menu.addSeparator()
        act_copy_err = menu.addAction("复制错误信息")
        act_copy_cell = menu.addAction("复制单元格")
        act_copy_output = menu.addAction("复制输出路径")
        menu.addSeparator()
        act_retry = menu.addAction("重试")
        act_remove = menu.addAction("从队列删除")
        action = menu.exec_(self.table.viewport().mapToGlobal(pos))
        if action == act_play:
            self.play_task_media(row)
        elif action == act_open:
            self.open_task_file(row)
        elif action == act_dir:
            self.open_task_dir(row)
        elif action == act_edit:
            self.edit_task_settings(row)
        elif action == act_up:
            self.move_task(row, -1)
        elif action == act_down:
            self.move_task(row, 1)
        elif action == act_copy_err:
            self.copy_task_error(row)
        elif action == act_copy_cell:
            self.copy_cell(row, pos)
        elif action == act_copy_output:
            self.copy_task_output(row)
        elif action == act_retry:
            self.retry_task(row)
        elif action == act_remove:
            self.remove_waiting_task(row)

    def on_task_double_clicked(self, index):
        self.open_task_file(index.row())

    def task_output_path(self, row):
        if row < 0 or row >= self.table.rowCount():
            return ""
        item = self.table.item(row, 3)
        return item.text().strip() if item else ""

    @staticmethod
    def _first_existing_media(text):
        """输出详情可能包含多条路径（封面/字幕），取第一条存在的媒体文件。"""
        for line in str(text or "").splitlines():
            path = line.strip()
            if path and Path(path).is_file():
                return path
        return ""

    def play_media_file(self, path, title=""):
        """优先用内置 mpv 播放；没有 mpv 时询问是否交给系统播放器。"""
        path = str(path or "").strip()
        if not path:
            QMessageBox.information(self, "提示", "该任务没有可播放的文件路径。")
            return
        if not Path(path).is_file():
            QMessageBox.information(self, "提示", "文件不存在，可能已被移动或删除。")
            return
        from .preview_player import LocalMediaDialog, find_mpv_executable

        if find_mpv_executable():
            dialog = LocalMediaDialog(self, path, title)
            dialog.exec_()
            return
        ret = QMessageBox.question(
            self,
            "内置播放不可用",
            "未检测到 mpv.exe，无法在软件内播放。\n"
            "可以把 mpv.exe 放到程序目录后重试，或现在用系统默认播放器打开？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if ret == QMessageBox.Yes:
            open_file_default(path)

    def play_task_media(self, row):
        if row < 0 or row >= self.table.rowCount():
            return
        path = self._first_existing_media(self.task_output_path(row))
        task = self._task_for_index(row)
        title = ""
        if task is not None:
            title = Path(task.get("output") or "").stem or task.get("url") or ""
        self.play_media_file(path, title)

    def open_task_file(self, row):
        path = self.task_output_path(row)
        if not path:
            QMessageBox.information(self, "提示", "该任务没有输出文件路径。")
            return
        if not Path(path).exists():
            QMessageBox.information(self, "提示", "文件不存在，可能已被移动或删除。")
            return
        open_file_default(path)

    def open_task_dir(self, row):
        path = self.task_output_path(row)
        if not path:
            path = self.dir_edit.text() or str(DEFAULT_DOWNLOAD_DIR)
        open_path_in_explorer(path)

    def copy_task_error(self, row):
        if row < 0 or row >= self.table.rowCount():
            return
        item = self.table.item(row, 2)
        text = item.text() if item else ""
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage("已复制错误信息", 3000)

    def copy_cell(self, row, pos):
        item = self.table.itemAt(pos)
        if item:
            QApplication.clipboard().setText(item.text())
            self.statusBar().showMessage("已复制单元格内容", 3000)

    def copy_task_output(self, row):
        path = self.task_output_path(row)
        if path:
            QApplication.clipboard().setText(path)
            self.statusBar().showMessage("已复制输出路径", 3000)

    def _can_edit_queue_task(self, row):
        if self.worker is not None:
            return False
        task = self._task_for_index(row)
        return bool(task and task.get("status") in ("等待", "失败", "已取消"))

    def edit_task_settings(self, row):
        if not self._can_edit_queue_task(row):
            QMessageBox.information(
                self, "提示", "只有等待、失败或已取消的任务可以修改设置，且不能在下载中修改。"
            )
            return
        task = self._task_for_index(row)
        from .task_settings_dialog import TaskSettingsDialog

        dialog = TaskSettingsDialog(task, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        task["settings"] = dialog.result_settings()
        task["format_full"], task["quality_label"] = self._task_labels(
            task["settings"]
        )
        self._apply_task_to_row(row, task)
        self._save_queue_state()
        self.statusBar().showMessage(
            f"已更新任务设置：{task.get('quality_label') or '-'}", 4000
        )

    def move_task(self, row, delta):
        """调整队列顺序；只在没有下载线程时允许，避免 worker 索引错位。"""
        if self.worker is not None:
            QMessageBox.information(
                self, "提示", "下载过程中不能调整队列顺序，请先等待完成或取消。"
            )
            return
        target = row + delta
        if row < 0 or target < 0 or target >= len(self.queue_tasks):
            return
        self.queue_tasks[row], self.queue_tasks[target] = (
            self.queue_tasks[target],
            self.queue_tasks[row],
        )
        self._task_progress.clear()
        self._populate_queue_table(self.queue_tasks)
        self._save_queue_state()
        self.table.selectRow(target)
        self.statusBar().showMessage("已调整队列顺序", 3000)

    def retry_task(self, row):
        if row < 0 or row >= self.table.rowCount():
            return
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "提示", "当前还有下载任务在进行，请等待完成或取消后再重试。")
            return
        task = self._task_for_index(row)
        if task is None:
            QMessageBox.information(self, "提示", "该任务缺少可重试的设置信息，请重新添加链接。")
            return
        self.switch_page(0)
        self.append_log(f"重试任务: {task.get('url') or ''}")
        self._start_worker([row])

    def remove_waiting_task(self, row):
        if row < 0 or row >= self.table.rowCount():
            return
        status_item = self.table.item(row, 1)
        status_text = status_item.text() if status_item else ""
        if status_text == "下载中":
            QMessageBox.information(self, "提示", "正在下载的任务不能直接从队列删除，请先取消。")
            return
        if status_text in self.QUEUE_EXCLUDED_STATES:
            return
        task = self._task_for_index(row)
        if self.worker and status_text == "等待":
            self.worker.skip_index(row)
        self._task_progress.pop(row, None)
        if task is not None:
            task.update(status="已删除", detail="已从队列删除")
            self._save_queue_state()
        self.set_cell(row, 1, "已删除")
        self.set_cell(row, 2, "-")
        self._set_row_status(row, "已删除")
        self._set_row_progress(row, -1, "已从队列删除")
        self._update_progress_bar()
        self.update_window_title()
        self.statusBar().showMessage("已从队列删除", 3000)

    def clear_finished_queue_tasks(self):
        """从队列移除已完成/已取消/已删除/已跳过的行；失败任务保留以便重试。"""
        if self.worker is not None:
            QMessageBox.information(self, "提示", "下载过程中不能清理队列。")
            return
        finished = ("完成", "完成（公开视频兜底）", "已取消", "已删除", "已跳过")
        kept = [
            task for task in self.queue_tasks if task.get("status") not in finished
        ]
        removed = len(self.queue_tasks) - len(kept)
        if removed <= 0:
            return
        self.queue_tasks = kept
        self._task_progress.clear()
        self._populate_queue_table(self.queue_tasks)
        self._save_queue_state()
        self.statusBar().showMessage(f"已清理 {removed} 个已结束任务", 4000)

    def retry_all_failed_tasks(self):
        if self.worker is not None:
            QMessageBox.information(self, "提示", "当前已有下载任务在进行。")
            return
        rows = [
            index
            for index, task in enumerate(self.queue_tasks)
            if task.get("status") == "失败"
        ]
        if not rows:
            QMessageBox.information(self, "提示", "当前没有失败任务。")
            return
        self.switch_page(0)
        self.append_log(f"批量重试 {len(rows)} 个失败任务")
        self._start_worker(rows)

    # ---------- 历史 ----------

    def refresh_history(self):
        self.history_records = load_history()
        self._populate_history_table()
        self._history_dirty = False
        total = len(self.history_records)
        self.statusBar().showMessage(f"历史记录: {total} 条")

    def _history_filtered_pairs(self):
        """返回 [(原始索引, 记录)]，供筛选后的表格和操作使用。"""
        query = ""
        status_filter = "all"
        if hasattr(self, "history_search_edit"):
            query = self.history_search_edit.text().strip().lower()
        if hasattr(self, "history_status_combo"):
            status_filter = self.history_status_combo.currentData() or "all"
        pairs = []
        for index, record in enumerate(self.history_records):
            status = str(record.get("status") or "")
            if status_filter == "missing":
                path = record.get("output_path") or ""
                if not path or Path(path).exists():
                    continue
            elif status_filter != "all" and status != status_filter:
                continue
            if query:
                haystack = " ".join(
                    str(record.get(key) or "")
                    for key in (
                        "title",
                        "url",
                        "output_path",
                        "resolution",
                        "error",
                    )
                ).lower()
                if query not in haystack:
                    continue
            pairs.append((index, record))
        return pairs

    def _apply_history_filter(self):
        if not hasattr(self, "history_table"):
            return
        if self._history_dirty:
            self.history_records = load_history()
            self._history_dirty = False
        self._populate_history_table()

    def _populate_history_table(self):
        pairs = self._history_filtered_pairs()
        # 填充期间必须关闭排序，否则 QTableWidget 会在插入过程中重排行序导致数据错位
        self.history_table.setSortingEnabled(False)
        self.history_table.setRowCount(0)
        for record_index, r in pairs:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            title_item = QTableWidgetItem(r.get("title") or "-")
            # 排序后视觉行号会变化，操作历史记录必须依赖这里保存的原始索引
            title_item.setData(Qt.UserRole, record_index)
            self.history_table.setItem(row, 0, title_item)
            status = r.get("status") or "-"
            if status == "completed":
                status = "完成"
            elif status == "failed":
                status = "失败"
            elif status == "cancelled":
                status = "已取消"
            if r.get("warning"):
                status = f"{status} · {r['warning']}"
            self.history_table.setItem(row, 1, QTableWidgetItem(status))
            self.history_table.setItem(row, 2, QTableWidgetItem(format_bytes(r.get("file_size"))))
            self.history_table.setItem(row, 3, QTableWidgetItem(str(r.get("duration") or "-")))
            self.history_table.setItem(row, 4, QTableWidgetItem(r.get("resolution") or "-"))
            ts = r.get("finished_at")
            time_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "-"
            self.history_table.setItem(row, 5, QTableWidgetItem(time_str))
            path = r.get("output_path") or ""
            if path and not Path(path).exists():
                path = f"{path}  (文件已移动或删除)"
            self.history_table.setItem(row, 6, QTableWidgetItem(path))
        self.history_table.setSortingEnabled(True)
        total = len(self.history_records)
        shown = len(pairs)
        if hasattr(self, "history_match_label"):
            self.history_match_label.setText(
                f"显示 {shown} / {total}" if shown != total else f"共 {total} 条"
            )

    def history_status_text(self, status):
        mapping = {"completed": "完成", "failed": "失败", "cancelled": "已取消"}
        return mapping.get(status, status or "-")

    def _history_record_index_for_row(self, visual_row):
        """把排序后的表格行号还原成 history_records 里的原始索引。"""
        if visual_row < 0:
            return -1
        item = self.history_table.item(visual_row, 0)
        if item is None:
            return -1
        index = item.data(Qt.UserRole)
        if isinstance(index, int) and 0 <= index < len(self.history_records):
            return index
        return -1

    def current_history_record(self):
        visual_row = self.history_table.currentRow()
        index = self._history_record_index_for_row(visual_row)
        if index < 0:
            return None, -1
        return self.history_records[index], index

    def copy_history_link(self, row=None):
        if row is None:
            row = self.history_table.currentRow()
        index = self._history_record_index_for_row(row)
        if index < 0:
            return
        url = self.history_records[index].get("url") or ""
        if url:
            QApplication.clipboard().setText(url)
            self.statusBar().showMessage("已复制链接", 3000)

    def redownload_history(self):
        record, _ = self.current_history_record()
        if not record:
            QMessageBox.information(self, "提示", "请先在历史列表中选择一条记录。")
            return
        url = record.get("url") or ""
        if not url:
            QMessageBox.warning(self, "提示", "该历史记录没有可用的链接。")
            return
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "提示", "当前还有下载任务在进行，请等待完成或取消后再重试。")
            return
        self.input_edit.setPlainText(url)
        self.switch_page(0)
        self.start_downloads()

    def open_history_file(self):
        record, _ = self.current_history_record()
        if not record:
            QMessageBox.information(self, "提示", "请先在历史列表中选择一条记录。")
            return
        path = record.get("output_path") or ""
        if not path:
            QMessageBox.information(self, "提示", "该记录没有输出文件路径。")
            return
        if not Path(path).exists():
            QMessageBox.information(self, "提示", "文件不存在，可能已被移动或删除。")
            return
        open_file_default(path)

    def play_history_media(self):
        record, _ = self.current_history_record()
        if not record:
            QMessageBox.information(self, "提示", "请先在历史列表中选择一条记录。")
            return
        self.play_media_file(
            record.get("output_path") or "",
            record.get("title") or "",
        )

    def open_history_dir(self):
        record, _ = self.current_history_record()
        if not record:
            path = self.dir_edit.text() or str(DEFAULT_DOWNLOAD_DIR)
        else:
            path = record.get("output_path") or self.dir_edit.text() or str(DEFAULT_DOWNLOAD_DIR)
        open_path_in_explorer(path)

    def delete_history(self):
        _, row = self.current_history_record()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择要删除的记录。")
            return
        ret = QMessageBox.question(
            self, "删除记录",
            "确定删除这条历史记录吗？（不会删除本地文件）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret == QMessageBox.Yes:
            remove_history_record(row)
            self.refresh_history()

    def clear_all_history(self):
        ret = QMessageBox.question(
            self, "清空历史",
            "确定清空全部历史记录吗？（不会删除本地文件）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret == QMessageBox.Yes:
            save_history([])
            self.refresh_history()

    def export_history(self, fmt):
        """导出历史记录到 CSV 或 JSON 文件。"""
        records = self.history_records or load_history()
        if not records:
            QMessageBox.information(self, "导出", "当前没有历史记录可导出。")
            return
        if fmt == "csv":
            default_name = "bilibili_history.csv"
            filter_str = "CSV 文件 (*.csv);;所有文件 (*)"
        else:
            default_name = "bilibili_history.json"
            filter_str = "JSON 文件 (*.json);;所有文件 (*)"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出历史记录", default_name, filter_str
        )
        if not path:
            return
        try:
            if fmt == "csv":
                fields = ["title", "url", "status", "file_size", "duration",
                          "resolution", "output_path", "error", "created_at", "finished_at"]
                with open(path, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                    writer.writeheader()
                    for r in records:
                        writer.writerow(r)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(records, f, ensure_ascii=False, indent=2)
            self.statusBar().showMessage(f"已导出 {len(records)} 条记录到 {path}", 5000)
            self.append_log(f"导出历史记录 ({fmt}): {path} ({len(records)} 条)")
            QMessageBox.information(self, "导出成功", f"已导出 {len(records)} 条记录到\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", f"导出失败：\n{format_error(exc)}")

    def export_settings(self):
        """导出当前设置到 JSON 文件。"""
        path, _ = QFileDialog.getSaveFileName(
            self, "导出设置", "bilibili_settings.json", "JSON 文件 (*.json);;所有文件 (*)"
        )
        if not path:
            return
        try:
            settings = self.collect_settings()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
            self.statusBar().showMessage(f"设置已导出到 {path}", 5000)
            self.append_log(f"导出设置: {path}")
            QMessageBox.information(self, "导出成功", f"设置已导出到\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", f"导出失败：\n{format_error(exc)}")

    def import_settings(self):
        """从 JSON 文件导入设置。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "导入设置", "", "JSON 文件 (*.json);;所有文件 (*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                QMessageBox.warning(self, "导入失败", "文件内容不是有效的设置 JSON。")
                return
            merged = dict(DEFAULT_SETTINGS)
            merged.update(data)
            ok, err = save_settings(merged)
            if not ok:
                QMessageBox.warning(self, "导入失败", f"保存设置失败：\n{err}")
                return
            self.settings = merged
            self.apply_settings_to_ui()
            self.statusBar().showMessage(f"设置已从 {path} 导入", 5000)
            self.append_log(f"导入设置: {path}")
            QMessageBox.information(self, "导入成功", "设置已导入并应用。")
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", f"导入失败：\n{format_error(exc)}")

    def show_history_context_menu(self, pos):
        index = self.history_table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        menu = QMenu(self.history_table)
        act_play = menu.addAction("在软件内播放")
        act_redownload = menu.addAction("重新下载")
        act_copy_link = menu.addAction("复制链接")
        act_open = menu.addAction("打开文件")
        act_dir = menu.addAction("打开目录")
        menu.addSeparator()
        act_delete = menu.addAction("删除记录")
        action = menu.exec_(self.history_table.viewport().mapToGlobal(pos))
        if action == act_play:
            self.history_table.selectRow(row)
            self.play_history_media()
        elif action == act_redownload:
            self.history_table.selectRow(row)
            self.redownload_history()
        elif action == act_copy_link:
            self.history_table.selectRow(row)
            self.copy_history_link(row)
        elif action == act_open:
            self.history_table.selectRow(row)
            self.open_history_file()
        elif action == act_dir:
            self.history_table.selectRow(row)
            self.open_history_dir()
        elif action == act_delete:
            self.history_table.selectRow(row)
            self.delete_history()

    def on_history_double_clicked(self, index):
        self.history_table.selectRow(index.row())
        self.open_history_file()

    # ---------- 关闭 ----------

    def closeEvent(self, event):
        # 如果托盘可用且不是强制退出，最小化到托盘
        if self.tray_icon and not self._force_quit:
            event.ignore()
            self.hide()
            self.tray_icon.showMessage(
                self.base_window_title,
                "已最小化到托盘，单击图标恢复窗口",
                QSystemTrayIcon.Information, 3000,
            )
            return
        if self.worker and self.worker.isRunning():
            ret = QMessageBox.question(
                self, "确认退出",
                "有下载任务正在进行，确定退出吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                event.ignore()
                return
            self.worker.cancel()
            if not self.worker.wait(15000):
                event.ignore()
                message = "下载线程仍在停止，请稍后再试退出"
                self.statusBar().showMessage(message, 5000)
                self.show_tray_message("正在停止", message)
                return
        if self.preview_worker and self.preview_worker.isRunning():
            if not self.preview_worker.wait(22000):
                event.ignore()
                message = "解析线程仍在停止，请稍后再试退出"
                self.statusBar().showMessage(message, 5000)
                self.show_tray_message("正在停止", message)
                return
        if self.cookie_check_worker and self.cookie_check_worker.isRunning():
            if not self.cookie_check_worker.wait(18000):
                event.ignore()
                message = "Cookie 检测仍在进行，请稍后再试退出"
                self.statusBar().showMessage(message, 5000)
                return
        if self.proxy_test_worker and self.proxy_test_worker.isRunning():
            if not self.proxy_test_worker.wait(12000):
                event.ignore()
                self.statusBar().showMessage("代理测试仍在进行，请稍后再试退出", 5000)
                return
        for thumb_worker in self._thumb_workers:
            if thumb_worker.isRunning() and not thumb_worker.wait(12000):
                event.ignore()
                message = "封面线程仍在停止，请稍后再试退出"
                self.statusBar().showMessage(message, 5000)
                return
        self._save_queue_state()
        ok, err = save_settings(self.collect_settings())
        if not ok:
            self.statusBar().showMessage(f"设置保存失败: {err}", 5000)
        if self.tray_icon:
            self.tray_icon.hide()
        event.accept()
