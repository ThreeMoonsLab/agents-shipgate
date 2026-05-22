# Harness families

`harness/` is the developer-only home for **harness families** — focused, local infrastructure that measures something about Shipgate's behavior or its adoption. It is **not packaged** into the `agents-shipgate` wheel.

The first family is [`harness.adoption`](adoption/), which drives coding agents (Claude Code, Codex, Cursor) across a matrix of (archetype, variant, prompt) cells and scores their behavior against the [adoption rubric](../docs/agent-adoption-harness.md).

This README documents the **layout convention** so future families — perf regression, false-positive baseline, framework-version drift, etc. — can be added with a shared shape and a shared dispatcher.

## Convention

A subpackage `harness/<name>/` is recognized as a harness family iff it satisfies all three rules:

| # | Rule | Why |
|---|------|-----|
| 1 | `harness/<name>/__init__.py` exists with a **non-empty docstring**. The first line becomes the family's one-line description in `python -m harness list`. | Discoverability + human-readable inventory. |
| 2 | `harness/<name>/cli.py` exists and exposes `app` — typically a `typer.Typer` instance, but any zero-arg callable suffices. | Single, predictable entry point that the dispatcher can introspect without running the harness. |
| 3 | `harness/<name>/__main__.py` exists and calls `app()`. | `python -m harness.<name>` is a working invocation regardless of how the dispatcher evolves. |

The convention is pinned by [`tests/harness/test_harness_layout.py`](../tests/harness/test_harness_layout.py) — every subpackage that LOOKS like a family but misses one of the three files fails the contract test loudly.

## Discovery and dispatch

```bash
# Show usage + every discovered family
python -m harness --help

# Tab-separated one-per-line listing (for piping)
python -m harness list

# Forward to a family's own CLI (identical to ``python -m harness.<name> ...``)
python -m harness adoption smoke
python -m harness adoption run --matrix benchmark/matrix.yaml
```

Forwarding is done via `subprocess` so the family's own `sys.argv[0]` matches a direct invocation exactly. Typer/Click `--help` output is byte-identical between `python -m harness.adoption --help` and `python -m harness adoption --help`.

The dispatcher returns:

- `0` on a successful forward (or when the family's own exit is `0`).
- The family's own exit code on a forwarded run.
- `2` if you name an unknown harness (config-error convention, mirrors `agents-shipgate scan` exit codes).

## Adding a new harness family

1. Pick a snake_case name. Examples: `perf_regression`, `false_positive_baseline`, `framework_version_drift`.
2. Create the three required files:
   ```
   harness/<name>/__init__.py     # docstring describes what the harness measures
   harness/<name>/cli.py          # exports ``app`` (Typer recommended)
   harness/<name>/__main__.py     # bootstrap sys.path, then ``app()``
   ```
   Use [`harness/adoption/__main__.py`](adoption/__main__.py) as the template for the `sys.path` bootstrap. Skipping that bootstrap means a sibling worktree's editable install can shadow the working tree under test.
3. Add any new shared runtime deps to [`harness/requirements.txt`](requirements.txt). Per-family `requirements.txt` files are not currently supported — if your family has conflicting deps, put it in a separate venv.
4. Drop tests under `tests/harness/`. The layout contract test picks the new family up automatically — no test wiring needed.
5. Document the rubric / what-it-measures in either:
   - the family's `cli.py` docstring (short),
   - `harness/<name>/README.md` (medium), or
   - `docs/agent-<name>-harness.md` (long, for adoption-class families).

## What goes UNDER a family

Anything family-internal. The dispatcher only scans the top level of `harness/`. The adoption family uses:

```
harness/adoption/
├── __init__.py      # docstring (rule 1)
├── __main__.py      # ``python -m harness.adoption`` (rule 3)
├── cli.py           # exports ``app`` (rule 2)
├── context.py
├── matrix.py
├── overlay.py
├── workspace.py
├── drivers/         # pluggable drivers per agent IDE
├── observer/        # transcript / fs / redaction
├── scorer/          # rubric application
└── scripts/         # fixture sync, etc.
```

There is no requirement to mirror this layout. A leaner family (one cli.py + a single scorer module) is fine. A larger family can grow its own subdirectories.

## What harnesses are NOT

- **Not packaged.** Harnesses ship inside the repo but never inside the wheel. The `[project]` table in `pyproject.toml` does not include `harness/` in its sdist or wheel.
- **Not part of the public API.** Internal modules under `harness/<name>/` can change shape between releases without a STABILITY contract bump. The only stable surface is the **layout convention** documented here.
- **Not a replacement for unit tests.** Harnesses measure end-to-end behavior on realistic inputs (cold-agent runs, perf regressions on real repos, etc.). Use `tests/` for invariants on small inputs.

## Where this convention is enforced

- **Layout contract**: [`tests/harness/test_harness_layout.py`](../tests/harness/test_harness_layout.py) — parametrized over `discover_harnesses()`. A new family that satisfies the convention is automatically covered.
- **Discovery code**: [`harness/__init__.py`](__init__.py) defines `HarnessSpec` and `discover_harnesses()`.
- **Dispatcher**: [`harness/__main__.py`](__main__.py) implements the `python -m harness ...` entry points.
