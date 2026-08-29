# Reproduce

Everything here was run on the machine that produced the results in
`evals/results/`. Commands are copy-pasteable from a clean clone.

## Requirements / versions

| What | Version | Note |
| ---- | ------- | ---- |
| Python | **3.10.0** | 3.10+ required (`anthropic` 1.x drops 3.9) |
| `anthropic` | **1.2.0** | pinned in `requirements.txt` |
| `python-dotenv` | **1.2.3** | pinned in `requirements.txt` |
| `httpx2` | 2.12.0 | transitive, recorded for reference |
| `pydantic` | 2.13.5 | transitive, recorded for reference |
| OS | macOS (Darwin 25.3.0) | nothing platform-specific; Linux should work |

**Models.** Both the baseline and the solution use the same model so the
comparison is fair; the grader is pinned separately and independently.

| Role | Model | Set by |
| ---- | ----- | ------ |
| Baseline + solution under test | `claude-sonnet-4-6` | `ANTHROPIC_MODEL` in `.env` (or `--model`) |
| Root-cause judge | `claude-haiku-4-5` | `JUDGE_MODEL` in `evals/run_eval.py` (or `--judge-model`) |

If `ANTHROPIC_MODEL` is unset, the code default is `claude-opus-5` — the results
committed here were produced with `claude-sonnet-4-6`, so set it explicitly to
reproduce those numbers.

**Account needed.** An Anthropic API key from https://console.anthropic.com/.
Nothing else — no database, no cloud account, no system packages.

## Setup from a clean clone

```bash
git clone https://github.com/MonaRahmani/micro1-hackathon.git
cd micro1-hackathon

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install --upgrade pip          # optional; venv ships pip 21.2.3, which warns
pip install -r requirements.txt

cp .env.example .env
# then edit .env and set:
#   ANTHROPIC_API_KEY=sk-ant-...
#   ANTHROPIC_MODEL=claude-sonnet-4-6
```

`.env` is gitignored and must never be committed. `.env.example` holds the
variable names only.

Confirm the environment is this repo's, not another project's:

```bash
which python                       # -> <repo>/.venv/bin/python
python -c "import anthropic; print(anthropic.__file__)"
```

## Verify with no API calls

This validates all six test cases — that every tag in each `answer.json` appears
verbatim in that incident's artifacts, no tag is undeclared, and no tag is used
as both evidence and red-herring noise. It costs nothing and is the fastest way
to confirm the checkout is intact:

```bash
python evals/run_eval.py --dry-run
```

Expected: `6/6 incidents valid.`

## Run the baseline

The baseline concatenates all five artifacts into one prompt and makes a single
API call. It is frozen — do not modify it to improve the comparison.

```bash
# one incident (cheapest real check, ~9s, ~$0.02)
python evals/run_eval.py --target baseline --incident incident_01

# all six incidents (~1 min, ~$0.11)
python evals/run_eval.py --target baseline
```

Run it directly on a single folder, outside the harness:

```bash
python baseline/run_baseline.py evals/test_cases/incident_01
```

## Run the solution

The solution runs 4 stages: 5 concurrent extract calls (one per artifact), then
hypothesize, then verify — 7 API calls per incident.

```bash
# one incident (~2 min, ~$0.30)
python evals/run_eval.py --target solution --incident incident_03

# all six incidents (~13 min, ~$1.81)
python evals/run_eval.py --target solution
```

Directly on a single folder:

```bash
python solution/agent.py evals/test_cases/incident_01
```

Every solution run writes a trajectory to
`trajectories/solution-agent/incident_0X.jsonl` and renders it to
`incident_0X.md`. The `.jsonl` files are gitignored; the rendered `.md`
transcripts are committed.

## Comparison table

Print the table from the most recent saved results of each target, without
spending anything:

```bash
python evals/run_eval.py --table-only
```

## Expected output

Each incident prints the judge's verdict, both root-cause strings for manual
review, the string-similarity score (recorded but **not** the pass/fail signal),
evidence tag matches, and timing:

```
--- incident_01 [baseline] ---
  root cause correct: True  (via judge/claude-haiku-4-5)
    expected: Deploy v2.14.0 raised WORKER_CONCURRENCY from 8 to 32 while leaving DB_POOL_SIZE at 10 ...
    actual:   The v2.14.0 deployment increased WORKER_CONCURRENCY from 8 to 32 without increasing ...
    judge:    Both identify the same specific change (WORKER_CONCURRENCY increased to 32 while ...
    similarity (not the verdict): seq=0.368 token_f1=0.383 would_pass=False
  evidence: p=1.0 r=0.75 f1=0.857
    matched: ['pool_exhaustion', 'pool_size_unchanged', 'worker_concurrency_raised']
    missed:  ['db_wait_time_spike']
  time: 9.106s   api calls: 1
```

then the comparison table, which reads the latest saved results for each target:

```
==============================================
metric              baseline    solution
----------------------------------------------
correct root cause  6/6         6/6
evidence accuracy   87%         95%
evidence precision  100%        100%
evidence recall     78%         92%
red herrings cited  0/2         0/2
avg time            10.6s       126.2s
avg api calls       1.0         7.0
errors              0           0
==============================================
baseline: 2026-08-29T04:36:10.920775+00:00 model=claude-sonnet-4-6 judge=claude-haiku-4-5
solution: 2026-08-29T03:42:23.975208+00:00 model=claude-sonnet-4-6 judge=claude-haiku-4-5
```

> **Your numbers will not match exactly, and the difference can be a whole
> incident.** Two full baseline runs on the same model and the same prompts
> scored 5/6 (`...T01-51-01_baseline.json`) and 6/6
> (`...T04-36-10_baseline.json`), differing on incident_03; aggregate evidence
> recall moved 83% -> 78% between them. Treat any single run as one sample.
> Sampling is non-deterministic and the harness does not pin a seed (the
> Messages API does not offer one). Draw conclusions from repeated runs, not
> from one table.

Results are written to `evals/results/<timestamp>_<target>.json`. Committed
examples of a full six-incident run:

- `evals/results/2026-08-29T01-51-01_baseline.json`
- `evals/results/2026-08-29T03-42-23_solution.json`

Each results file carries the aggregate metrics, plus per-incident: both
root-cause strings, the judge's verdict **and its stated reason**, the
similarity scores, matched/missed evidence tags, token usage, and — for the
solution — the causal `mechanism`, `ruled_out` candidates,
`cross_file_value_checks`, and the verify `verdict`.

The table shows `—` in a column when that target has no saved results yet. Note
that a single-incident run overwrites the column it belongs to, so a `1/1` next
to a `6/6` means the two columns are not comparable.

## Approximate runtime and cost

Measured from the token usage recorded in the two committed six-incident runs
above, at `claude-sonnet-4-6` ($3/$15 per MTok) and `claude-haiku-4-5`
($1/$5 per MTok). Actual figures, not estimates — but they will vary run to run
with response length.

| | per incident | full 6-incident run |
| --- | --- | --- |
| **Baseline** | ~10.9s, 1 API call, ~$0.018 | ~1 min, 6 calls, **~$0.11** |
| **Solution** | ~126s, 7 API calls, ~$0.30 | ~13 min, 42 calls, **~$1.81** |
| Judge (both) | 1 extra call/incident | ~$0.005 per full run |
| `--dry-run` / `--table-only` | free | free |

Solution time splits roughly into **~35s of extract** (5 calls running
concurrently, so one call deep rather than five) and **~90s of hypothesize +
verify**, which are sequential because each depends on the previous stage. That
90s is the current latency floor.
