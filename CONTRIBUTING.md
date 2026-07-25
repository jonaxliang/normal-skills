# 贡献指南

感谢你改进 Normal Skills。这个仓库重视可审查的工作流、可复现的脚本行为和清晰的安全边界。

## 开始之前

1. 搜索现有 issue，确认问题尚未被报告或正在处理。
2. 对较大的新技能或行为变更，先创建 issue 说明使用场景、边界和验证方式。
3. 安全漏洞不要公开讨论，请通过 GitHub 的私密漏洞报告入口提交。

## 本地开发

需要 Git 和 Python 3.10+。克隆仓库后运行：

```bash
python3 scripts/validate_repository.py
python3 scripts/run_tests.py
```

仓库没有强制的第三方开发依赖。单个技能若引入可选依赖，必须在对应 `SKILL.md` 和根目录 `README.md` 中说明用途、安装方式和缺失时的退化行为。

## 修改现有技能

- 保持 frontmatter 的 `name` 与技能目录名一致。
- `description` 应准确覆盖触发场景，也要避免把无关请求吸引进来。
- 修改脚本行为时增加或更新单元测试；修改技能决策时同步更新 `evals/evals.json`。
- 保留输入校验、重定向限制、路径安全、大小上限、原子写入和隐私保护等现有安全约束。
- 不要在测试、文档或日志样例中加入真实 Token、Cookie、个人路径或私有内容。

## 添加新技能

新技能至少应包含：

```text
<skill-name>/
├── SKILL.md
├── agents/openai.yaml
└── evals/evals.json
```

有确定性逻辑时，再增加 `scripts/` 与 `tests/`。请同时满足：

1. 技能目录名、`SKILL.md` 的 `name` 和 `evals.json` 的 `skill_name` 完全一致。
2. `agents/openai.yaml` 的默认提示词显式包含 `$<skill-name>`。
3. 每个评测项都有唯一 `id`、输入提示、预期输出和 `files` 列表。
4. 脚本优先使用标准库；网络请求必须校验协议、主机、重定向、响应大小和输出路径。
5. 在根目录 `README.md` 的技能列表中补充用途和运行条件。

## Pull Request

提交前请确认：

- `python3 scripts/validate_repository.py` 通过；
- `python3 scripts/run_tests.py` 通过；
- `git diff --check` 无输出；
- 改动不包含生成文件、下载内容、凭据或个人信息；
- 用户可见行为、依赖和限制已写入文档；
- 只包含与该 PR 目标相关的改动。

建议在 PR 描述中写明背景、主要改动、验证结果和仍存在的限制。提交贡献即表示你同意将该贡献按仓库的 [MIT License](LICENSE) 授权。
