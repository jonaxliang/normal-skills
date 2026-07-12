# Humanize Writing Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify a bilingual, preservation-first agent skill that removes AI-shaped writing patterns from user-supplied Chinese and English prose without inventing content.

**Architecture:** Keep the runtime contract in `SKILL.md`, move language-specific patterns and voice/fidelity guidance into one-level-deep references, and use a dependency-free Python scanner only for deterministic signals. Develop evaluation-first: capture no-skill baselines, implement the scanner with unit tests, write the minimal skill that addresses observed failures, then repeat the same evaluations with the skill.

**Tech Stack:** Agent Skills Markdown/YAML, Python 3 standard library, `unittest`, JSON evaluation fixtures, Codex subagents, skill-creator benchmark viewer.

---

## File map

- Create `humanize-writing/evals/evals.json`: bilingual qualitative evaluations.
- Create `humanize-writing/scripts/audit_ai_flavor.py`: deterministic scanner and CLI.
- Create `humanize-writing/tests/test_audit.py`: scanner and package-contract tests.
- Create `humanize-writing/references/patterns-zh.md`: Chinese patterns and false-positive guards.
- Create `humanize-writing/references/patterns-en.md`: English patterns and false-positive guards.
- Create `humanize-writing/references/voice-and-fidelity.md`: sample calibration and fidelity gates.
- Create `humanize-writing/SKILL.md`: trigger metadata, workflow, modes, and guardrails.
- Create `humanize-writing/agents/openai.yaml`: Codex UI metadata.
- Modify `.gitignore`: exclude `humanize-writing-workspace/` evaluation artifacts.

### Task 1: RED evaluation set and no-skill baseline

**Files:**
- Create: `humanize-writing/evals/evals.json`
- Modify: `.gitignore`
- Create, untracked: `humanize-writing-workspace/iteration-1/eval-*/without_skill/outputs/result.md`

- [ ] **Step 1: Create five representative evaluations before writing the skill**

Use this schema. Each prompt must request final text only and state its protected anchors.

```json
{
  "skill_name": "humanize-writing",
  "evals": [
    {
      "id": 1,
      "prompt": "请把下面这段中文改得更像人写，只输出修改后的正文。必须保留 2025 年、37%、青岚项目三个事实。原文：在当今快速变化的行业环境中，青岚项目不仅是一次效率升级，更是组织协作方式的重要转折点。值得注意的是，项目在 2025 年将平均处理时间降低了 37%。总的来说，这一成果充分彰显了团队持续创新、协同共创与追求卓越的坚定承诺。",
      "expected_output": "Natural Chinese rewrite with all anchors preserved and no canned framing or forced triplet.",
      "files": []
    },
    {
      "id": 2,
      "prompt": "Rewrite this so it sounds like a person wrote it. Return only the revision and preserve Acme Sync, 18 ms, and the claim that offline mode is unchanged. In today's rapidly evolving digital landscape, Acme Sync serves as a testament to our unwavering commitment to seamless innovation. First, it delivers speed. Second, it enhances reliability. Third, it empowers teams. The new cache reduces median lookup time to 18 ms, while offline mode remains unchanged. Ultimately, this groundbreaking release marks a pivotal step forward.",
      "expected_output": "Natural English with all anchors preserved and no grandiosity or mechanical triplet.",
      "files": []
    },
    {
      "id": 3,
      "prompt": "请参考我的样文语气改写目标文本，只输出结果。样文：我不太喜欢把小事说成趋势。能跑就是能跑，坏了就修，没必要每次都上升到方法论。上周那个缓存问题就是这样，查了半天，最后发现少写了一行配置。目标文本：本次工作不仅完成了接口迁移，更体现了团队在复杂技术环境下持续突破的能力。通过这一过程，我们进一步夯实了系统基础，为未来的创新发展奠定了坚实基础。事实只有：接口迁移已经完成。",
      "expected_output": "Restrained sample-like rhythm; only the completed migration fact; no cache-story leakage.",
      "files": []
    },
    {
      "id": 4,
      "prompt": "润色下面的中英混合技术说明，只输出正文。所有代码、链接、引语、数字、否定和因果关系必须保持：由于 retry_count > 3 会触发熔断，v2.4.1 不会自动重试 POST 请求。日志原文是 \"circuit open\"，排查手册见 https://example.com/runbook。It is important to note that this behavior is not a regression; it exists because duplicate writes cannot be rolled back safely.",
      "expected_output": "Conservative rewrite with every protected span and relation preserved.",
      "files": []
    },
    {
      "id": 5,
      "prompt": "去一下 AI 味；如果已经自然就少改，只输出结果：昨晚十点我把补丁发给小周，他回了句‘先别合’。早上再看，测试环境果然还在报错。这个判断救了我们一次。",
      "expected_output": "Minimal or no edit and no invented detail.",
      "files": []
    }
  ]
}
```

- [ ] **Step 2: Add `humanize-writing-workspace/` to `.gitignore`**

- [ ] **Step 3: Run fresh baseline agents without the new skill**

Save each result to `humanize-writing-workspace/iteration-1/eval-<id>/without_skill/outputs/result.md`. Record concrete failures in `baseline-observations.md`.

- [ ] **Step 4: Verify RED**

Expected: at least one baseline invents content, drifts an anchor, keeps a major template, adds a preface, or over-edits clean prose. If all pass, strengthen the evaluations before creating `SKILL.md`.

- [ ] **Step 5: Commit**

```bash
git add .gitignore humanize-writing/evals/evals.json
git commit -m "test: add humanize writing baseline evaluations"
```

### Task 2: Scanner tests first

**Files:**
- Create: `humanize-writing/tests/test_audit.py`

- [ ] **Step 1: Write failing tests that load the script by path**

```python
class AuditTests(unittest.TestCase):
    def test_detects_high_confidence_chinese_patterns(self):
        findings = MODULE.scan_text("下面我们来拆解。值得注意的是，这不是速度问题，而是系统问题。")
        self.assertEqual(
            {item.pattern_id for item in findings},
            {"ZH_CHATBOT_ROUTE", "ZH_EMPTY_EMPHASIS", "ZH_BINARY_SHELL"},
        )

    def test_detects_high_confidence_english_patterns(self):
        findings = MODULE.scan_text(
            "In today's rapidly evolving landscape, this release serves as a testament to our commitment."
        )
        self.assertEqual(
            {item.pattern_id for item in findings},
            {"EN_GENERIC_OPENER", "EN_SIGNIFICANCE_INFLATION"},
        )

    def test_masks_code_urls_and_markdown_link_targets(self):
        text = "`not just X but Y` https://example.com/not-just [guide](https://example.com/at-its-core)"
        self.assertEqual(MODULE.scan_text(text), [])

    def test_reports_original_line_number_after_masking(self):
        findings = MODULE.scan_text("正常第一行。\n总的来说，这很关键。")
        self.assertEqual(findings[0].line, 2)

    def test_does_not_flag_clean_human_text(self):
        self.assertEqual(MODULE.scan_text("昨晚服务挂了两次。第二次值班同学直接回滚，十分钟后恢复。"), [])
```

Add subprocess tests for `--json`, file input, stdin, and missing input returning exit code 2.

- [ ] **Step 2: Run and verify RED**

```bash
python3 -m unittest discover -s humanize-writing/tests -v
```

Expected: ERROR because `humanize-writing/scripts/audit_ai_flavor.py` does not exist.

### Task 3: Minimal deterministic scanner

**Files:**
- Create: `humanize-writing/scripts/audit_ai_flavor.py`
- Test: `humanize-writing/tests/test_audit.py`

- [ ] **Step 1: Implement the standard-library scanner**

Define immutable `Pattern` and `Finding` dataclasses and these public functions:

```python
@dataclass(frozen=True)
class Finding:
    pattern_id: str
    language: str
    category: str
    line: int
    match: str
    explanation: str


def mask_protected(text: str) -> str:
    """Mask code, URLs, and Markdown link targets while preserving offsets."""


def scan_text(text: str) -> list[Finding]:
    """Return ordered editing signals without judging authorship."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Accept an optional file, --stdin, and --json."""


def main(argv: list[str] | None = None) -> int:
    """Read one input source, print findings, and return 0 after a valid scan."""
```

JSON output must include `document_claim`, `finding_count`, and finding objects. It must not include an AI probability. Mask fenced code, inline code, raw URLs, and Markdown link targets; preserve newlines and offsets.

- [ ] **Step 2: Run and verify GREEN**

```bash
python3 -m unittest discover -s humanize-writing/tests -v
```

Expected: all tests pass without warnings.

- [ ] **Step 3: Smoke-test stdin JSON output**

```bash
printf '%s\n' '总的来说，这不仅是升级，更是一次重要转折。' | python3 humanize-writing/scripts/audit_ai_flavor.py --stdin --json
```

- [ ] **Step 4: Commit**

```bash
git add humanize-writing/scripts/audit_ai_flavor.py humanize-writing/tests/test_audit.py
git commit -m "feat: add bilingual AI flavor audit scanner"
```

### Task 4: Minimal skill from observed failures

**Files:**
- Create: `humanize-writing/references/patterns-zh.md`
- Create: `humanize-writing/references/patterns-en.md`
- Create: `humanize-writing/references/voice-and-fidelity.md`
- Create: `humanize-writing/SKILL.md`
- Create: `humanize-writing/agents/openai.yaml`

- [ ] **Step 1: Write contextual language references**

Each pattern entry must contain `Signals`, `Why it reads as AI-shaped`, `Keep when`, and `Rewrite move`. State that clusters and context matter; do not create a flat banned-word list.

- [ ] **Step 2: Write voice and fidelity guidance**

Define protected spans, semantic anchors, sample-only style extraction, one retry on drift, sentence-level rollback, conservative high-stakes handling, and this priority:

```text
meaning and protected content > domain register > supplied voice > naturalness > cosmetic variation
```

- [ ] **Step 3: Write `SKILL.md` under 500 lines**

```yaml
---
name: humanize-writing
description: Use when revising existing Chinese or English prose that sounds AI-generated, robotic, generic, over-polished, template-like, or unlike the user's supplied writing samples, including requests to 去AI味、降低AI感、改得像人写、润色得自然、humanize text、remove AI tone, sound less robotic, or match my voice.
---
```

Include scope, non-goals, modes, reference routing, protected-content setup, structure-first workflow, minimal editing, sample voice, fidelity and residue gates, output contract, conservative cases, common mistakes, and scanner usage. Default rewrite output is final text only.

- [ ] **Step 4: Add `agents/openai.yaml`**

```yaml
interface:
  display_name: "中英文本去 AI 味"
  short_description: "保留事实与作者语气，润色中英文 AI 式、模板化表达"
  default_prompt: "使用 $humanize-writing 润色下面的文本，去除 AI 味并保留原意；如果提供了样文，请匹配样文语气。"
```

- [ ] **Step 5: Add package-contract tests**

Assert matching skill name, a third-person `Use when...` description, fewer than 500 lines, no detector-bypass promise, and all three references exist.

- [ ] **Step 6: Run both repository test suites**

```bash
python3 -m unittest discover -s humanize-writing/tests -v
python3 -m unittest discover -s download-xhs-images/tests -v
```

- [ ] **Step 7: Commit**

```bash
git add humanize-writing
git commit -m "feat: add bilingual humanize writing skill"
```

### Task 5: GREEN qualitative evaluations and refactor

**Files:**
- Create, untracked: `humanize-writing-workspace/iteration-1/eval-*/with_skill/outputs/result.md`
- Create, untracked: `humanize-writing-workspace/iteration-1/eval-*/eval_metadata.json`
- Create, untracked: `humanize-writing-workspace/iteration-1/*/grading.json`

- [ ] **Step 1: Run the same prompts with fresh agents using the new skill**

Each agent reads `SKILL.md` and required references completely, then saves only its user-facing result.

- [ ] **Step 2: Grade objective assertions**

Use `text`, `passed`, and `evidence` fields exactly. Check anchors, sample-detail leakage, unwanted prefaces, and high-confidence residue. Keep naturalness and voice match qualitative.

- [ ] **Step 3: Compare baseline and with-skill outputs blindly**

An independent grader evaluates fidelity, naturalness, restraint, voice match, and output-contract compliance without knowing which configuration produced each output.

- [ ] **Step 4: Refactor only from observed failures and rerun affected evaluations**

Do not add speculative rules.

### Task 6: Viewer and final verification

**Files:**
- Create, untracked: `humanize-writing-workspace/iteration-1/benchmark.json`
- Create, untracked: `humanize-writing-workspace/iteration-1/benchmark.md`
- Create, untracked: `humanize-writing-workspace/iteration-1/review.html`

- [ ] **Step 1: Aggregate results**

```bash
cd /Users/bytedance/.agents/skills/skill-creator
python3 -m scripts.aggregate_benchmark /Users/bytedance/Codes/extend/normal-skills/humanize-writing-workspace/iteration-1 --skill-name humanize-writing
```

- [ ] **Step 2: Generate the official static review page**

```bash
python3 /Users/bytedance/.agents/skills/skill-creator/eval-viewer/generate_review.py \
  humanize-writing-workspace/iteration-1 \
  --skill-name humanize-writing \
  --benchmark humanize-writing-workspace/iteration-1/benchmark.json \
  --static humanize-writing-workspace/iteration-1/review.html
```

- [ ] **Step 3: Run final verification**

```bash
python3 -m unittest discover -s humanize-writing/tests -v
python3 -m unittest discover -s download-xhs-images/tests -v
python3 humanize-writing/scripts/audit_ai_flavor.py --help
git diff --check
git status --short
```

- [ ] **Step 4: Inspect final package**

Confirm `SKILL.md` is under 500 lines, all references resolve one level deep, no source text was copied verbatim, and no detector-evasion or authorship-classification claim remains.

- [ ] **Step 5: Commit evaluation-driven refinements if tracked files changed**

```bash
git add humanize-writing
git commit -m "refactor: tighten humanize writing fidelity gates"
```
