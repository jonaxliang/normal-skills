---
name: diagnose-macos-performance
description: Use when a macOS computer feels slow, laggy, frozen, unusually hot, or slow to open Terminal/apps, or when CPU, memory pressure, swap, disk I/O, process count, WindowServer, kernel_task, Spotlight, security agents, or shell startup may be abnormal.
---

# Diagnose macOS Performance

用持续、可归因的数值证据诊断 Mac 卡顿。先测量，再提出单一假设；在根因证据闭环前不要给修复结论。

## 快速采样

先告知用户默认流程只读，再运行：

```bash
python3 <skill-dir>/scripts/diagnose_macos_performance.py --duration 10
```

用户明确提到 Terminal、Ghostty、iTerm 或 shell 启动慢时，先说明下一探针会真实启动 login shell 五次，可能触发其启动脚本中的缓存、凭据、网络或后台动作；取得明确授权后才运行：

```bash
python3 <skill-dir>/scripts/diagnose_macos_performance.py --duration 10 --include-shell
```

需要稳定字段时使用 `--format json`。保留所有 limitations；不要因部分探针无权限而提权。

## 调查流程

1. 区分测量事实和历史高水位：load 不等于 CPU；Swap/压缩内存总量不等于当前换页。优先看多样本 CPU、swap 速率、磁盘 TPS/传输块大小和每进程 I/O 速率。
2. 区分容量、健康和活动：磁盘空间、SMART、实时 I/O 是三类证据；不要因磁盘忙就声称 SSD 损坏。
3. 从最强持续异常形成一个假设。若某 PID 高 I/O，先复测速率，再用 `lsof -n -P -p <pid>` 确认文件；用 `sample <pid> 3 5 -file /dev/stdout` 确认调用栈。权限不足时记录局限，不使用 `sudo`。
4. 只有调用栈或打开文件指向应用完整性检查时，才对具体 app 运行：

```bash
codesign --verify --strict --verbose=4 '/path/to/Suspect.app'
```

5. 对 Terminal 症状比较 clean zsh 与五次真实 login zsh。clean 快、真实慢才进入 rc 排查；只记录文件大小、耗时和初始化器名称，不回显配置内容、参数、Token 或环境变量。
6. 每次补充证据后更新同一个假设。证据否定它时再换下一个，不并行猜多个根因。

## 因果置信度

- **测量事实**：采样值、进程速率、文件描述符、调用栈、签名结果。
- **强支持链**：持续异常 → 对应资源/操作 → 匹配的调用栈或事件 → 与该假设相适配的独立验证。应用完整性问题可用 `codesign`，索引、编译、内存或网络问题使用各自的独立证据。关键链路缺失时不得写“强支持”或“根因”，只能写“当前最高优先级假设”，并指出缺失证据。
- **推断**：安全进程、Spotlight、WindowServer 与异常同时出现但缺少直接链路。

报告时给每条结论标注层级。不要把相关性写成因果，也不要把单次 `ps` CPU 当成持续占用。

## 安全与隐私门禁

- 默认禁止 `sudo`、`kill/pkill`、重启、`purge`、删缓存/Swap、`defaults write`、修改 `launchctl`、停止索引/备份、停用防病毒或终端安全软件，或改显示器/电源设置。
- 不运行 `env`，不采集浏览器状态，不使用完整 `command`/args；只使用 `comm`。展示路径时把 HOME 替换为 `~`。
- `fs_usage`、`powermetrics`、全卷校验等提权或重探针不默认运行。
- 任何会改变进程状态的 A/B 测试都先取得用户明确授权，并说明会暂时失去的功能。

## 输出要求

给出采样时间、机器核数/内存、load、CPU user/system/idle、内存压力/压缩/Swap 及速率、磁盘容量/SMART/I/O、进程与线程数、Top CPU/RSS/I/O、shell 对比和热状态。按证据强度列出首要原因、次要放大因素、已排除项与局限；同时注明采样器本身带来的轻微开销。
