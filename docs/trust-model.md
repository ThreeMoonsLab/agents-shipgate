# Trust Model

Agents Shipgate v0.1 is designed as a local static scanner.

## Default Invariants

By default Agents Shipgate does not:

- import user project code;
- execute agent code;
- call tools;
- call LLMs;
- connect to MCP servers;
- resolve remote or local filesystem OpenAPI `$ref` values;
- make network calls;
- shell out to subprocesses;
- import third-party check plugins;
- collect telemetry.

## Input Parsing

- YAML is parsed with `yaml.safe_load`.
- Declared local input paths are resolved relative to the manifest directory and rejected if they escape that base directory. This prevents a manifest from reading sibling repositories or absolute host files unless the source is first copied or symlinked into the reviewed workspace.
- OpenAPI `$ref` resolution only follows internal JSON pointers beginning with `#/`.
- `file://`, `http://`, and other external `$ref` values are left as inert schema values.
- OpenAI Agents SDK enrichment uses `ast.parse` only. It does not import or execute Python modules.
- `openai_api` artifacts are local prompt, JSON, YAML, or JSONL files. Agents Shipgate parses them locally and does not call OpenAI APIs, validate model availability, estimate pricing, or execute traces.
- Third-party check plugins are disabled by default. Setting `AGENTS_SHIPGATE_ENABLE_PLUGINS=1` opts into importing and running installed plugin entry points. Use `--no-plugins` to force plugins off for a scan even when the environment variable is set.

## Plugin Trust Boundary

Plugins are Python code. When plugin loading is enabled, Agents Shipgate imports every installed non-core entry point in the `agents_shipgate.checks` group and runs callable checks from those entry points. The default zero-execution guarantee stops at that opt-in boundary.

JSON and Markdown reports include loaded plugin provenance so CI reviewers can see which third-party check packages contributed to the scan. Treat plugin packages like other CI dependencies: pin versions, audit transitive dependencies, and avoid enabling plugins in untrusted environments unless those packages are approved.

## Failure Policy

Declared input sources should fail closed when parsing cannot complete. Required source parse errors return CLI exit code `3`. Optional source parse errors are recorded as source warnings and the scan continues. Policy gate failures use exit code `20`, keeping them distinct from configuration (`2`), input parsing (`3`), and internal (`4`) errors.

## Known Limits

Static analysis does not verify runtime tool routing, actual model behavior, external authorization enforcement, tool execution results, or prompt-injection resistance of returned tool content.

Verbose logs may include tool names, source counts, and risk-hint evidence. Treat `--verbose` output as release-review metadata.

## Diagnostics

Use `agents-shipgate doctor --config shipgate.yaml` to validate the manifest and enumerate declared sources without running checks. Use `agents-shipgate scan --verbose` for per-source and risk-hint debug logs. Set `AGENTS_SHIPGATE_LOG_FORMAT=json` to emit structured JSON logs to stderr.
