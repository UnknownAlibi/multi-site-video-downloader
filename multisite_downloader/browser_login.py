"""通过 Chrome/Edge 的 CDP 获取站点 Cookie，不依赖浏览器扩展。"""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import requests
import websocket
from PyQt5.QtCore import QThread, pyqtSignal

from .config import BASE_DIR


def find_browser_executable():
    """返回 (浏览器名, 可执行文件路径)，找不到时返回 (None, None)。"""
    candidates = []
    for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = os.environ.get(env_name)
        if not root:
            continue
        candidates.extend(
            [
                (Path(root) / "Google/Chrome/Application/chrome.exe", "Chrome"),
                (Path(root) / "Microsoft/Edge/Application/msedge.exe", "Edge"),
            ]
        )
    for name in ("chrome", "chrome.exe", "msedge", "msedge.exe", "chromium", "google-chrome"):
        found = shutil.which(name)
        if found:
            candidates.append((Path(found), "Chrome" if "chrome" in name.lower() else "Edge"))
    for path, label in candidates:
        if path.is_file():
            return label, path
    return None, None


def open_in_login_browser(url, profile_key="douyin"):
    """用已登录的独立浏览器配置打开页面（不启用调试端口）。"""
    label, executable = find_browser_executable()
    if not executable:
        return False
    try:
        subprocess.Popen(
            [
                str(executable),
                f"--user-data-dir={_profile_dir(profile_key)}",
                "--no-first-run",
                "--no-default-browser-check",
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def _free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _app_data_root():
    root = (
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("APPDATA")
        or tempfile.gettempdir()
    )
    current = Path(root) / "multisite-downloader"
    if current.exists():
        return current
    # 保留旧目录中的浏览器登录状态，避免升级后要求重新登录
    legacy = Path(root) / "downbili"
    if legacy.exists():
        return legacy
    return current


def _profile_dir(profile_key="douyin"):
    safe = "".join(ch for ch in str(profile_key) if ch.isalnum() or ch in "-_")
    safe = safe or "default"
    return _app_data_root() / "browser_profiles" / safe


def cookie_output_path():
    """抖音 Cookie 的默认导出位置。"""
    candidates = [
        BASE_DIR / "douyin_cookies.txt",
        _app_data_root() / "douyin_cookies.txt",
    ]
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            continue
    return Path(tempfile.gettempdir()) / "douyin_cookies.txt"


def _wait_cdp(port, timeout=20, should_cancel=None):
    deadline = time.time() + timeout
    endpoint = f"http://127.0.0.1:{port}/json/version"
    while time.time() < deadline:
        if should_cancel is not None and should_cancel():
            return ""
        try:
            response = requests.get(endpoint, timeout=1)
            if response.ok:
                return response.json().get("webSocketDebuggerUrl") or ""
        except Exception:
            pass
        time.sleep(0.25)
    return ""


def _cdp_call(ws, msg_id, method, params=None):
    ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
    while True:
        message = json.loads(ws.recv())
        if message.get("id") != msg_id:
            continue
        if "error" in message:
            raise RuntimeError(message["error"].get("message") or "CDP 调用失败")
        return message.get("result") or {}


def filter_cookies(cookies, domains):
    domains = tuple(domain.lower() for domain in domains or ())
    result = []
    for cookie in cookies or []:
        cookie_domain = str(cookie.get("domain") or "").lower()
        if any(domain in cookie_domain for domain in domains):
            result.append(cookie)
    return result


def filter_douyin_cookies(cookies):
    return filter_cookies(cookies, ("douyin.com", "iesdouyin.com"))


def has_login_cookies(cookies, names=("sessionid", "sessionid_ss", "sid_guard")):
    found = {str(cookie.get("name") or "") for cookie in cookies or []}
    return bool(found & set(names))


def has_public_cookies(
    cookies,
    required=("ttwid", "s_v_web_id"),
):
    found = {str(cookie.get("name") or "") for cookie in cookies or []}
    return bool(found) and set(required).issubset(found)


def _cookie_line(cookie):
    domain = str(cookie.get("domain") or "")
    include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
    path = str(cookie.get("path") or "/").replace("\t", " ")
    secure = "TRUE" if cookie.get("secure") else "FALSE"
    try:
        expires = int(float(cookie.get("expires") or 0))
    except (TypeError, ValueError):
        expires = 0
    if expires < 0:
        expires = 0
    name = str(cookie.get("name") or "").replace("\t", " ").replace("\n", " ")
    value = str(cookie.get("value") or "").replace("\t", " ").replace("\n", " ")
    prefix = "#HttpOnly_" if cookie.get("httpOnly") else ""
    return (
        f"{prefix}{domain}\t{include_subdomains}\t{path}\t{secure}\t"
        f"{expires}\t{name}\t{value}"
    )


def write_netscape_cookies(cookies, path):
    lines = [
        "# Netscape HTTP Cookie File",
        "# Generated by Multi-site Video Downloader",
        "",
    ]
    lines.extend(_cookie_line(cookie) for cookie in cookies if cookie.get("name"))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _page_ws_url(port, match=None):
    try:
        response = requests.get(f"http://127.0.0.1:{port}/json/list", timeout=2)
        pages = [
            target for target in response.json()
            if target.get("type") == "page" and target.get("webSocketDebuggerUrl")
        ]
        if match:
            for target in pages:
                if match in (target.get("url") or ""):
                    return target["webSocketDebuggerUrl"]
        for target in pages:
            return target["webSocketDebuggerUrl"]
    except Exception:
        pass
    return ""


def _read_cookies(ws, use_page_api=False):
    if use_page_api:
        _cdp_call(ws, 99, "Network.enable")
        result = _cdp_call(ws, 100, "Network.getAllCookies")
    else:
        result = _cdp_call(ws, 100, "Storage.getCookies")
    return result.get("cookies") or []


class BrowserCookieLoginWorker(QThread):
    """打开独立浏览器，等待用户登录后把指定域名的 Cookie 导出到文件。"""

    status = pyqtSignal(str)
    cookies_ready = pyqtSignal()
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(
        self,
        site_label="抖音",
        start_url="https://www.douyin.com/",
        cookie_domains=("douyin.com", "iesdouyin.com"),
        output_path=None,
        login_cookie_names=("sessionid", "sessionid_ss", "sid_guard"),
        public_cookie_required=("ttwid", "s_v_web_id"),
        profile_key="douyin",
        parent=None,
    ):
        super().__init__(parent)
        self.site_label = site_label
        self.start_url = start_url
        self.cookie_domains = tuple(cookie_domains)
        self.output_path = Path(output_path) if output_path else None
        self.login_cookie_names = tuple(login_cookie_names)
        self.public_cookie_required = tuple(public_cookie_required)
        self.profile_key = profile_key
        self._cancelled = False
        self._force_import = False
        self._proc = None

    def force_import(self):
        """用户点击「使用当前 Cookie」时提前结束等待。"""
        self._force_import = True

    def cancel(self):
        self._cancelled = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()

    def _has_login(self, cookies):
        if not self.login_cookie_names:
            return False
        return has_login_cookies(cookies, self.login_cookie_names)

    def _has_public(self, cookies):
        if not cookies:
            return False
        if not self.public_cookie_required:
            return True
        return has_public_cookies(cookies, self.public_cookie_required)

    def run(self):
        label, executable = find_browser_executable()
        if not executable:
            self.failed.emit(
                "没有找到 Chrome 或 Edge。\n"
                "请安装其中之一，或改为手动选择 cookies.txt。"
            )
            return

        port = _free_port()
        self.status.emit(f"正在启动 {label}...")
        args = [
            str(executable),
            f"--remote-debugging-port={port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={_profile_dir(self.profile_key)}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-session-crashed-bubble",
            self.start_url,
        ]
        try:
            self._proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            self.failed.emit(f"启动浏览器失败：{exc}")
            return

        ws = None
        try:
            self.status.emit(f"{label} 已启动，正在等待调试端口...")
            ws_url = _wait_cdp(
                port, timeout=20, should_cancel=lambda: self._cancelled
            )
            if self._cancelled:
                return
            if not ws_url:
                self.failed.emit(
                    "浏览器调试端口没有就绪。\n"
                    "可能是浏览器策略禁用了远程调试，或已经有登录窗口在运行。\n"
                    "请关闭所有登录窗口后重试。"
                )
                return
            ws = websocket.create_connection(ws_url, timeout=5)
            self.status.emit(
                f"请在打开的浏览器中登录{self.site_label}。\n"
                "登录完成后点击「使用当前 Cookie」；抖音会自动检测登录。"
            )
            deadline = time.time() + 120
            cookies = []
            last_count = -1
            public_ready = False
            while not self._cancelled and time.time() < deadline:
                try:
                    cookies = filter_cookies(_read_cookies(ws), self.cookie_domains)
                except Exception:
                    page_ws = _page_ws_url(port)
                    if page_ws:
                        try:
                            if ws:
                                ws.close()
                            ws = websocket.create_connection(page_ws, timeout=5)
                            cookies = filter_cookies(
                                _read_cookies(ws, use_page_api=True),
                                self.cookie_domains,
                            )
                        except Exception:
                            pass
                if len(cookies) != last_count:
                    last_count = len(cookies)
                    self.status.emit(f"已获取 {len(cookies)} 个 {self.site_label} Cookie...")
                if self._has_login(cookies):
                    break
                if self._has_public(cookies) and not public_ready:
                    public_ready = True
                    self.cookies_ready.emit()
                    self.status.emit(
                        "已获取站点 Cookie。\n"
                        "建议完成登录；也可以点击「使用当前 Cookie」先导入。"
                    )
                if self._force_import:
                    break
                time.sleep(1.2)

            if self._cancelled:
                return
            if not cookies:
                self.failed.emit(
                    f"没有获取到 {self.site_label} Cookie。\n"
                    "请确认浏览器里能正常打开该站点后重试。"
                )
                return

            path = self.output_path or cookie_output_path()
            path = write_netscape_cookies(cookies, path)
            self.status.emit(f"已获取 {len(cookies)} 个 Cookie，正在导入...")
            self.finished_ok.emit(str(path))
        except Exception as exc:
            self.failed.emit(f"获取 Cookie 失败：{exc}")
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
            proc = self._proc
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()


class DouyinBrowserLoginWorker(BrowserCookieLoginWorker):
    """抖音专用参数：等待登录 Cookie，或公开 Cookie 就绪后允许手动导入。"""

    def __init__(self, parent=None, output_path=None):
        super().__init__(
            site_label="抖音",
            start_url="https://www.douyin.com/",
            cookie_domains=("douyin.com", "iesdouyin.com"),
            output_path=output_path,
            login_cookie_names=("sessionid", "sessionid_ss", "sid_guard"),
            public_cookie_required=("ttwid", "s_v_web_id", "passport_csrf_token"),
            profile_key="douyin",
            parent=parent,
        )
