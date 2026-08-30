# AI Incident Investigator

Give it the logs, the deploy record, the metrics, and the diff from a production
incident; it tells you the root cause and quotes the lines that prove it.

## Problem statement

**Who.** The on-call engineer who just got paged — someone facing an active outage, a rapidly filling Slack channel, and an incident commander asking what changed.

**The bottleneck.** The evidence exists, but it is scattered across five places
that do not talk to each other: an application log, an error log, a deployment
record, a metrics dashboard, and the diff that shipped. Finding the cause means
holding all five in your head at once and correlating *across* them — noticing
that a value in the deploy record is in the wrong relationship to a value in a
config file nobody touched. That is the expensive part, and it is exactly the
part that degrades under time pressure.

**What it costs.** Two things, and the second is worse. The obvious cost is
minutes of downtime while someone reads. The real cost is a *confident wrong
answer*: a release that ships several changes at once offers a loud, suspicious
candidate and a quiet, connected one, and the loud one gets blamed. Then the
rollback doesn't fix it, and you have burned the outage window on the wrong
theory. Two of the six incidents in this repo's eval set are built exactly this
way — a real cause shipped alongside a plausible red herring (an `httpx`
migration; a Kafka consumer-group rename).

**Why this is tractable now.** The correlation work is reading and
cross-referencing bounded text — what a language model is good at. The risk is
that it is also good at producing a fluent wrong answer, which is why this
project is built around an eval harness rather than a demo.

## Solution overview

A baseline and a structured alternative, measured against each other on six
synthetic incidents with known causes.

**Baseline** (`baseline/run_baseline.py`, frozen): concatenate all five
artifacts into one prompt, one API call, ask for the root cause. This is what
most people would build first.

**Solution** (`solution/agent.py`): four stages, seven API calls per incident.

1. **Extract** (`solution/prompts/extract.md`) — one call per artifact, all five
   run concurrently. Each returns structured `facts[]`, where every fact carries
   the source `line` copied verbatim, a `kind` (`error`, `config_change`,
   `metric`, `unchanged`, ...), a timestamp, and the entities named. It is told
   not to diagnose — including facts about what did *not* change, which is how
   the later stages rule candidates out.
2. **Hypothesize** (`solution/prompts/hypothesize.md`) — one call over all
   facts. Returns a `root_cause`, the `mechanism` as an explicit causal chain,
   `supporting_facts`, `ruled_out` candidates with the fact that eliminates
   each, a `confidence`, and `what_would_disprove_this`.
3. **Verify** (`solution/prompts/verify.md`) — one call against the *raw*
   artifacts, not the summary of them. Checks that every cited line exists
   verbatim, and enumerates `cross_file_value_checks`: every timeout, limit,
   pool size, TTL, and threshold across all five files, each pair that governs
   the same resource marked `relationship_ok` true or false. It can `confirm`,
   `revise`, or `reject`; a rejection re-runs hypothesize once with the
   critique.
4. **Report** — same JSON shape as the baseline (`root_cause`, `evidence`,
   `confidence`) so the comparison is fair, with the stage detail preserved in
   `_meta`.

**Grading.** Evidence lines in the test artifacts carry inline
`# EVIDENCE: <tag>` markers, so evidence scoring is exact tag matching with no
model's opinion in it. Red herrings carry `# NOISE:` and are counted separately.
Root-cause correctness is judged by a pinned small model
(`claude-haiku-4-5`) that sees only the two strings and records its reason.

**What the evidence shows** (full runs, `claude-sonnet-4-6`, results in
`evals/results/`):

| metric | baseline | solution |
| --- | --- | --- |
| root-cause pass rate [^1] | 3/5 (60%) | 3/3 scoreable (of 4 attempted) |
| evidence recall | 83%, then 78% on rerun | 92% |
| evidence precision | 100% | 100% |
| red herrings cited | 0/2 | 0/2 |
| avg time per incident | ~10.9s | ~126s |
| API calls per incident | 1 | 7 |
| cost per 6-incident run | ~$0.11 | ~$1.81 |

[^1]: Repeated runs of incident_03 alone — the one case where the baseline is
unstable. Every other row is from full six-incident runs.

The honest reading: **the evidence-citation gap is real and the root-cause
accuracy gap is not established.** Running incident_03 repeatedly puts the
baseline at 3/5 and the solution at 3/3 scoreable, but the exact 95% intervals
are roughly [15%, 95%] and [29%, 100%] — overlapping across nearly their whole
range, so the difference is not distinguishable at this sample size. Three
successes cannot separate a better approach from a lucky draw against a
coin-flip baseline. (The solution's fourth run died on an API credit error and
its fifth left no result file; `CHANGELOG.md` v0.6 has the per-run detail and
the full stats.)

Runs are not deterministic, and we never pinned `temperature` to make them so —
nor could we have with the pinned `anthropic==1.2.0`, whose `messages.create()`
has no `temperature` parameter at all (passing one raises a client-side
`TypeError`), and the Messages API exposes no seed. Repeated runs with a
reported rate are the only available answer.

What holds across runs is that the staged pipeline cites more of the right lines
(92% vs. 78–83% recall at equal 100% precision), at roughly 12x the latency and
16x the cost.

**Out of scope for this build.** No live data sources — incidents are static
files, not queries against a real logging stack. No remediation, only diagnosis.
No multi-incident correlation or alert triage. No UI. Six synthetic incidents,
each with exactly one findable cause, which is a friendlier world than
production.

## Coding agents used (disclosure)

**Claude Code** powered by Opus 5 as the driving model; the pipeline under test runs claude-sonnet-4-6 with claude-haiku-4-5 as the grader.

Trajectories:

- `trajectories/solution-agent/incident_01.md` … `incident_06.md` — full
  rendered transcripts of the solution agent's four stages for all six
  incidents: every prompt sent, every response, every retry. Raw `.jsonl` is
  gitignored per `CLAUDE.md`; the rendered Markdown is committed.
- `trajectories/coding-agent/full-session.md` — the Claude Code session that built the repo.

## How to run

See **[REPRODUCE.md](REPRODUCE.md)** for setup from a clean clone, every
command, expected output, and measured runtime and cost.

Fastest check that a clone is intact, costing nothing:

```bash
python evals/run_eval.py --dry-run     # validates all 6 incidents, no API calls
```

## Hot take / insights

**Single-run pass/fail accuracy is a vanity metric; evidence recall is the number worth trusting.**
Benchmarking an LLM agent once and calling it "100% correct" is mostly
sampling noise, not a validated result. Our baseline scored 5/6 root causes
on one run and 6/6 on an identical rerun — no code, prompt, or model changed
between them. That single flip would have quietly reversed our headline if
we hadn't caught it.
Evidence recall moved too (baseline: 78–83% across the two runs) — so recall
isn't noise-free either. But even at baseline's *best* showing, it never
touched the solution's 92%, at the same 100% precision. That's the real
story: an SRE doesn't need the model to get lucky on a root-cause guess, they
need to know *why* it's right, with citations they can check in seconds — like
surfacing the exact mismatch between a 30s gateway timeout and a 10s ALB
timeout in incident_03. That evidence trail is what a multi-stage agent
actually buys you, and it holds up regardless of which run you happened to catch.
