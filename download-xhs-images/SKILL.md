---
name: download-xhs-images
description: Use whenever a user asks to scrape, download, save, or batch-export every image from one or more public Xiaohongshu (RED) image posts, including Chinese requests such as 抓取、下载或保存小红书图片, especially when images should be grouped by post ID, converted to JPG/JPEG, or exported without WebP files.
---

# Download Xiaohongshu Images

批量获取公开小红书图文帖子中的图片。优先运行确定性脚本；仅在公开页面受登录墙或结构变化影响时使用浏览器兜底。

需要 Python 3.8+ 和网络访问。JPEG 转换与完整校验还需要 macOS 自带的 `sips` 或 Pillow。

## 快速执行

```bash
python3 <skill-dir>/scripts/download_xhs_images.py \
  '<xiaohongshu-url-1>' '<xiaohongshu-url-2>' \
  --output-dir xhs-images
```

脚本只访问 HTTPS 小红书页面及受信任的图片 CDN，按帖子 ID 去重并输出 `xhs-images/<post-id>/01.jpg`。默认 JPG 质量为 92，单张图片上限为 64 MiB；已有且通过完整校验的 JPG 会跳过，损坏文件会重新下载，需要覆盖有效文件时加 `--force`。新文件完成转换和校验后才会原子替换旧文件。运行 `--help` 查看超时、大小上限等参数。

## 工作流

1. 从用户消息中收集所有帖子 URL，保留完整查询参数。
2. 在用户指定的目录运行脚本；未指定时使用当前目录下的 `xhs-images/`。
3. 检查进程退出码和汇总；单个帖子失败不应阻止其他帖子。
4. 确认每个成功帖子目录只有 JPG，没有临时 WebP。
5. 提供输出目录的可点击链接，并列出失败的帖子。

## 浏览器兜底

脚本报告“公开页面中未找到图片”时：

1. 使用可用的 Browser/Chrome 控制 Skill 打开原始分享 URL。
2. 读取 `script[type="application/ld+json"]` 的 `image` 数组；必要时从 hydration 数据的 `imageList[].urlDefault` 提取。
3. 将 `http:` 规范化为 `https:`，去重并下载资源。
4. 以两位序号写入对应 `<post-id>/`，转换为 JPEG 后删除中间文件。

不要读取或导出 Cookie、密码、localStorage 等账户数据。遇到 CAPTCHA 必须遵循浏览器安全确认要求。

## 验证

```bash
find xhs-images -type f
file xhs-images/*/*.jpg
find xhs-images -type f -name '*.webp'
```

每个 JPG 必须能被 `file` 识别为 JPEG，最后一条命令应无输出。

## 常见问题

- URL 缺少 `/discovery/item/<24位ID>` 或 `/explore/<24位ID>`：要求用户提供帖子详情页链接。
- 视频帖子或已删除帖子：报告不支持或失效，不要生成空目录冒充成功。
- macOS 外没有 `sips`：安装 Pillow 后重试；脚本会自动使用 Pillow。
- 批量中部分失败：保留成功结果并明确列出失败项，不要删除整批输出。
