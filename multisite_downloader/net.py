"""HTTP 请求头、yt-dlp 静默 logger、Cookie/代理选项注入。"""

import shutil
from pathlib import Path

from .cookies import ensure_youtube_browser_cookie, find_domain_cookie_file
from .sites import SITE_YOUTUBE, detect_site, site_cookie_key


def std_headers(url=None):
    """按目标链接生成请求头。

    只有 B 站链接才带 B 站 Referer；给 YouTube / 抖音等站点带 B 站 Referer
    容易触发防盗链或风控，所以其他站点不发送 Referer。
    url=None 时保留 B 站默认值，兼容 B 站内部接口调用。
    """
    is_bili = url is None or "bilibili.com" in str(url) or "b23.tv" in str(url)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if is_bili:
        headers["Referer"] = "https://www.bilibili.com/"
    return headers


class QuietYtdlpLogger:
    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def js_runtime_options():
    """yt-dlp 默认只启用 Deno；本机装有 Node 时也一起启用，改善 YouTube 解析。"""
    return {name: {} for name in ("deno", "node") if shutil.which(name)}


def apply_cookie_and_proxy_options(opts, settings, url=""):
    """为 yt-dlp 选项注入 Cookie 和代理。

    站点专用 Cookie 优先于全局设置：例如抖音单独一份 cookies.txt，
    就不会被 B 站的全局 Cookie 覆盖。
    """
    site_key = site_cookie_key(url)
    site_cookie = (settings.get(site_key) or "").strip() if site_key else ""
    if site_cookie and Path(site_cookie).exists():
        opts["cookiefile"] = site_cookie
    else:
        domain_cookie = find_domain_cookie_file(url)
        if domain_cookie is not None:
            opts["cookiefile"] = str(domain_cookie)
            mode = "none"
        else:
            mode = settings.get("cookie_mode") or "none"
        if mode == "file":
            cookie_file = (settings.get("cookie_file") or "").strip()
            if cookie_file and Path(cookie_file).exists():
                opts["cookiefile"] = cookie_file
        elif mode in ("chrome", "edge", "firefox"):
            opts["cookiesfrombrowser"] = (mode,)
        if (
            "cookiefile" not in opts
            and "cookiesfrombrowser" not in opts
            and detect_site(url) == SITE_YOUTUBE
        ):
            auto_cookie = ensure_youtube_browser_cookie(settings)
            if auto_cookie:
                opts["cookiefile"] = str(auto_cookie)

    proxy = (settings.get("proxy") or "").strip()
    if proxy:
        opts["proxy"] = proxy
    return opts
