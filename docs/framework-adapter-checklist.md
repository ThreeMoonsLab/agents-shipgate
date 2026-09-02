# Framework Adapter Checklist

Use this checklist when adding or changing a framework adapter. Framework
support must stay consistent with the default Agents Shipgate trust model:
static file parsing only, no imports, no agent execution, no tool calls, no
model calls, no MCP connections, and no network access.

## Static Extraction

- Parse Python with `ast.parse` or parse declarative config with safe structured
  loaders only.
- Never use `import`, `exec`, `eval`, framework CLIs, subprocess execution, or
  package runtime APIs to discover tools.
- Reuse `inputs/common.py` for path containment, size limits, structured-file
  parsing, schema conversion, and stable tool IDs.
- For Python framework adapters, reuse `inputs/_python_framework.py` for
  source loading, inventory wrapping, AST ordering, duplicate handling, and
  shared `Tool` construction before adding framework-specific extraction logic.
- Keep file processing deterministic: manifest references in declared order,
  supplemental discovered files sorted by resolved path, and AST objects in
  source order.

## Tool Normalization

- Normalize each enumerated callable surface into `core.models.Tool`.
- Use source types that describe both framework and confidence source, such as
  `langchain_function`, `crewai_class_tool`, or `<framework>_inventory`.
- Preserve descriptions, parameter schemas, auth scopes, annotations, owners,
  and risk hints when they are statically knowable.
- Generate stable names and IDs so finding fingerprints and baselines are
  reproducible.

## Agent Bindings

- Emit one `Tool` per tool *definition*, never one per agent binding. Catalog
  observation identity is `(source_type, source_id, native_locator)`, so a tool
  object bound to several agents collides and aborts the scan; widening that
  identity with the agent name is not the fix, because it models one underlying
  action as several independent capabilities.
- Publish agent wiring as `LoadedToolSource.binding_observations`
  (`AgentBindingObservation`) rather than a per-tool annotation. A single-valued
  annotation can only ever name one of N binding agents, and catalog-controlled
  annotations must never become authority-bearing binding evidence.
- Keep unique tool counts and binding counts separately reportable, so one tool
  shared by three agents reads as one tool and three bindings.

## Confidence

- Set high confidence for explicit inventories and direct static function/class
  tools with names, descriptions, and schemas.
- Set medium confidence for static framework wrappers when some metadata is
  inferred.
- Set low confidence for prebuilt/config stubs whose runtime schema cannot be
  fully known from local source.
- Let `SHIP-INVENTORY-LOW-CONFIDENCE-PRODUCTION-SURFACE` cover production
  exposure of low-confidence tools instead of duplicating that condition in a
  framework-specific check.

## Determinism Boundary

- Declare a `coverage: ClassVar[SourceCoverage]` on the adapter class, beside
  the code that mints its confidences. Answer **every** declaration shape —
  `export_artifact`, `literal_registration`, `factory`, `dynamic_construction`
  — including with "this input has no such declaration". Generation fails on an
  adapter that declares none, and on a source type added to
  `AST_ONLY_SOURCE_TYPES` or `MCP_SOURCE_TYPES` that no cell emits.
- Declare only what the adapter *does*: the emitted `Tool.source_type`, the
  ceiling, the `extraction["surface"]` evidence, and the annotation flags (which
  must be keys `SURFACE_INCOMPLETE_ANNOTATIONS` actually names — a flag the
  engine ignores is inert, and inert reads as "surface complete"). What that
  means for a verdict is derived by asking the engine's own predicates — never
  restate it.
- Name `manifest_section` when the adapter also runs from a top-level manifest
  key. It is validated against `AgentsShipgateManifest`, and it is not always
  the adapter's own name (`anthropic_api` reads `anthropic`, `codex_plugin`
  reads `codex_plugins`, and `conductor` is per-scan with no section at all).
- Give a shape more than one cell (each with a `variant`) when the input has
  genuinely different routes through it, rather than publishing the lower
  ceiling for both.
- Regenerate `docs/determinism-boundary.{md,json}` with
  `python scripts/generate_schemas.py` and commit them; CI's `--check` run
  fails on drift.

## Source Priority

- Explicit local framework inventories should outrank static extraction.
- Static custom function/class tools should outrank low-confidence prebuilt or
  config stubs.
- Register new source types in the scan merge priority table before relying on
  duplicate-name resolution.

## Dynamic Surfaces

- Emit source warnings for dynamic or unresolved tool surfaces, including
  factory calls, loop-built lists, comprehensions, unresolved imports, and
  external schema classes that cannot be inspected safely.
- Add framework-specific findings only when static extraction cannot enumerate
  the surface and no explicit inventory resolves it.
- Keep warning ordering stable, normally by `(source_ref, line, message)`.

## Report Surface

- Add a typed artifact model to `ScanContext`, following the
  `google_adk_artifacts` pattern.
- Add an additive `frameworks.<framework>` report block with count fields and
  warnings.
- Update Markdown rendering through the generic framework renderer instead of
  adding copy-paste branches.
- Bump `report_schema_version` only for additive report fields, and keep
  manifest `version: "0.1"` unless the manifest schema itself changes.

## Tests And Fixtures

- Add focused unit tests for each supported source shape and each dynamic
  warning/finding path.
- Include a fixture whose top-level Python code would fail if imported.
- Add a sample under `samples/` with golden `report.md` and `report.json`.
- Add the fixture to `agents-shipgate self-check --json` when it is part of the
  supported install surface.
- Regenerate `docs/manifest-v0.1.json` and `docs/checks.json` with
  `python scripts/generate_schemas.py` after schema or check metadata changes.

## Documentation

- Document the manifest form, supported static patterns, unsupported dynamic
  patterns, trust boundary, source priorities, and remediation path through
  explicit inventories.
- Document the tested framework version range so users can map unsupported
  source shapes to version churn instead of scanner failure.
- Add check catalog entries for every new `SHIP-*` ID.
