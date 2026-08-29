# Normal Skills

[![CI](https://github.com/jonaxliang/normal-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/jonaxliang/normal-skills/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## 有什么

| Skill | 用途 |
| --- | --- |
| [`diagnose-macos-performance`](diagnose-macos-performance/SKILL.md) | 只读诊断 Mac 卡顿、发热和 Terminal 启动慢 |
| [`humanize-writing`](humanize-writing/SKILL.md) | 调整中英文文本的模板感和机械感，同时保留原意 |
| [`download-xhs-images`](download-xhs-images/SKILL.md) | 批量下载公开小红书图文帖里的图片并转成 JPG |
| [`download-douyin-video`](download-douyin-video/SKILL.md) | 下载公开可访问的抖音视频并校验 MP4 |
| [`qzone-post-recovery`](qzone-post-recovery/SKILL.md) | 扫码从历史痕迹中找回本人删掉的 QQ 空间说说，并生成私有离线档案 |

## 安装

```bash
git clone https://github.com/jonaxliang/normal-skills.git
cd normal-skills
mkdir -p "$HOME/.agents/skills"
ln -s "$PWD/humanize-writing" "$HOME/.agents/skills/humanize-writing"
```

把 `humanize-writing` 换成想装的 Skill 即可。项目内使用时也可以放进 `.agents/skills`；其他 Agent 按各自的 Skills 目录安装。

## 使用

使用时直接点名 Skill：

```text
$humanize-writing 把下面这段话改得自然一点，但不要改变原意：……
```

每个目录的 `SKILL.md` 里有更具体的说明和示例。

## 环境

统一建议使用 Python 3.10+。Mac 性能诊断只支持 macOS；小红书图片转换需要 `sips` 或 Pillow；抖音视频建议安装带 `ffprobe` 的 FFmpeg；QQ 空间历史归档需要 `requests`、网络访问和用户本人扫码登录。

## 说明

下载类 Skill 只处理公开内容，不绕过登录或其他访问限制。请自行遵守平台规则、版权和隐私要求。平台页面改版后脚本偶尔失效也很正常。

这个仓库和 OpenAI、抖音、小红书、腾讯 QQ 没有隶属或授权关系。

## License

[MIT](LICENSE)。下载到的第三方内容不在本许可证范围内。
