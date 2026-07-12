import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "audit_ai_flavor.py"
SKILL_ROOT = Path(__file__).parents[1]
SKILL_FILE = SKILL_ROOT / "SKILL.md"
OPENAI_FILE = SKILL_ROOT / "agents" / "openai.yaml"
PATTERNS_ZH_FILE = SKILL_ROOT / "references" / "patterns-zh.md"
PATTERNS_EN_FILE = SKILL_ROOT / "references" / "patterns-en.md"
VOICE_FIDELITY_FILE = SKILL_ROOT / "references" / "voice-and-fidelity.md"
PACKAGE_FILES = (
    SKILL_FILE,
    OPENAI_FILE,
    PATTERNS_ZH_FILE,
    PATTERNS_EN_FILE,
    VOICE_FIDELITY_FILE,
)
SPEC = importlib.util.spec_from_file_location("audit_ai_flavor", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


DOCUMENT_CLAIM = "Signals are editing cues, not proof of AI authorship."
REQUIRED_FINDING_FIELDS = {
    "pattern_id",
    "language",
    "category",
    "line",
    "match",
    "explanation",
}
FORBIDDEN_JSON_FIELD_MARKERS = {
    "aiscore",
    "probability",
    "likelihood",
    "generatedbyai",
    "authorship",
    "verdict",
}


def run_cli(*args, input_text=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )


def newline_indexes(text):
    return [index for index, character in enumerate(text) if character == "\n"]


def expected_mask(fragment):
    return "".join("\n" if character == "\n" else " " for character in fragment)


def iter_json_field_names(value):
    if isinstance(value, dict):
        for field_name, nested_value in value.items():
            yield field_name
            yield from iter_json_field_names(nested_value)
    elif isinstance(value, list):
        for item in value:
            yield from iter_json_field_names(item)


class AuditTests(unittest.TestCase):
    def test_detects_high_confidence_chinese_patterns(self):
        findings = MODULE.scan_text(
            "下面我们来拆解。值得注意的是，这不是速度问题，而是系统问题。"
        )
        self.assertEqual(
            {item.pattern_id for item in findings},
            {"ZH_CHATBOT_ROUTE", "ZH_EMPTY_EMPHASIS", "ZH_BINARY_SHELL"},
        )

    def test_detects_high_confidence_english_patterns(self):
        findings = MODULE.scan_text(
            "In today's rapidly evolving landscape, this release serves as a "
            "testament to our commitment."
        )
        self.assertEqual(
            {item.pattern_id for item in findings},
            {"EN_GENERIC_OPENER", "EN_SIGNIFICANCE_INFLATION"},
        )

    def test_masks_inline_code_after_positive_control(self):
        signal = "not just speed but reliability"
        self.assertTrue(MODULE.scan_text(signal))
        self.assertEqual(MODULE.scan_text(f"`{signal}`"), [])

    def test_escaped_backticks_do_not_mask_visible_prose(self):
        findings = MODULE.scan_text(r"\`At its core\` remains visible.")

        self.assertEqual(
            [item.pattern_id for item in findings],
            ["EN_CORE_FRAMING"],
        )

    def test_unmatched_backticks_do_not_pair_across_blank_lines(self):
        text = "`unfinished code span\n\nAt its core, this is visible `"

        findings = MODULE.scan_text(text)

        self.assertEqual(
            [(item.pattern_id, item.line) for item in findings],
            [("EN_CORE_FRAMING", 3)],
        )

    def test_unmatched_backticks_do_not_cross_markdown_block_lines(self):
        cases = {
            "heading": "# `unfinished\nAt its core, visible prose.\n# `another",
            "list_item": "- `unfinished\nAt its core, visible prose.\n- `another",
            "blockquote": "> `unfinished\nAt its core, visible prose.\n> `another",
        }

        for name, text in cases.items():
            with self.subTest(name=name):
                self.assertEqual(
                    [
                        (item.pattern_id, item.line)
                        for item in MODULE.scan_text(text)
                    ],
                    [("EN_CORE_FRAMING", 2)],
                )

    def test_inline_code_can_span_soft_line_breaks_in_normal_prose(self):
        text = "Before `At its core,\nthe phrase remains code` after."

        self.assertEqual(MODULE.scan_text(text), [])

    def test_masks_multiline_fenced_code_after_positive_control(self):
        signal = "At its core, the retry policy changed."
        self.assertTrue(MODULE.scan_text(signal))
        fenced = f"```text\n{signal}\nsecond code line\n```"
        self.assertEqual(MODULE.scan_text(fenced), [])

    def test_longer_fence_closer_ends_fenced_block(self):
        text = (
            "```text\n"
            "At its core, this is hidden.\n"
            "````\n"
            "At its core, this is visible."
        )

        findings = MODULE.scan_text(text)

        self.assertEqual(
            [(item.pattern_id, item.line) for item in findings],
            [("EN_CORE_FRAMING", 4)],
        )

    def test_masks_raw_url_after_positive_control(self):
        signal = "值得注意的是"
        self.assertTrue(MODULE.scan_text(signal))
        self.assertEqual(MODULE.scan_text(f"https://example.com/{signal}"), [])

    def test_masks_relative_markdown_link_target_after_positive_control(self):
        signal = "下面我们来拆解"
        self.assertTrue(MODULE.scan_text(signal))
        self.assertEqual(MODULE.scan_text(f"[guide](./docs/{signal}.md)"), [])

    def test_markdown_url_target_does_not_mask_adjacent_prose(self):
        findings = MODULE.scan_text(
            "[guide](https://example.com)值得注意的是。"
        )

        self.assertEqual(
            [item.pattern_id for item in findings],
            ["ZH_EMPTY_EMPHASIS"],
        )

    def test_isolated_markdown_target_syntax_remains_visible_prose(self):
        findings = MODULE.scan_text(
            "Visible syntax ](At its core) remains prose."
        )

        self.assertEqual(
            [item.pattern_id for item in findings],
            ["EN_CORE_FRAMING"],
        )

    def test_raw_urls_stop_at_cjk_punctuation_and_balance_parentheses(self):
        cases = (
            ("详见https://example.com。值得注意的是，这很关键。", "https://example.com"),
            (
                "详见https://example.com/a_(b)。值得注意的是，这很关键。",
                "https://example.com/a_(b)",
            ),
        )

        for text, url in cases:
            with self.subTest(url=url):
                masked = MODULE.mask_protected(text)
                start = text.index(url)
                self.assertEqual(
                    masked[start : start + len(url)],
                    expected_mask(url),
                )
                self.assertEqual(masked[start + len(url)], "。")
                self.assertEqual(
                    [item.pattern_id for item in MODULE.scan_text(text)],
                    ["ZH_EMPTY_EMPHASIS"],
                )

    def test_raw_urls_stop_at_ascii_comma_and_semicolon(self):
        for boundary in (",", ";"):
            with self.subTest(boundary=boundary):
                text = (
                    f"See https://example.com{boundary}"
                    "值得注意的是，这很关键。"
                )
                self.assertEqual(
                    [item.pattern_id for item in MODULE.scan_text(text)],
                    ["ZH_EMPTY_EMPHASIS"],
                )

    def test_mask_protected_preserves_offsets_and_blanks_protected_content(self):
        inline = "`not just speed but reliability`"
        fenced = "```text\nAt its core, the retry policy changed.\nsecond line\n```"
        raw_url = "https://example.com/值得注意的是"
        relative_target = "./docs/下面我们来拆解.md"
        text = (
            f"{inline}\n{fenced}\n{raw_url}\n"
            f"[guide]({relative_target})\nordinary prose"
        )

        masked = MODULE.mask_protected(text)

        self.assertEqual(len(masked), len(text))
        self.assertEqual(newline_indexes(masked), newline_indexes(text))
        for fragment in (inline, fenced, raw_url, relative_target):
            with self.subTest(fragment=fragment):
                start = text.index(fragment)
                self.assertEqual(
                    masked[start : start + len(fragment)],
                    expected_mask(fragment),
                )

    def test_reports_original_line_number_after_masking(self):
        fenced = (
            "```text\n"
            "下面我们来拆解。\n"
            "At its core, this is not just speed but reliability.\n"
            "```"
        )
        text = f"{fenced}\n总的来说，这很关键。"

        masked = MODULE.mask_protected(text)
        findings = MODULE.scan_text(text)

        self.assertEqual(len(masked), len(text))
        self.assertEqual(newline_indexes(masked), newline_indexes(text))
        self.assertEqual([item.match for item in findings], ["总的来说"])
        self.assertEqual([item.line for item in findings], [5])

    def test_orders_findings_by_source_position_without_duplicates(self):
        text = "\n".join(
            [
                "下面我们来拆解。",
                "In today's rapidly evolving landscape.",
                "值得注意的是。",
                "This release serves as a testament to our commitment.",
            ]
        )

        findings = MODULE.scan_text(text)
        finding_keys = [
            (item.pattern_id, item.line, item.match) for item in findings
        ]

        self.assertEqual(
            [item.pattern_id for item in findings],
            [
                "ZH_CHATBOT_ROUTE",
                "EN_GENERIC_OPENER",
                "ZH_EMPTY_EMPHASIS",
                "EN_SIGNIFICANCE_INFLATION",
            ],
        )
        self.assertEqual([item.line for item in findings], [1, 2, 3, 4])
        self.assertEqual(len(finding_keys), len(set(finding_keys)))

    def test_large_malformed_markdown_completes_and_preserves_visible_prose(self):
        text = "](" * 30_000 + "`unfinished\n\nAt its core, this is visible."

        result = run_cli("--stdin", "--json", input_text=text)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn(
            "EN_CORE_FRAMING",
            [item["pattern_id"] for item in payload["findings"]],
        )

    def test_does_not_flag_clean_human_text(self):
        text = "昨晚服务挂了两次。第二次值班同学直接回滚，十分钟后恢复。"
        self.assertEqual(MODULE.scan_text(text), [])

    def test_stdin_json_reports_document_claim_and_findings(self):
        result = run_cli(
            "--stdin",
            "--json",
            input_text="下面我们来拆解。值得注意的是，这不是速度问题，而是系统问题。",
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["document_claim"], DOCUMENT_CLAIM)
        self.assertEqual(payload["finding_count"], len(payload["findings"]))
        self.assertGreater(payload["finding_count"], 0)

    def test_file_input_emits_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "sample.md"
            source.write_text("总的来说，这很关键。", encoding="utf-8")

            result = run_cli(str(source), "--json")

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["finding_count"], len(payload["findings"]))
        self.assertGreater(payload["finding_count"], 0)

    def test_json_findings_have_editing_signal_fields_without_authorship_verdicts(self):
        result = run_cli(
            "--stdin",
            "--json",
            input_text=(
                "In today's rapidly evolving landscape, this release serves as a "
                "testament to our commitment."
            ),
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["findings"])
        self.assertEqual(payload["document_claim"], DOCUMENT_CLAIM)
        for field_name in iter_json_field_names(payload):
            normalized = "".join(
                character for character in field_name.lower() if character.isalnum()
            )
            self.assertFalse(
                any(
                    marker in normalized
                    for marker in FORBIDDEN_JSON_FIELD_MARKERS
                ),
                msg=f"unsafe JSON field leaked: {field_name}",
            )
        for finding in payload["findings"]:
            self.assertEqual(set(finding), REQUIRED_FINDING_FIELDS)
            explanation = finding["explanation"].lower()
            for unsafe_claim in (
                "proof",
                "ai-generated",
                "ai generated",
                "generated by ai",
                "authorship",
            ):
                self.assertNotIn(unsafe_claim, explanation)

    def test_missing_input_returns_usage_error(self):
        result = run_cli(input_text="")
        self.assertEqual(result.returncode, 2)
        self.assertRegex(
            result.stderr.lower(),
            r"(usage:|error:|no input|input (?:is )?required|provide .*input)",
        )

    def test_nonexistent_file_returns_usage_error_without_traceback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "missing.md"

            result = run_cli(str(source))

        self.assertEqual(result.returncode, 2)
        self.assertIn("error:", result.stderr.lower())
        self.assertIn(str(source), result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_invalid_utf8_file_returns_usage_error_without_traceback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "invalid.md"
            source.write_bytes(b"\xff\xfe")

            result = run_cli(str(source))

        self.assertEqual(result.returncode, 2)
        self.assertIn("error:", result.stderr.lower())
        self.assertRegex(result.stderr.lower(), r"(utf-8|decode|codec)")
        self.assertNotIn("Traceback", result.stderr)

    def test_public_parse_args_accepts_stdin(self):
        arguments = MODULE.parse_args(["--stdin"])

        self.assertTrue(arguments.stdin)
        self.assertFalse(arguments.json)
        self.assertIsNone(arguments.file)

    def test_help_succeeds(self):
        result = run_cli("--help", input_text="")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        help_text = result.stdout + result.stderr
        self.assertIn("--stdin", help_text)
        self.assertIn("--json", help_text)

    def test_file_and_stdin_conflict_returns_usage_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "sample.md"
            source.write_text("值得注意的是。", encoding="utf-8")

            result = run_cli(
                str(source),
                "--stdin",
                input_text="下面我们来拆解。",
            )

        self.assertEqual(result.returncode, 2)

    def test_unknown_argument_returns_usage_error(self):
        result = run_cli("--unknown-option", input_text="")
        self.assertEqual(result.returncode, 2)

    def test_default_human_output_reports_signal_without_authorship_verdict(self):
        result = run_cli("--stdin", input_text="值得注意的是。")

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("ZH_EMPTY_EMPHASIS", result.stdout)
        output = result.stdout.lower()
        for unsafe_claim in (
            "ai score",
            "ai_score",
            "probability",
            "likelihood",
            "generated by ai",
            "ai-generated",
            "authorship verdict",
        ):
            self.assertNotIn(unsafe_claim, output)

    def test_human_output_escapes_terminal_control_characters(self):
        controls = "\x1b]0;owned\x07\r\u202e\u2028\u2029"
        text = f"not just safe{controls} but reliable"

        result = run_cli("--stdin", input_text=text)

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("EN_NEGATIVE_PARALLELISM", result.stdout)
        for character in ("\x1b", "\x07", "\r", "\u202e", "\u2028", "\u2029"):
            with self.subTest(character=repr(character)):
                self.assertNotIn(character, result.stdout)
        for escaped in (
            r"\u001b",
            r"\u0007",
            r"\r",
            r"\u202e",
            r"\u2028",
            r"\u2029",
        ):
            with self.subTest(escaped=escaped):
                self.assertIn(escaped, result.stdout)


class PackageContractTests(unittest.TestCase):
    def read_required(self, path):
        self.assertTrue(path.is_file(), msg=f"required package file is missing: {path}")
        return path.read_text(encoding="utf-8")

    def parse_frontmatter(self):
        skill_text = self.read_required(SKILL_FILE)
        self.assertTrue(skill_text.startswith("---\n"))
        closing_index = skill_text.find("\n---\n", 4)
        self.assertNotEqual(closing_index, -1, msg="SKILL.md frontmatter is not closed")
        fields = {}
        for line in skill_text[4:closing_index].splitlines():
            key, separator, value = line.partition(":")
            self.assertEqual(separator, ":", msg=f"invalid frontmatter line: {line}")
            fields[key.strip()] = value.strip().strip('"')
        return fields

    def test_required_package_files_exist(self):
        for path in PACKAGE_FILES:
            with self.subTest(path=path):
                self.assertTrue(
                    path.is_file(),
                    msg=f"required package file is missing: {path}",
                )

    def test_frontmatter_name_and_description_cover_bilingual_triggers(self):
        fields = self.parse_frontmatter()

        self.assertEqual(set(fields), {"name", "description"})
        self.assertEqual(fields["name"], "humanize-writing")
        description = fields["description"]
        self.assertTrue(description.startswith("Use when"))
        self.assertNotRegex(description, r"\b(?:I|we|you)\b")
        for trigger in (
            "Chinese",
            "English",
            "AI-generated",
            "robotic",
            "generic",
            "over-polished",
            "template-like",
            "sample",
            "去AI味",
            "降低AI感",
            "改得像人写",
            "润色得自然",
            "humanize",
            "remove AI tone",
            "sound less robotic",
            "match my voice",
        ):
            with self.subTest(trigger=trigger):
                self.assertIn(trigger.lower(), description.lower())

    def test_skill_is_concise_and_directly_links_all_references(self):
        skill_text = self.read_required(SKILL_FILE)

        self.assertLess(len(skill_text.splitlines()), 500)
        for relative_path in (
            "references/patterns-zh.md",
            "references/patterns-en.md",
            "references/voice-and-fidelity.md",
        ):
            with self.subTest(relative_path=relative_path):
                self.assertRegex(
                    skill_text,
                    rf"\[[^\]]+\]\({relative_path.replace('.', r'\.') }\)",
                )

    def test_runtime_contract_preserves_fidelity_without_detector_promises(self):
        skill_text = self.read_required(SKILL_FILE)
        runtime_text = "\n".join(
            self.read_required(path)
            for path in (*PACKAGE_FILES, SCRIPT)
        )

        for forbidden_promise in (
            "guarantee undetectable",
            "guaranteed undetectable",
            "bypass AI detectors",
            "evade detection",
        ):
            with self.subTest(forbidden_promise=forbidden_promise):
                self.assertNotIn(forbidden_promise.lower(), runtime_text.lower())

        self.assertRegex(
            skill_text,
            r"(?m)^- \*\*Rewrite \(default\):\*\* (?:Output|Return) only the final body text\.$",
        )
        for contract in (
            "Never invent facts, examples, experiences, opinions, emotions, jokes, slang, quotes, or citations.",
            "Never transfer facts, claims, opinions, or experiences from a sample into the target.",
            "Retry the affected span once. If it still drifts, restore the original span.",
            "meaning and protected content > domain register > supplied voice > naturalness > cosmetic variation",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, skill_text)

    def test_author_stance_is_a_semantic_anchor(self):
        required_phrases = (
            "opinions and evaluative claims are semantic anchors",
            "only zero-proposition meta language may be deleted automatically",
            "delete or tone down evaluation only with explicit user authorization",
            "if a passage cannot be naturalized without changing its stance, preserve it or ask",
            "humanization alone does not authorize deleting, weakening, or strengthening the target's opinions, evaluative claims, brand stance, or promotional stance",
        )
        for path in (SKILL_FILE, VOICE_FIDELITY_FILE):
            with self.subTest(path=path):
                text = self.read_required(path).lower()
                missing = [
                    phrase for phrase in required_phrases if phrase not in text
                ]
                self.assertEqual(
                    missing,
                    [],
                    msg=f"author-stance contract is incomplete in {path}",
                )

    def test_openai_interface_metadata_is_exact(self):
        openai_text = self.read_required(OPENAI_FILE)
        expected = '''interface:
  display_name: "中英文本去 AI 味"
  short_description: "保留事实与作者语气，润色中英文 AI 式、模板化表达"
  default_prompt: "使用 $humanize-writing 润色下面的文本，去除 AI 味并保留原意；如果提供了样文，请匹配样文语气。"
'''
        self.assertEqual(openai_text, expected)

    def test_pattern_references_are_contextual_catalogs_not_banned_word_lists(self):
        category_headings = (
            "Chatbot artifacts",
            "Generic openings and closings",
            "Inflated significance",
            "Promotional language and vague attribution",
            "Binary framing and false concessions",
            "Mechanical sequence and rule of three",
            "Symmetry and parallel shells",
            "Unsupported emotion, opinion, and intimacy",
            "Fake engagement",
            "Nominalization, passive voice, and translation tone",
            "Formatting",
            "Lexical clusters",
        )
        for path in (PATTERNS_ZH_FILE, PATTERNS_EN_FILE):
            with self.subTest(path=path):
                text = self.read_required(path)
                self.assertIn("Do not rewrite from one signal alone", text)
                self.assertIn("false positive", text.lower())
                self.assertIn("cluster", text.lower())
                for heading in category_headings:
                    self.assertIn(f"### {heading}", text)
                field_counts = [
                    text.count(field)
                    for field in (
                        "**Signals**",
                        "**Why it reads as AI-shaped**",
                        "**Keep when**",
                        "**Rewrite move**",
                    )
                ]
                self.assertEqual(len(set(field_counts)), 1)
                self.assertEqual(field_counts[0], len(category_headings))

    def test_pattern_references_guard_unsupported_emotion_opinion_and_intimacy(self):
        language_markers = {
            PATTERNS_ZH_FILE: "材料未支撑的情绪、观点或亲密感",
            PATTERNS_EN_FILE: "unsupported emotion, opinion, or intimacy",
        }
        heading = "### Unsupported emotion, opinion, and intimacy"
        for path, language_marker in language_markers.items():
            with self.subTest(path=path):
                text = self.read_required(path)
                self.assertIn(heading, text)
                section = text.split(heading, 1)[1].split("\n### ", 1)[0]
                self.assertIn(language_marker, section)
                for field in (
                    "**Signals**",
                    "**Why it reads as AI-shaped**",
                    "**Keep when**",
                    "**Rewrite move**",
                ):
                    self.assertIn(field, section)

    def test_voice_and_fidelity_reference_defines_rollback_contract(self):
        text = self.read_required(VOICE_FIDELITY_FILE)

        for heading in (
            "# Voice and Fidelity",
            "## Separate target and sample",
            "## Exact protected spans",
            "## Semantic anchors",
            "## Voice contract",
            "## Minimal-edit rule",
            "## Fidelity retry and rollback",
            "## High-stakes conservative mode",
            "## Final checklist",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, text)
        self.assertIn("Never copy sample facts, claims, opinions, or experiences", text)
        self.assertIn("retry once", text.lower())
        self.assertIn("restore the original span", text.lower())
        self.assertIn("sentence-level rollback", text)
        self.assertIn("affected original sentence", text)
        self.assertIn(
            "meaning and protected content > domain register > supplied voice > naturalness > cosmetic variation",
            text,
        )

    def test_academic_prose_is_in_both_conservative_lists(self):
        skill_text = self.read_required(SKILL_FILE)
        voice_text = self.read_required(VOICE_FIDELITY_FILE)
        high_stakes_section = voice_text.split(
            "## High-stakes conservative mode", 1
        )[1].split("## Final checklist", 1)[0]

        with self.subTest(path=SKILL_FILE):
            self.assertRegex(skill_text, r"high-risk[^\n]*\bacademic\b")
        with self.subTest(path=VOICE_FIDELITY_FILE):
            self.assertIn("academic", high_stakes_section.lower())


if __name__ == "__main__":
    unittest.main()
