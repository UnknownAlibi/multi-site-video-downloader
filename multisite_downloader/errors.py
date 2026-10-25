"""错误信息格式化与 B站错误分类。"""

from .config import BILI_ERROR_CATEGORIES


def format_error(exc):
    msg = str(exc)
    if not msg:
        msg = exc.__class__.__name__
    return msg


def format_bili_error(exc):
    """格式化错误信息：先按 B 站分类，再匹配通用站点错误。"""
    base = format_error(exc)
    category = classify_bili_error(exc) or classify_common_error(exc)
    if category:
        return f"[{category[0]}] {base}\n→ {category[1]}"
    return base


def classify_bili_error(exc):
    """对 B站错误进行分类，返回 (类别, 说明) 或 None。"""
    msg = str(exc)
    if "HTTP Error 412" in msg or "Precondition Failed" in msg:
        return ("风控拦截", "B站 412 风控，请配置 Cookie 或稍后再试。")
    if "HTTP Error 403" in msg:
        return ("权限不足", "HTTP 403，可能需要登录或大会员。")
    if "HTTP Error 404" in msg:
        return ("视频不存在", "HTTP 404，视频可能已被删除。")
    for code, category, hint in BILI_ERROR_CATEGORIES:
        if str(code) in msg:
            return (category, hint)
    return None


def format_site_error(exc, url):
    """在通用错误提示后追加对应站点的处理入口。"""
    message = format_bili_error(exc)
    try:
        from .sites import SITE_DOUYIN, SITE_YOUTUBE, detect_site

        site = detect_site(url)
    except Exception:
        return message
    if site == SITE_DOUYIN:
        lowered = message.lower()
        if "unsupported url" in lowered:
            message += (
                "\n→ 这是抖音精选/弹窗链接。程序会自动转换成 /video/<id>，"
                "如果仍然失败，请复制视频分享链接后重试。"
            )
        elif "cookie" in lowered or "fresh" in lowered or "403" in lowered:
            message += (
                "\n→ 设置 → 站点专用 Cookie → 抖音，"
                "点「一键登录抖音」并扫码登录，或选择包含 douyin.com 的 cookies.txt。"
            )
    elif site == SITE_YOUTUBE and (
        "bot" in message.lower() or "login" in message.lower()
    ):
        message += "\n→ 设置 → 站点专用 Cookie → YouTube，或检查代理设置。"
    return message


def classify_common_error(exc):
    """对 yt-dlp 常见跨站点错误给出可执行的提示。"""
    msg = str(exc).lower()
    if "fresh cookies" in msg or "cookies are needed" in msg:
        return (
            "需要 Cookie",
            "该站点要求有效的浏览器 Cookie。请先在浏览器访问/登录该站点，"
            "再使用 cookies.txt 或浏览器 Cookie 模式。",
        )
    if "not a bot" in msg or "sign in to confirm" in msg:
        return (
            "需要浏览器验证",
            "站点要求登录或人机验证。请在浏览器打开该视频后，"
            "使用浏览器 Cookie 模式重试。",
        )
    if (
        "failed to decrypt" in msg
        or "cookie decryption" in msg
        or "app-bound" in msg
        or "unable to get key" in msg
    ):
        return (
            "无法读取浏览器 Cookie",
            "Chrome / Edge 新版启用了 App-Bound Encryption，关闭浏览器也无法解决。"
            "请改用 cookies.txt 导出，或安装 Firefox 后使用 Firefox Cookie。",
        )
    if (
        "only works when logged-in" in msg
        or "login required" in msg
        or "logged-in" in msg
        or "log in to" in msg
    ):
        return (
            "需要登录",
            "该站点要求登录 Cookie。请在浏览器登录后导出 cookies.txt，"
            "或安装 Firefox 后使用 Firefox Cookie。",
        )
    if (
        "no video formats found" in msg
        or "there's no video" in msg
        or "no video could be found" in msg
    ):
        return (
            "没有可下载视频",
            "该链接可能是图文/纯音频，或页面需要登录 Cookie 才能拿到视频。",
        )
    if "no supported javascript runtime" in msg or "js runtime" in msg or "js challenge" in msg:
        return (
            "缺少 JS 运行时",
            "YouTube 等站点需要 Deno 或 Node.js。安装 Deno 并加入 PATH 后重启程序。",
        )
    if "drm" in msg or "protected by" in msg:
        return ("受保护内容", "该内容受 DRM 保护，暂不支持下载。")
    if "http error 403" in msg:
        return ("访问被拒绝", "站点拒绝了请求，可能需要 Cookie、登录或代理。")
    if "http error 404" in msg:
        return ("链接不存在", "页面不存在，链接可能已失效。")
    if (
        "video unavailable" in msg
        or "video is unavailable" in msg
        or "private video" in msg
        or "not available in your country" in msg
    ):
        return ("视频不可用", "视频可能已删除、私密、会员或受地区限制。")
    if "unsupported url" in msg or "no suitable extractor" in msg:
        return ("不支持的链接", "yt-dlp 没有该站点的提取器，或链接格式不正确。")
    if "requested format is not available" in msg:
        return ("格式不可用", "所选格式已失效，请重新解析后选择其他格式。")
    if (
        "unable to download webpage" in msg
        or "getaddrinfo" in msg
        or "timed out" in msg
        or "connection refused" in msg
        or "connection reset" in msg
        or "ssl" in msg
    ):
        return (
            "网络错误",
            "无法连接站点，请检查网络或代理设置。"
            "yt-dlp 不会自动使用 Windows 系统代理，需要在这里单独填写。",
        )
    return None
