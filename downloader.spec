# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for 多站点视频下载器。

打包命令：
    py -m PyInstaller downloader.spec

产物：
    dist/multi-site-video-downloader/multi-site-video-downloader.exe  (onedir 模式)

说明：
- onedir 模式：启动更快，ffmpeg.exe 可在 dist 目录下替换。
- 用户文件（settings.json、download/、history.json）写入 exe 同级目录。
- 内置资源（icon.ico、icon.png、download.png、folder.png、sounds/、ffmpeg.exe）打包进应用目录。
- PyInstaller 6 起 onedir 的依赖与资源放在 `_internal/` 子目录下，
  因此替换 ffmpeg 是 `dist/multi-site-video-downloader/_internal/ffmpeg.exe`；
  程序也会优先读 exe 同级目录的 ffmpeg.exe，想覆盖就直接放在 exe 旁边。
"""

import sys
from pathlib import Path

block_cipher = None
project_root = Path(SPECPATH).resolve()

datas = []
binaries = []

# 内置只读资源（icon.png 必须包含：._app_icon_path 优先用 icon.png 作为侧边栏看板娘）
for resource in ("icon.ico", "icon.png", "download.png", "folder.png"):
    res_path = project_root / resource
    if res_path.exists():
        datas.append((str(res_path), "."))

# ffmpeg.exe 作为二进制资源内置
ffmpeg_path = project_root / "ffmpeg.exe"
if ffmpeg_path.exists():
    binaries.append((str(ffmpeg_path), "."))

# 可选：抖音内置预览需要 mpv.exe；缺失时程序自动回退浏览器
mpv_path = project_root / "mpv.exe"
if mpv_path.exists():
    binaries.append((str(mpv_path), "."))

# 许可说明（程序内“关于”可打开）
for doc_name in ("THIRD_PARTY_NOTICES.md",):
    doc_path = project_root / doc_name
    if doc_path.exists():
        datas.append((str(doc_path), "."))

# 音效资源（缺失时程序会在只读临时目录里生成，打包进去更干净）
sounds_dir = project_root / "sounds"
if sounds_dir.is_dir():
    datas.append((str(sounds_dir), "sounds"))

# 界面图标（QComboBox 下拉箭头）
arrow_icon = project_root / "multisite_downloader" / "ui" / "arrow_down.png"
if arrow_icon.exists():
    datas.append((str(arrow_icon), "multisite_downloader/ui"))

# 界面立绘 / 背景图（可选，用户也可以把同名图片放在 exe 同级 assets/ 覆盖）
assets_dir = project_root / "assets"
if assets_dir.is_dir():
    datas.append((str(assets_dir), "assets"))

a = Analysis(
    ["gui_download_qt.py"],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "yt_dlp",
        "yt_dlp.extractor",
        "yt_dlp.postprocessor",
        "browser_cookie3",
        "browser_cookie3.chrome",
        "browser_cookie3.firefox",
        "websocket",
        "qrcode",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "scipy",
        "pandas",
        "PySide6",
        "PyQt6",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="multi-site-video-downloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(project_root / "version_info.txt")
    if (project_root / "version_info.txt").exists()
    else None,
    icon=str(project_root / "icon.ico") if (project_root / "icon.ico").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="multi-site-video-downloader",
)
