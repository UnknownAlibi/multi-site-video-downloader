# 更新日志

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
