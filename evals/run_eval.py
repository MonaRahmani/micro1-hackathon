#!/usr/bin/env python3
"""Eval harness: baseline vs. staged investigator on 6 synthetic incidents.

Each test case is a folder `evals/test_cases/incident_0X/` holding five
artifacts plus `answer.json` (the graded key). Evidence lines in the artifacts
carry inline `# EVIDENCE: <tag>` markers; red-herring lines carry
`# NOISE: <tag>`. Evidence grading is exact-match on the tags recovered from
the lines the model cites — no model's opinion involved.

Scored per incident:
  * root cause correct   — LLM-as-judge (a pinned small model) decides whether
                           the answer names the same underlying cause as
                           answer.json. String similarity is still recorded but
                           is NOT the verdict; it failed correct-but-paraphrased
                           answers. The judge's reason is stored in the results.
  * evidence precision / recall / F1 vs. `evidence_tags`
  * red herring cited    — did the model quote a `# NOISE:` line
  * wall-clock seconds   — the target only; the judge call is not counted

Usage:
    python evals/run_eval.py --dry-run                  # no API calls, validates cases
    python evals/run_eval.py --target baseline
    python evals/run_eval.py --target solution
    python evals/run_eval.py --target solution --incident incident_02
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVALS_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVALS_DIR.parent
TEST_CASES_DIR = EVALS_DIR / "test_cases"
RESULTS_DIR = EVALS_DIR / "results"

sys.path.insert(0, str(REPO_ROOT))

INCIDENT_FILES = (
    "application.log",
    "error.log",
    "deployment.txt",
    "metrics.json",
    "recent_changes.diff",
)

EVIDENCE_RE = re.compile(r"EVIDENCE:\s*([a-z0-9_]+)")
NOISE_RE = re.compile(r"NOISE:\s*([a-z0-9_]+)")

# Root cause pass/fail comes from the LLM judge below. String similarity is
# still computed and stored — it is useful for spotting drift — but it is no
# longer the verdict: a correct answer in different words scored 0.34/0.38.
# These thresholds only decide the fallback verdict when the judge itself fails.
SEQUENCE_THRESHOLD = 0.40
TOKEN_F1_THRESHOLD = 0.45

# The judge is deliberately a small, fast model and is pinned regardless of
# which model is under evaluation, so grading stays constant across runs.
JUDGE_MODEL = "claude-haiku-4-5"
JUDGE_MAX_TOKENS = 512

JUDGE_SYSTEM = (
    "You grade incident post-mortems. You are given a reference root cause and "
    "a candidate root cause. Decide whether they identify the SAME underlying "
    "cause — the same specific change or condition, and the same mechanism. "
    "Wording, length, and level of detail do not matter. Different phrasing of "
    "the same cause is correct. Naming only a symptom (\"the database was "
    "slow\", \"the pool was exhausted\") when the reference names the change "
    "that produced it is incorrect. Blaming a different change is incorrect. "
    "Respond with JSON only. No preamble, no markdown code fences."
)

JUDGE_TEMPLATE = """Reference root cause:
{expected}

Candidate root cause:
{actual}

Do these describe the same underlying cause?

Respond with exactly this JSON object:

{{"correct": true or false, "reason": "one sentence saying what matched or what the candidate got wrong"}}
"""

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "because", "been", "but", "by",
    "for", "from", "had", "has", "have", "in", "into", "is", "it", "its", "of",
    "on", "or", "so", "that", "the", "their", "then", "there", "this", "to",
    "was", "were", "when", "which", "while", "with", "while", "every", "all",
}


# ------------------------------------------------------------ test cases ---
def load_incidents(only: str | None = None) -> list[dict[str, Any]]:
    """Load every incident_* folder that has an answer.json."""
    cases: list[dict[str, Any]] = []
    for path in sorted(TEST_CASES_DIR.glob("incident_*")):
        if not path.is_dir():
            continue
        if only and path.name != only:
            continue
        answer_path = path / "answer.json"
        if not answer_path.exists():
            continue
        answer = json.loads(answer_path.read_text(encoding="utf-8"))
        cases.append({"id": path.name, "dir": path, "answer": answer})
    return cases


def artifact_text(incident_dir: Path) -> str:
    return "\n".join(
        (incident_dir / name).read_text(encoding="utf-8") for name in INCIDENT_FILES
    )


# --------------------------------------------------------------- scoring ---
def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def content_tokens(text: str) -> set[str]:
    return {t for t in normalize(text).split() if t not in STOPWORDS and len(t) > 2}


def string_similarity(actual: str, expected: str) -> dict[str, Any]:
    """Kept for drift-spotting and as the judge's fallback. Not the verdict."""
    seq = difflib.SequenceMatcher(None, normalize(actual), normalize(expected)).ratio()
    a, e = content_tokens(actual), content_tokens(expected)
    overlap = len(a & e)
    precision = overlap / len(a) if a else 0.0
    recall = overlap / len(e) if e else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "sequence_ratio": round(seq, 3),
        "token_precision": round(precision, 3),
        "token_recall": round(recall, 3),
        "token_f1": round(f1, 3),
        "would_pass_threshold": bool(
            seq >= SEQUENCE_THRESHOLD or f1 >= TOKEN_F1_THRESHOLD
        ),
    }


def judge_root_cause(
    actual: str, expected: str, judge_model: str = JUDGE_MODEL
) -> dict[str, Any]:
    """Ask a small model whether the two describe the same underlying cause.

    Sees only the two strings — never the incident artifacts — so it grades
    agreement, not the incident. Raises on API or parse failure; the caller
    falls back to string similarity and records that it did.
    """
    import anthropic  # imported here so --dry-run needs no SDK

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=judge_model,
        max_tokens=JUDGE_MAX_TOKENS,
        system=JUDGE_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": JUDGE_TEMPLATE.format(expected=expected, actual=actual),
            }
        ],
    )
    raw = "".join(b.text for b in response.content if b.type == "text").strip()

    text = raw
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
        text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"judge returned no JSON object: {raw[:200]!r}")
    parsed = json.loads(text[start : end + 1])

    return {
        "correct": bool(parsed.get("correct")),
        "reason": str(parsed.get("reason", "")),
        "model": judge_model,
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
    }


def score_root_cause(
    actual: str, expected: str, judge_model: str = JUDGE_MODEL
) -> dict[str, Any]:
    """Judge decides correct/incorrect; similarity is recorded alongside it."""
    similarity = string_similarity(actual, expected)
    score: dict[str, Any] = {"similarity": similarity}

    if not actual.strip():
        score.update(
            correct=False,
            verdict_source="empty_answer",
            judge={"reason": "the target returned no root cause"},
        )
        return score

    try:
        judge = judge_root_cause(actual, expected, judge_model)
        score.update(correct=judge["correct"], verdict_source="judge", judge=judge)
    except Exception as exc:  # noqa: BLE001 - a judge failure must not lose the run
        score.update(
            correct=similarity["would_pass_threshold"],
            verdict_source="similarity_fallback",
            judge={"error": f"{type(exc).__name__}: {exc}"},
        )
    return score


def tags_from(lines: list[str], pattern: re.Pattern[str]) -> set[str]:
    found: set[str] = set()
    for line in lines:
        found.update(pattern.findall(line))
    return found


def score_evidence(cited: list[str], expected_tags: list[str]) -> dict[str, Any]:
    predicted = tags_from(cited, EVIDENCE_RE)
    expected = set(expected_tags)
    hit = predicted & expected
    precision = len(hit) / len(predicted) if predicted else 0.0
    recall = len(hit) / len(expected) if expected else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    noise = tags_from(cited, NOISE_RE)
    return {
        "cited_count": len(cited),
        "predicted_tags": sorted(predicted),
        "expected_tags": sorted(expected),
        "matched_tags": sorted(hit),
        "missed_tags": sorted(expected - predicted),
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "red_herring_tags_cited": sorted(noise),
        "red_herring_cited": bool(noise),
    }


# ----------------------------------------------------------- case checks ---
def validate_case(case: dict[str, Any]) -> list[str]:
    """Dry-run sanity checks. No API calls. Returns a list of problems."""
    problems: list[str] = []
    incident_dir: Path = case["dir"]
    answer = case["answer"]

    for name in INCIDENT_FILES:
        if not (incident_dir / name).exists():
            problems.append(f"missing artifact {name}")
    if problems:
        return problems

    blob = artifact_text(incident_dir)
    present = set(EVIDENCE_RE.findall(blob))
    expected = set(answer.get("evidence_tags", []))

    if not expected:
        problems.append("answer.json has no evidence_tags")
    for tag in sorted(expected - present):
        problems.append(f"tag '{tag}' in answer.json never appears in the artifacts")
    for tag in sorted(present - expected):
        problems.append(f"tag '{tag}' is marked EVIDENCE but is not in answer.json")

    noise_present = set(NOISE_RE.findall(blob))
    if answer.get("has_red_herring"):
        if not noise_present:
            problems.append("has_red_herring is true but no NOISE lines exist")
        declared = set(answer.get("red_herring_tags", []))
        for tag in sorted(noise_present - declared):
            problems.append(f"NOISE tag '{tag}' not declared in red_herring_tags")
    elif noise_present:
        problems.append(f"unexpected NOISE tags {sorted(noise_present)}")

    if noise_present & present:
        problems.append("a tag is used as both EVIDENCE and NOISE")
    if not answer.get("root_cause"):
        problems.append("answer.json has no root_cause")
    return problems


# ------------------------------------------------------------- execution ---
def get_runner(target: str):
    if target == "baseline":
        from baseline.run_baseline import run_baseline
        return run_baseline
    if target == "solution":
        from solution.agent import run_solution
        return run_solution
    raise ValueError(f"unknown target: {target}")


def run_all(
    target: str,
    cases: list[dict[str, Any]],
    model: str | None,
    judge_model: str = JUDGE_MODEL,
) -> dict:
    runner = get_runner(target)
    results: list[dict[str, Any]] = []

    for case in cases:
        entry: dict[str, Any] = {"id": case["id"], "target": target}
        started = time.perf_counter()
        try:
            output = runner(case["dir"], model=model)
            # Stop the clock before grading — the judge call is harness
            # overhead and must not land in the target's wall-clock metric.
            elapsed = time.perf_counter() - started

            expected = case["answer"]
            rc = score_root_cause(
                output.get("root_cause", ""), expected["root_cause"], judge_model
            )
            ev = score_evidence(
                output.get("evidence", []), expected.get("evidence_tags", [])
            )
            meta = output.get("_meta", {})

            entry.update(
                {
                    "status": "ok",
                    "elapsed_seconds": round(elapsed, 3),
                    "api_calls": meta.get("api_calls"),
                    "usage": meta.get("usage"),
                    "confidence": output.get("confidence"),
                    "root_cause_actual": output.get("root_cause", ""),
                    "root_cause_expected": expected["root_cause"],
                    "root_cause_score": rc,
                    "evidence_score": ev,
                    "evidence_cited": output.get("evidence", []),
                    "has_red_herring": expected.get("has_red_herring", False),
                    "meta": meta,
                }
            )
        except Exception as exc:  # noqa: BLE001 - the harness must not crash
            entry.update(
                {
                    "status": "error",
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                }
            )
        results.append(entry)
        print_case(entry)

    return summarize(target, model, results, judge_model)


def resolved_model(results: list[dict], requested: str | None) -> str:
    """The model string the target actually sent, not the one we asked for.

    `--model` is often omitted and the model comes from ANTHROPIC_MODEL or the
    target's own default, so the request value alone records None. Each run
    reports what it used in `_meta.model`; that is the authoritative value.
    """
    used = {
        r["meta"].get("model")
        for r in results
        if r.get("status") == "ok" and r.get("meta", {}).get("model")
    }
    if len(used) == 1:
        return used.pop()
    if used:
        return "mixed: " + ", ".join(sorted(used))
    return requested or os.environ.get("ANTHROPIC_MODEL") or "unknown"


def summarize(
    target: str,
    model: str | None,
    results: list[dict],
    judge_model: str = JUDGE_MODEL,
) -> dict:
    ok = [r for r in results if r["status"] == "ok"]
    total = len(results)
    correct = sum(1 for r in ok if r["root_cause_score"]["correct"])
    fallbacks = sum(
        1 for r in ok if r["root_cause_score"].get("verdict_source") != "judge"
    )

    def mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 4) if values else 0.0

    herring_cases = [r for r in ok if r.get("has_red_herring")]
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target": target,
        "model": resolved_model(results, model),
        "model_requested": model,
        "judge_model": judge_model,
        "total": total,
        "errors": total - len(ok),
        "correct_root_cause": correct,
        "judge_fallbacks": fallbacks,
        "evidence_precision": mean([r["evidence_score"]["precision"] for r in ok]),
        "evidence_recall": mean([r["evidence_score"]["recall"] for r in ok]),
        "evidence_f1": mean([r["evidence_score"]["f1"] for r in ok]),
        "avg_seconds": mean([r["elapsed_seconds"] for r in ok]),
        "avg_api_calls": mean([r["api_calls"] or 0 for r in ok]),
        "red_herring_cases": len(herring_cases),
        "red_herring_cases_contaminated": sum(
            1 for r in herring_cases if r["evidence_score"]["red_herring_cited"]
        ),
        "results": results,
    }


# --------------------------------------------------------------- output ----
def print_case(entry: dict) -> None:
    print(f"\n--- {entry['id']} [{entry['target']}] ---")
    if entry["status"] != "ok":
        print(f"  ERROR: {entry['error']}")
        return
    rc = entry["root_cause_score"]
    ev = entry["evidence_score"]
    sim = rc["similarity"]
    judge = rc.get("judge", {})
    source = rc.get("verdict_source", "judge")
    print(f"  root cause correct: {rc['correct']}  (via {source}"
          f"{'/' + judge['model'] if judge.get('model') else ''})")
    print(f"    expected: {entry['root_cause_expected']}")
    print(f"    actual:   {entry['root_cause_actual']}")
    if judge.get("reason"):
        print(f"    judge:    {judge['reason']}")
    if judge.get("error"):
        print(f"    judge FAILED: {judge['error']} — fell back to string similarity")
    print(f"    similarity (not the verdict): seq={sim['sequence_ratio']} "
          f"token_f1={sim['token_f1']} would_pass={sim['would_pass_threshold']}")
    print(f"  evidence: p={ev['precision']} r={ev['recall']} f1={ev['f1']}")
    print(f"    matched: {ev['matched_tags']}")
    print(f"    missed:  {ev['missed_tags']}")
    if entry.get("has_red_herring"):
        verdict = "CITED (bad)" if ev["red_herring_cited"] else "avoided"
        print(f"  red herring: {verdict} {ev['red_herring_tags_cited']}")
    print(f"  time: {entry['elapsed_seconds']}s   api calls: {entry['api_calls']}")


def latest_results(target: str) -> dict | None:
    files = sorted(RESULTS_DIR.glob(f"*_{target}.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))


def print_table(baseline: dict | None, solution: dict | None) -> None:
    def cell(summary: dict | None, fn) -> str:
        if summary is None:
            return "—"
        try:
            return fn(summary)
        except Exception:  # noqa: BLE001
            return "—"

    rows = [
        ("correct root cause", lambda s: f"{s['correct_root_cause']}/{s['total']}"),
        ("evidence accuracy", lambda s: f"{s['evidence_f1'] * 100:.0f}%"),
        ("evidence precision", lambda s: f"{s['evidence_precision'] * 100:.0f}%"),
        ("evidence recall", lambda s: f"{s['evidence_recall'] * 100:.0f}%"),
        ("red herrings cited",
         lambda s: f"{s['red_herring_cases_contaminated']}/{s['red_herring_cases']}"),
        ("avg time", lambda s: f"{s['avg_seconds']:.1f}s"),
        ("avg api calls", lambda s: f"{s['avg_api_calls']:.1f}"),
        ("errors", lambda s: str(s["errors"])),
    ]

    print("\n" + "=" * 46)
    print(f"{'metric':<20}{'baseline':<12}{'solution':<12}")
    print("-" * 46)
    for label, fn in rows:
        print(f"{label:<20}{cell(baseline, fn):<12}{cell(solution, fn):<12}")
    print("=" * 46)
    for name, summary in (("baseline", baseline), ("solution", solution)):
        if summary:
            print(f"{name}: {summary['timestamp']} "
                  f"model={summary.get('model') or 'unknown'} "
                  f"judge={summary.get('judge_model') or 'n/a'}")
            if summary.get("judge_fallbacks"):
                print(f"  warning: {summary['judge_fallbacks']} verdict(s) fell back "
                      "to string similarity — the judge call failed")


def write_results(summary: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    out = RESULTS_DIR / f"{stamp}_{summary['target']}.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


# ------------------------------------------------------------------ main ---
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("baseline", "solution"),
                        help="which approach to run (omit with --dry-run)")
    parser.add_argument("--incident", help="run a single incident, e.g. incident_02")
    parser.add_argument("--model", help="override the model for this run")
    parser.add_argument("--judge-model", default=JUDGE_MODEL,
                        help=f"model that grades root causes (default: {JUDGE_MODEL}); "
                             "pinned independently of the model under evaluation")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate the test cases only, no API calls")
    parser.add_argument("--table-only", action="store_true",
                        help="print the comparison table from saved results")
    args = parser.parse_args()

    cases = load_incidents(args.incident)
    if not cases:
        print(f"No incident folders with answer.json found in {TEST_CASES_DIR}/")
        return 1

    if args.table_only:
        print_table(latest_results("baseline"), latest_results("solution"))
        return 0

    if args.dry_run or not args.target:
        print(f"Dry run: validating {len(cases)} incident(s). No API calls.\n")
        bad = 0
        for case in cases:
            problems = validate_case(case)
            tags = case["answer"].get("evidence_tags", [])
            herring = " +red-herring" if case["answer"].get("has_red_herring") else ""
            if problems:
                bad += 1
                print(f"  [FAIL] {case['id']}{herring}")
                for p in problems:
                    print(f"         - {p}")
            else:
                print(f"  [ok]   {case['id']}{herring}  tags={tags}")
        print(f"\n{len(cases) - bad}/{len(cases)} incidents valid.")
        if not args.target:
            print("Pass --target baseline|solution to run for real (costs API calls).")
        return 0 if bad == 0 else 1

    summary = run_all(args.target, cases, args.model, args.judge_model)
    out = write_results(summary)
    print(f"\nran {summary['target']} on model={summary['model']} "
          f"(requested={summary['model_requested']}), "
          f"graded by judge={summary['judge_model']}")

    other = "solution" if args.target == "baseline" else "baseline"
    print_table(
        summary if args.target == "baseline" else latest_results("baseline"),
        summary if args.target == "solution" else latest_results("solution"),
    )
    if latest_results(other) is None:
        print(f"\n(no saved {other} results yet — run --target {other} to fill the column)")
    print(f"\nResults written to {out.relative_to(REPO_ROOT)}")
    return 0 if summary["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
