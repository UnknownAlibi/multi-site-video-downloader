"""Cookie 有效性检测线程。"""

from http.cookiejar import MozillaCookieJar
from pathlib import Path

import requests

from PyQt5.QtCore import QThread, pyqtSignal

from ..config import BILIBILI_NAV_API
from ..cookies import find_domain_cookie_file
from ..errors import format_error
from ..logs import write_crash_log
from ..net import std_headers


class CookieCheckWorker(QThread):
    result_ready = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings

    def run(self):
        try:
            cookies = self.load_cookies()
            if cookies is None:
                self.result_ready.emit({"logged_in": False, "reason": "无法读取 Cookie"})
                return
            session = requests.Session()
            session.cookies.update(cookies)
            proxy = (self.settings.get("proxy") or "").strip()
            if proxy:
                session.proxies.update({"http": proxy, "https": proxy})
            response = session.get(
                BILIBILI_NAV_API,
                headers=std_headers("https://www.bilibili.com/"),
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") or {}
            is_logged = bool(data.get("isLogin"))
            result = {
                "logged_in": is_logged,
                "username": data.get("uname") or "",
                "mid": data.get("mid") or "",
                "vip_type": data.get("vipType") or 0,
                "vip_status": data.get("vipStatus") or 0,
                "sessdata_found": any(c.name == "SESSDATA" for c in session.cookies),
            }
            self.result_ready.emit(result)
        except Exception as exc:
            try:
                self.failed.emit(format_error(exc))
            except Exception as top_exc:
                write_crash_log(type(top_exc), top_exc, top_exc.__traceback__,
                                source="CookieCheckWorker")

    @staticmethod
    def _load_cookie_file(path):
        if not path or not Path(path).exists():
            return None
        try:
            cj = MozillaCookieJar(path)
            cj.load(ignore_discard=True, ignore_expires=True)
            return {c.name: c.value for c in cj}
        except Exception:
            return None

    def load_cookies(self):
        # B站专用文件优先；没有或不存在时再退回全局设置
        site_file = (self.settings.get("site_cookie_bilibili") or "").strip()
        if site_file and Path(site_file).exists():
            cookies = self._load_cookie_file(site_file)
            if cookies is not None:
                return cookies

        domain_file = find_domain_cookie_file("https://www.bilibili.com/")
        if domain_file is not None:
            cookies = self._load_cookie_file(str(domain_file))
            if cookies is not None:
                return cookies

        mode = self.settings.get("cookie_mode") or "none"
        if mode == "none":
            return {}
        if mode == "file":
            return self._load_cookie_file(
                (self.settings.get("cookie_file") or "").strip()
            )
        try:
            import browser_cookie3
        except ImportError as exc:
            raise RuntimeError(
                "未安装 browser_cookie3，无法读取浏览器 Cookie。\n"
                "请执行 py -m pip install browser-cookie3 后重试，"
                "或改用 cookies.txt 文件方式。"
            ) from exc
        try:
            func = {"chrome": browser_cookie3.chrome,
                    "edge": browser_cookie3.edge,
                    "firefox": browser_cookie3.firefox}.get(mode)
            if not func:
                return None
            cj = func(domain_name="bilibili.com")
            return {c.name: c.value for c in cj if "bilibili.com" in c.domain}
        except Exception as exc:
            msg = format_error(exc)
            lowered = msg.lower()
            if (
                "unable to get key" in lowered
                or "dpapi" in lowered
                or "decrypt" in lowered
                or "app-bound" in lowered
            ):
                raise RuntimeError(
                    f"读取浏览器 Cookie 失败：{msg}\n"
                    "Chrome / Edge 新版本启用了 App-Bound Encryption，"
                    "第三方程序无法解密 Cookie，关闭浏览器也无法解决。\n"
                    "请改用 cookies.txt（用浏览器扩展导出），"
                    "或安装 Firefox 登录后选择“读取 Firefox Cookie”。"
                ) from exc
            raise RuntimeError(
                f"读取浏览器 Cookie 失败：{msg}\n"
                "请先彻底关闭浏览器（Chrome/Edge 会锁定 Cookie 数据库）后重试，"
                "或改用 cookies.txt 文件方式。"
            ) from exc
