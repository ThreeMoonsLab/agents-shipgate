# Claude Code Instructions

The full agent-facing instructions for this repo live in [`AGENTS.md`](AGENTS.md). Everything there applies to Claude Code.

A few Claude-specific notes:

## Running the CLI in this repository

Use `./shipgate <command> ...` from the repository root — the repository launcher, and the canonical command here (`python shipgate <command> ...` on Windows, which does not read a shebang). It is the same CLI as `agents-shipgate`, but it runs this working tree's `src/`, picks a supported interpreter (the main checkout's virtualenv when you are in a `git worktree`), and needs no install and no `PYTHONPATH`. The recovery commands it prints name the launcher, so you can run them as printed.

A bare `agents-shipgate` may resolve to a different, older install on `PATH`, or fail with `ModuleNotFoundError` if the environment behind it is gone. Use it only to check what an installed build does. If a command behaves as though an edit never happened, run `./shipgate doctor --config shipgate.yaml --json` and read the `environment` block — it names the interpreter, the imported package, the checkout, and any `mismatches[]`.

## Permissions

- `agents-shipgate scan`, `preflight`, `init`, `doctor`, `explain`, `list-checks`, `fixture`, `self-check` are **read-only** with respect to user code; safe to run without confirmation.
- `agents-shipgate init --write` writes `shipgate.yaml` in the workspace. Confirm before running on an unfamiliar repo.
- `agents-shipgate init --write --agent-instructions=...` writes/updates `AGENTS.md`, `CLAUDE.md`, `.cursor/rules/agents-shipgate.mdc`, and the PR template via managed-block markers. Idempotent; confirm before running on an unfamiliar repo.
- `agents-shipgate baseline save` writes one JSON file under `.agents-shipgate/`. Safe to run; reversible.

## Output handling

Prefer `--json` on every command and parse the result programmatically. Do not scrape stdout when a JSON form exists. The stable JSON shape is the contract.

For `scan`, parse `agents-shipgate-reports/report.json` directly — that's where the structured output lives. The stdout summary is for humans.

## Slash command

A `/shipgate` slash command is registered at [`.claude/commands/shipgate.md`](.claude/commands/shipgate.md). It runs the full bootstrap flow.

## Skills

When invoking the CLI from a skill, set `AGENTS_SHIPGATE_AGENT_MODE=1` so errors include a structured `next_action` JSON line on stderr.

## Surface discipline

Before adding any new public surface — a CLI command or sub-app, a schema version, a report/`verifier.json` summary block, an agent-discovery surface, or a framework adapter — follow the gate in [`CONTRIBUTING.md` § Surface discipline](CONTRIBUTING.md#surface-discipline): name the headline metric it moves, prefer extending the one decision engine over adding a parallel one, and respect the [roadmap non-goals](ROADMAP.md#explicit-non-goals). When it is unclear which metric a new surface moves, default to not adding it and open an issue instead. Deleting or consolidating surface needs no headline-metric justification, but removing or renaming surface that already shipped in a tagged release (e.g. a check ID or a stable JSON field) still follows the deprecation and compatibility rules in [`STABILITY.md`](STABILITY.md) — a shipped check ID is deprecated over a minor cycle, never hard-removed.

Changing an *existing* surface is governed by [`docs/distribution-surfaces.md`](docs/distribution-surfaces.md): the registry of every surface this engine is published through, what each one claims, and which test proves it. Editing a surface means updating its row there and its claims in `tests/test_distribution_surface_parity.py` — the two are checked against each other. A surface that cannot be brought to parity needs a *Known parity gaps* row with an owning issue, never a silent exemption.
