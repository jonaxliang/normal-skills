---
name: download-douyin-video
description: Use whenever a user asks to download, save, export, or batch-fetch public Douyin videos, including Chinese requests such as 下载、保存或导出抖音视频, especially when they paste a v.douyin.com share link, a www.douyin.com/video URL, or a full share message and want a playable MP4, a best-effort clean version, or a specific output directory.
---

# Download Douyin Video

下载公开可访问的抖音视频。优先运行随技能提供的确定性脚本：它会从分享文本提取链接、解析作品 ID、读取抖音移动分享页、获取媒体地址、保存 MP4，并验证媒体流。不要先尝试需要 Cookie 的下载器。

需要 Python 3.10+ 和网络访问。`ffprobe` 是可选依赖，但建议安装以完成严格的媒体流验证；缺失时脚本只做有限的 MP4 文件头检查。

## 快速执行

```bash
python3 <skill-dir>/scripts/download_douyin_video.py \
  'https://v.douyin.com/example/' \
  --output-dir douyin-videos
```

如果只拿到整段分享口令，优先由调用方提取其中的抖音 URL 后再传入。需要直接传整段文本时可走标准输入，避免内容出现在进程参数中：

```bash
python3 <skill-dir>/scripts/download_douyin_video.py \
  --stdin --output-dir douyin-videos
```

把用户提供的原始文本写入上述进程的标准输入。构造 shell 命令时不要把未转义的用户文本拼接进命令字符串。

可一次传入多个已提取的链接：

```bash
python3 <skill-dir>/scripts/download_douyin_video.py \
  'https://v.douyin.com/example-1/' \
  'https://v.douyin.com/example-2/' \
  --output-dir douyin-videos
```

默认尝试请求抖音的 clean play 端点，并以 `<作者>-<标题>-<作品ID>.mp4` 命名；这是 best effort，脚本无法通过像素内容证明画面一定无水印。已有有效文件会跳过；需要覆盖时加 `--force`。用户明确要求保留平台水印时加 `--keep-watermark`，文件名会带 `-watermarked`。单个视频默认最大 2048 MiB，可用 `--max-size-mib` 调整。运行 `--help` 查看所有参数。

## 工作流

1. 从用户消息收集抖音分享短链、作品页链接或包含链接的完整分享文本。
2. 在用户指定目录运行脚本；未指定目录时使用当前目录的 `douyin-videos/`。
3. 检查每条 `[OK]`、`[SKIP]` 或 `[ERROR]` 结果。批量任务中单条失败不应删除其他成功文件。
4. 确认脚本汇总中的分辨率、时长和文件大小；若系统没有 `ffprobe`，脚本会退化为 MP4 文件头与非空检查，并标记 `UNVERIFIED`，交付时明确说明验证有限。
5. 向用户提供成功文件或输出目录的可点击链接，并单独列出失败项。

## 浏览器兜底

仅当脚本提示作品不存在、公开分享页结构变化、登录墙或 CAPTCHA 时使用 Browser/Chrome 控制 Skill：

1. 打开用户给出的原始分享链接，确认页面确实对应目标作品。
2. 优先使用页面媒体资源能力导出当前视频；其次从页面可见的 `video` 元素或公开 hydration 数据定位媒体 URL。
3. 不读取、导出或落盘 Cookie、密码、localStorage、浏览历史等账户数据。
4. 遇到 CAPTCHA、登录或下载权限提示时遵守浏览器安全确认要求，不绕过验证。

如果只能获得带水印版本，应如实说明，不要声称是无水印文件。

## 验证

脚本会自动验证。需要人工复核时运行：

```bash
ffprobe -v error \
  -show_entries format=duration,size:stream=codec_type,codec_name,width,height \
  -of json '<downloaded-file>.mp4'
```

经 `ffprobe` 验证的成功文件应包含视频流、时长大于零，且文件大小合理。没有 `ffprobe` 时只完成基础文件头检查，不能声称已验证完整性。不要把 HTML 风控页或 JSON 错误响应改名为 MP4 交付。

## 常见问题

- 分享口令含大量文字：优先先提取其中的抖音 URL；必须传原文时使用 `--stdin`，不要把整段文本放进位置参数。
- 短链已失效或作品已删除/仅自己可见：报告无法公开访问，不生成空文件冒充成功。
- 平台返回 HTTP 403/429：脚本会对媒体候选有限重试；持续失败则使用浏览器兜底，不循环轰炸接口。
- 系统没有 `ffprobe`：仍可下载，但交付时注明仅完成基础文件校验。
- 文件名包含非法字符或过长：脚本会清理并截断，不改变作品 ID。
- 用户要求下载他人受限、付费或私密内容：不要绕过访问控制。
