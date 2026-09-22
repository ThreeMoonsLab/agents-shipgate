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
| Shared/GitHub | first-class | `AGENTS.md`, Shipgate policies/state, skills, `.github/workflows/*` | instruction/gate weakening, workflow permissions and triggers, remote step action references, named secret sources passed to reusable workflows |

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
adapter reads them. Editing any of them produces no row and no coverage limit, and
a file among them that no adapter reads is not listed under `What this run
established` either, which names only sources an inventory observed (#812):

- **A composite action a workflow invokes** (`uses: ./.github/actions/<name>`).
  It runs inside the calling job, with that job's `permissions` and secrets, so
  adding a `run:` step to `.github/actions/<name>/action.yml` adds a command
  holding the caller's scopes. Only the workflow file is read (#701).
- **A script a hook command runs** (for example
  `.claude/hooks/session-start`). The hook entry is read; the file it executes
  is not, so editing the script changes what runs without changing the hook
  (#702).
- **The path of a remote MCP server's URL.** The host and query are compared
  and the path is not, because a webhook-style path can itself be the secret.
  Changing `/read` to `/admin` on the same host produces no row (#772).
- **Hooks in subagent frontmatter** (`.claude/agents/*.md`). Claude Code runs
  them while that subagent runs; no adapter reads the file. A skill's `hooks`
  frontmatter is type-checked with the skill's instructions, never read as a
  hook grant, so its events get no hook row (#714).

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
`ghp_…` or `AKIA…`, a credential assignment, or registry userinfo such as
`docker://user:password@…`, whose password may itself hold `/`, `:` or `@` —
is published redacted and cannot be compared, so it
makes GitHub coverage partial and a comparison of that changed workflow refuses.

A named secret passed to a reusable workflow is read (#693). For a job that
calls a reusable workflow, each `secrets:` entry whose whole value is
`${{ secrets.NAME }}` is listed on the workflow grant as the called workflow's
secret input and the source name `NAME`; the secret's value is never read.
`${{ github.token }}` is read as the same source as
`${{ secrets.GITHUB_TOKEN }}`, because GitHub documents the two as functionally
equivalent, so moving between the spellings is quiet. Source names are compared
case-insensitively, as GitHub references them; the callee's secret id is
compared as written, because GitHub does not document it as case-insensitive,
so a case-only edit there reads as a removal plus an addition.
Adding or removing a destination, or pointing one at a different source name
(`STAGING_TOKEN` to `PRODUCTION_TOKEN`), is a `changed` row that names
`job/destination`. A name does not establish the secret's privilege, whether
the caller has it, or what the called workflow does with it, so the read
never marks the row as widening; `secrets: inherit` keeps its own widening
row. Reordering the entries or re-spacing or re-quoting the expression is
quiet. Any other value — a literal, another expression such as
`${{ secrets['NAME'] }}`, `${{ inputs.x }}` or `${{ SECRETS.X }}`, a
non-string, or a `secrets:` that is neither `inherit` nor a mapping — publishes
nothing of its value. That entry is a named, non-blocking limit that says which
`job/destination` it is, carried by the host inventory and printed under
`audit --host` → Coverage issues; `diff`, `verify` and `check` carry no limit
for it, exactly as on `1.0.0`. GitHub coverage stays complete, so `check`,
baselines and every other row on the file are unaffected, and adding, removing or
re-forming such an entry is still a row. Only an edit between two values of the
same unreadable form is not reported. A destination or source name, or a job's
reusable `uses:` target, containing credential-shaped text is published
redacted and refuses the same way a step reference does, so two values that
redact alike never compare as unchanged.

A workflow's labels are published redacted (#802). A job id, a step's `id` or
`name`, a trigger and a permission scope name go through the same redaction as
step text, and any userinfo after `scheme://` inside one is replaced, so a
job id shaped like `ghp_…` reads `[REDACTED:github_token]` and a step named
`Pull docker://ci:<password>@gcr.io/proj/img` reads
`Pull docker://<redacted>@gcr.io/proj/img`. Each label is computed once, where
the workflow grant is built, and used by every field and row that names it,
and `config_sha256` is computed over it. `check`'s workflow evidence is
derived from the raw declarations, which it still compares, and redacts
`evidence.job` and `evidence.scope` by the same rule. Ordinary names such as
`build`, `deploy-prod`, `secret-scan` or `token-refresh` are unchanged. A name
that only looks like a token, such as `sk-integration-tests-matrix`, is
redacted too, and so is userinfo that holds no credential, such as `git@` in
an `ssh://` URL. A single redacted label still compares and refuses nothing.
A token joined to a name by `_`, a letter or a digit is not recognised by the
redactor's word-boundary patterns, so `deploy_ghp_…` is published as written;
this predates #802.

Two distinct job ids or triggers in one workflow, or two scope names in one
`permissions` mapping that a job's permissions are read from, that publish
alike would compare as one, so they refuse the same way a redacted step
reference does, with a coverage issue that says to rename or remove one. That
limit lasts as long as the workflow holds both names, whether or not a pull
request changes it: `check` refuses on every run, because its boundary result
cannot carry a limit; `audit --host --save-baseline` exits `2`; and drift is
incomparable. Only `diff` and `verify` compare past an unchanged workflow and
name it in `unchanged_limits`. Two ordinary ids such as `sk-integration-tests-matrix` and
`sk-integration-tests-linux-arm` collide this way. Renaming one of them clears
it once the rename is on the base branch; the pull request that renames it
still refuses against its base.

Renaming a lone redacted label to another that redacts alike (`ghp_A…` →
`ghp_B…`) is not a row in `diff`, `check` or `verify`. The workflow file's
artifact `redacted_sha256` is still a digest of the whole parsed file, as on
`1.0.0`, so drift reports that rename, or an edit to only the password in a
step name, once as an artifact change with no grant change, and
`--fail-on-drift` exits `20`. `check` pairs jobs by their raw ids, so a
renamed job is compared with the top-level `permissions` rather than its own
earlier declaration: a `write-all` it declares blocks, and a scope it declares
`write` above a top-level `read` requires review as an expansion, with no row
beside either. A step label is read for userinfo only in a token holding
`scheme://`, so a scheme-less `user:password@host` in a step name is not read
as userinfo.

A hook row states its loading basis (#714). Parsing a hook file proves the
file exists, not that a host loads it, so hooks are published four ways:

- **Declared by a file the host loads for this scope**: Claude Code
  settings (`.claude/settings.json`, `.claude/settings.local.json`, user or
  managed settings) or Codex `.codex/hooks.json`. `access: execute`,
  `risk: high`, and adding or changing one is an expansion.
- **Selected by a plugin this repository's project settings enable.** A
  project settings file (`.claude/settings.json` or
  `.claude/settings.local.json`) sets `enabledPlugins` `<plugin>@<marketplace>`
  to `true`; a project settings file registers that marketplace in
  `extraKnownMarketplaces` (or its `additionalMarketplaces` alias) as a
  `directory` source whose relative `path` holds a
  `.claude-plugin/marketplace.json`, or a `file` source whose relative `path`
  is one; and that marketplace lists the plugin with a `./` or
  `metadata.pluginRoot` source. `<marketplace>` matches either the
  `extraKnownMarketplaces` key or the registered `marketplace.json`'s own
  `name`, which Claude Code's schema calls the identifier users see after the
  `@`. The settings documentation only shows the two equal, so neither is
  documented as the one `enabledPlugins` matches, and reading both errs toward
  showing a hook. The `name` never registers a marketplace on its own.
  Claude Code leaves only a plugin from an
  external source waiting for a manual install, so this plugin loads once the
  folder is trusted. Its selected hooks are `access: execute`, `risk: high`,
  and adding or changing one is an expansion, as in `1.0.0`. The row says the
  project settings enable the plugin.
- **Selected by a plugin** in the repository, without that enablement. The
  plugin's root is recognised by its `.claude-plugin/plugin.json`, or by a
  `.claude-plugin/marketplace.json` entry whose `source` is a `./` path or a
  bare name (no `/`) under `metadata.pluginRoot`. A plugin selects
  `hooks/hooks.json` at its root, a `./` path or array named by `hooks` in its
  manifest or marketplace entry, or hooks written inline in either, including
  a `strict: false` entry. `access: execute`, `risk: medium`, the event and
  every command change. It is not an expansion, and the row says whether the
  plugin is installed or enabled is not established.
- **Selected by nothing**, such as a standalone or nested
  `.claude/hooks/hooks.json`, which is not a location Claude Code documents.
  `access: unknown`, `risk: unknown`. Every change is still a row; none is an
  expansion.

A removal names no basis, because the grant it describes may come from a
baseline recorded before the basis was published. `1.0.0` recorded every hook
file as `execute`/`high`, the pair an enabled plugin's hook carries now, so a
removal never claims selection or enablement. A hook-file grant with any other
pair claims no selection.

No row is proof that a hook ran. What remains unread:

- Enablement is read only from the repository's project settings, and only
  for a marketplace registered inside the repository. A `github`, `git`,
  `url` or `settings` marketplace source, an absolute or home-relative path, a
  path leaving the repository, a plugin the marketplace does not list, and a
  value other than `true` establish nothing, and the plugin's hooks stay
  `medium`. A `true` in either project settings file counts, even when the
  other sets `false`: a `false` in `.claude/settings.local.json` is one
  machine's opt-out. Installation state, user settings and workspace trust are
  never read. A marketplace entry with a remote `source` names nothing in the
  repository.
- A reference is followed only to a file named `hooks.json`, or
  `<name>-hooks.json` (`_` or `.` also separate). In a plugin manifest, any
  other name, a path outside the plugin directory, a `hooks` member of the
  wrong type, and an unreadable manifest are blocking coverage limits. `diff`
  and `verify` refuse the comparison when the limit is new, changed or on one
  side only, and name a parse or shape limit when the manifest is unchanged.
  A read limit is never named as unchanged: an untouched plugin hook file over
  the read bound makes `diff` and `verify` incomparable, as an oversize
  settings file already did in `1.0.0`.
- The same problems in a marketplace entry are named without blocking, and so
  is a `metadata.pluginRoot` that is not a `./` path inside the marketplace.
  So are a reference to a file that does not exist, a reference into a
  directory the reader never walks (`node_modules`, `.venv` and the like), and
  a selected file with no `hooks` object. A reference beneath a link that
  leaves the workspace is covered by the blocking limit on that link alone.
- A reference matches a file case-insensitively when exactly one file
  matches, erring toward showing a hook a case-insensitive filesystem would
  load.
- `check` routes a changed `.claude/hooks/hooks.json` to protected-surface
  review whatever its basis. It does not route a plugin manifest, a
  marketplace or a plugin-selected hook file, so a plugin-reference limit
  never changes a `check` decision from what `1.0.0` gave. A hook an enabled
  plugin selects outside the registry paths therefore gets an expanding row
  under an `allow` decision, as `1.0.0` allowed the same change with no row
  (#809). Its host
  comparison leaves out a limit both sides share on an untouched source,
  because its result cannot name a limit. A limit only one side carries, or
  one on a source the change touched, makes that comparison incomparable
  (`base_inventory_incomplete` or `head_inventory_incomplete`), so no added or
  removed row is built from a manifest that could not be read. That refusal is
  repository-wide, so an unrelated row is withheld too (#808).

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

`complete` coverage for a changed host file means the file was read, not that
every key's meaning is modelled. A Claude Code or Cursor settings file that
parses but sets a top-level key outside the host-boundary rule allow-list (for
example `outputStyle`) is complete input; its
`SHIP-HOST-BOUNDARY-CONFIG-PARSE-FAILED` row, with evidence kind
`unknown_host_config_key` and the key's name, records the change and still owes
review. Some keys outside that allow-list are modelled elsewhere: inventory and
diff rows still describe `enabledPlugins` and `extraKnownMarketplaces` as
plugin and marketplace grants, while the boundary rules only record the key.
The Claude Code settings in [the setting table](#claude-code-setting-ratings)
are not unknown keys: `check` rates them by that table. A file that could not
be parsed or resolved is `partial` and never authorizes publication.

### Claude Code setting ratings

One table in the engine (`core/host_settings.py`) rates every Claude Code
setting the host inventory publishes as a `permission_mode` grant, and every
surface reads it (#827). The `audit --host` grant carries the rating as its
`risk`; a `diff`, `verify` or `check` row carries it as its `severity`, names
the setting and its value (`enableAllProjectMcpServers: true`,
`defaultMode: dontAsk`) and states the table's basis as its `why`; and `check`
raises one violation for each value a change sets, at the same rating, which
`verify` reports as a finding of that severity. Before, one value could carry
three answers: `enableAllProjectMcpServers: true` was a `critical` grant and row
but a `medium` "could not be parsed" violation, and `defaultMode: dontAsk` a
`medium` row but a blocking `critical` violation.

Each value is rated by what Claude Code documents it to do:

| Value | Rating | `check` raises |
| --- | --- | --- |
| `defaultMode: bypassPermissions` — skips every permission prompt | critical | `SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW` (blocks) |
| `skipDangerousModePermissionPrompt: true` — skips the confirmation before that mode starts | critical | `SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW` (blocks) |
| `enableAllProjectMcpServers: true` — approves every MCP server the project's `.mcp.json` declares | critical | `SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW` (blocks) |
| `defaultMode: acceptEdits` — accepts file edits without a prompt | high | `SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED` |
| `defaultMode: auto` — lets Claude Code approve tool calls itself | high | `SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED` |
| a `defaultMode` Claude Code does not document | high | `SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED` |
| an `enabledMcpjsonServers` entry — approves that one project server | high | `SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED` |
| `defaultMode: dontAsk` — denies every tool call no allow rule permits, instead of prompting | medium | `SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED` |
| `defaultMode: plan` or `default` | medium | `SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED` |
| `disableBypassPermissionsMode`, `disableAllHooks`, `allowManagedPermissionRulesOnly`, `allowManagedHooksOnly`, and `false` for the two `critical` switches | medium | `SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED` |

`dontAsk` removes prompts, but by refusing what no allow rule permits, not by
running it; this repository's own rater harness sets it to confine a session.
A value is rated the same whichever settings file sets it: `bypassPermissions`,
`auto` and `skipDangerousModePermissionPrompt` in a project file are rated as
though they take effect, as the inventory already lists them, because a static
audit cannot see whether the installed client predates the release that
ignores them there (see [Local-static audit scope](#local-static-audit-scope)).
A `medium` violation stays in the graded review band, so the coding agent is
routed to `verify` and the pull request still goes to a human. A rating is of
the value, not of the change: whether one mode is wider than the one it
replaced is not modelled, so every value a change sets is reviewed, and a
row's `⚠` still marks every added or changed mode. Removing a setting raises
nothing of its own, as removing `defaultMode` never did; the file is still a
protected surface and the removal is still a row. A host-boundary policy that
raises either rule above its default raises these violations with it; none can
lower them.

`enabledMcpjsonServers` is read as one grant per server name, so approving one
more server is one `high` row. An entry that names no server — an object, a
number, a blank string — is kept whole as its own `high` grant, row and
violation, like a mode Claude Code does not document, rather than dropped. A
violation's evidence carries the value its grant publishes, with credentials
redacted, cut to at most 200 characters. `disabledMcpjsonServers` is not read: a change
to it is still an unknown key. A setting also set under `permissions` is read
there only, so a top-level copy beside it stays an unknown key, exactly as the
inventory ignores it. Codex, Cursor and VS Code settings keep their own
readers' ratings; their rows name the setting and value too.

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
