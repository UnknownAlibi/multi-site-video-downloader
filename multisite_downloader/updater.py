"""程序内更新：检查 GitHub Release、下载安装包并校验 SHA256。"""

import hashlib
import os
import re
import time
from pathlib import Path

import requests

from . import __version__
from .config import BASE_DIR

GITHUB_REPO = "UnknownAlibi/multi-site-video-downloader"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
INSTALLER_APP_ID = "{1BA388AD-167E-4B64-9F96-BAE43C79D230}_is1"


def parse_version(text):
    """把 v1.2.3 / 1.2.3 转成可比较的元组。"""
    numbers = re.findall(r"\d+", str(text or ""))
    if not numbers:
        return ()
    return tuple(int(part) for part in numbers[:4])


def is_newer(latest, current=None):
    current = current or __version__
    latest_tuple = parse_version(latest)
    current_tuple = parse_version(current)
    if not latest_tuple or not current_tuple:
        return False
    length = max(len(latest_tuple), len(current_tuple))
    latest_tuple += (0,) * (length - len(latest_tuple))
    current_tuple += (0,) * (length - len(current_tuple))
    return latest_tuple > current_tuple


def _asset_sha_name(setup_name):
    return f"{setup_name}.sha256"


def release_info(payload):
    """从 GitHub Release JSON 里提取更新需要的信息。"""
    tag = str(payload.get("tag_name") or "").strip()
    version = tag[1:] if tag.lower().startswith("v") else tag
    assets = payload.get("assets") or []
    setup_asset = None
    for asset in assets:
        name = str(asset.get("name") or "")
        if name.lower().endswith(".exe") and "setup" in name.lower():
            setup_asset = asset
            break
    if setup_asset is None:
        raise RuntimeError("最新 Release 中没有找到 setup.exe 安装包。")

    sha_asset = None
    expected_sha_name = _asset_sha_name(setup_asset.get("name"))
    for asset in assets:
        if str(asset.get("name") or "") == expected_sha_name:
            sha_asset = asset
            break
    if sha_asset is None:
        raise RuntimeError("最新 Release 中缺少安装包的 SHA256 校验文件，已停止自动更新。")

    return {
        "version": version,
        "tag": tag,
        "name": payload.get("name") or tag,
        "notes": payload.get("body") or "",
        "html_url": payload.get("html_url") or RELEASES_URL,
        "published_at": payload.get("published_at") or "",
        "setup_name": setup_asset.get("name") or "setup.exe",
        "setup_url": setup_asset.get("browser_download_url") or "",
        "setup_size": int(setup_asset.get("size") or 0),
        "sha_name": sha_asset.get("name") or "",
        "sha_url": sha_asset.get("browser_download_url") or "",
    }


def _session(settings):
    session = requests.Session()
    proxy = (settings.get("proxy") or "").strip() if settings else ""
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    session.headers.update(
        {
            "User-Agent": f"multi-site-video-downloader/{__version__}",
            "Accept": "application/vnd.github+json",
        }
    )
    return session


def fetch_latest_release(settings=None, timeout=20):
    """请求 GitHub 最新 Release；网络错误会抛出带说明的异常。"""
    session = _session(settings)
    try:
        response = session.get(RELEASES_API, timeout=timeout)
        if response.status_code == 403:
            raise RuntimeError(
                "GitHub API 返回 403，可能是请求频率受限或代理不可用。"
            )
        response.raise_for_status()
        payload = response.json()
        return release_info(payload)
    finally:
        session.close()


def parse_sha256(text):
    match = re.search(r"\b([0-9a-fA-F]{64})\b", str(text or ""))
    return match.group(1).lower() if match else ""


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_update(info, settings=None, dest_dir=None, progress=None, cancelled=None):
    """下载安装包并校验 SHA256，返回本地路径。

    progress(percent, detail)：percent 为 -1 表示总大小未知。
    cancelled()：返回 True 时抛出手动取消异常。
    """
    if not info.get("setup_url") or not info.get("sha_url"):
        raise RuntimeError("更新信息不完整，缺少下载地址或校验文件。")

    target_dir = Path(dest_dir or (BASE_DIR / "updates"))
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(info.get("setup_name") or "setup.exe").name
    final_path = target_dir / safe_name
    part_path = final_path.with_suffix(final_path.suffix + ".part")
    sha_path = target_dir / f"{safe_name}.sha256"

    session = _session(settings)
    try:
        sha_response = session.get(info["sha_url"], timeout=20)
        sha_response.raise_for_status()
        expected = parse_sha256(sha_response.text)
        if not expected:
            raise RuntimeError("下载到的 SHA256 文件格式不正确。")
        sha_path.write_text(sha_response.text, encoding="utf-8")

        response = session.get(info["setup_url"], stream=True, timeout=30)
        response.raise_for_status()
        total = int(response.headers.get("Content-Length") or info.get("setup_size") or 0)
        downloaded = 0
        with open(part_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if cancelled is not None and cancelled():
                    raise RuntimeError("已取消更新下载")
                if not chunk:
                    continue
                handle.write(chunk)
                downloaded += len(chunk)
                if total:
                    percent = min(int(downloaded * 100 / total), 100)
                    detail = (
                        f"{downloaded / 1024 / 1024:.1f} / "
                        f"{total / 1024 / 1024:.1f} MB"
                    )
                else:
                    percent = -1
                    detail = f"{downloaded / 1024 / 1024:.1f} MB"
                if progress is not None:
                    progress(percent, detail)

        actual = sha256_file(part_path)
        if actual != expected:
            raise RuntimeError(
                "安装包 SHA256 校验失败，文件可能不完整，已停止安装。\n"
                f"期望 {expected[:16]}…，实际 {actual[:16]}…"
            )
        os.replace(part_path, final_path)
        if progress is not None:
            progress(100, "校验通过")
        return final_path
    finally:
        session.close()
        if part_path.exists():
            try:
                part_path.unlink()
            except OSError:
                pass


def is_installed_app():
    """判断当前运行的是否是 Inno Setup 安装版。"""
    if os.name != "nt":
        return False
    try:
        import winreg

        base = str(BASE_DIR).rstrip("\\").lower()
        key_path = (
            "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\"
            + INSTALLER_APP_ID
        )
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(root, key_path) as key:
                    location, _ = winreg.QueryValueEx(key, "InstallLocation")
                    if not location:
                        return True
                    if str(location).rstrip("\\").lower() == base:
                        return True
            except OSError:
                continue
    except Exception:
        pass
    return False


def installer_arguments():
    return ["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"]


def should_auto_check(settings, interval_hours=24):
    if not settings or not settings.get("auto_check_update", True):
        return False
    try:
        last = float(settings.get("update_last_check") or 0)
    except (TypeError, ValueError):
        last = 0
    return time.time() - last >= interval_hours * 3600
