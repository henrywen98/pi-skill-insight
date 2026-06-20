# Skill Usage Insight — Sample Report

> ⚠️ **This is a synthetic, illustrative sample.** Every number, skill name, and quote
> below is fabricated to show the *shape* of the report `pi-skill-insight` produces — it is
> **not** real usage data. The live tool generates this from your own `~/.claude` transcripts
> and writes it (in Chinese by default) to `skill-log/skill_usage_report_<date>.md`.
> To emit English instead, change the "写入…（中文 Markdown）" line in the prompt inside
> `run_skill_insight.sh`.

**Window:** 2026-05-12 → 2026-05-26 (14 days) · **Sessions scanned:** 312 · **Skill calls:** 187 · **Skills installed:** 24

---

## ① Scorecard

> **A** = clean success · **B** = light correction (1–2 msgs) · **C** = heavy correction (≥3 msgs / step-by-step) · **D** = failure (interrupted / user took over)
> **Intervention rate** = (B+C+D) / calls · **Failure rate** = D / calls

| Skill | Calls | A | B | C | D | Intervention | Failure | Trigger |
|---|--:|--:|--:|--:|--:|--:|--:|---|
| `brainstorming` | 28 | 10 | 9 | 6 | 3 | **64%** | 11% | auto |
| `docx` | 22 | 18 | 3 | 1 | 0 | 18% | 0% | auto |
| `commit` | 19 | 17 | 2 | 0 | 0 | 11% | 0% | slash |
| `pdf-extract` | 14 | 6 | 4 | 3 | 1 | 57% | 7% | auto |
| `web-research` | 11 | 9 | 1 | 1 | 0 | 18% | 0% | mixed |
| *(19 more…)* | 93 | — | — | — | — | — | — | — |
| **Total** | **187** | 110 | 41 | 23 | 13 | **41%** | 7% | — |

**Headlines**
- **Overall 41% intervention** — roughly 4 in 10 skill runs needed rework. New baseline metric; track the trend.
- **Worst offender:** `brainstorming` (64% intervention) — fires on tiny UI tweaks that don't need a framework.
- **Cleanest:** `docx` (18%, 0 failures) — does what it says.
- **3 zero-call skills** this window despite relevant tasks → likely a *triggering* (description) problem, not a content problem (see §5).

---

## ② Per-skill intervention analysis

### `brainstorming` — 28 calls · 64% intervention · 11% failure 🔴

**Pattern:** 6 of 18 interventions are the user immediately saying some variant of *"this is a tiny change, skip the process."* The skill's HARD-GATE pulls full-framework brainstorming into one-line UI edits.

**Evidence (fabricated):**
> "no need to brainstorm, just move the button 4px"
> "this is literally a copy change, why are we exploring requirements"
> "stop — just do it, it's one line"

**Root cause:** `description` says *"…or modifying behavior"* — too broad; every edit "modifies behavior."

**Suggested `SKILL.md` rewrite (copy-paste):**
> Add a **negative-trigger** block: *"Do NOT use for: cosmetic/UI tweaks, copy edits, single-line changes, or mechanical refactors where the requirement is already unambiguous. Brainstorming is for open-ended work where the design space is genuinely wide."* Frame it as a principle with the *why* (Claude under-distinguishes scope), not a list of banned keywords.

### `pdf-extract` — 14 calls · 57% intervention 🟠

**Pattern:** High-variance. Clean on text PDFs; collapses on scanned/image PDFs — user re-runs or hand-feeds OCR.

**Evidence (fabricated):**
> "it's a scanned doc, you need OCR first"
> "the tables came out scrambled, try again page by page"

**Suggested fix:** Declare the boundary in `SKILL.md`: detect scanned PDFs up front and route through OCR; state the page-by-page fallback for tables. This is a *content* fix, not triggering.

---

## ③ Baseline & analyst findings

- **Systemic (not any one skill):** ~30% of *all* skill calls are followed by "use the non-default port / avoid 3000" — this is a global preference, not a skill defect. Belongs in `CLAUDE.md`, don't penalize individual skills.
- **High variance:** `pdf-extract` swings A↔D purely by input type → the skill should *declare its applicable boundary*.
- **Repeated manual labor:** across 5 sessions the agent re-wrote near-identical "convert csv → formatted xlsx" helper code → candidate to harden into a bundled `scripts/` helper for the spreadsheet skill.

---

## ④ Previous-cycle follow-up

| Last cycle's suggestion | Adopted? | Effect |
|---|---|---|
| `brainstorming`: add negative triggers | ❌ not adopted | intervention 61% → 64% (still rising) — **re-prioritize** |
| `commit`: stop re-confirming staged files | ✅ adopted | intervention 19% → 11% ✅ worked |
| `web-research`: cite sources inline | ✅ adopted | failure 9% → 0% ✅ worked |

*Closing the loop is the whole point: 2 of 3 adopted fixes measurably helped; the un-adopted one is now the top P0.*

---

## ⑤ This cycle's suggestions

**P0 — execution (rewrite `SKILL.md`)**
1. `brainstorming`: add the negative-trigger block above. *Evidence: 6 cross-session scope complaints.*
2. `pdf-extract`: declare scanned-PDF / table boundary + OCR routing.

**P1 — triggering (rewrite `description`)**
3. `changelog-writer` (0 calls, but 4 relevant sessions): description lacks the words users actually say ("release notes", "what changed"). Add them + enumerate trigger contexts.

**P2**
4. Promote the recurring csv→xlsx helper into the spreadsheet skill's `scripts/`.

---

## ⑥ Appendix — trigger eval set

Real user phrasings, for feeding back into description optimization. (Quotes fabricated here.)

```json
[
  {"skill": "brainstorming", "query": "just move the button 4px", "should_trigger": false},
  {"skill": "brainstorming", "query": "let's design the whole onboarding flow", "should_trigger": true},
  {"skill": "changelog-writer", "query": "write release notes for v2.1", "should_trigger": true},
  {"skill": "changelog-writer", "query": "what changed since last week", "should_trigger": true},
  {"skill": "pdf-extract", "query": "pull the tables out of this scanned report", "should_trigger": true}
]
```
