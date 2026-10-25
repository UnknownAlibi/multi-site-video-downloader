"""浅色二次元主题：设计变量与全局样式表。

界面配色、圆角、间距集中在这里，后续换肤只需要改本文件。
"""

from pathlib import Path
from string import Template


_ARROW_ICON = (Path(__file__).resolve().parent / "arrow_down.png").as_posix()


# ---------- 设计变量 ----------

INK = "#3B4764"
INK_SOFT = "#7C8BA8"
INK_MUTE = "#A4B1C7"

BLUE = "#5B8DEF"
BLUE_DEEP = "#3F6FD6"
BLUE_TINT = "#EDF4FF"

PINK = "#FF7EA8"
PINK_DEEP = "#F45E92"
PINK_TINT = "#FFF2F7"

CARD_BG = "rgba(255,255,255,0.94)"
BORDER = "#E4EEFB"
BORDER_STRONG = "#CFE2F9"

SKY_TOP = "#DDF0FF"
SKY_MID = "#EAF4FF"
SKY_BOTTOM = "#FFF0F7"

RADIUS_CARD = 18
RADIUS_CTRL = 12


# ---------- 明暗主题 ----------

_LIGHT = {
    "text": INK,
    "text_soft": INK_SOFT,
    "text_mute": INK_MUTE,
    "card_bg": "#FFFFFF",
    "card_alpha": 250,
    "card_hover": "#FFFFFF",
    "border": BORDER,
    "border_strong": BORDER_STRONG,
    "tile_blue": "#EAF2FF",
    "tile_gray": "#EEF3FA",
    "track": "#E4EEFA",
    "toggle_off": "#DCE7F5",
    "placeholder_from": "#FFFFFF",
    "placeholder_to": "#FFEAF4",
    "sky_top": SKY_TOP,
    "sky_mid": SKY_MID,
    "sky_bottom": SKY_BOTTOM,
    "sky_veil": "#FFFFFF",
    "sky_veil_alpha": 62,
    "cloud": "#FFFFFF",
    "cloud_alpha": 1.0,
    "blue_text": BLUE_DEEP,
    "pink_text": PINK_DEEP,
}

_DARK = {
    "text": "#E7EDF7",
    "text_soft": "#9BA8BF",
    "text_mute": "#71809A",
    "card_bg": "#232A38",
    "card_alpha": 252,
    "card_hover": "#2C3546",
    "border": "#33405A",
    "border_strong": "#43516B",
    "tile_blue": "#2A3A52",
    "tile_gray": "#2E3646",
    "track": "#2C374A",
    "toggle_off": "#3A4760",
    "placeholder_from": "#232A38",
    "placeholder_to": "#2B2230",
    "sky_top": "#121824",
    "sky_mid": "#18202E",
    "sky_bottom": "#241C2A",
    "sky_veil": "#080C14",
    "sky_veil_alpha": 120,
    "cloud": "#6F8AAE",
    "cloud_alpha": 0.55,
    "blue_text": "#8FB8F9",
    "pink_text": "#FF9CBE",
}

_MODE = "light"


def set_mode(mode):
    global _MODE
    _MODE = "dark" if str(mode).lower() == "dark" else "light"


def get(key, default=None):
    palette = _DARK if _MODE == "dark" else _LIGHT
    return palette.get(key, _LIGHT.get(key, default))


def is_dark():
    return _MODE == "dark"


_QSS = Template("""
QWidget {
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 13px;
    color: $INK;
}

QMainWindow {
    /* 窗口改为不透明后必须给一个明确底色，否则深色系统主题下会露出黑色 */
    background: $SKY_MID;
}
QDialog {
    background: #FFFFFF;
}

#appRoot, #bodyWidget, #pageRoot, #pageContainer, #pageScroll,
QScrollArea, QScrollArea > QWidget > QWidget {
    background: transparent;
    border: none;
}

/* ---------- 侧边栏 ---------- */
#sidebar {
    background: rgba(255, 255, 255, 0.92);
    border: 1px solid rgba(255, 255, 255, 0.95);
    border-right: none;
    border-top-left-radius: 18px;
    border-bottom-left-radius: 18px;
}
#logoTitle {
    font-size: 17px;
    font-weight: 800;
    color: $INK;
    background: transparent;
}
#logoSubtitle {
    font-size: 11px;
    color: $INK_SOFT;
    background: transparent;
}
#navBtn {
    background: transparent;
    color: $INK_SOFT;
    border: none;
    border-radius: 14px;
    padding: 12px 14px;
    text-align: left;
    font-size: 13px;
    font-weight: 600;
}
#navBtn:hover:!checked {
    background: rgba(91, 141, 239, 0.08);
    color: $BLUE_DEEP;
}
#navBtn:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #7DB1FF, stop:1 #4F86EB);
    color: #FFFFFF;
}
#sidebarTip {
    font-size: 11px;
    color: $INK_SOFT;
    background: rgba(255, 255, 255, 0.7);
    border: 1px solid rgba(207, 226, 249, 0.8);
    border-radius: 12px;
    padding: 8px 10px;
}
#mascotBubble {
    font-size: 11px;
    color: $INK;
    background: rgba(255, 255, 255, 0.92);
    border: 1px solid $BORDER;
    border-radius: 12px;
    padding: 7px 10px;
}
#sidebarQuote {
    font-size: 11px;
    color: $INK_SOFT;
    background: transparent;
}

/* ---------- 标题栏 ---------- */
#titleBar {
    background: transparent;
    border: none;
}
#versionChip {
    background: rgba(255, 255, 255, 0.75);
    border: 1px solid $BORDER;
    border-radius: 9px;
    color: $BLUE_DEEP;
    font-size: 11px;
    font-weight: 700;
    padding: 2px 9px;
}
#titleSlogan {
    color: #5E6D8A;
    font-size: 12px;
    font-weight: 600;
    background: transparent;
}
#windowCtrl, #windowCtrlClose {
    background: transparent;
    color: $INK_SOFT;
    border: none;
    border-radius: 8px;
    font-size: 15px;
    font-weight: 500;
    padding: 0;
}
#windowCtrl:hover {
    background: rgba(91, 141, 239, 0.12);
    color: $BLUE_DEEP;
}
#windowCtrlClose:hover {
    background: #FFE3EC;
    color: $PINK_DEEP;
}

/* ---------- 卡片 ---------- */
#softCard {
    background: transparent;
    border: none;
}
#cardTitle {
    font-size: 14px;
    font-weight: 700;
    color: $INK;
    background: transparent;
    padding-bottom: 2px;
}
#cardHint {
    font-size: 11px;
    color: $INK_MUTE;
    background: transparent;
}
#choiceTitle {
    font-size: 15px;
    font-weight: 700;
    background: transparent;
}
#choiceSub {
    font-size: 10px;
    background: transparent;
}
#choiceDetail {
    font-size: 9px;
    background: transparent;
}
#sectionLabel {
    font-size: 12px;
    font-weight: 700;
    color: $INK_SOFT;
    background: transparent;
}
#hintText {
    font-size: 11px;
    color: $INK_MUTE;
    background: transparent;
}
#optionLabel {
    font-size: 11px;
    color: $INK_SOFT;
    background: transparent;
}
#sidePath {
    font-size: 10px;
    color: $INK_SOFT;
    background: transparent;
}
#metaDot {
    color: #C3D2E6;
    background: transparent;
}

/* ---------- 文本 ---------- */
#pageTitle {
    font-size: 20px;
    font-weight: 800;
    color: $INK;
}
#pageSubtitle {
    font-size: 12px;
    color: $INK_SOFT;
}
#previewTitle {
    font-size: 18px;
    font-weight: 700;
    color: $INK;
    background: transparent;
}
#previewUploader {
    font-size: 12px;
    font-weight: 600;
    color: $BLUE_DEEP;
    background: transparent;
}
#previewMeta {
    font-size: 11px;
    color: $INK_SOFT;
    background: transparent;
}
#previewNote {
    font-size: 11px;
    color: $INK_SOFT;
    background: transparent;
}
#rightQuote {
    font-size: 12px;
    color: $INK_SOFT;
    background: transparent;
}

#pill {
    background: rgba(91, 141, 239, 0.10);
    color: $BLUE_DEEP;
    border-radius: 9px;
    padding: 3px 9px;
    font-size: 11px;
    font-weight: 600;
}
#pillPink {
    background: rgba(255, 126, 168, 0.14);
    color: $PINK_DEEP;
    border-radius: 9px;
    padding: 3px 9px;
    font-size: 11px;
    font-weight: 600;
}

/* ---------- 输入 ---------- */
QLineEdit, QPlainTextEdit, QSpinBox {
    background: rgba(255, 255, 255, 0.92);
    border: 1px solid $BORDER;
    border-radius: 12px;
    padding: 8px 12px;
    color: $INK;
    selection-background-color: rgba(255, 126, 168, 0.35);
    selection-color: $INK;
}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {
    border: 1px solid #8FB8F9;
    background: #FFFFFF;
}
QLineEdit:disabled, QPlainTextEdit:disabled, QSpinBox:disabled {
    background: rgba(245, 249, 255, 0.8);
    color: $INK_MUTE;
}
#linkInput {
    font-size: 13px;
    padding: 12px 15px;
    border-radius: 14px;
}
#inputShell {
    background: rgba(255, 255, 255, 0.97);
    border: 1px solid #E1ECFA;
    border-radius: 14px;
}
#linkInputInline {
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 0;
    font-size: 13px;
}
#linkInputInline:focus {
    background: transparent;
    border: none;
}
#iconBtn {
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 0;
}
#iconBtn:hover {
    background: rgba(91, 141, 239, 0.12);
}
#vSep {
    background: rgba(226, 237, 250, 0.95);
}
#hSep {
    background: rgba(228, 238, 251, 0.95);
}
#settingsPlaceholder {
    background: rgba(91, 141, 239, 0.05);
    border: 1px dashed #CFE2F9;
    border-radius: 12px;
}
#placeholderTitle {
    font-size: 13px;
    font-weight: 700;
    color: $INK;
    background: transparent;
}
#tagChip {
    background: rgba(91, 141, 239, 0.10);
    color: #3F6FD6;
    border-radius: 8px;
    padding: 2px 8px;
    font-size: 10px;
    font-weight: 600;
}
QTableWidget#queueTable {
    background: transparent;
    border: none;
}
QTableWidget#queueTable::item {
    border-bottom: 1px solid rgba(236, 243, 252, 0.9);
}

QComboBox {
    background: rgba(255, 255, 255, 0.92);
    border: 1px solid $BORDER;
    border-radius: 10px;
    padding: 6px 10px;
    min-height: 18px;
    color: $INK;
}
QComboBox:focus, QComboBox:hover {
    border: 1px solid #8FB8F9;
}
QComboBox:disabled {
    color: $INK_MUTE;
    background: rgba(245, 249, 255, 0.8);
}
QComboBox::drop-down {
    border: none;
    width: 22px;
    subcontrol-origin: padding;
    subcontrol-position: center right;
}
QComboBox::down-arrow {
    image: url("$ARROW_ICON");
    width: 14px;
    height: 14px;
}

QComboBox QAbstractItemView {
    background: #FFFFFF;
    border: 1px solid $BORDER;
    border-radius: 10px;
    padding: 4px;
    outline: none;
    selection-background-color: rgba(91, 141, 239, 0.14);
    selection-color: $INK;
}

QSpinBox::up-button, QSpinBox::down-button {
    background: transparent;
    border: none;
    width: 16px;
}

/* ---------- 按钮 ---------- */
QPushButton {
    background: rgba(255, 255, 255, 0.92);
    color: $INK;
    border: 1px solid $BORDER;
    border-radius: 11px;
    padding: 8px 14px;
    font-weight: 600;
    font-size: 12px;
}
QPushButton:hover:!disabled {
    border: 1px solid #B9D6F8;
    background: #FFFFFF;
}
QPushButton:pressed:!disabled {
    background: $BLUE_TINT;
}
QPushButton:disabled {
    background: rgba(243, 247, 253, 0.8);
    color: $INK_MUTE;
    border: 1px solid #EDF3FB;
}
#primaryBtn {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #FF9CBE, stop:1 #FF6FA5);
    color: #FFFFFF;
    border: none;
    border-radius: 13px;
    padding: 10px 22px;
    font-size: 13px;
    font-weight: 700;
}
#primaryBtn:hover:!disabled {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #FF8CB4, stop:1 #F95E99);
}
#primaryBtn:pressed:!disabled {
    background: #F04F8C;
}
#primaryBtn:disabled {
    background: #FFD3E2;
    color: rgba(255, 255, 255, 0.85);
    border: none;
}
#heroBtn {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #FF9EC0, stop:1 #FF6DA3);
    color: #FFFFFF;
    border: none;
    border-radius: 16px;
    padding: 15px 20px;
    font-size: 15px;
    font-weight: 800;
}
#heroBtn:hover:!disabled {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #FF8CB4, stop:1 #F95E99);
}
#heroBtn:disabled {
    background: #FFD3E2;
    color: rgba(255, 255, 255, 0.9);
}
#secondaryBtn {
    background: rgba(255, 255, 255, 0.88);
    color: $INK_SOFT;
    border: 1px solid $BORDER;
}
#secondaryBtn:hover:!disabled {
    color: $BLUE_DEEP;
    border: 1px solid #B9D6F8;
    background: #FFFFFF;
}
#ghostBtn {
    background: transparent;
    color: $INK_SOFT;
    border: none;
    padding: 6px 10px;
    font-size: 11px;
    font-weight: 600;
}
#ghostBtn:hover:!disabled {
    color: $PINK_DEEP;
    background: rgba(255, 126, 168, 0.08);
    border-radius: 9px;
}
#dangerBtn {
    color: $PINK_DEEP;
}

/* ---------- 进度 / 表格 ---------- */
QProgressBar {
    border: none;
    border-radius: 6px;
    height: 12px;
    background: rgba(226, 237, 250, 0.9);
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    border-radius: 6px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #FFB3CC, stop:0.5 #8FB8F9, stop:1 #5B8DEF);
}

QTableWidget {
    background: rgba(255, 255, 255, 0.55);
    border: 1px solid #ECF3FC;
    border-radius: 12px;
    gridline-color: transparent;
    selection-background-color: rgba(91, 141, 239, 0.12);
    selection-color: $INK;
    alternate-background-color: rgba(247, 251, 255, 0.65);
    outline: none;
}
QTableWidget::item {
    padding: 7px 6px;
    border: none;
    border-bottom: 1px solid rgba(236, 243, 252, 0.9);
}
QTableWidget::item:selected {
    background: rgba(91, 141, 239, 0.12);
}
QHeaderView::section {
    background: rgba(243, 249, 255, 0.95);
    color: $INK_SOFT;
    border: none;
    border-bottom: 1px solid #E8F1FB;
    padding: 8px 6px;
    font-weight: 700;
    font-size: 11px;
}
QTableCornerButton::section {
    background: rgba(243, 249, 255, 0.95);
    border: none;
}

/* ---------- 复选 / 开关容器 ---------- */
QCheckBox {
    spacing: 8px;
    font-size: 12px;
    font-weight: 500;
    color: $INK;
    background: transparent;
}
QCheckBox::indicator {
    width: 17px;
    height: 17px;
    border-radius: 5px;
    border: 1px solid $BORDER_STRONG;
    background: #FFFFFF;
}
QCheckBox::indicator:hover {
    border: 1px solid #8FB8F9;
}
QCheckBox::indicator:checked {
    background: $BLUE;
    border: 1px solid $BLUE;
}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {
    background: transparent;
    width: 9px;
    border: none;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: rgba(139, 170, 214, 0.55);
    border-radius: 4px;
    min-height: 32px;
}
QScrollBar::handle:vertical:hover {
    background: rgba(107, 148, 207, 0.75);
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
    border: none;
    height: 0;
}
QScrollBar:horizontal {
    background: transparent;
    height: 8px;
    border: none;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: rgba(148, 176, 214, 0.38);
    border-radius: 4px;
    min-width: 32px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: none;
    border: none;
    width: 0;
}

/* ---------- 状态栏 / 菜单 ---------- */
QStatusBar {
    background: #F8FBFF;
    color: $INK_SOFT;
    border-top: 1px solid rgba(228, 238, 251, 0.9);
    padding: 3px 14px;
    font-size: 11px;
}
QStatusBar::item {
    border: none;
}
QMenu {
    background: #FFFFFF;
    border: 1px solid $BORDER;
    border-radius: 12px;
    padding: 6px;
}
QMenu::item {
    padding: 7px 24px;
    border-radius: 8px;
    font-size: 12px;
}
QMenu::item:selected {
    background: rgba(91, 141, 239, 0.12);
    color: $BLUE_DEEP;
}
QMenu::separator {
    height: 1px;
    background: $BORDER;
    margin: 5px 10px;
}
QToolTip {
    background: #3B4764;
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 11px;
}
QMessageBox {
    background: #FFFFFF;
}
""")


_DARK_QSS = """
/* ---------- 夜间模式覆盖 ---------- */
QWidget { color: #E7EDF7; }
QMainWindow { background: #131A26; }
QDialog, QMessageBox { background: #1B2230; }

#sidebar {
    background: rgba(24, 30, 42, 0.94);
    border-color: rgba(255, 255, 255, 0.08);
}
#logoTitle, #pageTitle, #cardTitle, #previewTitle, #placeholderTitle {
    color: #E7EDF7;
}
#logoSubtitle, #sidebarQuote, #pageSubtitle, #previewMeta, #previewNote,
#cardHint, #rightQuote, #sectionLabel, #titleSlogan, #optionLabel {
    color: #9BA8BF;
}
#hintText { color: #A9B6CC; }
#sidePath { color: #C7D2E5; }
#navBtn { color: #9BA8BF; }
#navBtn:hover:!checked { background: rgba(91, 141, 239, 0.18); color: #BBD4FF; }
#navBtn:checked { color: #FFFFFF; }
#versionChip {
    background: rgba(255, 255, 255, 0.08);
    border-color: #3A4760;
    color: #A9C8FF;
}
#windowCtrl, #windowCtrlClose { color: #9BA8BF; }
#windowCtrl:hover { background: rgba(91, 141, 239, 0.20); color: #BBD4FF; }
#windowCtrlClose:hover { background: rgba(255, 126, 168, 0.22); color: #FFC2D8; }
#mascotBubble {
    background: rgba(35, 42, 56, 0.96);
    border-color: #33405A;
    color: #E7EDF7;
}
#previewUploader { color: #8FB8F9; }
#pill { background: rgba(91, 141, 239, 0.18); color: #A9C8FF; }
#pillPink { background: rgba(255, 126, 168, 0.18); color: #FFC2D8; }
#tagChip { background: rgba(91, 141, 239, 0.16); color: #A9C8FF; }
#metaDot { color: #4A5A75; }
#vSep, #hSep { background: #33405A; }
#settingsPlaceholder {
    background: rgba(91, 141, 239, 0.07);
    border-color: #3A4F6E;
}

QLineEdit, QPlainTextEdit, QSpinBox {
    background: #202736;
    border-color: #33405A;
    color: #E7EDF7;
}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {
    background: #232C3C;
    border-color: #6FA0E8;
}
QLineEdit:disabled, QPlainTextEdit:disabled, QSpinBox:disabled {
    background: #1B2230;
    color: #5E6C84;
}
#inputShell { background: #202736; border-color: #33405A; }
#linkInputInline { background: transparent; border: none; }
#iconBtn:hover { background: rgba(91, 141, 239, 0.20); }

QComboBox {
    background: #202736;
    border-color: #33405A;
    color: #E7EDF7;
}
QComboBox:focus, QComboBox:hover { border-color: #6FA0E8; }
QComboBox:disabled { background: #1B2230; color: #5E6C84; }
QComboBox QAbstractItemView {
    background: #232A38;
    border-color: #3A4760;
    color: #E7EDF7;
    selection-background-color: rgba(91, 141, 239, 0.25);
    selection-color: #E7EDF7;
}

QPushButton {
    background: #2A3342;
    color: #E7EDF7;
    border-color: #3A4760;
}
QPushButton:hover:!disabled {
    background: #33405A;
    border-color: #4A5C7A;
}
QPushButton:pressed:!disabled { background: #202736; }
QPushButton:disabled {
    background: #252C3A;
    color: #5E6C84;
    border-color: #2C374A;
}
#primaryBtn:disabled { background: #4A3440; color: #C9A9B6; }
#heroBtn:disabled { background: #4A3440; color: #C9A9B6; }
#secondaryBtn {
    background: #2A3342;
    color: #C7D2E5;
    border-color: #3A4760;
}
#secondaryBtn:hover:!disabled {
    background: #33405A;
    color: #E7EDF7;
    border-color: #4A5C7A;
}
#ghostBtn { color: #B8C6DC; }
#ghostBtn:hover:!disabled {
    color: #FFC2D8;
    background: rgba(255, 126, 168, 0.12);
}
#dangerBtn { color: #FF9CBE; }

QProgressBar { background: #2C374A; }
QTableWidget {
    background: rgba(255, 255, 255, 0.02);
    border-color: #2C374A;
    selection-background-color: rgba(91, 141, 239, 0.22);
    selection-color: #E7EDF7;
    alternate-background-color: rgba(255, 255, 255, 0.03);
}
QTableWidget::item { border-bottom-color: rgba(255, 255, 255, 0.06); }
QHeaderView::section {
    background: #1E2532;
    color: #9BA8BF;
    border-bottom-color: #2C374A;
}
QTableCornerButton::section { background: #1E2532; }
QTableWidget#queueTable { background: transparent; }
QTableWidget#queueTable::item { border-bottom-color: rgba(255, 255, 255, 0.06); }

QCheckBox { color: #E7EDF7; }
QCheckBox::indicator { background: #202736; border-color: #43516B; }
QCheckBox::indicator:hover { border-color: #6FA0E8; }
QCheckBox::indicator:checked { background: #5B8DEF; border-color: #5B8DEF; }

QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: rgba(150, 170, 205, 0.42);
}
QStatusBar {
    background: #171E2A;
    color: #9BA8BF;
    border-top-color: #2C374A;
}
QMenu { background: #232A38; border-color: #3A4760; }
QMenu::item:selected { background: rgba(91, 141, 239, 0.22); color: #E7EDF7; }
QMenu::separator { background: #33405A; }
QToolTip { background: #E7EDF7; color: #1B2230; }
"""


def build_stylesheet():
    """返回当前主题的完整 QSS。"""
    qss = _QSS.substitute(
        INK=INK,
        INK_SOFT=INK_SOFT,
        INK_MUTE=INK_MUTE,
        BLUE=BLUE,
        BLUE_DEEP=BLUE_DEEP,
        BLUE_TINT=BLUE_TINT,
        PINK=PINK,
        PINK_DEEP=PINK_DEEP,
        PINK_TINT=PINK_TINT,
        BORDER=BORDER,
        BORDER_STRONG=BORDER_STRONG,
        ARROW_ICON=_ARROW_ICON,
        SKY_MID=SKY_MID,
    )
    if is_dark():
        qss += _DARK_QSS
    return qss
