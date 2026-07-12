#!/usr/bin/env python3
"""Collect read-only macOS performance evidence."""

from __future__ import annotations

import re
import statistics


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
    match = re.search(
        r"(?:Load Avg:|load averages?:)\s*([0-9.]+)\s*,?\s+([0-9.]+)\s*,?\s+([0-9.]+)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return tuple(float(match.group(index)) for index in range(1, 4))


def parse_cpu_samples(text: str) -> dict[str, float | int]:
    samples = [
        tuple(float(value) for value in match)
        for match in re.findall(
            r"CPU usage:\s*([0-9.]+)% user,\s*([0-9.]+)% sys,\s*([0-9.]+)% idle",
            text,
            re.IGNORECASE,
        )
    ]
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
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            result[key] = _size_to_bytes(match.group(1))
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
