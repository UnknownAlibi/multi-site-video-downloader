# Multi-site Video Downloader（多站点视频下载器）

一个面向 Windows 的开源桌面视频下载工具。项目采用描述性的开源项目命名，不绑定单一站点；入口是 `gui_download_qt.py`，支持 B站、抖音、YouTube、Vimeo、X 等 yt-dlp 可识别的站点。

当前版本：**1.1.2**（2026-10-27）

## 功能

- 粘贴链接自动解析，支持多行批量输入和从文件导入链接。
- B站链接、BV 号、分 P/合集选择；其他站点交给 yt-dlp。
- 预览显示封面、标题、作者、时长、播放量、点赞、分 P 和可用格式。
- 按视频页面实际提供的画质展示卡片；抖音读取页面画质菜单并原样显示。
- 格式表展示格式 ID、类型、分辨率、帧率、编码和估算大小；可锁定具体格式。
- 支持仅音频（MP3）、封面、字幕和 B站弹幕下载。
- 下载队列支持持久化、逐任务独立设置、重试、删除、上移/下移、修改清晰度和保存目录。
- 下载历史支持搜索、状态筛选、文件缺失筛选、导出 CSV/JSON 和重新下载。
- 支持全局下载限速、分片并发、任务并发、编码偏好、音频质量偏好和文件名模板。
- Cookie 库按域名保存为 `cookies/<域名>.txt`，支持查看、打开和删除。
- B站扫码登录一次后保存 Cookie；解析或下载时若 Cookie 无效，会静默尝试复用浏览器登录。
- 抖音在无头 Chrome/Edge 中解析，不弹解析窗口；登录一次后复用浏览器配置目录。
- YouTube 没有配置 Cookie 时，可按设置静默尝试复用浏览器登录；失败时继续无 Cookie 解析。
- 代理支持 HTTP/SOCKS，并可在设置页测试连通性。
- 抖音封面使用无头浏览器实际画面截图；点击封面可预览前 5 分钟（需要 `mpv.exe`）。
- 历史和队列中的已完成文件可用内置 mpv 播放，缺少 mpv 时可回退系统播放器。
- 运行日志和崩溃日志自动轮转，程序启动时清理过期的预览/封面临时文件。

## 画质与登录说明

B站无 Cookie 的公开兜底接口通常最高约 720P。需要 1080P+、DASH 精确格式、仅音频、字幕/弹幕或会员内容时，请登录一次：

1. 在设置页点击“扫码登录”，或用浏览器登录 B站后导出 `cookies.txt`。
2. 如果本机 Firefox/Edge/Chrome 已有 B站登录，程序会静默复用，不弹登录窗口。
3. 登录状态会保存到 Cookie 库，后续启动直接复用。

未登录时也可以导出 B站 MP3：程序会下载公开 MP4 后本地提取音轨，最高约 720P；登录后会优先使用音频流。

抖音的画质选项完全以视频页面为准（例如 4K/2K/1080P/720P/540P/智能）。选择某个画质后，程序会让页面切换到该档再捕获直链；个别视频即使页面显示 1080P，源流本身可能仍是 720P，这属于视频源限制。部分高清档需要登录，界面会给出提示。

YouTube 建议同时准备 Cookie 和 JavaScript 运行时（Deno 或 Node），否则部分视频可能只能解析低清或被要求验证。

## 内置播放（可选）

软件内播放依赖本机 `mpv.exe`，不是必需依赖：

- 优先查找程序目录或 `_internal/` 中的 `mpv.exe`，其次查找系统 `PATH`。
- 找不到 mpv 时：抖音封面预览会回退浏览器；本地文件播放会询问是否使用系统播放器。
- 把 `mpv.exe` 放到项目根目录后再执行打包，会自动带入发行目录。

## 项目结构

```text
gui_download_qt.py          启动入口（单实例锁 + 主窗口）
multisite_downloader/
  config.py                 路径、默认设置、画质常量、ffmpeg 探测
  utils.py                  纯函数工具（输入拆分、文件名模板、格式化）
  urls.py                   链接 / BV 号解析
  sites.py                  站点识别、Cookie 域名映射
  cookies.py                Cookie 库、浏览器 Cookie 复用
  bilibili.py               B站 Web API、登录、字幕、弹幕
  douyin_browser.py         抖音无头浏览器解析、画质切换、封面
  net.py                    HTTP 头、yt-dlp 选项、Cookie/代理注入
  media.py                  ffmpeg 媒体信息探测
  history.py                下载历史
  queue.py                  队列持久化
  settings.py               settings.json 原子写入
  errors.py                 错误分类与用户提示
  logs.py                   崩溃/运行日志（自动轮转）
  shell.py                  用系统程序打开文件、目录
  mascot.py                 看板娘绘制
  workers/                  预览、下载、Cookie 检测、扫码、代理测试、封面线程
  ui/                       主窗口、对话框、内置播放器、主题与特效
sounds/                     音效
assets/                     界面素材
download/                   下载产物、历史、队列、运行日志（已 gitignore）
cookies/                    按域名保存的 Cookie（已 gitignore）
```

## 数据位置

默认都在程序/项目目录下，便携运行不会写入临时解压目录：

- `settings.json`：程序设置
- `download/history.json`：下载历史
- `download/queue.json`：未完成队列
- `download/runtime.log`：运行日志（超过 1 MB 自动轮转）
- `crash.log`：未捕获异常
- `cookies/<域名>.txt`：Cookie 库

## 测试

```powershell
py -m pytest -q
```

- `test_utils.py`：纯函数、链接解析、Cookie 库、抖音信息、媒体信息等。
- `test_smoke.py`：离屏构建主窗口和 Worker、队列/历史、下载选项、登录与预览等回归测试。

GitHub Actions 会在 Windows 上运行同一套测试，见 `.github/workflows/tests.yml`。

## 打包

1. 安装 PyInstaller：`py -m pip install pyinstaller`
2. 确认项目根目录存在 `ffmpeg.exe`、`icon.ico`、`icon.png`、`download.png`、`folder.png`；可选放入 `mpv.exe`。
3. 执行：`py -m PyInstaller downloader.spec`
4. 产物位于 `dist/multi-site-video-downloader/`，运行 `multi-site-video-downloader.exe`。
5. 发行时打包整个目录（含 `_internal`）。用户数据写在 exe 同级；ffmpeg 可放在 exe 同级覆盖 `_internal/ffmpeg.exe`。
6. 可运行 `multi-site-video-downloader.exe --self-test` 检查依赖、ffmpeg、浏览器与界面构建，结果写入同级 `self_test.log`。

版本号定义在 `multisite_downloader/__init__.py`，打包版本资源在 `version_info.txt`。

## 发布状态

1.1.2 已完成核心功能实机回归，详细记录见 [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md)。B站高清登录、真实外部代理成功路径和干净机器环境仍需在正式发布前补测。

## 已知限制

- B站游客兜底约 720P；高清依赖登录和 ffmpeg。
- 抖音解析依赖本机 Chrome 或 Edge，以及 `websocket-client`。
- Chrome/Edge 新版 Cookie 可能受 App-Bound Encryption 保护，此时请在浏览器导出 `cookies.txt` 或使用 Firefox。
- 取消任务会清理本次产生的 `.part/.ytdl/.temp` 文件，因此取消后重新下载不会断点续传已完成的部分。
- 抖音内置预览只生成前 5 分钟；完整内容请下载后播放。
- 代理测试只检查 B站连通性，不代表目标站点一定可访问。
- 第三方依赖许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)；公开发行前请先确认 PyQt5 等依赖的授权要求。

## 更新日志

版本变更见 [CHANGELOG.md](CHANGELOG.md)。
