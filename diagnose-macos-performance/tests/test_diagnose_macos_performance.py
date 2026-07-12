import importlib.util
import sys
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
