Multi-site Video Downloader · 界面素材目录

把下面 4 个文件放在本目录，文件名必须一致：

  mascot_left.png    左侧看板娘    建议 600×900，PNG 透明背景
  mascot_right.png   右侧看板娘    建议 600×900，PNG 透明背景
  bg_sky.png         窗口背景      建议 1920×1080 或 2560×1440，PNG/JPG/WebP
  logo.png           侧边栏图标    建议 256×256，PNG 透明背景（可选）

说明：
- 缺少图片不会报错，界面会显示占位框或程序绘制的渐变天空背景。
- 放好图片后重启程序即可生效。
- 打包版请把 assets 目录放在 exe 同级，例如：
  dist/multi-site-video-downloader/assets/
  程序会优先读取 exe 同级的 assets，其次读取打包内置的 assets。
- 背景图请用浅色、低对比的图片，避免影响文字阅读。
