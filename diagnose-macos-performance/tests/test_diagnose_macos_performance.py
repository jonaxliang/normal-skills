import importlib.util
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "diagnose_macos_performance.py"
SPEC = importlib.util.spec_from_file_location("diagnose_macos_performance", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


CPU_FIXTURE = """
CPU usage: 40.0% user, 50.0% sys, 10.0% idle
CPU usage: 50.0% user, 20.0% sys, 30.0% idle
"""

IOSTAT_FIXTURE = """
              disk0
    KB/t  tps  MB/s
    5.00 8000 40.00
    6.00 10000 60.00
    4.00 20000 80.00
"""

VM_STAT_FIXTURE = """
Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                100.
Pages occupied by compressor:              300.
Swapins:                                    20.
Swapouts:                                    5.
"Translation faults":                    9999.
"""

TOP_FIXTURE = """
Processes: 900 total, 20 running, 1 stuck, 879 sleeping, 12000 threads
Load Avg: 10.00, 8.00, 6.00
CPU usage: 40.0% user, 50.0% sys, 10.0% idle
PhysMem: 47G used (6409M wired, 24G compressor), 382M unused.
Processes: 905 total, 22 running, 2 stuck, 881 sleeping, 12100 threads
Load Avg: 12.00, 9.00, 7.00
CPU usage: 50.0% user, 20.0% sys, 30.0% idle
PhysMem: 47G used (6410M wired, 24G compressor), 300M unused.
"""


class ParserTests(unittest.TestCase):
    def function(self, name):
        value = getattr(MODULE, name, None)
        self.assertIsNotNone(value, f"missing function: {name}")
        return value

    def test_parses_load_average_from_top_and_uptime(self):
        parse = self.function("parse_load_average")
        self.assertEqual(parse("Load Avg: 65.06, 86.65, 60.41"), (65.06, 86.65, 60.41))
        self.assertEqual(parse("load averages: 3.25 2.50 1.75"), (3.25, 2.5, 1.75))
        self.assertIsNone(parse("no load data"))

    def test_averages_cpu_samples(self):
        result = self.function("parse_cpu_samples")(CPU_FIXTURE)
        self.assertEqual(result["sample_count"], 2)
        self.assertEqual(result["user_avg_pct"], 45.0)
        self.assertEqual(result["system_avg_pct"], 35.0)
        self.assertEqual(result["idle_avg_pct"], 20.0)
        self.assertEqual(self.function("parse_cpu_samples")("bad"), {"sample_count": 0})

    def test_parses_last_top_summary(self):
        text = (
            "Processes: 900 total, 20 running, 1 stuck, 879 sleeping, 12000 threads\n"
            "Processes: 984 total, 42 running, 7 stuck, 935 sleeping, 12904 threads\n"
        )
        self.assertEqual(
            self.function("parse_top_summary")(text),
            {"process_count": 984, "running_count": 42, "stuck_count": 7, "thread_count": 12904},
        )

    def test_parses_physical_memory_units(self):
        result = self.function("parse_physmem")(
            "PhysMem: 47G used (6409M wired, 24G compressor), 382M unused."
        )
        self.assertEqual(result["used_bytes"], 47 * 1024**3)
        self.assertEqual(result["wired_bytes"], 6409 * 1024**2)
        self.assertEqual(result["compressor_bytes"], 24 * 1024**3)
        self.assertEqual(result["unused_bytes"], 382 * 1024**2)

    def test_parses_swap_usage(self):
        result = self.function("parse_swapusage")(
            "vm.swapusage: total = 21504.00M  used = 20700.06M  free = 803.94M  (encrypted)"
        )
        self.assertEqual(result["total_bytes"], 21504 * 1024**2)
        self.assertEqual(result["used_bytes"], 21705586114)
        self.assertEqual(result["free_bytes"], int(803.94 * 1024**2))

    def test_parses_memory_pressure_percentage(self):
        parse = self.function("parse_memory_pressure")
        self.assertEqual(parse("System-wide memory free percentage: 34%"), 34)
        self.assertIsNone(parse("memory pressure unavailable"))

    def test_parses_vm_stat_names_and_page_size(self):
        result = self.function("parse_vm_stat")(VM_STAT_FIXTURE)
        self.assertEqual(result["page_size_bytes"], 16384)
        self.assertEqual(result["pages_free"], 100)
        self.assertEqual(result["pages_occupied_by_compressor"], 300)
        self.assertEqual(result["swapins"], 20)
        self.assertEqual(result["swapouts"], 5)
        self.assertEqual(result["translation_faults"], 9999)

    def test_parses_iostat_live_samples_not_cumulative_first_row(self):
        result = self.function("parse_iostat")(IOSTAT_FIXTURE)
        self.assertEqual(result["sample_count"], 2)
        self.assertEqual(result["avg_kb_per_transfer"], 5.0)
        self.assertEqual(result["avg_tps"], 15000.0)
        self.assertEqual(result["avg_mib_per_second"], 70.0)
        self.assertEqual(self.function("parse_iostat")("bad"), {"sample_count": 0, "samples": []})

    def test_parses_df_capacity(self):
        text = (
            "Filesystem 1024-blocks Used Available Capacity iused ifree %iused Mounted on\n"
            "/dev/disk3s1s1 1000000 600000 400000 60% 1 2 0% /\n"
        )
        result = self.function("parse_df")(text)
        self.assertEqual(result["total_bytes"], 1000000 * 1024)
        self.assertEqual(result["used_bytes"], 600000 * 1024)
        self.assertEqual(result["free_bytes"], 400000 * 1024)
        self.assertEqual(result["used_pct"], 60.0)

    def test_prefers_data_volume_over_read_only_root_snapshot(self):
        text = (
            "Filesystem 1024-blocks Used Available Capacity iused ifree %iused Mounted on\n"
            "/dev/disk3s3s1 971350180 17427640 346392208 5% 1 2 0% /\n"
            "/dev/disk3s1 971350180 555000000 346392208 62% 1 2 0% /System/Volumes/Data\n"
        )
        result = self.function("parse_df")(text)
        self.assertEqual(result["used_pct"], 62.0)
        self.assertEqual(result.get("mount_point"), "/System/Volumes/Data")

    def test_parses_disk_health_and_trim(self):
        result = self.function("parse_diskutil_info")(
            "   SMART Status:              Verified\n   TRIM Support:              Yes\n"
        )
        self.assertEqual(result, {"smart_status": "Verified", "trim_status": "Yes"})

    def test_parses_thermal_warning_state(self):
        parse = self.function("parse_thermal")
        self.assertEqual(
            parse("Note: No thermal warning level has been recorded"),
            {"available": True, "warning": False},
        )
        self.assertEqual(parse("CPU_Speed_Limit = 50"), {"available": True, "warning": True})
        self.assertEqual(parse(""), {"available": False, "warning": None})

    def test_parses_ps_without_losing_spaces_and_redacts_home(self):
        text = (
            "5941 1 501 48.5 57104 S /Library/Application Support/Logitech/logi voice\n"
            "7000 1 501 1.0 1024 R /Users/alice/bin/private worker\n"
        )
        rows = self.function("parse_ps")(text, "/Users/alice")
        self.assertEqual(rows[0]["pid"], 5941)
        self.assertEqual(rows[0]["executable"], "/Library/Application Support/Logitech/logi voice")
        self.assertEqual(rows[1]["executable"], "~/bin/private worker")
        self.assertNotIn("arguments", rows[0])

    def test_calculates_non_negative_io_rate(self):
        rate = self.function("io_rate")
        self.assertEqual(rate(100, 200, 5.0), 20.0)
        self.assertEqual(rate(200, 100, 5.0), 0.0)
        with self.assertRaises(ValueError):
            rate(100, 200, 0)

    def test_redacts_only_an_exact_home_prefix(self):
        redact = self.function("redact_path")
        self.assertEqual(redact("/Users/alice/private/file", "/Users/alice"), "~/private/file")
        self.assertEqual(redact("/Users/alice", "/Users/alice"), "~")
        self.assertEqual(redact("/Users/alice2/file", "/Users/alice"), "/Users/alice2/file")


class CollectionTests(unittest.TestCase):
    def function(self, name):
        value = getattr(MODULE, name, None)
        self.assertIsNotNone(value, f"missing function: {name}")
        return value

    def result(self, args, stdout="", returncode=0, stderr="", error=None, elapsed=0.01):
        cls = self.function("CommandResult")
        return cls(tuple(args), returncode, stdout, stderr, elapsed, error)

    def fake_runner(self, args, timeout):
        del timeout
        name = Path(args[0]).name
        if name == "sw_vers":
            output = "ProductName:\tmacOS\nProductVersion:\t26.3.2\nBuildVersion:\t25D2140\n"
        elif name == "sysctl" and args[-1] == "vm.swapusage":
            output = "vm.swapusage: total = 21504.00M used = 20700.06M free = 803.94M (encrypted)\n"
        elif name == "sysctl":
            output = "Mac16,8\n14\n51539607552\n"
        elif name == "uptime":
            output = "18:03 up 12 days, 8 users, load averages: 10.00 8.00 6.00\n"
        elif name == "df":
            output = "/dev/disk3s1s1 1000000 600000 400000 60% 1 2 0% /\n"
        elif name == "diskutil":
            return self.result(args, returncode=1, stderr="permission denied", error="exit 1")
        elif name == "memory_pressure":
            output = "System-wide memory free percentage: 34%\n"
        elif name == "pmset" and args[-1] == "therm":
            output = "Note: No thermal warning level has been recorded\n"
        elif name == "pmset" and args[-1] == "batt":
            output = "Now drawing from 'AC Power'\n -InternalBattery-0\t99%; charging; present: true\n"
        elif name == "ps":
            output = (
                "5941 1 501 48.5 57104 S /Library/Application Support/Logitech/logi voice\n"
                "7000 1 501 5.0 1024 R /Users/alice/bin/helper\n"
            )
        elif name == "top":
            output = TOP_FIXTURE
        elif name == "iostat":
            output = IOSTAT_FIXTURE
        elif name == "vm_stat":
            output = VM_STAT_FIXTURE
        else:
            return self.result(args, returncode=127, stderr="not found", error="not found")
        return self.result(args, stdout=output)

    def fake_rate_sampler(self, duration, processes, runner):
        del duration, processes, runner
        return {
            "vm_rates": {"swapins_mib_per_second": 1.5, "swapouts_mib_per_second": 0.0},
            "process_io": [
                {
                    "pid": 5941,
                    "executable": "/Library/Application Support/Logitech/logi voice",
                    "read_mib_per_second": 60.0,
                    "write_mib_per_second": 0.0,
                }
            ],
            "io_attribution": {"accessible_processes": 2, "inaccessible_processes": 1},
            "limitations": [],
        }

    def test_collects_sections_and_preserves_partial_failures(self):
        report = self.function("collect_report")(
            duration=3,
            include_shell=False,
            runner=self.fake_runner,
            platform_name="Darwin",
            rate_sampler=self.fake_rate_sampler,
            home="/Users/alice",
        )
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["metadata"]["logical_cpus"], 14)
        self.assertEqual(report["load"]["one_minute"], 12.0)
        self.assertEqual(report["cpu"]["idle_avg_pct"], 30.0)
        self.assertEqual(report["memory"]["swap_used_bytes"], 21705586114)
        self.assertEqual(report["disk"]["avg_tps"], 15000.0)
        self.assertEqual(report["processes"]["process_count"], 905)
        self.assertEqual(report["processes"]["top_io"][0]["read_mib_per_second"], 60.0)
        self.assertFalse(report["thermal"]["warning"])
        self.assertNotIn("shell", report)
        self.assertTrue(any("diskutil" in item for item in report["limitations"]))
        self.assertNotIn("/Users/alice", str(report))

    def test_rejects_non_macos_platform(self):
        unsupported = self.function("UnsupportedPlatformError")
        with self.assertRaises(unsupported):
            self.function("collect_report")(
                duration=3,
                include_shell=False,
                runner=self.fake_runner,
                platform_name="Linux",
                rate_sampler=self.fake_rate_sampler,
                home="/Users/alice",
            )

    def test_run_command_turns_timeout_into_data(self):
        with mock.patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["/usr/bin/top"], timeout=1),
        ):
            result = self.function("run_command")(["/usr/bin/top"], timeout=1)
        self.assertEqual(result.returncode, 124)
        self.assertEqual(result.error, "timeout")
        self.assertGreaterEqual(result.elapsed_seconds, 0)

    def test_builds_process_io_rates_for_matching_processes(self):
        before = {
            10: {"executable": "reader", "read_bytes": 100, "write_bytes": 50},
            20: {"executable": "gone", "read_bytes": 100, "write_bytes": 0},
        }
        after = {
            10: {"executable": "reader", "read_bytes": 100 + 20 * 1024**2, "write_bytes": 50},
            20: {"executable": "reused", "read_bytes": 1000, "write_bytes": 0},
        }
        rows = self.function("build_process_io_rates")(before, after, seconds=2.0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pid"], 10)
        self.assertEqual(rows[0]["read_mib_per_second"], 10.0)

    def test_snapshot_counts_inaccessible_processes(self):
        processes = [
            {"pid": 10, "uid": 501, "executable": "reader"},
            {"pid": 20, "uid": 501, "executable": "denied"},
        ]

        def reader(pid):
            if pid == 20:
                raise PermissionError("denied")
            return 100, 10

        snapshot = self.function("snapshot_process_io")(processes, reader=reader, current_uid=501)
        self.assertEqual(snapshot["accessible_processes"], 1)
        self.assertEqual(snapshot["inaccessible_processes"], 1)
        self.assertIn(10, snapshot["processes"])

    def test_shell_timing_uses_one_clean_and_five_real_trials(self):
        elapsed = iter([0.01, 0.2, 0.25, 0.3, 0.35, 0.4])

        def runner(args, timeout):
            del timeout
            return self.result(args, elapsed=next(elapsed))

        shell = self.function("measure_shell_startup")(runner=runner)
        self.assertEqual(shell["clean_seconds"], 0.01)
        self.assertEqual(shell["trial_count"], 5)
        self.assertEqual(shell["median_seconds"], 0.3)
        self.assertEqual(shell["p90_seconds"], 0.4)


class ReportingTests(unittest.TestCase):
    def function(self, name):
        value = getattr(MODULE, name, None)
        self.assertIsNotNone(value, f"missing function: {name}")
        return value

    def high_pressure_report(self):
        gib = 1024**3
        return {
            "schema_version": 1,
            "metadata": {
                "timestamp": "2026-07-12T18:03:00+08:00",
                "sampling_duration_seconds": 10,
                "model": "Mac16,8",
                "logical_cpus": 14,
                "physical_memory_bytes": 48 * gib,
            },
            "load": {
                "one_minute": 35.0,
                "five_minutes": 20.0,
                "fifteen_minutes": 10.0,
                "one_minute_per_cpu": 2.5,
            },
            "cpu": {
                "sample_count": 10,
                "user_avg_pct": 45.0,
                "system_avg_pct": 48.0,
                "idle_avg_pct": 7.0,
            },
            "memory": {
                "physical_bytes": 48 * gib,
                "used_bytes": 47 * gib,
                "unused_bytes": 128 * 1024**2,
                "compressor_bytes": 24 * gib,
                "memory_pressure_free_pct": 34,
                "swap_total_bytes": 20 * gib,
                "swap_used_bytes": 18 * gib,
                "swapouts_mib_per_second": 0.0,
            },
            "disk": {
                "total_bytes": 1000 * gib,
                "free_bytes": 80 * gib,
                "used_pct": 92.0,
                "smart_status": "Verified",
                "sample_count": 10,
                "avg_kb_per_transfer": 5.8,
                "avg_tps": 17000.0,
                "avg_mib_per_second": 100.0,
            },
            "processes": {
                "process_count": 1000,
                "running_count": 50,
                "stuck_count": 2,
                "thread_count": 12900,
                "top_cpu": [{"pid": 42, "executable": "reader", "cpu_pct": 60.0, "rss_bytes": gib}],
                "top_memory": [],
                "top_io": [
                    {
                        "pid": 42,
                        "executable": "reader",
                        "read_mib_per_second": 60.0,
                        "write_mib_per_second": 0.0,
                    }
                ],
                "io_attribution": {"accessible_processes": 10, "inaccessible_processes": 3},
                "executable_counts": [],
            },
            "thermal": {"available": True, "warning": False},
            "power": {"source": "AC Power", "battery_pct": 99},
            "shell": {
                "clean_seconds": 0.01,
                "trial_count": 5,
                "median_seconds": 1.2,
                "p90_seconds": 1.4,
                "min_seconds": 1.1,
                "max_seconds": 1.5,
                "limitations": [],
            },
            "limitations": ["diskutil: permission denied"],
        }

    def test_findings_are_ranked_and_do_not_overclaim_root_cause(self):
        findings = self.function("derive_findings")(self.high_pressure_report())
        priorities = {"critical": 0, "warning": 1, "info": 2}
        self.assertEqual(
            [priorities[item["severity"]] for item in findings],
            sorted(priorities[item["severity"]] for item in findings),
        )
        self.assertEqual(findings[0]["confidence"], "measured")
        io_finding = next(item for item in findings if item["category"] == "process_io")
        self.assertIn("60.0 MiB/s", io_finding["evidence"])
        self.assertNotIn("root cause", str(findings).lower())

    def test_low_raw_free_memory_alone_is_not_called_oom(self):
        report = self.high_pressure_report()
        report["load"] = {}
        report["cpu"] = {"sample_count": 3, "idle_avg_pct": 80.0, "system_avg_pct": 5.0}
        report["memory"].update(
            {
                "compressor_bytes": 1 * 1024**3,
                "swap_total_bytes": 20 * 1024**3,
                "swap_used_bytes": 0,
                "memory_pressure_free_pct": 40,
            }
        )
        report["disk"].update({"used_pct": 50.0, "avg_tps": 100.0, "avg_kb_per_transfer": 64.0})
        report["processes"].update(
            {"process_count": 300, "thread_count": 2000, "top_io": []}
        )
        report.pop("shell")
        findings = self.function("derive_findings")(report)
        self.assertFalse(any(item["category"] == "memory" for item in findings))
        self.assertNotIn("OOM", str(findings))

    def test_markdown_and_json_include_findings_metrics_and_limitations(self):
        report = self.high_pressure_report()
        markdown = self.function("render_markdown")(report)
        self.assertIn("60.0 MiB/s", markdown)
        self.assertIn("Limitations", markdown)
        self.assertIn("diskutil: permission denied", markdown)
        payload = json.loads(self.function("render_json")(report))
        self.assertEqual(payload["schema_version"], 1)
        self.assertTrue(payload["findings"])
        self.assertEqual(payload["limitations"], report["limitations"])

    def test_duration_parser_enforces_safe_range(self):
        parse = self.function("parse_duration")
        self.assertEqual(parse("3"), 3)
        self.assertEqual(parse("30"), 30)
        with self.assertRaises(Exception):
            parse("2")
        with self.assertRaises(Exception):
            parse("31")

    def test_main_renders_injected_report_without_live_collection(self):
        output = io.StringIO()

        def collector(**kwargs):
            self.assertEqual(kwargs["duration"], 3)
            self.assertTrue(kwargs["include_shell"])
            return self.high_pressure_report()

        exit_code = self.function("main")(
            ["--duration", "3", "--format", "json", "--include-shell"],
            collector=collector,
            platform_name="Darwin",
            stdout=output,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue())["schema_version"], 1)

    def test_main_returns_two_outside_macos(self):
        output = io.StringIO()
        error = io.StringIO()
        exit_code = self.function("main")(
            [],
            collector=lambda **kwargs: self.fail(str(kwargs)),
            platform_name="Linux",
            stdout=output,
            stderr=error,
        )
        self.assertEqual(exit_code, 2)
        self.assertIn("macOS", error.getvalue())


if __name__ == "__main__":
    unittest.main()
