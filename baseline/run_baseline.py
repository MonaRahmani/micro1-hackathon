#!/usr/bin/env python3
"""Baseline: one Claude call per incident, everything in one prompt.

This is the naive approach we are trying to beat. It concatenates all five
incident files into a single prompt and asks for the root cause in one shot.
No extraction stage, no verification, no retries, no tools.

FROZEN. Per CLAUDE.md, do not change this file to make the solution look
better. If it has to change, say so explicitly in CHANGELOG.md.

Usage:
    python baseline/run_baseline.py evals/test_cases/incident_01
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = "claude-opus-5"
MAX_TOKENS = 8000

INCIDENT_FILES = (
    "application.log",
    "error.log",
    "deployment.txt",
    "metrics.json",
    "recent_changes.diff",
)

SYSTEM_PROMPT = (
    "You are an on-call site reliability engineer investigating a production "
    "incident. Respond with JSON only. No preamble, no markdown code fences."
)

USER_TEMPLATE = """Below is everything we have about a production incident.

{bundle}

What caused this incident?

Respond with exactly this JSON object and nothing else:

{{
  "root_cause": "one or two sentences naming the specific cause",
  "evidence": ["verbatim lines from the files above that support your answer"],
  "confidence": 0-100
}}

Each string in "evidence" must be copied verbatim from the files above,
including any trailing comment on the line.
"""


def read_incident(incident_dir: Path) -> dict[str, str]:
    """Read the five incident files. Missing files raise."""
    contents: dict[str, str] = {}
    for name in INCIDENT_FILES:
        path = incident_dir / name
        contents[name] = path.read_text(encoding="utf-8")
    return contents


def build_prompt(incident_dir: Path) -> str:
    contents = read_incident(incident_dir)
    bundle = "\n\n".join(
        f"===== {name} =====\n{body}" for name, body in contents.items()
    )
    return USER_TEMPLATE.format(bundle=bundle)


def _text_of(response: anthropic.types.Message) -> str:
    """Concatenate the text blocks of a response (skips thinking blocks)."""
    return "".join(b.text for b in response.content if b.type == "text")


def parse_json_object(raw: str) -> dict[str, Any]:
    """Best-effort extraction of the first JSON object in `raw`.

    The baseline gets exactly one shot — no retry, no reprompt. If the model
    returns prose or a fenced block we still try to recover the object, but we
    do not spend another API call on it.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found in model response")
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("unterminated JSON object in model response")


def run_baseline(
    incident_dir: str | Path,
    model: str | None = None,
    client: anthropic.Anthropic | None = None,
) -> dict[str, Any]:
    """Run the baseline on one incident folder.

    Returns {"root_cause", "evidence", "confidence", "_meta"}. On a parse
    failure the first three are empty/zero and "_meta" carries the error and
    the raw response, so the eval harness can score it as a miss rather than
    crash.
    """
    incident_dir = Path(incident_dir)
    model = model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL
    client = client or anthropic.Anthropic()

    prompt = build_prompt(incident_dir)

    started = time.perf_counter()
    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed = time.perf_counter() - started

    raw = _text_of(response)
    meta: dict[str, Any] = {
        "target": "baseline",
        "incident": incident_dir.name,
        "model": model,
        "api_calls": 1,
        "elapsed_seconds": round(elapsed, 3),
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
        "stop_reason": response.stop_reason,
    }

    try:
        parsed = parse_json_object(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        meta["parse_error"] = str(exc)
        meta["raw_response"] = raw
        return {"root_cause": "", "evidence": [], "confidence": 0, "_meta": meta}

    evidence = parsed.get("evidence") or []
    if isinstance(evidence, str):
        evidence = [evidence]

    return {
        "root_cause": str(parsed.get("root_cause", "")),
        "evidence": [str(e) for e in evidence],
        "confidence": parsed.get("confidence", 0),
        "_meta": meta,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    result = run_baseline(argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
