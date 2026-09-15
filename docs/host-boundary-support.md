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
| Claude Code | first-class | `.claude/settings.json`, `.claude/settings.local.json`, `.mcp.json`, `CLAUDE.md`, Claude skills | permission modes/rules, sandbox/network, additional paths, MCP restrictions, plugins and their marketplaces (`extraKnownMarketplaces`), hooks |
| Cursor | first-class | `.cursor/cli.json`, `.cursor/mcp.json`, `.cursor/rules/**` | Shell/Read/Write rules, MCP declarations, instruction trust roots |
| VS Code MCP | first-class | `.vscode/mcp.json` | MCP servers; `sandbox` and per-server `sandboxEnabled`; `${input:…}` references by name, never value; `envFile` recorded as a limit; other top-level keys partial |
| Shared/GitHub | first-class | `AGENTS.md`, Shipgate policies/state, skills, `.github/workflows/*` | instruction/gate weakening, workflow permissions and triggers, remote step action references |

A registered adapter reports `complete`, `not_applicable`, `partial`, or
`experimental` coverage. A relevant malformed, unreadable, binary, oversized,
external, unresolved-symlink, or unsupported input prevents a complete control
result. An in-tree symlink at a boundary path is read at its target and recorded
in `resolved_through` (#700), and so is a link anywhere that points at an
in-tree file. A dangling link, a link that leaves the repository, or a
directory link outside the boundary paths refuses the whole comparison, even
when no host file sits behind it (#688). That refusal is deliberate (#659),
and it caused every widening the 1.0 host-config measurement missed.
Path classification is case-insensitive so protected files cannot evade review
on macOS or Windows. Nested `.codex/**`, `.mcp.json`, and
`.github/workflows/**` copies remain protected for repository-wide drift and
trust-root review, even when the host only loads the root copy.

The boundary is intentionally fail-closed above the adapters' specialized
semantics. Most instruction, policy, skill, and workflow edits therefore route
to human review unless a dedicated rule can prove the change safe. This can be
noisier than the former Codex-only evaluator; it prevents an unclassified
cross-host trust-root edit from being reported as complete.

### Known unread surfaces

These change what runs, or what it can reach, with a host's authority, but no
adapter reads them. Editing any of them produces no row and no coverage limit:

- **A composite action a workflow invokes** (`uses: ./.github/actions/<name>`).
  It runs inside the calling job, with that job's `permissions` and secrets, so
  adding a `run:` step to `.github/actions/<name>/action.yml` adds a command
  holding the caller's scopes. Only the workflow file is read (#701).
- **A script a hook command runs** (for example
  `.claude/hooks/session-start`). The hook entry is read; the file it executes
  is not, so editing the script changes what runs without changing the hook
  (#702).
- **Named secrets passed to a reusable workflow**
  (`secrets: { token: ${{ secrets.NAME }} }`). `secrets: inherit` is read; a
  named mapping is dropped before comparison, so pointing it at a different
  secret produces no row (#693).
- **The path of a remote MCP server's URL.** The host and query are compared
  and the path is not, because a webhook-style path can itself be the secret.
  Changing `/read` to `/admin` on the same host produces no row (#772).

Review changes to those files and fields as you would a change to the workflow,
hook or server entry that holds them.

A workflow step's remote action reference is read (#771). Each step's
`uses: owner/repo[/path]@ref` or `uses: docker://…` is listed on the workflow
grant with its job and step: the step's `id`, else its `name`, else
`steps[N]`. Adding, removing or changing one, such as moving
`actions/checkout` from a pinned SHA to `@main`, is a `changed` row that names
`job/step` on both sides. The reference is compared as text and never fetched,
so the row says different code runs with that job's token. It does not say a
scope was added, and it never marks the row as widening. Reordering or renaming
steps that declare the same references is quiet. A local `./…` reference is
not part of this read and stays unread (#701). An expression, a string in
neither form, or a non-string value is listed as `unresolved` with its reason:
its text is compared, and what it evaluates to is not. A `steps` value that is
not a list of mappings is listed as `unresolved` as well, and none of its text
is published. A reference containing credential-shaped text — a token such as
`ghp_…` or `AKIA…`, a credential assignment, or `docker://user:password@…`
userinfo — is published redacted and cannot be compared, so it
makes GitHub coverage partial and a comparison of that changed workflow refuses.

A hook row describes the file, not the host. A `hooks.json` found under
`.claude/hooks/` is reported as an `execute` grant even when no settings file
or plugin manifest references it, so the row does not establish that Claude
Code loads it (#714).

Skill and command frontmatter is read the way Claude Code documents it (#730).
Frontmatter is optional: a skill without it takes its name from the directory
and its description from the first non-empty line. Documented fields are
type-checked. An undocumented key (`version`, `author`, `category`, …) is
digested as written rather than refused, so changing it is still a change and
none is read as a permission. A Cursor rule still refuses a key outside
`description`, `globs` and `alwaysApply`.

## Local-static audit scope

`shipgate audit --host --scope local-static` is an explicit local-machine
inventory. In addition to repository surfaces, it reads supported static user
and file/OS-managed sources such as Codex `$CODEX_HOME` configuration, Claude
Code user and file-based managed settings, the current workspace's static
Claude MCP entry, and Cursor's user CLI/MCP configuration.

Shipgate never runs dynamic policy helpers. Sources that cannot be reconstructed
deterministically are recorded as partial coverage rather than treated as
absent.

Local layers are combined the way the host documents, not listed as though
each applied on its own. For Claude Code, permission rules, additional
directories and hooks merge across layers. A scalar setting keeps the highest
layer that sets it (managed, then project local, shared project and user), and
the remaining grant's `source` names that layer. A same-named MCP server keeps
the current workspace's local-scope entry over `.mcp.json`, and
`allowManagedPermissionRulesOnly` and `allowManagedHooksOnly` restrict rules
and hooks only from managed settings. A `defaultMode` of `auto` or
`bypassPermissions` in a project file is still listed beside the lower layer's
mode, because clients before v2.1.257 honored it. Where the documentation does
not settle a case — disagreeing `sandbox` or `enabledPlugins` values, or a
layer with no documented rank — and for Codex and Cursor layers, coverage stays
partial and the issue names the key and each layer.

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

For local control, parse `shipgate.agent_boundary_result/v3` and switch on
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
