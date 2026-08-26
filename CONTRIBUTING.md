# Contributing

Yes, please contribute.

## Local Setup

```bash
python -m pip install --require-hashes --requirement constraints/dev.txt
python -m pip install --require-hashes --requirement constraints/build-backend.txt
python -m pip install -e . --no-deps --no-build-isolation
pytest
```

`python -m pip install -e ".[dev]"` still works and is fine for a quick look,
but it resolves fresh, so it is not the environment CI and the release run. The
locked closure above is; reproducing a CI failure locally starts with it.

## Running the CLI

```bash
./shipgate --help
```

**`./shipgate` is the canonical command in this repository**, and the only one
the docs and the generated agent instructions use. On Windows the same file is
run as `python shipgate --help`: a shebang is a POSIX kernel feature, so
Windows will not start the file on its own. That is the only difference, and
the launcher knows about it — the recovery commands it prints there name
`<interpreter> shipgate`, so they stay runnable as printed.

It is the same CLI as `agents-shipgate`, and it needs no installation, no
activated virtualenv, and no `PYTHONPATH`:

- it runs **this** working tree's `src/`, ahead of any copy on `PATH`;
- it selects a supported interpreter — `AGENTS_SHIPGATE_PYTHON` if you set one,
  otherwise the project virtualenv, looked up in the main checkout too so a
  `git worktree` shares it;
- the commands it prints back name the launcher, so they run as printed.

**Why not a bare `agents-shipgate`.** It resolves through `PATH`, so a pipx
copy, a base conda env, or a globally pinned older release can silently execute
an old build — we have seen `0.8.0` shadow a worktree, which makes new
subcommands look "missing" — and a console script promoted from an environment
that no longer exists fails with `ModuleNotFoundError` before Shipgate can say
anything at all. Run the console script when you want to know what an
*installed* build does; run `./shipgate` when you want to know what your edit
does.

**When something looks wrong**, ask rather than guess:

```bash
./shipgate doctor --config shipgate.yaml --json
```

The `environment` block in each payload names the interpreter, the launcher and
every Shipgate console script on your `PATH`, where the imported package came
from, the installed and source-tree versions, and a `mismatches[]` list with a
runnable recovery command. `./shipgate contract --json` still prints the running
build's version and contract; for a one-off against a published release, pin it
with `uvx agents-shipgate@<version> ...` or `pipx run agents-shipgate==<version>
...`.

## Changing dependencies

`pyproject.toml` declares the ranges; `constraints/*.txt` pin what actually gets
installed, with hashes. After editing a requirement — in `pyproject.toml` or in
a `constraints/*.in` — regenerate and check:

```bash
python scripts/update_locks.py
python scripts/verify_dependency_lock.py
```

The second command also runs in CI and in release verification, so a lock left
stale fails there by name rather than as a puzzling import error. See
[`docs/release-runbook.md`](docs/release-runbook.md#the-environment-is-locked-and-it-is-cis)
for which job installs which lock.

## Changing the CHANGELOG

Entries go under `## Unreleased`. Cutting a release renames that heading to
`## <version> - <date>`, and **that section becomes the GitHub Release body** —
release verification refuses a tag with no matching section, so the changelog is
part of the release, not documentation about it.

## Useful Commands

```bash
./shipgate init --workspace samples/support_refund_agent
./shipgate doctor --config samples/support_refund_agent/shipgate.yaml
./shipgate scan --config samples/support_refund_agent/shipgate.yaml
./shipgate list-checks
```

## Contribution Areas

- new deterministic checks;
- loader hardening and OpenAPI edge cases;
- docs and integration recipes;
- false-positive reduction tests;
- report/schema compatibility tests.

## Surface discipline

Read this before adding a new public surface. This project has shipped surface
area faster than it has proven the surface it already has. Until the
verdict-accuracy benchmark and default-on activation land, the bar for new
surface is deliberately high.

A **new surface** is any of: a new CLI command or sub-app; a new
`report_schema_version` or other versioned schema; a new top-level report or
`verifier.json` summary block; a new agent-discovery surface; or a new framework
adapter.

Before adding one, the PR description must answer, in a sentence or two:

1. **Which headline metric does this move?** Blocked-recall, noise rate,
   activation rate, or time-to-first-verdict. "Completeness" and "consistency"
   are not metrics.
2. **Can an existing surface carry it?** Prefer extending the one decision
   engine (`release_decision.decision`) and its projections over adding a
   parallel one.
3. **Does it respect the [roadmap non-goals](ROADMAP.md#explicit-non-goals)?**
   No second verdict; more adapters is not the roadmap; no agent execution, LLM
   calls, or network access in the default path.

If the answer to (1) is unclear, the default is **don't** — open an issue
instead. Deleting or consolidating surface never needs this new-surface
justification — but removing or renaming surface that already shipped in a
tagged release (a check ID, a stable JSON field, a CLI flag) still follows the
compatibility and deprecation rules in [`STABILITY.md`](STABILITY.md): a shipped
check ID is deprecated for at least one minor cycle, never hard-removed.

## Adopter-facing copy

A separate rule from surface discipline, and easier to break by accident:
**a string an adopter is expected to act on must name something they can
open.** A file, a symbol, an agent, or a manifest key.

Internal identity vocabulary — `source_type`, `source_id`, `native_locator`,
observation ids, fingerprints, and derived `tool_v…` / `agent_v…` identifiers —
belongs in `report.json` evidence blocks, the tool catalog, and the
verification artifacts, where tooling reads it and precision is the point. It
does not belong in console output, the agent-mode `message` / `next_action` /
`next_actions[]`, `agent-handoff.json` prose, `fix_task.instructions[]`, or PR
comment text. Where an internal identifier is load-bearing for diagnosis, keep
it in the structured payload (`AgentsShipgateError.details`, the envelope's
`details` object, `EvidenceGap.subject_id`) and write the sentence in the
adopter's terms.

`source_id` and `source_type` are the awkward pair: both are real manifest
keys under `tool_identity.bindings[].members[]`, and `source_id` is one under
`tool_inventories[]` and `agent_bindings.root` too. Spelled with the surface
they belong to they are locatable; spelled bare they are the model leaking.

The rule and its two categories live in
[`core/adopter_text.py`](src/agents_shipgate/core/adopter_text.py);
[`tests/test_adopter_vocabulary.py`](tests/test_adopter_vocabulary.py) enforces
it. If you add a message builder to `core/source_warnings.py`, that test's
sweep table will tell you.

## Schema Changes

The JSON Schemas under `docs/` (`manifest-v0.1.json`, `checks.json`,
`report-schema.v0.<minor>.json`, `packet-schema.v0.<minor>.json`) are
**generated artifacts**, not hand-written. They are checked into the
repo so external consumers can validate against a stable URL.

If you change a Pydantic model — adding/removing a field, bumping
`report_schema_version`, editing `CheckMetadata` — you must regenerate
the schemas and commit them in the same PR:

```bash
python scripts/generate_schemas.py
git add docs/ && git commit
```

CI runs `python scripts/generate_schemas.py --check` and fails fast
with a unified diff if a committed schema drifts from the live model.
The same drift is also caught by `tests/test_schema_roundtrip.py`, so
your test suite will reject the change locally before CI does.

## Check Contributions

Checks should be deterministic, explainable, and covered by tests. Avoid LLM calls, network calls, user-code import, or runtime tool execution.

Each new check should include catalog metadata, a test fixture, and documentation in `docs/checks.md`.

## Adding A Check End To End

1. Create or update a module under `src/agents_shipgate/checks/`.
2. Implement a pure function with the shape `run(context: ScanContext) -> list[Finding]`.
3. Use `tool_finding(...)` or `agent_finding(...)` from `src/agents_shipgate/checks/base.py` so evidence, recommendations, and source references stay consistent.
4. Register the function and metadata in `src/agents_shipgate/checks/registry.py`.
5. Add a unit test that proves the check fires and a false-positive test that proves it does not fire on a nearby safe case.
6. Add the check ID, severity, and plain-language meaning to `docs/checks.md`.
7. Run:

```bash
pytest
./shipgate list-checks
./shipgate explain YOUR-CHECK-ID
```

Good checks are narrow, evidence-backed, and easy to suppress with a reason when a team has intentionally accepted the risk.
