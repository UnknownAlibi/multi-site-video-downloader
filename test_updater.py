"""程序内更新的无网络单元测试。"""

from multisite_downloader.updater import (
    installer_arguments,
    is_newer,
    parse_sha256,
    parse_version,
    release_info,
    should_auto_check,
)


def test_parse_version():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("1.2.3.4") == (1, 2, 3, 4)
    assert parse_version("") == ()


def test_is_newer():
    assert is_newer("1.1.5", "1.1.4")
    assert is_newer("v1.2", "1.1.9")
    assert not is_newer("1.1.4", "1.1.4")
    assert not is_newer("1.1.3", "1.1.4")
    assert not is_newer("", "1.1.4")


def test_release_info_selects_setup_and_sha():
    payload = {
        "tag_name": "v1.1.5",
        "name": "v1.1.5",
        "body": "changes",
        "html_url": "https://github.com/example/releases/tag/v1.1.5",
        "assets": [
            {
                "name": "multi-site-video-downloader-1.1.5-win64.zip",
                "browser_download_url": "https://example.com/app.zip",
                "size": 10,
            },
            {
                "name": "multi-site-video-downloader-1.1.5-setup.exe",
                "browser_download_url": "https://example.com/app-setup.exe",
                "size": 20,
            },
            {
                "name": "multi-site-video-downloader-1.1.5-setup.exe.sha256",
                "browser_download_url": "https://example.com/app-setup.exe.sha256",
                "size": 64,
            },
        ],
    }
    info = release_info(payload)
    assert info["version"] == "1.1.5"
    assert info["setup_url"].endswith("app-setup.exe")
    assert info["sha_url"].endswith("app-setup.exe.sha256")
    assert info["setup_size"] == 20


def test_parse_sha256():
    value = "A" * 64
    assert parse_sha256(f"{value}  app.exe") == "a" * 64
    assert parse_sha256("not a hash") == ""


def test_installer_arguments_for_nsis():
    assert installer_arguments() == ["/S"]


def test_should_auto_check():
    import time

    assert not should_auto_check({"auto_check_update": False, "update_last_check": 0})
    assert should_auto_check({"auto_check_update": True, "update_last_check": 0})
    assert not should_auto_check(
        {"auto_check_update": True, "update_last_check": time.time()}
    )
