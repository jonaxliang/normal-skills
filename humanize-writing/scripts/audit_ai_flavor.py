#!/usr/bin/env python3
from __future__ import annotations

import argparse
from bisect import bisect_right
import json
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence


DOCUMENT_CLAIM = "Signals are editing cues, not proof of AI authorship."


@dataclass(frozen=True)
class Pattern:
    pattern_id: str
    language: str
    category: str
    expression: re.Pattern[str]
    explanation: str


@dataclass(frozen=True)
class Finding:
    pattern_id: str
    language: str
    category: str
    line: int
    match: str
    explanation: str


PATTERNS = (
    Pattern(
        pattern_id="ZH_CHATBOT_ROUTE",
        language="zh",
        category="staged_transition",
        expression=re.compile(r"下面(?:我们)?来(?:拆解|分析)"),
        explanation="This staged transition can be replaced with a direct, specific lead-in.",
    ),
    Pattern(
        pattern_id="ZH_EMPTY_EMPHASIS",
        language="zh",
        category="empty_emphasis",
        expression=re.compile(r"值得注意的是"),
        explanation="Name the important detail directly instead of announcing its importance.",
    ),
    Pattern(
        pattern_id="ZH_BINARY_SHELL",
        language="zh",
        category="binary_parallelism",
        expression=re.compile(
            r"(?:不是[^。！？\n]{1,80}?而是|不仅[^。！？\n]{1,80}?更(?:是|要|能|会|可)?)"
        ),
        explanation="Check whether the contrast adds substance or only supplies a rhetorical frame.",
    ),
    Pattern(
        pattern_id="ZH_SUMMARY_CLOSER",
        language="zh",
        category="generic_summary",
        expression=re.compile(r"总的来说"),
        explanation="Consider ending with the concrete conclusion instead of a generic summary cue.",
    ),
    Pattern(
        pattern_id="EN_GENERIC_OPENER",
        language="en",
        category="generic_opener",
        expression=re.compile(
            r"\bIn today(?:'|’)s rapidly evolving(?:\s+(?:world|landscape|environment|era))?\b",
            re.IGNORECASE,
        ),
        explanation="Open with the concrete change or context instead of a broad scene-setter.",
    ),
    Pattern(
        pattern_id="EN_SIGNIFICANCE_INFLATION",
        language="en",
        category="significance_inflation",
        expression=re.compile(r"\bserves as a testament\b", re.IGNORECASE),
        explanation="State what the example demonstrates in specific, measurable terms.",
    ),
    Pattern(
        pattern_id="EN_NEGATIVE_PARALLELISM",
        language="en",
        category="negative_parallelism",
        expression=re.compile(
            r"\bnot\s+just\b[^.!?\n]{1,120}?\bbut\b", re.IGNORECASE
        ),
        explanation="Check whether the paired contrast is clearer as one direct assertion.",
    ),
    Pattern(
        pattern_id="EN_CORE_FRAMING",
        language="en",
        category="generic_framing",
        expression=re.compile(r"\bAt its core\b", re.IGNORECASE),
        explanation="Replace the framing phrase with the precise subject or main claim.",
    ),
)


_URL_SCHEME_RE = re.compile(r"https?://", re.IGNORECASE)
_URL_STOP_CHARACTERS = frozenset(
    {"<", ">", '"', "'", "`", "\\", ",", ";"}
)
_URL_CJK_PUNCTUATION = frozenset("。！？；，、：（）【】《》“”‘’")
_URL_TRAILING_PUNCTUATION = frozenset(".,;:!?")
_UNSAFE_DISPLAY_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})


def _fence_delimiter(line: str) -> tuple[str, int, str] | None:
    position = 0
    while position < len(line) and line[position] == " ":
        position += 1
        if position > 3:
            return None
    if position >= len(line) or line[position] not in {"`", "~"}:
        return None

    marker = line[position]
    run_end = position
    while run_end < len(line) and line[run_end] == marker:
        run_end += 1
    length = run_end - position
    if length < 3:
        return None
    return marker, length, line[run_end:]


def _fenced_code_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    offset = 0
    fence_start: int | None = None
    fence_marker = ""
    fence_length = 0

    for line in text.splitlines(keepends=True):
        line_body = line.rstrip("\r\n")
        delimiter = _fence_delimiter(line_body)
        if fence_start is None:
            if delimiter is not None:
                marker, length, remainder = delimiter
                if marker != "`" or "`" not in remainder:
                    fence_start = offset
                    fence_marker = marker
                    fence_length = length
        elif delimiter is not None:
            marker, length, remainder = delimiter
            if (
                marker == fence_marker
                and length >= fence_length
                and not remainder.strip(" \t")
            ):
                spans.append((fence_start, offset + len(line)))
                fence_start = None
                fence_marker = ""
                fence_length = 0
        offset += len(line)

    if fence_start is not None:
        spans.append((fence_start, len(text)))
    return spans


def _is_escaped(text: str, position: int) -> bool:
    backslashes = 0
    position -= 1
    while position >= 0 and text[position] == "\\":
        backslashes += 1
        position -= 1
    return backslashes % 2 == 1


def _append_paired_code_spans(
    runs: list[tuple[int, int, int]], spans: list[tuple[int, int]]
) -> None:
    next_matching: list[int | None] = [None] * len(runs)
    next_by_length: dict[int, int] = {}
    for index in range(len(runs) - 1, -1, -1):
        length = runs[index][2]
        next_matching[index] = next_by_length.get(length)
        next_by_length[length] = index

    index = 0
    while index < len(runs):
        closing_index = next_matching[index]
        if closing_index is None:
            index += 1
            continue
        spans.append((runs[index][0], runs[closing_index][1]))
        index = closing_index + 1


def _starts_isolated_inline_block(line: str) -> bool:
    """Recognize only ATX headings, list items, and block quotes."""

    indent = len(line) - len(line.lstrip(" "))
    if indent > 3:
        return False
    content = line[indent:]
    if not content:
        return False
    if content.startswith(">"):
        return True
    if content[0] in {"-", "+", "*"}:
        return len(content) == 1 or content[1] in {" ", "\t"}
    if content[0] == "#":
        marker_end = 0
        while marker_end < len(content) and content[marker_end] == "#":
            marker_end += 1
        return marker_end <= 6 and (
            marker_end == len(content) or content[marker_end] in {" ", "\t"}
        )

    marker_end = 0
    while marker_end < len(content) and content[marker_end].isdigit():
        marker_end += 1
    return (
        1 <= marker_end <= 9
        and marker_end < len(content)
        and content[marker_end] in {".", ")"}
        and (
            marker_end + 1 == len(content)
            or content[marker_end + 1] in {" ", "\t"}
        )
    )


def _inline_code_spans(
    text: str, fenced_spans: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    runs: list[tuple[int, int, int]] = []
    position = 0
    fenced_index = 0
    line_has_content = False
    at_line_start = True
    flush_at_line_end = False

    while position < len(text):
        while (
            fenced_index < len(fenced_spans)
            and fenced_spans[fenced_index][1] <= position
        ):
            fenced_index += 1
        if (
            fenced_index < len(fenced_spans)
            and fenced_spans[fenced_index][0] <= position
        ):
            _append_paired_code_spans(runs, spans)
            runs.clear()
            position = fenced_spans[fenced_index][1]
            line_has_content = False
            at_line_start = True
            flush_at_line_end = False
            continue

        if at_line_start:
            line_end = text.find("\n", position)
            if line_end < 0:
                line_end = len(text)
            line = text[position:line_end].rstrip("\r")
            flush_at_line_end = _starts_isolated_inline_block(line)
            if flush_at_line_end:
                _append_paired_code_spans(runs, spans)
                runs.clear()
            at_line_start = False

        character = text[position]
        if character == "\n":
            if not line_has_content or flush_at_line_end:
                _append_paired_code_spans(runs, spans)
                runs.clear()
            line_has_content = False
            at_line_start = True
            flush_at_line_end = False
            position += 1
            continue
        if character not in {" ", "\t", "\r"}:
            line_has_content = True
        if character != "`":
            position += 1
            continue

        run_end = position + 1
        while run_end < len(text) and text[run_end] == "`":
            run_end += 1
        if not _is_escaped(text, position):
            runs.append((position, run_end, run_end - position))
        position = run_end

    _append_paired_code_spans(runs, spans)
    return spans


def _markdown_link_target_spans(
    text: str, blocked_spans: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    position = 0
    blocked_index = 0
    target_start: int | None = None
    depth = 0
    label_stack: list[int] = []

    while position < len(text):
        while (
            blocked_index < len(blocked_spans)
            and blocked_spans[blocked_index][1] <= position
        ):
            blocked_index += 1
        if (
            blocked_index < len(blocked_spans)
            and blocked_spans[blocked_index][0] <= position
        ):
            target_start = None
            depth = 0
            label_stack.clear()
            position = blocked_spans[blocked_index][1]
            continue

        character = text[position]
        if target_start is not None:
            if character == "\n":
                target_start = None
                depth = 0
                label_stack.clear()
                position += 1
                continue
            if character == "\\" and position + 1 < len(text):
                position += 2
                continue
            if character == "(":
                depth += 1
            elif character == ")":
                if depth == 0:
                    spans.append((target_start, position))
                    target_start = None
                    position += 1
                    continue
                depth -= 1
            position += 1
            continue

        if character == "\n":
            label_stack.clear()
            position += 1
            continue
        if character == "[" and not _is_escaped(text, position):
            label_stack.append(position)
            position += 1
            continue
        if character == "]" and not _is_escaped(text, position):
            if not label_stack:
                position += 1
                continue
            label_stack.pop()
            if position + 1 < len(text) and text[position + 1] == "(":
                target_start = position + 2
                depth = 0
                position += 2
                continue
            position += 1
            continue
        position += 1
    return spans


def _raw_url_end(text: str, scheme_end: int) -> int:
    position = scheme_end
    bracket_stack: list[tuple[str, int]] = []
    opening_brackets = {"(": ")", "[": "]", "{": "}"}
    closing_brackets = {")": "(", "]": "[", "}": "{"}

    while position < len(text):
        character = text[position]
        if (
            character.isspace()
            or character in _URL_STOP_CHARACTERS
            or character in _URL_CJK_PUNCTUATION
            or unicodedata.category(character) in _UNSAFE_DISPLAY_CATEGORIES
        ):
            break
        if character in opening_brackets:
            bracket_stack.append((character, position))
        elif character in closing_brackets:
            if not bracket_stack or bracket_stack[-1][0] != closing_brackets[character]:
                break
            bracket_stack.pop()
        position += 1

    end = position
    if bracket_stack:
        end = min(end, bracket_stack[0][1])
    while end > scheme_end and text[end - 1] in _URL_TRAILING_PUNCTUATION:
        end -= 1
    return end


def _raw_url_spans(
    text: str, blocked_spans: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    position = 0
    blocked_index = 0

    while position < len(text):
        while (
            blocked_index < len(blocked_spans)
            and blocked_spans[blocked_index][1] <= position
        ):
            blocked_index += 1
        if (
            blocked_index < len(blocked_spans)
            and blocked_spans[blocked_index][0] <= position
        ):
            position = blocked_spans[blocked_index][1]
            continue

        scheme = _URL_SCHEME_RE.match(text, position)
        if scheme is None:
            position += 1
            continue
        end = _raw_url_end(text, scheme.end())
        spans.append((position, end))
        position = end

    return spans


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if start >= end:
            continue
        if merged and start <= merged[-1][1]:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


def _protected_spans(text: str) -> list[tuple[int, int]]:
    fenced_spans = _fenced_code_spans(text)
    inline_spans = _inline_code_spans(text, fenced_spans)
    code_spans = _merge_spans([*fenced_spans, *inline_spans])
    link_spans = _markdown_link_target_spans(text, code_spans)
    structural_spans = _merge_spans([*code_spans, *link_spans])
    url_spans = _raw_url_spans(text, structural_spans)
    return _merge_spans([*structural_spans, *url_spans])


def mask_protected(text: str) -> str:
    """Blank fenced/inline code, link targets, and URLs without changing offsets."""

    characters = list(text)
    for start, end in _protected_spans(text):
        for index in range(start, end):
            if characters[index] != "\n":
                characters[index] = " "
    return "".join(characters)


def scan_text(text: str) -> list[Finding]:
    """Return conservative editing signals found outside protected content."""

    masked = mask_protected(text)
    newline_indexes = [
        index for index, character in enumerate(text) if character == "\n"
    ]
    located: list[tuple[int, int, int, Finding]] = []
    seen: set[tuple[str, int, int]] = set()

    for declaration_order, pattern in enumerate(PATTERNS):
        for match in pattern.expression.finditer(masked):
            start, end = match.span()
            key = (pattern.pattern_id, start, end)
            if key in seen:
                continue
            seen.add(key)
            located.append(
                (
                    start,
                    declaration_order,
                    end,
                    Finding(
                        pattern_id=pattern.pattern_id,
                        language=pattern.language,
                        category=pattern.category,
                        line=bisect_right(newline_indexes, start) + 1,
                        match=text[start:end],
                        explanation=pattern.explanation,
                    ),
                )
            )

    located.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in located]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Find conservative Chinese and English AI-flavor editing cues."
    )
    parser.add_argument("file", nargs="?", help="UTF-8 text or Markdown file to scan")
    parser.add_argument(
        "--stdin", action="store_true", help="read the document from standard input"
    )
    parser.add_argument("--json", action="store_true", help="emit structured JSON")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    if arguments.file is None and not arguments.stdin:
        parser.error("provide a file or --stdin")
    if arguments.file is not None and arguments.stdin:
        parser.error("file and --stdin are mutually exclusive")
    return arguments


def _read_input(file: str | None, use_stdin: bool) -> str:
    try:
        if use_stdin:
            return sys.stdin.read()
        assert file is not None
        return Path(file).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        source = "standard input" if use_stdin else repr(file)
        _build_parser().error(f"unable to read {source}: {error}")


def _safe_display(value: str) -> str:
    rendered: list[str] = []
    for character in value:
        if character == "\\":
            rendered.append("\\\\")
        elif character == "\r":
            rendered.append("\\r")
        elif character == "\n":
            rendered.append("\\n")
        elif character == "\t":
            rendered.append("\\t")
        elif unicodedata.category(character) in _UNSAFE_DISPLAY_CATEGORIES:
            codepoint = ord(character)
            if codepoint <= 0xFFFF:
                rendered.append(f"\\u{codepoint:04x}")
            else:
                rendered.append(f"\\U{codepoint:08x}")
        else:
            rendered.append(character)
    return "".join(rendered)


def _print_human(findings: list[Finding]) -> None:
    print(DOCUMENT_CLAIM)
    print(f"Finding count: {len(findings)}")
    for finding in findings:
        print(f"- {finding.pattern_id} (line {finding.line})")
        print(f"  Match: {_safe_display(finding.match)}")
        print(f"  Explanation: {_safe_display(finding.explanation)}")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(argv)
    text = _read_input(arguments.file, arguments.stdin)
    findings = scan_text(text)

    if arguments.json:
        payload = {
            "document_claim": DOCUMENT_CLAIM,
            "finding_count": len(findings),
            "findings": [asdict(finding) for finding in findings],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_human(findings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
