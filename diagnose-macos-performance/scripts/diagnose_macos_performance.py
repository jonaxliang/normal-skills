#!/usr/bin/env python3
"""Collect read-only macOS performance evidence."""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import platform
import re
import statistics
import struct
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable


_BINARY_UNITS = {
    "": 1,
    "B": 1,
    "K": 1024,
    "KB": 1024,
    "M": 1024**2,
    "MB": 1024**2,
    "G": 1024**3,
    "GB": 1024**3,
    "T": 1024**4,
    "TB": 1024**4,
}


def _size_to_bytes(value: str) -> int:
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?B?)?[+.-]*\s*", value, re.IGNORECASE)
    if not match:
        raise ValueError(f"unsupported size: {value}")
    number = float(match.group(1))
    unit = (match.group(2) or "").upper()
    return int(number * _BINARY_UNITS[unit])


def parse_load_average(text: str) -> tuple[float, float, float] | None:
    matches = re.findall(
        r"(?:Load Avg:|load averages?:)\s*([0-9.]+)\s*,?\s+([0-9.]+)\s*,?\s+([0-9.]+)",
        text,
        re.IGNORECASE,
    )
    if not matches:
        return None
    return tuple(float(value) for value in matches[-1])


def parse_cpu_samples(text: str, *, skip_first: bool = False) -> dict[str, float | int]:
    samples = [
        tuple(float(value) for value in match)
        for match in re.findall(
            r"CPU usage:\s*([0-9.]+)% user,\s*([0-9.]+)% sys,\s*([0-9.]+)% idle",
            text,
            re.IGNORECASE,
        )
    ]
    if skip_first and len(samples) > 1:
        samples = samples[1:]
    if not samples:
        return {"sample_count": 0}
    return {
        "sample_count": len(samples),
        "user_avg_pct": statistics.fmean(sample[0] for sample in samples),
        "system_avg_pct": statistics.fmean(sample[1] for sample in samples),
        "idle_avg_pct": statistics.fmean(sample[2] for sample in samples),
    }


def parse_top_summary(text: str) -> dict[str, int]:
    matches = re.findall(
        r"Processes:\s*(\d+) total,\s*(\d+) running,\s*(\d+) stuck,\s*(\d+) sleeping,\s*(\d+) threads",
        text,
        re.IGNORECASE,
    )
    if not matches:
        return {}
    total, running, stuck, _sleeping, threads = (int(value) for value in matches[-1])
    return {
        "process_count": total,
        "running_count": running,
        "stuck_count": stuck,
        "thread_count": threads,
    }


def parse_physmem(text: str) -> dict[str, int]:
    patterns = {
        "used_bytes": r"PhysMem:\s*([0-9.]+\s*[KMGT]?B?[+.-]*)\s+used",
        "wired_bytes": r"\(([0-9.]+\s*[KMGT]?B?[+.-]*)\s+wired",
        "compressor_bytes": r",\s*([0-9.]+\s*[KMGT]?B?[+.-]*)\s+compressor",
        "unused_bytes": r"\),\s*([0-9.]+\s*[KMGT]?B?[+.-]*)\s+unused",
    }
    result: dict[str, int] = {}
    for key, pattern in patterns.items():
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            result[key] = _size_to_bytes(matches[-1])
    return result


def parse_swapusage(text: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for label, key in (("total", "total_bytes"), ("used", "used_bytes"), ("free", "free_bytes")):
        match = re.search(rf"\b{label}\s*=\s*([0-9.]+\s*[KMGT]?B?)", text, re.IGNORECASE)
        if match:
            result[key] = _size_to_bytes(match.group(1))
    return result


def parse_memory_pressure(text: str) -> int | None:
    match = re.search(r"System-wide memory free percentage:\s*(\d+)%", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _counter_value(value: str) -> int:
    cleaned = value.strip().rstrip(".").replace(",", "")
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([KMG]?)", cleaned, re.IGNORECASE)
    if not match:
        raise ValueError(value)
    multiplier = {"": 1, "K": 1000, "M": 1000**2, "G": 1000**3}[match.group(2).upper()]
    return int(float(match.group(1)) * multiplier)


def parse_vm_stat(text: str) -> dict[str, int]:
    result: dict[str, int] = {}
    page_size = re.search(r"page size of\s+(\d+) bytes", text, re.IGNORECASE)
    if page_size:
        result["page_size_bytes"] = int(page_size.group(1))
    for raw_key, raw_value in re.findall(r'^\s*"?([^"\n:]+)"?:\s*([0-9.,]+[KMG]?)[.]?\s*$', text, re.MULTILINE):
        key = re.sub(r"[^a-z0-9]+", "_", raw_key.strip().lower()).strip("_")
        try:
            result[key] = _counter_value(raw_value)
        except ValueError:
            continue
    return result


def parse_iostat(text: str) -> dict[str, float | int | list[dict[str, float]]]:
    rows: list[dict[str, float]] = []
    for line in text.splitlines():
        tokens = line.split()
        if len(tokens) < 3:
            continue
        try:
            kb_per_transfer, tps, mib_per_second = (float(token) for token in tokens[:3])
        except ValueError:
            continue
        rows.append(
            {
                "kb_per_transfer": kb_per_transfer,
                "tps": tps,
                "mib_per_second": mib_per_second,
            }
        )
    live = rows[1:] if rows else []
    if not live:
        return {"sample_count": 0, "samples": []}
    return {
        "sample_count": len(live),
        "samples": live,
        "avg_kb_per_transfer": statistics.fmean(row["kb_per_transfer"] for row in live),
        "avg_tps": statistics.fmean(row["tps"] for row in live),
        "avg_mib_per_second": statistics.fmean(row["mib_per_second"] for row in live),
    }


def parse_df(text: str) -> dict[str, int | float]:
    lines = [line for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        fields = line.split()
        if len(fields) < 5 or not fields[4].endswith("%"):
            continue
        try:
            total_kib, used_kib, free_kib = (int(fields[index]) for index in (1, 2, 3))
            used_pct = float(fields[4].rstrip("%"))
        except ValueError:
            continue
        return {
            "total_bytes": total_kib * 1024,
            "used_bytes": used_kib * 1024,
            "free_bytes": free_kib * 1024,
            "used_pct": used_pct,
            "mount_point": fields[-1],
        }
    return {}


def parse_diskutil_info(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for label, key in (("SMART Status", "smart_status"), ("TRIM Support", "trim_status")):
        match = re.search(rf"^\s*{re.escape(label)}:\s*(.+?)\s*$", text, re.MULTILINE | re.IGNORECASE)
        if match:
            result[key] = match.group(1).strip()
    return result


def parse_thermal(text: str) -> dict[str, bool | None]:
    stripped = text.strip()
    if not stripped:
        return {"available": False, "warning": None}
    lower = stripped.lower()
    no_warning = "no thermal warning" in lower and "cpu_speed_limit" not in lower
    return {"available": True, "warning": not no_warning}


def redact_path(value: str, home: str) -> str:
    normalized_home = home.rstrip("/")
    if not normalized_home:
        return value
    if value == normalized_home:
        return "~"
    if value.startswith(normalized_home + "/"):
        return "~" + value[len(normalized_home) :]
    return value


def parse_ps(text: str, home: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in text.splitlines():
        fields = line.strip().split(maxsplit=6)
        if len(fields) != 7:
            continue
        try:
            pid, ppid, uid = (int(fields[index]) for index in range(3))
            cpu_pct = float(fields[3])
            rss_bytes = int(fields[4]) * 1024
        except ValueError:
            continue
        rows.append(
            {
                "pid": pid,
                "ppid": ppid,
                "uid": uid,
                "cpu_pct": cpu_pct,
                "rss_bytes": rss_bytes,
                "state": fields[5],
                "executable": redact_path(fields[6], home),
            }
        )
    return rows


def io_rate(before: int, after: int, seconds: float) -> float:
    if seconds <= 0:
        raise ValueError("seconds must be positive")
    return max(0, after - before) / seconds


class UnsupportedPlatformError(RuntimeError):
    """Raised when the collector is invoked outside macOS."""


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    error: str | None = None


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_command(args: list[str], timeout: float) -> CommandResult:
    """Run one read-only probe without leaking an exception to the report."""
    started = time.monotonic()
    try:
        completed = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            tuple(args),
            124,
            _as_text(exc.stdout),
            _as_text(exc.stderr),
            time.monotonic() - started,
            "timeout",
        )
    except FileNotFoundError:
        return CommandResult(tuple(args), 127, "", "", time.monotonic() - started, "not found")
    except OSError as exc:
        return CommandResult(
            tuple(args),
            126,
            "",
            "",
            time.monotonic() - started,
            f"{type(exc).__name__}",
        )
    error = None if completed.returncode == 0 else f"exit {completed.returncode}"
    return CommandResult(
        tuple(args),
        completed.returncode,
        completed.stdout,
        completed.stderr,
        time.monotonic() - started,
        error,
    )


def _safe_runner(
    runner: Callable[[list[str], float], CommandResult],
    args: list[str],
    timeout: float,
) -> CommandResult:
    try:
        return runner(args, timeout)
    except Exception as exc:  # a custom runner must not abort the remaining probes
        return CommandResult(tuple(args), 126, "", "", 0.0, type(exc).__name__)


def _command_name(result: CommandResult) -> str:
    return Path(result.args[0]).name if result.args else "probe"


def _limitation_for(result: CommandResult) -> str | None:
    if result.returncode == 0 and result.error is None:
        return None
    return f"{_command_name(result)}: {result.error or f'exit {result.returncode}'}"


def _parse_sw_vers(text: str) -> dict[str, str]:
    mapping = {
        "ProductName": "product_name",
        "ProductVersion": "product_version",
        "BuildVersion": "build_version",
    }
    result: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key in mapping:
            result[mapping[key]] = value.strip()
    return result


def _parse_hardware(text: str) -> dict[str, int | str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    result: dict[str, int | str] = {}
    if lines:
        result["model"] = lines[0]
    if len(lines) > 1:
        try:
            result["logical_cpus"] = int(lines[1])
        except ValueError:
            pass
    if len(lines) > 2:
        try:
            result["physical_memory_bytes"] = int(lines[2])
        except ValueError:
            pass
    return result


def parse_power(text: str) -> dict[str, int | str | None]:
    source = re.search(r"Now drawing from '([^']+)'", text)
    percent = re.search(r"\b(\d{1,3})%;", text)
    return {
        "source": source.group(1) if source else None,
        "battery_pct": int(percent.group(1)) if percent else None,
    }


def snapshot_process_io(
    processes: list[dict[str, object]],
    *,
    reader: Callable[[int], tuple[int, int]],
    current_uid: int,
) -> dict[str, object]:
    snapshot: dict[int, dict[str, int | str]] = {}
    inaccessible = 0
    for process in processes:
        if process.get("uid") != current_uid:
            continue
        pid = int(process["pid"])
        try:
            read_bytes, write_bytes = reader(pid)
        except (OSError, PermissionError, ProcessLookupError):
            inaccessible += 1
            continue
        snapshot[pid] = {
            "executable": str(process["executable"]),
            "read_bytes": max(0, int(read_bytes)),
            "write_bytes": max(0, int(write_bytes)),
        }
    return {
        "processes": snapshot,
        "accessible_processes": len(snapshot),
        "inaccessible_processes": inaccessible,
    }


def build_process_io_rates(
    before: dict[int, dict[str, int | str]],
    after: dict[int, dict[str, int | str]],
    *,
    seconds: float,
    limit: int = 10,
) -> list[dict[str, int | float | str]]:
    rows: list[dict[str, int | float | str]] = []
    for pid, first in before.items():
        second = after.get(pid)
        if not second or first.get("executable") != second.get("executable"):
            continue
        read_rate = io_rate(int(first["read_bytes"]), int(second["read_bytes"]), seconds)
        write_rate = io_rate(int(first["write_bytes"]), int(second["write_bytes"]), seconds)
        if read_rate <= 0 and write_rate <= 0:
            continue
        rows.append(
            {
                "pid": pid,
                "executable": str(first["executable"]),
                "read_mib_per_second": read_rate / 1024**2,
                "write_mib_per_second": write_rate / 1024**2,
            }
        )
    rows.sort(
        key=lambda row: float(row["read_mib_per_second"]) + float(row["write_mib_per_second"]),
        reverse=True,
    )
    return rows[:limit]


_LIBPROC = None


def read_process_rusage(pid: int) -> tuple[int, int]:
    """Read cumulative disk bytes for one process via proc_pid_rusage."""
    global _LIBPROC
    if _LIBPROC is None:
        _LIBPROC = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        _LIBPROC.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        _LIBPROC.proc_pid_rusage.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(256)
    if _LIBPROC.proc_pid_rusage(pid, 2, buffer) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    values = struct.unpack_from("18Q", buffer.raw, 16)
    return values[16], values[17]


def _vm_rates(before: dict[str, int], after: dict[str, int], seconds: float) -> dict[str, float]:
    page_size = after.get("page_size_bytes") or before.get("page_size_bytes") or 0
    if not page_size or seconds <= 0:
        return {}
    result: dict[str, float] = {}
    for counter in ("swapins", "swapouts", "pageins", "pageouts", "compressions", "decompressions"):
        if counter not in before or counter not in after:
            continue
        pages_per_second = io_rate(before[counter], after[counter], seconds)
        result[f"{counter}_mib_per_second"] = pages_per_second * page_size / 1024**2
    return result


def measure_rates(
    duration: int,
    processes: list[dict[str, object]],
    runner: Callable[[list[str], float], CommandResult],
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    rusage_reader: Callable[[int], tuple[int, int]] = read_process_rusage,
) -> dict[str, object]:
    vm_before_result = _safe_runner(runner, ["/usr/bin/vm_stat"], 5)
    io_before = snapshot_process_io(processes, reader=rusage_reader, current_uid=os.getuid())
    started = time.monotonic()
    sleep_fn(duration)
    elapsed = max(time.monotonic() - started, 0.001)
    vm_after_result = _safe_runner(runner, ["/usr/bin/vm_stat"], 5)
    io_after = snapshot_process_io(processes, reader=rusage_reader, current_uid=os.getuid())

    before_processes = io_before["processes"]
    after_processes = io_after["processes"]
    assert isinstance(before_processes, dict) and isinstance(after_processes, dict)
    limitations = [
        limitation
        for result in (vm_before_result, vm_after_result)
        if (limitation := _limitation_for(result)) is not None
    ]
    return {
        "vm_rates": _vm_rates(
            parse_vm_stat(vm_before_result.stdout),
            parse_vm_stat(vm_after_result.stdout),
            elapsed,
        ),
        "process_io": build_process_io_rates(before_processes, after_processes, seconds=elapsed),
        "io_attribution": {
            "accessible_processes": min(
                int(io_before["accessible_processes"]), int(io_after["accessible_processes"])
            ),
            "inaccessible_processes": max(
                int(io_before["inaccessible_processes"]), int(io_after["inaccessible_processes"])
            ),
        },
        "limitations": limitations,
    }


def measure_shell_startup(
    *,
    runner: Callable[[list[str], float], CommandResult] = run_command,
) -> dict[str, object]:
    clean = _safe_runner(runner, ["/bin/zsh", "-dflic", "exit"], 15)
    trials = [_safe_runner(runner, ["/bin/zsh", "-lic", "exit"], 15) for _ in range(5)]
    successful = [trial.elapsed_seconds for trial in trials if trial.returncode == 0]
    limitations = [
        limitation
        for result in [clean, *trials]
        if (limitation := _limitation_for(result)) is not None
    ]
    result: dict[str, object] = {
        "clean_seconds": clean.elapsed_seconds if clean.returncode == 0 else None,
        "trial_count": len(successful),
        "limitations": sorted(set(limitations)),
    }
    if successful:
        ordered = sorted(successful)
        p90_index = max(0, math.ceil(0.9 * len(ordered)) - 1)
        result.update(
            {
                "median_seconds": statistics.median(ordered),
                "p90_seconds": ordered[p90_index],
                "min_seconds": ordered[0],
                "max_seconds": ordered[-1],
            }
        )
    return result


def _redact_io_rows(rows: list[dict[str, object]], home: str) -> list[dict[str, object]]:
    redacted: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        if "executable" in item:
            item["executable"] = redact_path(str(item["executable"]), home)
        redacted.append(item)
    return redacted


def collect_report(
    duration: int = 10,
    include_shell: bool = False,
    *,
    runner: Callable[[list[str], float], CommandResult] = run_command,
    platform_name: str | None = None,
    rate_sampler: Callable[[int, list[dict[str, object]], Callable[[list[str], float], CommandResult]], dict[str, object]] = measure_rates,
    home: str | None = None,
) -> dict[str, object]:
    system_name = platform_name or platform.system()
    if system_name != "Darwin":
        raise UnsupportedPlatformError(f"unsupported platform: {system_name}")
    if duration < 3 or duration > 30:
        raise ValueError("duration must be between 3 and 30 seconds")
    home = home or str(Path.home())

    static_commands = {
        "sw_vers": (["/usr/bin/sw_vers"], 5),
        "hardware": (["/usr/sbin/sysctl", "-n", "hw.model", "hw.logicalcpu", "hw.memsize"], 5),
        "uptime": (["/usr/bin/uptime"], 5),
        "df": (["/bin/df", "-k", "/", "/System/Volumes/Data"], 5),
        "diskutil": (["/usr/sbin/diskutil", "info", "/"], 10),
        "memory_pressure": (["/usr/bin/memory_pressure", "-Q"], 5),
        "swap": (["/usr/sbin/sysctl", "vm.swapusage"], 5),
        "thermal": (["/usr/bin/pmset", "-g", "therm"], 5),
        "power": (["/usr/bin/pmset", "-g", "batt"], 5),
        "ps": (["/bin/ps", "-Ao", "pid=,ppid=,uid=,%cpu=,rss=,state=,comm="], 10),
    }
    static_results = {
        name: _safe_runner(runner, args, timeout)
        for name, (args, timeout) in static_commands.items()
    }
    processes = parse_ps(static_results["ps"].stdout, home)

    sample_count = duration + 1
    top_args = ["/usr/bin/top", "-l", str(sample_count), "-s", "1", "-n", "0"]
    iostat_args = ["/usr/sbin/iostat", "-d", "-c", str(sample_count), "-w", "1"]
    with ThreadPoolExecutor(max_workers=3) as executor:
        top_future = executor.submit(_safe_runner, runner, top_args, duration + 8)
        iostat_future = executor.submit(_safe_runner, runner, iostat_args, duration + 8)
        rate_future = executor.submit(rate_sampler, duration, processes, runner)
        top_result = top_future.result()
        iostat_result = iostat_future.result()
        try:
            rate_data = rate_future.result()
        except Exception as exc:
            rate_data = {
                "vm_rates": {},
                "process_io": [],
                "io_attribution": {"accessible_processes": 0, "inaccessible_processes": 0},
                "limitations": [f"rate sampler: {type(exc).__name__}"],
            }

    hardware = _parse_hardware(static_results["hardware"].stdout)
    logical_cpus = int(hardware.get("logical_cpus", os.cpu_count() or 1))
    load_values = parse_load_average(top_result.stdout) or parse_load_average(static_results["uptime"].stdout)
    load: dict[str, float] = {}
    if load_values:
        load = {
            "one_minute": load_values[0],
            "five_minutes": load_values[1],
            "fifteen_minutes": load_values[2],
            "one_minute_per_cpu": load_values[0] / max(logical_cpus, 1),
        }

    cpu = parse_cpu_samples(top_result.stdout, skip_first=True)
    physmem = parse_physmem(top_result.stdout)
    swap = parse_swapusage(static_results["swap"].stdout)
    memory: dict[str, object] = {
        "physical_bytes": int(hardware.get("physical_memory_bytes", 0)),
        **physmem,
        "memory_pressure_free_pct": parse_memory_pressure(static_results["memory_pressure"].stdout),
        "swap_total_bytes": swap.get("total_bytes", 0),
        "swap_used_bytes": swap.get("used_bytes", 0),
        "swap_free_bytes": swap.get("free_bytes", 0),
        **dict(rate_data.get("vm_rates", {})),
    }

    disk: dict[str, object] = {}
    disk.update(parse_df(static_results["df"].stdout))
    disk.update(parse_diskutil_info(static_results["diskutil"].stdout))
    disk.update(parse_iostat(iostat_result.stdout))

    top_summary = parse_top_summary(top_result.stdout)
    command_counts = Counter(os.path.basename(str(process["executable"])) for process in processes)
    process_report: dict[str, object] = {
        **top_summary,
        "top_cpu": sorted(processes, key=lambda item: float(item["cpu_pct"]), reverse=True)[:10],
        "top_memory": sorted(processes, key=lambda item: int(item["rss_bytes"]), reverse=True)[:10],
        "top_io": _redact_io_rows(list(rate_data.get("process_io", [])), home),
        "io_attribution": dict(rate_data.get("io_attribution", {})),
        "executable_counts": [
            {"executable": executable, "count": count}
            for executable, count in command_counts.most_common(20)
        ],
    }

    limitations = [
        limitation
        for result in [*static_results.values(), top_result, iostat_result]
        if (limitation := _limitation_for(result)) is not None
    ]
    limitations.extend(str(item) for item in rate_data.get("limitations", []))

    report: dict[str, object] = {
        "schema_version": 1,
        "metadata": {
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "sampling_duration_seconds": duration,
            "architecture": platform.machine(),
            **_parse_sw_vers(static_results["sw_vers"].stdout),
            **hardware,
        },
        "load": load,
        "cpu": cpu,
        "memory": memory,
        "disk": disk,
        "processes": process_report,
        "thermal": parse_thermal(static_results["thermal"].stdout),
        "power": parse_power(static_results["power"].stdout),
        "limitations": sorted(set(limitations)),
    }
    if include_shell:
        shell = measure_shell_startup(runner=runner)
        report["shell"] = shell
        report["limitations"] = sorted(
            set([*report["limitations"], *shell.get("limitations", [])])
        )
    return report


_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def _finding(
    severity: str,
    category: str,
    summary: str,
    evidence: str,
    *,
    confidence: str = "measured",
) -> dict[str, str]:
    return {
        "severity": severity,
        "category": category,
        "summary": summary,
        "evidence": evidence,
        "confidence": confidence,
    }


def derive_findings(report: dict[str, object]) -> list[dict[str, str]]:
    """Derive conservative triage flags without declaring a concrete cause."""
    findings: list[dict[str, str]] = []
    load = dict(report.get("load", {}))
    cpu = dict(report.get("cpu", {}))
    memory = dict(report.get("memory", {}))
    disk = dict(report.get("disk", {}))
    processes = dict(report.get("processes", {}))
    thermal = dict(report.get("thermal", {}))

    load_per_cpu = float(load.get("one_minute_per_cpu") or 0)
    if load_per_cpu >= 2:
        findings.append(
            _finding(
                "critical",
                "load",
                "运行队列远高于 CPU 数量",
                f"1 分钟 load/逻辑核 = {load_per_cpu:.2f}",
            )
        )
    elif load_per_cpu >= 1:
        findings.append(
            _finding(
                "warning",
                "load",
                "运行队列已达到 CPU 数量",
                f"1 分钟 load/逻辑核 = {load_per_cpu:.2f}",
            )
        )

    if int(cpu.get("sample_count") or 0) > 0:
        idle = float(cpu.get("idle_avg_pct") or 0)
        system = float(cpu.get("system_avg_pct") or 0)
        if idle < 10:
            findings.append(_finding("critical", "cpu", "CPU 几乎没有空闲", f"平均 idle = {idle:.1f}%"))
        elif idle < 25:
            findings.append(_finding("warning", "cpu", "CPU 空闲余量偏低", f"平均 idle = {idle:.1f}%"))
        if system >= 40:
            findings.append(
                _finding("warning", "cpu_system", "系统态 CPU 偏高", f"平均 system = {system:.1f}%")
            )

    physical = int(memory.get("physical_bytes") or 0)
    compressor = int(memory.get("compressor_bytes") or 0)
    if physical and compressor / physical >= 0.35:
        findings.append(
            _finding(
                "warning",
                "memory",
                "压缩内存占比较高",
                f"compressor/physical = {compressor / physical:.1%}",
            )
        )
    swap_total = int(memory.get("swap_total_bytes") or 0)
    swap_used = int(memory.get("swap_used_bytes") or 0)
    if swap_total and swap_used / swap_total >= 0.8:
        findings.append(
            _finding(
                "warning",
                "memory",
                "Swap 占用接近上限",
                f"swap used = {swap_used / swap_total:.1%}",
            )
        )
    pressure_free = memory.get("memory_pressure_free_pct")
    if pressure_free is not None and float(pressure_free) <= 10:
        findings.append(
            _finding("critical", "memory", "当前内存压力很高", f"memory_pressure free = {pressure_free}%")
        )
    elif pressure_free is not None and float(pressure_free) <= 25:
        findings.append(
            _finding("warning", "memory", "当前内存余量偏低", f"memory_pressure free = {pressure_free}%")
        )
    swapout_rate = float(memory.get("swapouts_mib_per_second") or 0)
    if swapout_rate >= 10:
        findings.append(
            _finding("critical", "memory_activity", "正在发生大量换出", f"swapout = {swapout_rate:.1f} MiB/s")
        )
    elif swapout_rate >= 1:
        findings.append(
            _finding("warning", "memory_activity", "检测到持续换出", f"swapout = {swapout_rate:.1f} MiB/s")
        )

    disk_used = disk.get("used_pct")
    if disk_used is not None and float(disk_used) >= 90:
        findings.append(_finding("critical", "disk_capacity", "磁盘可用空间不足", f"used = {float(disk_used):.1f}%"))
    elif disk_used is not None and float(disk_used) >= 80:
        findings.append(_finding("warning", "disk_capacity", "磁盘使用率偏高", f"used = {float(disk_used):.1f}%"))
    smart = str(disk.get("smart_status") or "")
    if smart and smart.lower() != "verified":
        findings.append(_finding("critical", "disk_health", "磁盘健康状态异常", f"SMART = {smart}"))
    tps = float(disk.get("avg_tps") or 0)
    kb_per_transfer = float(disk.get("avg_kb_per_transfer") or 0)
    if tps >= 5000 and 0 < kb_per_transfer < 16:
        findings.append(
            _finding(
                "warning",
                "disk_activity",
                "磁盘存在持续大量小块 I/O",
                f"{tps:.0f} tps, {kb_per_transfer:.1f} KiB/transfer",
            )
        )

    for row in list(processes.get("top_io", [])):
        read_rate = float(row.get("read_mib_per_second") or 0)
        write_rate = float(row.get("write_mib_per_second") or 0)
        peak = max(read_rate, write_rate)
        if peak < 25:
            continue
        severity = "critical" if peak >= 50 else "warning"
        findings.append(
            _finding(
                severity,
                "process_io",
                f"进程 {row.get('executable', 'unknown')} 持续高 I/O",
                f"PID {row.get('pid')}: read {read_rate:.1f} MiB/s, write {write_rate:.1f} MiB/s",
            )
        )

    process_count = int(processes.get("process_count") or 0)
    thread_count = int(processes.get("thread_count") or 0)
    if process_count >= 800 or thread_count >= 10000:
        findings.append(
            _finding(
                "warning",
                "process_scale",
                "进程或线程数量偏高",
                f"processes = {process_count}, threads = {thread_count}",
            )
        )
    if thermal.get("warning") is True:
        findings.append(_finding("critical", "thermal", "检测到温控或性能限制", "pmset therm reported a warning"))

    shell = report.get("shell")
    if isinstance(shell, dict) and shell.get("median_seconds") is not None:
        median = float(shell["median_seconds"])
        if median >= 1:
            findings.append(_finding("critical", "shell", "登录 shell 启动明显偏慢", f"median = {median:.2f}s"))
        elif median >= 0.5:
            findings.append(_finding("warning", "shell", "登录 shell 启动略慢", f"median = {median:.2f}s"))

    return sorted(findings, key=lambda item: _SEVERITY_ORDER[item["severity"]])


def _with_findings(report: dict[str, object]) -> dict[str, object]:
    payload = dict(report)
    payload["findings"] = derive_findings(report)
    return payload


def render_json(report: dict[str, object]) -> str:
    return json.dumps(_with_findings(report), ensure_ascii=False, indent=2)


def _format_bytes(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(number) < 1024 or unit == "TiB":
            return f"{number:.1f} {unit}"
        number /= 1024
    return "n/a"


def render_markdown(report: dict[str, object]) -> str:
    payload = _with_findings(report)
    metadata = dict(payload.get("metadata", {}))
    load = dict(payload.get("load", {}))
    cpu = dict(payload.get("cpu", {}))
    memory = dict(payload.get("memory", {}))
    disk = dict(payload.get("disk", {}))
    processes = dict(payload.get("processes", {}))
    thermal = dict(payload.get("thermal", {}))
    lines = [
        "# macOS Performance Snapshot",
        "",
        f"- Time: {metadata.get('timestamp', 'n/a')}",
        f"- Model: {metadata.get('model', 'n/a')} / {metadata.get('logical_cpus', 'n/a')} logical CPUs",
        f"- Sampling window: {metadata.get('sampling_duration_seconds', 'n/a')}s",
        "",
        "## Findings",
        "",
    ]
    findings = list(payload.get("findings", []))
    if findings:
        for item in findings:
            lines.append(
                f"- [{str(item['severity']).upper()}][{item['confidence']}] "
                f"{item['summary']} — {item['evidence']}"
            )
    else:
        lines.append("- No advisory threshold was crossed in this sampling window.")

    lines.extend(
        [
            "",
            "## Metrics",
            "",
            f"- Load (1/5/15m): {load.get('one_minute', 'n/a')} / {load.get('five_minutes', 'n/a')} / {load.get('fifteen_minutes', 'n/a')}",
            f"- CPU avg user/system/idle: {cpu.get('user_avg_pct', 'n/a')}% / {cpu.get('system_avg_pct', 'n/a')}% / {cpu.get('idle_avg_pct', 'n/a')}%",
            f"- Memory used/compressor/free: {_format_bytes(memory.get('used_bytes'))} / {_format_bytes(memory.get('compressor_bytes'))} / {_format_bytes(memory.get('unused_bytes'))}",
            f"- Swap used/total: {_format_bytes(memory.get('swap_used_bytes'))} / {_format_bytes(memory.get('swap_total_bytes'))}",
            f"- Disk used: {disk.get('used_pct', 'n/a')}%; SMART: {disk.get('smart_status', 'unavailable')}",
            f"- Disk avg: {disk.get('avg_mib_per_second', 'n/a')} MiB/s, {disk.get('avg_tps', 'n/a')} tps, {disk.get('avg_kb_per_transfer', 'n/a')} KiB/transfer",
            f"- Processes/running/stuck/threads: {processes.get('process_count', 'n/a')} / {processes.get('running_count', 'n/a')} / {processes.get('stuck_count', 'n/a')} / {processes.get('thread_count', 'n/a')}",
            f"- Thermal warning: {thermal.get('warning', 'unavailable')}",
        ]
    )

    top_cpu = list(processes.get("top_cpu", []))[:5]
    if top_cpu:
        lines.extend(["", "## Top CPU Processes", "", "| PID | Executable | CPU | RSS |", "|---:|---|---:|---:|"])
        for row in top_cpu:
            lines.append(
                f"| {row.get('pid')} | {row.get('executable')} | {float(row.get('cpu_pct') or 0):.1f}% | {_format_bytes(row.get('rss_bytes'))} |"
            )
    top_io = list(processes.get("top_io", []))[:10]
    if top_io:
        lines.extend(["", "## Per-process I/O", ""])
        for row in top_io:
            lines.append(
                f"- PID {row.get('pid')} {row.get('executable')}: read {float(row.get('read_mib_per_second') or 0):.1f} MiB/s, write {float(row.get('write_mib_per_second') or 0):.1f} MiB/s"
            )

    shell = payload.get("shell")
    if isinstance(shell, dict):
        lines.extend(
            [
                "",
                "## Shell startup",
                "",
                f"- Clean zsh: {shell.get('clean_seconds', 'n/a')}s",
                f"- Login zsh median/P90: {shell.get('median_seconds', 'n/a')}s / {shell.get('p90_seconds', 'n/a')}s",
            ]
        )

    lines.extend(["", "## Limitations", ""])
    limitations = list(payload.get("limitations", []))
    if limitations:
        lines.extend(f"- {item}" for item in limitations)
    else:
        lines.append("- None reported.")
    return "\n".join(lines)


def parse_duration(value: str) -> int:
    try:
        duration = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("duration must be an integer") from exc
    if duration < 3 or duration > 30:
        raise argparse.ArgumentTypeError("duration must be between 3 and 30 seconds")
    return duration


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect read-only macOS performance evidence")
    parser.add_argument("--duration", type=parse_duration, default=10, help="sampling seconds, from 3 to 30")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", help="output format")
    parser.add_argument(
        "--include-shell",
        action="store_true",
        help="also time one clean and five real login zsh startups",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    collector: Callable[..., dict[str, object]] = collect_report,
    platform_name: str | None = None,
    stdout=None,
    stderr=None,
) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    system_name = platform_name or platform.system()
    if system_name != "Darwin":
        stderr.write("This collector supports macOS only.\n")
        return 2
    args = build_parser().parse_args(argv)
    try:
        report = collector(
            duration=args.duration,
            include_shell=args.include_shell,
            platform_name=system_name,
        )
        rendered = render_json(report) if args.format == "json" else render_markdown(report)
        stdout.write(rendered + "\n")
    except BrokenPipeError:
        return 0
    except UnsupportedPlatformError as exc:
        stderr.write(f"{exc}\n")
        return 2
    except (OSError, RuntimeError, ValueError) as exc:
        stderr.write(f"collection failed: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
