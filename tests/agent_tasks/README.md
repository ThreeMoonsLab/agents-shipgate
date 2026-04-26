# Agent task harness

End-to-end tests that measure whether AI coding agents (Claude Code, Codex, Cursor, Aider) can complete real tasks against Agents Shipgate.

Each task is a complete unit:

- `prompt.md` — the prompt given to the agent
- `starter_repo/` — a minimal repo for the agent to work in (copied to a tempdir per run)
- `expected/assertions.py` — verification: file paths to check, JSON fields to compare, exit codes to expect

## Layout

```
tests/agent_tasks/
├── conftest.py                          # shared harness
├── 01_install_and_scan/
│   ├── prompt.md
│   ├── starter_repo/
│   │   └── tools.json
│   └── expected/
│       ├── assertions.py
│       └── run.sh                       # deterministic baseline that should always pass
└── 02_fix_top_finding/
    └── ...
```

## Running

By default the test suite **does not invoke real agents** (that requires API keys and is not free). Instead, each task ships a `run.sh` that performs the same actions deterministically. The pytest harness exercises that path so we always know the task is well-formed.

```bash
python -m pytest tests/agent_tasks
```

To exercise an actual agent (Claude Code via the Anthropic API, Codex via OpenAI Responses, etc.), run the harness in `--agent` mode:

```bash
python -m pytest tests/agent_tasks --agent=claude-code
python -m pytest tests/agent_tasks --agent=codex
```

These require `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` and are gated to nightly/weekly cron in CI. They are not part of the standard PR test run.

## Adding a new task

1. Pick a number in sequence (`03_*`, `04_*`, ...).
2. Write `prompt.md` self-contained: an agent reading only this file should know what to do.
3. Build `starter_repo/` with the minimum files needed.
4. Write `expected/assertions.py` with a single function `def assert_outcome(workdir: Path) -> None:` that raises on failure.
5. Write `expected/run.sh` that executes the deterministic baseline (typically `agents-shipgate ...` + an explicit edit).
6. Add an entry to the compatibility-matrix README at the top of this directory.

## Why this exists

Without a measurement harness, "make Agents Shipgate easier for agents" is unfalsifiable. With one, every agent-mode regression is caught nightly.
