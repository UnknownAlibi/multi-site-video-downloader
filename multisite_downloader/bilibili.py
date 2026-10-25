"""B站 Web API：视频信息、播放地址、Cookie 会话、弹幕。"""

import re
import threading
import time
import zlib
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from urllib.parse import urlparse

import requests

from .config import (
    BILIBILI_DM_LIST_API,
    BILIBILI_NAV_API,
    BILIBILI_PLAYER_V2_API,
    BILIBILI_PLAYURL_API,
    BILIBILI_VIEW_API,
)
from .cookies import find_domain_cookie_file, save_cookie_jar
from .errors import format_error
from .logs import write_runtime_log
from .net import std_headers
from .urls import extract_video_id, is_bilibili_url, selected_page_number
from .utils import unique_path


def _load_cookie_jar(path):
    """读取 Netscape 格式 Cookie 文件，失败时返回 None。"""
    if not path:
        return None
    try:
        if not Path(path).is_file():
            return None
        jar = MozillaCookieJar(str(path))
        jar.load(ignore_discard=True, ignore_expires=True)
        # RequestsCookieJar 提供 get(domain=...) 等接口，直接给 Session 使用更稳妥
        result = requests.cookies.RequestsCookieJar()
        for cookie in jar:
            result.set_cookie(cookie)
        return result
    except Exception:
        return None


def _build_bili_session(settings):
    """构建带 Cookie 和代理的 B站请求 session。

    B站接口和兜底下载必须和 yt-dlp 使用同一套 Cookie 来源：
    站点专用 Cookie > cookies/<域名>.txt > 全局 cookies.txt / 浏览器。
    旧实现只认全局 cookie_mode，导致设置页里的 B站专用 Cookie 对兜底无效。
    """
    session = requests.Session()
    proxy = (settings.get("proxy") or "").strip()
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})

    cookie_loaded = False
    explicit = (settings.get("site_cookie_bilibili") or "").strip()
    domain_file = find_domain_cookie_file("https://www.bilibili.com/")
    for candidate in (explicit, str(domain_file) if domain_file else ""):
        if not candidate:
            continue
        jar = _load_cookie_jar(candidate)
        if jar is not None and len(jar) > 0:
            session.cookies = jar
            cookie_loaded = True
            break

    if not cookie_loaded:
        mode = settings.get("cookie_mode") or "none"
        if mode == "file":
            cookie_file = (settings.get("cookie_file") or "").strip()
            jar = _load_cookie_jar(cookie_file)
            if jar is not None:
                session.cookies = jar
        elif mode in ("chrome", "edge", "firefox"):
            try:
                import browser_cookie3
                func = {"chrome": browser_cookie3.chrome,
                        "edge": browser_cookie3.edge,
                        "firefox": browser_cookie3.firefox}.get(mode)
                if func:
                    cj = func(domain_name="bilibili.com")
                    session.cookies = cj
            except ImportError:
                write_runtime_log("未安装 browser_cookie3，浏览器 Cookie 模式不可用（请安装或改用 cookies.txt）。")
            except Exception as exc:
                write_runtime_log(f"读取浏览器 Cookie 失败: {format_error(exc)}")
    session.headers.update(std_headers())
    return session


_BILI_LOGIN_CACHE = {"key": None, "time": 0.0, "ok": False, "source": ""}
_BILI_LOGIN_CACHE_LOCK = threading.Lock()
_BILI_LOGIN_RESOLVE_LOCK = threading.Lock()
_BILI_LOGIN_TTL = 300.0
_BILI_LOGIN_NEGATIVE_TTL = 30.0


def invalidate_bili_login_cache():
    with _BILI_LOGIN_CACHE_LOCK:
        _BILI_LOGIN_CACHE.update(
            {"key": None, "time": 0.0, "ok": False, "source": ""}
        )


def _bili_login_cache_key(settings):
    return (
        settings.get("cookie_mode") or "none",
        settings.get("cookie_file") or "",
        settings.get("site_cookie_bilibili") or "",
        settings.get("proxy") or "",
    )

def _bili_session_with_jar(settings, jar):
    session = requests.Session()
    proxy = (settings.get("proxy") or "").strip()
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    if jar is not None:
        session.cookies = jar
    session.headers.update(std_headers())
    return session


def bili_login_status(session):
    """请求 nav 接口，返回 (是否登录, data)。"""
    try:
        response = session.get(BILIBILI_NAV_API, timeout=15)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or {}
        return bool(payload.get("code") == 0 and data.get("isLogin")), data
    except Exception:
        return False, {}


def resolve_bili_login(settings, allow_browser=True):
    """返回可用的B站登录状态：(是否登录, 来源说明)。

    先验证当前配置的文件/Cookie库/浏览器模式；都无效时再静默尝试
    本机 Firefox / Edge / Chrome 的 bilibili.com Cookie。浏览器登录有效时
    会保存到 Cookie 库，后续启动直接复用，不再反复读取浏览器。
    """
    settings = settings or {}
    key = _bili_login_cache_key(settings) + (bool(allow_browser),)
    now = time.time()
    with _BILI_LOGIN_CACHE_LOCK:
        cached = dict(_BILI_LOGIN_CACHE)
        if cached["key"] == key:
            ttl = _BILI_LOGIN_TTL if cached["ok"] else _BILI_LOGIN_NEGATIVE_TTL
            if now - cached["time"] < ttl:
                return cached["ok"], cached["source"]

    with _BILI_LOGIN_RESOLVE_LOCK:
        now = time.time()
        with _BILI_LOGIN_CACHE_LOCK:
            cached = dict(_BILI_LOGIN_CACHE)
            if cached["key"] == key:
                ttl = _BILI_LOGIN_TTL if cached["ok"] else _BILI_LOGIN_NEGATIVE_TTL
                if now - cached["time"] < ttl:
                    return cached["ok"], cached["source"]

        session = _build_bili_session(settings)
        ok, _data = bili_login_status(session)
        source = "当前配置"
        if not ok and allow_browser:
            try:
                import browser_cookie3
            except ImportError:
                browser_cookie3 = None
                write_runtime_log(
                    "未安装 browser_cookie3，无法静默复用浏览器登录。"
                )
            if browser_cookie3 is not None:
                for label, loader in (
                    ("Firefox", browser_cookie3.firefox),
                    ("Edge", browser_cookie3.edge),
                    ("Chrome", browser_cookie3.chrome),
                ):
                    try:
                        jar = loader(domain_name="bilibili.com")
                    except Exception:
                        continue
                    if not jar or len(jar) == 0:
                        continue
                    browser_session = _bili_session_with_jar(settings, jar)
                    ok, _data = bili_login_status(browser_session)
                    if not ok:
                        continue
                    saved = save_cookie_jar("bilibili.com", jar)
                    source = f"浏览器 {label}"
                    write_runtime_log(
                        f"已静默复用 {label} 的B站登录"
                        + (f"，Cookie 已保存到 {saved}" if saved else "")
                    )
                    break

        with _BILI_LOGIN_CACHE_LOCK:
            _BILI_LOGIN_CACHE.update(
                {"key": key, "time": time.time(), "ok": ok, "source": source if ok else ""}
            )
        return ok, (source if ok else "")


def resolve_bilibili_url(url, settings=None):
    """把 b23.tv 短链跟随重定向成包含 BV/av 号的完整链接。

    b23 短链本身不含 BV 号，无 Cookie 时下载会直接走兜底接口，
    不先解析重定向就会报「无法从链接中识别 BV 号或 av 号」。
    """
    url = str(url or "").strip()
    if not url or extract_video_id(url)[1]:
        return url
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        host = ""
    if not (
        host == "b23.tv"
        or host.endswith(".b23.tv")
        or host == "bili2233.cn"
        or host.endswith(".bili2233.cn")
    ):
        return url
    try:
        session = _build_bili_session(settings or {})
        response = session.get(url, allow_redirects=True, timeout=15)
        final_url = response.url or ""
        if extract_video_id(final_url)[1]:
            return final_url
    except Exception as exc:
        write_runtime_log(f"解析B站短链失败: {format_error(exc)}")
    return url


def bili_view(video_id, settings):
    """获取视频信息。video_id 可以是 BV 号、av 号或视频 URL。"""
    video_id = resolve_bilibili_url(video_id, settings)
    session = _build_bili_session(settings)
    id_type, id_value = extract_video_id(video_id) if isinstance(video_id, str) else (None, None)
    if id_type == "aid":
        params = {"aid": id_value}
    elif id_type == "bvid":
        params = {"bvid": id_value}
    else:
        # 兼容直接传 BV 号的旧调用方式
        params = {"bvid": video_id}
    resp = session.get(
        BILIBILI_VIEW_API,
        params=params,
        headers=std_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(payload.get("message") or "B站视频信息接口返回失败。")
    return payload["data"]


def bili_playurl(video_id, cid, qn, settings, page=None, fnval=None):
    """获取播放地址。video_id 可以是 BV 号、av 号或视频 URL。
    fnval: 格式标志位，None 表示自动（有 Cookie 用 4048，无 Cookie 用 0）。
    """
    video_id = resolve_bilibili_url(video_id, settings)
    session = _build_bili_session(settings)
    id_type, id_value = extract_video_id(video_id) if isinstance(video_id, str) else (None, None)
    # 自动选择 fnval：有 Cookie 时请求 DASH（高清），无 Cookie 时只请求 durl（低清但可下载）
    if fnval is None:
        has_cookie = (settings.get("cookie_mode") or "none") != "none"
        fnval = 4048 if has_cookie else 0
    if id_type == "aid":
        params = {"aid": id_value, "cid": cid, "qn": qn, "fnval": fnval, "fourk": 1}
    else:
        params = {"bvid": id_value or video_id, "cid": cid, "qn": qn, "fnval": fnval, "fourk": 1}
    if page:
        params["page"] = page
    resp = session.get(
        BILIBILI_PLAYURL_API,
        params=params,
        headers=std_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(payload.get("message") or "B站下载地址接口返回失败。")
    return payload["data"]


def save_cookies_to_netscape_file(cookies, path):
    """把 Cookie 字典保存为 Netscape 格式 cookies.txt。"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for name, value in cookies.items():
            f.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\t{name}\t{value}\n")
    invalidate_bili_login_cache()


def download_bili_thumbnail(pic_url, output_base, settings):
    """下载 B站封面到视频旁边，成功返回文件路径。"""
    pic_url = (pic_url or "").strip()
    if not pic_url:
        return ""
    if pic_url.startswith("//"):
        pic_url = f"https:{pic_url}"
    elif pic_url.startswith("http://"):
        pic_url = "https://" + pic_url[len("http://"):]
    session = _build_bili_session(settings)
    response = session.get(pic_url, timeout=20)
    response.raise_for_status()
    if not response.content:
        return ""
    suffix = Path(urlparse(pic_url).path).suffix.lower()
    if suffix not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        suffix = ".jpg"
    target = unique_path(Path(f"{output_base}{suffix}"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)
    return str(target)


def _srt_timestamp(value):
    try:
        total_ms = max(0, int(round(float(value) * 1000)))
    except (TypeError, ValueError):
        total_ms = 0
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def subtitle_body_to_srt(body):
    """把 B站字幕 JSON 的 body 转成 SRT 文本。"""
    lines = []
    index = 1
    for item in body or []:
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        start = _srt_timestamp(item.get("from") or 0)
        end = _srt_timestamp(item.get("to") or item.get("from") or 0)
        lines.append(str(index))
        lines.append(f"{start} --> {end}")
        lines.append(content)
        lines.append("")
        index += 1
    return "\n".join(lines).strip() + ("\n" if lines else "")


def download_bili_subtitles(video_url, cid, output_base, settings):
    """尽力下载 B站字幕；需要登录 Cookie 时由调用方给出提示。

    旧兜底路径只下载视频，勾选“下载字幕”也不会生效。这里通过
    player/v2 接口获取字幕列表，并把 JSON 字幕转成通用 SRT。
    """
    resolved_url = resolve_bilibili_url(video_url, settings)
    id_type, id_value = extract_video_id(resolved_url)
    if not id_value or not cid:
        return []
    session = _build_bili_session(settings)
    params = {"cid": cid}
    if id_type == "aid":
        params["aid"] = id_value
    else:
        params["bvid"] = id_value
    response = session.get(BILIBILI_PLAYER_V2_API, params=params, timeout=15)
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        return []
    subtitle = (payload.get("data") or {}).get("subtitle") or {}
    entries = subtitle.get("subtitles") or []
    if not entries:
        return []
    preferred = [
        entry for entry in entries
        if str(entry.get("lan") or "").lower().startswith(("zh", "en"))
    ]
    if not preferred:
        preferred = entries[:2]
    saved = []
    for entry in preferred:
        subtitle_url = str(entry.get("subtitle_url") or "").strip()
        if not subtitle_url:
            continue
        if subtitle_url.startswith("//"):
            subtitle_url = f"https:{subtitle_url}"
        try:
            sub_response = session.get(subtitle_url, timeout=20)
            sub_response.raise_for_status()
            body = (sub_response.json() or {}).get("body") or []
            srt = subtitle_body_to_srt(body)
            if not srt:
                continue
            lang = re.sub(r"[^0-9A-Za-z_-]+", "_", str(entry.get("lan") or "subtitle"))
            target = unique_path(Path(f"{output_base}.{lang or 'subtitle'}.srt"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(srt, encoding="utf-8")
            saved.append(str(target))
        except Exception as exc:
            write_runtime_log(f"下载B站字幕失败: {format_error(exc)}")
    return saved


def normalize_danmaku_xml(text):
    """把弹幕接口返回的内容整理成合法 XML。

    B站 list.so 已经返回完整的 `<?xml?><i>...</i>`；旧实现又包了一层 <i>，
    会写出「XML 声明出现在文件中间」的非法 XML，弹幕播放器无法解析。
    """
    text = (text or "").strip()
    if not text:
        return ""
    if "<i>" in text or re.search(r"<i\s", text):
        return text
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<i>\n"
        f"{text}\n"
        "</i>\n"
    )


def download_bili_danmaku(cid, output_path, settings):
    """下载 B站弹幕 XML。成功返回 True，失败返回 False。"""
    try:
        session = requests.Session()
        proxy = (settings.get("proxy") or "").strip()
        if proxy:
            session.proxies.update({"http": proxy, "https": proxy})
        session.headers.update(std_headers())
        resp = session.get(
            BILIBILI_DM_LIST_API,
            params={"oid": cid},
            timeout=15,
        )
        resp.raise_for_status()
        raw = resp.content or b""
        content = raw.decode("utf-8", errors="ignore")
        # requests 通常会自动解压；这里保留手动 deflate 兜底，
        # 但必须检查内容而不是依赖 decode 抛异常（errors="ignore" 不会抛）。
        if "<d" not in content and raw:
            for wbits in (15, -15, 47):
                try:
                    content = zlib.decompress(raw, wbits).decode(
                        "utf-8", errors="ignore"
                    )
                    break
                except Exception:
                    continue
        if not content or "<d" not in content:
            return False
        document = normalize_danmaku_xml(content)
        if not document:
            return False
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(document)
            if not document.endswith("\n"):
                f.write("\n")
        return True
    except Exception:
        return False


def fetch_bili_danmaku_for_url(url, output_base, settings):
    """为 B站 URL 下载弹幕，output_base 是不含扩展名的输出路径前缀。"""
    if not is_bilibili_url(url):
        return False
    resolved_url = resolve_bilibili_url(url, settings)
    id_type, id_value = extract_video_id(resolved_url)
    if not id_value:
        return False
    try:
        data = bili_view(resolved_url, settings)
        page_num = selected_page_number(resolved_url)
        pages = data.get("pages") or []
        cid = None
        for p in pages:
            if (p.get("page") or 1) == page_num:
                cid = p.get("cid")
                break
        if cid is None and pages:
            cid = pages[0].get("cid")
        if cid is None:
            return False
        output_path = f"{output_base}.danmaku.xml"
        return download_bili_danmaku(cid, output_path, settings)
    except Exception:
        return False
