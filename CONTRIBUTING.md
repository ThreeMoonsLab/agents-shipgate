# Contributing

Yes, please contribute.

Start architecture or release-contract work with the current
[accepted-decision index](docs/decisions.md). It links the governing contracts,
the v0.1 defaults they supersede, and accepted directions that still have open
implementation obligations.

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
python scripts/regenerate_goldens.py --check
```

### Sample goldens

The [golden generator](scripts/regenerate_goldens.py) owns the 24 committed
artifacts in seven `samples/*/expected/` directories. With the normal development
dependencies and Git available:

```bash
python scripts/regenerate_goldens.py                       # regenerate all
python scripts/regenerate_goldens.py conductor_agent       # one sample
python scripts/regenerate_goldens.py --check                # read-only drift check
```

It scans disposable copies with the source-tree writers; the caller's working
tree, manifests, declarations and CI summary are not scan output destinations.
Generation validates the entire selected set before writing any expected file.
Exit 0 means generated/matching, 1 means drift in check mode, and 2 means the
recipe could not finish. Every drift names its file. An added expected artifact
requires an explicit recipe; deleting one does not remove it from the check.
`tests/test_regenerate_goldens.py` invokes the actual `--check` command in the
normal CI suite. Existing behavioral assertions remain independent oracles;
do not regenerate away a changed verdict, open question or safety regression.

The recipe pins packet time, preserves the ordinary and cold-manifest states,
uses relative output paths under the manifest, normalizes only known path
fields, and writes LF bytes. Text inputs in the disposable copy are normalized
to LF too, so a Windows checkout does not change the manifest bytes bound by
the sample pointer. Scans disable installed plugins and isolate inherited Git
configuration; symlinked fixture paths and leaked temporary paths are errors. The
Conductor scan pointer is rebound **after** normalization and has no predecessor
from another generating run; it retains scan-only permissions, never a verifier
receipt or release authority. These development fixtures are not qualification
evidence. #569 still owns the actual report 1.0 freeze and migration fixtures.

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

Once a surface exists, [`docs/distribution-surfaces.md`](docs/distribution-surfaces.md)
governs what it is allowed to *say*: every surface that answers a question the
engine also answers must give the engine's answer, or record in that registry
what it does not answer. Adding or changing one means adding its row there and
its claims in `tests/test_distribution_surface_parity.py` — the two are checked
against each other, so neither can be updated alone. A surface that cannot be
brought to parity is a **gap**: it needs a row in that document's *Known parity
gaps* table with an owning issue, and an `xfail(strict=True)` row in the parity
test, so the day the fix lands the exemption fails and has to be retired.

### The adoption freeze (in force)

Until the [Adoption M2 exit](https://github.com/ThreeMoonsLab/agents-shipgate/issues/645)
is recorded, three additions are refused outright: a **new check ID**, a
**new versioned schema family**, and a **new input adapter**.
`scripts/check_surface_freeze.py` runs on every pull request and fails on
one.

Everything else is unaffected: bug fixes, readers and renderers for
families that already exist, and the milestone's own work. To add one
anyway, apply the `freeze-exception` label and answer the three questions
above in the PR body — the label makes it a decision somebody made rather
than one that happened.

Why now: the project has shipped surface faster than it has proven the
surface it already has. Measured over the 30 days to 2026-09-10 — 130
merged pull requests, 372,000 added lines, 144 check IDs and 27 schema
families, with 27 of 63 open issues filed as byproducts of reviewing that
work, and none of it reaching users because the published build was two
months old.

The same check reports a **review budget**: at most 800 added lines,
excluding goldens and generated files. It is reported, never enforced. A
number nobody agreed to should not block a merge, and the author who
happens to trip it is rarely the one who can shorten the change. Split it
where you can; where you cannot, say in the PR body why it is worth reading
whole.

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
After a report or contract change, run
`python scripts/regenerate_goldens.py`, inspect the semantic diff, then run
`python scripts/regenerate_goldens.py --check` and the affected behavioral
tests. The [sample recipe](#sample-goldens) owns path and digest normalization;
do not update a version stamp or pointer hash by hand.

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
