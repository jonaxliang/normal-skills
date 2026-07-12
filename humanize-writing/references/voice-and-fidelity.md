# Voice and Fidelity

Fidelity is the hard constraint; voice matching operates inside it. Use this reference for every task, including audit-only work and text that may need no edits.

## Separate target and sample

Label the roles internally before editing:

- **Target:** the only prose whose content may be rewritten.
- **Sample:** evidence about style, never a source of content.
- **Instruction:** the requested outcome, mode, and explicit preservation rules.

If the roles are unclear, ask which passage is the target and which is the sample. Never copy sample facts, claims, opinions, or experiences into the target. Do not imply a sample event happened in the target context, and do not borrow its names, numbers, examples, jokes, emotions, or conclusions.

When no sample exists, infer register only from the target and request. Do not manufacture a personality to make the prose seem more human.

## Exact protected spans

Create an internal list of strings that must survive byte-for-byte when practical:

- user-designated exact wording;
- code, inline code, and fenced code;
- commands and flags;
- raw URLs and Markdown link targets;
- identifiers, API names, versions, and literals;
- citations and direct quotations.

Protect the span, not merely its meaning. Do not normalize punctuation, capitalization, spacing, or quoting inside an exact span. Surrounding prose may change if its relationship to the span remains correct. If the user explicitly authorizes changing a normally protected category, narrow that permission to the named material.

## Semantic anchors

Build a compact internal ledger before rewriting. Record both the value and its logical role.

| Anchor | What must remain stable |
| --- | --- |
| Claims and actions | Who did what; state versus proposal; completion versus intention |
| Numbers and dates | Value, unit, range, approximation, and associated entity |
| Named entities | Identity, role, and relationships |
| Polarity and negation | `not`, `never`, exclusions, absence, and prohibited actions |
| Comparisons | Direction, baseline, degree, and whether equality is claimed |
| Causality | Cause, effect, and whether the relation is asserted or only correlated |
| Conditions and scope | `only if`, `unless`, audience, time window, and exceptions |
| Uncertainty | Hedges, confidence, attribution, possibility, and unresolved status |
| Domain terms | Defined language, regulatory wording, and conventional labels |
| Opinions and evaluations | Stance, intensity, approval or disapproval, and brand or promotional position |

Opinions and evaluative claims are semantic anchors. Humanization alone does not authorize deleting, weakening, or strengthening the target's opinions, evaluative claims, brand stance, or promotional stance. Do not turn a possibility into a fact, a planned action into a completed one, correlation into causation, or a narrow condition into a general rule.

## Voice contract

Derive a short internal contract from the target and any supplied sample:

- **Rhythm:** typical sentence length, fragment tolerance, and cadence.
- **Vocabulary:** plain versus specialist wording, abstraction level, and preferred verbs.
- **Punctuation:** commas, semicolons, dashes, parentheses, and quotation style.
- **Paragraph openings:** direct claims, time markers, scene setting, or connective starts.
- **Connectors:** explicit transitions versus adjacency; formal versus conversational links.
- **Explicitness:** how much reasoning is stated rather than implied.
- **Person:** first-person singular or plural, impersonal voice, and distance from the reader.

Match tendencies, not quirks. Preserve a coherent document-level register in mixed-language text. Do not insert slang, humor, intimacy, or emotion merely because the sample contains them; use only style features compatible with the target's purpose and supplied content.

## Minimal-edit rule

Change the smallest unit that solves the diagnosed problem. Only zero-proposition meta language may be deleted automatically.

1. Delete answer-routing or filler only when it carries no proposition or stance.
2. Repair a phrase when the sentence structure works.
3. Repair a sentence when its shell is mechanical or unclear.
4. Rewrite a paragraph only when its organization, repeated syntax, or logic is the problem.
5. Leave natural, specific prose unchanged.

Delete or tone down evaluation only with explicit user authorization. If a passage cannot be naturalized without changing its stance, preserve it or ask.

Preserve headings, lists, paragraph order, and other formatting unless they cause the issue or the user requests a new structure. Variation is not a goal by itself; legitimate repetition can improve precision.

## Fidelity retry and rollback

Compare the draft with the protected-span list and semantic ledger, anchor by anchor. Check sample-content leakage separately.

When a sentence drifts, use sentence-level rollback: retry once with a narrower edit that explicitly preserves the affected anchors. If the retry fails, restore the affected original sentence. For drift isolated to a non-sentence protected span, restore the original span. Do not keep polishing around an unresolved fidelity error.

Run this comparison after any late cleanup as well. A stylistic improvement made after the first fidelity check can still reverse causality, lose a hedge, or damage an exact string.

## High-stakes conservative mode

For medical, legal, financial, policy, security, technical operational, and academic prose:

- prioritize terminology, scope, and traceability over conversational ease;
- retain hedges, qualifications, exceptions, and attribution;
- retain conventional passive voice when the actor is unknown, protected, disputed, or intentionally backgrounded;
- avoid replacing precise domain language with approachable but broader synonyms;
- make local edits unless the user explicitly requests structural rewriting.

Natural high-stakes prose can remain formal. Formality alone is not an AI-shaped signal.

When goals conflict, use this priority: meaning and protected content > domain register > supplied voice > naturalness > cosmetic variation.

## Final checklist

- [ ] Only the target was changed; sample content did not migrate.
- [ ] Every exact protected span is present and unchanged.
- [ ] Claims, actors, numbers, dates, entities, and domain terms still match.
- [ ] Negation, comparison, causality, conditions, scope, and uncertainty still match.
- [ ] Opinions, evaluative intensity, brand stance, and promotional stance remain unchanged unless explicit authorization scoped a change.
- [ ] Register and first-person stance are consistent across the document.
- [ ] Headings, links, citations, quotations, and formatting remain intact unless authorized.
- [ ] No fact, example, experience, opinion, emotion, joke, slang, quote, or citation was invented.
- [ ] No generic claim replaced a deleted generic claim.
- [ ] Every automatic deletion was zero-proposition meta language; authorized evaluation changes stayed within scope.
- [ ] Already-natural passages were left alone.
- [ ] Any unresolved drift was rolled back to the original span.
