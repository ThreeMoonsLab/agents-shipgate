# Contributing

Yes, please contribute.

## Local Setup

```bash
python -m pip install -e ".[dev]"
pytest
```

After the editable install, the `agents-shipgate` / `shipgate` console scripts
run from your working tree. **Beware a stale shadow install.** If you run a bare
`agents-shipgate` or `python -m agents_shipgate` from a shell where another copy
is on `PATH` — pipx, a base conda env, a globally pinned older release — you can
silently execute an old build (we have seen `0.8.0` shadow a worktree, which
makes new subcommands look "missing"). To stay honest:

- Work inside the project venv and confirm with `agents-shipgate --version`.
- For a one-off, pin the exact version: `uvx agents-shipgate@<version> ...` or
  `pipx run agents-shipgate==<version> ...`.
- When in doubt, `agents-shipgate contract --json` prints the running build's
  version and contract, so you never reason against a version you don't have.

## Useful Commands

```bash
agents-shipgate init --workspace samples/support_refund_agent
agents-shipgate doctor --config samples/support_refund_agent/shipgate.yaml
agents-shipgate scan --config samples/support_refund_agent/shipgate.yaml
agents-shipgate list-checks
```

## Contribution Areas

- new deterministic checks;
- loader hardening and OpenAPI edge cases;
- docs and integration recipes;
- false-positive reduction tests;
- report/schema compatibility tests.

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
agents-shipgate list-checks
agents-shipgate explain YOUR-CHECK-ID
```

Good checks are narrow, evidence-backed, and easy to suppress with a reason when a team has intentionally accepted the risk.
