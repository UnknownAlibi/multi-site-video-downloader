"""主题化基础控件：天空背景、圆角卡片、选项卡片、开关、封面与图片占位。"""

import math

from PyQt5.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QTimer,
    Qt,
    pyqtProperty,
    pyqtSignal,
)
from PyQt5.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PyQt5.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ..config import BASE_DIR, RESOURCE_DIR
from .theme import (
    BLUE,
    PINK,
    PINK_DEEP,
    PINK_TINT,
    get as theme_get,
    is_dark,
)


def _first_existing(*names):
    """优先读 exe 同级目录，方便用户直接替换图片，其次读打包内置资源。"""
    for name in names:
        for root in (BASE_DIR, RESOURCE_DIR):
            path = root / name
            if path.exists():
                return path
    return None


class SkyBackground(QWidget):
    """柔和天空背景：优先读取 assets/bg_sky.png，缺失时用渐变 + 云朵绘制。"""

    CLOUDS = [
        (0.10, 0.10, 0.22, 190),
        (0.32, 0.06, 0.18, 150),
        (0.55, 0.12, 0.26, 170),
        (0.78, 0.05, 0.20, 160),
        (0.92, 0.16, 0.18, 130),
        (0.22, 0.24, 0.16, 90),
        (0.68, 0.26, 0.15, 80),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._radius = 0
        self._cache = QPixmap()
        # 拖动缩放时不要每个 resize 事件都重建整窗背景，等停下来再重建
        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(150)
        self._rebuild_timer.timeout.connect(self._rebuild_cache)
        path = _first_existing("assets/bg_sky.png", "assets/bg_sky.jpg", "assets/bg_sky.webp")
        self._image = QPixmap(str(path)) if path else QPixmap()

    def set_radius(self, radius):
        if self._radius != radius:
            self._radius = radius
            self._rebuild_timer.start()
            self.update()

    def resizeEvent(self, event):
        self._rebuild_timer.start()
        super().resizeEvent(event)

    def _rebuild_cache(self):
        self._build_cache()
        self.update()

    def _build_cache(self):
        """背景只在尺寸变化时重建，避免每帧缩放整张背景图。"""
        ratio = self.devicePixelRatioF()
        pixmap = QPixmap(int(self.width() * ratio), int(self.height() * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0, 0, self.width(), self.height())
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        painter.setClipPath(path)

        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0.0, QColor(theme_get("sky_top")))
        gradient.setColorAt(0.55, QColor(theme_get("sky_mid")))
        gradient.setColorAt(1.0, QColor(theme_get("sky_bottom")))
        painter.fillPath(path, QBrush(gradient))

        if not self._image.isNull():
            scaled = self._image.scaled(
                self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            painter.setOpacity(0.92)
            painter.drawPixmap(
                (self.width() - scaled.width()) // 2,
                (self.height() - scaled.height()) // 2,
                scaled,
            )
            painter.setOpacity(1.0)
            # 背景图整体压暗/压淡，避免和卡片、文字抢视觉
            veil = QColor(theme_get("sky_veil"))
            veil.setAlpha(int(theme_get("sky_veil_alpha")))
            painter.fillPath(path, veil)
            painter.end()
            self._cache = pixmap
            return

        painter.setPen(Qt.NoPen)
        cloud_color = QColor(theme_get("cloud"))
        cloud_strength = float(theme_get("cloud_alpha") or 1.0)
        for xr, yr, rr, alpha in self.CLOUDS:
            cx = self.width() * xr
            cy = self.height() * yr
            radius = max(self.width(), 600) * rr
            cloud = QRadialGradient(cx, cy, radius)
            cloud.setColorAt(0.0, QColor(cloud_color.red(), cloud_color.green(), cloud_color.blue(), int(alpha * cloud_strength)))
            cloud.setColorAt(0.55, QColor(cloud_color.red(), cloud_color.green(), cloud_color.blue(), int(alpha * 0.55 * cloud_strength)))
            cloud.setColorAt(1.0, QColor(cloud_color.red(), cloud_color.green(), cloud_color.blue(), 0))
            painter.setBrush(QBrush(cloud))
            painter.drawEllipse(QRectF(cx - radius, cy - radius * 0.62, radius * 2, radius * 1.24))

        # 少量星点，避免大片空白
        for i in range(16):
            x = (i * 137.5) % max(self.width(), 1)
            y = (i * 89.3) % max(int(self.height() * 0.55), 1)
            r = 1.2 + (i % 3) * 0.7
            painter.setBrush(QColor(255, 255, 255, int((110 + (i % 4) * 25) * cloud_strength)))
            painter.drawEllipse(QRectF(x, y, r, r))

        painter.end()
        self._cache = pixmap

    def apply_theme(self):
        self._cache = QPixmap()
        self._rebuild_timer.start()
        self.update()

    def paintEvent(self, event):
        if self._cache.isNull():
            self._build_cache()
        painter = QPainter(self)
        # 先拉伸旧缓存顶住缩放过程，150ms 后再生成清晰的新缓存
        painter.drawPixmap(self.rect(), self._cache)
        painter.end()


class SoftCard(QFrame):
    """圆角白色卡片。

    shadow 参数保留用于兼容调用，但不再使用 QGraphicsEffect：
    半透明窗口 + 卡片特效在拖动缩放时会导致明显闪烁。
    """

    def __init__(self, title=None, parent=None, shadow=True, radius=18):
        super().__init__(parent)
        self.setObjectName("softCard")
        self._radius = radius
        self._glow = False
        self.body_layout = QVBoxLayout(self)
        self.body_layout.setContentsMargins(18, 16, 18, 18)
        self.body_layout.setSpacing(10)
        self.title_label = None
        if title:
            self.title_label = QLabel(title)
            self.title_label.setObjectName("cardTitle")
            self.body_layout.addWidget(self.title_label)

    def set_glow_enabled(self, enabled):
        if self._glow != enabled:
            self._glow = enabled
            self.update()

    def apply_theme(self):
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        background = QColor(theme_get("card_bg"))
        background.setAlpha(int(theme_get("card_alpha")))
        painter.fillPath(path, background)
        painter.setPen(QPen(QColor(theme_get("border")), 1))
        painter.drawPath(path)
        if self._glow:
            for i, alpha in ((3, 26), (2, 34), (1, 44)):
                pen = QPen(QColor(255, 126, 168, alpha))
                pen.setWidth(2)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(
                    rect.adjusted(-i, -i, i, i), self._radius + i, self._radius + i
                )
        painter.end()


class ChoiceCard(QFrame):
    """画质 / 格式选择卡片：左侧图标块 + 右侧标题、说明、补充信息。"""

    clicked = pyqtSignal(str)
    cleared = pyqtSignal(str)

    def __init__(self, value, title, subtitle="", detail="", parent=None,
                 width=112, height=88, accent="pink", badge="", icon_kind=""):
        super().__init__(parent)
        self.value = value
        self._selected = False
        self._hover = False
        self._accent = accent
        self._badge = badge
        self._icon_kind = icon_kind
        self.setFixedSize(width, height)
        self.setCursor(Qt.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.tile = QLabel()
        self.tile.setFixedSize(26, 26)
        self.tile.setAlignment(Qt.AlignCenter)
        self.tile.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.tile, 0, Qt.AlignVCenter)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("choiceTitle")
        title_font = QFont("Microsoft YaHei UI", 10)
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        self.title_label.setStyleSheet(
            f"color: {theme_get('text')}; background: transparent;"
        )

        self.sub_label = QLabel(subtitle)
        self.sub_label.setObjectName("choiceSub")
        sub_font = QFont("Microsoft YaHei UI", 8)
        self.sub_label.setFont(sub_font)
        self.sub_label.setStyleSheet(
            f"color: {theme_get('text_soft')}; background: transparent;"
        )

        self.detail_label = QLabel(detail)
        self.detail_label.setObjectName("choiceDetail")
        detail_font = QFont("Microsoft YaHei UI", 8)
        self.detail_label.setFont(detail_font)
        self.detail_label.setStyleSheet(
            f"color: {theme_get('text_mute')}; background: transparent;"
        )
        for label in (self.title_label, self.sub_label, self.detail_label):
            label.setAttribute(Qt.WA_TransparentForMouseEvents)

        text_col.addStretch()
        text_col.addWidget(self.title_label)
        text_col.addWidget(self.sub_label)
        text_col.addWidget(self.detail_label)
        text_col.addStretch()
        layout.addLayout(text_col, 1)
        self._refresh_tile()

    def apply_theme(self):
        self.title_label.setStyleSheet(
            f"color: {theme_get('text')}; background: transparent;"
        )
        self.sub_label.setStyleSheet(
            f"color: {theme_get('text_soft')}; background: transparent;"
        )
        self.detail_label.setStyleSheet(
            f"color: {theme_get('text_mute')}; background: transparent;"
        )
        for label in (self.title_label, self.sub_label, self.detail_label):
            label.update()
            label.repaint()
        self._refresh_tile()
        self.update()
        self.repaint()

    def _refresh_tile(self):
        tile_blue = theme_get("tile_blue")
        if self._icon_kind:
            if self._icon_kind == "audio":
                background = tile_blue
                icon_color = BLUE
            elif self._selected:
                background = PINK
                icon_color = "#FFFFFF"
            else:
                background = tile_blue
                icon_color = BLUE
            self.tile.setPixmap(make_tool_icon(self._icon_kind, icon_color).pixmap(15, 15))
            self.tile.setStyleSheet(f"background: {background}; border-radius: 7px;")
            return

        if self._selected:
            background = PINK
            color = "#FFFFFF"
        elif self._badge in ("HD", "4K", "2K", "MAX"):
            background = tile_blue
            color = BLUE
        else:
            background = theme_get("tile_gray")
            color = theme_get("text_soft")
        badge_font = QFont("Microsoft YaHei UI", 7)
        badge_font.setBold(True)
        self.tile.setFont(badge_font)
        self.tile.setText(self._badge)
        self.tile.setStyleSheet(
            f"background: {background}; border-radius: 7px; color: {color};"
            " font-size: 9px; font-weight: 700;"
        )

    def set_selected(self, selected):
        if self._selected != selected:
            self._selected = selected
            self._refresh_tile()
            self.update()

    def is_selected(self):
        return self._selected

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            in_clear = event.pos().x() >= self.width() - 22 and event.pos().y() <= 22
            if self._selected and in_clear and self.value not in ("best", "video"):
                self.cleared.emit(self.value)
            else:
                self.clicked.emit(self.value)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        accent = QColor(PINK if self._accent == "pink" else BLUE)
        if self._selected:
            tint = QColor(accent)
            tint.setAlpha(22)
            painter.fillPath(path, tint)
            painter.setPen(QPen(accent, 1.6))
        elif self._hover:
            painter.fillPath(path, QColor(theme_get("card_hover")))
            painter.setPen(QPen(QColor("#B9D6F8"), 1.2))
        else:
            background = QColor(theme_get("card_bg"))
            background.setAlpha(248)
            painter.fillPath(path, background)
            painter.setPen(QPen(QColor(theme_get("border")), 1))
        painter.drawPath(path)

        if self._selected and self.value not in ("best", "video"):
            cx = rect.right() - 8
            cy = rect.top() + 8
            painter.setPen(Qt.NoPen)
            painter.setBrush(accent)
            painter.drawEllipse(QRectF(cx - 5, cy - 5, 10, 10))
            pen = QPen(QColor(255, 255, 255))
            pen.setWidthF(1.2)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawLine(int(cx - 2), int(cy - 2), int(cx + 2), int(cy + 2))
            painter.drawLine(int(cx + 2), int(cy - 2), int(cx - 2), int(cy + 2))
        painter.end()


class ToggleSwitch(QAbstractButton):
    """圆角胶囊开关。"""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(40, 22)
        self.setCursor(Qt.PointingHandCursor)
        self._offset = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.InOutCubic)
        self.toggled.connect(self._animate)

    def get_offset(self):
        return self._offset

    def set_offset(self, value):
        self._offset = value
        self.update()

    offset = pyqtProperty(float, fget=get_offset, fset=set_offset)

    def apply_theme(self):
        self.update()

    def _animate(self, checked):
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        track = QColor(PINK if self.isChecked() else theme_get("toggle_off"))
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        knob = 16.0
        x = 3 + self._offset * (rect.width() - knob - 6)
        y = (rect.height() - knob) / 2
        painter.setBrush(QColor(255, 255, 255, 245))
        painter.drawEllipse(QRectF(x, y, knob, knob))
        painter.end()


class CoverLabel(QLabel):
    """带圆角、播放按钮和时长角标的封面控件。"""

    clicked = pyqtSignal()

    def __init__(self, width=300, height=168, parent=None):
        super().__init__(parent)
        self.setFixedSize(width, height)
        self.setCursor(Qt.PointingHandCursor)
        self._pixmap = QPixmap()
        self._placeholder = "暂无封面"
        self._badge = ""

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def setPixmap(self, pixmap):
        self._pixmap = QPixmap(pixmap) if pixmap is not None else QPixmap()
        self.update()

    def setText(self, text):
        self._placeholder = text or "暂无封面"
        self.update()

    def set_badge(self, text):
        self._badge = text or ""
        self.update()

    def apply_theme(self):
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        painter.setClipPath(path)

        if not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            painter.drawPixmap(
                int((self.width() - scaled.width()) / 2),
                int((self.height() - scaled.height()) / 2),
                scaled,
            )
        else:
            gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
            gradient.setColorAt(0.0, QColor(theme_get("placeholder_from")))
            gradient.setColorAt(1.0, QColor(theme_get("placeholder_to")))
            painter.fillPath(path, QBrush(gradient))
            painter.setPen(QColor(theme_get("text_mute")))
            painter.drawText(rect, Qt.AlignCenter, self._placeholder)

        # 播放按钮
        cx, cy, r = self.width() / 2, self.height() / 2, 21
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 205))
        painter.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        triangle = QPainterPath()
        triangle.moveTo(cx - 5, cy - 8)
        triangle.lineTo(cx + 9, cy)
        triangle.lineTo(cx - 5, cy + 8)
        triangle.closeSubpath()
        painter.fillPath(triangle, QColor(PINK_DEEP))

        if self._badge:
            painter.setFont(QFont("Microsoft YaHei UI", 8))
            metrics = painter.fontMetrics()
            text_w = metrics.horizontalAdvance(self._badge) + 12
            badge = QRectF(self.width() - text_w - 10, self.height() - 26, text_w, 18)
            painter.setBrush(QColor(43, 52, 74, 165))
            painter.drawRoundedRect(badge, 9, 9)
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(badge, Qt.AlignCenter, self._badge)
        painter.end()


class ImageSlot(QWidget):
    """图片占位：图片缺失时显示虚线框和需要的文件名。"""

    def __init__(self, paths, placeholder, parent=None, min_height=180, radius=16):
        super().__init__(parent)
        self._paths = list(paths)
        self._placeholder = placeholder
        self._radius = radius
        self._pixmap = QPixmap()
        self._scaled_cache = QPixmap()
        self._scaled_for = None
        self.setMinimumHeight(min_height)
        self._reload()

    def _reload(self):
        for path in self._paths:
            if path and path.exists():
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    self._pixmap = pixmap
                    self._scaled_for = None
                    return
        self._pixmap = QPixmap()
        self._scaled_for = None

    def refresh(self):
        self._reload()
        self.update()

    def apply_theme(self):
        self.update()

    def resizeEvent(self, event):
        self._scaled_for = None
        super().resizeEvent(event)

    def has_image(self):
        return not self._pixmap.isNull()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        painter.setClipPath(path)

        if not self._pixmap.isNull():
            # 立绘保留完整人物，不裁切；底部对齐更接近参考图的构图
            if self._scaled_for != self.size():
                self._scaled_cache = self._pixmap.scaled(
                    self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                self._scaled_for = self.size()
            scaled = self._scaled_cache
            painter.drawPixmap(
                int((self.width() - scaled.width()) / 2),
                int(self.height() - scaled.height()),
                scaled,
            )
            painter.end()
            return

        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, QColor(theme_get("placeholder_from")))
        gradient.setColorAt(1.0, QColor(theme_get("placeholder_to")))
        painter.fillPath(path, QBrush(gradient))

        pen = QPen(QColor(theme_get("border_strong")), 1.2, Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect.adjusted(6, 6, -6, -6), self._radius - 4, self._radius - 4)

        painter.setPen(QColor(theme_get("text_soft")))
        font = QFont("Microsoft YaHei UI", 9)
        font.setBold(True)
        painter.setFont(font)
        title_rect = QRectF(rect.left(), rect.center().y() - 22, rect.width(), 20)
        painter.drawText(title_rect, Qt.AlignCenter, "图片占位")
        painter.setFont(QFont("Microsoft YaHei UI", 7))
        painter.setPen(QColor(theme_get("text_mute")))
        body_rect = QRectF(rect.left() + 12, rect.center().y(), rect.width() - 24, 34)
        painter.drawText(body_rect, Qt.AlignHCenter | Qt.TextWordWrap, self._placeholder)
        painter.end()


def make_nav_icon(kind, color_off=None, color_on="#FFFFFF"):
    """绘制侧边栏线性图标（下载 / 历史 / 设置）。"""
    if color_off is None:
        color_off = theme_get("text_soft")
    icon = QIcon()
    for state, color in ((QIcon.Off, color_off), (QIcon.On, color_on)):
        pixmap = QPixmap(20, 20)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(color))
        pen.setWidthF(1.7)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        if kind == "download":
            painter.drawLine(10, 3, 10, 12)
            painter.drawLine(6, 8, 10, 12)
            painter.drawLine(14, 8, 10, 12)
            painter.drawLine(4, 16, 16, 16)
        elif kind == "history":
            painter.drawEllipse(QRectF(4, 4, 12, 12))
            painter.drawLine(10, 6, 10, 10)
            painter.drawLine(10, 10, 13, 12)
        elif kind == "settings":
            painter.drawEllipse(QRectF(7, 7, 6, 6))
            for i in range(6):
                angle = math.radians(i * 60)
                x1 = 10 + 5.2 * math.cos(angle)
                y1 = 10 + 5.2 * math.sin(angle)
                x2 = 10 + 7.6 * math.cos(angle)
                y2 = 10 + 7.6 * math.sin(angle)
                painter.drawLine(int(x1), int(y1), int(x2), int(y2))
        painter.end()
        icon.addPixmap(pixmap, QIcon.Normal, state)
    return icon


def make_media_tile(width=30, height=20):
    """队列行首的小缩略图占位：渐变底 + 播放三角。"""
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    gradient = QLinearGradient(0, 0, width, height)
    gradient.setColorAt(0.0, QColor("#FFC2D8"))
    gradient.setColorAt(1.0, QColor("#A9C8FF"))
    painter.setBrush(QBrush(gradient))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(QRectF(0, 0, width, height), 5, 5)
    triangle = QPainterPath()
    triangle.moveTo(width / 2 - 3, height / 2 - 5)
    triangle.lineTo(width / 2 + 5, height / 2)
    triangle.lineTo(width / 2 - 3, height / 2 + 5)
    triangle.closeSubpath()
    painter.fillPath(triangle, QColor(255, 255, 255, 230))
    painter.end()
    return QIcon(pixmap)


def make_tool_icon(kind, color=None):
    """绘制 18px 线性小图标：链接 / 导入 / 关闭 / 文件夹 / 磁盘 / 下载 / 播放。"""
    if color is None:
        color = theme_get("text_soft")
    pixmap = QPixmap(18, 18)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(1.5)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    if kind == "globe":
        painter.drawEllipse(QRectF(2.5, 2.5, 13, 13))
        painter.drawEllipse(QRectF(7, 2.5, 4, 13))
        painter.drawLine(2, 9, 16, 9)
    elif kind == "import":
        painter.drawLine(9, 2, 9, 10)
        painter.drawLine(5, 6, 9, 10)
        painter.drawLine(13, 6, 9, 10)
        painter.drawLine(3, 16, 15, 16)
    elif kind == "close":
        painter.drawLine(5, 5, 13, 13)
        painter.drawLine(13, 5, 5, 13)
    elif kind == "folder":
        path = QPainterPath()
        path.moveTo(2, 5)
        path.lineTo(7, 5)
        path.lineTo(9, 7)
        path.lineTo(16, 7)
        path.lineTo(16, 14)
        path.lineTo(2, 14)
        path.closeSubpath()
        painter.drawPath(path)
    elif kind == "disk":
        painter.drawRoundedRect(QRectF(3, 3.5, 12, 11), 2, 2)
        painter.drawEllipse(QRectF(6.5, 6, 5, 5))
    elif kind == "download":
        painter.drawLine(9, 2, 9, 11)
        painter.drawLine(5, 7, 9, 11)
        painter.drawLine(13, 7, 9, 11)
        painter.drawLine(3, 15, 15, 15)
    elif kind == "video":
        painter.drawRoundedRect(QRectF(2.5, 4, 13, 10), 2, 2)
        triangle = QPainterPath()
        triangle.moveTo(7, 7)
        triangle.lineTo(11.5, 9)
        triangle.lineTo(7, 11)
        triangle.closeSubpath()
        painter.fillPath(triangle, QColor(color))
    elif kind == "audio":
        painter.drawLine(8, 4, 8, 12)
        painter.drawLine(8, 4, 13, 3)
        painter.drawLine(13, 3, 13, 10)
        painter.drawEllipse(QRectF(5, 11, 4, 3))
        painter.drawEllipse(QRectF(10.5, 9.5, 4, 3))
    elif kind == "monitor":
        painter.drawRoundedRect(QRectF(2, 3, 14, 9), 2, 2)
        painter.drawLine(9, 12, 9, 15)
        painter.drawLine(6, 15, 12, 15)
    elif kind == "headphones":
        painter.drawArc(QRectF(2.5, 3, 13, 13), 0, 180 * 16)
        painter.drawRoundedRect(QRectF(2.5, 8, 3.5, 5), 1.5, 1.5)
        painter.drawRoundedRect(QRectF(12, 8, 3.5, 5), 1.5, 1.5)
    elif kind == "play":
        path = QPainterPath()
        path.moveTo(5, 3)
        path.lineTo(14, 9)
        path.lineTo(5, 15)
        path.closeSubpath()
        painter.fillPath(path, QColor(color))
    elif kind == "min":
        painter.drawLine(4, 9, 14, 9)
    elif kind == "max":
        painter.drawRoundedRect(QRectF(4.5, 4.5, 9, 9), 2, 2)
    elif kind == "moon":
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(QRectF(3, 2, 12, 12))
        painter.setCompositionMode(QPainter.CompositionMode_Clear)
        painter.drawEllipse(QRectF(9.5, 0.5, 10.5, 10.5))
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
    elif kind == "sun":
        painter.drawEllipse(QRectF(6, 6, 6, 6))
        for dx, dy in ((0, -7), (0, 7), (-7, 0), (7, 0), (5, -5), (-5, 5), (5, 5), (-5, -5)):
            painter.drawLine(
                int(9 + dx * 0.65),
                int(9 + dy * 0.65),
                int(9 + dx),
                int(9 + dy),
            )
    painter.end()
    return QIcon(pixmap)


_STATUS_COLORS = {
    "等待": ("#7C8BA8", "rgba(124,139,168,0.13)"),
    "下载中": ("#3F6FD6", "rgba(91,141,239,0.16)"),
    "完成": ("#1F9D63", "rgba(64,190,132,0.16)"),
    "完成（公开视频兜底）": ("#1F9D63", "rgba(64,190,132,0.16)"),
    "失败": ("#E24B78", "rgba(255,126,168,0.18)"),
    "已取消": ("#A4B1C7", "rgba(164,177,199,0.16)"),
    "已删除": ("#A4B1C7", "rgba(164,177,199,0.16)"),
    "已跳过": ("#A4B1C7", "rgba(164,177,199,0.16)"),
}

_STATUS_COLORS_DARK = {
    "等待": ("#A4B1C7", "rgba(164,177,199,0.16)"),
    "下载中": ("#8FB8F9", "rgba(91,141,239,0.22)"),
    "完成": ("#6FD6A6", "rgba(64,190,132,0.20)"),
    "完成（公开视频兜底）": ("#6FD6A6", "rgba(64,190,132,0.20)"),
    "失败": ("#FF9CBE", "rgba(255,126,168,0.22)"),
    "已取消": ("#8B9AB3", "rgba(164,177,199,0.18)"),
    "已删除": ("#8B9AB3", "rgba(164,177,199,0.18)"),
    "已跳过": ("#8B9AB3", "rgba(164,177,199,0.18)"),
}


class StatusCell(QWidget):
    """队列表格里的状态胶囊。"""

    def __init__(self, status="等待", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        self.chip = QLabel(status)
        self.chip.setAlignment(Qt.AlignCenter)
        self.chip.setFixedHeight(21)
        self.chip.setMinimumWidth(52)
        self.chip.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.chip, 0, Qt.AlignVCenter)
        layout.addStretch()
        self.set_status(status)

    def set_status(self, status):
        key = "完成" if status.startswith("完成") else status
        self._status = key
        palette = _STATUS_COLORS_DARK if is_dark() else _STATUS_COLORS
        color, background = palette.get(key, palette["等待"])
        self.chip.setText(key)
        self.chip.setStyleSheet(
            f"color: {color}; background: {background}; border-radius: 8px;"
            " padding: 2px 8px; font-size: 10px; font-weight: 600;"
        )

    def apply_theme(self):
        self.set_status(getattr(self, "_status", "等待"))


class QueueProgress(QWidget):
    """队列进度：细进度条 + 速度 / 剩余时间。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(34)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._percent = 0
        self._detail = ""

    def set_value(self, percent, detail=""):
        self._percent = percent
        self._detail = detail or ""
        self.update()

    def apply_theme(self):
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width = self.width() - 4
        if self._percent >= 0:
            bar = QRectF(2, 5, max(width, 10), 5)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme_get("track")))
            painter.drawRoundedRect(bar, 2.5, 2.5)
            ratio = max(0.0, min(100.0, self._percent)) / 100.0
            if ratio > 0:
                fill = QRectF(bar.left(), bar.top(), max(bar.width() * ratio, 4), bar.height())
                gradient = QLinearGradient(fill.topLeft(), fill.topRight())
                if self._percent >= 100:
                    gradient.setColorAt(0, QColor("#6FD6A6"))
                    gradient.setColorAt(1, QColor("#3FBE8C"))
                else:
                    gradient.setColorAt(0, QColor("#9CC2FB"))
                    gradient.setColorAt(1, QColor("#5B8DEF"))
                painter.setBrush(QBrush(gradient))
                painter.drawRoundedRect(fill, 2.5, 2.5)

        detail_rect = QRectF(2, 14, width, 16)
        painter.setPen(QColor(theme_get("text_soft")))
        font = QFont("Microsoft YaHei UI", 7)
        painter.setFont(font)
        text = QFontMetrics(font).elidedText(self._detail, Qt.ElideRight, int(width))
        painter.drawText(detail_rect, Qt.AlignLeft | Qt.AlignVCenter, text)
        painter.end()
