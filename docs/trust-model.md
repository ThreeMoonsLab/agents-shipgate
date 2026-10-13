# Trust Model

Agents Shipgate is designed as a local static scanner.

## Default Invariants

By default Agents Shipgate does not:

- import user project code;
- execute agent code;
- call tools;
- call LLMs;
- connect to MCP servers;
- resolve remote or local filesystem OpenAPI `$ref` values;
- make network calls;
- start any process other than the ones listed under
  [Process Boundaries](#process-boundaries);
- import third-party check or adapter plugins, from any command — the MCP
  server's read-only tools and `capability export` included;
- collect telemetry.

## Input Parsing

- YAML is parsed with `yaml.safe_load`.
- Declared local input paths are resolved relative to the manifest directory and rejected if they escape that base directory. This prevents a manifest from reading sibling repositories or absolute host files unless the source is first copied or symlinked into the reviewed workspace.
- OpenAPI `$ref` resolution only follows internal JSON pointers beginning with `#/`.
- `file://`, `http://`, and other external `$ref` values are left as inert schema values.
- Workspace discovery for `detect` and auto-init may invoke the local `git`
  binary (`rev-parse` and `ls-files`) with short timeouts to enumerate
  tracked/unignored files. This does not contact remotes, execute user code,
  run framework CLIs, connect to MCP servers, call tools, call models, or
  affect scan verdict semantics. If `git` is unavailable or fails, Agents
  Shipgate falls back to local filesystem walking with skip rules.
- OpenAI Agents SDK enrichment uses `ast.parse` only. It does not import or execute Python modules.
- `openai_api` artifacts are local prompt, JSON, YAML, or JSONL files. Agents Shipgate parses them locally and does not call OpenAI APIs, validate model availability, estimate pricing, or execute traces.
- Google ADK support is static-only. Agents Shipgate parses Python AST and Agent Config YAML, but does not import ADK code, run `adk run`, run `adk web`, run `adk eval`, connect to MCP servers, call tools, call models, or fetch remote specs by default.
- LangChain/LangGraph and CrewAI support is static-only. Agents Shipgate parses Python AST, but does not import framework packages, run graphs, crews, or agents, call models, call tools, execute `exec`/`eval`, connect to MCP servers, shell out to framework subprocesses, or make network calls.
- ADK callbacks and plugins are recorded as static guardrail evidence only. They are not proof that runtime enforcement is correct.
- Codex plugin support is static-only. Agents Shipgate parses
  `.codex-plugin/plugin.json`, local marketplace files, skills, `.mcp.json`,
  `.app.json`, hook config files, and explicit local MCP inventories, but does
  not install plugins, launch MCP server commands, execute hooks, authenticate
  connectors, call tools, call models, or make network requests.
- Codex plugin findings do not prove runtime installation, connector
  authentication, marketplace availability, MCP server behavior, or runtime
  permission enforcement. Findings emitted from `codex_plugin_surface`
  describe the static plugin contract only; a running plugin may diverge from
  its manifest.
- Declarative policy packs are local YAML data. They do not import Python,
  execute code, connect to services, or weaken the default no-execution model.
- Third-party check and adapter plugins are disabled by default. Setting `AGENTS_SHIPGATE_ENABLE_PLUGINS=1` opts into importing and running installed plugin entry points. Every command that can load them honours the same opt-in, including `capability export` and the MCP server's `shipgate.capabilities` tool, and a capability lock records the effective value in `source.plugins_enabled`. Use `--no-plugins` (`no_plugins` on the MCP tools) to force plugins off even when the environment variable is set.

## Process Boundaries

Agents Shipgate starts a child process at the call sites below and nowhere
else, and none of them uses a shell. Each call site
under `src/agents_shipgate/` is pinned with its rationale in
`tests/test_adapter_static_only.py::ALLOWED_EXCEPTIONS`, the audited meta-CLI
allowlist that `STABILITY.md` § Trust-model invariants summarises, and
`test_trust_model_lists_every_process_boundary` in the same file fails when a
spawn site is added, removed or moved to another function without this table
changing with it. Paths are relative to `src/agents_shipgate/`, except the hook
runner, which `install-hooks --target claude-code` writes into a repository.

| Call site | What it runs | Reached by |
| --- | --- | --- |
| `cli/bootstrap.py::_run_step` | Shipgate's own CLI as `<sys.executable> -P -m agents_shipgate` (`detect`, `init`, `scan`, `apply-patches`), with the workspace as the working directory. `-P` keeps that directory off the child's `sys.path`, so a `yaml/` or `agents_shipgate/` package committed in the workspace is never imported in place of the real one. | `bootstrap` |
| `cli/discovery/artifacts.py::_git_candidate_files` | `git rev-parse --show-toplevel`, with a sanitised environment and a 2-second timeout. | workspace discovery in `detect` and auto-init |
| `cli/discovery/artifacts.py::_run_git_inventory_bounded` | `git ls-files`, drained under byte and wall-clock bounds. | workspace discovery in `detect` and auto-init |
| `cli/verify/git.py::_run_process` | Local Git plumbing: ref and merge-base resolution, `pack-objects`, `index-pack` and `fsck` into an isolated object store, and `cat-file` reads of that store. It never fetches. | every command that reads Git state, among them `verify`, `check`, `diff` and `trigger` |
| `cli/verify/git.py::_run_git_bounded_result` | Bounded Git reads: diffs, changed paths, attributes and inventories. | the same commands |
| `cli/fixture.py::_git` | `git init`, `config`, `add`, `commit` and `update-ref` in a temporary copy of a bundled fixture. | `fixture run ai_generated_refund_pr` |
| `core/authorization_execution.py::_run_process` | The host-protected `/usr/bin/git`, including the one network-capable process: an exact `--force-with-lease` HTTPS `git push` of an externally signed commit graph. | `authorization execute` only |
| `core/authorization_execution.py::_run_bounded_stdout_process` | `git pack-objects`, streamed into the isolated graph snapshot under output and time bounds. | `authorization execute` only |
| `core/openshell_native.py::_execute` | The operator-pinned native containment prover, copied into a private directory and run with a minimal environment, bounded output and a bounded process-group lifetime. | `verify --openshell-proof-config` only |
| `.claude/hooks/agents-shipgate.py::_unchanged_instruction_preview` | Shipgate's `preflight`, through `AGENTS_SHIPGATE_CLI` or else `agents-shipgate`. | Claude Code hook events, after `install-hooks` |
| `.claude/hooks/agents-shipgate.py::_run` | Git, and Shipgate's `verify`, `diff` and `trigger` the same way. | Claude Code hook events, after `install-hooks` |
| `.claude/hooks/agents-shipgate.py::_run_git_bounded` | Bounded Git reads. | Claude Code hook events, after `install-hooks` |

`AGENTS_SHIPGATE_CLI` is used as written. If you set it to a `python -m`
spelling, include `-P` (`python3 -P -m agents_shipgate`): the hooks run it from
the repository root, where plain `python -m` would import a package of the same
name committed there first. Recovery commands that a `python -m agents_shipgate`
run prints carry `-P` for the same reason.

## Privacy Boundary

- Reports and Release Evidence Packets are local artifacts only. Agents
  Shipgate does not upload tool schemas, prompts, manifests, reports, or packet
  files.
- Public outputs are redacted by default before being written: report JSON,
  Markdown, SARIF, packet JSON/Markdown/HTML/PDF, GitHub step summaries,
  `explain-finding` output, and JSON logs.
- Redaction is best-effort pattern/key based, not a complete secret scanner.
  It preserves source paths, JSON pointers, scopes, and other local routing
  metadata while replacing secret-like substrings with `[REDACTED:<kind>]`.
- v0.18+ reports include `privacy_audit`, which records rules and inventory
  versions, covered output surfaces, and aggregate redaction counts by
  structural path. It never stores raw values or raw-value hashes.

## Plugin Trust Boundary

Plugins are Python code. When plugin loading is enabled, Agents Shipgate imports every installed non-core entry point in the `agents_shipgate.checks` and `agents_shipgate.adapters` groups, runs callable checks from the first, and lets adapters from the second load tool sources. The default zero-execution guarantee stops at that opt-in boundary. Only `AGENTS_SHIPGATE_ENABLE_PLUGINS` enables loading; no command or MCP tool turns it on by itself.

JSON and Markdown reports include loaded plugin provenance so CI reviewers can see which third-party check packages contributed to the scan. Treat plugin packages like other CI dependencies: pin versions, audit transitive dependencies, and avoid enabling plugins in untrusted environments unless those packages are approved.

## Failure Policy

Declared input sources should fail closed when parsing cannot complete. Required source parse errors return CLI exit code `3`. Optional source parse errors are recorded as source warnings and the scan continues. Policy gate failures use exit code `20`, keeping them distinct from configuration (`2`), input parsing (`3`), and internal (`4`) errors.

## Known Limits

Static analysis does not verify runtime tool routing, actual model behavior, external authorization enforcement, tool execution results, or prompt-injection resistance of returned tool content.

Verbose logs may include tool names, source counts, and risk-hint evidence. Treat `--verbose` output as release-review metadata.

## Diagnostics

Use `agents-shipgate doctor --config shipgate.yaml` to validate the manifest and enumerate declared sources without running checks. Use `agents-shipgate scan --verbose` for per-source and risk-hint debug logs. Set `AGENTS_SHIPGATE_LOG_FORMAT=json` to emit structured JSON logs to stderr.
