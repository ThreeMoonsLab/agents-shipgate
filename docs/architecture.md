# Architecture

A single-page summary of the agents-shipgate codebase for new contributors
and AI coding agents extending the project.

## Pipeline

```
config/loader.py        →  loads & validates shipgate.yaml (Pydantic v2)
                        ↓
inputs/{mcp,openapi,    →  per-source loaders normalize into Tool objects
  openai_api,           ↓     each loader is pure (no network, no model)
  anthropic_api,
  google_adk,
  openai_sdk_static}.py
                        ↓
core/risk_hints.py      →  enriches tools with risk tags (read_only, write,
                        ↓     destructive, financial_action, ...)
core/context.py         →  ScanContext (manifest + tools + artifacts)
                        ↓
checks/*.py             →  each check is a pure function
                        ↓     ScanContext → list[Finding]
core/findings.py        →  build_report() assembles the ReadinessReport
                        ↓
report/{markdown,json,  →  formatters write to agents-shipgate-reports/
  sarif}.py             ↓
cli/scan.py             →  entry point; orchestrates the whole pipeline
```

## Module map

```
src/agents_shipgate/
├── cli/             Click entry points (scan, init, doctor, explain, ...)
├── config/          Pydantic schema + manifest loader
├── core/            Shared models (Tool, Finding, Report, ScanContext)
├── checks/          Per-category check functions (api, auth, doc, ...)
├── inputs/          Adapters: mcp, openapi, openai_*, anthropic_api, google_adk
├── report/          Output formatters: markdown, json, sarif
└── plugins/         Plugin loading machinery (off by default)
```

## Determinism

Two non-negotiable invariants:

1. **No network calls in core code paths.** Inputs are local files. Plugins
   can opt-in but are off by default.
2. **Same inputs → same report.** Findings appear in stable check-execution
   order; per-finding fingerprints are deterministic (excluding timestamps)
   so they are reproducible across runs and serve as the baseline key.

Coverage:

- **Property-based loader tests** (Hypothesis) in
  [`tests/test_property_loaders.py`](../tests/test_property_loaders.py)
  fuzz the input adapters with generated manifests and tool-source
  shapes.
- **Fingerprint-stability unit tests** in
  [`tests/test_findings.py`](../tests/test_findings.py) pin the report
  builder's deterministic fingerprint contract.

## Adding a new input adapter

1. Create `src/agents_shipgate/inputs/<adapter>.py` exposing
   `load_<adapter>_artifacts(config, base_dir) -> tuple[LoadedToolSource, Artifacts]`.
2. Reuse helpers from `inputs/common.py` (`load_structured_file`,
   `resolve_input_path`, `schema_to_parameters`, `stable_tool_id`).
3. Add the source type to `core/risk_hints.py:_KEYWORD_GATED_SOURCE_TYPES`
   so name-keyword classification fires.
4. Wire into `cli/scan.py` (`run_scan` and `inspect_sources`).
5. Add a sample fixture under `samples/` and golden expected reports.
6. Add tests in `tests/test_<adapter>.py`.

## Adding a new check

1. Add the check function to the appropriate `checks/<category>.py` file.
2. Register the check ID and metadata in `checks/registry.py:CHECK_METADATA`.
3. Add a test in `tests/`.
4. Document in `docs/checks.md` (and regenerate `docs/checks.json` via
   `python scripts/generate_schemas.py`).
5. **Do not change check IDs in published versions.** Always add new ones.

## Trust model

See [`trust-model.md`](trust-model.md). Headlines:

- No model invocation.
- No MCP server connections.
- Files outside the manifest directory are rejected (path-traversal containment).
- Files larger than 10 MB are rejected.
- Plugins off by default.

## Stability contract

See [`../STABILITY.md`](../STABILITY.md). Headlines:

- Manifest schema stable across `0.x`.
- Report JSON shape stable (additive changes only).
- Exit codes stable: `0` pass, `2` config error, `3` input parse error,
  `4` other error, `20` strict-gate failure.
- Check IDs never change in published versions.

## Related reading

- [`concepts.md`](concepts.md) — tool-use readiness in depth
- [`category.md`](category.md) — what an "agent release gate" is
- [`manifest-v0.1.md`](manifest-v0.1.md) — full manifest schema
- [`AGENTS.md`](../AGENTS.md) — agent-facing instructions
