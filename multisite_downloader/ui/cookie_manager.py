"""Cookie 库管理对话框：查看、打开和删除按域名保存的 Cookie。"""

import time

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..cookies import (
    cookie_store_dir,
    domain_cookie_details,
    remove_domain_cookie,
)
from ..shell import open_file_default, open_path_in_explorer
from ..utils import format_bytes


class CookieManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.deleted = False
        self.deleted_domains = []
        self.setWindowTitle("Cookie 库管理")
        self.resize(800, 460)
        self._build_ui()
        self.reload()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title = QLabel("Cookie 库")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        hint = QLabel(
            "按域名保存在 cookies/<域名>.txt，解析和下载时会自动匹配。\n"
            "Cookie 包含登录凭据，请勿分享文件；删除后下次解析会重新要求登录。"
        )
        hint.setObjectName("hintText")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["域名", "Cookie 数", "大小", "最后更新"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 4):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeToContents
            )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.doubleClicked.connect(lambda _index: self.open_file())
        layout.addWidget(self.table, 1)

        self.count_label = QLabel("")
        self.count_label.setObjectName("hintText")
        layout.addWidget(self.count_label)

        buttons = QHBoxLayout()
        self.open_file_btn = QPushButton("打开文件")
        self.open_file_btn.setObjectName("secondaryBtn")
        self.open_file_btn.clicked.connect(self.open_file)
        self.open_dir_btn = QPushButton("打开 Cookie 目录")
        self.open_dir_btn.setObjectName("secondaryBtn")
        self.open_dir_btn.clicked.connect(self.open_dir)
        self.delete_btn = QPushButton("删除")
        self.delete_btn.setObjectName("secondaryBtn")
        self.delete_btn.clicked.connect(self.delete_selected)
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("primaryBtn")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(self.open_file_btn)
        buttons.addWidget(self.open_dir_btn)
        buttons.addWidget(self.delete_btn)
        buttons.addStretch()
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    def reload(self):
        details = domain_cookie_details()
        self.table.setRowCount(0)
        for item in details:
            row = self.table.rowCount()
            self.table.insertRow(row)
            domain_item = QTableWidgetItem(item["domain"])
            domain_item.setData(Qt.UserRole, item["path"])
            self.table.setItem(row, 0, domain_item)
            self.table.setItem(
                row, 1, QTableWidgetItem(str(item.get("cookie_count") or 0))
            )
            self.table.setItem(row, 2, QTableWidgetItem(format_bytes(item["size"])))
            updated = time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(item["updated_at"])
            )
            self.table.setItem(row, 3, QTableWidgetItem(updated))
        self.count_label.setText(
            f"共 {len(details)} 个站点 · {cookie_store_dir()}"
            if details
            else "Cookie 库为空。登录 B站/抖音或为任意站点获取 Cookie 后会出现在这里。"
        )

    def _selected_path(self):
        row = self.table.currentRow()
        if row < 0:
            return ""
        item = self.table.item(row, 0)
        return str(item.data(Qt.UserRole) or "") if item else ""

    def open_file(self):
        path = self._selected_path()
        if not path:
            QMessageBox.information(self, "提示", "请先选择一个 Cookie 文件。")
            return
        open_file_default(path)

    def open_dir(self):
        open_path_in_explorer(str(cookie_store_dir()))

    def delete_selected(self):
        row = self.table.currentRow()
        path = self._selected_path()
        if not path:
            QMessageBox.information(self, "提示", "请先选择要删除的 Cookie。")
            return
        domain = self.table.item(row, 0).text()
        ret = QMessageBox.question(
            self,
            "删除 Cookie",
            f"确定删除 {domain} 的 Cookie 吗？\n"
            "删除后该站点的登录状态会失效，下次可能需要重新登录。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        if not remove_domain_cookie(domain):
            QMessageBox.warning(self, "删除失败", "无法删除该 Cookie 文件。")
            return
        self.deleted = True
        self.deleted_domains.append(domain)
        self.reload()
