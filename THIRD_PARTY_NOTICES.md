# 第三方组件与许可说明

本项目使用或可选捆绑以下第三方组件。许可证以各组件官方发布内容为准；发行二进制时请一并保留相应许可证文本。

## 运行时依赖

| 组件 | 用途 | 许可证 |
| --- | --- | --- |
| PyQt5 5.15 | 桌面界面 | GPL v3（或 Riverbank 商业许可） |
| Qt 5 | PyQt5 底层框架 | LGPL v3 / GPL v3 / 商业许可 |
| yt-dlp | 通用站点解析与下载 | Unlicense |
| requests | HTTP 请求 | Apache-2.0 |
| urllib3 / certifi / charset-normalizer / idna | requests 依赖 | Apache-2.0 / MPL-2.0 / MIT / BSD |
| browser-cookie3 | 读取本机浏览器 Cookie | LGPL |
| websocket-client | 抖音/登录页面的 CDP 通信 | Apache-2.0 |
| qrcode | B站扫码登录二维码 | BSD |
| Pillow | qrcode 图片来源支持 | HPND（Pillow License） |
| pytest | 仅开发测试 | MIT |

## 可选外部程序

| 组件 | 用途 | 许可证 |
| --- | --- | --- |
| FFmpeg | 音视频合并、转码、媒体信息、预览生成 | LGPL-2.1+ 或 GPL（取决于具体构建选项） |
| mpv | 软件内预览与本地播放 | GPL-2.0+ 或 LGPL-2.1+（取决于构建） |

`ffmpeg.exe` 与 `mpv.exe` 不是由本项目编译，发行时应以所用构建的官方许可证为准。例如包含 GPL 组件的 FFmpeg 构建整体按 GPL 分发。

## PyQt5 授权提醒

PyQt5 的 GPL v3 版本要求以 GPL v3 兼容方式分发整个应用程序，并提供对应源代码；闭源商业发行需要购买 Riverbank 的商业许可。当前仓库尚未声明自身开源许可证，公开发布前请先确定项目的授权方式并满足上述要求。
