# Changelog

## The arc

<!--
"The arc" summary:
- 3-6 sentences telling the story of how the solution evolved.
- Start state -> key turning points -> end state.
- Written last, updated as the project progresses. This is the narrative a judge reads first.
-->

_(placeholder — fill in the arc of the project here once there are real entries below)_

---

<!--
ENTRY FORMAT (delete the EXAMPLE entry below once you have real ones):
Each entry is one meaningful change. Newest at the top, under "The arc".
Every entry must be evidence-based: no "felt faster", only measured results.
-->

## [EXAMPLE — DELETE THIS ENTRY] v0.1 — "First real prompt for the extraction step"

**What I saw (evidence):**
Baseline scored 4/10 on the eval harness. Reviewing the 6 failures, 5 of them
were the model returning prose instead of JSON, and 1 was a wrong field value.
Logs: `evals/results/2026-08-27T09-12-00.json`.

**What I changed:**
Rewrote `solution/prompts/extract.md` to (a) show two few-shot examples of the
exact JSON shape, and (b) add a "respond with JSON only, no preamble" system line.
No code changes.

**What happened (measured result):**
Eval harness went from 4/10 to 8/10. All 5 prose-instead-of-JSON failures now
pass. The 1 wrong-field-value case still fails. Runtime unchanged (~45s for 10
cases). Results: `evals/results/2026-08-27T11-40-00.json`.

**Next question:**
Is the remaining failure a prompt problem or a genuinely ambiguous test case?
Need to look at whether a human would agree on the expected value.
