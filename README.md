# Normal Skills

[![CI](https://github.com/jonaxliang/normal-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/jonaxliang/normal-skills/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

一组面向 Codex 与兼容 Agent Skills 运行时的实用技能。每个技能都以 `SKILL.md` 描述工作流，并尽量配套确定性 Python 脚本、单元测试、评测样例和界面元数据，便于审查、复用和持续验证。

## 技能列表

| 技能 | 用途 | 主要运行条件 |
| --- | --- | --- |
| [`diagnose-macos-performance`](diagnose-macos-performance/SKILL.md) | 以只读采样诊断 macOS 的 CPU、内存、磁盘、进程和 Terminal 启动性能 | macOS、Python 3.10+ |
| [`humanize-writing`](humanize-writing/SKILL.md) | 在保留事实、格式和作者立场的前提下，减少中英文文本的模板化与机械感 | Python 3.10+；扫描脚本可选 |
| [`download-xhs-images`](download-xhs-images/SKILL.md) | 批量下载公开小红书图文帖图片并转换为 JPG | Python 3.10+、网络；`sips` 或 Pillow |
| [`download-douyin-video`](download-douyin-video/SKILL.md) | 下载公开可访问的抖音视频并验证 MP4 | Python 3.10+、网络；建议安装 `ffprobe` |

## 安装

先克隆仓库：

```bash
git clone https://github.com/jonaxliang/normal-skills.git
cd normal-skills
```

Codex 会从用户级的 `$HOME/.agents/skills` 和仓库级的 `.agents/skills` 目录发现技能。下面以用户级安装 `humanize-writing` 为例：

```bash
mkdir -p "$HOME/.agents/skills"
ln -s "$PWD/humanize-writing" "$HOME/.agents/skills/humanize-writing"
```

将目录名替换为其他技能即可。安装前请先确认目标路径不存在；使用符号链接可以让本地 `git pull` 后的更新立即生效。也可以在 Codex 中调用 `$skill-installer`，让它从本仓库的指定技能目录安装。

如果技能没有立即出现，请刷新技能列表或重启 Codex。

## 使用

在 Codex CLI 或 IDE 扩展中，可以输入 `/skills` 浏览技能，或在提示词中显式使用技能名：

```text
$humanize-writing 把下面这段中文改得更自然，但保留所有数字和专有名词：……
```

```text
$diagnose-macos-performance 检查这台 Mac 最近为什么变卡，只做只读诊断。
```

各技能目录内的 Python 脚本也可以独立运行：

```bash
python3 humanize-writing/scripts/audit_ai_flavor.py --help
python3 diagnose-macos-performance/scripts/diagnose_macos_performance.py --help
python3 download-xhs-images/scripts/download_xhs_images.py --help
python3 download-douyin-video/scripts/download_douyin_video.py --help
```

## 依赖

仓库测试和主要脚本优先使用 Python 标准库，没有统一的强制第三方依赖。部分能力有可选依赖：

- `certifi`：在本机 Python 缺少可用 CA 证书时提供证书包。
- Pillow：在非 macOS 环境中将小红书图片转换并校验为 JPEG。
- `ffprobe`（随 FFmpeg 提供）：严格校验抖音视频的媒体流、时长和分辨率。

按需安装 Python 可选依赖：

```bash
python3 -m pip install certifi Pillow
```

## 开发与验证

运行仓库结构校验和全部单元测试：

```bash
python3 scripts/validate_repository.py
python3 scripts/run_tests.py
```

也可以只运行单个技能的测试：

```bash
python3 -m unittest discover -s humanize-writing/tests -p 'test_*.py' -v
```

持续集成会在多个 Python 版本上执行结构校验、字节码编译和全部测试。

## 仓库结构

```text
normal-skills/
├── <skill-name>/
│   ├── SKILL.md
│   ├── agents/openai.yaml
│   ├── evals/evals.json
│   ├── scripts/
│   └── tests/
├── docs/
├── scripts/
└── .github/
```

## 安全与合规

- 下载类技能只处理公开可访问内容，不绕过登录、验证码、付费或其他访问控制。
- 请遵守适用法律、平台规则、版权和隐私要求；下载成功不代表获得内容的复制、传播或商用授权。
- 不要在 issue、日志或测试夹具中提交 Cookie、Token、个人数据或私有链接。
- 平台页面和公开接口可能变化，下载类技能不保证长期可用或必然获得无水印版本。

本项目是独立的社区项目，与 OpenAI、抖音、小红书及其关联公司不存在隶属、授权或背书关系。相关名称和商标归各自权利人所有。

## 参与贡献

提交改进前请阅读 [贡献指南](CONTRIBUTING.md)。安全问题请通过 GitHub 的私密漏洞报告入口提交，不要创建包含漏洞细节的公开 issue。

## 许可证

仓库中的代码和文档采用 [MIT License](LICENSE)。通过本工具获取的第三方内容不在该许可证授权范围内。
