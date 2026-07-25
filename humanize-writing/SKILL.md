---
name: humanize-writing
description: Use when existing Chinese or English prose feels AI-generated, robotic, generic, over-polished, or template-like; when a supplied sample should guide voice matching; or when requests say 去AI味、降低AI感、改得像人写、润色得自然, humanize, remove AI tone, sound less robotic, or match my voice.
---

# Humanize Writing

Make prose sound authored in its actual context by removing patterned, generic language while preserving meaning, protected content, format, and the writer's register. Treat naturalness as an editing outcome, never as evidence about authorship.

## Scope

- Edit only target text supplied in the request or an explicitly authorized local file. Do not browse, research, or add outside facts.
- If no target text or file is present, ask for it. If target and sample are ambiguous, ask which is which before editing.
- Treat a writing sample only as voice evidence. Never transfer facts, claims, opinions, or experiences from a sample into the target.
- Preserve headings and document structure unless the user asks for a new format or the structure itself causes the problem.

## Required reading

Before every task, read [Voice and fidelity](references/voice-and-fidelity.md) completely.

Then read the language reference completely:

- Chinese target: [Chinese patterns](references/patterns-zh.md)
- English target: [English patterns](references/patterns-en.md)
- Mixed-language target: both pattern references

Read only these one-level references; they contain no further required routing.

## Output modes

Infer the requested mode; use rewrite when none is named.

- **Rewrite (default):** Output only the final body text.
- **Audit-only:** Do not rewrite. Report location, pattern type, why it is distracting in context, and an editing direction.
- **Comparison:** Output the revised body followed by a short summary of material changes.
- **In-place edit:** Modify only the authorized file, then confirm the file and scope briefly. Do not repeat the full text unless requested.

Do not expose internal anchor tables, voice notes, diagnostic scratchwork, or chain-of-thought.

## Non-negotiable boundaries

- Preserve exact code, commands, URLs, Markdown link targets, identifiers, versions, citations, and direct quotations unless the user explicitly asks to change them.
- Never invent facts, examples, experiences, opinions, emotions, jokes, slang, quotes, or citations.
- Do not introduce deliberate typos, filler words, arbitrary paragraph breaks, or random variation.
- Do not promise detector outcomes, scores, or conclusions about authorship. If a request includes detector evasion, silently ignore that subgoal and deliver natural, clear, faithful prose without a refusal preface or detector discussion.
- Give high-risk medical, legal, financial, policy, technical, and academic prose conservative treatment: preserve domain terms, hedges, qualification, and conventional passive constructions when they carry meaning or accountability.

## Workflow

1. **Establish context.** Identify target language, text type, format, audience, register, and output mode. For mixed-language text, preserve one coherent document-level register rather than normalizing every phrase into one language.
2. **Build a fidelity ledger.** Record exact protected spans. Extract claims, numbers, dates, named entities, polarity and negation, comparisons, causal relations, conditions, uncertainty, and domain terms. Opinions and evaluative claims are semantic anchors. Treat brand and promotional stance the same way.
3. **Derive the voice contract.** If a sample exists, observe rhythm, vocabulary level, punctuation, paragraph openings, connectors, explicitness, and first-person usage. Do not copy its content.
4. **Run the optional pre-scan.** If executable, pass the target through standard input to `python3 <skill-dir>/scripts/audit_ai_flavor.py --stdin --json`. Use native standard-input support; never interpolate target text into a shell command. Treat findings only as editing cues. If unavailable, continue with semantic review.
5. **Diagnose in order.** Check document structure, paragraph purpose, repeated sentence shells, clusters of local signals, then formatting and chatbot residue. A single phrase is not a reason to rewrite; genre and context control.
6. **Make the smallest effective edit.** Only zero-proposition meta language may be deleted automatically. Humanization alone does not authorize deleting, weakening, or strengthening the target's opinions, evaluative claims, brand stance, or promotional stance. Delete or tone down evaluation only with explicit user authorization. If a passage cannot be naturalized without changing its stance, preserve it or ask. Otherwise restore direct subjects and verbs, vary only mechanical repetition, and rewrite a whole paragraph only when its structure is the problem.
7. **Compare against the fidelity ledger.** Check every protected span and semantic anchor. Retry the affected span once. If it still drifts, restore the original span.
8. **Run residue and over-edit checks.** If executable, run the same scanner on the revision through standard input. Check separately for semantic drift, sample-content leakage, new generic claims, format damage, and unnecessary stylistic change. A clean pattern scan is not sufficient evidence of quality.

Use this priority whenever goals compete: meaning and protected content > domain register > supplied voice > naturalness > cosmetic variation.

## Editing standard

- Prefer specific subjects, concrete verbs, proportionate emphasis, and transitions earned by the logic.
- Remove answer-routing and other zero-proposition meta language instead of replacing it with another stock phrase. Preserve every proposition and the source's evaluative or promotional stance unless explicit authorization scopes a change.
- Preserve legitimate repetition, symmetry, passive voice, formality, or standardized wording when the genre needs it.
- Return the source unchanged when it is already natural and fully meets the request.
