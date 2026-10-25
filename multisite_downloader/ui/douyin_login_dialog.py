"""通用站点 Cookie 获取对话框（默认参数用于抖音）。"""

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..browser_login import BrowserCookieLoginWorker
from ..cookies import cookie_store_path


class CookieLoginDialog(QDialog):
    def __init__(
        self,
        parent=None,
        site_label="抖音",
        start_url="https://www.douyin.com/",
        cookie_domains=("douyin.com", "iesdouyin.com"),
        output_path=None,
        profile_key="douyin",
        login_cookie_names=(),
        public_cookie_required=(),
    ):
        super().__init__(parent)
        self.cookie_path = ""
        self.site_label = site_label
        self.setWindowTitle(f"{site_label} Cookie 获取")
        self.resize(500, 280)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)

        title = QLabel(f"{site_label} Cookie 获取")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        hint = QLabel(
            "会打开一个独立的 Chrome / Edge 窗口。请在这个窗口里登录目标站点。\n"
            "抖音会自动检测登录；其他站点登录后点击「使用当前 Cookie」。"
        )
        hint.setObjectName("hintText")
        hint.setWordWrap(True)
        hint.setMinimumHeight(38)
        layout.addWidget(hint)

        self.status_label = QLabel("正在启动浏览器...")
        self.status_label.setObjectName("previewMeta")
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumHeight(56)
        self.status_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        layout.addWidget(self.status_label, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.force_btn = QPushButton("使用当前 Cookie")
        self.force_btn.setObjectName("secondaryBtn")
        self.force_btn.setEnabled(False)
        self.force_btn.clicked.connect(self._force_import)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("secondaryBtn")
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.force_btn)
        buttons.addWidget(self.cancel_btn)
        layout.addLayout(buttons)

        self.worker = BrowserCookieLoginWorker(
            site_label=site_label,
            start_url=start_url,
            cookie_domains=cookie_domains,
            output_path=output_path,
            login_cookie_names=login_cookie_names,
            public_cookie_required=public_cookie_required,
            profile_key=profile_key,
            parent=self,
        )
        self.worker.status.connect(self._on_status)
        self.worker.cookies_ready.connect(lambda: self.force_btn.setEnabled(True))
        self.worker.finished_ok.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        QTimer.singleShot(0, self.worker.start)

    def _on_status(self, message):
        self.status_label.setText(message)
        if "使用当前 Cookie" in message:
            self.force_btn.setEnabled(True)

    def _force_import(self):
        self.force_btn.setEnabled(False)
        self.worker.force_import()

    def _on_finished(self, path):
        self.cookie_path = path
        self.accept()

    def _on_failed(self, message):
        self.status_label.setText(message)
        self.force_btn.setEnabled(False)
        self.cancel_btn.setText("关闭")

    def _stop_worker(self, timeout_ms=12000):
        if not self.worker.isRunning():
            return True
        self.worker.cancel()
        return self.worker.wait(timeout_ms)

    def reject(self):
        if not self._stop_worker():
            self.status_label.setText("正在关闭浏览器，请稍后再试")
            return
        super().reject()

    def closeEvent(self, event):
        if not self._stop_worker():
            self.status_label.setText("正在关闭浏览器，请稍后再试")
            event.ignore()
            return
        event.accept()


class DouyinLoginDialog(CookieLoginDialog):
    def __init__(self, parent=None):
        super().__init__(
            parent=parent,
            site_label="抖音",
            start_url="https://www.douyin.com/",
            cookie_domains=("douyin.com", "iesdouyin.com"),
            output_path=str(cookie_store_path("douyin.com")),
            profile_key="douyin",
            login_cookie_names=("sessionid", "sessionid_ss", "sid_guard"),
            public_cookie_required=("ttwid", "s_v_web_id", "passport_csrf_token"),
        )
