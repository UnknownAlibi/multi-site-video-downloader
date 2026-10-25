"""按域名存放 Cookie 文件：cookies/<domain>.txt。"""

import os
import tempfile
import threading
import time
from http.cookiejar import CookieJar, MozillaCookieJar
from pathlib import Path

from .config import BASE_DIR
from .sites import (
    cookie_domain_for_url,
    cookie_domains_for_url,
    cookie_file_contains_domain,
    site_cookie_key,
)


_STORE_OVERRIDE = None


def cookie_store_dir():
    global _STORE_OVERRIDE
    if _STORE_OVERRIDE is not None:
        path = Path(_STORE_OVERRIDE)
        path.mkdir(parents=True, exist_ok=True)
        return path
    candidates = [
        BASE_DIR / "cookies",
        Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
        / "multisite-downloader"
        / "cookies",
    ]
    for path in candidates:
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            continue
    fallback = Path(tempfile.gettempdir()) / "multisite-downloader_cookies"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def cookie_store_path(domain):
    safe = "".join(ch for ch in str(domain) if ch.isalnum() or ch in ".-_")
    return cookie_store_dir() / f"{safe}.txt"


def find_domain_cookie_file(url):
    """按 URL 查找 cookie 库里的文件；没有时返回 None。"""
    for domain in cookie_domains_for_url(url):
        path = cookie_store_path(domain)
        if path.is_file():
            return path
    return None


def list_domain_cookies():
    """返回 [(域名, 文件路径)]，用于设置页展示。"""
    store = cookie_store_dir()
    items = []
    for path in sorted(store.glob("*.txt")):
        if path.is_file():
            items.append((path.stem, path))
    return items


def domain_cookie_details():
    """返回 Cookie 库明细，按最后更新时间倒序。"""
    details = []
    for domain, path in list_domain_cookies():
        try:
            stat = path.stat()
        except OSError:
            continue
        cookie_count = 0
        try:
            jar = MozillaCookieJar(str(path))
            jar.load(ignore_discard=True, ignore_expires=True)
            cookie_count = len(jar)
        except Exception:
            cookie_count = 0
        details.append(
            {
                "domain": domain,
                "path": str(path),
                "size": stat.st_size,
                "updated_at": stat.st_mtime,
                "cookie_count": cookie_count,
            }
        )
    details.sort(key=lambda item: item["updated_at"], reverse=True)
    return details


def domain_cookie_path(url):
    domain = cookie_domain_for_url(url)
    return cookie_store_path(domain) if domain else None


def has_cookie_for_url(url, settings):
    """判断当前配置里是否已经有该站点的 Cookie。"""
    domains = cookie_domains_for_url(url)
    if not domains:
        return False

    site_key = site_cookie_key(url)
    explicit = (settings.get(site_key) or "").strip() if site_key else ""
    if (
        explicit
        and Path(explicit).is_file()
        and any(cookie_file_contains_domain(explicit, domain) for domain in domains)
    ):
        return True

    if find_domain_cookie_file(url) is not None:
        return True

    mode = settings.get("cookie_mode") or "none"
    if mode in ("chrome", "edge", "firefox"):
        # 浏览器模式无法提前判断，交给 yt-dlp 尝试
        return True
    if mode == "file":
        cookie_file = (settings.get("cookie_file") or "").strip()
        if (
            cookie_file
            and Path(cookie_file).is_file()
            and any(
                cookie_file_contains_domain(cookie_file, domain)
                for domain in domains
            )
        ):
            return True
    return False


def remove_domain_cookie(domain):
    path = cookie_store_path(domain)
    try:
        path.unlink()
        return True
    except OSError:
        return False


def save_cookie_jar(domain, cookies):
    """把浏览器读取到的 Cookie 保存到 Cookie 库，便于下次直接复用。"""
    path = cookie_store_path(domain)
    jar = MozillaCookieJar(str(path))
    saved = 0
    for cookie in cookies or []:
        try:
            jar.set_cookie(cookie)
            saved += 1
        except Exception:
            continue
    if not saved:
        return None
    try:
        jar.save(ignore_discard=True, ignore_expires=True)
    except Exception:
        return None
    return path


_YOUTUBE_AUTH_COOKIES = {
    "SID",
    "HSID",
    "SSID",
    "APISID",
    "SAPISID",
    "LOGIN_INFO",
    "__Secure-1PSID",
    "__Secure-3PSID",
    "__Secure-1PAPISID",
    "__Secure-3PAPISID",
}
_BROWSER_COOKIE_LOCK = threading.Lock()
_BROWSER_COOKIE_ATTEMPTS = {}
_BROWSER_COOKIE_FAIL_TTL = 600


def _youtube_browser_cookie_jar():
    """静默读取浏览器中 YouTube/Google 登录 Cookie；失败返回 None。"""
    try:
        import browser_cookie3
    except ImportError:
        return None
    loaders = (
        ("Firefox", browser_cookie3.firefox),
        ("Edge", browser_cookie3.edge),
        ("Chrome", browser_cookie3.chrome),
    )
    for label, loader in loaders:
        jar = CookieJar()
        try:
            for domain in ("youtube.com", "google.com"):
                try:
                    part = loader(domain_name=domain)
                except Exception:
                    continue
                for cookie in part or []:
                    jar.set_cookie(cookie)
        except Exception:
            continue
        if any(cookie.name in _YOUTUBE_AUTH_COOKIES for cookie in jar):
            return label, jar
    return None


def ensure_youtube_browser_cookie(settings):
    """YouTube 没有配置 Cookie 时，尝试静默复用浏览器登录并落盘。

    只做一次成功/失败尝试（失败缓存 10 分钟），避免每个任务都去读浏览器数据库。
    """
    if not settings or settings.get("auto_browser_login") is not True:
        return None
    if (settings.get("site_cookie_youtube") or "").strip():
        return None
    if find_domain_cookie_file("https://www.youtube.com/") is not None:
        return None
    now = time.time()
    with _BROWSER_COOKIE_LOCK:
        attempted = _BROWSER_COOKIE_ATTEMPTS.get("youtube.com")
        if attempted and now - attempted < _BROWSER_COOKIE_FAIL_TTL:
            return None
        _BROWSER_COOKIE_ATTEMPTS["youtube.com"] = now
    found = _youtube_browser_cookie_jar()
    if not found:
        return None
    label, jar = found
    saved = save_cookie_jar("youtube.com", jar)
    if saved:
        with _BROWSER_COOKIE_LOCK:
            _BROWSER_COOKIE_ATTEMPTS.pop("youtube.com", None)
        try:
            from .logs import write_runtime_log

            write_runtime_log(
                f"已静默复用浏览器 {label} 的 YouTube 登录，Cookie 已保存到 {saved}"
            )
        except Exception:
            pass
    return saved
