# Project instructions for Claude Code

This is a **hackathon project**. Speed matters, but every claim must be backed by
evidence from the eval harness, not vibes.

## Ground rules

- **Keep the baseline and the solution clearly separated.** `baseline/` holds the
  unmodified starting approach and must not be changed to make the solution look
  better. All improvements go in `solution/`. If you need to change the baseline,
  call it out explicitly and explain why.
- **Always update `CHANGELOG.md` after a meaningful change.** Use the entry format
  already in that file: What I saw (evidence) / What I changed / What happened
  (measured result) / Next question. No entry without a measured result.
- **Never commit `.env` or credentials.** No API keys, tokens, or secrets in the
  repo, in code, or in committed logs. `.gitignore` already covers `.env`,
  `*.key`, and raw trajectory logs — keep it that way.
- **Log every solution-agent run.** Use the trajectory logger
  (`solution/trajectory_logger.py`) so each run appends a JSONL trajectory under
  `trajectories/`, then render it to Markdown for review. Raw `.jsonl` files are
  gitignored; commit the rendered `.md` transcripts.

## Layout

- `baseline/` — starting approach, frozen
- `solution/` — improved approach, prompts in `solution/prompts/`
- `evals/` — `run_eval.py` harness, `test_cases/*.json`, results in `evals/results/`
- `trajectories/` — `coding-agent/` and `solution-agent/` run logs
