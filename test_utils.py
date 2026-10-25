"""gui_download_qt 纯函数单元测试。

运行方式:
    pytest test_utils.py -v
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# 确保能导入项目主模块
sys.path.insert(0, str(Path(__file__).resolve().parent))

from multisite_downloader.bilibili import normalize_danmaku_xml
from gui_download_qt import (
    extract_aid,
    extract_bvid,
    extract_video_id,
    format_bytes,
    format_duration,
    format_error,
    is_bilibili_url,
    normalize_input,
    parse_media_info_text,
    render_filename_template,
    sanitize_filename,
    selected_page_number,
    split_inputs,
)
from multisite_downloader.media import has_no_audio


# ---------- split_inputs ----------

class TestSplitInputs:
    def test_empty(self):
        assert split_inputs("") == []
        assert split_inputs(None) == []

    def test_single(self):
        assert split_inputs("https://bilibili.com/video/BV1xx") == [
            "https://bilibili.com/video/BV1xx"
        ]

    def test_newline_separated(self):
        text = "BV1abc\nBV2def\nBV3ghi"
        assert split_inputs(text) == ["BV1abc", "BV2def", "BV3ghi"]

    def test_mixed_separators(self):
        text = "BV1abc, BV2def\nBV3ghi BV4jkl，BV5mno"
        assert split_inputs(text) == ["BV1abc", "BV2def", "BV3ghi", "BV4jkl", "BV5mno"]

    def test_whitespace_only(self):
        assert split_inputs("   \n\t  ") == []


# ---------- extract_bvid ----------

class TestExtractBvid:
    def test_standard_url(self):
        url = "https://www.bilibili.com/video/BV1xx411c7mD"
        assert extract_bvid(url) == "BV1xx411c7mD"

    def test_b23_short(self):
        url = "https://b23.tv/BV1xx411c7mD"
        assert extract_bvid(url) == "BV1xx411c7mD"

    def test_with_query(self):
        url = "https://www.bilibili.com/video/BV1xx411c7mD?p=2&t=30"
        assert extract_bvid(url) == "BV1xx411c7mD"

    def test_no_bvid(self):
        assert extract_bvid("https://www.youtube.com/watch?v=abc") == ""

    def test_bare_bvid(self):
        assert extract_bvid("BV1xx411c7mD") == "BV1xx411c7mD"


# ---------- extract_aid ----------

class TestExtractAid:
    def test_standard(self):
        assert extract_aid("https://www.bilibili.com/video/av170001") == "170001"

    def test_uppercase(self):
        assert extract_aid("https://www.bilibili.com/video/AV170001") == "170001"

    def test_no_aid(self):
        assert extract_aid("https://www.bilibili.com/video/BV1xx411c7mD") == ""


# ---------- extract_video_id ----------

class TestExtractVideoId:
    def test_bvid(self):
        kind, vid = extract_video_id("https://bilibili.com/video/BV1xx411c7mD")
        assert kind == "bvid"
        assert vid == "BV1xx411c7mD"

    def test_aid(self):
        kind, vid = extract_video_id("https://bilibili.com/video/av170001")
        assert kind == "aid"
        assert vid == "170001"

    def test_none(self):
        kind, vid = extract_video_id("https://youtube.com/watch?v=abc")
        assert kind is None
        assert vid is None


# ---------- normalize_input ----------

class TestNormalizeInput:
    def test_bvid_completion(self):
        assert normalize_input("BV1xx411c7mD") == "https://www.bilibili.com/video/BV1xx411c7mD"

    def test_bvid_with_page_query(self):
        assert (
            normalize_input("BV1xx411c7mD?p=2")
            == "https://www.bilibili.com/video/BV1xx411c7mD?p=2"
        )

    def test_av_completion(self):
        assert normalize_input("av170001") == "https://www.bilibili.com/video/av170001"

    def test_av_with_page_query(self):
        assert (
            normalize_input("av170001?p=2")
            == "https://www.bilibili.com/video/av170001?p=2"
        )

    def test_AV_uppercase(self):
        assert normalize_input("AV170001") == "https://www.bilibili.com/video/av170001"

    def test_full_url_unchanged(self):
        url = "https://www.bilibili.com/video/BV1xx411c7mD?p=2"
        assert normalize_input(url) == url

    def test_empty(self):
        assert normalize_input("") == ""
        assert normalize_input(None) == ""

    def test_other_url_unchanged(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert normalize_input(url) == url


# ---------- is_bilibili_url ----------

class TestIsBilibiliUrl:
    def test_bilibili_com(self):
        assert is_bilibili_url("https://www.bilibili.com/video/BV1xx411c7mD")

    def test_b23_tv(self):
        assert is_bilibili_url("https://b23.tv/abc")

    def test_bare_bvid(self):
        assert is_bilibili_url("BV1xx411c7mD")

    def test_bare_av(self):
        assert is_bilibili_url("av170001")

    def test_other(self):
        assert not is_bilibili_url("https://youtube.com/watch?v=abc")


# ---------- selected_page_number ----------

class TestSelectedPageNumber:
    def test_default(self):
        assert selected_page_number("https://bilibili.com/video/BV1xx") == 1

    def test_explicit(self):
        assert selected_page_number("https://bilibili.com/video/BV1xx?p=3") == 3

    def test_invalid(self):
        assert selected_page_number("https://bilibili.com/video/BV1xx?p=abc") == 1


# ---------- sanitize_filename ----------

class TestSanitizeFilename:
    def test_normal(self):
        assert sanitize_filename("我的视频") == "我的视频"

    def test_special_chars(self):
        assert sanitize_filename("a/b:c*d?e\"f<g>h|i") == "a_b_c_d_e_f_g_h_i"

    def test_empty(self):
        assert sanitize_filename("") == "video"
        assert sanitize_filename(None) == "video"

    def test_max_len(self):
        name = "a" * 200
        assert len(sanitize_filename(name, max_len=50)) == 50


# ---------- format_duration ----------

class TestFormatDuration:
    def test_seconds(self):
        assert format_duration(45) == "0:45"

    def test_minutes(self):
        assert format_duration(125) == "2:05"

    def test_hours(self):
        assert format_duration(3661) == "1:01:01"

    def test_zero(self):
        assert format_duration(0) == "-"

    def test_negative(self):
        assert format_duration(-5) == "-"

    def test_invalid(self):
        assert format_duration("abc") == "-"


# ---------- format_bytes ----------

class TestFormatBytes:
    def test_bytes(self):
        assert format_bytes(512) == "512.0 B"

    def test_kb(self):
        assert format_bytes(2048) == "2.0 KB"

    def test_mb(self):
        assert format_bytes(1048576) == "1.0 MB"

    def test_gb(self):
        assert format_bytes(1073741824) == "1.0 GB"

    def test_zero(self):
        assert format_bytes(0) == "-"

    def test_negative(self):
        assert format_bytes(-100) == "-"

    def test_invalid(self):
        assert format_bytes("abc") == "-"


# ---------- format_error ----------

class TestFormatError:
    def test_with_message(self):
        assert format_error(ValueError("test message")) == "test message"

    def test_empty_message(self):
        exc = ValueError()
        assert format_error(exc) == "ValueError"


# ---------- render_filename_template ----------

class TestRenderFilenameTemplate:
    def test_default_template(self):
        name = render_filename_template(
            "%(title).180B [%(id)s].%(ext)s",
            {"title": "标题", "id": "BV1xx", "ext": "mp4"},
        )
        assert name == "标题 [BV1xx].mp4"

    def test_truncate(self):
        name = render_filename_template(
            "%(title).5s.%(ext)s", {"title": "abcdefgh", "ext": "mp4"}
        )
        assert name == "abcde.mp4"

    def test_unknown_field_becomes_empty(self):
        name = render_filename_template(
            "%(title)s-%(unknown)s.%(ext)s", {"title": "a", "ext": "mp4"}
        )
        assert name == "a-.mp4"

    def test_empty_template_fallback(self):
        assert render_filename_template("", {"title": "a", "ext": "mp4"}) == "a.mp4"
        assert render_filename_template(None, {"title": "a", "ext": "mp4"}) == "a.mp4"

    def test_illegal_chars_sanitized(self):
        name = render_filename_template(
            "%(title)s.%(ext)s", {"title": "a/b:c*d", "ext": "mp4"}
        )
        assert name == "a_b_c_d.mp4"

    def test_missing_title_falls_back_to_video(self):
        assert render_filename_template("%(title)s", {}) == "video"


# ---------- parse_media_info_text ----------

FFMPEG_MP4_OUTPUT = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'x.mp4':
  Metadata:
    major_brand     : isom
  Duration: 00:03:12.34, start: 0.000000, bitrate: 2936 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(progressive), 1920x1080 [SAR 1:1 DAR 16:9], 2911 kb/s, 30 fps, 30 tbr, 15360 tbn (default)
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 44100 Hz, stereo, fltp, 128 kb/s (default)
"""


class TestParseMediaInfoText:
    def test_real_ffmpeg_output(self):
        info = parse_media_info_text(FFMPEG_MP4_OUTPUT)
        assert info["duration"] == "00:03:12.34"
        assert info["video_codec"] == "h264"
        assert info["width"] == 1920
        assert info["height"] == 1080
        assert info["fps"] == 30
        assert info["audio_codec"] == "aac"
        assert info["audio_sample_rate"] == 44100

    def test_hex_codec_tag_not_treated_as_resolution(self):
        info = parse_media_info_text(FFMPEG_MP4_OUTPUT)
        assert info["width"] == 1920 and info["height"] == 1080

    def test_hevc_stream(self):
        text = (
            "  Duration: 00:00:10.00, start: 0.000000, bitrate: 1000 kb/s\n"
            "  Stream #0:0: Video: hevc (Main), yuv420p(tv), 3840x2160 [SAR 1:1 DAR 16:9], "
            "2000 kb/s, 59.94 fps, 60 tbr, 15360 tbn\n"
        )
        info = parse_media_info_text(text)
        assert info["video_codec"] == "hevc"
        assert info["width"] == 3840
        assert info["height"] == 2160
        assert info["fps"] == 59.94

    def test_audio_only(self):
        text = (
            "  Duration: 00:02:00.00, start: 0.000000, bitrate: 128 kb/s\n"
            "  Stream #0:0: Audio: mp3, 48000 Hz, stereo, fltp, 128 kb/s\n"
        )
        info = parse_media_info_text(text)
        assert info["audio_codec"] == "mp3"
        assert info["audio_sample_rate"] == 48000
        assert "width" not in info

    def test_empty(self):
        assert parse_media_info_text("") == {}
        assert parse_media_info_text(None) == {}


# ---------- has_no_audio ----------

class TestHasNoAudio:
    def test_video_without_audio(self):
        assert has_no_audio({"video_codec": "h264", "audio_codec": ""})

    def test_normal_file(self):
        assert not has_no_audio({"video_codec": "h264", "audio_codec": "aac"})

    def test_audio_only(self):
        assert not has_no_audio({"video_codec": "", "audio_codec": "aac"})

    def test_empty_info(self):
        assert not has_no_audio({})


class TestThemeStylesheet:
    def test_light_and_dark(self):
        from multisite_downloader.ui import theme
        theme.set_mode('dark')
        dark = theme.build_stylesheet()
        assert theme.is_dark()
        assert '#131A26' in dark
        theme.set_mode('light')
        light = theme.build_stylesheet()
        assert not theme.is_dark()
        assert '#131A26' not in light


class TestSiteCookies:
    def test_detect_site(self):
        from multisite_downloader.sites import detect_site
        assert detect_site('https://www.bilibili.com/video/BV1xx') == 'bilibili'
        assert detect_site('https://b23.tv/abc') == 'bilibili'
        assert detect_site('https://v.douyin.com/abc') == 'douyin'
        assert detect_site('https://www.youtube.com/watch?v=1') == 'youtube'
        assert detect_site('https://youtu.be/1') == 'youtube'
        assert detect_site('https://notbilibili.com/x') == 'generic'

    def test_site_cookie_overrides_global(self, tmp_path):
        from multisite_downloader.net import apply_cookie_and_proxy_options
        global_file = tmp_path / 'global.txt'
        douyin_file = tmp_path / 'douyin.txt'
        global_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        douyin_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        settings = {
            'cookie_mode': 'file',
            'cookie_file': str(global_file),
            'site_cookie_bilibili': '',
            'site_cookie_douyin': str(douyin_file),
            'site_cookie_youtube': '',
        }
        opts = apply_cookie_and_proxy_options({}, settings, 'https://www.douyin.com/video/1')
        assert opts['cookiefile'] == str(douyin_file)
        opts = apply_cookie_and_proxy_options({}, settings, 'https://www.bilibili.com/video/BV1')
        assert opts['cookiefile'] == str(global_file)
        opts = apply_cookie_and_proxy_options({}, settings, 'https://www.youtube.com/watch?v=1')
        assert opts['cookiefile'] == str(global_file)

    def test_cookie_file_contains_domain(self, tmp_path):
        from multisite_downloader.sites import cookie_file_contains_domain
        path = tmp_path / 'cookies.txt'
        path.write_text(
            "# Netscape HTTP Cookie File\n"
            "#HttpOnly_.douyin.com\tTRUE\t/\tFALSE\t0\tttwid\tabc\n",
            encoding="utf-8",
        )
        assert cookie_file_contains_domain(path, 'douyin.com')
        assert not cookie_file_contains_domain(path, 'bilibili.com')


class TestBrowserLoginCookies:
    def test_write_netscape_cookies(self, tmp_path):
        from multisite_downloader.browser_login import (
            has_login_cookies,
            has_public_cookies,
            write_netscape_cookies,
        )

        cookies = [
            {
                "domain": ".douyin.com",
                "path": "/",
                "secure": True,
                "expires": -1,
                "name": "ttwid",
                "value": "abc",
                "httpOnly": True,
            },
            {
                "domain": "www.douyin.com",
                "path": "/",
                "secure": False,
                "expires": 2000000000,
                "name": "passport_csrf_token",
                "value": "xyz",
                "httpOnly": False,
            },
        ]
        path = write_netscape_cookies(cookies, tmp_path / "douyin.txt")
        text = path.read_text(encoding="utf-8")
        assert "#HttpOnly_.douyin.com\tTRUE\t/\tTRUE\t0\tttwid\tabc" in text
        assert "www.douyin.com\tFALSE\t/\tFALSE\t2000000000" in text
        assert has_login_cookies([{"name": "sessionid"}])
        assert has_public_cookies(
            [
                {"name": "ttwid"},
                {"name": "s_v_web_id"},
                {"name": "passport_csrf_token"},
            ]
        )
        assert not has_public_cookies([{"name": "ttwid"}])


class TestCookieDomainStore:
    def test_domain_mapping(self):
        from multisite_downloader.sites import cookie_domain_for_url, registrable_domain

        assert cookie_domain_for_url("https://youtu.be/abc") == "youtube.com"
        assert cookie_domain_for_url("https://v.douyin.com/abc") == "douyin.com"
        assert cookie_domain_for_url("https://www.xiaohongshu.com/x") == "xiaohongshu.com"
        assert cookie_domain_for_url("https://x.com/a") == "x.com"
        assert registrable_domain("www.bbc.co.uk") == "bbc.co.uk"
        assert registrable_domain("a.b.example.com") == "example.com"

    def test_store_lookup(self, tmp_path, monkeypatch):
        import multisite_downloader.cookies as cookies
        from multisite_downloader.net import apply_cookie_and_proxy_options

        monkeypatch.setattr(cookies, "_STORE_OVERRIDE", tmp_path)
        path = cookies.cookie_store_path("vimeo.com")
        path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        assert cookies.find_domain_cookie_file("https://vimeo.com/123") == path
        assert cookies.find_domain_cookie_file("https://example.com/x") is None

        settings = {
            "cookie_mode": "none",
            "cookie_file": "",
            "site_cookie_vimeo": "",
        }
        opts = apply_cookie_and_proxy_options(
            {}, settings, "https://vimeo.com/123"
        )
        assert opts["cookiefile"] == str(path)

    def test_store_details(self, tmp_path, monkeypatch):
        import multisite_downloader.cookies as cookies

        monkeypatch.setattr(cookies, "_STORE_OVERRIDE", tmp_path)
        path = cookies.cookie_store_path("vimeo.com")
        path.write_text(
            "# Netscape HTTP Cookie File\n"
            ".vimeo.com\tTRUE\t/\tFALSE\t0\tvimeo_session\tabc\n",
            encoding="utf-8",
        )
        details = cookies.domain_cookie_details()
        assert len(details) == 1
        assert details[0]["domain"] == "vimeo.com"
        assert details[0]["cookie_count"] == 1
        assert details[0]["size"] == path.stat().st_size
        assert cookies.remove_domain_cookie("vimeo.com") is True
        assert cookies.domain_cookie_details() == []


class TestYouTubeBrowserCookie:
    @staticmethod
    def _cookie(name, domain=".youtube.com"):
        from http.cookiejar import Cookie

        return Cookie(
            version=0,
            name=name,
            value="test-value",
            port=None,
            port_specified=False,
            domain=domain,
            domain_specified=True,
            domain_initial_dot=domain.startswith("."),
            path="/",
            path_specified=True,
            secure=True,
            expires=None,
            discard=False,
            comment=None,
            comment_url=None,
            rest={},
        )

    def test_saved_only_for_youtube_auto_mode(self, tmp_path, monkeypatch):
        import multisite_downloader.cookies as cookies
        from http.cookiejar import CookieJar

        monkeypatch.setattr("multisite_downloader.logs.write_runtime_log", lambda message: None)
        monkeypatch.setattr(cookies, "_STORE_OVERRIDE", tmp_path)
        monkeypatch.setattr(cookies, "_BROWSER_COOKIE_ATTEMPTS", {})
        jar = CookieJar()
        jar.set_cookie(self._cookie("SID"))
        monkeypatch.setattr(
            cookies, "_youtube_browser_cookie_jar", lambda: ("Firefox", jar)
        )

        settings = {"auto_browser_login": True, "site_cookie_youtube": ""}
        path = cookies.ensure_youtube_browser_cookie(settings)
        assert path == cookies.cookie_store_path("youtube.com")
        assert "SID" in path.read_text(encoding="utf-8")
        assert cookies.ensure_youtube_browser_cookie(settings) is None

        disabled = {"auto_browser_login": False, "site_cookie_youtube": ""}
        assert cookies.ensure_youtube_browser_cookie(disabled) is None

    def test_apply_options_uses_auto_cookie(self, tmp_path, monkeypatch):
        import multisite_downloader.cookies as cookies
        import multisite_downloader.net as net

        monkeypatch.setattr(cookies, "_STORE_OVERRIDE", tmp_path)
        auto_file = tmp_path / "youtube_auto.txt"
        auto_file.write_text(
            "# Netscape HTTP Cookie File\n"
            ".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tabc\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            net, "ensure_youtube_browser_cookie", lambda settings: auto_file
        )
        settings = {
            "cookie_mode": "none",
            "cookie_file": "",
            "site_cookie_youtube": "",
            "auto_browser_login": True,
        }
        opts = net.apply_cookie_and_proxy_options(
            {}, settings, "https://youtu.be/abc"
        )
        assert opts["cookiefile"] == str(auto_file)


class TestDouyinUrlNormalization:
    def test_modal_id_becomes_video_url(self):
        from multisite_downloader.urls import normalize_input

        assert normalize_input(
            "https://www.douyin.com/jingxuan?modal_id=7698309610885741221"
        ) == "https://www.douyin.com/video/7698309610885741221"
        assert normalize_input(
            "https://www.douyin.com/user/abc?modal_id=7698309610885741221"
        ) == "https://www.douyin.com/video/7698309610885741221"

    def test_normal_urls_unchanged(self):
        from multisite_downloader.urls import normalize_input

        assert normalize_input(
            "https://www.douyin.com/video/7698309610885741221"
        ) == "https://www.douyin.com/video/7698309610885741221"
        assert normalize_input("https://v.douyin.com/abc") == "https://v.douyin.com/abc"


class TestHasCookieForUrl:
    def test_missing_then_explicit_then_store(self, tmp_path, monkeypatch):
        import multisite_downloader.cookies as cookies

        monkeypatch.setattr(cookies, "_STORE_OVERRIDE", tmp_path)
        bili = tmp_path / "bili.txt"
        bili.write_text(
            "# Netscape HTTP Cookie File\n.bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tabc\n",
            encoding="utf-8",
        )
        settings = {
            "cookie_mode": "file",
            "cookie_file": str(bili),
            "site_cookie_douyin": "",
        }
        assert not cookies.has_cookie_for_url(
            "https://www.douyin.com/video/1", settings
        )

        douyin = tmp_path / "douyin.txt"
        douyin.write_text(
            "# Netscape HTTP Cookie File\n#HttpOnly_.douyin.com\tTRUE\t/\tFALSE\t0\tttwid\tabc\n",
            encoding="utf-8",
        )
        settings["site_cookie_douyin"] = str(douyin)
        assert cookies.has_cookie_for_url(
            "https://www.douyin.com/video/1", settings
        )

        store = cookies.cookie_store_path("xiaohongshu.com")
        store.write_text(
            "# Netscape HTTP Cookie File\n.xiaohongshu.com\tTRUE\t/\tFALSE\t0\ta\tb\n",
            encoding="utf-8",
        )
        settings["site_cookie_douyin"] = ""
        assert cookies.has_cookie_for_url(
            "https://www.xiaohongshu.com/explore/1", settings
        )


class TestDouyinBrowserInfo:
    def test_k_quality_labels_map_to_heights(self):
        from multisite_downloader.douyin_browser import _height_from_label, _parse_page_info

        assert _height_from_label("超清 4K") == 2160
        assert _height_from_label("超清 2K") == 1440
        assert _height_from_label("超清 8K") == 4320
        assert _height_from_label("高清 1080P") == 1080
        assert _height_from_label("智能") == 0

        info = _parse_page_info(
            '{"qualities": ['
            '{"label": "超清 4K", "height": 0}, '
            '{"label": "超清 2K", "height": 0}, '
            '{"label": "超清 1080P", "height": 1080}, '
            '{"label": "智能", "height": 0}]}'
        )
        heights = [q["height"] for q in info["qualities"]]
        assert heights == [2160, 1440, 1080, 0]

    def test_build_info(self):
        from multisite_downloader.douyin_browser import build_douyin_info

        data = {
            "title": "测试视频",
            "duration": 12.5,
            "width": 720,
            "height": 1280,
            "author": "作者",
            "video_url": "https://example.com/media-video-hvc1/?x=1",
            "audio_url": "https://example.com/media-audio-und-mp4a/?x=1",
            "video_bitrate": 530,
            "cookie_header": "a=b",
            "cover": "https://example.com/cover.jpg",
            "cover_path": "C:/tmp/cover.png",
            "logged_in": True,
        }
        info = build_douyin_info(data, "https://www.douyin.com/video/123")
        assert info["title"] == "测试视频"
        assert info["uploader"] == "作者"
        assert info["duration"] == 12.5
        assert info["source"] == "browser"
        assert info["formats"][0]["height"] == 1280
        assert info["formats"][0]["vcodec"] == "hevc"
        assert info["formats"][0]["tbr"] == 530
        assert info["cover_path"] == "C:/tmp/cover.png"
        assert info["logged_in"] is True
        assert info["stream"]["video_url"] == data["video_url"]
        assert "530" in info["note"]

    def test_build_info_exposes_all_page_qualities(self):
        from multisite_downloader.douyin_browser import build_douyin_info

        data = {
            "title": "测试视频",
            "duration": 12.5,
            "width": 1280,
            "height": 720,
            "author": "作者",
            "video_url": "https://example.com/media-video-hvc1/?x=1",
            "audio_url": "https://example.com/media-audio-und-mp4a/?x=1",
            "video_bitrate": 530,
            "cookie_header": "a=b",
            "logged_in": False,
            "qualities": [
                {"label": "超清 4K", "height": 2160,
                 "login_required": False, "selected": False},
                {"label": "超清 2K", "height": 1440,
                 "login_required": False, "selected": False},
                {"label": "高清 1080P", "height": 1080,
                 "login_required": True, "selected": False},
                {"label": "高清 720P", "height": 720,
                 "login_required": False, "selected": True},
                {"label": "标清 540P", "height": 540,
                 "login_required": False, "selected": False},
                {"label": "智能", "height": 0,
                 "login_required": False, "selected": False},
            ],
        }
        info = build_douyin_info(data, "https://www.douyin.com/video/123")
        ids = [item["format_id"] for item in info["formats"]]
        assert ids == [
            "douyin-2160",
            "douyin-1440",
            "douyin-1080",
            "douyin-720",
            "douyin-540",
            "douyin-auto",
        ]
        assert info["formats"][0]["height"] == 2160
        assert info["formats"][0]["width"] == 3840
        assert info["formats"][2]["height"] == 1080
        assert info["formats"][2]["width"] == 1920
        assert "需登录" in info["formats"][2]["note"]
        assert info["formats"][4]["height"] == 540
        assert info["formats"][5]["note"] == "页面智能画质"

    def test_media_bitrate_uses_highest_quality_parameter(self):
        from multisite_downloader.douyin_browser import _media_bitrate

        assert _media_bitrate("https://v.example/media-video?br=530&bt=99") == 530
        assert _media_bitrate("https://v.example/media-video?bt=297") == 297
        assert _media_bitrate("https://v.example/media-video") == 0

    def test_preview_headers_include_referer_and_cookie(self):
        from multisite_downloader.ui.preview_player import preview_headers

        ua, headers = preview_headers({"cookie_header": "sessionid=abc"})
        assert "Mozilla" in ua
        assert "Referer: https://www.douyin.com/" in headers
        assert "Cookie: sessionid=abc" in headers


class TestDanmakuXml:
    def test_complete_document_is_not_wrapped_again(self):
        raw = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<i><d p="1,1,25">hello</d></i>'
        )
        document = normalize_danmaku_xml(raw)
        assert document == raw
        root = ET.fromstring(document)
        assert root.tag == "i"
        assert root.find("d") is not None

    def test_fragment_is_wrapped_into_valid_document(self):
        document = normalize_danmaku_xml('<d p="1,1,25">hello</d>')
        root = ET.fromstring(document)
        assert root.tag == "i"
        assert root.find("d").text == "hello"

    def test_empty_content(self):
        assert normalize_danmaku_xml("") == ""

    def test_download_writes_valid_xml(self, tmp_path, monkeypatch):
        import multisite_downloader.bilibili as bilibili

        raw = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<i><d p="1,1,25">hello</d></i>'
        ).encode("utf-8")

        class FakeResponse:
            content = raw

            def raise_for_status(self):
                pass

        class FakeSession:
            headers = {}
            proxies = {}

            def get(self, *args, **kwargs):
                return FakeResponse()

        monkeypatch.setattr(bilibili.requests, "Session", lambda: FakeSession())
        output = tmp_path / "danmaku.xml"
        assert bilibili.download_bili_danmaku(1, str(output), {})
        root = ET.parse(output).getroot()
        assert root.tag == "i"
        assert root.find("d").text == "hello"


class TestBilibiliExtras:
    def test_subtitle_body_to_srt(self):
        from multisite_downloader.bilibili import subtitle_body_to_srt

        srt = subtitle_body_to_srt([
            {"from": 1.5, "to": 2.75, "content": "你好"},
            {"from": 3, "to": 4.2, "content": "世界"},
        ])
        assert "1\n00:00:01,500 --> 00:00:02,750\n你好" in srt
        assert "2\n00:00:03,000 --> 00:00:04,200\n世界" in srt

    def test_thumbnail_download(self, tmp_path, monkeypatch):
        import multisite_downloader.bilibili as bilibili

        class FakeResponse:
            content = b"image-bytes"

            def raise_for_status(self):
                pass

        class FakeSession:
            def get(self, *args, **kwargs):
                return FakeResponse()

        monkeypatch.setattr(
            bilibili, "_build_bili_session", lambda settings: FakeSession()
        )
        target = bilibili.download_bili_thumbnail(
            "//i2.hdslb.com/bfs/archive/cover.jpg",
            str(tmp_path / "视频 [BV1xx411c7mD]"),
            {},
        )
        assert Path(target).name == "视频 [BV1xx411c7mD].jpg"
        assert Path(target).read_bytes() == b"image-bytes"


class TestQueuePersistence:
    def test_roundtrip(self, tmp_path):
        from multisite_downloader.queue import load_queue, save_queue

        path = tmp_path / "queue.json"
        tasks = [
            {"url": "https://example.com/a", "status": "等待", "settings": {"quality": "1080"}},
            {"url": "https://example.com/b", "status": "失败", "error": "boom"},
        ]
        ok, err = save_queue(tasks, path)
        assert ok, err
        assert load_queue(path) == tasks
        assert not path.with_name("queue.json.tmp").exists()

    def test_missing_or_corrupt_queue(self, tmp_path):
        from multisite_downloader.queue import load_queue

        assert load_queue(tmp_path / "missing.json") == []
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert load_queue(bad) == []
        not_list = tmp_path / "not_list.json"
        not_list.write_text('{"a": 1}', encoding="utf-8")
        assert load_queue(not_list) == []
