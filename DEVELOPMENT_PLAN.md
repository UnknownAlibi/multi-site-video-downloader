# 多站点视频下载器 · 开发与发布说明

一个面向 Windows 的开源多站点视频下载工具。项目使用描述性名称，不以商业品牌方式命名。

- 产品名：**多站点视频下载器 / Multi-site Video Downloader**
- 英文项目标识与仓库名：**multi-site-video-downloader**
- Python 包：**multisite_downloader**
- 版本：**1.1.2**（`multisite_downloader/__init__.py`）
- 最后实机回归：2026-10-25

用户使用说明见 `README.md`，版本变更见 `CHANGELOG.md`。

## 功能状态

- B站：链接/BV/短链、多分 P、DASH 格式、公开接口兜底、扫码登录、浏览器 Cookie 静默复用、字幕、弹幕、封面、高清下载。
- 抖音：无头 Chrome/Edge 解析、页面画质菜单读取与切换、视频/音频流合并、页面画面封面、登录状态复用、前 5 分钟内置预览。
- 通用站点：yt-dlp 下载、格式表、代理、文件名模板、仅音频、字幕、并发、限速、历史记录。
- 任务系统：持久化队列、逐任务设置快照、暂停/取消、单任务重试、批量重试、删除、排序、任务设置编辑。
- 数据与诊断：Cookie 库管理、代理连通性测试、历史搜索/筛选、版本与诊断信息复制、日志轮转、临时文件清理。
- 播放：可选 mpv 内嵌播放，抖音预览和已完成本地文件都走同一套可选依赖策略。

## 实机回归记录（2026-10-25）

已通过：

- 141 项自动化测试，包含无 ffmpeg 环境；打包版 `--self-test`、含空格路径启动、最终 exe 启动均通过。
- B站游客预览与 720P 下载、封面、弹幕、限速、暂停/继续、取消清理。
- B站未登录 MP3：下载公开 MP4 后提取音轨，真实视频 4:27 输出 MP3 且无源视频残留。
- 抖音 4K/2K/1080P/720P/540P/智能读取，4K 流捕获，540P 与 MP3 实际下载，封面、预览和 mpv 播放。
- 双任务并发队列、UI 暂停/继续、单任务重试、批量重试、队列恢复、历史搜索/筛选/导出。
- 设置导入导出、Cookie 库删除、代理直连/无效代理/本地 CONNECT 代理、托盘最小化与退出。
- YouTube 解析与实际下载（640×360 AV1/Opus），通用 MP4 直链解析。

回归中发现并修复：

- 抖音画质菜单晚于视频流加载时，旧逻辑只切换一次，可能实际未使用所选画质。现已改为菜单出现前重试，最多等待 10 秒。

仍受外部条件阻塞：

- B站扫码登录与登录后的 1080P+、字幕、仅音频：当前没有有效账号 Cookie，无法完成登录态回归。
- YouTube 浏览器 Cookie 静默复用成功路径：本机浏览器没有可检出的 YouTube 登录 Cookie。
- 真实第三方代理成功路径：已用本地 CONNECT 代理验证客户端链路，但没有可用外部代理。
- 干净 Windows / 无 mpv 环境：已完成打包自检和含空格路径测试，尚未在第二台干净机器执行完整 UI 回归。

## 架构

```text
gui_download_qt.py        入口：QApplication、单实例锁、临时文件清理、主窗口
multisite_downloader/
  config.py               路径、默认设置、画质常量、ffmpeg 探测
  settings.py             settings.json（临时文件 + fsync + replace）
  history.py              download/history.json
  queue.py                download/queue.json
  cookies.py              Cookie 库、浏览器 Cookie 提取
  bilibili.py             B站 Web API、登录校验、字幕、弹幕、直链兜底
  douyin_browser.py       无头浏览器 CDP 解析、媒体捕获、画质切换、封面截图
  net.py                  yt-dlp 选项、Cookie/代理注入、YouTube 静默 Cookie
  urls.py / sites.py      链接与站点识别
  media.py                ffmpeg 媒体信息解析
  utils.py                纯函数
  errors.py               错误分类
  logs.py                 崩溃/运行日志（1 MB 轮转）
  workers/
    preview.py            预览解析
    download.py           yt-dlp、B站兜底、抖音合流、进度/暂停/取消
    cookie.py             Cookie 检测
    qrlogin.py            B站扫码轮询
    proxy.py              代理连通性测试
    thumbnail.py          封面加载
  ui/
    main_window.py        三页主界面与全部交互
    preview_player.py     mpv 内嵌预览与本地播放
    cookie_manager.py     Cookie 库管理
    task_settings_dialog.py 单任务设置
    douyin_login_dialog.py / qrlogin_dialog.py
    theme.py / widgets.py / effects.py
```

### 线程与数据约定

- 网络请求、浏览器解析、ffmpeg、下载都在 Worker 线程；主线程只更新 UI。
- 每个下载任务携带独立 settings 快照，`DownloadWorker` 用线程局部变量读取当前任务设置。
- 队列/设置/历史都采用临时文件写入后原子替换，异常退出不会留下半份 JSON。
- 取消下载只清理本 worker 登记过的临时文件，已完成成品不删除。
- 下载线程存在时禁止修改队列顺序或任务设置，避免 worker 索引错位。
- 历史表排序后通过 `Qt.UserRole` 保存原始记录索引，所有历史操作都走该索引。

## 发布前验证清单

### 自动验证

```powershell
py -m py_compile gui_download_qt.py multisite_downloader\ui\main_window.py
py -m pytest -q
```

GitHub Actions（`.github/workflows/tests.yml`）在 Windows 上运行同一套测试。

打包产物支持无界面自检，结果写入程序目录的 `self_test.log`：

```powershell
.\multi-site-video-downloader.exe --self-test
# 可选：额外验证 HTTPS 请求
$env:MULTISITE_SELF_TEST_URL = "https://www.bilibili.com/"
.\multi-site-video-downloader.exe --self-test
```

### 手动功能回归

- [ ] 启动程序，下载页/历史页/设置页均可切换。
- [ ] 粘贴 B站链接：封面、标题、分 P、格式表正确；无 Cookie 时提示约 720P，下载可用。
- [ ] 有有效 B站 Cookie 时：预览/下载走 yt-dlp，1080P 可选，音视频合并成功、有声音。
- [ ] B站扫码登录后重启，Cookie 仍可复用。
- [ ] 抖音链接在无头模式解析，封面正常；页面画质全部显示，选择画质后下载使用对应捕获流。
- [ ] 抖音未登录提示出现；登录后封面/画质/预览正常。
- [ ] 点击抖音封面能在软件内播放（mpv 可用时）；关闭后临时预览文件被删除。
- [ ] 队列：修改任务设置、上移/下移、重试失败任务、清空已结束任务、退出重启后恢复未完成任务。
- [ ] 历史：搜索、状态筛选、文件缺失筛选、内置播放、打开目录、导出 CSV/JSON。
- [ ] Cookie 库：列表显示、打开、删除；删除后对应站点的 Cookie 输入框被清空。
- [ ] 代理测试：有效/无效代理均给出明确结果，不阻塞 UI。
- [ ] 限速：设为 512 KB/s 时 yt-dlp 下载与抖音直链下载均明显低于不限速。
- [ ] 取消/暂停/继续/退出：无残留 `.part` 文件，窗口和托盘状态正常。
- [ ] 运行日志超过 1 MB 后生成 `runtime.log.1`。

### 打包验证

1. `py -m pip install pyinstaller`
2. 确认 `ffmpeg.exe` 存在；如需内置播放，把 `mpv.exe` 放入项目根目录。
3. `py -m PyInstaller downloader.spec`
4. 将 `dist/multi-site-video-downloader/` 复制到干净目录启动。
5. 验证：设置/历史/队列/Cookie 写入 exe 同级；ffmpeg 可用；预览、下载、历史、退出均正常。
6. 验证文件属性中的版本为 `1.1.2`。
7. 更新 `multisite_downloader/__init__.py` 版本号时，同时更新 `version_info.txt` 和 README。

### GitHub 发布

- [ ] 将 GitHub 仓库名改为 `multi-site-video-downloader`。
- [ ] 更新本地 `origin` 指向新仓库地址。
- [ ] 推送 `master`，创建 `v1.1.2` Release 并上传发行压缩包。
- [ ] 更新仓库简介为“多站点视频下载器 - Windows 开源视频下载工具”。

## 已知限制

- B站游客兜底接口通常最高 720P；4K/HDR/会员内容需要登录和对应权限。
- 抖音页面标注的画质不保证等于源文件内在分辨率；程序以页面可选项和实际捕获流为准。
- 抖音解析依赖 Chrome/Edge 和 `websocket-client`；Chrome/Edge 被占用或无法启动时可能失败。
- 抖音内置预览只生成前 5 分钟；完整内容需要下载后播放。
- 浏览器 Cookie 受 Chrome/Edge App-Bound Encryption、浏览器运行状态和权限影响。
- 任务暂停不会立即中断当前网络块，取消后不保留断点续传。
- 队列只持久化未完成/失败/已取消任务；已完成任务进入历史。
- mpv 是可选依赖；缺失时抖音预览回退浏览器，本地播放回退系统播放器。
- 代理测试仅验证 B站连通性。

## 发布注意

- 不要把 `settings.json`、`cookies/`、`cookies.txt`、`download/`、`dist/`、`build/` 提交到 git。
- 不要在日志、截图或 Issue 中暴露 Cookie、代理账号密码或个人信息。
- PyQt5 为 GPL v3/商业双许可；公开发行前必须确定项目许可证并满足 PyQt5/Qt/FFmpeg/mpv 的分发要求，详见 `THIRD_PARTY_NOTICES.md`。
- 当前仓库尚未声明项目自身的开源许可证，这是公开发布前需要维护者决定的事项。
