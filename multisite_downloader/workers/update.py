"""程序更新检查与安装包下载线程。"""

from PyQt5.QtCore import QThread, pyqtSignal

from ..errors import format_error
from ..updater import download_update, fetch_latest_release


class UpdateCheckWorker(QThread):
    result = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings

    def run(self):
        try:
            info = fetch_latest_release(self.settings)
            self.result.emit(info)
        except Exception as exc:
            self.failed.emit(format_error(exc))


class UpdateDownloadWorker(QThread):
    progress = pyqtSignal(int, str)
    downloaded = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, info, settings, dest_dir=None, parent=None):
        super().__init__(parent)
        self.info = info
        self.settings = settings
        self.dest_dir = dest_dir
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def is_cancelled(self):
        return self._cancelled

    def run(self):
        try:
            path = download_update(
                self.info,
                self.settings,
                self.dest_dir,
                progress=self.progress.emit,
                cancelled=self.is_cancelled,
            )
            self.downloaded.emit(str(path))
        except Exception as exc:
            self.failed.emit(format_error(exc))
