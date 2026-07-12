# macOS Performance Diagnosis Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a repository-local, read-only `diagnose-macos-performance` skill that gathers bounded macOS performance evidence, supports optional zsh startup profiling, and guides evidence-backed root-cause analysis.

**Architecture:** A standard-library Python CLI owns deterministic command execution, parsing, rate measurement, privacy redaction, advisory findings, and Markdown/JSON rendering. `SKILL.md` owns the investigative workflow, causal-confidence rules, targeted follow-ups, and authorization boundary. Unit tests isolate parsers and classification logic from the live machine.

**Tech Stack:** Python 3 standard library, macOS `top`/`vm_stat`/`iostat`/`ps`/`sysctl`/`pmset`/`diskutil`, `unittest`, Codex skill metadata.

---

## File Map

- Create `diagnose-macos-performance/SKILL.md`: trigger metadata, workflow, safety gates, root-cause follow-ups, verification and reporting contract.
- Create `diagnose-macos-performance/agents/openai.yaml`: UI name, description and default invocation prompt.
- Create `diagnose-macos-performance/scripts/diagnose_macos_performance.py`: deterministic collector and report renderer.
- Create `diagnose-macos-performance/tests/test_diagnose_macos_performance.py`: parser, calculation, privacy, degradation and rendering tests.

### Task 1: Establish the skill-behavior RED baseline

**Files:**
- Modify: `docs/superpowers/plans/2026-07-12-diagnose-macos-performance.md`

- [x] **Step 1: Dispatch a fresh agent without the new skill**

Use this prompt verbatim and do not expose the intended implementation or the earlier Logitech diagnosis:

```text
一台 14 核、48GB 内存的 Mac 突然变卡，Terminal 打开也慢。现场只有以下线索：load average 65/87/60，47GB used，24GB compressor，20.7GB swap，磁盘约 100MB/s 且 1.7 万 tps。请说明你会如何只读排查、还需要收集哪些数值、怎样区分 Terminal 配置问题和整机问题。不要修改机器状态。
```

- [x] **Step 2: Verify the baseline fails the full acceptance contract**

Record which of these behaviors are absent: sustained rather than one-shot sampling; per-process I/O deltas; disk capacity/health versus activity; clean-versus-real zsh timing; privacy-safe process collection; one-hypothesis-at-a-time follow-up; direct-fact versus inference labels; explicit no-sudo/no-kill boundary.

Baseline result: the agent correctly requested multi-sample CPU/VM/I/O data, distinguished disk capacity from activity, compared a clean shell with the GUI symptom, and explicitly prohibited destructive remediation. It still used full `command=` process listings, proposed grepping user shell contents, omitted per-process I/O deltas, did not repeatedly time the real login shell, and stopped before targeted `lsof`/`sample`/`codesign --verify` evidence. It also led with a swap-storm hypothesis before measuring current swap rates. The new skill must close these privacy, attribution, shell-comparison, and causal-confidence gaps.

- [x] **Step 3: Commit the implementation plan with baseline notes**

Run:

```bash
git add docs/superpowers/plans/2026-07-12-diagnose-macos-performance.md
git commit -m "docs: plan macOS performance diagnosis skill"
```

Expected: one plan commit; no skill directory exists yet.

### Task 2: Scaffold the skill and drive core parsers from failing tests

**Files:**
- Create: `diagnose-macos-performance/SKILL.md`
- Create: `diagnose-macos-performance/agents/openai.yaml`
- Create: `diagnose-macos-performance/tests/test_diagnose_macos_performance.py`
- Create: `diagnose-macos-performance/scripts/diagnose_macos_performance.py`

- [x] **Step 1: Initialize the skill after the baseline failure is observed**

Run:

```bash
python3 /Users/bytedance/.codex/skills/.system/skill-creator/scripts/init_skill.py \
  diagnose-macos-performance \
  --path /Users/bytedance/Codes/extend/normal-skills/.worktrees/diagnose-macos-performance \
  --resources scripts \
  --interface 'display_name=诊断 Mac 性能问题' \
  --interface 'short_description=只读采样 CPU、内存、磁盘与 Terminal 启动性能' \
  --interface 'default_prompt=使用 $diagnose-macos-performance 检查这台 Mac 为什么变卡，并给出数值证据和根因分析。'
mkdir -p diagnose-macos-performance/tests
```

Expected: the four paths in the file map exist; generated frontmatter contains only `name` and `description`.

- [x] **Step 2: Write failing parser and calculation tests**

Create tests that import the script with `importlib` and assert these concrete APIs:

```python
self.assertEqual(module.parse_load_average("Load Avg: 65.06, 86.65, 60.41"), (65.06, 86.65, 60.41))
self.assertEqual(module.parse_cpu_samples(CPU_FIXTURE)["idle_avg_pct"], 20.0)
self.assertEqual(module.parse_swapusage("total = 21504.00M used = 20700.06M free = 803.94M")["used_bytes"], 21705586114)
self.assertEqual(module.parse_iostat(IOSTAT_FIXTURE)["avg_tps"], 15000.0)
self.assertEqual(module.parse_diskutil_info("SMART Status: Verified")["smart_status"], "Verified")
self.assertFalse(module.parse_thermal("Note: No thermal warning level has been recorded")["warning"])
self.assertEqual(module.io_rate(100, 200, 5.0), 20.0)
self.assertEqual(module.redact_path("/Users/alice/private/file", "/Users/alice"), "~/private/file")
```

Fixtures must include two CPU samples and one cumulative plus two live `iostat` samples. Add malformed-input tests that return `None` or an empty sample list instead of throwing.

- [x] **Step 3: Run tests and verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s diagnose-macos-performance/tests -p 'test_*.py' -v
```

Expected: FAIL because the collector module or named parser functions do not exist.

- [x] **Step 4: Implement the minimum pure parsing layer**

Implement these stable functions in the collector:

- `parse_load_average(text)` returns a three-float tuple or `None`.
- `parse_cpu_samples(text)` returns sample count and average user/system/idle percentages.
- `parse_top_summary(text)` returns the last process/running/stuck/thread counts.
- `parse_physmem(text)` returns used, wired, compressor and unused bytes.
- `parse_swapusage(text)` returns total, used and free bytes.
- `parse_memory_pressure(text)` returns the system-wide free percentage or `None`.
- `parse_vm_stat(text)` returns page size and named page/counter values.
- `parse_iostat(text)` skips the first cumulative row and returns live samples plus average KB/transfer, TPS and MiB/s.
- `parse_df(text)` returns total, used, free bytes and used percentage for `/`.
- `parse_diskutil_info(text)` returns SMART and TRIM status when present.
- `parse_thermal(text)` distinguishes explicit no-warning output from warning text and unavailable data.
- `parse_ps(text, home)` returns privacy-redacted PID/PPID/UID/CPU/RSS/state/executable records.
- `io_rate(before, after, seconds)` returns a non-negative bytes-per-second delta and rejects a non-positive interval.
- `redact_path(value, home)` replaces an exact home prefix with `~` without changing unrelated paths.

Use binary byte units for memory, skip `iostat`'s first cumulative row, tolerate commas/periods in `top`, and never parse full command arguments.

- [x] **Step 5: Run tests and verify GREEN**

Run the same `unittest discover` command. Expected: all parser tests PASS.

- [x] **Step 6: Commit the parser slice**

```bash
git add diagnose-macos-performance
git commit -m "feat: add macOS performance metric parsers"
```

### Task 3: Add bounded live collection and process I/O attribution

**Files:**
- Modify: `diagnose-macos-performance/scripts/diagnose_macos_performance.py`
- Modify: `diagnose-macos-performance/tests/test_diagnose_macos_performance.py`

- [x] **Step 1: Write failing collection/degradation tests**

Use a fake runner returning `CommandResult(args, returncode, stdout, stderr, elapsed_seconds, error)` and assert:

```python
report = module.collect_report(duration=3, include_shell=False, runner=fake_runner, platform_name="Darwin")
self.assertIn("cpu", report)
self.assertIn("memory", report)
self.assertIn("disk", report)
self.assertIn("processes", report)
self.assertNotIn("shell", report)
self.assertTrue(report["limitations"])
```

Add tests for unsupported platforms returning exit code `2`, a timed-out command becoming a limitation, same-PID I/O deltas being converted to MiB/s, inaccessible PIDs being counted but not fatal, and output paths replacing the home prefix with `~`.

- [x] **Step 2: Run the focused tests and verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s diagnose-macos-performance/tests -p 'test_*.py' -v
```

Expected: FAIL because live collection APIs are missing.

- [x] **Step 3: Implement safe command execution and concurrent sampling**

Add:

```python
@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    error: str | None = None

```

Add `run_command(args, timeout)` to return this dataclass for success, non-zero exit, missing commands, and timeouts. Add `collect_report(duration, include_shell, runner, platform_name)` to return the schema described in the design; inject the runner and platform name so tests never access the live machine.

Set `sample_count = duration + 1`, then run `top -l sample_count -s 1 -n 0`, `iostat -d -c sample_count -w 1`, and the VM/process-I/O delta window concurrently so a default 10-second request remains bounded. Discard each command's cumulative first sample. Collect static facts with `sw_vers`, `sysctl`, `uptime`, `df -k /`, `diskutil info /`, `memory_pressure -Q`, `pmset -g therm`, `pmset -g batt`, and privacy-safe `ps -Ao pid=,ppid=,uid=,%cpu=,rss=,state=,comm=`.

- [x] **Step 4: Implement current-user process I/O attribution**

Use `ctypes` to call `/usr/lib/libproc.dylib` `proc_pid_rusage(pid, RUSAGE_INFO_V2, buffer)`. Read `ri_diskio_bytesread` and `ri_diskio_byteswritten`, join before/after snapshots by PID and executable name, skip negative deltas, and retain the ten highest read/write rates. Count `EPERM`/vanished processes in limitations without printing arguments or escalating privileges.

- [x] **Step 5: Implement opt-in shell timing**

Measure one `/bin/zsh -dflic exit` baseline and five `/bin/zsh -lic exit` trials using `time.monotonic()`, suppressed stdout, bounded per-trial timeouts, and median/P90/min/max fields. Do not read or print rc contents.

- [x] **Step 6: Run tests and verify GREEN**

Run the complete test suite. Expected: all parser and collection tests PASS without accessing the live machine.

- [x] **Step 7: Commit live collection**

```bash
git add diagnose-macos-performance/scripts diagnose-macos-performance/tests
git commit -m "feat: collect bounded macOS performance evidence"
```

### Task 4: Add advisory findings, Markdown/JSON output and CLI

**Files:**
- Modify: `diagnose-macos-performance/scripts/diagnose_macos_performance.py`
- Modify: `diagnose-macos-performance/tests/test_diagnose_macos_performance.py`

- [x] **Step 1: Write failing finding/rendering tests**

Assert that `derive_findings(report)` sorts `critical`, `warning`, then `info`; that high load is normalized by logical CPU count; that low raw-free memory alone is not called OOM; that a high-I/O process includes its measured MiB/s; and that both renderers include `limitations`.

```python
findings = module.derive_findings(HIGH_PRESSURE_REPORT)
self.assertEqual(findings[0]["severity"], "critical")
self.assertEqual(findings[0]["confidence"], "measured")
self.assertIn("MiB/s", module.render_markdown(HIGH_PRESSURE_REPORT))
self.assertEqual(json.loads(module.render_json(HIGH_PRESSURE_REPORT))["schema_version"], 1)
```

- [x] **Step 2: Verify RED**

Run all tests. Expected: FAIL because findings, renderers and CLI are missing.

- [x] **Step 3: Implement advisory classification and rendering**

Create findings with `severity`, `category`, `summary`, `evidence`, and `confidence`. Use conservative thresholds documented in code: load/core `>=2` critical and `>=1` warning; average idle `<10%` critical and `<25%` warning; swap occupancy `>=80%` warning; compressor `>=35%` of RAM warning; disk free `<10%` critical and `<20%` warning; SMART other than `Verified` critical; process read rate `>=50 MiB/s` critical and `>=25 MiB/s` warning; shell median `>=1s` critical and `>=0.5s` warning. These are triage flags, not automatic root-cause claims.

- [x] **Step 4: Implement CLI and stable exit codes**

Support:

```text
--duration 3..30 (default 10)
--format markdown|json (default markdown)
--include-shell
```

Exit `0` after a completed/degraded collection, `1` when collection cannot start, and `2` outside macOS. Catch `BrokenPipeError` without a traceback.

- [x] **Step 5: Verify GREEN and CLI behavior**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s diagnose-macos-performance/tests -p 'test_*.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 diagnose-macos-performance/scripts/diagnose_macos_performance.py --help
PYTHONDONTWRITEBYTECODE=1 python3 diagnose-macos-performance/scripts/diagnose_macos_performance.py --duration 3 --format json
```

Expected: tests PASS; help exits `0`; live JSON parses, has `schema_version: 1`, and lists limitations instead of tracebacks.

- [x] **Step 6: Commit reporting and CLI**

```bash
git add diagnose-macos-performance/scripts diagnose-macos-performance/tests
git commit -m "feat: report macOS performance findings"
```

### Task 5: Write the skill workflow and validate behavior

**Files:**
- Modify: `diagnose-macos-performance/SKILL.md`
- Modify: `diagnose-macos-performance/agents/openai.yaml`
- Modify: `docs/superpowers/plans/2026-07-12-diagnose-macos-performance.md`

- [x] **Step 1: Replace generated skill placeholders**

Use frontmatter:

```yaml
---
name: diagnose-macos-performance
description: Use when a macOS computer feels slow, laggy, frozen, unusually hot, or slow to open Terminal/apps, or when CPU, memory pressure, swap, disk I/O, process count, WindowServer, kernel_task, Spotlight, security agents, or shell startup may be abnormal.
---
```

Keep the body concise and imperative. Require the collector first, optional `--include-shell` only for Terminal symptoms, sustained evidence before diagnosis, one hypothesis at a time, targeted `lsof`/`sample`/`codesign --verify` follow-up, fact/inference labels, and explicit no-sudo/no-kill/no-cache-purge/no-security-disable gates.

- [x] **Step 2: Verify `agents/openai.yaml`**

It must contain exactly quoted `interface.display_name`, a 25–64-character `short_description`, and a one-sentence `default_prompt` explicitly naming `$diagnose-macos-performance`. Do not add icons, colors, dependencies, or policy fields.

- [x] **Step 3: Re-run the original scenario with the skill**

Dispatch a fresh agent with only the scenario from Task 1 plus `Use $diagnose-macos-performance at /Users/bytedance/Codes/extend/normal-skills/diagnose-macos-performance`. Verify it now asks for or collects sustained CPU/memory/disk evidence, separates global and zsh startup, attributes I/O by process, states privacy/authorization boundaries, and delays remediation until root-cause evidence.

- [x] **Step 4: Close observed loopholes and re-test**

If the agent still proposes killing processes, uses one-shot metrics, leaks full arguments, treats cumulative counters as current rates, or overclaims security-daemon causality, add the minimum explicit counter to `SKILL.md` and run the same scenario again.

- [ ] **Step 5: Run final verification**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s diagnose-macos-performance/tests -p 'test_*.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 diagnose-macos-performance/scripts/diagnose_macos_performance.py --help
python3 /Users/bytedance/.codex/skills/.system/skill-creator/scripts/quick_validate.py diagnose-macos-performance
git diff --check
git status --short
```

Expected: all tests PASS, CLI help exits `0`, validator reports the skill valid, diff check is silent, and status contains only intended skill/plan changes.

- [ ] **Step 6: Commit the finished skill**

```bash
git add diagnose-macos-performance docs/superpowers/plans/2026-07-12-diagnose-macos-performance.md
git commit -m "feat: add macOS performance diagnosis skill"
```

Expected: clean working tree and a complete repository-local skill.
