"""修改单个队列任务的下次下载设置。"""

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..config import DEFAULT_SETTINGS, QUALITY_LABELS


class TaskSettingsDialog(QDialog):
    """只修改等待/失败/已取消任务，不影响其他任务。"""

    def __init__(self, task, parent=None):
        super().__init__(parent)
        self.task = task
        self._settings = dict(task.get("settings") or DEFAULT_SETTINGS)
        self.setWindowTitle("修改任务设置")
        self.setMinimumWidth(520)
        self._build_ui()
        self._load()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel("修改任务设置")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        url = QLabel(self.task.get("url") or "")
        url.setObjectName("hintText")
        url.setWordWrap(True)
        url.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(url)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)

        grid.addWidget(QLabel("清晰度"), 0, 0)
        self.quality_combo = QComboBox()
        for value, label in QUALITY_LABELS.items():
            self.quality_combo.addItem(label, value)
        grid.addWidget(self.quality_combo, 0, 1)

        grid.addWidget(QLabel("保存目录"), 1, 0)
        dir_row = QHBoxLayout()
        self.dir_edit = QLineEdit()
        self.dir_edit.setReadOnly(True)
        dir_btn = QPushButton("浏览")
        dir_btn.setObjectName("secondaryBtn")
        dir_btn.clicked.connect(self._choose_dir)
        dir_row.addWidget(self.dir_edit, 1)
        dir_row.addWidget(dir_btn)
        grid.addLayout(dir_row, 1, 1)

        layout.addLayout(grid)

        self.custom_note = QLabel("")
        self.custom_note.setObjectName("hintText")
        self.custom_note.setWordWrap(True)
        layout.addWidget(self.custom_note)

        self.thumbnail_check = QCheckBox("下载封面")
        self.subtitle_check = QCheckBox("下载字幕")
        self.danmaku_check = QCheckBox("下载弹幕（B站）")
        extra_row = QHBoxLayout()
        extra_row.addWidget(self.thumbnail_check)
        extra_row.addWidget(self.subtitle_check)
        extra_row.addWidget(self.danmaku_check)
        extra_row.addStretch()
        layout.addLayout(extra_row)

        hint = QLabel("修改清晰度会清除该任务单独选中的格式，避免格式覆盖清晰度。")
        hint.setObjectName("hintText")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("secondaryBtn")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("保存")
        save_btn.setObjectName("primaryBtn")
        save_btn.clicked.connect(self._save)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)
        layout.addLayout(buttons)

    def _load(self):
        quality = str(self._settings.get("quality") or "best")
        index = self.quality_combo.findData(quality)
        if index < 0 and quality.isdigit():
            self.quality_combo.addItem(f"{quality}P", quality)
            index = self.quality_combo.findData(quality)
        if index >= 0:
            self.quality_combo.setCurrentIndex(index)
        self.dir_edit.setText(
            self._settings.get("download_dir") or str(DEFAULT_SETTINGS["download_dir"])
        )
        self.thumbnail_check.setChecked(bool(self._settings.get("download_thumbnail")))
        self.subtitle_check.setChecked(bool(self._settings.get("download_subtitle")))
        self.danmaku_check.setChecked(bool(self._settings.get("download_danmaku")))
        custom_format = (self._settings.get("custom_format") or "").strip()
        self.custom_note.setText(
            f"当前格式覆盖：{custom_format}；保存后会改为按清晰度自动选择。"
            if custom_format
            else ""
        )
        self.custom_note.setVisible(bool(custom_format))

    def _choose_dir(self):
        path = QFileDialog.getExistingDirectory(
            self, "选择保存目录", self.dir_edit.text()
        )
        if path:
            self.dir_edit.setText(path)

    def _save(self):
        directory = self.dir_edit.text().strip()
        if not directory:
            QMessageBox.information(self, "提示", "保存目录不能为空。")
            return
        if not Path(directory).is_dir():
            try:
                Path(directory).mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                QMessageBox.warning(self, "目录不可用", f"无法创建保存目录：\n{exc}")
                return
        self._settings.update(
            {
                "quality": self.quality_combo.currentData(),
                "download_dir": directory,
                "custom_format": "",
                "custom_format_source": "",
                "download_thumbnail": self.thumbnail_check.isChecked(),
                "download_subtitle": self.subtitle_check.isChecked(),
                "download_danmaku": self.danmaku_check.isChecked(),
            }
        )
        self.accept()

    def result_settings(self):
        return dict(self._settings)
