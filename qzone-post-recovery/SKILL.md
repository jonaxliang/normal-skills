---
name: qzone-post-recovery
description: Recover content and image remnants from a user's own deleted QQ Zone (QQ空间) 说说 after QR-code login, separating recoverable deletion candidates from uncertain records and current posts in a private offline archive. Use for 找回、恢复或导出本人删除的说说; not for restoring posts back to QQ, accessing other people's private spaces, or bypassing access controls.
---

# 找回被删掉的 QQ 空间说说

扫码登录用户自己的 QQ 空间，从仍然存在的历史互动痕迹中找回已删除说说可能留下的正文和图片，再整理成可离线打开的时光档案。找回的是残留内容和删除候选，不会把说说重新发布到 QQ；产物不包含 XLSX。

## 执行前

1. 用自然、友好的方式提醒用户：找回结果来自历史残留与当前列表的核对，不是 QQ 官方删除证明；没有留下历史痕迹的内容无法恢复。
2. **每次执行都先请用户选择产物包的父目录。** 可以这样问：`📁 想把这份时光档案保存到哪里？请给我一个目录。` 用户未指定时不要擅自落到仓库或 Skill 目录。
3. 只处理用户本人扫码授权可见的数据。不要要求用户粘贴 Cookie，不要输出或交付登录 Cookie。会话缓存和最终档案必须保持私有文件权限。在 WSL 中只允许保存到 Linux 文件系统（如 `/home/<用户>/archives`）；不要使用 `/mnt/c`、`/mnt/e` 等 Windows 挂载盘，因为 POSIX 权限不能可靠代表 Windows ACL。

## 交互风格

- 把整个过程描述成“整理时光档案”，避免照搬原项目里偏程序化的“获取数量、读取列表、Pause”等提示。
- 关键节点可以使用少量容易辨认的符号：`📁` 选目录、`📱` 扫码、`✓` 成功、`↻` 刷新、`⚠` 风险、`📦` 产物包。
- 提示保持短句，一次只说一件事。不要连续堆叠 emoji，也不要用庆祝语掩盖错误或不完整结果。
- 出错时先说明哪里没完成，再给一个可以执行的下一步；不要只抛异常堆栈。

## 快速执行

```bash
python3 <skill-dir>/scripts/qzone_post_recovery.py \
  --output-dir '<用户选择的目录>'
```

脚本会用四个清晰阶段陪用户完成找回：

1. 检查本机私有会话缓存；需要重新登录时生成二维码，等待用户用手机 QQ 扫码确认。
2. 分页读取历史互动流，并保留发布者 UIN、历史昵称、正文、时间和图片线索。
3. 完整读取当前可见说说及接口允许返回的评论，再核对历史记录；当前列表不完整时禁止把未匹配记录判为删除。
4. 下载可访问的图片和视频封面，生成“找回的说说 / 待确认 / 发布事件 / 当前可见 / 其他互动”五个标签页。

常用选项：

```bash
# 强制重新扫码，不复用本机私有会话
python3 <skill-dir>/scripts/qzone_post_recovery.py \
  --output-dir '<目录>' --fresh-login

# 调试或恢复时限制最多扫描的历史记录数量
python3 <skill-dir>/scripts/qzone_post_recovery.py \
  --output-dir '<目录>' --max-history 2000
```

需要 Python 3.10+、`requests>=2.33.0,<3` 和网络访问。缺少依赖时安装：

```bash
python3 -m pip install 'requests>=2.33.0,<3'
```

## 产物包

脚本在用户选择的父目录内新建 `qzone-post-recovery-<QQ号>-<时间>/`：

```text
qzone-post-recovery-<QQ号>-<时间>/
├── index.html
├── assets/
│   └── images/
└── data/
    └── archive.json
```

- `index.html`：PC 优先的暗色档案页，可搜索、按年份和类型筛选、分页、查看详情与评论。
- `assets/images/`：成功下载的说说图片和视频封面；失败资源保留经过 QQ HTTPS 白名单校验的远程链接，并在结果摘要中计数。
- `data/archive.json`：网页使用的结构化数据和扫描摘要，方便后续重新分析；不包含 Cookie。

不要额外创建 Excel、CSV 或电子表格。

## 判定与结果边界

- “找回的说说”：当前列表完整读取后，历史互动流中能通过发布者 UIN（无 UIN 时才回退昵称）归属为本人发布/转发，但无法与当前记录匹配的内容。它是删除候选，仍需人工确认。
- “待确认”：当前列表读取失败或不完整时，所有未匹配的本人历史内容；不得称为已删除或计入找回候选。
- “发布事件”：历史接口只留下“发表说说”动作但没有正文的记录；单独展示，不计入找回候选。
- “当前可见”：当前说说接口仍能返回的记录，包含接口返回的评论列表。
- “其他互动”：由他人发布、评论相关、转发相关或包含 `@` 线索的历史内容。发布者只从记录结构或明确的文本前缀提取；不要因为正文提到本人昵称就认定为本人发布。
- 已删除说说通常没有可再次请求的 `tid`，因此无法保证拉取完整评论；历史互动流若留下评论痕迹，只作为“其他互动/历史线索”呈现。

## 验证与交付

执行结束后检查：

```bash
test -f '<产物包>/index.html'
test -f '<产物包>/data/archive.json'
find '<产物包>' -iname '*.xlsx' -o -iname '*.xls'
```

最后一条命令应无输出。读取脚本末尾摘要，报告找回候选、待确认、发布事件、当前可见、其他互动、评论和图片数量，并提供 `index.html` 和产物目录。若当前说说扫描不完整，明确说明本次没有做删除判定；若历史扫描不完整，说明找回结果可能有遗漏。

## 常见问题

- 二维码没有自动打开：按终端输出的路径手动打开 `QR.png`；过期后脚本会生成新二维码。
- 返回未登录或权限错误：加 `--fresh-login` 重新扫码。
- HTTP 429/频繁失败：停止高频重试，稍后再运行；不要绕过平台限制。
- 图片下载失败：网页仍保留原始 URL；QQ CDN 链接过期后可能无法恢复。
- 找回候选数量异常偏高：先核对 `comparison_complete`，再查看每条记录的判定依据；候选不能直接等同于 QQ 官方确认的删除记录。
