"""抖音浏览器解析：从真实页面网络请求里捕获视频/音频直链。"""

import base64
import json
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import websocket

from .browser_login import (
    _cdp_call,
    _free_port,
    _page_ws_url,
    _profile_dir,
    _read_cookies,
    _wait_cdp,
    filter_cookies,
    find_browser_executable,
)
from .net import std_headers
from .urls import normalize_input


_CACHE = {}
_CACHE_TTL = 240
_CAPTURE_LOCK = threading.Lock()


def _quality_height(value):
    """把设置里的画质转成页面菜单的目标高度；best/auto/audio 返回 0。"""
    text = str(value or "").strip()
    return int(text) if text.isdigit() else 0


def _cache_key(url, quality=None):
    height = _quality_height(quality)
    return (normalize_input(url), f"q{height}" if height else "auto")


def get_cached_douyin(url, max_age=_CACHE_TTL, quality=None):
    key = _cache_key(url, quality)
    item = _CACHE.get(key)
    if not item:
        return None
    timestamp, data = item
    if time.time() - timestamp > max_age:
        _CACHE.pop(key, None)
        return None
    return data


def _cache_douyin(url, data, quality=None):
    _CACHE[_cache_key(url, quality)] = (time.time(), data)


def clear_douyin_cache(url=None):
    """登录/画质变化后清掉缓存，确保重新捕获到对应画质。"""
    if not url:
        _CACHE.clear()
        return
    base = normalize_input(url)
    for key in list(_CACHE):
        if key[0] == base:
            _CACHE.pop(key, None)


def _evaluate(ws, msg_id, expression):
    result = _cdp_call(
        ws,
        msg_id,
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True},
    )
    return result.get("result", {}).get("value") or ""


def _height_from_label(label, height=0):
    """把页面画质文字（1080P/4K/2K/8K）统一成可选择的画面高度。"""
    try:
        height = int(height or 0)
    except (TypeError, ValueError):
        height = 0
    if height > 0:
        return height
    text = str(label or "")
    match = re.search(r"(\d{3,4})\s*[pP]\b", text)
    if match:
        return int(match.group(1))
    for token, value in (("8K", 4320), ("4K", 2160), ("2K", 1440)):
        if re.search(rf"\b{token}\b", text, re.IGNORECASE):
            return value
    return 0


def _parse_page_info(raw):
    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}
    title = str(data.get("title") or "").strip()
    title = re.sub(r"\s*-\s*抖音\s*$", "", title).strip()
    qualities = []
    seen = set()
    for item in data.get("qualities") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        height = _height_from_label(label, item.get("height") or 0)
        key = (label, height)
        if key in seen:
            continue
        seen.add(key)
        qualities.append(
            {
                "label": label,
                "height": height,
                "login_required": bool(item.get("login_required")),
                "selected": bool(item.get("selected")),
            }
        )
    return {
        "title": title or "抖音视频",
        "duration": float(data.get("duration") or 0),
        "width": int(data.get("width") or 0),
        "height": int(data.get("height") or 0),
        "cover": str(data.get("cover") or ""),
        "author": str(data.get("author") or "").strip(),
        "hd_requires_login": bool(data.get("hd_requires_login")),
        "qualities": qualities,
    }


PAGE_INFO_JS = r"""
(() => {
  const v = document.querySelector('video');
  const author = document.querySelector('[data-e2e="video-author"]')
    || document.querySelector('a[href*="/user/"]');
  const ogTitle = document.querySelector('meta[property="og:title"]');
  const ogImage = document.querySelector('meta[property="og:image"]');
  let hdRequiresLogin = false;
  let qualities = [];
  try {
    const gear = document.querySelector('.gear');
    if (gear) {
      qualities = Array.from(gear.querySelectorAll('.item')).map(el => {
        const text = (el.innerText || '').trim();
        const match = text.match(/(\d{3,4})\s*[Pp]/);
        let height = match ? parseInt(match[1], 10) : 0;
        if (!height) {
          if (/\b8K\b/i.test(text)) height = 4320;
          else if (/\b4K\b/i.test(text)) height = 2160;
          else if (/\b2K\b/i.test(text)) height = 1440;
        }
        return {
          label: text,
          height: height,
          login_required: /登录即享/.test(text),
          selected: el.classList.contains('selected')
        };
      }).filter(item => item.label);
      const item = qualities.find(item => item.height === 1080);
      hdRequiresLogin = !!item && item.login_required;
    }
  } catch (e) {}
  return JSON.stringify({
    title: (ogTitle && ogTitle.content) || document.title || '',
    duration: v ? (v.duration || 0) : 0,
    width: v ? (v.videoWidth || 0) : 0,
    height: v ? (v.videoHeight || 0) : 0,
    cover: (ogImage && ogImage.content) || (v ? (v.poster || '') : ''),
    author: author ? (author.innerText || '') : '',
    hd_requires_login: hdRequiresLogin,
    qualities: qualities
  });
})()
"""


PAGE_COVER_JS = """
(() => {
  const v = document.querySelector('video');
  if (!v) return '';
  try {
    v.controls = false;
    v.muted = true;
    if (v.paused) v.play();
    if (v.currentTime < 0.3) v.currentTime = 0.3;
  } catch (e) {}
  const r = v.getBoundingClientRect();
  return JSON.stringify({
    x: r.left + window.scrollX,
    y: r.top + window.scrollY,
    width: r.width,
    height: r.height,
    ready: v.readyState || 0,
    currentTime: v.currentTime || 0,
    paused: !!v.paused
  });
})()
"""


PAGE_SET_QUALITY_JS = r"""
(() => {
  const gear = document.querySelector('.gear');
  if (!gear) return '';
  const items = Array.from(gear.querySelectorAll('.item')).map(el => {
    const text = (el.innerText || '').trim();
    const match = text.match(/(\d{3,4})\s*[Pp]/);
    let height = match ? parseInt(match[1], 10) : 0;
    if (!height) {
      if (/\b8K\b/i.test(text)) height = 4320;
      else if (/\b4K\b/i.test(text)) height = 2160;
      else if (/\b2K\b/i.test(text)) height = 1440;
    }
    return {
      el: el,
      label: text,
      height: height,
      login_required: /登录即享/.test(text)
    };
  }).filter(item => item.label);
  const targetHeight = {target_height};
  let target = null;
  if (targetHeight) {
    target = items.find(item => item.height === targetHeight) || null;
    if (!target) return 'not_found';
    if (target.login_required) return 'login_required';
  } else {
    const available = items
      .filter(item => item.height > 0 && !item.login_required)
      .sort((a, b) => b.height - a.height);
    target = available[0] || null;
    if (!target) {
      const auto = items.find(item => !item.height && !item.login_required);
      if (auto) {
        auto.el.click();
        return 'clicked:auto';
      }
      return 'login_required';
    }
  }
  try {
    const btn = gear.querySelector('.btn');
    if (btn) {
      btn.dispatchEvent(new MouseEvent('mouseenter', {bubbles: true}));
      btn.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
    }
    target.el.click();
    return 'clicked:' + target.height;
  } catch (e) {
    return 'error';
  }
})()
"""


def _quality_select_js(target_height):
    return PAGE_SET_QUALITY_JS.replace("{target_height}", str(int(target_height or 0)))


PAGE_PLAY_JS = """
(() => {
  const v = document.querySelector('video');
  if (v) {
    try {
      v.muted = true;
      v.play();
    } catch (e) {}
  }
  return '';
})()
"""


def _media_bitrate(url):
    """从抖音媒体 URL 的 br/bt 参数读取码率，用于挑选最高画质。"""
    try:
        query = parse_qs(urlparse(url).query)
    except Exception:
        return 0
    for key in ("br", "bt"):
        values = query.get(key) or []
        if not values:
            continue
        try:
            return int(values[0])
        except (TypeError, ValueError):
            continue
    return 0


def _capture_cover(ws, key):
    """截取视频元素当前画面作为封面；失败返回空字符串。"""
    try:
        raw = _evaluate(ws, 12000, PAGE_COVER_JS)
        info = json.loads(raw) if raw else {}
        if not info or info.get("width", 0) < 40 or info.get("height", 0) < 40:
            return ""
        if info.get("ready", 0) < 2:
            time.sleep(0.5)
            raw = _evaluate(ws, 12001, PAGE_COVER_JS)
            info = json.loads(raw) if raw else info
        clip = {
            "x": max(0.0, float(info.get("x") or 0)),
            "y": max(0.0, float(info.get("y") or 0)),
            "width": max(1.0, float(info.get("width") or 1)),
            "height": max(1.0, float(info.get("height") or 1)),
            "scale": 1,
        }
        result = _cdp_call(
            ws,
            12002,
            "Page.captureScreenshot",
            {
                "format": "png",
                "fromSurface": True,
                "clip": clip,
            },
        )
        payload = result.get("data") or ""
        if not payload:
            return ""
        image = base64.b64decode(payload)
        if not image:
            return ""
        match = re.search(r"/video/(\d+)", key)
        name = match.group(1) if match else str(abs(hash(key)))
        target = Path(tempfile.gettempdir()) / "multisite-downloader_covers" / f"{name}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(image)
        return str(target)
    except Exception:
        return ""


def capture_douyin_media(url, settings=None, timeout=30, quality=None):
    """串行打开浏览器捕获，避免并发任务抢同一个浏览器配置目录。"""
    cached = get_cached_douyin(url, quality=quality)
    if cached is not None:
        return cached
    with _CAPTURE_LOCK:
        cached = get_cached_douyin(url, quality=quality)
        if cached is not None:
            return cached
        return _capture_douyin_media_impl(url, settings, timeout, quality=quality)


def _capture_douyin_media_impl(url, settings=None, timeout=30, quality=None):
    """打开真实浏览器页面，捕获视频/音频直链、标题和时长。"""
    key = normalize_input(url)
    target_height = _quality_height(quality)
    label, executable = find_browser_executable()
    if not executable:
        raise RuntimeError(
            "没有找到 Chrome 或 Edge，无法用浏览器模式解析抖音。"
        )

    port = _free_port()
    user_agent = std_headers("https://www.douyin.com/")["User-Agent"]
    args = [
        str(executable),
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={_profile_dir('douyin')}",
        "--headless=new",
        f"--user-agent={user_agent}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--autoplay-policy=no-user-gesture-required",
        "--window-size=1920,1080",
        "--window-position=0,0",
        "--force-device-scale-factor=1",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-background-timer-throttling",
    ]
    proxy = ((settings or {}).get("proxy") or "").strip()
    if proxy:
        # Chrome 只接受 host:port 或 scheme://host:port，代理账号密码需在系统代理里配置
        args.append(f"--proxy-server={proxy}")
        args.append("--proxy-bypass-list=localhost;127.0.0.1")
    args.append(key)
    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ws = None
    try:
        ws_url = _wait_cdp(port, timeout=20)
        if not ws_url:
            raise RuntimeError(
                "浏览器调试端口没有就绪。\n"
                "请关闭已打开的抖音登录窗口后重试。"
            )
        page_ws = _page_ws_url(port, "/video/") or _page_ws_url(port) or ws_url
        ws = websocket.create_connection(page_ws, timeout=5)
        _cdp_call(ws, 1, "Network.enable")
        _cdp_call(ws, 2, "Runtime.enable")
        _cdp_call(ws, 3, "Page.enable")
        try:
            _cdp_call(
                ws,
                4,
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": 1920,
                    "height": 1080,
                    "deviceScaleFactor": 1,
                    "mobile": False,
                    "screenWidth": 1920,
                    "screenHeight": 1080,
                },
            )
        except Exception:
            pass
        try:
            _cdp_call(ws, 5, "Page.reload", {"ignoreCache": False})
        except Exception:
            pass
        try:
            _cdp_call(ws, 6, "Page.bringToFront")
        except Exception:
            pass
        ws.settimeout(1.0)

        video_candidates = {}
        audio_candidates = {}
        video_url = ""
        audio_url = ""
        page_info = {}
        stable_page_info = {}
        stable_cover = ""
        collect_until = None
        quality_result = ""
        quality_stop = False
        post_switch_time = None
        deadline = time.time() + timeout
        # 页面画质菜单可能晚于视频流出现：显式画质多等一会，自动模式稍短
        quality_wait_until = time.time() + (10.0 if target_height else 6.0)
        next_eval = time.time() + 2
        msg_id = 10
        while time.time() < deadline:
            if time.time() >= next_eval:
                next_eval = time.time() + 1.5
                try:
                    page_info = _parse_page_info(_evaluate(ws, msg_id, PAGE_INFO_JS))
                    msg_id += 1
                except Exception:
                    pass
                if (
                    page_info.get("title")
                    and video_candidates
                    and post_switch_time is None
                    and not quality_stop
                ):
                    if (
                        page_info.get("width")
                        and page_info.get("height")
                        and not stable_page_info
                    ):
                        stable_page_info = dict(page_info)
                        stable_cover = _capture_cover(ws, key)
                    try:
                        quality_result = _evaluate(
                            ws, msg_id, _quality_select_js(target_height)
                        )
                        msg_id += 1
                        if quality_result.startswith("clicked"):
                            # 切换画质后播放器会重新请求对应码率
                            post_switch_time = time.time()
                            collect_until = time.time() + 7.0
                        elif quality_result == "login_required":
                            quality_stop = True
                    except Exception:
                        pass

            try:
                message = json.loads(ws.recv())
            except Exception:
                if collect_until is not None and time.time() >= collect_until:
                    if (
                        post_switch_time is None
                        and not quality_stop
                        and time.time() < quality_wait_until
                    ):
                        # 还没拿到画质菜单时先别结束，给页面多一次机会
                        collect_until = quality_wait_until
                    else:
                        break
                continue
            if message.get("method") not in (
                "Network.requestWillBeSent",
                "Network.responseReceived",
            ):
                continue
            params = message.get("params") or {}
            if message.get("method") == "Network.requestWillBeSent":
                request = params.get("request") or {}
                candidate = request.get("url") or ""
            else:
                response = params.get("response") or {}
                candidate = response.get("url") or ""
            lowered = candidate.lower()
            if not candidate:
                continue
            is_audio = "media-audio" in lowered
            is_video = "media-video" in lowered or (
                "mime_type=video_mp4" in lowered and not is_audio
            )
            if is_video:
                video_candidates[candidate] = (
                    _media_bitrate(candidate),
                    time.time(),
                )
            elif is_audio:
                audio_candidates[candidate] = _media_bitrate(candidate)
            if video_candidates and page_info:
                if collect_until is None:
                    # 给播放器一点时间切到更高码率，再选最高的一条
                    collect_until = time.time() + 4.0
                elif time.time() >= collect_until:
                    if (
                        post_switch_time is None
                        and not quality_stop
                        and time.time() < quality_wait_until
                    ):
                        collect_until = quality_wait_until
                    else:
                        break

        if video_candidates:
            if target_height and post_switch_time is not None:
                switched = [
                    (timestamp, bitrate, candidate)
                    for candidate, (bitrate, timestamp) in video_candidates.items()
                    if timestamp >= post_switch_time
                ]
                if switched:
                    # 指定画质时优先使用切换后最后请求的流，避免又选中旧的高码率流
                    video_url = max(switched, key=lambda item: item[0])[2]
                else:
                    video_url = max(
                        video_candidates, key=lambda url: video_candidates[url][0]
                    )
            else:
                video_url = max(
                    video_candidates, key=lambda url: video_candidates[url][0]
                )
        if audio_candidates:
            audio_url = max(audio_candidates, key=audio_candidates.get)

        if not video_url:
            raise RuntimeError(
                "没有从页面捕获到视频地址。\n"
                "请确认抖音页面能正常播放；如果显示登录框，请先完成登录。"
            )

        # 页面信息可能比媒体请求晚就绪，最多再等 3 秒拿标题/时长/分辨率
        try:
            ws.settimeout(3.0)
            _evaluate(ws, 9998, PAGE_PLAY_JS)
        except Exception:
            pass
        final_info = {}
        for attempt in range(8):
            try:
                ws.settimeout(3.0)
                candidate = _parse_page_info(
                    _evaluate(ws, 9999 + attempt, PAGE_INFO_JS)
                )
                if candidate.get("title"):
                    final_info = candidate
                if (
                    candidate.get("duration")
                    and candidate.get("width")
                    and candidate.get("height")
                ):
                    final_info = candidate
                    break
            except Exception:
                pass
            time.sleep(0.5)
        try:
            ws.settimeout(1.0)
        except Exception:
            pass
        if final_info:
            page_info = final_info
        if stable_page_info and (
            (page_info.get("width") or 0) < (stable_page_info.get("width") or 0)
        ):
            for field in ("width", "height", "cover"):
                if stable_page_info.get(field):
                    page_info[field] = stable_page_info[field]

        cover_path = stable_cover
        if not cover_path:
            cover_path = _capture_cover(ws, key)

        cookie_header = ""
        cookie_names = set()
        try:
            cookies = filter_cookies(
                _read_cookies(ws, use_page_api=True),
                ("douyin.com", "iesdouyin.com"),
            )
            cookie_header = "; ".join(
                f"{cookie.get('name')}={cookie.get('value')}"
                for cookie in cookies
                if cookie.get("name") and cookie.get("value")
            )
            cookie_names = {
                str(cookie.get("name") or "")
                for cookie in cookies
                if cookie.get("name")
            }
        except Exception:
            pass
        logged_in = bool(
            cookie_names & {"sessionid", "sessionid_ss", "sid_guard"}
        )
        needs_login = bool(page_info.get("hd_requires_login")) or (
            quality_result == "login_required"
        )
        if needs_login:
            logged_in = False

        selected_height = 0
        if quality_result.startswith("clicked:"):
            suffix = quality_result.split(":", 1)[1]
            if suffix.isdigit():
                selected_height = int(suffix)

        data = dict(page_info)
        data.update(
            {
                "video_url": video_url,
                "audio_url": audio_url,
                "video_bitrate": _media_bitrate(video_url),
                "audio_bitrate": _media_bitrate(audio_url),
                "cookie_header": cookie_header,
                "cover_path": cover_path,
                "logged_in": logged_in,
                "needs_login": needs_login,
                "quality_result": quality_result,
                "requested_quality": target_height,
                "selected_height": selected_height,
                "page_url": key,
                "browser": label,
            }
        )
        _cache_douyin(key, data, quality=quality)
        return data
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()


def build_douyin_info(data, url):
    """把浏览器捕获结果转换成 PreviewWorker 的 info 结构。"""
    base_width = int(data.get("width") or 0)
    base_height = int(data.get("height") or 0)
    vcodec = "hevc" if "hvc1" in data.get("video_url", "") else "h264"
    qualities = data.get("qualities") or []

    formats = []
    seen_heights = set()
    seen_auto = False
    for item in qualities:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        try:
            height = int(item.get("height") or 0)
        except (TypeError, ValueError):
            height = 0
        login_required = bool(item.get("login_required"))
        if height > 0:
            if height in seen_heights:
                continue
            seen_heights.add(height)
            width = (
                round(height * base_width / base_height)
                if base_width and base_height
                else 0
            )
            formats.append(
                {
                    "format_id": f"douyin-{height}",
                    "ext": "mp4",
                    "vcodec": vcodec,
                    "acodec": "aac",
                    "width": width,
                    "height": height,
                    "fps": 0,
                    "filesize": 0,
                    "tbr": 0,
                    "note": "页面画质" + ("（需登录）" if login_required else ""),
                }
            )
        elif label and "智能" in label and not seen_auto:
            seen_auto = True
            formats.append(
                {
                    "format_id": "douyin-auto",
                    "ext": "mp4",
                    "vcodec": vcodec,
                    "acodec": "aac",
                    "width": 0,
                    "height": 0,
                    "fps": 0,
                    "filesize": 0,
                    "tbr": 0,
                    "note": "页面智能画质",
                }
            )

    if not formats:
        formats = [
            {
                "format_id": "douyin-browser",
                "ext": "mp4",
                "vcodec": vcodec,
                "acodec": "aac",
                "width": base_width,
                "height": base_height,
                "fps": 0,
                "filesize": 0,
                "tbr": data.get("video_bitrate") or 0,
                "note": "浏览器捕获",
            }
        ]

    note = "已通过浏览器捕获真实播放地址"
    if data.get("video_bitrate"):
        note += f"（视频码率 {data['video_bitrate']} kbps）"
    return {
        "title": data.get("title") or "抖音视频",
        "uploader": data.get("author") or "",
        "duration": data.get("duration") or 0,
        "thumbnail": data.get("cover") or "",
        "cover_path": data.get("cover_path") or "",
        "logged_in": data.get("logged_in"),
        "qualities": qualities,
        "view_count": 0,
        "like_count": 0,
        "upload_date": "",
        "tags": [],
        "pages": [],
        "formats": formats,
        "source": "browser",
        "url": url,
        "note": note,
        "stream": {
            "video_url": data.get("video_url") or "",
            "audio_url": data.get("audio_url") or "",
            "cookie_header": data.get("cookie_header") or "",
        },
    }
