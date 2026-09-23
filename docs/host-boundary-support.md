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
| Shared/GitHub | first-class | `AGENTS.md`, Shipgate policies/state, skills, `.github/workflows/*` | instruction/gate weakening, workflow permissions and triggers, remote step action references, named secret sources passed to reusable workflows, agent launches (documented agent action inputs, `run:` steps that are one plain `claude -p` / `codex exec` command, `run:` steps that mention an agent CLI in shell this audit does not parse, named as a limit) and `actions/checkout` refs |

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
established` either, which names only sources an inventory observed (#812) and
the changed inputs the candidate rules at the end of this section name (#821):

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
- **An agent launched any way the workflow reader below does not read**
  (#823): an action outside its table, even one that takes `claude_args`; a
  composite action (#701); a script the step runs; a `run:` that is not one
  line of plain words running `claude -p` or `codex exec` under `bash` or `sh`
  (more than one line or command, quoting, an expansion, a redirection, a
  comment, a here-doc, `npx`, `timeout`, `sudo`, `bash -c`, `codex` with an
  option before `exec`); an agent CLI reached through a variable or a
  function; and a step's `env:` and `if:`.
  A `run:` among these that mentions `claude` or `codex` as a word of its own
  is named as a non-blocking limit (below); editing, adding or removing it
  gives no row. The rest give no row and name no limit. A launch this audit
  read that becomes one of these is a row saying the step no longer declares
  an agent launch this audit reads, and that it may still start one this way;
  it never says the step no longer starts an agent. The one exception is a
  rule that moved between jobs (below): when another job adds the same launch
  while this job keeps no named unread step of that agent and no launch of it
  whose input the rule is read from this audit did not read, and no job but
  the one adding it holds more of them than it did, the row says the launch
  moved there, as a step reference moved between jobs does, so a launch that
  became a script or an action outside the table in the same change reads as
  moved; while the job keeps such a step, or any job but the receiving one
  gains one, it never does.

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

How a coding agent is launched inside a job is read (#823). Every value is
compared as the text it declares, less what the host readers withhold (below);
no action is fetched, no command is run and no expression is evaluated. Shell
is not parsed: a value is read only in a form every parser involved reads the
same way, a plain list of words, and every other form is named as a limit and
never guessed at. Four things are listed on the workflow grant, each naming its
`job/step` (the step's `id`, else its `name`, else `steps[N]`):

- **A documented agent action** — a step whose `uses:` is one of these
  `owner/repo` references, at any ref and in any letter case — with the inputs
  it declares from this table. Other inputs, such as `prompt` or an API key,
  are not listed.

  | Action | Inputs compared as text | Documented widening |
  |---|---|---|
  | `anthropics/claude-code-action` | `additional_permissions`, `allowed_bots`, `allowed_non_write_users`, `claude_args`, `plugin_marketplaces`, `plugins`, `settings`, and the earlier `allowed_tools`, `disallowed_tools`, `mcp_config` | a plain `claude_args` gains `--dangerously-skip-permissions` or `--permission-mode bypassPermissions` (the last `--permission-mode` counting); `settings`, written as JSON, gains `defaultMode: bypassPermissions` (under `permissions`, else at the top, as the settings reader reads `.claude/settings.json`); `allowed_bots` (any bot) or `allowed_non_write_users` (any user) gains a `*` entry |
  | `anthropics/claude-code-base-action`, also published as `anthropics/claude-code-action/base-action` | `claude_args`, `plugin_marketplaces`, `plugins`, `settings`, `allowed_tools`, `disallowed_tools`, `mcp_config` | `claude_args` and `settings` as above |
  | `openai/codex-action` | `allow-bot-users`, `allow-bots`, `allow-users`, `codex-args`, `permission-profile`, `safety-strategy`, `sandbox` | `sandbox` becomes `danger-full-access`; `permission-profile` becomes `:danger-full-access`, Codex's reserved name for its built-in full-access profile; `safety-strategy` becomes `unsafe`; a plain `codex-args` gains `--dangerously-bypass-approvals-and-sandbox` (`--yolo`) or `--sandbox danger-full-access` (`-s`, attached or not); `allow-users` gains a `*` entry. A sandbox `--config` override in `codex-args` meets none: after `codex-args` the action appends its own `--sandbox`, or its own `default_permissions` override for a `permission-profile`, which takes precedence |

  `claude_args` and `codex-args` are read only when they are a **plain list
  of words**: words made of letters, digits and `_ . / : = , % + - ( )`,
  separated by blanks or newlines, with no `--settings` or `--mcp-config` flag
  in any spelling. The Claude actions (`base-action/src/parse-sdk-options.ts`,
  shell-quote with `()|&;<>` made literal) and `openai/codex-action`
  (string-argv) both split such text at its blanks and nowhere else, so it is
  published as those words, one space apart — `claude_args: |` on several
  lines reads as it would on one, and reformatting it is quiet — and, as the
  Claude actions read it, a word starting with `--` is always a flag, never
  another flag's value. Any other
  value — holding a quote, a `${{ }}` expression, `$`, a backtick, a
  backslash, a `#` comment, `;`, `&`, `|`, `<`, `>`, a glob, JSON, a
  `--settings` or `--mcp-config` flag, or any other character — is **not
  read** (`unresolved_reason: unread_arguments`): none of its text is
  published, its `value` is `<withheld:…>`, a short digest, so an edit to it
  is a `changed` row whose cell shows the digest, no documented widening rule
  is read from it, and it is a non-blocking limit (below). So the
  reproduction's `--allowedTools "Read"` →
  `--permission-mode bypassPermissions --allowedTools "Bash(*)"` is a
  `changed` row saying the input is not read, while the same change written
  `--allowedTools Read` → `--permission-mode bypassPermissions --allowedTools Bash`
  is `widened`.

- **An agent CLI launched by a `run:`** — only when the whole `run:` is **one
  line of plain words**: letters, digits and `_ . / : = , % + -`, separated by
  spaces or tabs, so it holds no quote, `$`, backtick, backslash, `#`, `;`,
  `&`, `|`, `<`, `>`, parenthesis, brace, glob, `~`, `!`, `@` or second line,
  and every POSIX shell runs it as exactly those words. It must run under
  `bash`, `sh` or no declared `shell:` (the step's, else its job's or its
  workflow's `defaults.run.shell`); a template is read only as `bash` or `sh`
  running the script alone — option words it still runs the script under
  (`set` flags, `-l`, `-i`, `-r`, `-o`/`-O` with an option name other than
  `noexec`, `--noprofile`, `--norc`, `--posix`, `--login`, `--restricted`,
  `--noediting`, `--verbose`), then `{0}` last, as in
  `bash --noprofile --norc -eo pipefail {0}` — so one such as
  `bash -c '…' {0}`, which may run a command of its own, or one with `-s`,
  `-n` or `--rcfile`, is not. After any `NAME=value` assignments, which
  are skipped and never published, the program's file name must be `claude`
  with `-p`/`--print` among its arguments, or `codex` followed by `exec`
  (`codex e`): `claude -p …`, `./node_modules/.bin/claude -p …` and
  `CI=1 codex exec …` are read. Its documented permission flags are listed
  under their primary spelling, and every other word — the prompt, `--model`,
  an undocumented flag — is not compared. For `claude`: `--permission-mode`,
  `--dangerously-skip-permissions`, `--allow-dangerously-skip-permissions`,
  `--allowedTools`/`--allowed-tools`, `--disallowedTools`/`--disallowed-tools`,
  `--add-dir` and `--permission-prompt-tool`; gaining
  `--dangerously-skip-permissions` or `--permission-mode bypassPermissions`
  (one rule, so moving between the spellings is not a widening; of a repeated
  `--permission-mode`, the last counts, as the CLI keeps it) widens, and a
  `--settings` or `--mcp-config` flag makes the step unread. For `codex exec`:
  `--sandbox`/`-s`, `--dangerously-bypass-approvals-and-sandbox`/`--yolo`,
  `--approve-for-me`/`--not-so-yolo`, `--dangerously-bypass-hook-trust`,
  `--add-dir`, `--config`/`-c` and `--profile`/`-p`, a short flag's value
  read attached as clap reads it (`-sdanger-full-access`, `-s=…`,
  `-c<override>`, `-c=<override>`); gaining
  `--dangerously-bypass-approvals-and-sandbox` or the full-access sandbox
  widens. The full-access sandbox is `--sandbox danger-full-access` or, when
  the command passes no `--sandbox`, which takes precedence, a `--config`
  override in any of its four spellings that sets `sandbox_mode` to
  `danger-full-access` (the setting `--sandbox` sets) or `default_permissions`
  to `:danger-full-access` (the built-in full-access profile, which the
  action's `permission-profile` input passes the CLI the same way). The last
  override of a key counts, and a `default_permissions` override outranks a
  `sandbox_mode` one. A key under another table, such as
  `profiles.<name>.sandbox_mode`, and a `--profile`, which names a
  configuration this audit does not read, meet none. A flag the CLI reads as
  variadic (`--allowedTools`, `--add-dir`, …) takes every following word up
  to the next word starting with `-`, as the CLI reads it, so a prompt
  written after it is compared as one of its values; the row shows it.

- **An unread agent step** — any other `run:` that mentions `claude` or
  `codex` as a word of its own: more than one line or command, quoting, an
  expansion or a `${{ }}` expression, a redirection or a here-doc, a comment,
  a line continuation, another program such as `npx`, `timeout`, `sudo` or
  `echo`, a subcommand that is not a headless launch (`claude mcp add`,
  `codex login`), `codex` with an option before `exec`, a script named after
  an agent, or a declared `shell:` other than `bash` or `sh` running the
  script alone. It is listed in
  `unread_agent_runs` once for each agent CLI it mentions, with no other
  field, and is a non-blocking limit (below). Nothing more: none of its text
  is published, it is never compared, so adding, removing or editing it gives
  no row, and it never says that the step starts, or does not start, an agent.
  `npm ci && claude -p --dangerously-skip-permissions "Review"`, a
  `# Don't run this on forks` comment above a launch, a PR comment drafted in
  a here-doc that mentions `claude -p` and `gh pr comment --body "$(claude -p …)"`
  are all unread steps.

- **Each `actions/checkout` step's `with.ref`**, or the default when it
  declares none or an empty one. Adding `ref:
  ${{ github.event.pull_request.head.sha }}` is a `changed` row naming the
  step on both sides.

Each job's multiset of launches and of checkout refs is compared, so renaming
or reordering steps is quiet. An added, removed or changed launch or ref is a
`changed` row on the workflow naming `job/step` and the value on each side.
Direction is claimed only by the documented rules above. The rules each launch
meets are decided when the workflow is read, from the declared text before
anything is withheld for publication, and published on the launch as
`widening_rules` (each rule and the setting it was read from), so redaction
never hides one.

Only text this audit reads exactly meets a rule. GitHub substitutes a `${{ }}`
expression into an input before the action reads it, and the substituted text
may be anything, so an argument input holding one is not read at all; a user
gate's entries that hold no expression are read, since the substituted text
may add entries but cannot remove a literal one, so `"${{ vars.USERS }}, *"`
opens the gate; and a `sandbox`, `permission-profile`, `safety-strategy` or
`settings` value holding one meets none. Such a setting is published with
`holds_expression: true`, and a row that changes it says the text the
expression reaches is not read for a rule, rather than that none was gained.

When a job's launches gain a rule, the workflow earns
`workflow_agent_widened_<added|changed>`, the row is `widened` and its `why`
names the rule and step. Three gains are named in the `why` and not claimed:

- where, in the same job, a step that may launch that agent in a form this
  audit does not read (an unread agent step, or an action whose `with:` is
  not a mapping) is gone and a launch this audit reads is added, because the
  added launch may be that step rewritten, which may already have met the
  rule — so rewriting `npm ci && claude -p --dangerously-skip-permissions "Review"`
  as two plain steps is not a widening. An unread step that remains takes no
  gain from another launch: beside it, a new plain
  `claude -p --dangerously-skip-permissions Review` step is a widening;
- where the job's launch of that agent held, before, text this audit did not
  read for the rule in an input the rule is read from (`claude_args` or
  `settings` for bypassed permission checks, `sandbox`, `permission-profile`
  or `codex-args` for a full-access sandbox, the gate for a `*` entry): a
  `${{ }}` expression, or an argument input that was not a plain list of
  words, because it may already have met it — so unquoting
  `--dangerously-skip-permissions --append-system-prompt "Review"` is not a
  widening;
- where the rule moved between jobs: another job met it before and the launch
  that met it left that job — the job no longer exists, as when it is renamed,
  or the same launch now runs in this job while each launch of that agent this
  job had still runs here or in that job, as when an agent step moves or two
  jobs swap launches — as a step reference moved between jobs adds no scope.
  A second job gaining a rule a first job keeps, or a different launch
  gaining it while the first job still exists, is claimed: that job may still
  run its launch in a form this audit does not read (`npx`, quoting), so a
  launch that only becomes a named unread step has not left it, and a launch
  edited in place into the one that job had gains the rule. A launch has not
  left a job that still has any named unread step of that agent — wherever it
  stands and whether or not it was there before, because an unread step
  carries no text that tells which launch it is — or a launch of it holding
  an expression or an unread argument input the rule is read from. So quoting
  the prompt of `claude -p --dangerously-skip-permissions Review` in one job,
  or merging that job's `npm i -g @anthropic-ai/claude-code` step into it,
  while another job adds that plain step is a widening, and so is moving that
  step to another job while the job it left keeps a `claude mcp add` step.
  Nor has it left while any job but the one gaining the rule has more such
  steps and launches of that agent than it had before — a job new at the
  head holding any — because the launch may be one of them, and the job it
  left may be that job under a new name. So renaming the job while quoting
  that launch or running it through `npx`, or removing the job while another
  job gains it quoted, as a third job adds the plain step, is a widening;
  renaming a job with the install step it keeps beside the launch, or beside
  another job that keeps its unread step, is a move. Two jobs renamed at
  once, one of them holding an unread step, cannot be told apart from those,
  so they claim the gain, in the safe direction.

Any other edit — `--allowedTools Read` to `--allowedTools Bash`,
`acceptEdits`, a new plugin, an argument input this audit does not read, a
head-ref checkout — is `changed`; rating a tool rule's reach is a job for
issue #824. `access` and `risk` still describe the token and triggers alone.

A workflow row whose workflow runs an agent ends its `why` with the job facts
beside each agent step, whatever else the row is about: an untrusted-input
trigger (`issue_comment`, `issues`, `pull_request_target`, `workflow_run`), the
job's write scopes, the secrets the job references (`${{ secrets.NAME }}` in the
job, or in the workflow's `env`), and a checkout in the job of pull request
code (`github.event.pull_request.head.sha`, `.head.ref` or `.merge_commit_sha`,
`github.head_ref`, `github.event.workflow_run.head_sha` or `.head_branch`, or
`refs/pull/<n>/head` and `/merge`). It is a note, not a verdict: it moves no
direction, and `if:` conditions and the default checkout of a `pull_request`
event are not read into it. An unread agent step is not an agent step here. A
removed workflow gets no note.

A structured input publishes its shape and none of its free text, so a setting
never publishes what `.claude/settings.json` and `.mcp.json` would withhold
(#823 review). A JSON object — a `settings` or `mcp_config` value — publishes,
in canonical JSON:

- its key names, numbers, booleans and `null`, so reordering keys compares
  as unchanged and adding a key is a change;
- `<redacted>` for `env` and `headers` values (and codex's `http_headers` and
  `env_http_headers`), `apiKeyHelper`, every other secret-named value and the
  word after a secret-named argument such as `--token`, as the host readers
  redact them, so rotating one compares as unchanged;
- each other string as `<withheld:…>`, a short digest of what the host
  readers digest for it: the sanitized text, and a URL's query. Editing it is
  a `changed` row, and none of its text is published. So an MCP server's
  `args` — an `mcp-remote --header "Authorization: Bearer …"` included — and a
  hook's command, matcher and type publish only digests;
- except the strings a host reader publishes: a `permissions.allow`, `ask` or
  `deny` rule, and the value of a documented Claude Code setting
  (`defaultMode`, the switches in [the ratings table](#claude-code-setting-ratings),
  `enabledMcpjsonServers` entries), as the settings reader publishes them; and,
  under `mcpServers` (or `mcp_servers`), a server's command name and its URL's
  scheme and host, as the MCP reader publishes them. Each is followed by the
  digest when it drops something the digest reads: `"command":"npx <withheld:…>"`
  for `npx -y some-server`, a URL's digest for its query. A URL's path is
  neither published nor compared, as an MCP server's is not (#723).

A `settings` or `mcp_config` value that is not a JSON object is published as
written only when it is a plain file path: path characters, and any `${{ }}`
expression in it a plain context reference. Any other value, such as a comment
line before the JSON, publishes only `<withheld:…>`, a digest, so an edit to it
is still a `changed` row and none of its text is published.

JSON passed through `claude_args`, `codex-args` or a `run:` is never read, so
it is never published: the argument input or step is not read at all. A codex
`--config` override in a plain list of words — `-c key=value`,
`--config=key=value`, `-ckey=value` or `-c=key=value` — publishes its key;
its value is `<redacted>` under `env`, `headers` or a secret-named key, so
rotating it is quiet, published as written for `sandbox_mode`,
`default_permissions`, `approval_policy` and `model`, and `<withheld:…>`, a
digest, under any other key, such as an MCP server's `command` or `url` or a
`shell_environment_policy` value. The word after a secret-named word such
as `--token` or `password` is `<redacted>`, as the host readers redact it
among an MCP server's arguments, and the value is then credential-shaped
(below). Other argument text — a prompt word, a
flag's value — is published as written through the label redaction below,
except that a URL in it publishes its scheme, host and port, with
`<redacted-path>` for any path, as an MCP server's URL does (#723); the rest
of the setting is compared, so a change only to such a URL's path — which
repository a `plugin_marketplaces` URL names, for one — is not reported, and
a zero-row result says redacted values are not compared. A `${{ }}`
expression is one word while this is decided, so one inside a URL's userinfo
is withheld with it.

Any other text the #802 label redaction rewrites is credential-shaped: a token
shape, a credential assignment such as `token=…`, a bearer or header value, a
URL's userinfo — and ordinary prose too, such as "never print bearer tokens" in
a system prompt. In a setting, the value is published redacted with
`unresolved_reason: redacted` and compared as published, beside the rules read
from its declared text, so a permission change or a rule gained beside it is
still a row; only an edit inside what is redacted that gains no rule is not
reported, and that is named as a non-blocking limit below. A checkout ref names
the code a job runs, as a step reference does, so a redacted ref refuses the
same way a step reference does (#767), because two refs that redact alike
cannot be compared apart: GitHub coverage is `partial`, a changed workflow's
comparison is refused, and an unchanged one is named in `unchanged_limits`.

An unread agent step; an argument input that is not a plain list of words
(`unread_arguments`); an action whose `with:` is not a mapping; a setting that
is not a string (`not_a_string`), that holds text starting like JSON that does
not parse (`unparsed_json`, whose values cannot be told from its keys), or
that is published redacted (`redacted`); and a checkout ref that is not a
string or whose `with:` is not a mapping record a **non-blocking**
`unsupported` coverage issue naming the `job/step`, printed under
`audit --host` → Coverage issues; none but a redacted setting publishes any of
the value's text. GitHub coverage stays complete, so `check`, baselines and
every other row are unaffected. An unread agent step is never compared, so
adding, removing or editing it gives no row; for the others, adding, removing
or re-forming the entry, or its gaining a documented rule, is still a row, and
only an edit inside it that gains no rule is not reported (an unread argument
input's edit is a `changed` row by its digest). `diff`, `verify` and `check`
carry no limit for any of them, as for an unread secret value (#693): a
`diff` of a change that only adds an unread agent step says
`No static host-grant changes detected`, and `audit --host` is where the step
is named. The workflow's coverage line says so: `… compared; changed, but no
grant this entry compares changed, so no row (text this entry does not read,
such as a step's env or an unread agent step, is not compared; audit --host
names each unread agent step)`, where another file's line names redacted
values such as env values and `apiKeyHelper`.

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

A hook row names what changed in the hook, and an MCP row a change to the
server's launch arguments (#819), without publishing any command or argument
text. A hook grant publishes each handler under its event: the group's
`matcher`, through the published-label redaction (a matcher longer than 1,024
characters, as `config_sha256`'s input holds it, is `<not-shown>`); its command as the name of its executable — the
last path segment of its first word, only when that is a plain token and the
word is no URL, otherwise `<not-shown>` — and a SHA-256 digest of the whole
command; and its `timeout`. So a matcher, command or timeout edit reads
`PostToolUse: matcher Edit → Edit|Write|Bash`,
`PostToolUse: command changed (lint.sh sha256:… → curl sha256:…)` or
`PostToolUse: timeout 10 → 600` rather than `PostToolUse → PostToolUse`. An
MCP server grant publishes, from its arguments, only a package specification
of a strict shape (npm `name@version`, PyPI `name==version`, an OCI image with
a tag or digest) and a digest of the rest, so a version pin moving to
`@latest` reads `package example-mcp-server@1.2.3 → example-mcp-server@latest`
and any other argument edit `launch arguments changed` with both digests. The
detail is a display of the declaration, never an input to the comparison: the
command is not resolved or run, the script it names is not read (#702), and
the digests are of the configuration as `config_sha256`'s input holds it. A
value that input already redacts, such as the value after `--token`,
`--api-key` or `--password`, a `--password=…` value, an `X-Api-Key:` header
value or a URL's path, moves no digest, so a change confined to it is no row,
as before. A saved baseline holds none of
this detail, so nothing read from a user, managed or git-ignored settings file
reaches the committed file. A hook declaration outside the documented shape
publishes no handlers, and its row says the matcher, command and timeout are
not shown; when only one side is outside it, the row names that side and lists
the other side's handlers.

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
  repository; a change to that source is named under `What this run
  established`, never fetched (#821, see
  [Changed inputs named but not read](#changed-inputs-named-but-not-read)).
- A reference is followed only to a file named `hooks.json`, or
  `<name>-hooks.json` (`_` or `.` also separate). In a plugin manifest, any
  other name, a path outside the plugin directory, a `hooks` member of the
  wrong type, and an unreadable manifest are blocking coverage limits. `diff`
  and `verify` name a parse or shape limit when the manifest is unchanged; in
  a `partial` comparison every plugin-reference limit, an unchanged one
  included, is named instead with the directory it leaves uncompared. A
  read limit is never named as unchanged. When the limit is new, changed or on
  one side only — or is a read limit, such as an untouched plugin hook file
  over the read bound — `diff` and `verify` never compare that plugin: they
  leave its directory uncompared on both sides, name it, and compare the rest,
  as a `partial` comparison, where nothing outside the directory depends on it
  (#808, see [Partial comparisons](#partial-comparisons)). Otherwise they
  refuse the whole comparison, as an oversize settings file already did in
  `1.0.0`.
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
  review whatever its basis. It also routes a changed file that declares
  hooks of a plugin the project settings enable, wherever the plugin keeps
  it (#809): a hook file such a plugin selects, such as
  `plugins/demo/cfg/hooks.json`, and a manifest or marketplace whose inline
  hooks it loads. The route takes two facts. The path must be one the plugin
  hook reader opens (a hook-named file, a `.claude-plugin/plugin.json` or a
  `.claude-plugin/marketplace.json`), and the reader must find, in the base
  or the head, that an enabled plugin loads hooks from it. So a hook file
  deleted along with its reference is still routed from the base, and a
  change to one gets `require_review` through
  `SHIP-AGENT-BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED`, with evidence
  `hook_loading_basis: project_enabled_plugin`, beside the same expanding row
  as before. A `verify` with a manifest runs the same check from the same
  two-sided evidence, so there the finding moves the release gate to
  `review_required` and the merge verdict to `human_review_required`, and
  the PR comment shows both. A head that leaves such a hook file or plugin
  manifest unreadable is also incomplete input, as at a registry path. A
  marketplace the head makes unparseable is not: a marketplace limit never
  blocks, so the route still gives `require_review` with input `complete`,
  and the comparison shows its inline hooks as `removed`. For a provided
  `--diff`, enablement is read from the workspace tree alone.
- A hook file an enabled plugin selects under a name the reader does not
  follow, such as `./cfg/lifecycle.json`, or inside a directory the walk
  skips, holds hooks the host loads and that nothing read. Its limit, named
  above, counts as `check` and `verify` input when the change touches the
  file, including when only the base still selects it, so the change gets
  `SHIP-AGENT-BOUNDARY-INPUT-INCOMPLETE` and `human_review_required`. It is
  not routed to protected-surface review, since nothing in it was read.
- A hook a plugin only selects is not routed, and neither is a plugin
  manifest or marketplace that only references hook files: a change that
  makes an enabled plugin load an existing, unchanged file has an `added` or
  `widened` row for that file under an `allow` decision. `check` gives the
  same when that file is `.claude/hooks/hooks.json`, so this limit is not
  specific to plugin paths. Otherwise a plugin-reference limit never changes
  a `check` decision from what `1.0.0` gave. Its host
  comparison leaves out a limit both sides share on an untouched source,
  because its result cannot name a limit. A limit only one side carries, or
  one on a source the change touched, makes that comparison incomparable
  (`base_inventory_incomplete` or `head_inventory_incomplete`), so no added or
  removed row is built from a manifest that could not be read. In `check` that
  refusal stays repository-wide, so an unrelated row is withheld too: its
  boundary result cannot name the directory a `partial` comparison leaves
  uncompared (#808), and its decision comes from its own routing either way.

Skill and command frontmatter is read the way Claude Code documents it (#730).
Frontmatter is optional: a skill without it takes its name from the directory
and its description from the first non-empty line. Documented fields are
type-checked. An undocumented key (`version`, `author`, `category`, …) is
digested as written rather than refused, so changing it is still a change and
none is read as a permission. A Cursor rule still refuses a key outside
`description`, `globs` and `alwaysApply`.

#### Changed inputs named but not read

These shapes are plausibly agent configuration and no adapter reads them
either, but `diff`, `verify` and the manifest-free PR comment name a changed
one under `What this run established`, so a zero-row result is not read as
covering it (#821). Each is found from the comparison's own changed-file set —
the committed `base..head` change, or the working tree's tracked and untracked
changes against the base — never from a walk of the repository, so an
unchanged file is never named. The item is `changed_not_read` with the
`candidate` rule that named it, printed as `<path> (<host>): changed, not read
by this entry: <what it is>; no row, and loading is not established` (`added`
or `removed` for a file or member only one side has). It is never a row, a
widening, a `check` violation or a claim that a host loads the file.

- **A plugin's MCP configuration** (`plugin_mcp_config`): `mcp.json` in a
  directory that holds `.claude-plugin/plugin.json`,
  `.codex-plugin/plugin.json`, `.cursor-plugin/plugin.json` or
  `.github/plugin/plugin.json` on the side it exists on, such as a Cursor
  plugin's `plugins/demo/mcp.json` or a root `mcp.json` in a plugin
  repository.
- **A plugin manifest's MCP servers** (`plugin_manifest_mcp_servers`): the
  `mcpServers` member of any of those manifests, inline or a reference, when
  its text differs between the sides. The file a reference names is not
  followed.
- **A plugin manifest's hooks** (`plugin_manifest_hooks`): the `hooks` member
  of a Codex, Cursor or Copilot manifest, when its text differs. A Claude Code
  manifest's `hooks` is read, as described under the hook loading basis
  above.
- **A hook file a plugin manifest names** (`plugin_hook_file`): a changed file
  named like a hook declaration (`hooks.json`, `<name>-hooks.json`) that the
  `hooks` member of a Codex, Cursor or Copilot manifest in one of its eight
  nearest ancestor directories names by a relative path inside that plugin,
  such as `p/hooks/hooks.json` referenced by `p/.codex-plugin/plugin.json`.
- **A plugin manifest or marketplace that does not parse**
  (`unparsed_plugin_manifest`): a changed manifest or
  `.claude-plugin/marketplace.json` whose members could not be compared, unless
  an inventory already names it, as a blocking limit names an unreadable Claude
  Code manifest.
- **Cursor project hooks** (`cursor_project_hooks`): `.cursor/hooks.json`, at
  any depth, such as removing a `beforeShellExecution` guard.
- **Host settings below the repository root** (`nested_host_settings`):
  `<dir>/.claude/settings.json`, `<dir>/.claude/settings.local.json`,
  `<dir>/.cursor/cli.json`, `<dir>/.cursor/mcp.json` and
  `<dir>/.vscode/mcp.json`, such as a dotfiles package's
  `claude/.claude/settings.json`. Whether it is a nested project's settings or
  a user-scope package's is not established.
- **An external marketplace plugin source** (`external_plugin_source`): a
  `.claude-plugin/marketplace.json` `plugins[]` entry whose `source` is an
  object (`github`, `git`, `url` and the like) that was added, removed or
  changed — its `repo`, `url`, `ref` or `sha` — compared as text by entry
  name. The item is `<marketplace>#plugins.<name>`, the name redacted and
  bounded (with a digest of the exact name when that changed it), and names
  what the source now points at (`github example/one at <sha>`), redacted; the
  content is never fetched.

A whole file is named only when no inventory published it: a file a reader
read is an item of its own. A member is named whatever else read the file,
because no reader reads that member. Nothing is fetched or run, only a
plugin manifest or marketplace is read, and every read stays inside the
repository within the host reader's own per-file bound. Paths under
`node_modules`, `.venv` and the other directories the host readers never walk
are not considered. At most 32 candidate paths are examined per comparison;
the rest, and any whose rule needed a file it could not use — a manifest a
hook file could be named by that was not read or did not parse, or a changed
manifest or marketplace present but not read, such as a link — are counted
as not examined, never guessed at: one count in
`coverage.unread_candidates_not_examined`, and one line in the block,
`N changed candidate inputs not examined: past the discovery bound, or a file
the rule needed was not read or did not parse`, which names both causes
because the count does not tell them apart. When the changed files cannot be
listed or looked at, the block says so and names none. Ordinary
documentation, an unrelated `*.json`, and a candidate the change did not touch
produce nothing, and a shape outside this list — a root `plugin.json`, a
Codex or Cursor marketplace, a hook file a manifest names under another file
name, a file a `mcpServers` reference names — is still neither read nor named.
Each shape stays here until a reader exists for it (#663).

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

### Partial comparisons

A plugin directory `diff` and `verify` could not compare no longer hides the
changes outside it (#808). They publish `comparison_status: partial` — a
status of the comparison, not a host's `partial` coverage — with the same
`incomparable_reasons` the refusal would name, when every blocking limit that
refused the comparison is one of the plugin-reference limits listed under
[Known unread surfaces](#known-unread-surfaces), bounded by the plugin
directory whose references raised it, and every other limit is an unchanged
one. A reference is followed only inside its plugin
directory, so that directory holds everything such a limit can hide. It is
left uncompared on both sides — every host's artifacts, grants and
non-blocking issues under it, not only the plugin's, matched
case-insensitively — and named as `scope` on the limit's coverage
item; a directory inside another is covered by the outer one, and a hook file
another plugin also selects is withheld with it. The rows outside it are
published, led by `Not compared: <directory>, a plugin directory this entry
could not read completely, …`, and a partial result with no row is never a
no-change answer. A changed `.claude/settings.json` or
`.claude/settings.local.json` with no row of its own reads `changed, but no
row is attributed to this path` there, never that no compared grant changed:
those settings decide the loading basis of the hooks in the directory, which
were not compared.

Independence is read off the reference graph, never off directory names. The
comparison refuses as before when a limit is neither a bounded plugin
reference nor unchanged (an unreadable settings file, an instruction file
whose structure could not be established, a link that is not read through),
when a reference names a path outside its plugin, when the plugin is at the
repository root, when its directory holds `.claude/settings.json` or
`.claude/settings.local.json`, which decide every plugin hook's loading basis,
when a marketplace outside the directory declares inline hooks for that
plugin, when the directory does not publish as itself (a redacted or shortened
path), and when nothing outside it was read. A partial comparison is not
comparable: `verify`'s control, `check`'s decision, the control envelope, the
Stop hook, baselines and drift treat it as they treated the refusal.

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
row's `⚠` still marks every added or changed mode. Moving a value between
`permissions` and the top level sets it too, in either direction: the
inventory reads a setting from either place, but Claude Code documents
`defaultMode` under `permissions` and `enableAllProjectMcpServers` at the top
level, so moving a top-level `defaultMode: bypassPermissions` into
`permissions` turns the mode on without changing its value, and `check` blocks
it. Its grant is the same value on both sides, so `diff`, `verify` and the
`check` rows show no row for the move; the violation, and `verify`'s finding,
is its record. Removing a setting raises nothing of its own, as removing
`defaultMode` never did; the file is still a protected surface and the removal
is still a row. A host-boundary policy that raises either rule above its
default raises these violations with it; none can lower them.

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
