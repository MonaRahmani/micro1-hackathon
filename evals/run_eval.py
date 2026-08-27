#!/usr/bin/env python3
"""Eval harness for the hackathon project.

Loads JSON test cases from evals/test_cases/, runs each one through
`run_solution`, compares against the expected output, and writes a results
file to evals/results/<timestamp>.json.

Usage:
    python evals/run_eval.py
"""

from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
TEST_CASES_DIR = EVALS_DIR / "test_cases"
RESULTS_DIR = EVALS_DIR / "results"


def load_test_cases(test_cases_dir: Path = TEST_CASES_DIR) -> list[dict]:
    """Load every *.json file in test_cases_dir as a test case dict.

    Each test case is expected to look roughly like:
        {
            "id": "some-unique-id",
            "input": { ... },          # passed to run_solution
            "expected": { ... }        # compared against the solution output
        }
    """
    cases: list[dict] = []
    for path in sorted(test_cases_dir.glob("*.json")):
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("id", path.stem)
        data["_source_file"] = path.name
        cases.append(data)
    return cases


def run_solution(test_case: dict):
    """Run the solution under test for a single test case.

    PLACEHOLDER: fill this in. It should take a test case dict, run the
    solution (call the model, run the pipeline, whatever the solution is),
    and return the produced output in the same shape as test_case["expected"].
    """
    raise NotImplementedError(
        "run_solution() is not implemented yet. Wire it up to solution/ code."
    )


def check(actual, expected) -> bool:
    """Return True if `actual` satisfies `expected`.

    Default is strict equality. Loosen this per-project as needed (e.g. ignore
    key order, allow fuzzy string match, check a subset of fields).
    """
    return actual == expected


def run_all(test_cases: list[dict]) -> dict:
    results = []
    passed = 0

    for case in test_cases:
        entry = {"id": case.get("id"), "source_file": case.get("_source_file")}
        try:
            actual = run_solution(case)
            ok = check(actual, case.get("expected"))
            entry["status"] = "pass" if ok else "fail"
            entry["actual"] = actual
            entry["expected"] = case.get("expected")
            if ok:
                passed += 1
        except NotImplementedError as exc:
            entry["status"] = "error"
            entry["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001 - harness should never crash
            entry["status"] = "error"
            entry["error"] = f"{type(exc).__name__}: {exc}"
            entry["traceback"] = traceback.format_exc()
        results.append(entry)

    total = len(test_cases)
    score = (passed / total) if total else 0.0
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "score": round(score, 4),
        "results": results,
    }


def write_results(summary: dict, results_dir: Path = RESULTS_DIR) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    out_path = results_dir / f"{stamp}.json"
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)
    return out_path


def main() -> int:
    test_cases = load_test_cases()
    if not test_cases:
        print(f"No test cases found in {TEST_CASES_DIR}/ (looked for *.json).")
        return 1

    summary = run_all(test_cases)
    out_path = write_results(summary)

    print(f"Ran {summary['total']} test case(s)")
    print(f"  passed: {summary['passed']}")
    print(f"  failed: {summary['failed']}")
    print(f"  score:  {summary['score']:.2%}")
    print(f"Results written to {out_path}")

    for entry in summary["results"]:
        if entry["status"] != "pass":
            detail = entry.get("error", "output did not match expected")
            print(f"  [{entry['status']}] {entry['id']}: {detail}")

    # Non-zero exit if anything did not pass, so CI can gate on it.
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
