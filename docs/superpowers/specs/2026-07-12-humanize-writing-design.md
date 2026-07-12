# Humanize writing skill design

## Problem

AI-assisted Chinese and English drafts often preserve the right topic while flattening authorship: they use generic framing, symmetrical structure, canned transitions, inflated significance, and a voice that fits no particular writer. Surface-level synonym replacement does not solve this and can change facts or tone.

The new skill should edit user-supplied prose so it reads naturally while preserving the source text's meaning, claims, register, and formatting. When the user supplies writing samples, the skill should use them only as style evidence.

## Goals

- Rewrite existing Chinese and English prose to reduce AI-like, robotic, generic, over-polished, or template-shaped writing.
- Preserve facts, numbers, names, quotations, polarity, causality, conditions, uncertainty, domain terminology, and intended register.
- Match a supplied writing sample's rhythm, vocabulary level, punctuation, paragraph habits, and use of first person without copying its subject matter.
- Prefer the smallest effective edit and leave already-natural text mostly unchanged.
- Support rewrite, audit-only, comparison, and in-place file-editing requests.
- Provide a deterministic, dependency-free audit script for high-confidence surface patterns.

## Non-goals

- Do not browse, research, fact-check, or supplement the user's material.
- Do not invent facts, examples, experiences, opinions, emotions, jokes, slang, quotations, or citations.
- Do not add deliberate mistakes or optimize for an AI detector, perplexity target, burstiness threshold, or fabricated probability score.
- Do not treat a detected phrase as proof that text was AI-generated.
- Do not generate a new article from a bare topic; the skill requires target text.

## Package structure

```text
humanize-writing/
├── SKILL.md
├── agents/openai.yaml
├── references/
│   ├── patterns-zh.md
│   ├── patterns-en.md
│   └── voice-and-fidelity.md
├── scripts/
│   └── audit_ai_flavor.py
├── tests/
│   └── test_audit.py
└── evals/
    └── evals.json
```

`SKILL.md` contains the operating contract and points directly to one-level-deep references. The language references hold contextual pattern catalogs and false-positive guards. `voice-and-fidelity.md` defines sample-based voice calibration, protected spans, semantic anchors, and the final fidelity audit. The script only reports high-confidence surface patterns; the model performs semantic editing.

## Triggering and inputs

The skill should trigger when a user asks to revise existing Chinese or English text that sounds AI-generated, robotic, generic, overly polished, templated, or unlike a supplied sample. Trigger phrases include 去 AI 味、降低 AI 感、改得像人写、润色得自然、humanize text、remove AI tone, sound less robotic, and match my voice.

Inputs may be pasted text or a user-authorized local file. Optional style samples must be clearly distinguished from the target. If no target text can be identified, ask for it. If no style sample is supplied, preserve the target's existing register instead of inventing a new persona.

## Modes and output

- **Rewrite, default:** return only the final revised text.
- **Audit-only:** report location, pattern, explanation, and suggested editing direction without rewriting.
- **Comparison:** return the revised text followed by a concise list of material changes.
- **In-place edit:** modify only the requested file or span and confirm completion briefly.

Do not expose private scratch work, semantic-anchor notes, or chain-of-thought. If the text already reads naturally, make few or no changes.

## Editing workflow

1. Identify the target, samples, language or mixed-language segments, document type, audience clues, register, formatting, and requested mode.
2. Fence protected spans: code, commands, URLs, Markdown link targets, identifiers, version strings, citations, and direct quotations unless the user asks to edit them.
3. Extract semantic anchors from the target: claims, numbers, dates, named entities, polarity, negation, comparison, causality, conditions, uncertainty, and domain terms.
4. If samples exist, derive an internal voice contract from sentence rhythm, vocabulary level, punctuation, paragraph openings, connector habits, explicitness, and first-person use. Never import sample facts or opinions.
5. Diagnose in order: document structure, paragraph function, discourse and sentence shells, clustered vocabulary, formatting, and chatbot residue.
6. Apply the smallest stable edit. Rewrite a whole paragraph only when its structure causes the AI flavor. Keep useful lists, contrasts, sequence markers, hedges, and technical passives when the genre needs them.
7. Compare the revision against protected spans and semantic anchors. On drift, retry the affected sentence once with explicit preservation constraints. If it still drifts, restore the original span.
8. Run a residue and over-editing pass. Remove only remaining contextual problems; do not force variation or personality unsupported by the source.
9. Return the mode-specific output.

## Pattern model

Both language catalogs organize patterns by function rather than a flat banned-word list:

- chatbot residue and response framing;
- generic openings, route markers, and summary conclusions;
- significance inflation, promotional gloss, and vague attribution;
- false concessions, binary-correction shells, mechanical sequencing, and forced rules of three;
- over-symmetrical paragraphs, decorative lists, fake engagement questions, and formatting residue;
- nominalization, passive inflation, translation-shaped syntax, and clustered AI-associated vocabulary;
- emotional claims, opinions, or intimacy that the supplied material has not earned.

Each entry includes signals, why the pattern is risky, false-positive guards, and rewrite tactics. A single common word is not enough to justify an edit.

## Audit script

`audit_ai_flavor.py` is a standard-library Python CLI accepting a file path or stdin. It supports human-readable and JSON output. It masks fenced code, inline code, URLs, and Markdown link targets before scanning.

The script reports high-confidence phrase and structure matches with stable IDs, language, category, line number, matched text, and explanation. It does not rewrite, call external services, calculate an authorship probability, or claim that a document is AI-generated. Exit status is zero for a completed scan even when findings exist; invalid input or arguments return a non-zero status.

## Error handling and conservative cases

- Missing target: request the text or file.
- Ambiguous target versus sample: ask the user to label them.
- Mixed Chinese and English: apply the corresponding catalog per segment while keeping one document-level register.
- Academic, technical, legal, medical, policy, or financial prose: preserve terminology, hedging, passive constructions, and structure when they carry meaning or convention.
- Conflicting goals: fidelity outranks naturalness; naturalness outranks cosmetic variation.
- Scanner unavailable: continue with semantic review and state the limitation only if it affects the requested output.

## Evaluation plan

Use baseline-first evaluation before writing the skill body, then repeat the same scenarios with the skill:

1. Chinese public-writing draft with template contrasts and inflated summary; all facts must survive.
2. English product explanation with grandiosity, signposting, and rule-of-three padding.
3. Chinese target plus personal sample; voice should move toward the sample without importing its facts or phrases.
4. Technical mixed-content text containing numbers, negation, causality, code, links, and quotations; protected content must remain exact.
5. Already-natural prose; the correct behavior is minimal or no editing.

Deterministic tests cover masking, Chinese and English high-confidence detections, JSON schema, line numbers, clean-text false positives, stdin and file input, and invalid arguments. Qualitative evaluation compares fidelity, naturalness, voice match, restraint, and output-contract compliance.

## Design provenance

The design synthesizes preservation-first and structure-first ideas from the MIT-licensed projects `B1lli/remove-ai-flavor-writing-skill`, `lynote-ai/humanize-text-skill`, `devswha/patina`, `thekozugroup/humanizer`, and `lynote-ai/best-humanizer-handbook`. It deliberately excludes detector-evasion promises, compulsory opinion injection, invented specificity, and rigid stylometric thresholds.
