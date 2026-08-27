"""Trajectory logging for solution-agent runs.

Two pieces:

  * `TrajectoryLogger` — append-only JSONL writer. Each call records one event
    (prompt / llm_response / tool_call / tool_result) with a UTC timestamp.
  * `render_to_markdown` — turn a JSONL trajectory file into a readable
    Markdown transcript for review / committing.

Example
-------
    from solution.trajectory_logger import TrajectoryLogger, render_to_markdown

    log = TrajectoryLogger("trajectories/solution-agent/run-001.jsonl",
                           run_id="run-001", metadata={"model": "claude-sonnet-5"})
    log.prompt("Extract the invoice fields as JSON.")
    log.llm_response("Sure, here is the JSON...", tokens={"in": 812, "out": 143})
    log.tool_call("read_file", {"path": "invoice.txt"})
    log.tool_result("read_file", "Invoice #4471 ...")
    log.close()

    render_to_markdown("trajectories/solution-agent/run-001.jsonl",
                       "trajectories/solution-agent/run-001.md")
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

EVENT_TYPES = ("prompt", "llm_response", "tool_call", "tool_result")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TrajectoryLogger:
    """Append JSONL events to a trajectory file."""

    def __init__(
        self,
        path: str | Path,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self._fh = self.path.open("a", encoding="utf-8")
        self._closed = False
        if metadata is not None or run_id is not None:
            self._write(
                "run_start",
                {"run_id": run_id, "metadata": metadata or {}},
            )

    # -- low-level ---------------------------------------------------------
    def _write(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._closed:
            raise RuntimeError("TrajectoryLogger is closed")
        record = {
            "ts": _now_iso(),
            "type": event_type,
        }
        if self.run_id is not None:
            record["run_id"] = self.run_id
        record.update(payload)
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()

    def log_event(self, event_type: str, **payload: Any) -> None:
        """Log an arbitrary event type (escape hatch)."""
        self._write(event_type, payload)

    # -- typed helpers --------------------------------------------------
    def prompt(self, content: str, **extra: Any) -> None:
        self._write("prompt", {"content": content, **extra})

    def llm_response(self, content: str, **extra: Any) -> None:
        self._write("llm_response", {"content": content, **extra})

    def tool_call(self, name: str, arguments: Any = None, **extra: Any) -> None:
        self._write(
            "tool_call",
            {"name": name, "arguments": arguments, **extra},
        )

    def tool_result(
        self, name: str, result: Any = None, is_error: bool = False, **extra: Any
    ) -> None:
        self._write(
            "tool_result",
            {"name": name, "result": result, "is_error": is_error, **extra},
        )

    # -- lifecycle ------------------------------------------------------
    def close(self) -> None:
        if not self._closed:
            try:
                self._write("run_end", {})
            finally:
                self._fh.close()
                self._closed = True

    def __enter__(self) -> "TrajectoryLogger":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


# -- rendering --------------------------------------------------------------
def _iter_events(jsonl_path: str | Path) -> Iterable[dict[str, Any]]:
    with Path(jsonl_path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def _fmt_value(value: Any) -> str:
    if value is None:
        return "_(none)_"
    if isinstance(value, str):
        return value
    return "```json\n" + json.dumps(value, indent=2, ensure_ascii=False) + "\n```"


def render_to_markdown(jsonl_path: str | Path, output_path: str | Path) -> Path:
    """Convert a JSONL trajectory file into a Markdown transcript."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = ["# Trajectory transcript", ""]
    lines.append(f"_Source: `{Path(jsonl_path).name}`_")
    lines.append("")

    for ev in _iter_events(jsonl_path):
        ts = ev.get("ts", "")
        etype = ev.get("type", "event")

        if etype == "run_start":
            lines.append("## Run start")
            lines.append("")
            if ev.get("run_id"):
                lines.append(f"- **run_id:** `{ev['run_id']}`")
            md = ev.get("metadata") or {}
            for k, v in md.items():
                lines.append(f"- **{k}:** {v}")
            lines.append("")
            continue

        if etype == "run_end":
            lines.append("## Run end")
            lines.append("")
            continue

        if etype == "prompt":
            lines.append(f"### 🧑 Prompt  \n`{ts}`")
            lines.append("")
            lines.append(_fmt_value(ev.get("content")))
            lines.append("")
        elif etype == "llm_response":
            lines.append(f"### 🤖 LLM response  \n`{ts}`")
            lines.append("")
            lines.append(_fmt_value(ev.get("content")))
            if "tokens" in ev:
                lines.append("")
                lines.append(f"_tokens: {ev['tokens']}_")
            lines.append("")
        elif etype == "tool_call":
            lines.append(f"### 🔧 Tool call: `{ev.get('name', '?')}`  \n`{ts}`")
            lines.append("")
            lines.append(_fmt_value(ev.get("arguments")))
            lines.append("")
        elif etype == "tool_result":
            flag = " (error)" if ev.get("is_error") else ""
            lines.append(
                f"### 📤 Tool result: `{ev.get('name', '?')}`{flag}  \n`{ts}`"
            )
            lines.append("")
            lines.append(_fmt_value(ev.get("result")))
            lines.append("")
        else:
            lines.append(f"### {etype}  \n`{ts}`")
            lines.append("")
            payload = {k: v for k, v in ev.items() if k not in ("ts", "type", "run_id")}
            lines.append(_fmt_value(payload))
            lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")
    return out


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Render a JSONL trajectory to Markdown.")
    parser.add_argument("jsonl_path")
    parser.add_argument("output_path")
    args = parser.parse_args()
    dest = render_to_markdown(args.jsonl_path, args.output_path)
    print(f"Wrote {dest}")
