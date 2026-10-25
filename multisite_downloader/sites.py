"""站点识别与 Cookie 域名映射。"""

from urllib.parse import urlparse


SITE_BILIBILI = "bilibili"
SITE_DOUYIN = "douyin"
SITE_YOUTUBE = "youtube"
SITE_VIMEO = "vimeo"
SITE_XHS = "xiaohongshu"
SITE_X = "x"
SITE_INSTAGRAM = "instagram"
SITE_FACEBOOK = "facebook"
SITE_TIKTOK = "tiktok"
SITE_WEIBO = "weibo"
SITE_GENERIC = "generic"

SITE_LABELS = {
    SITE_BILIBILI: "B站",
    SITE_DOUYIN: "抖音",
    SITE_YOUTUBE: "YouTube",
    SITE_VIMEO: "Vimeo",
    SITE_XHS: "小红书",
    SITE_X: "X / Twitter",
    SITE_INSTAGRAM: "Instagram",
    SITE_FACEBOOK: "Facebook",
    SITE_TIKTOK: "TikTok",
    SITE_WEIBO: "微博",
    SITE_GENERIC: "通用",
}

# 站点 -> 设置项里的专用 Cookie 文件路径（只有这三个有独立输入框）
SITE_COOKIE_KEYS = {
    SITE_BILIBILI: "site_cookie_bilibili",
    SITE_DOUYIN: "site_cookie_douyin",
    SITE_YOUTUBE: "site_cookie_youtube",
}

# 站点 -> 需要抓取/匹配的 Cookie 域名
SITE_COOKIE_DOMAINS = {
    SITE_BILIBILI: ("bilibili.com",),
    SITE_DOUYIN: ("douyin.com", "iesdouyin.com"),
    SITE_YOUTUBE: ("youtube.com",),
    SITE_VIMEO: ("vimeo.com",),
    SITE_XHS: ("xiaohongshu.com",),
    SITE_X: ("x.com", "twitter.com"),
    SITE_INSTAGRAM: ("instagram.com",),
    SITE_FACEBOOK: ("facebook.com",),
    SITE_TIKTOK: ("tiktok.com",),
    SITE_WEIBO: ("weibo.com",),
}

# 这些站点在没有 Cookie 时基本无法解析，解析前直接引导登录
COOKIE_REQUIRED_SITES = {
    SITE_DOUYIN,
    SITE_XHS,
    SITE_VIMEO,
    SITE_X,
    SITE_INSTAGRAM,
    SITE_FACEBOOK,
}

_MULTI_PART_SUFFIXES = {
    "com.cn",
    "net.cn",
    "org.cn",
    "gov.cn",
    "edu.cn",
    "com.hk",
    "com.tw",
    "co.uk",
    "org.uk",
    "co.jp",
    "co.kr",
    "com.au",
}


def _host_is(host, domain):
    return host == domain or host.endswith("." + domain)


def _url_host(url):
    try:
        host = (urlparse(str(url)).netloc or "").lower()
    except Exception:
        host = ""
    return host.split("@")[-1].split(":")[0].strip(".")


def detect_site(url):
    """根据域名判断站点；识别不了时走通用 yt-dlp。"""
    host = _url_host(url)
    if host in ("b23.tv", "bili2233.cn") or _host_is(host, "bilibili.com"):
        return SITE_BILIBILI
    if _host_is(host, "douyin.com") or _host_is(host, "iesdouyin.com"):
        return SITE_DOUYIN
    if host == "youtu.be" or _host_is(host, "youtube.com"):
        return SITE_YOUTUBE
    if _host_is(host, "vimeo.com"):
        return SITE_VIMEO
    if host == "xhslink.com" or _host_is(host, "xiaohongshu.com"):
        return SITE_XHS
    if _host_is(host, "x.com") or _host_is(host, "twitter.com") or host == "t.co":
        return SITE_X
    if _host_is(host, "instagram.com"):
        return SITE_INSTAGRAM
    if _host_is(host, "facebook.com") or host == "fb.watch":
        return SITE_FACEBOOK
    if _host_is(host, "tiktok.com"):
        return SITE_TIKTOK
    if _host_is(host, "weibo.com") or _host_is(host, "weibo.cn") or host == "t.cn":
        return SITE_WEIBO
    return SITE_GENERIC


def registrable_domain(host):
    """简单提取可注册域名，支持常见的双段后缀。"""
    host = (host or "").lower().strip(".")
    parts = [part for part in host.split(".") if part]
    if len(parts) <= 2:
        return ".".join(parts)
    if ".".join(parts[-2:]) in _MULTI_PART_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def cookie_domains_for_url(url):
    """返回该链接应匹配的 Cookie 域名列表（用于筛选和文件命名）。"""
    site = detect_site(url)
    domains = SITE_COOKIE_DOMAINS.get(site)
    if domains:
        return domains
    host = _url_host(url)
    domain = registrable_domain(host)
    return (domain,) if domain else ()


def cookie_domain_for_url(url):
    domains = cookie_domains_for_url(url)
    return domains[0] if domains else ""


def site_label(url):
    return SITE_LABELS.get(detect_site(url), "通用")


def site_cookie_key(url):
    return SITE_COOKIE_KEYS.get(detect_site(url), "")


def cookie_file_contains_domain(path, domain):
    """检查 Netscape cookies.txt 里是否包含指定域名的 Cookie。"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("#HttpOnly_"):
                    host = line[len("#HttpOnly_"):].split("\t", 1)[0]
                elif line.startswith("#"):
                    continue
                else:
                    host = line.split("\t", 1)[0]
                if domain.lower() in host.lower():
                    return True
    except OSError:
        return False
    return False
