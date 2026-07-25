#!/usr/bin/env python3
"""Validate the repository-level contract shared by all skills."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ROOT_FILES = (
    "README.md",
    "LICENSE",
    "CONTRIBUTING.md",
)
IGNORED_PARTS = {
    ".git",
    ".worktrees",
    "__pycache__",
    "douyin-videos",
    "humanize-writing-workspace",
    "xhs-images",
}
TEXT_SUFFIXES = {".md", ".py", ".json", ".yaml", ".yml", ".txt"}


def read_text(path: Path, errors: list[str]) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        errors.append(f"{path.relative_to(ROOT)} is not readable UTF-8 text: {exc}")
        return ""


def parse_frontmatter(path: Path, errors: list[str]) -> dict[str, str]:
    text = read_text(path, errors)
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", text, re.DOTALL)
    if not match:
        errors.append(f"{path.relative_to(ROOT)} has no valid YAML frontmatter")
        return {}

    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        field = re.match(r"^([a-z_]+):\s*(.+?)\s*$", line)
        if field:
            fields[field.group(1)] = field.group(2).strip("\"'")
    return fields


def load_json(path: Path, errors: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"{path.relative_to(ROOT)} is not valid JSON: {exc}")
        return None


def validate_eval_file(path: Path, skill_name: str, errors: list[str]) -> None:
    payload = load_json(path, errors)
    if not isinstance(payload, dict):
        return
    if payload.get("skill_name") != skill_name:
        errors.append(f"{path.relative_to(ROOT)} skill_name must be {skill_name!r}")

    evals = payload.get("evals")
    if not isinstance(evals, list) or not evals:
        errors.append(f"{path.relative_to(ROOT)} must contain a non-empty evals list")
        return

    seen_ids: set[object] = set()
    for index, item in enumerate(evals, start=1):
        label = f"{path.relative_to(ROOT)} eval #{index}"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object")
            continue
        eval_id = item.get("id")
        if not isinstance(eval_id, int) or isinstance(eval_id, bool):
            errors.append(f"{label} id must be an integer")
        elif eval_id in seen_ids:
            errors.append(f"{label} duplicates id {eval_id}")
        else:
            seen_ids.add(eval_id)
        for field in ("prompt", "expected_output"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                errors.append(f"{label} {field} must be a non-empty string")
        if not isinstance(item.get("files"), list):
            errors.append(f"{label} files must be a list")


def validate_openai_metadata(path: Path, skill_name: str, errors: list[str]) -> None:
    text = read_text(path, errors)
    required_fields = ("interface:", "display_name:", "short_description:", "default_prompt:")
    for field in required_fields:
        if not re.search(rf"^\s*{re.escape(field)}", text, re.MULTILINE):
            errors.append(f"{path.relative_to(ROOT)} is missing {field.rstrip(':')}")
    if f"${skill_name}" not in text:
        errors.append(
            f"{path.relative_to(ROOT)} default_prompt must explicitly name ${skill_name}"
        )


def validate_skill(skill_dir: Path, errors: list[str]) -> str | None:
    skill_file = skill_dir / "SKILL.md"
    fields = parse_frontmatter(skill_file, errors)
    name = fields.get("name")
    description = fields.get("description")
    if name != skill_dir.name:
        errors.append(
            f"{skill_file.relative_to(ROOT)} name must match directory {skill_dir.name!r}"
        )
    if not description:
        errors.append(f"{skill_file.relative_to(ROOT)} needs a non-empty description")

    metadata = skill_dir / "agents" / "openai.yaml"
    eval_file = skill_dir / "evals" / "evals.json"
    for required in (metadata, eval_file):
        if not required.is_file():
            errors.append(f"missing {required.relative_to(ROOT)}")

    if metadata.is_file():
        validate_openai_metadata(metadata, skill_dir.name, errors)
    if eval_file.is_file():
        validate_eval_file(eval_file, skill_dir.name, errors)
    return name


def iter_text_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in {"LICENSE"}:
            files.append(path)
    return files


def validate_sensitive_markers(errors: list[str]) -> None:
    markers = {
        "company email": "@" + "bytedance.com",
        "local corporate path": "/Users/" + "bytedance/",
        "GitHub classic token": "gh" + "p_",
        "GitHub fine-grained token": "github_" + "pat_",
        "AWS access key": "AK" + "IA",
        "private key": "-----BEGIN " + "PRIVATE KEY-----",
        "RSA private key": "-----BEGIN RSA " + "PRIVATE KEY-----",
        "OpenSSH private key": "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
    }
    for path in iter_text_files():
        text = read_text(path, errors)
        for label, marker in markers.items():
            if marker in text:
                errors.append(f"{path.relative_to(ROOT)} contains a possible {label}")


def main() -> int:
    errors: list[str] = []
    for relative in REQUIRED_ROOT_FILES:
        if not (ROOT / relative).is_file():
            errors.append(f"missing {relative}")

    skill_directories = sorted(
        path.parent for path in ROOT.glob("*/SKILL.md") if path.is_file()
    )
    if not skill_directories:
        errors.append("no top-level skills found")

    names: list[str] = []
    for skill_directory in skill_directories:
        if name := validate_skill(skill_directory, errors):
            names.append(name)
    duplicate_names = sorted({name for name in names if names.count(name) > 1})
    if duplicate_names:
        errors.append(f"duplicate skill names: {', '.join(duplicate_names)}")

    readme = read_text(ROOT / "README.md", errors) if (ROOT / "README.md").is_file() else ""
    for skill_directory in skill_directories:
        if f"`{skill_directory.name}`" not in readme:
            errors.append(f"README.md does not list {skill_directory.name}")

    validate_sensitive_markers(errors)

    if errors:
        print("Repository validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(f"Repository validation passed for {len(skill_directories)} skills.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
