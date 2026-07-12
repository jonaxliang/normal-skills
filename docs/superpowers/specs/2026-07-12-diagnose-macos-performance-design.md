# macOS Performance Diagnosis Skill Design

Date: 2026-07-12  
Status: Approved for implementation

## Goal

Add a reusable `diagnose-macos-performance` skill that diagnoses reports such as “my Mac is slow”, “Terminal opens slowly”, “CPU/memory/disk usage looks abnormal”, or “the machine suddenly became laggy”. It must collect repeatable numerical evidence, separate global system pressure from application-specific startup cost, trace suspicious processes to a defensible root cause, and produce a ranked report with confidence and limitations.

The default workflow is read-only. It must not use `sudo`, terminate or restart processes, purge caches, delete swap, disable security software, edit shell configuration, or change power and display settings. Any later A/B test that changes process state requires explicit user authorization.

## Considered Approaches

1. **Instructions only:** one `SKILL.md` containing commands and interpretation rules. This is small, but repeated sampling and rate calculations are easy to perform inconsistently.
2. **Skill plus deterministic collector and tests:** keep judgment and targeted root-cause tracing in `SKILL.md`, while a standard-library Python CLI performs safe sampling and emits structured evidence. This is the selected approach because it balances repeatability, explainability, and scope.
3. **Full monitoring suite:** add background collection, historical storage, dashboards, and automatic remediation. This is rejected because it broadens the authorization and maintenance surface beyond a one-shot diagnostic skill.

## Repository Layout

```text
diagnose-macos-performance/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── scripts/
│   └── diagnose_macos_performance.py
└── tests/
    └── test_diagnose_macos_performance.py
```

This mirrors the repository's existing skill. No extra README, dependency manifest, persistent report directory, or reference file is required initially.

## Workflow

1. **Establish scope and safety.** Confirm macOS, record the symptom and time, state that collection is read-only, and avoid commands that expose environment variables or full process arguments.
2. **Collect a short baseline.** Record hardware, OS, uptime, load averages, CPU samples, process/thread counts, thermal and power warnings, memory compression, swap usage and rates, disk capacity/health, disk I/O rates, and top CPU/RSS processes.
3. **Attribute active pressure.** Measure per-process disk read/write deltas for accessible processes using `proc_pid_rusage`; aggregate processes by executable name without printing sensitive command-line arguments. Distinguish instantaneous rates from cumulative counters.
4. **Split global and Terminal-specific diagnosis.** Only when Terminal or shell startup is part of the symptom, compare a clean interactive zsh baseline with real login-shell startup. Do not dump shell files or environment variables. Inspect only sizes, timings, and relevant initialization command names.
5. **Form one hypothesis at a time.** Rank anomalies by sustained evidence. For a suspicious process, use targeted read-only follow-ups such as `lsof`, `sample`, and `codesign --verify` when applicable. Treat permission failures as limitations, not reasons to escalate privileges.
6. **Report causality carefully.** Separate directly measured facts, strongly supported causal chains, and weaker inferences. State diagnostic overhead and sampling-window limitations. Never recommend a fix before root-cause evidence exists.

## Collector Interface

The script uses only the Python standard library and macOS-provided commands.

```bash
python3 <skill-dir>/scripts/diagnose_macos_performance.py
python3 <skill-dir>/scripts/diagnose_macos_performance.py --duration 10 --format json
python3 <skill-dir>/scripts/diagnose_macos_performance.py --include-shell
```

- Default output is concise Markdown on stdout; `--format json` exposes stable structured data.
- `--duration` defaults to 10 seconds and accepts 3–30 seconds.
- `--include-shell` runs one clean-shell baseline and five login-shell trials; it is opt-in because sourcing user rc files can have normal incidental effects such as refreshing completion caches.
- Partial command failures produce a degraded report with explicit limitations rather than discarding successful evidence.
- Exit code `0` means collection completed (possibly with listed limitations), `1` means collection could not start, and `2` means the platform is unsupported.
- The default run writes no files and performs no network access.

## Analysis Rules

The collector derives flags but does not claim a final root cause by itself. Rules must account for machine size and measurement semantics:

- Compare load average with logical CPU count; do not equate load directly with CPU percentage.
- Report CPU user/system/idle distributions across multiple samples, not the first `top` sample.
- Interpret raw free memory together with memory pressure, compression, swap occupancy, and swap-in/out rates.
- Distinguish disk capacity and SMART status from current I/O activity; compute throughput, transactions per second, and average transfer size.
- Treat high process counts and aggregate RSS as contributors; shared/compressed pages mean RSS groups are not additive physical memory.
- Label code-signing or security-daemon involvement as inferred until a process stack, open-file evidence, or signature verification supports the chain.

## Privacy and Failure Handling

- Use executable names (`comm`) rather than full argument lists.
- Do not print environment variables, browser state, credentials, shell contents, or arbitrary user files.
- Redact the home-directory prefix from displayed paths.
- Skip inaccessible system processes and list the attribution gap.
- Do not run `fs_usage`, `powermetrics`, full-volume verification, or other privileged/heavy probes by default.
- Preserve successful sections when an individual command is missing, times out, or changes output format.

## Test Strategy

Skill behavior follows RED–GREEN–REFACTOR:

1. Run a baseline subagent scenario without the new skill and capture missed evidence or premature-fix behavior.
2. Write failing unit tests for parsers, rate calculations, redaction, degradation behavior, and report classification before implementing the collector.
3. Implement the minimum collector and skill instructions that close the observed gaps.
4. Re-run the same scenario with the skill and verify that the agent collects sustained samples, distinguishes global from shell startup cost, and avoids unauthorized changes.

Unit tests use `unittest` and fixture strings; they do not require a loaded machine or root access. Required checks are:

- load, `top`, swap, memory, `vm_stat`, `iostat`, capacity, and thermal parsing;
- per-process I/O delta and rate calculation;
- unavailable-command and permission-denied degradation;
- home-path redaction and absence of full command arguments;
- Markdown/JSON report shape and severity ordering;
- CLI `--help`, short live smoke test on macOS, skill frontmatter validation, and `git diff --check`.

## Acceptance Criteria

- A default run is read-only, bounded, network-free, and produces numerical CPU, memory, swap, disk, process, thermal, and limitation sections.
- Terminal profiling is opt-in and compares clean versus real zsh startup.
- Reports distinguish current rates from cumulative values and facts from inference.
- Suspicious-process follow-up reaches evidence such as stack, open file, or signature status before proposing remediation.
- Unit tests, CLI smoke test, and skill validator pass with no untracked runtime artifacts.
