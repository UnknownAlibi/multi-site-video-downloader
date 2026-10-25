"""代理连通性测试线程。"""

import time

import requests
from PyQt5.QtCore import QThread, pyqtSignal

from ..errors import format_error
from ..net import std_headers


class ProxyTestWorker(QThread):
    result = pyqtSignal(bool, str)

    def __init__(self, proxy, parent=None, timeout=10):
        super().__init__(parent)
        self.proxy = (proxy or "").strip()
        self.timeout = timeout

    def run(self):
        session = requests.Session()
        if self.proxy:
            session.proxies.update({"http": self.proxy, "https": self.proxy})
        started = time.monotonic()
        try:
            response = session.get(
                "https://www.bilibili.com/",
                headers=std_headers("https://www.bilibili.com/"),
                timeout=self.timeout,
                allow_redirects=True,
            )
            elapsed_ms = int((time.monotonic() - started) * 1000)
            response.raise_for_status()
            target = "代理" if self.proxy else "直连"
            self.result.emit(
                True,
                f"{target}连接成功 · HTTP {response.status_code} · {elapsed_ms} ms",
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            self.result.emit(
                False,
                f"连接失败（{elapsed_ms} ms）：{format_error(exc)}",
            )
        finally:
            session.close()
