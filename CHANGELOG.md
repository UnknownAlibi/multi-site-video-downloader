# 更新日志

## 1.1.8 - 2026-11-06

### 修复

- 修复选中队列行时状态列出现两份“完成”文字的问题。
- 状态文字继续保留在表格数据中供统计和操作使用，但由 `NoTextDelegate` 隐藏绘制，只显示状态胶囊。
- 保持进度百分比、详情文字和路径列布局不变。

### 验证

- 148 项自动化测试通过。
- 选中完成行渲染检查通过，状态胶囊无重影。
- 1.1.8 覆盖安装 E 盘，Cookie、设置、历史保留。

### 产物

- `dist/multi-site-video-downloader/multi-site-video-downloader.exe`
- `dist/multi-site-video-downloader-1.1.8-win64.zip`
- `dist/multi-site-video-downloader-1.1.8-setup.exe`

## 1.1.7 - 2026-11-06

### 修复

- 修复下载队列完成行里 `100%` 和 `已完成` 重叠的问题：进度列只由自绘控件显示，底层表格文字不再参与渲染。
- 进度百分比固定显示在进度列右侧，详情文字显示在左侧。
- 输出列改为固定宽度，长路径不会再把 `E:\...` 压缩成 `E:...`。
- 任务列自动占满剩余宽度，窄窗口下优先保住路径列和状态列。

### 验证

- 148 项自动化测试通过。
- 1524 宽度下队列列宽为 `381 / 92 / 190 / 300`，进度和路径渲染检查通过。

### 产物

- `dist/multi-site-video-downloader/multi-site-video-downloader.exe`
- `dist/multi-site-video-downloader-1.1.7-win64.zip`
- `dist/multi-site-video-downloader-1.1.7-setup.exe`

## 1.1.6 - 2026-11-06

### 安装器

- 安装程序从 Inno Setup 切换为 NSIS 3 + Modern UI 2。
- 安装界面改为简体中文，加入品牌欢迎图、图标和页脚版本。
- 保留开始菜单、可选桌面快捷方式和卸载程序。
- 继续使用同一卸载注册表项，支持从 1.1.5 覆盖升级。
- 覆盖升级时保留 `settings.json`、Cookie、下载历史和用户资源。
- 程序内更新改用 NSIS 静默参数 `/S`；安装器同时兼容旧版 Inno 更新器传入的 `/SILENT`。

### 验证

- 148 项自动化测试通过。
- NSIS 静默安装、安装后自检、卸载通过。
- 中文和空格路径安装、旧版 Inno 覆盖升级验证通过。
- 安装包版本资源为 `1.1.6.0`，界面语言为简体中文。

### 产物

- `dist/multi-site-video-downloader/multi-site-video-downloader.exe`
- `dist/multi-site-video-downloader-1.1.6-win64.zip`
- `dist/multi-site-video-downloader-1.1.6-setup.exe`

## 1.1.5 - 2026-11-06

### 新增

- 设置页新增「检查更新」和「启动时自动检查更新」。
- 自动请求 GitHub 最新 Release，比较版本并显示更新说明。
- 下载 `setup.exe` 到 `updates/`，使用 Release 中的 SHA256 文件校验。
- 安装版可以直接关闭程序并覆盖升级；免安装版提示手动替换。
- 下载更新前检查是否有下载任务，避免中断正在进行的下载。

### 验证

- 147 项自动化测试通过。
- 实际请求 GitHub API 成功，能识别 1.1.4 安装包和校验文件。
- 更新检查、版本比较、资产选择、SHA256 解析均有单元测试。

### 产物

- `dist/multi-site-video-downloader/multi-site-video-downloader.exe`
- `dist/multi-site-video-downloader-1.1.5-win64.zip`
- `dist/multi-site-video-downloader-1.1.5-setup.exe`

## 1.1.4 - 2026-11-04

### 修复

- 修复 B站扫码登录后仍显示“未登录”：Netscape 文件中的 session cookie（`expires=0`）被 `requests` 误判为过期并丢弃，加载时改为真正的 session cookie。
- 扫码登录成功后自动重新解析当前视频，登录状态和可用画质立即刷新。
- 预览来源在登录后显示“B站登录”，不再显示“未登录”。

### 界面

- 1080 宽度下自动收起右侧看板，避免主内容被挤压。
- 下载队列移入滚动区，窄窗口下下载设置不再被队列截断。
- 画质/格式卡片加大到 96×96，5 个画质与 MP4/MP3 保持一行。
- 品牌名改为单行，右侧保存路径和状态文字加大。

### 验证

- 142 项自动化测试通过。
- 真实 B站 Cookie 验证 `isLogin: true`，预览来源显示“B站登录”。
- 1080×720 与 1280×860 实机渲染检查通过。

### 产物

- `dist/multi-site-video-downloader/multi-site-video-downloader.exe`
- `dist/multi-site-video-downloader-1.1.4-win64.zip`
- `dist/multi-site-video-downloader-1.1.4-setup.exe`

## 1.1.3 - 2026-11-03

### 更新

- 内置 yt-dlp 升级到 2026.10.21，改善 YouTube 和通用站点解析。
- 重新构建 Windows 发行包。
- 新增 Windows 安装程序，包含开始菜单、可选桌面快捷方式和卸载程序。

### 验证

- 141 项自动化测试通过。
- 打包版 `--self-test` 通过：依赖、ffmpeg、Chrome、界面构建均正常。
- 安装包静默安装、安装后自检、卸载、开始菜单清理均通过。
- 含中文和空格的安装路径下自检通过。
- 当前 `cookies.txt` 的 B站 `SESSDATA` 已失效（`isLogin: false`），登录态 1080P+ 回归仍缺有效账号。

### 产物

- `dist/multi-site-video-downloader/multi-site-video-downloader.exe`
- `dist/multi-site-video-downloader-1.1.3-win64.zip`
- `dist/multi-site-video-downloader-1.1.3-setup.exe`

## 1.1.2 - 2026-10-27

### 新增

- B站未登录时选择“仅音频”不再直接失败：先下载公开 MP4，再用 ffmpeg 提取 MP3，最高约 720P。
- 已登录时仍优先使用 DASH 音频流，不受该回退影响。

### 验证

- 使用真实 B站视频完成未登录音频提取，得到 4:27 的 MP3，无残留源视频。
- 141 项自动化测试通过，打包版版本资源更新为 `1.1.2`。

### 产物

- `dist/multi-site-video-downloader/multi-site-video-downloader.exe`
- `dist/multi-site-video-downloader-1.1.2-win64.zip`

## 1.1.1 - 2026-10-25

### 修复

- 右侧面板可用空间较长时文字被裁切。
- CI 增加 pytest 失败注解，便于直接从 GitHub 查看失败测试。
- 打包版 `--self-test`、含空格路径启动、版本资源 `1.1.1` 通过。

### 产物

- `dist/multi-site-video-downloader/multi-site-video-downloader.exe`
- `dist/multi-site-video-downloader-1.1.1-win64.zip`

## 1.1.0 - 2026-10-25

项目名称统一为功能描述式的开源项目名 **Multi-site Video Downloader（多站点视频下载器）**，Python 包同步为 `multisite_downloader`。

### 新增

- B站：扫码登录、浏览器 Cookie 静默复用、多分 P、DASH 格式、公开接口兜底、封面、字幕、弹幕。
- 抖音：无头 Chrome/Edge 解析、页面画质完整读取与切换、4K/2K 画质、视频/音频合并、封面截图、前 5 分钟内置预览。
- 通用站点：yt-dlp 下载、格式表、代理、文件名模板、编码/音频质量偏好、仅音频 MP3。
- 任务：持久化队列、逐任务设置、修改清晰度/目录、排序、单任务与批量重试、清理已结束任务。
- 历史：搜索、状态筛选、文件缺失筛选、CSV/JSON 导出、重新下载。
- Cookie 库管理、代理测试、下载限速、版本与诊断信息、日志轮转、临时文件清理。
- 可选 mpv 内置播放：抖音预览和历史/队列中的已完成文件。

### 修复

- 抖音 4K/2K 页面画质未显示。
- 抖音画质菜单延迟加载时，所选画质可能未实际切换；现在会在菜单出现前重试，最多等待 10 秒。
- B站兜底下载合并失败后残留 `.part` 文件、无音轨文件误判、弹幕 XML 重复包裹。
- 历史表排序后操作错位；删除/取消队列任务后进度统计错误。
- 浏览器 Cookie 读取、YouTube 静默 Cookie、代理与下载流的资源释放问题。

### 发布与验证

- 140 项自动化测试通过，包含无 ffmpeg 环境。
- 打包版 `--self-test`、含空格路径启动、版本资源 `1.1.0` 通过。
- 实机跑通 B站游客下载、抖音 4K 捕获/540P 下载/MP3、双任务并发队列、暂停/继续/取消、代理、托盘与 mpv 播放。
- 仍待有效凭据补测：B站登录后的 1080P+/字幕/仅音频；YouTube 浏览器 Cookie 复用成功路径；真实外部代理；第二台干净 Windows 环境。

### 产物

- `dist/multi-site-video-downloader/multi-site-video-downloader.exe`
- `dist/multi-site-video-downloader-1.1.0-win64.zip`

### 许可

第三方组件许可见 `THIRD_PARTY_NOTICES.md`。PyQt5 使用 GPL v3 / 商业双许可；本仓库自身许可证尚待维护者确定。
