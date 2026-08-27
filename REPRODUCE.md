# Reproduce

This document lets anyone go from a clean clone to running the baseline, the
solution, and the eval harness.

## Requirements / versions

<!--
- Python version (e.g. 3.11+)
- Node version, if the solution uses Node
- OS assumptions (e.g. macOS / Linux)
- API keys / accounts needed (e.g. ANTHROPIC_API_KEY) and where to get them
- Any system packages
-->

## Setup from a clean clone

<!--
Step-by-step, copy-pasteable. Example shape:
1. git clone <repo> && cd micro1-hackathon
2. python -m venv .venv && source .venv/bin/activate
3. pip install -r requirements.txt
4. cp .env.example .env  and fill in ANTHROPIC_API_KEY
-->

## Run the baseline

<!--
The exact command to run the unmodified baseline approach.
Example: python baseline/run.py --input <...>
-->

## Run the solution

<!--
The exact command to run the improved solution.
Example: python solution/run.py --input <...>
-->

## Run the eval harness

```
python evals/run_eval.py
```

<!--
Note where test cases live (evals/test_cases/*.json) and where results are
written (evals/results/<timestamp>.json).
-->

## Expected output

<!--
What a successful run looks like: sample stdout, the pass/fail line, the score,
and the path of the results file that gets written.
-->

## Approximate runtime and cost

<!--
- Baseline: ~X seconds, ~$Y in API calls
- Solution: ~X seconds, ~$Y in API calls
- Full eval harness: ~X seconds, ~$Y in API calls
-->
