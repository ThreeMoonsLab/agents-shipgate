# Static Host-Boundary Support

Agents Shipgate's zero-config boundary is a static configuration review. It
does not execute a coding agent, connect to MCP servers, call tools, import user
code, or verify that a host enforced the configuration at runtime.

`shipgate check` always evaluates every recognized changed repository surface.
The `--agent` option identifies the caller for routing and rerun commands; it is
not a host-coverage selector.

## Repository scope

Repository scope is deterministic and is the default for `check`, verification,
and `audit --host`.

| Adapter | Status | Repository surfaces | Static semantics |
|---|---|---|---|
| Codex | first-class | `.codex/config.toml`, `.codex/hooks.json` | sandbox, approvals, network, MCP/app approvals, hooks |
| Claude Code | first-class | `.claude/settings.json`, `.claude/settings.local.json`, `.mcp.json`, `CLAUDE.md`, Claude skills | permission modes/rules, sandbox/network, additional paths, MCP restrictions, plugins, hooks |
| Cursor | first-class | `.cursor/cli.json`, `.cursor/mcp.json`, `.cursor/rules/**` | Shell/Read/Write rules, MCP declarations, instruction trust roots |
| VS Code MCP | experimental | `.vscode/mcp.json` | MCP additions and changes; relevant changes fail closed |
| Shared/GitHub | first-class | `AGENTS.md`, Shipgate policies/state, skills, `.github/workflows/*` | instruction/gate weakening, workflow permissions and triggers |

A registered adapter reports `complete`, `not_applicable`, `partial`, or
`experimental` coverage. A relevant malformed, unreadable, binary, oversized,
external, symlinked, or unsupported input prevents a complete control result.
Path classification is case-insensitive so protected files cannot evade review
on macOS or Windows. Nested `.codex/**`, `.mcp.json`, and
`.github/workflows/**` copies remain protected for repository-wide drift and
trust-root review, even when the host only loads the root copy.

The boundary is intentionally fail-closed above the adapters' specialized
semantics. Most instruction, policy, skill, and workflow edits therefore route
to human review unless a dedicated rule can prove the change safe. This can be
noisier than the former Codex-only evaluator; it prevents an unclassified
cross-host trust-root edit from being reported as complete.

## Local-static audit scope

`shipgate audit --host --scope local-static` is an explicit local-machine
inventory. In addition to repository surfaces, it reads supported static user
and file/OS-managed sources such as Codex `$CODEX_HOME` configuration, Claude
Code user and file-based managed settings, the current workspace's static
Claude MCP entry, and Cursor's user CLI/MCP configuration.

Shipgate never runs dynamic policy helpers. Sources that cannot be reconstructed
deterministically are recorded as partial coverage rather than treated as
absent.

Both scopes explicitly exclude:

- transient permission prompts and approvals;
- command-line and environment overrides for a particular host invocation;
- host UI/session state;
- remote/server-delivered managed settings;
- runtime sandbox enforcement and operating-system behavior;
- the behavior or trustworthiness of an MCP server or tool.

The path and precedence fixtures are pinned to the vendor contracts for
[Codex configuration](https://developers.openai.com/codex/config-reference),
[Claude Code settings](https://code.claude.com/docs/en/settings), and
[Cursor CLI permissions](https://docs.cursor.com/cli/reference/permissions).

## Reading the result

For local control, parse `shipgate.agent_boundary_result/v2` and switch on
`control.state`. Review `input_coverage`, `host_coverage[]`, `affected_hosts[]`,
`issues[]`, and `excluded_scopes[]` before relying on the result.

When host inventory fails, `violations[].evidence.recovery` carries the observed
read phase, reason and source, plus configured limits when a resource bound is
known. The violation title and control explanation name the failed operation;
its recommendation names the input to restore before rerunning. Read failures,
directory inventory failures and final snapshot-validation failures remain
distinct. A final validation failure means the read could not be confirmed as
coherent; it does not by itself establish concurrent modification as the cause.
Existing inventory issue kinds and control permissions are unchanged. Older
snapshots without these private reader facts keep their generic recovery route.

Recovery text and paths are redacted and bounded to 512 characters. Raw
filesystem exception text and source contents are never copied into this
recovery evidence. Configured limits describe the bounds the failed operation
ran under, not a measured amount by which a particular bound was exceeded.
Failed runs never retry automatically or authorize continuation. Only an
explicit new valid run can replace the stop under the current-control protocol.

For inventory and drift, parse host-grants v0.2. An incomplete inventory cannot
be acknowledged as a baseline. A v0.1 baseline, scope mismatch, or incomplete
comparison is `incomparable`; `--fail-on-drift` exits 20 for both drift and
incomparability.
