# Changelog

## The arc

We began by establishing a frozen 1-call baseline concatenating all incident artifacts, alongside a 6-incident evaluation harness graded by a pinned LLM judge (`claude-haiku-4-5`). To improve extraction depth and auditability, we built a 4-stage multi-agent pipeline (`Extract` -> `Hypothesize` -> `Verify` -> `Report`), parallelizing extraction to cut execution latency by 35%. Early runs suggested a root-cause accuracy lead over the baseline (6/6 vs 5/6), but clean-environment reruns revealed sampling variance in single-pass LLM accuracy (baseline scored 6/6 on rerun). What proved consistently superior was evidence recall the multi-stage agent achieves 92% recall versus 78–83% for the baseline at 100% precision, demonstrating that agentic pipelines excel at rigorous evidence gathering rather than single guess accuracy.

---

## v0.6 — "The baseline flips a coin on incident_03: 3/5"

**What I saw (evidence):**
v0.5 established that the untouched baseline scored 5/6 on one run and 6/6 on
another, and closed by asking how many runs it takes to say anything at all
about root-cause accuracy. This entry answers that with a rate instead of a
verdict. Nine single-incident runs of incident_03, five baseline and four
solution, in `evals/results/2026-08-29T06-31-07` through `...T06-38-48`.

**What I changed:**
Nothing in `baseline/`, `solution/`, `evals/`, or any prompt. This is
measurement only.

**What happened (measured result):**

| target | pass rate | per-run correctness | per-run evidence recall |
| ------ | --------- | ------------------- | ----------------------- |
| baseline | **3/5 (60%)** | False, False, True, True, True | 0.50, 0.50, 1.00, 1.00, 0.75 |
| solution | **3/3 scoreable** | True, True, True | 1.00, 0.75, 1.00 |

**The baseline genuinely flips a coin on incident_03.** Identical input,
identical model, identical prompt, 60% pass. That fully explains the v0.3/v0.4
5/6 and the v0.5 6/6 — they were two draws from the same distribution, not a
change in capability. Evidence recall on the baseline is just as unstable,
swinging 0.50 to 1.00 across the same five runs.

**The solution side is 3 runs, not 5 — say it plainly.** Run 4 died on
`BadRequestError: 400 — Your credit balance is too low to access the Anthropic
API`. Run 5 left no result file and no trajectory write at all (the agent
unlinks and recreates its `.jsonl` as its first action, and
`incident_03.jsonl` still carries run 4's timestamp), so it failed before the
agent began logging. The cause is **not determined** — the run loop filtered
stderr, so the traceback was lost. It is not counted as a pass or a failure.

**What this does and does not establish.** 3/3 against 3/5 is not a
distinguishable difference. Exact binomial 95% intervals are roughly
[29%, 100%] for the solution and [15%, 95%] for the baseline; they overlap
across almost their whole range. Three successes cannot separate a genuinely
better approach from a lucky draw against a coin-flip baseline. What v0.6 does
settle is the *baseline's* instability, which is what v0.5 needed and now has.
The solution's own rate remains unmeasured at any useful precision.

This also retires the idea of fixing variance at the source: `temperature`,
`top_p`, and `top_k` were removed from `messages.create()` in
`anthropic==1.2.0` — passing one is a `TypeError`, not a model-specific 400, so
there is no sampling knob to pin and no seed on the Messages API. Repeated runs
with a reported rate are the only available answer.

**Next question:**
The solution needs the same five-run treatment before any accuracy claim is
defensible — roughly $0.60 of credit for the two missing runs, more if the goal
is a rate tight enough to compare. Worth deciding first whether incident_03 is
even the right case to spend on: it is the only one of the six where the
baseline is unstable, so it is the most informative, but a rate measured on the
hardest case does not generalize to the other five.

---

## v0.5 — "A rerun of the untouched baseline scores 6/6, which puts v0.4's headline in doubt"

**What I saw (evidence):**
This repo had never been run against its own environment — every result so far
was produced with `/Users/ada/micro1_projects/agenteval/.venv`, a different
project's virtualenv. Fixing that meant re-running the baseline to prove the new
environment works. That rerun is the finding.

**What I changed:**
- Created `.venv` inside this repo on **Python 3.10.0** and pinned
  `requirements.txt` to the versions actually in use: `anthropic==1.2.0`,
  `python-dotenv==1.2.3` (transitives `httpx2==2.12.0`, `pydantic==2.13.5`
  recorded as comments).
- Replaced the `REPRODUCE.md` placeholder with the real setup, commands,
  expected output, measured runtime, and measured cost.
- **No change to `baseline/`, `solution/`, `evals/`, or any prompt.** The code
  that produced the run below is byte-identical to v0.4's.

**What happened (measured result):**
The full baseline, unchanged, run under the new environment
(`evals/results/2026-08-29T04-36-10_baseline.json`) scored **6/6**, not the 5/6
in `evals/results/2026-08-29T01-51-01_baseline.json`. The incident that moved is
**incident_03** — the exact case v0.4 used as its headline evidence. This time
the baseline got it right, and the judge's reason shows it found the same pair
the solution's verify stage does:

> Both identify the same underlying cause: PR #881's timeout mismatch (30s
> gateway vs 10s load balancer) combined with disabled circuit breaker allowed
> gateway threads to block longer than the load balancer would wait...

| baseline run | root cause | evidence recall |
| ------------ | ---------- | --------------- |
| 2026-08-29T01:51:01 (v0.3/v0.4) | 5/6 | 83% |
| 2026-08-29T04:36:10 (this) | **6/6** | **78%** |

Per-incident, correctness moved only on incident_03 (False -> True), but
evidence recall moved on three others (02: 1.00 -> 0.67, 05: 1.00 -> 0.75, 03:
0.50 -> 0.75). So the variance is not confined to one borderline case.

**What this means for v0.4.** v0.4's headline was "solution 6/6 vs baseline
5/6", built on incident_03 being a baseline failure the pipeline fixed. On this
evidence that gap is **not established**: the baseline scores 6/6 too when
re-run, and n=1 per configuration cannot separate a real improvement from
sampling noise. What v0.4 measured that still stands is the evidence recall gap
(baseline 78-83% across two runs vs. solution 92%) and the latency/cost profile
(1 call and ~10.6s vs. 7 calls and ~126s). The root-cause accuracy claim does
not stand on one run per side, and the v0.4 entry should be read with this one.

**Next question:**
How many runs are needed to say anything about root-cause accuracy at all? With
6 incidents and a binary outcome per incident, single runs cannot distinguish
5/6 from 6/6. The cheap experiment is 5 baseline runs and 5 solution runs on
incident_03 alone (~$0.10 and ~$1.50 respectively) to get a pass rate per
approach rather than a single verdict. Until that exists, the defensible claim
for this project is the evidence-recall and citation-quality gap, not "the
pipeline is more accurate."

---

## v0.4 — "6/6, and an honest look at what actually moved"

**What I saw (evidence):**
v0.3 left two questions: does forcing cross-file value comparison in the verify
stage fix incident_03 without a fifth stage, and does parallelizing the extract
calls recover the latency. Both were changed and the full set rerun.
Baseline (unchanged, v0.3 run): `evals/results/2026-08-29T01-51-01_baseline.json`.
Solution: `evals/results/2026-08-29T03-42-23_solution.json`.

**What I changed:**
- `solution/prompts/verify.md`: the verify stage must now enumerate every
  config value, timeout, limit, threshold, pool size, capacity, rate, TTL, and
  interval across all five artifacts — explicitly including values the deploy
  record marks *unchanged* — and check each pair that governs the same request
  path or resource for the correct relationship to the other. A new required
  output field, `cross_file_value_checks`, makes that work visible rather than
  implied, and it is stored in `_meta` so it is auditable.
- `solution/agent.py`: the 5 extract calls now run concurrently in a
  `ThreadPoolExecutor` — they are independent, one per file. Hypothesize and
  verify stay sequential; each depends on the prior stage. Each parallel call
  logs into its own `_BufferedLog` which replays into the real
  `TrajectoryLogger` in file order after all five finish, so trajectories stay
  readable and deterministic while timestamps still show the calls overlapping.
  Buffers replay even when a call fails, so a failed run still leaves a usable
  transcript. API-call and token counters are now lock-guarded.

**What happened (measured result):**

| metric              | baseline | solution |
| ------------------- | -------- | -------- |
| correct root cause  | 5/6      | **6/6**  |
| evidence recall     | 83%      | 92%      |
| evidence precision  | 100%     | 100%     |
| red herrings cited  | 0/2      | 0/2      |
| avg time            | 10.9s    | 126.2s   |
| avg api calls       | 1.0      | 7.0      |

**The headline: the solution now scores 6/6 against the baseline's 5/6.**
incident_03 — the timeout misconfiguration that both approaches failed in v0.3
— flipped from **193.4s / False** to **122.1s / True**, with evidence recall on
that incident going 0.75 -> 1.00 (it now cites `edge_timeout_unchanged`, the
line it previously buried in `red_herrings`). The pair that closed the gap
appears in its `cross_file_value_checks`:

> `PAYMENTS_READ_TIMEOUT 30s (deployment.txt / recent_changes.diff)` vs.
> `alb-prod proxy_read_timeout = 10s (deployment.txt / recent_changes.diff)` —
> `relationship_ok: false` — "ALB abandons the client-facing request after 10s
> and returns 504, but the gateway worker thread stays blocked on
> PaymentsClient.execute() for up to 30s."

**Latency: parallelizing extract cut average wall clock 35%**, 195.6s -> 126.2s,
with every one of the six incidents faster (incident_03 itself 36.9%). The
extract stage now costs a mean of 35.0s — one call deep instead of five — which
also sets the ceiling on this approach: **hypothesize and verify are now the
time floor.** They are inherently sequential, and the verify prompt got longer
in this same change. Roughly 90s of the remaining 126s is the two sequential
stages; no further parallelism is available without changing the architecture.

**Caveat 1 — the recall gain is a wash against v0.3, not a net improvement.**
Aggregate evidence recall is 91.67% in both v0.3 and v0.4, identical to four
decimal places. What actually happened is a swap: incident_03 gained
(0.75 -> 1.00) and **incident_06 dropped (1.00 -> 0.75)**, losing the
`db_qps_sawtooth` tag while still scoring `correct=True`. The verify change did
not raise recall; it moved a quarter point from one incident to another. The
real recall gain is baseline -> solution (83% -> 92%), and that was already
established in v0.3 — this release did not add to it.

**Caveat 2 — the override mechanism is not confirmed.** In the single-incident
test of this change, incident_03's verify verdict was `revised`, i.e. the
verifier actively overrode the hypothesis. In this full run the same incident
came back `confirmed` with `rehypothesis_rounds: 0` — the right answer, recall
1.00, but no override. What is established is that `cross_file_value_checks` are
now computed and in front of the model when it writes the final `root_cause`;
that the verifier will *reject and rewrite* a hypothesis on the strength of them
rests on one sample and should not be claimed as a stable mechanism.

**Regression check — incidents 02 and 04 held steady.** These were the risk: the
sharpened value-mismatch instruction could pull a non-mismatch incident (the
unbounded dict; the dropped index) toward a spurious pair. Neither moved.
Both remain `correct=True`, `verdict=confirmed`, `rehypothesis_rounds: 0`,
evidence recall 1.00, no red-herring contamination. Each did emit 6
`cross_file_value_checks` with 3 flagged mismatched, but those stayed internal —
they did not override a correct hypothesis or drag the root cause off target.
No verdict anywhere in the set flipped `confirmed` -> `revised`.

**Next question:**
Is incident_06's recall dip noise, or a real trade-off? The verify prompt is now
substantially longer, and the plausible mechanism is that the added value-pair
instruction crowded out the sweep for supporting lines that previously caught
`db_qps_sawtooth`. Rerunning incident_06 alone a few times separates the two: if
it recovers on some runs it is variance, if it consistently drops that tag the
longer prompt is costing recall elsewhere and the verify stage needs the two
concerns separated rather than stacked.

---

## v0.3 — "First full comparison: the pipeline wins on evidence, not on accuracy"

**What I saw (evidence):**
The harness was trustworthy enough to run both targets over all 6 incidents on
`claude-sonnet-4-6`, judged by `claude-haiku-4-5`.
Baseline: `evals/results/2026-08-29T01-51-01_baseline.json`.
Solution: `evals/results/2026-08-29T02-23-41_solution.json`.

| metric              | baseline | solution |
| ------------------- | -------- | -------- |
| correct root cause  | 5/6      | 5/6      |
| evidence recall     | 83%      | 92%      |
| evidence precision  | 100%     | 100%     |
| red herrings cited  | 0/2      | 0/2      |
| avg time            | 10.9s    | 195.6s   |
| avg api calls       | 1.0      | 7.0      |

**What I changed:**
Nothing — this entry is the measurement itself, the first head-to-head run of
the frozen baseline against the 4-stage pipeline.

**What happened (measured result):**
A mixed result, and worth saying plainly: **the pipeline did not improve
root-cause accuracy.** Both targets scored 5/6 and both failed the same
incident. What it bought was a modest evidence recall gain, 83% -> 92%
(incident_03 0.50 -> 0.75, incident_06 0.75 -> 1.00; the other four unchanged),
for 18x the wall-clock time and 7x the API calls. Precision was already 100% for
both, and neither target cited a red-herring line in incidents 02 or 05 — the
red herrings did not separate the two approaches the way the design assumed.

Both failed incident_03 (the timeout misconfiguration) in the same way. The
judge on the baseline:

> The reference root cause identifies a specific timeout mismatch between edge
> LB (10s) and gateway (30s) as the mechanism, while the candidate omits this
> critical detail and instead attributes the issue solely to the longer timeout
> without explaining why the edge LB's shorter timeout prevented fast failure.

and on the solution:

> The candidate describes a thread pool exhaustion mechanism but misses the
> critical mismatch between the 30s gateway timeout and the 10s edge load
> balancer timeout that caused the specific symptom of 504 errors being returned
> to clients while threads remained pinned.

This is a cross-file numeric correlation: the 30s lives in `deployment.txt` and
the diff, the 10s lives in a line the same record marks as *not* part of the
release. Each value is individually correct in both answers; what neither states
is that they are in the wrong relationship to each other.

One caveat the results file makes visible, and the reason this is a summarising
failure rather than purely a reasoning one: the solution's verify stage *did*
find the relationship internally. Its `mechanism` array contains "The ALB has a
fixed 10s proxy_read_timeout; it returns 504 to the client at 10s, but the
gateway thread is NOT released — it stays blocked in PaymentsClient.execute()
for the remaining ~20s", and it quoted the `edge_timeout_unchanged` line
verbatim while ruling that candidate out. But it put that line in `red_herrings`
rather than `evidence`, and the final one-paragraph `root_cause` — the field the
judge grades — drops the comparison entirely. The verdict was `confirmed` with
0 re-hypothesis rounds, so nothing in the pipeline pushed back.

**Next question:**
Two, one per axis of the loss. (1) Does forcing the verify stage to enumerate
every config/timeout/threshold value across files and check them *against each
other* fix incident_03 without adding a fifth stage — i.e. is this a prompt
problem rather than an architecture problem? (2) The 5 extract calls are
independent and currently sequential; does running them concurrently recover
enough of the 195.6s to make the evidence-recall gain worth paying for?

---

## v0.2 — "The grader was wrong, not the baseline"

**What I saw (evidence):**
A single sanity run, `evals/results/2026-08-28T21-41-07_baseline.json`, scored
incident_01 as a miss. Reading the two strings side by side, the answer was
right: it named the v2.14.0 deploy, the 8->32 concurrency raise, the pool left
at 10, and the resulting exhaustion. The string matcher disagreed only about
wording — `seq=0.337`, `token_f1=0.383`, both under threshold. The same run
recorded `"model": null` in its summary even though the per-incident
`_meta.model` showed `claude-sonnet-4-6`, so results files could not say which
model produced them.

**What I changed:**
- `evals/run_eval.py`: root-cause pass/fail now comes from an LLM judge
  (`judge_root_cause`), a separate call that receives only
  `{expected, actual}` — never the incident artifacts — and returns
  `{"correct": bool, "reason": str}`. It asks whether the two describe the same
  underlying cause, not whether the wording matches, and it is told that naming
  only a symptom is incorrect. The judge is pinned to `claude-haiku-4-5`
  (`--judge-model`) regardless of which model is under evaluation, so grading
  stays constant across runs. Its verdict, reason, model, and token usage are
  written into `root_cause_score.judge` — auditable, not a black box.
- String similarity is still computed and stored under
  `root_cause_score.similarity` with a `would_pass_threshold` field, so drift is
  visible; it is no longer the verdict. It is used only as a fallback when the
  judge call itself fails, and that case is recorded as
  `verdict_source: "similarity_fallback"`, counted in `judge_fallbacks`, and
  printed as a warning so a degraded run cannot pass as a judged one.
- The clock stops before the judge call, so grading latency stays out of the
  target's `avg_time`.
- `resolved_model()` now reads the model each run actually sent from its
  `_meta.model` rather than storing the CLI argument. Results carry both `model`
  (what ran) and `model_requested` (what was asked for), and both are printed.

**What happened (measured result):**
Rerunning the same case, `evals/results/2026-08-28T22-18-23_baseline.json`:
incident_01 now scores `correct: True via judge/claude-haiku-4-5`, with the
judge's reason recorded ("Both identify the same root cause: WORKER_CONCURRENCY
was increased to 32 without a corresponding increase to DB_POOL_SIZE..."). The
similarity line prints alongside it at `seq=0.31 token_f1=0.275
would_pass=False` — the false negative is now visible instead of silent. The
summary records `model=claude-sonnet-4-6 (requested=None)`. Evidence scoring was
untouched: still `p=1.0 r=0.75`, still exact tag match, no model opinion in it.

**Next question:**
v0.1 asked whether models would quote log lines with their trailing
`# EVIDENCE:` comment intact. They do — recall was 0.75 on this case with
correctly-formatted citations, so the tag grader works as designed. The open
question moves on: how does the staged solution compare on a full 6-incident
run?

---

## v0.1 — "Six incidents, a frozen baseline, and a gradeable harness"

**What I saw (evidence):**
Nothing to measure yet — the repo was scaffolding only (`run_eval.py` raised
`NotImplementedError`, `baseline/` and `solution/` were empty). Root-cause
answers are prose, so the first real problem was making "did it find the right
evidence" gradeable without a second model's opinion in the loop.

**What I changed:**
- Wrote 6 synthetic incidents in `evals/test_cases/incident_01..06/` (pool
  exhaustion, memory leak, timeout misconfig, bad migration, retry storm, cache
  stampede), 5 artifacts each. Evidence lines carry inline `# EVIDENCE: <tag>`
  markers; incidents 02 and 05 also carry `# NOISE: <tag>` red-herring lines
  (an httpx migration and a Kafka consumer-group rebalance) shipped in the same
  release as the real cause.
- `baseline/run_baseline.py`: one API call per incident, all 5 files
  concatenated, single JSON answer, no retry. Frozen from here.
- `solution/`: three staged prompts (`extract` per file → `hypothesize` over
  the facts → `verify` against the raw artifacts, which may revise or reject)
  and `agent.py` orchestrating them, ~7-8 API calls per incident. A `rejected`
  verdict re-runs hypothesize once with the verifier's critique. Every prompt,
  response, and retry is logged via `TrajectoryLogger` and rendered to Markdown.
- `evals/run_eval.py`: folder-based test cases, `--target baseline|solution`,
  per-incident root-cause fuzzy match (both strings printed), evidence
  precision/recall/F1 by exact tag match, red-herring contamination count,
  wall-clock, and a baseline-vs-solution comparison table.

**What happened (measured result):**
No eval numbers yet — nothing has been run against the API. The measured result
so far is `python evals/run_eval.py --dry-run`: 6/6 incidents validate, meaning
every tag in each `answer.json` appears verbatim in that incident's artifacts,
no EVIDENCE tag is undeclared, and no tag is used as both EVIDENCE and NOISE.
Bundle size is 6.2-8.2 KB per incident (~2K tokens), so a full 6-incident
comparison is a few dollars on `claude-opus-5`.

**Next question:**
Grading assumes the model quotes log lines *with* their trailing
`# EVIDENCE:` comment. Both prompts ask for verbatim lines, and the risk is
symmetric across baseline and solution — but if recall comes back near zero for
both, that's a citation-format artifact, not a reasoning failure. First real run
should check the raw `evidence` arrays before believing the score.