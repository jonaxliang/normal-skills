#!/usr/bin/env python3
"""Run every skill's test suite in an isolated Python process."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def find_test_directories() -> list[Path]:
    return sorted(
        path
        for path in ROOT.glob("*/tests")
        if path.is_dir() and any(path.glob("test_*.py"))
    )


def main() -> int:
    test_directories = find_test_directories()
    if not test_directories:
        print("No skill test directories found.", file=sys.stderr)
        return 1

    failed: list[str] = []
    for test_directory in test_directories:
        relative = test_directory.relative_to(ROOT)
        print(f"\n==> {relative}", flush=True)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                str(relative),
                "-p",
                "test_*.py",
                "-v",
            ],
            cwd=ROOT,
            check=False,
        )
        if result.returncode:
            failed.append(str(relative))

    if failed:
        print(f"\nFailed suites: {', '.join(failed)}", file=sys.stderr)
        return 1

    print(f"\nAll {len(test_directories)} skill test suites passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
