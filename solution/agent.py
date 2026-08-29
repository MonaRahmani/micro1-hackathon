#!/usr/bin/env python3
"""Solution: a staged incident investigator.

Three real stages, each its own API call (or calls):

  1. extract     one call per artifact -> structured facts (5 calls, concurrent)
  2. hypothesize one call over all facts -> candidate root cause
  3. verify      one call against the RAW artifacts -> confirm / revise / reject

A `rejected` verdict feeds the verifier's critique back into hypothesize once,
then re-verifies. Final output has the same JSON shape as the baseline
(root_cause / evidence / confidence) so the eval harness can compare them
directly.

Every prompt, response, and retry is logged to
`trajectories/solution-agent/<incident>.jsonl` and rendered to `.md`.

Usage:
    python solution/agent.py evals/test_cases/incident_01
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from solution.trajectory_logger import TrajectoryLogger, render_to_markdown  # noqa: E402

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
TRAJECTORY_DIR = REPO_ROOT / "trajectories" / "solution-agent"

DEFAULT_MODEL = "claude-opus-5"
MAX_TOKENS = 8000
MAX_JSON_RETRIES = 1          # per stage, on unparseable JSON
MAX_REHYPOTHESIS = 1          # how many times a `rejected` verdict re-runs stage 2

INCIDENT_FILES = (
    "application.log",
    "error.log",
    "deployment.txt",
    "metrics.json",
    "recent_changes.diff",
)


# ---------------------------------------------------------------- prompts ---
def load_prompt(name: str) -> tuple[str, str]:
    """Split a prompt file into (system, user_template) on its ## headings."""
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    if "## System" not in text or "## User" not in text:
        raise ValueError(f"{name}.md must contain '## System' and '## User'")
    _, rest = text.split("## System", 1)
    system, user = rest.split("## User", 1)
    return system.strip(), user.strip()


def fill(template: str, **values: str) -> str:
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template


# ------------------------------------------------------------- json utils ---
def parse_json_object(raw: str) -> dict[str, Any]:
    """Extract the first complete JSON object from a model response."""
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


def _text_of(response: anthropic.types.Message) -> str:
    return "".join(b.text for b in response.content if b.type == "text")


# ------------------------------------------------------- parallel logging ---
class _BufferedLog:
    """Collects one parallel extract call's events for ordered replay.

    The extract calls run concurrently, but a trajectory that interleaves five
    conversations is unreadable. Each call logs into its own buffer, stamping
    the real time the event happened; the buffers are then replayed into the
    real TrajectoryLogger in file order. Nothing is lost — the timestamps still
    show the calls overlapping.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def _add(self, event_type: str, payload: dict[str, Any]) -> None:
        # `ts` in the payload overrides the logger's own stamp on replay.
        self.events.append(
            (event_type, {"ts": datetime.now(timezone.utc).isoformat(), **payload})
        )

    def prompt(self, content: str, **extra: Any) -> None:
        self._add("prompt", {"content": content, **extra})

    def llm_response(self, content: str, **extra: Any) -> None:
        self._add("llm_response", {"content": content, **extra})

    def tool_call(self, name: str, arguments: Any = None, **extra: Any) -> None:
        self._add("tool_call", {"name": name, "arguments": arguments, **extra})

    def tool_result(
        self, name: str, result: Any = None, is_error: bool = False, **extra: Any
    ) -> None:
        self._add(
            "tool_result", {"name": name, "result": result, "is_error": is_error, **extra}
        )

    def log_event(self, event_type: str, **payload: Any) -> None:
        self._add(event_type, payload)

    def replay_into(self, log: TrajectoryLogger) -> None:
        for event_type, payload in self.events:
            log.log_event(event_type, **payload)


# ----------------------------------------------------------------- agent ----
class IncidentAgent:
    """Extract -> hypothesize -> verify, with a trajectory log at every step."""

    def __init__(
        self,
        model: str | None = None,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self.model = model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL
        self.client = client or anthropic.Anthropic()
        self.api_calls = 0
        self.usage = {"input_tokens": 0, "output_tokens": 0}
        self._counter_lock = threading.Lock()  # extract stage runs concurrently

    # -- one logged, retried JSON call ------------------------------------
    def _call_json(
        self,
        log: TrajectoryLogger,
        stage: str,
        system: str,
        user: str,
    ) -> dict[str, Any]:
        attempt = 0
        message = user
        while True:
            log.prompt(message, stage=stage, attempt=attempt, system=system)
            response = self.client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": message}],
            )
            with self._counter_lock:
                self.api_calls += 1
                self.usage["input_tokens"] += response.usage.input_tokens
                self.usage["output_tokens"] += response.usage.output_tokens

            raw = _text_of(response)
            log.llm_response(
                raw,
                stage=stage,
                attempt=attempt,
                tokens={
                    "in": response.usage.input_tokens,
                    "out": response.usage.output_tokens,
                },
                stop_reason=response.stop_reason,
            )

            try:
                return parse_json_object(raw)
            except (ValueError, json.JSONDecodeError) as exc:
                if attempt >= MAX_JSON_RETRIES:
                    log.log_event(
                        "stage_failed", stage=stage, error=str(exc), raw=raw
                    )
                    raise
                log.log_event("retry", stage=stage, attempt=attempt, reason=str(exc))
                message = (
                    user
                    + "\n\nYour previous reply could not be parsed as JSON "
                    f"({exc}). Reply with the JSON object only — no prose, no "
                    "code fences."
                )
                attempt += 1

    # -- stages ------------------------------------------------------------
    def extract(
        self, log: TrajectoryLogger, incident_id: str, artifacts: dict[str, str]
    ) -> list[dict[str, Any]]:
        """One call per artifact, run concurrently — the calls are independent.

        Each call logs into its own buffer; the buffers replay into the real
        trajectory in file order once all five have finished, so the transcript
        stays readable and deterministic while the wall clock is one call deep
        instead of five.
        """
        system, template = load_prompt("extract")
        names = list(artifacts)
        log.log_event("stage_start", stage="extract", files=names, parallel=True)

        def extract_one(name: str) -> tuple[dict[str, Any], _BufferedLog]:
            content = artifacts[name]
            buf = _BufferedLog()
            buf.log_event("stage_start", stage=f"extract:{name}", file=name)
            buf.tool_call("read_file", {"path": name, "bytes": len(content)})
            buf.tool_result("read_file", content)
            result = self._call_json(
                buf,
                stage=f"extract:{name}",
                system=system,
                user=fill(
                    template,
                    FILE_NAME=name,
                    INCIDENT_ID=incident_id,
                    FILE_CONTENT=content,
                ),
            )
            result.setdefault("file", name)
            return result, buf

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            futures = {name: pool.submit(extract_one, name) for name in names}

        # Replay every buffer, including those of calls that failed, before
        # re-raising: a failed run must still leave a usable trajectory.
        facts: list[dict[str, Any]] = []
        first_error: BaseException | None = None
        for name in names:
            try:
                result, buf = futures[name].result()
                buf.replay_into(log)
                facts.append(result)
            except BaseException as exc:  # noqa: BLE001 - re-raised below
                log.log_event("stage_failed", stage=f"extract:{name}", error=str(exc))
                first_error = first_error or exc

        log.log_event(
            "stage_end",
            stage="extract",
            parallel=True,
            files_extracted=len(facts),
            elapsed_seconds=round(time.perf_counter() - started, 3),
        )
        if first_error is not None:
            raise first_error
        return facts

    def hypothesize(
        self,
        log: TrajectoryLogger,
        incident_id: str,
        facts: list[dict[str, Any]],
        critique: str | None = None,
    ) -> dict[str, Any]:
        system, template = load_prompt("hypothesize")
        critique_block = ""
        if critique:
            critique_block = (
                "A reviewer rejected your previous hypothesis. Their critique:\n\n"
                f"{critique}\n\n"
                "Produce a different hypothesis that answers it."
            )
        log.log_event("stage_start", stage="hypothesize", rehypothesis=bool(critique))
        return self._call_json(
            log,
            stage="hypothesize" if not critique else "rehypothesize",
            system=system,
            user=fill(
                template,
                INCIDENT_ID=incident_id,
                FACTS_JSON=json.dumps(facts, indent=2, ensure_ascii=False),
                CRITIQUE_BLOCK=critique_block,
            ),
        )

    def verify(
        self,
        log: TrajectoryLogger,
        incident_id: str,
        hypothesis: dict[str, Any],
        artifacts: dict[str, str],
        round_no: int = 0,
    ) -> dict[str, Any]:
        system, template = load_prompt("verify")
        raw_artifacts = "\n\n".join(
            f"===== {name} =====\n{body}" for name, body in artifacts.items()
        )
        log.log_event("stage_start", stage="verify", round=round_no)
        return self._call_json(
            log,
            stage=f"verify:{round_no}",
            system=system,
            user=fill(
                template,
                INCIDENT_ID=incident_id,
                HYPOTHESIS_JSON=json.dumps(hypothesis, indent=2, ensure_ascii=False),
                RAW_ARTIFACTS=raw_artifacts,
            ),
        )

    # -- orchestration -----------------------------------------------------
    def investigate(self, incident_dir: str | Path) -> dict[str, Any]:
        incident_dir = Path(incident_dir)
        incident_id = incident_dir.name
        artifacts = {
            name: (incident_dir / name).read_text(encoding="utf-8")
            for name in INCIDENT_FILES
        }

        TRAJECTORY_DIR.mkdir(parents=True, exist_ok=True)
        jsonl_path = TRAJECTORY_DIR / f"{incident_id}.jsonl"
        md_path = TRAJECTORY_DIR / f"{incident_id}.md"
        jsonl_path.unlink(missing_ok=True)  # one trajectory per run, not appended

        started = time.perf_counter()
        log = TrajectoryLogger(
            jsonl_path,
            run_id=f"solution-{incident_id}",
            metadata={
                "target": "solution",
                "incident": incident_id,
                "model": self.model,
                "stages": "extract -> hypothesize -> verify",
            },
        )

        try:
            facts = self.extract(log, incident_id, artifacts)
            hypothesis = self.hypothesize(log, incident_id, facts)

            verification = self.verify(log, incident_id, hypothesis, artifacts)
            rounds = 0
            while (
                verification.get("verdict") == "rejected"
                and rounds < MAX_REHYPOTHESIS
            ):
                rounds += 1
                log.log_event(
                    "verdict_rejected",
                    round=rounds,
                    unresolved=verification.get("unresolved", ""),
                )
                critique = json.dumps(
                    {
                        "unresolved": verification.get("unresolved", ""),
                        "dropped_citations": verification.get("dropped_citations", []),
                        "red_herrings": verification.get("red_herrings", []),
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                hypothesis = self.hypothesize(log, incident_id, facts, critique)
                verification = self.verify(
                    log, incident_id, hypothesis, artifacts, round_no=rounds
                )

            elapsed = time.perf_counter() - started

            evidence = verification.get("evidence") or []
            if isinstance(evidence, str):
                evidence = [evidence]

            report = {
                "root_cause": str(
                    verification.get("root_cause")
                    or hypothesis.get("root_cause", "")
                ),
                "evidence": [str(e) for e in evidence],
                "confidence": verification.get("confidence", 0),
                "_meta": {
                    "target": "solution",
                    "incident": incident_id,
                    "model": self.model,
                    "api_calls": self.api_calls,
                    "elapsed_seconds": round(elapsed, 3),
                    "usage": dict(self.usage),
                    "verdict": verification.get("verdict"),
                    "rehypothesis_rounds": rounds,
                    "mechanism": hypothesis.get("mechanism", []),
                    "ruled_out": hypothesis.get("ruled_out", []),
                    "red_herrings": verification.get("red_herrings", []),
                    "cross_file_value_checks": verification.get(
                        "cross_file_value_checks", []
                    ),
                    "dropped_citations": verification.get("dropped_citations", []),
                    "added_evidence": verification.get("added_evidence", []),
                    "unresolved": verification.get("unresolved", ""),
                    "trajectory_jsonl": str(jsonl_path.relative_to(REPO_ROOT)),
                    "trajectory_md": str(md_path.relative_to(REPO_ROOT)),
                },
            }
            log.log_event("final_report", report=report)
            return report
        finally:
            log.close()
            render_to_markdown(jsonl_path, md_path)


def run_solution(
    incident_dir: str | Path,
    model: str | None = None,
    client: anthropic.Anthropic | None = None,
) -> dict[str, Any]:
    """Entry point used by evals/run_eval.py."""
    return IncidentAgent(model=model, client=client).investigate(incident_dir)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    result = run_solution(argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
