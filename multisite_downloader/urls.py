"""视频链接 / BV 号解析与规范化。"""

import re
from urllib.parse import parse_qs, urlparse


def extract_bvid(url):
    """从链接中提取 BV 号。"""
    m = re.search(r"(BV[0-9A-Za-z]{10,})", url)
    return m.group(1) if m else ""


def extract_aid(url):
    """从链接中提取 av 号（纯数字）。"""
    m = re.search(r"av(\d+)", url, re.IGNORECASE)
    return m.group(1) if m else ""


def extract_video_id(url):
    """从链接中提取视频 ID，返回 (类型, id) 或 (None, None)。"""
    bvid = extract_bvid(url)
    if bvid:
        return ("bvid", bvid)
    aid = extract_aid(url)
    if aid:
        return ("aid", aid)
    return (None, None)


def normalize_douyin_url(url):
    """把抖音精选/弹窗链接转成 yt-dlp 能识别的 /video/<id> 形式。"""
    try:
        parsed = urlparse(str(url))
    except Exception:
        return url
    host = (parsed.netloc or "").lower()
    if not (
        host == "douyin.com"
        or host.endswith(".douyin.com")
        or host.endswith(".iesdouyin.com")
    ):
        return url
    query = parse_qs(parsed.query)
    for key in ("modal_id", "video_id", "vid"):
        value = (query.get(key) or [""])[0].strip()
        if re.fullmatch(r"\d{6,}", value):
            return f"https://www.douyin.com/video/{value}"
    return url


def normalize_input(text):
    """规范化输入：BV/av 号补全为完整链接，抖音弹窗链接转为视频直链。

    裸 BV/av 号后面允许带 ?p=2 这类查询参数（预览也依赖这一步）；
    旧实现只接受「纯 BV 号」，带分 P 参数时会原样传给 yt-dlp 导致解析失败。
    """
    text = (text or "").strip()
    if not text:
        return ""
    match = re.fullmatch(r"(BV[0-9A-Za-z]{10,})((?:[?#].*)?)", text)
    if match:
        return f"https://www.bilibili.com/video/{match.group(1)}{match.group(2)}"
    match = re.fullmatch(r"([aA][vV]\d+)((?:[?#].*)?)", text)
    if match:
        return f"https://www.bilibili.com/video/{match.group(1).lower()}{match.group(2)}"
    return normalize_douyin_url(text)


def is_bilibili_url(url):
    return "bilibili.com" in url or "b23.tv" in url or re.fullmatch(r"BV[0-9A-Za-z]{10,}", url) or re.fullmatch(r"[aA][vV]\d+", url)


def selected_page_number(url):
    """从 URL 中提取 ?p= 参数。"""
    try:
        qs = parse_qs(urlparse(url).query)
        p = qs.get("p", ["1"])[0]
        return int(p)
    except Exception:
        return 1
