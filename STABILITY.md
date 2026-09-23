# Stability Contract · 1.1.0

What agents and CI integrations can rely on across versions of Agents Shipgate.

Unreleased, runtime contract v41: a host comparison names the changed inputs it
does not read (#821). Verifier `0.21` and capability diff `0.4` add a
`changed_not_read` coverage item, with the `candidate` rule that named it, for
each path in the comparison's own changed-file set that a bounded, documented
rule recognises as plausibly agent configuration and no reader of this entry
read. It is never a row, a widening or a `check` violation, and never a claim
that a host loads the file. `read_sources_only` becomes `false` while one is
named, and `unread_candidates` says whether the change set was examined. A
manifest-free `verify` whose only host-relevant change is such an input, or a
changed candidate it counts as not examined, now publishes that comparison
instead of the setup route, and `verify --preview` then names `audit --host`
(`discover`) instead of `init --write` (`initialize`), in an agent-related
workspace too.
`minimum_control_contract_version` stays `21`. See
[the migration note](#unread-changed-inputs-821).

Unreleased, runtime contract v41 reads how a coding agent is launched inside a
workflow job (#823). Contract v40 and host-grants `0.6` shipped in 1.1.0, so
host-grants inventory, baseline and drift schemas move to `0.7`: a workflow
grant adds `agent_launches[]` — a documented agent action's permission inputs,
or the permission flags of a `run:` that is one literal `claude -p` or
`codex exec` command, compared as text and never executed — and
`checkout_refs[]`, each `actions/checkout` step's `with.ref`. Only a documented
rule a job's launches gain widens (`workflow_agent_widened_<added|changed>`);
every other edit is a `changed` row naming `job/step`, and a workflow row that
runs an agent ends with the job facts beside each agent step. The rules each
launch meets are read from its declared text and published as
`widening_rules`, and `claude_args` and `codex-args` are split as each action
splits them, and only from literal text a `${{ }}` expression cannot reach (a
setting holding one says so with `holds_expression`); a rule a launch already
met in a job it left is moved, not gained. A setting publishes what the host
readers would: a JSON object its key names, however it is attached to its flag,
a URL its scheme and host. A compound command, an expansion or an expression in
`run:` is `unresolved`, a named non-blocking limit that leaves coverage
complete; a setting holding credential-shaped text, prose included, is
published redacted, compared as published and named the same way, while a
checkout ref holding it refuses as a redacted step reference does. A `0.4`–`0.6` baseline holding a workflow
grant is incomparable (`baseline_workflow_agent_launches_unavailable`); one
without a workflow stays comparable. Verifier `0.20`, capability diff `0.3` and
`minimum_control_contract_version` `21` are unchanged. See
[the migration note](#workflow-agent-launches-contract-v41-823).

Also unreleased, and moving no version of its own: a Claude Code setting that
disables prompts or approves project MCP servers carries one rating on every
surface (#827). The `audit --host` grant, the `diff`, `verify` and `check`
rows, `check`'s violation and `verify`'s finding read one table.
`enableAllProjectMcpServers: true` and `skipDangerousModePermissionPrompt: true`
move from `SHIP-HOST-BOUNDARY-CONFIG-PARSE-FAILED` to
`SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW`, and `defaultMode: dontAsk` from
the wildcard check to `SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED` at
`medium`. Setting rows name the setting and value, and `enabledMcpjsonServers`
entries become grants. See
[the migration note](#claude-setting-ratings-827).

Also unreleased, and moving no version of its own: `check` and `verify` route
a changed hook declaration of a plugin the repository's project settings
enable to protected-surface review, wherever the plugin keeps it (#809). A
hook file such a plugin selects outside the registry paths, such as
`plugins/demo/cfg/hooks.json`, and a manifest or marketplace whose inline hooks
it loads, now fire `SHIP-AGENT-BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED`: in
`check`, `require_review` instead of `allow` beside an expanding row; in a
`verify` with a manifest, and its PR comment, `review_required` /
`human_review_required` instead of `passed` / `mergeable`. A changed hook file
such a plugin selects under a name the reader does not follow fires
`SHIP-AGENT-BOUNDARY-INPUT-INCOMPLETE` in both. A hook a plugin only selects is
still not routed. No schema, member, row or check id moves. See
[the migration note](#enabled-plugin-hook-routing-809).

Also unreleased, and moving no version of its own: a `verify --preview` pointer
never binds the verification plan (#807). In a repository with a manifest, the
preview's pointer bound the plan a `verify` would run, whose inputs a preview
never reads, so it had no input-directory census and every reader refused it:
`verify --preview --format control` said `human_review_required` where `--json`
said `agent_action_required` with the `verify` command, and `agent control`
exited `4`. The pointer now binds the verifier route and the working tree it
was read from, as a manifest-free preview's does, so both answer the `--json`
route until the tree moves. A pointer that binds a plan without its census is
refused as before, and a pointer published inside a repository without a plan
now always declares its worktree snapshot, so Git configuration the worktree
readers refuse (#813) no longer leaves a preview current. See
[the migration note](#preview-control-currency-807).

Previous runtime contract v40 reads the action reference each workflow step declares
(#771). Host-grants inventory, baseline and drift schemas move to `0.6`, and a
workflow grant adds `step_actions[]`: the job, the step (`id`, else `name`,
else `steps[N]`), the declared `uses`, and its `form` — `remote`, `docker`, or
`unresolved` with a reason. Moving a step from a pinned SHA to `@main` is a
`changed` row naming the job and step, with `expands: false`: a reference
names different code, not new token scopes. Local `./` actions stay unread
(#701). A `0.4` or `0.5` baseline holding a workflow grant is incomparable
(`baseline_workflow_step_actions_unavailable`); one without a workflow stays
comparable, and `minimum_control_contract_version` stays `21`. See
[the migration note](#workflow-step-action-references-contract-v40-771).
The same contract and `0.6` schemas, new in 1.1.0, also read the named secrets a
job passes to a reusable workflow (#693): a reusable call adds
`secret_mappings[]` (the destination, the source name from
`${{ secrets.NAME }}`, `form` and `unresolved_reason`) and `uses_redacted`.
Pointing a destination at a different source is a `changed` row with
`expands: false`; a reusable target or secret name that redacts is a blocking
limit rather than a comparison, while a literal or another expression is a
named non-blocking limit that leaves coverage complete. See
[the migration note](#reusable-workflow-secret-mappings-contract-v40-693).
The same contract and `0.6` schemas, new in 1.1.0, publish every workflow label
redacted (#802): a job id, a step's `id` or `name`, a trigger and a
permission scope name shaped like a credential (`ghp_…`, `AKIA…`) or holding
userinfo after `scheme://` are redacted in every field, row and `check`
evidence that names them, and `config_sha256` is computed over the redacted
labels. Ordinary names are unchanged. One redacted label still compares. Two
distinct job ids or triggers in a workflow, or scope names in one
`permissions` mapping, that publish alike are a blocking limit: while they
exist, `check`, `audit --host --save-baseline` and drift refuse on every run.
See [the migration note](#workflow-label-redaction-contract-v40-802).

New in 1.1.0, still contract v40: a hook grant states its loading basis (#714).
A Claude Code hook file nothing in the repository selects is published with
`access: unknown` and earns no expansion signal. A hook a plugin manifest or
marketplace entry selects is `execute`/`medium` and earns none either, unless
the repository's own project settings enable that plugin from a marketplace
inside the repository: then it is `execute`/`high` and an expansion, as it was
in `1.0.0`. Settings hooks are unchanged. No schema moves. A baseline that
recorded an unselected file as `execute`/`high` reports one changed,
non-widening row. `check` decides as `1.0.0` did when a plugin reference cannot
be read, and its host comparison refuses when only one side carries that limit.
See [the migration note](#hook-loading-basis-714).

New in 1.1.0, still contract v40 and host-grants `0.6`: permission rule direction
follows Claude Code's documented rule syntax (#816). A `Bash` rule's trailing
`:*` is the trailing ` *` it spells, and a trailing ` *` covers the bare command,
so `Bash(npm:*)` -> `Bash(npm test:*)` is no longer an expansion. A rule whose
text only moved from `deny` or `ask` into `allow` no longer blocks pairing the
replacement beside it; a rule moved out of `allow` is still the replaced rule
when it is the only allow rule that left. `mcp__<server>__<tool>` is a scoped grant
(`wildcard: false`, `medium`), and `mcp__<server>` compares as
`mcp__<server>__*`. No
member, schema or check id moves; `expansion_signals`, row `direction`,
`expands`, `severity` and `why`, and `check` decisions change for these shapes.
See [the migration note](#permission-rule-direction-816).

New in 1.1.0, still contract v40: every host comparison says what it established,
source by source (#812). Verifier `0.20` adds `host_comparison.coverage`, and
`shipgate diff --json` moves to capability diff `0.3` with the same block: at
most ten items of `{source, hosts, side, status, rows, limit, detail, scope}`
and an `omitted_items` count. A file compared on both sides names how many
rows it gave, counting a source inside it such as a Codex profile, and is
called unchanged only when its bytes are proven identical; a file that changed
with no compared grant change is `changed_without_grant_change`, so an `env`
or `apiKeyHelper` edit, key or value, no longer reads as no change; one whose
bytes cannot be proven identical, such as a link read, is
`unchanged_not_proven`; any other changed file with no row, such as a plugin
manifest's `hooks` reference or a retargeted link, is `changed_without_rows`; one only a
single side published names that side (`base` for a deleted file, `head` for a
new or untracked one or a hook file only the head's plugin configuration
selects); and an incomparable comparison names each blocking source and its
kind, with the refusal unchanged. `diff`, `verify` text and the PR comment print
it as `What this run established`, under one line saying what the list cannot
be read as: only sources this entry read or tried to read are in it. No row,
reason, route, digest or baseline moves. A `0.19` verifier reads with coverage
not recorded; one that claims coverage is refused. See
[the migration note](#host-comparison-coverage-812).

New in 1.1.0, still contract v40: the same two versions publish what the text
says about the rows, so the two cannot disagree (#795). A row adds
`disposition`, the `allow`/`ask`/`deny` list a permission rule is declared
under (`null` for every other kind), on every route that publishes rows, and
`host_comparison.review` / `diff --json`'s `review` adds the changes the text
prints: for each, the rows it stands for, its direction — including the
`widened`, `narrowed` and `moved` a pair of rows cannot carry — its cells, its
`why` and one `expands`, plus a `summary` of `{rows, changes, widenings}`, the
review question and the reproduction command. A joined change whose two sides
read alike is refused, so a route that redacts rule arguments publishes rows
alone. Every row value and the row count are unchanged, and the benchmark
replays reproduce their published scores. A `0.19` verifier reads with neither
recorded; one that claims either is refused. See
[the migration note](#host-diff-review-json-795).

Previous runtime contract v39 reads through an in-tree link at a boundary path (#700).
A link such as `CLAUDE.md -> AGENTS.md` or `.claude/skills -> ../.agents/skills`
that resolves inside the repository is read at its target and published under
its own path. Host-grants inventory, baseline and drift schemas move to `0.5`,
which adds `artifacts[].resolved_through`, the in-tree paths the read
followed, present only on such an artifact. An external, escaping or dangling
target, a link inside a linked directory, a chain past eight hops, and a
directory link that could only hide a `**/` match still refuse. A `0.4`
baseline stays comparable, and `minimum_control_contract_version` stays `21`.
See
[the migration note](#link-read-through-at-boundary-paths-contract-v39-700).

Previous runtime contract v38 lets the control envelope name what host capability
changed (#662). `shipgate.agent_control/v1` gains an optional
`capability_rows` block on `check --format agent-control-json`,
`verify --format control` and `agent control`. It holds up to five rows,
the rows the engine called an expansion first, plus `omitted_rows`,
`comparison_status`, `incomparable_reasons` and `unchanged_limit_count`.
It is evidence beside the control and moves no state, permission or route.
It is omitted when no host comparison ran, so those envelopes are
byte-identical. The envelope object is closed, so a reader that validates
against the v37 schema rejects an envelope carrying the block. That break
is deliberate. `minimum_control_contract_version` stays `21`. See
[the migration note](#host-capability-rows-in-the-control-envelope-contract-v38-662).

Previous runtime contract v37 names the limits a host comparison compared past (#721).
Verifier `0.19` adds `host_comparison.unchanged_limits`, and `shipgate diff`
(capability diff `0.2`) carries the same list. A surface that is partial
(`unsupported`, `parse_failed`) or experimental on both sides, and
byte-identical between them, no longer refuses the whole comparison: the rest
is compared and each limit is named. A limit that changed, appears on one side
only, or is `unreadable` still refuses. `check`'s boundary result cannot name
limits yet and keeps refusing. See
[the migration note](#unchanged-comparison-limits-contract-v37-721).

Previous runtime contract v36 freezes the report contract at `1.0` (#569). No field is
added, renamed, retyped or removed; `minimum_control_contract_version` stays at
`21` because every operational control shape is byte-identical. The production
qualification policy's `required_report_schema_version` moves to `1.0` with the
engine, and issuance of the `pre_1_0` qualification tier is retired — existing
`pre_1_0` artifacts stay readable, nameable and scoreable, and every scoring
floor is unchanged.

Runtime contract v33 extends `detect` with `host_boundary_candidates` and
`host_discovery_incomplete_paths`. They are filename applicability and
incomplete-traversal evidence, never permission assertions. The second field
names every path the bounded census could not see through — a link it does not
follow, a directory it could not read, the entry bound — and a census that
stops publishes no candidates. It never refuses the classification: an
unreadable path withholds the product-wide negative and leaves the framework,
source and scope answers standing, the same way `audit --host` records the
failure and continues. An absent field cannot satisfy the product-wide
negative predicate. Host-only `init` returns
`manifest_status: "not_applicable_host_review"` without setup writes;
`bootstrap` returns `verdict: "host_review_required"` and the existing detect
control route. Both may exit zero with work still required, and all setup
permissions remain false.

The hand-off applies to every detection-driven `init`, including `--ci`,
`--claude-code`, `--agent-instructions` and `--local-review`, and not to
`init --minimal`. The line is what the flag does with discovery, not how
explicit it is: `--minimal` selects the legacy template without classifying
the workspace at all, so there is no classification for a host-only route to
act on. Every other mode renders from the detection this route belongs to, so
a repository whose only surface is host configuration would get a manifest
declaring nothing — the dead end #568 exists to remove. A host-only workspace
that genuinely wants a manifest asks for the template by name. Existing manifests, builder sources, and Python-cap recovery retain
their routes. Persisted release evidence and its schema versions are unchanged.

This document is the contract. If the runtime ever diverges from what's documented here, that's a bug — please file an issue.

The `report.json` schema is **frozen at `1.0`**
(`report_schema_version`, [`docs/report-schema.v1.0.json`](docs/report-schema.v1.0.json)),
satisfying the condition this document set for beginning a `1.0` line. `1.0`
is a promotion of the `0.43` shape, not a break: the two schemas are
byte-identical apart from `$id`, `title` and the version constant, so a
consumer written against `0.43` reads a `1.0` report unchanged.

`1.x` is additive-only. A minor may add a block, a member, an open-enum value
or a check ID; it may not rename, retype, remove or redefine anything. A change
that cannot be expressed additively needs `2.0`. A deprecation cycle is counted
in **shipped releases**, never in elapsed time on unreleased `main` — a
deprecation nobody could install gave nobody the chance to migrate. Every
published `docs/report-schema.v<version>.json` keeps its bytes forever.

The freeze is conditional: it holds while no breaking change lands, and a
breaking change restarts it and invalidates every qualification receipt
collected against it. The CLI surface, exit codes and `contract_version` remain
stable as described here. Pre-freeze `0.x` reports stay published and readable,
but are no longer accepted as *input* to this engine — they are refused by name
with a regeneration route rather than reinterpreted under the current model's
defaults. The stable/provisional inventory, the `1.x` rules and the migration
from the shipped `v0.15.0` contract are in
[`docs/report-1-0-contract.md`](docs/report-1-0-contract.md). Pin a version (or
the Action tag) for reproducible CI.

---

<a id="partial-host-comparison-808"></a>

## Migration Note: Unreleased — a plugin directory that cannot be compared no longer hides the rest (verifier `0.21`, capability diff `0.4`, contract v41, #808)

A pull request that broke one plugin manifest — `plugins/demo/.claude-plugin/plugin.json`
left as `{not json` — and also dropped a `deny` rule from `.claude/settings.json`
printed `Cannot compare against main: head_inventory_incomplete` on `diff`,
`verify` and the manifest-free PR comment, with no row at all, where the
published `1.0.0` showed `⚠ low removed claude-code .claude/settings.json`
`deny: Bash(curl:*) → gone`. The plugin-reference limit #714 introduced
refused the whole comparison, including files that plugin cannot reach.

Such a comparison is now `partial`, inside the existing `comparison_status`
and `coverage` members. No command, reader, row kind, verdict or control state
is added.

```json
"comparison_status": "partial",
"incomparable_reasons": ["head_inventory_incomplete"],
"rows": [
  {"subject": "claude-code .claude/settings.json", "before": "Bash(curl:*)", "after": "—",
   "direction": "removed", "disposition": "deny", "expands": true, "severity": "low",
   "why": "removes a denial the agent was subject to"}
],
"coverage": {
  "items": [
    {"source": "plugins/demo/.claude-plugin/plugin.json", "hosts": ["claude-code"], "side": "head",
     "status": "blocking_limit", "rows": 0, "limit": "parse_failed", "detail": "static parser rejected this json file (JSONDecodeError); the hook files this plugin manifest may select were not read",
     "candidate": null, "scope": "plugins/demo"},
    {"source": ".claude/settings.json", "hosts": ["claude-code"], "side": "both",
     "status": "compared", "rows": 1, "limit": null, "detail": null, "candidate": null, "scope": null}
  ],
  "omitted_items": 0,
  "read_sources_only": true,
  "unread_candidates": "examined",
  "unread_candidates_not_examined": 0
}
```

- **When.** Only where the reader's own reference graph bounds every blocking limit that refused the comparison, and no compared source depends on what it bounds. A plugin-reference limit — a Claude Code plugin manifest that does not parse or is not an object, a `hooks` member of the wrong type, a reference to a file not named `hooks.json` or `<name>-hooks.json`, or a read limit of a hook file only a plugin selects — is bounded by the plugin directory whose references raised it: a reference is followed only inside its plugin directory (#714), so everything the limit can hide is published under that directory. In this entry's repository scope every other grant is read from its own file. Any blocking limit that is not such a limit must be an unchanged limit of what remains, proven exactly as `unchanged_limits` proves one (#721).
- **Where independence is not established, nothing changes.** The comparison stays `incomparable`, with no row and no `review`, when a blocking limit is neither bounded nor unchanged — an unreadable settings file, an instruction file whose structure could not be established (the added and edited skill cases #808 recorded), a link #700 does not read through (`docs/proxy -> ../src/pkg`, a dangling `NOTES.md`); when a reference names a path outside its plugin, which no directory bounds; when the plugin is at the repository root; when the directory holds `.claude/settings.json` or `.claude/settings.local.json`, which decide every plugin hook's loading basis; when a marketplace outside the directory declares inline hooks for the plugin inside it, since those grants are published under the marketplace; when the directory does not publish as itself (a redacted or shortened path, or one holding `#`); and when nothing outside the directories was read, which would retain nothing. A different directory alone never establishes independence.
- **What is left uncompared.** Everything published under the directory, on both sides and compared case-insensitively, as the reader matches references: its artifacts, grants and non-blocking issues. A plugin directory inside another is covered by the outer one. A hook file another plugin also selects is withheld with it, so a shared reference never lends it a result the other side could not read. Nothing is cached: repairing the manifest restores that directory's rows on the next run, and a limit on another dependency is judged by the same rules.
- **`comparison_status: partial`.** `incomparable_reasons` names which inventory is incomplete, exactly as the refusal would have (`base_inventory_incomplete`, `head_inventory_incomplete`, or both). `rows`, `review` and `unchanged_limits` are those of the comparison outside the withheld directories, built by the comparator a comparable result uses. It is never a complete comparison and never a no-change answer.
- **`coverage.items[].scope`.** Reserved and always `null` in verifier `0.20`. Now the withheld directory, on each `blocking_limit` item of a partial comparison and on those only: every blocking item of a partial comparison names one, and blocking limits still rank first inside the same cap of ten, so the first is always listed. The other items are what the comparison established outside the directories, in the existing order and wording, and a changed input no reader reads (#821) is still named, inside a withheld directory too. One status is narrower there: a changed `.claude/settings.json` or `.claude/settings.local.json` that gives no row of its own is `changed_without_rows`, never `changed_without_grant_change`, because those settings decide the loading basis of the hooks inside a withheld directory, which were not compared, so the data cannot show that no compared grant moved. Registering the repository as a marketplace for an enabled plugin whose manifest the head also breaks is the case: comparable, it gives a `widened` row on the plugin's hook file; partial, it gives no row and the settings file reads `changed, but no row is attributed to this path`. `scope` is `null` on every item of a comparable or incomparable comparison and on every other status, and never names the repository root.
- **Text.** `diff` opens with `Partial comparison against main (<sha>) -> working tree: head_inventory_incomplete`, then, before any row, `Not compared: plugins/demo, a plugin directory this entry could not read completely, so no change inside it is shown and nothing is claimed about it.` and `The changes below come only from sources outside it, so they are not the whole change; nothing here is a claim that the change is safe.` With no row, that last line reads `No static host-grant change was detected outside it. That is not a no-change answer for this change, and no verdict is implied.`, and `No static host-grant changes detected` is never printed. `verify --format text` and the manifest-free PR comment open with `Host capability comparison partial: head_inventory_incomplete` and the same two lines. The limit's item reads `plugins/demo/.claude-plugin/plugin.json (claude-code): parse_failed in head, so nothing in plugins/demo was compared`. The rows, their summary, the review question and the reproduction lines print as a comparable result prints them.
- **Authority and routes do not move.** A partial comparison is not comparable, so every consumer that switches on `comparison_status == "comparable"` reads it as it read the refusal. `verify`'s control state, permissions, next action (`audit --host`), merge verdict and exit code are the incomplete comparison's, and only its headline changes, to `Host comparison is partial: N repository-declared host capability change(s) outside what it could not compare, listed under host_comparison in verifier.json; review its input limits before interpreting changes.` The control envelope's `capability_rows` projects a partial comparison as `incomparable`, with its reasons and no rows, exactly as before: that block cannot name a directory, so its `reason`, which is that headline, points to the rows in `verifier.json`. `check` records no coverage, so it cannot name one either and refuses its comparison as `1.1.0` did; its decision and violations come from its own routing, so the #808 fixture still gives `require_review` with `HOST-PERMISSION-DENY-REMOVED` and no rows. The Stop hook still tells the agent to treat the change as unreviewed. `audit --host`, the inventory digests, saved host-grants baselines and drift payloads are unchanged (host-grants stays `0.6`), so a partial comparison never creates or satisfies a baseline. The host-config and cold-start benchmark replays reproduce their run-of-record scores. `minimum_control_contract_version` stays `21`.

**Compatibility.** Verifier `0.21`, capability diff `0.4` and runtime contract v41 are unreleased, so they are extended in place. `comparison_status` is a closed enumeration, so a reader validating against the frozen [`docs/verifier-schema.v0.20.json`](docs/verifier-schema.v0.20.json) rejects `partial`; the current reader refuses a `0.20` artifact that claims a partial comparison or a `scope`. A consumer that treats every status but `comparable` as not comparable is unaffected. One that expected only `comparable` or `incomparable` should read `partial` as `incomparable` for any decision, and may read its rows as what is known outside the directories named.

---

<a id="workflow-agent-launches-contract-v41-823"></a>

## Migration Note: Unreleased — workflow agent launches (host-grants `0.7`, contract v41, #823)

Contract v40 and host-grants `0.6` shipped in 1.1.0, so this mints host-grants inventory, baseline and drift `0.7` and runtime contract `41` rather than extending them in place. The `0.6` schema files stay published and unchanged. A workflow grant adds two members, each present only when a step declares one; in a `0.7` grant their absence means the steps were read and declare none:

```json
{
  "agent_launches": [
    {
      "job": "review", "step": "steps[1]", "agent": "anthropics/claude-code-action",
      "form": "read", "unresolved_reason": null,
      "settings": [
        {"name": "claude_args", "value": "--permission-mode bypassPermissions --allowedTools \"Bash(*)\"", "unresolved_reason": null}
      ],
      "widening_rules": [{"rule": "bypass_permissions", "setting": "claude_args"}],
      "job_secrets": ["CLAUDE_CODE_OAUTH_TOKEN"]
    }
  ],
  "checkout_refs": [
    {"job": "review", "step": "steps[0]", "ref": "${{ github.event.pull_request.head.sha }}", "unresolved_reason": null}
  ]
}
```

- **What is read.** A step whose `uses:` is `anthropics/claude-code-action`, `anthropics/claude-code-base-action` (also published as `anthropics/claude-code-action/base-action`) or `openai/codex-action` (at any ref, in any letter case) is an agent launch listing the documented inputs it sets; a `run:` that is one literal simple command starting with `claude` and passing `-p`/`--print`, or with `codex exec` (`codex e`), lists its documented permission flags under their primary spelling. Each `actions/checkout` step lists its `with.ref`, `null` for the default. The support page has [the input and flag tables](docs/host-boundary-support.md#known-unread-surfaces). Values are text: no action is fetched, no command run, no expression evaluated. `job_secrets` names the secrets the launch's job references and the workflow's `env` passes; it is context for the row and is not compared. `job`, `step`, values and secret names are published labels (#802).
- **What is compared.** Each job's multiset of launches (`job`, `agent`, `form`, `unresolved_reason`, `settings` by `name`, `value` and `unresolved_reason`, and `widening_rules`) and of checkout refs (`job`, `ref`, `unresolved_reason`), never the step label, so a rename or reorder is quiet. An added, removed or changed entry is one `changed` row on the workflow naming `job/step` and the value on each side. Of a CLI launch only the documented permission flags and their values are compared, so `--model` and any undocumented flag are not; a flag `claude` reads as variadic (`--allowedTools`, `--disallowedTools`, `--add-dir`, `--mcp-config`) takes every following word up to the next word starting with `-`, as the CLI reads it, so a prompt written after one is compared, and published, as one of its values; any other prompt is not. An agent action's `claude_args` or `codex-args` is compared whole, as the text the action parses: the Claude actions drop full-line `#` comments, which are therefore neither published nor compared.
- **What is withheld.** A setting publishes what the host readers would publish for the same text. A JSON object — a `settings` or `mcp_config` value, a `--settings` or `--mcp-config` value written as its own word or attached as `--settings={…}`, or any word of `claude_args` or `codex-args` — publishes its key names, with `env` and `headers` values, `apiKeyHelper` and every secret-named value `<redacted>`, as canonical JSON; a codex `--config` override (`-c`, `--config=`, `-c<override>`, `-c=<override>`) under `env`, `headers` or a secret-named key publishes `<redacted>` for its value. A URL publishes its scheme, host and port, with `<redacted-path>` for any path and no query, as an MCP server's URL does (#723), so a change only to a URL's path or query is not reported; a `${{ }}` expression is one word while this is decided, so one in a URL's userinfo is withheld with it. Text that starts like JSON, or a codex `--config` table or array, and does not parse is withheld whole (`unparsed_json`).
- **Direction.** Only a documented rule a job's launches gain widens: bypassed permission checks (`--dangerously-skip-permissions` or `--permission-mode bypassPermissions`, one rule), `--dangerously-bypass-approvals-and-sandbox`, a `danger-full-access` sandbox, `safety-strategy: unsafe`, or a `*` entry in `allowed_bots`, `allowed_non_write_users` or `allow-users`. The rules each launch meets are decided from its declared text when the workflow is read, before anything is withheld, and published as `widening_rules` (`rule`, and the `setting` it was read from), so redaction never hides one; `claude_args` and `codex-args` are split as each action splits them, so a rule on any line of `claude_args: |` counts and one in a full-line `#` comment does not. It is read from literal text only: GitHub substitutes a `${{ }}` expression before the action reads the input, so a rule is read from the words of `claude_args` or `codex-args` before the first expression (less the word it touches and a quoted run open at it; in a JSON-array `codex-args`, the elements before the one holding it) and from the gate entries that hold none, and a `sandbox` or `safety-strategy` value holding one meets none. A setting holding one is published with `holds_expression: true` (omitted otherwise), and a row that changes it says the text the expression reaches is not read. Three gains are named in the `why` and not claimed: where the job launched that agent before only in a form this audit does not read, as for a job whose permissions were not explicit; where the job's launch held a `${{ }}` expression before in an input the rule is read from, whose substituted text may already have met it; and where the rule moved between jobs, because the launch that met it in another job left that job (the job no longer exists or no longer launches that agent, or the same launch now runs elsewhere), as a step reference moved between jobs adds no scope. Otherwise the grant earns `workflow_agent_widened_changed` (or `_added` for a new workflow), the row is `widened` with `expands: true`, its `why` names the rule and step, and the Stop hook announces it. Every other edit is `changed` with `expands: false`, including a tool rule such as `--allowedTools "Bash(*)"` (rating its reach is #824's), `acceptEdits`, a new plugin and a head-ref checkout. `access` and `risk` still describe the token and triggers alone.
- **The note on a workflow row.** Whatever the row is about, when its workflow runs an agent its `why` ends with the job facts beside each agent step: an untrusted-input trigger (`issue_comment`, `issues`, `pull_request_target`, `workflow_run`), the job's write scopes, the job's secrets, and a checkout of pull request code in the job. It moves no direction and is not a verdict. A removed workflow gets none.
- **Unresolved and unreadable values are a named limit, not a blocking one.** A `run:` holding more than one command, a shell expansion or a `${{ }}` expression is `form: unresolved` (`compound_command`, `shell_expansion`, `expression`) with no settings, as is an agent action whose `with:` is not a mapping (`inputs_not_a_mapping`). A setting that is not a string (`not_a_string`) or holds text that starts like JSON and does not parse (`unparsed_json`), and a ref that is not a string, has `value`/`ref: null`. A setting holding credential-shaped text is published redacted (`redacted`). Each records a non-blocking `unsupported` coverage issue naming its `job/step`, as an unread secret value does (#693): GitHub coverage stays `complete`, and adding, removing or re-forming such an entry, or its gaining a rule, is still a row. Only an edit inside it that gains no rule is not reported.
- **Credential-shaped text.** Other text the #802 label redaction rewrites — a token shape, a credential assignment, a bearer or header value, a URL's userinfo, and prose such as "never print bearer tokens" in a system prompt — is published redacted with `unresolved_reason: redacted`. In a setting it is compared as published, beside the rules read from its declared text, and named by the non-blocking limit above, so a permission change or a rule gained beside it is still a row and only an edit inside what is redacted is not reported. A checkout ref names the code a job runs, so a redacted one refuses as a redacted step reference does (#767): a changed workflow's comparison is refused, and an unchanged one is named in `unchanged_limits`.
- **What is not read.** An action outside the table, a composite action (#701), a script the step runs, an agent CLI reached through another command (`npx`, `timeout`, `sudo`, a path), and a step's `env:`, `shell:` and `if:`. The support page lists them under Known unread surfaces.

**Compatibility.**
- **A committed `0.4`, `0.5` or `0.6` baseline holding a workflow grant** is loaded but incomparable: it never read agent launches or checkout refs, so its silence is not evidence that none changed. `audit --host --drift` reports `comparison_status: incomparable` with `baseline_workflow_agent_launches_unavailable` among `incomparable_reasons` (beside the #771 and #693 reasons for a `0.4`/`0.5` one), `has_drift: null` and `next_action: null`, and exits `20` under `--fail-on-drift`; `preflight` raises a `high`, `actor: human` `host_grant_drift` signal naming it. To migrate, follow [the #771 steps](#workflow-step-action-references-contract-v40-771) from a checkout of the reviewed default branch, keeping the old file as `host-grants.v0.6.json`: review `audit --host`, move the baseline aside, `audit --host --save-baseline`, and confirm drift is comparable with `has_drift: false`.
- **A `0.4`–`0.6` baseline with no workflow grant** stays comparable for drift. `audit --host --save-baseline` refuses to overwrite any baseline older than `0.7`, with or without a workflow grant, and exits `2` with `unsupported_baseline_schema`; move it aside and re-save.
- **Git-backed `diff`, `check` and manifest-free `verify`** read both refs with the current reader and need no migration. Their rows keep their shape, and verifier `0.20` and capability diff `0.3` do not move. What changes is values: a workflow row can now be `widened` for an agent launch, its `before`/`after` cells list changed launches and checkout refs, and the `why` of every workflow row whose workflow runs an agent gains the note, so a consumer that matches `why` text exactly sees new text. No check id is added or removed, and `check` decides as before.
- **Validators pinned to the `0.6` schemas** reject a `0.7` inventory, baseline or drift payload.
- **`minimum_control_contract_version`** stays `21`.

<a id="unread-changed-inputs-821"></a>

## Migration Note: Unreleased — the changed inputs a host comparison does not read (verifier `0.21`, capability diff `0.4`, contract v41, #821)

A zero-row comparison could not tell a reviewer that the change touched agent
configuration this entry does not read. A pull request that added a Cursor
plugin's `mcp.json` launching `pipx run --spec git+https://…`, removed a
`beforeShellExecution` guard from `.cursor/hooks.json`, gave a dotfiles
package's `claude/.claude/settings.json` `Bash(*)` and `bypassPermissions`, or
moved a marketplace plugin's pinned `sha` printed `No static host-grant changes
detected`, exactly as a docs-only change does, and `audit --host` reported
every host `complete`. The coverage block could not help, because it named only
sources an inventory read. On a 23-PR public corpus, 0 of the 9 comparable
zero-row results that changed such a file named it, and 4 of them named a file
the pull request did not touch while omitting the one it did.

The comparison now names them, inside the existing `coverage` member. No
command, verdict, reader, row or control state is added.

```json
"coverage": {
  "items": [
    {"source": "plugins/demo/mcp.json", "hosts": ["cursor"], "side": "head",
     "status": "changed_not_read", "rows": 0, "limit": null, "detail": null,
     "candidate": "plugin_mcp_config", "scope": null}
  ],
  "omitted_items": 0,
  "read_sources_only": false,
  "unread_candidates": "examined",
  "unread_candidates_not_examined": 0
}
```

- **Where.** `host_comparison.coverage` in `verifier.json` (verifier schema `0.21`, [`docs/verifier-schema.v0.21.json`](docs/verifier-schema.v0.21.json)) and top-level `coverage` in `shipgate diff --json` (capability diff `0.4`) — the same object for the same comparison, as since #812. `check`'s boundary result and a provided diff carry no coverage and look for nothing.
- **What is looked at.** Only the comparison's own changed-file set: the committed `base..head` change, both names of a rename, or for a working-tree head the tracked changes against the base plus untracked files, without `verify`'s output directory. Never a walk of the repository, so an unchanged candidate is never named.
- **`status: changed_not_read`.** A changed path a documented candidate rule names and no reader of this entry read. `candidate` is the rule: `plugin_mcp_config` (`mcp.json` in a directory holding a Claude Code, Codex, Cursor or Copilot plugin manifest), `plugin_manifest_mcp_servers` (a manifest's `mcpServers` member), `plugin_manifest_hooks` (a Codex, Cursor or Copilot manifest's `hooks` member; a Claude Code manifest's is read), `plugin_hook_file` (a hook-named file such a manifest's `hooks` names), `unparsed_plugin_manifest` (a changed manifest or marketplace whose members could not be compared), `cursor_project_hooks` (`.cursor/hooks.json`), `nested_host_settings` (Claude Code, Cursor or VS Code settings below the repository root, whose scope is not established) and `external_plugin_source` (a marketplace entry's object `source`, compared as text by entry name). The rules, their precedence and their bounds are listed in [`docs/host-boundary-support.md`](docs/host-boundary-support.md#changed-inputs-named-but-not-read). On such an item `hosts` is the host the rule attributes the path to — a manifest's host, which can be `copilot`, `cursor` for `.cursor/hooks.json`, or the settings file's host — and never a host whose reader read it, since none did.
- **`source`.** The file, through `public_host_path`, or a member inside it: `<manifest>#mcpServers`, `<manifest>#hooks`, `<marketplace>#plugins.<name>`. An entry `<name>` is published as `detail` is — redacted as a whole, on one line, a lone surrogate as its `\uXXXX` escape, at most 100 characters — and followed by `~` and a 12-character digest of the exact name whenever that changed it, so two entries that publish alike stay two items. A member is named when its text differs between the sides — added, removed or edited — whatever else read the file, because no reader reads that member. A whole file is named only when no inventory published it as an artifact, the file of a grant or a blocking issue: a file a reader read is an item of its own.
- **`side`.** Where the file or member exists: `head` added, `base` removed, `both` changed. Nothing published it, so the text says `added`, `removed` or `changed` rather than `read in`.
- **`detail`.** For `external_plugin_source`, what the source now names (`was` for a removed one): its kind, `repo`, `url` or `package`, `path`, and `sha`, `ref` or `version`, as `github example/one at <sha>`, redacted as every published string is, a lone surrogate as its `\uXXXX` escape, and at most 200 characters. A URL is published only in the engine's sanitized form. Nothing is fetched. `null` for every other rule. `limit` is always `null` and `rows` always `0`.
- **Order and cap.** Directly after the blocking limits and before every other item, because it is the one change nothing else in the output shows at all; inside the same cap of ten, counted in `omitted_items` past it and printed as `N more items not listed, each ranked below those above`. It may accompany a refused comparison beside its blocking limits: it is not a compared source.
- **`read_sources_only`.** `true` exactly when no item, listed or omitted, is `changed_not_read`. Neither value makes the list a complete account: the rules are a bounded list, and any other changed file no reader reads is still absent.
- **`unread_candidates`.** `examined` when the changed-file set was matched against the rules; `not_examined` when it could not be listed or its sides could not be looked at, so no candidate is named and an absent one says nothing; `null` when not recorded. **`unread_candidates_not_examined`** counts candidate paths that were not examined, for either of two causes the one count does not tell apart: past the bound of 32, or the rule needed a file that was present but not read within its bound, or read but not a JSON object. The second cause is a hook file whose Codex, Cursor or Copilot manifest in an ancestor directory was not read or did not parse while no readable one names it, and a changed manifest or marketplace present on a side but not read as a regular file within the bound, such as a link; a changed manifest or marketplace that was read but does not parse is an `unparsed_plugin_manifest` item instead. Counted, never guessed at, and never counted in `omitted_items`.
- **Bounds.** At most 32 candidate paths are examined per comparison, in path order. For all of them together, one presence question and one read per side (split across Git invocations only to keep each within its argument bound), reading plugin manifests and marketplaces only; a hook file's manifest is searched in its eight nearest ancestor directories. No link is followed, nothing outside the repository is read, nothing is fetched or run. Paths under a directory the host readers never walk (`node_modules`, `.venv` and the like) are not considered.

**Text.** `diff`, `verify --format text` and the manifest-free PR comment print each item as `<source> (<hosts>): <added|removed|changed>, not read by this entry: <what it is>; no row, and loading is not established`, for example `plugins/demo/mcp.json (cursor): added, not read by this entry: MCP configuration in a plugin directory; no row, and loading is not established`; an external source reads `…: an external plugin source, now github example/one at <sha>; no row, and its content is not fetched`. While such an item is named, the block's first line, which read `only sources this entry read or tried to read are listed, so this is not the whole change: a changed file it does not read is absent` and would then be false, reads instead `sources this entry read or tried to read are listed, and changed inputs a bounded candidate list names that it does not read, so this is not the whole change: any other changed file it does not read is absent`. When nothing is named the first line is unchanged, byte for byte. Under it, `the changed files could not be listed or looked at, so no changed input this entry does not read is named` when the set was not examined, and `N changed candidate inputs not examined: past the discovery bound, or a file the rule needed was not read or did not parse` when `unread_candidates_not_examined` is not `0`, for either cause; like the boundary, neither is dropped to make room for an item. The PR comment bounds the block exactly as before. The no-change, entries and cannot-compare headlines are unchanged.

**One route moves, on `verify` and `verify --preview` alike.** `verify` without a `shipgate.yaml` returned to the setup route (`Shipgate config not found`, exit `2`) whenever neither side of the comparison held a host artifact, and that route says nothing about the change. A comparison that read no artifact but names a changed input this entry does not read, or counts one or more changed candidate inputs as not examined (`unread_candidates_not_examined` above `0`, the one place that change is mentioned), is now published instead, on the existing manifest-free host route: advisory, exit `0`, `control.state` `agent_action_required` with the `audit --host` next action that route already names. `verify --preview` runs the same comparison and moves the same way: where its next action was `initialize` (`init --write`) with `host_comparison: null`, it is now `discover` (`audit --host`) with the comparison published and the host route's headline; `control.state` stays `agent_action_required` and the exit stays `0`. That includes an agent-related workspace, such as one whose change also adds a tool: a published host comparison takes the preview route whenever one exists, exactly as it already did when the change edits a host file this entry reads, such as the root `.claude/settings.json`. A comparison that reads no artifact, names nothing and counts nothing as not examined still takes the setup route on `verify` and `initialize` on `verify --preview`, as before; so does one whose changed files could not be listed (`unread_candidates: not_examined`), which says nothing about whether a candidate changed.

**What does not change.** `comparison_status`, `incomparable_reasons`, `rows` and every row value, `review`, `unchanged_limits`, every other coverage item, the inventory digests, saved host-grants baselines and drift payloads (host-grants stays `0.6`), `audit --host`, `check`'s decision, rows and text, the control envelope's `capability_rows`, and every control state, permission and next action on a comparison that reads a host artifact. The host-config and cold-start benchmark replays reproduce their run-of-record scores. `minimum_control_contract_version` stays `21`.

**Compatibility.** `coverage` and its items are closed objects, so a reader validating against the published [`docs/verifier-schema.v0.20.json`](docs/verifier-schema.v0.20.json) rejects a `0.21` artifact's new members; that schema stays frozen. The current reader reads a `0.20` artifact as `0.21` with `unread_candidates: null`, which is what that build knew, and refuses one that claims a `changed_not_read` item, a `candidate`, `read_sources_only: false` or either `unread_candidates` member. A `diff --json` consumer sees `capability_diff_schema_version: "0.4"`. A consumer switching on `coverage.items[].status` should treat an unknown status as a change it must read, not as no change.

<a id="claude-setting-ratings-827"></a>

## Migration Note: Unreleased — one rating per Claude Code setting (#827)

This change moves no version of its own: no schema, member, check id or
`minimum_control_contract_version` moves, and host-grants stays `0.6`, as
shipped in 1.1.0. The capability diff `0.4`, verifier `0.21` and runtime
contract `41` of the unreleased tree are #821's
([migration note](#unread-changed-inputs-821)), not this change's. What moves
is the value of existing fields for the Claude Code settings the host inventory
publishes as `permission_mode` grants. One table, `core/host_settings.py`, now
rates each value, and the grant's `access` and `risk`, a row's `severity`,
`before`, `after` and `why`, and `check`'s violation — and so `verify`'s
finding — all read it. The ratings and their basis are in
[`docs/host-boundary-support.md`](docs/host-boundary-support.md#claude-code-setting-ratings).

| Value a change sets | Grant `risk` before → after | `check` before → after |
| --- | --- | --- |
| `defaultMode: bypassPermissions` | `medium` (`unknown`) → `critical` (`admin`) | `…-PERMISSION-WILDCARD-ALLOW`, `block`, `critical` (unchanged) |
| `defaultMode: dontAsk` | `medium` (unchanged) | `…-PERMISSION-WILDCARD-ALLOW`, `block`, `critical`, kind `permission_mode_expanded` → `…-PERMISSION-ALLOW-EXPANDED`, `require_review`, `medium`, kind `permission_mode_changed` |
| `defaultMode: acceptEdits` | `medium` → `high` (`write`) | `…-PERMISSION-ALLOW-EXPANDED`, `high` (unchanged) |
| `defaultMode: auto`, or a mode Claude Code does not document | `medium` → `high` | `…-PERMISSION-ALLOW-EXPANDED`, `high` (unchanged) |
| `defaultMode: plan` or `default` | `medium` (unchanged) | `…-PERMISSION-ALLOW-EXPANDED`, `high` → `medium` |
| `enableAllProjectMcpServers: true`, `skipDangerousModePermissionPrompt: true` | `critical` (unchanged) | `…-CONFIG-PARSE-FAILED`, `require_review`, `medium`, kind `unknown_host_config_key` → `…-PERMISSION-WILDCARD-ALLOW`, `block`, `critical`, kind `permission_mode_expanded` |
| `false` for either, `disableAllHooks`, `allowManagedPermissionRulesOnly`, `allowManagedHooksOnly`, or a top-level `disableBypassPermissionsMode` | `medium` (unchanged) | `…-CONFIG-PARSE-FAILED`, `medium` → `…-PERMISSION-ALLOW-EXPANDED`, `medium`, kind `permission_mode_changed` |
| any of these set under `permissions` (for example `permissions.disableBypassPermissionsMode`) | as above | `…-PERMISSION-ALLOW-EXPANDED`, `high`, kind `claude_permission_boundary_changed` → the value's rating and rule, as above |
| a value moved between `permissions` and the top level without changing it | unchanged, and no row, as on 1.1.0 | the value's rule and rating, as when a change first sets it, in either direction. A top-level `defaultMode: bypassPermissions` moved into `permissions`, where Claude Code reads it, still blocks with `…-PERMISSION-WILDCARD-ALLOW` at `critical`, and `acceptEdits` still raises `…-PERMISSION-ALLOW-EXPANDED` at `high`, as on 1.1.0; a value moved out of `permissions` reads as the top-level rows above |
| an `enabledMcpjsonServers` entry | no grant → one `high` (`external`) grant per server | `…-CONFIG-PARSE-FAILED`, `medium` → `…-PERMISSION-ALLOW-EXPANDED`, `high`, one per added server |
| an `enabledMcpjsonServers` entry that names no server (an object, a number, a blank string) | no grant → one `high` grant per distinct entry, kept whole | `…-CONFIG-PARSE-FAILED`, `medium` → `…-PERMISSION-ALLOW-EXPANDED`, `high`, one per added entry |

- **Check ids.** None is added, removed or renamed; per [Check IDs](#check-ids),
  the conditions under which three of them fire move, and this note records
  it. `SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW` now also fires for
  `enableAllProjectMcpServers: true` and `skipDangerousModePermissionPrompt: true`,
  and no longer for `defaultMode: dontAsk`.
  `SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED` fires for `dontAsk`, the other
  modelled settings and each added `enabledMcpjsonServers` entry, at the value's
  rating, which may be `medium`: below the catalog's default of `high` and at
  its `floor_severity`. `SHIP-HOST-BOUNDARY-CONFIG-PARSE-FAILED` no longer fires
  for a modelled setting. A suppression or baseline entry keyed on the old id or
  evidence for one of these values no longer matches, so the finding reads as
  new. `defaultMode` evidence keeps its shape (`kind`, `mode`), so
  `bypassPermissions`, `acceptEdits`, `auto`, `plan` and `default` findings keep
  their fingerprints; every other setting's evidence names `setting` and `value`.
  Either carries the value as the setting's `audit --host` grant publishes it:
  credentials redacted before it is rendered, then cut to at most 200
  characters. Neither changes a documented mode or switch value.
- **Decisions.** `check` alone on `enableAllProjectMcpServers: true` or
  `skipDangerousModePermissionPrompt: true` moves from `require_review` /
  `agent_action_required` to `block` / `human_review_required`, and a
  manifest's `verify` reports a `critical` finding that blocks the release. On
  `dontAsk` alone it moves from `block` / `human_review_required` to
  `require_review` / `agent_action_required`, a `medium` review item. On
  `plan`, `default` or a setting under `permissions` rated `medium` it moves
  from `review_publishable` to `agent_action_required`; the pull request still
  goes to a human. On an `enabledMcpjsonServers` entry it moves from
  `agent_action_required` to `review_publishable`.
- **Rows.** A `permission_mode` or `sandbox` row's `before` and `after` name
  the setting and the value as the file spells it, for every host:
  `enableAllProjectMcpServers: true` for `True`, `defaultMode: dontAsk` for
  `dontAsk`, `approval_policy: never` for `never`. A string where the setting's
  documented value is a boolean is quoted (`"True"`), so it is not read as
  one. A Claude Code setting's `why` is the table's basis, such as "skips every
  permission prompt, so any tool call runs without one", instead of "changes a
  permission_mode grant". Every row keeps its direction and `expands`: a mode
  added or changed is still `⚠`, because the direction between two modes is
  not modelled.
- **Policy.** A host-boundary policy that raises either rule above its engine
  default raises these violations to at least its level; `DEFAULT_RULES` stays
  the floor for a policy file. The packaged policy states the defaults, so it
  changes nothing.
- **Compatibility.** A host-grants baseline saved before this change that
  holds `bypassPermissions`, `acceptEdits`, `auto` or an undocumented mode
  recorded it at `medium`. The grant id is unchanged, so drift reports one
  `changed` grant for it, with no expansion signal, and `--fail-on-drift`
  exits `20` once. One holding `enabledMcpjsonServers` reports each server as
  an added grant with `permission_mode_added`. Review the rows and re-save the
  baseline.
- **Not changed.** Removing a setting raises nothing of its own, as removing
  `defaultMode` never did; a settings change with no other violation still
  raises `SHIP-AGENT-BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED`.
  `disabledMcpjsonServers`, `outputStyle` and every other unmodelled key still
  read as `SHIP-HOST-BOUNDARY-CONFIG-PARSE-FAILED` with `unknown_host_config_key`.
  A scalar setting set under `permissions` is read there only, so a top-level
  copy beside it stays an unknown key. Codex, Cursor and VS Code setting ratings
  are unchanged. Every vendored host-config and cold-start case replays to its
  recorded outcome, and the recorded runs score as published: the benchmark's
  row matcher reads both spellings of a setting row.

<a id="enabled-plugin-hook-routing-809"></a>

## Migration Note: Unreleased — an enabled plugin's hook is routed wherever it lives (#809)

No schema, member, check id or `minimum_control_contract_version` moves. This
change adds no version of its own: host-grants, capability diff, verifier and
the runtime contract are what the rest of the unreleased tree carries. What
moves is when two existing checks fire, and so `check`'s decision and control,
and a manifest-backed `verify`'s release decision, merge verdict and PR
comment.

1.1.0 published a hook that a plugin selects, where the repository's project
settings enable that plugin from a marketplace inside the repository, as
`execute`/`high` with an expansion signal (#714). But `check`, and the boundary
check a `verify` with a manifest runs, routed only registry paths such as
`.claude/hooks/hooks.json`. A changed hook file at any other path therefore
gave a `widened`, `expands: true` row beside `decision: allow` and
`control.permissions.merge: true`, and `verify` gave `passed` / `mergeable`.
`1.0.0` allowed the same change with no row.

| A change to | 1.1.0 | now: `check` | now: `verify` with a manifest, and its PR comment |
| --- | --- | --- | --- |
| a hook file an enabled plugin selects outside the registry paths, changed, added or deleted | `check` `allow`, `complete`; `verify` `passed`, `mergeable` | `require_review`, `agent_action_required`, `SHIP-AGENT-BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED` | `review_required`, `human_review_required`, the same finding |
| a `.claude-plugin/plugin.json` or `.claude-plugin/marketplace.json` whose inline hooks an enabled plugin loads | the same | the same | the same |
| a hook file or plugin manifest of those the head leaves unreadable | `check` `allow`, `complete`, comparison `incomparable`; `verify` `passed` | `require_review`, `human_review_required`, `SHIP-AGENT-BOUNDARY-INPUT-INCOMPLETE` as well, input `partial`, as at a registry path | `review_required`, `human_review_required` |
| such a marketplace the head leaves unparseable | `check` `allow`, `complete`; `verify` `passed` | `require_review`, `agent_action_required`, input `complete`; a marketplace limit never blocks, and the comparison shows its inline hooks as `removed` | `review_required`, `human_review_required` |
| a hook file an enabled plugin selects under a name the reader does not follow, or in a directory the walk skips | `check` `allow`, `complete`; `verify` `passed` | `require_review`, `human_review_required`, `SHIP-AGENT-BOUNDARY-INPUT-INCOMPLETE`, input `partial` | `review_required`, `human_review_required`, the same finding |
| a hook file a plugin selects without that enablement | `allow`; `passed` | unchanged | unchanged |
| a manifest or marketplace that only references hook files, a registry path, or any other file | as before | as before | as before |

- **The route takes two facts.** The path is one the plugin hook reader opens
  — a hook-named file, a plugin manifest or a marketplace — which the name
  decides, and the reader found, in the base or the head, that a plugin the
  project settings enable loads hooks from it. This is the selection that
  publishes the hook as `project_enabled_plugin`, read from the reader rather
  than back from a grant's `access`/`risk` pair. The base tree is read, as the
  host comparison reads it, only when a changed path is such a file, so a
  hook file deleted along with its reference is still routed. `check` and
  `verify` read both sides through one function, so they decide one change
  alike. For a provided `--diff` only the workspace tree is read. If a
  compared commit cannot be read, each such path is
  `SHIP-AGENT-BOUNDARY-INPUT-INCOMPLETE` with code `host_inventory_unreadable`
  rather than a guess.
- **A file the reader does not open.** An enabled plugin can select its hooks
  from any `./` path, and the static reader follows only `hooks.json` and
  `<name>-hooks.json` outside the directories its walk skips. The inventory
  already names such a file as an `unsupported` limit. `check` left that limit
  out of its decision; where the change touches the file, it now counts, with
  code `host_inventory_unsupported`, from the base when the head no longer
  selects the file. Such a file is not routed to protected-surface review,
  because nothing in it was read.
- **Evidence.** The protected-surface violation's evidence adds
  `hook_loading_basis: project_enabled_plugin` beside
  `kind: protected_surface_unclassified`, only where no registry name or trust
  root explains the route, so every existing row keeps its fingerprint.
  `host_coverage` lists the file under the `claude_code` adapter, and
  `affected_hosts` names `claude-code`.
- **Check ids.** None is added, removed or renamed. Per
  [Check IDs](#check-ids), the conditions under which
  `SHIP-AGENT-BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED` and
  `SHIP-AGENT-BOUNDARY-INPUT-INCOMPLETE` fire broaden to these files, in
  `check` and `verify` alike, and this note records it. No existing
  suppression stops matching.
- **Not changed.** The rows, their `why`, `expands` and severity, every
  inventory, baseline and drift payload, `diff`, a manifest-free `verify` (its
  host route is advisory and already withheld merge without a human) and the
  trigger catalog. The catalog is a path-only relevance screen that cannot
  read enablement; it already reports such a file as `unclassified` rather
  than a skip.
- **Remaining limit.** A change only to a selector, a manifest's `hooks`
  reference or a marketplace entry, that makes an enabled plugin load an
  existing, unchanged hook file is still `allow` beside that file's `added` or
  `widened` row. `check` gives the same when the selected file is
  `.claude/hooks/hooks.json`, so this limit is not specific to plugin paths.

<a id="preview-control-currency-807"></a>

## Migration Note: Unreleased — a preview is read against the working tree it read, configured or not (#807)

No schema, contract, member, error kind, refusal code, exit code or
`minimum_control_contract_version` moves. What changes is which artifacts a
`verify --preview` pointer binds and what its `workspace_identity` records in
a repository with a manifest, and so what `verify --preview --format control`
(and `--format text`'s control headline) and an `agent control` refresh of that
pointer answer.

**Why.** With a manifest present, `verify --preview` records the verification
plan a `verify` would run (`verification-plan.json`, embedded in
`verify-run.json`), built from what the manifest declares. Its pointer bound
that plan, took its `workspace_identity` from it, and so made the plan's
recorded inputs its currency test. A preview runs no adapter, so none of those
inputs was captured and the plan carries no `inputs.options.input_directories`
census, and a missing census is unknown capture, which every reader refuses
([the census](docs/verification-reproducibility.md)). On `1.0.0` and `1.1.0`,
in a repository with a committed `shipgate.yaml`, `verify --preview --json`
answered `agent_action_required` with the exact `verify` command, while
`verify --preview --format control` answered `human_review_required` ("The
recorded source and dependency inputs are no longer current: input directory
capture is unavailable; re-run verification") and `agent control` exited `4`
with `workspace_unverifiable`, with the default `--out`, an in-repository one
and a sibling one alike. Re-running reproduced it.

**Now.**

| In a repository with a manifest | before | now |
| --- | --- | --- |
| `verify --preview --format control` | `human_review_required`, no command | `agent_action_required`, the `--json` `next_action` (`kind: "verify"` and its command) |
| `agent control` over that pointer, worktree unchanged | exit `4`, `workspace_unverifiable` | exit `0`, the same state, command and `current_control_id` |
| after a tracked edit or a new untracked file | exit `4`, `workspace_changed`, as a change the decision never saw | exit `4`, `workspace_changed`, naming up to three paths that differ from HEAD, redacted |
| after that change is undone | exit `4`, `workspace_unverifiable` | exit `0`, current again |
| the pointer's `artifacts` | include `verification_plan` | the verifier route without `verification_plan` |
| its `workspace_identity` | the plan's: base, merge base, policy snapshot, the plan's overlay | the working tree's: repository, HEAD, its tree, `snapshot_kind: "worktree_overlay"` and the overlay of every path differing from HEAD |

A manifest-free preview already published that shape and answers as before,
except in two ways. Its `workspace_changed` refusal now names the paths as
well: it said the paths it was read from "no longer have the content they
had", which was false for a tracked edit or a new file after a preview of a
clean tree. And a repository whose Git configuration the worktree readers
refuse (#813) no longer leaves it current:

| Under Git configuration the worktree readers refuse | before | now |
| --- | --- | --- |
| a pointer published inside the repository without a plan: a preview, or a `verify` that stopped before building one (a `--config` or `--head` that does not exist) | `workspace_identity` without a `snapshot_kind`, so the refresh compared HEAD alone: `agent control` exit `0` over any later edit | `snapshot_kind: "worktree_overlay"`, with no overlay when it could not be read: exit `4`, `workspace_unverifiable`, the cause first and a `review` next action, as for every other pointer there |
| `--format control` of that run | `agent_action_required` with its route; a configured preview, which bound its plan, was already `human_review_required` with the cause | `human_review_required`, the cause first; `--json`, the exit code and the route in `verifier.json` are unchanged |

Such a pointer whose overlay could not be read claims none: once the
configuration is gone, it reads as current only over a tree identical to HEAD,
the committed evidence a preview falls back on when it cannot read the tree.
Outside a repository a preview still declares no snapshot and still reads.

- **Unchanged.** `verify --preview --json`, its exit code, `verifier.json`,
  `verify-run.json` and the plan's bytes. The one containment rule every output
  directory follows (#575, #804): a directory outside the repository needs no
  exclusion, and one inside it is left out of the overlay and must be ignored
  or hold nothing but Shipgate artifacts. The causes an unreadable workspace
  is refused with (#813). Every `verify` pointer, which binds its plan as
  before.
- **Still refused.** A pointer that binds a plan without its census —
  including one a `1.0.0` or `1.1.0` configured preview left in a reports
  directory — is `workspace_unverifiable`, and `agent control`'s next action
  re-runs `verify`; re-running the preview replaces the pointer. The plan gains
  no census, so `verification worker` still refuses to replay it.
- **What the refresh checks.** A preview's answer rests on the working tree,
  not on the plan's inputs, so it is compared the way a manifest-free preview
  is: HEAD, its tree and the live overlay. A base the preview detected is not
  bound, as it was not for a manifest-free preview, and a gitignored file is
  outside the overlay: removing a manifest kept out of Git, such as a local
  review manifest in `.git/info/exclude`, does not make the preview stale, and
  the `verify` it routes to then reports the missing manifest.
- **Compatibility.** A consumer that read a configured preview's
  `verification_plan` through the pointer reads `verification-plan.json` or
  `verify-run.json` directly; neither was ever current evidence for a preview.

<a id="host-comparison-coverage-812"></a>

## Migration Note: 1.1.0 — what each host comparison established (verifier `0.20`, capability diff `0.3`, contract v40, #812)

A reviewer given zero rows could not tell a docs-only change from an `env` or
`apiKeyHelper` edit this entry does not compare, or from a deleted settings file
that held only such fields: `diff`, `verify` and the PR comment printed the same
"No static host-grant changes detected" for all three. A value-only edit left
even `verifier.json`'s own inventory digests equal, because the inventory
redacts those values. An incomparable result printed
`head_inventory_incomplete` and named no source. Both answers needed a separate
`audit --host`, which reads only the head.

The comparison now publishes what it established, from facts it already
computed: the grant changes behind the rows, the artifact changes, the sources
each inventory observed and the blocking issues each carries. No discovery rule,
verdict, command or input is added.

```json
"coverage": {
  "items": [
    {"source": ".claude/settings.json", "hosts": ["claude-code"], "side": "both",
     "status": "changed_without_grant_change", "rows": 0, "limit": null, "detail": null, "scope": null}
  ],
  "omitted_items": 0,
  "read_sources_only": true
}
```

- **Where.** `host_comparison.coverage` in `verifier.json` (verifier schema `0.20`, [`docs/verifier-schema.v0.20.json`](docs/verifier-schema.v0.20.json)) and top-level `coverage` in `shipgate diff --json` (capability diff `0.3`). The two are the same object for the same comparison. `null` means coverage was not recorded: a `0.19` or older verifier, or a comparison that never read an inventory, such as `shallow_history`.
- **`status`.**
  - `compared`: the source was read, and `rows` of the published rows come from it. A row of a source inside a file — a Codex profile, `.codex/config.toml#profiles.dev`, or a marketplace entry's inline hooks, `.claude-plugin/marketplace.json#plugins.demo` — counts for that file, so the file is one item. With `0` rows on `both` sides, the file's bytes were proven identical on both sides: the same regular-file blob, exactly as the Git blob identity check `unchanged_limits` uses proves it. `diff` and `verify` ask Git privately, in one question for every such file, because the artifact digest redacts values such as `env` values and `apiKeyHelper` and so cannot show them change. The answer is identical, differs or neither shown (review cycle 3), and only identical is `compared`: nothing that differs, or that could not be proven identical, is ever called unchanged. It does not mean every field in the file was understood. With `0` rows on one side, the file declares no grant this entry compares and its artifact did not change, as for a new guidance-only instruction file.
  - `changed_without_grant_change`: the file changed, it gives no row, and the published data shows no grant this entry compares moved. Either its artifact, which digests the whole file, changed while every side that has the file parsed it and nothing but that digest differs, or Git shows its content differs while its artifact did not, as for an edited `env` value, `apiKeyHelper` or MCP server `env` value, which the digest redacts and no comparison compares. Git shows a difference between commits as two different blobs, and in the working tree as bytes that still differ from the base blob once `CRLF` is read as `LF`, on a path no `filter`, `ident` or `working-tree-encoding` attribute converts; no filter is run to tell. It does not say which fields changed: a reordered or repeated rule moves the digest and no grant, and the change may be in a field a grant reads without changing the grant. On one side only it is a new or deleted file that declares no compared grant. Never a plugin manifest or marketplace, a retargeted link, or `.claude/settings.json` or `.claude/settings.local.json` while a hook's loading basis changed, since those settings decide which plugin hooks load and that change is published on the hook file.
  - `changed_without_rows`: the file changed and no row is attributed to it, but the data does not show that no compared grant moved. A plugin manifest or marketplace publishes only its `hooks`, and the rows of a reference it adds, retargets or removes are published under the hook files it selects; a retargeted link changes `resolved_through`; a parse status or instruction structure is read too; and project settings decide a hook's loading basis.
  - `unchanged_not_proven`: both sides read the file, it gives no row and its artifact did not change, but its bytes could be neither proven identical nor shown to differ, so a change in a value the artifact redacts would not show. Nothing is asked for a provided diff, a file read through a link (`resolved_through`), a redacted published path, which names no file, or a source no artifact publishes on both sides. Git shows neither for a working-tree file that differs from its base blob only as a checkout conversion makes it — `eol=crlf`, or `core.autocrlf=true` as Git for Windows sets it, which Git itself reports as no change — for a path a `filter`, `ident` or `working-tree-encoding` attribute converts, for a mode-only change between commits, a file past the read bound, or any Git failure. Never read it as no change, and never as a change.
  - `blocking_limit`: only on an `incomparable` comparison. The source carries a blocking inventory issue; `limit` is its kind (`unreadable`, `unsupported`, `parse_failed`, …) and `detail` the issue's published message.
- **`side`.** Which inventories published the source, as an artifact or as the file of a grant, or carry the limit: `base`, `head` or `both`. A file is published whenever an inventory reads it, so a deleted file stays attributable as `base`, and `head` is a new or untracked file, such as `.claude/settings.local.json`, or a hook file only the head's plugin configuration selects. A plugin manifest or marketplace is published only while it declares hooks, so for one of those `side` does not say whether the file exists on the other side.
- **One item per source, status, side and limit.** Hosts that read one source alike are merged into `hosts`, and their rows are summed. When nothing is omitted, the items' `rows` add up to the comparison's row count.
- **Order and cap.** Blocking limits, then changes with no row (`changed_without_grant_change`, `changed_without_rows`), then sources only one side published, then `unchanged_not_proven`, then sources both sides read that gave rows, then sources proven unchanged; by source within each. What the entries cannot show comes before a file's rows, which they already show (review cycle 5), so ten files with rows never count an `env` edit away. Within the blocking limits the kind decides next, most actionable first — `unreadable`, `parse_failed`, `unresolved_precedence`, then `unsupported`, `dynamic_source_excluded`, `remote_source_excluded` — because a refused comparison publishes nothing else and the source name alone used to decide the cap: on one corpus repository twenty-one routine `unsupported` items sorted ahead of the single `unreadable` source and pushed it past ten. That is order, not severity: no kind is dropped, none is called worse than another, and `unsupported` carries both a file this profile merely does not accept and one whose own text would not parse, so it ranks kinds rather than items and an item behind the cap may still be one to repair. `limit` is a closed set, so those six are every kind this version publishes and the order has no position past them: the sort key stays total for an unregistered kind, sorting it after the six rather than ahead of them, but an item carrying such a kind is refused by the model immediately after the sort when the cap keeps it, and past the cap is only counted into `omitted_items`, so it is never printed last either. Within a rank — and within the blocking limits, after the kind — the source name decides, and after it every remaining field an item is keyed by: its side, its limit and its status. No two items can tie, so the same two inventories always publish the same list. At most ten items; `omitted_items` counts the rest, which are always the lowest-ranked, so a cap drops a quiet source first and a file with rows next.
- **`read_sources_only`.** Always `true`, like `static_analysis_only` beside it: every item is a source an inventory read or was refused by, so the list is never a complete account of what the change touched. See **What it does not claim**.
- **Not repeated.** A source already named in `unchanged_limits` is not an item.
- **`scope`.** Reserved for a comparison decided per scope (#808). Always `null` in this version.
- **Redaction.** `source` is the path the inventory already publishes through `public_host_path`, so a credential-shaped component reads `[REDACTED:…]~<digest>`. `detail` is the inventory issue's sanitized message.

**Text.** `diff`, `verify --format text` and the manifest-free PR comment print the block as `What this run established:`, and under it, always, one line stating what the list cannot be read as: `only sources this entry read or tried to read are listed, so this is not the whole change: a changed file it does not read is absent`. That line is not an item and is never dropped to make room for one; a bound that cannot hold it and a count prints no block at all. `diff` prints it after the rows and summary (before the review question), after the no-change line, or after the cannot-compare lines; `verify` and the PR comment print it after the entries or the no-change or unavailable line. Each item a reviewer must read is one line — `.claude/settings.json (claude-code): compared; changed, but no grant this entry compares changed, so no row (redacted values such as env values and apiKeyHelper are not compared)`, `.claude/settings.json (claude-code): compared; no grant this entry compares changed, but the file was not proven unchanged (redacted values such as env values and apiKeyHelper are not compared)`, `.claude-plugin/plugin.json (claude-code): compared; changed, but no row is attributed to this path`, `… read in base only; 2 rows`, `.mcp.json (claude-code): parse_failed in head, so the head inventory is incomplete` — and sources proven byte-identical share one line, `compared with no change in what this entry reads: <first three> and N more`. On one side only, a file that holds no compared grant reads `read in base only; declares no grant this entry compares, so no row (redacted values such as env values and apiKeyHelper are not compared)`. A file whose path the inventory reads as instructions, such as `AGENTS.md`, `CLAUDE.md`, a skill or a Cursor rule, holds no such value, so its line has no redacted-values note: a `CLAUDE.md` link to `AGENTS.md` reads `CLAUDE.md (claude-code): compared; no grant this entry compares changed, but the file was not proven unchanged` on a docs-only change. One side is `read in base only` or `read in head only`, except for a plugin manifest or marketplace, which is `published by base only` or `published by head only`. A new instruction file the engine reads as guidance publishes no grant: `AGENTS.md (claude-code, codex, cursor): read in head only; declares no grant this entry compares, so no row`. Items past the cap are `N more items not listed, each ranked below those above` (items, not sources: one source can be several items), so a capped block says in words that it is capped and that nothing more actionable is hidden behind the count; where nothing at all is listed it is `N items not listed`. The PR comment's human summary is bounded as a whole, so there the block gets only the room its other lines leave — the entries, the review question and reproduction, and the advisory, next action and evidence after it — and at most 2000 characters of it. It lists what fits, counts the rest the same way (`N items not listed` when it lists none), and is left out when not even the heading, the boundary and that count fit. So the block never pushes out a line the comment prints without it (review cycle 5). A row list long enough to fill the comment by itself still truncates it, as on `1.0.0`. A comparison that read no source prints `What this run established: no host configuration source was compared.`, with the same boundary line under it. The no-change, entries and cannot-compare headlines are unchanged.

**What does not change.** `comparison_status`, `incomparable_reasons`, `rows`, `unchanged_limits`, the identity answer itself (which is not published), the inventory digests, saved host-grants baselines and drift payloads, `check`'s boundary result and text (which carry no coverage, so neither `check` nor a provided diff asks Git anything for it), the control envelope's `capability_rows`, and every control state, permission and next action — an incomparable manifest-free `verify` still routes to `audit --host`. `minimum_control_contract_version` stays `21`, and runtime contract v40, unpublished before 1.1.0, is extended in place.

**What it does not claim.** The list names only sources an inventory already observed. A file this entry does not recognize, such as a plugin's `mcp.json` or an unselected hook file, is not an item, so its absence says nothing about it; naming relevant unread candidates is #821. That was true of slice 1 and is now stated where it is read, in the block's own first line and in `read_sources_only`, because a true list under the heading `What this run established` still reads as the account of the change: on one corpus pull request the block listed seventeen items, named neither of the two files the change added — a hooks wiring file and a pin script — and showed three bare hook removals, inviting the conclusion that the hooks were deleted rather than moved.

**Wording.** An instruction file whose declared structure this entry could not establish publishes a blocking `unsupported` inventory issue, whose message is what `audit --host`, `unchanged_limits[].detail` and a refused comparison's coverage `detail` print. It read `Instruction structure is unresolved (<reason>); repair or review this declared surface.` for every reason, including the many that refuse a file whose own text parses — a documented field in a shape this bounded profile does not accept (`frontmatter_invalid_structure`), a key it does not list, an anchor or duplicate key it will not expand, a role it does not read, text past a bound. On one corpus repository that advice was repeated for 40 of 43 items whose YAML is legal. Now only a file whose own text would not parse (`frontmatter_invalid`, `frontmatter_unterminated`, `instruction_text_invalid`) is told to `Repair or review this declared surface.`; every other reason reads `what this file declares could not be established, so no claim is made here about it. The limit may be this entry's rather than the file's, so no repair is prescribed.` The second sentence holds on `audit --host`, which compares nothing, as well as on a comparison. The issue `kind`, its `blocking` flag, the refusal it causes and the coverage `limit` are unchanged — only the sentence is. Correcting the profiles that refuse those files is #822.

**Two reasons split out of `frontmatter_invalid`** (review cycle 1). That reason was also the catch-all, so the sentence above was false for two exits that involve no parse failure, and `audit --host` printed `the file's own text could not be parsed` about headers `yaml.safe_load` reads without error. Each now names itself, and both take the no-repair sentence:

- `frontmatter_not_mapping` — the header is legal YAML whose top level is neither a mapping nor null (`- a\n- b`, or a bare scalar). Reading frontmatter as a mapping is this profile's rule, not YAML's. A header of `~` or `null` is **not** this reason: the parser this entry then calls resolves it to the empty mapping, exactly as it reads `---\n---`, so such a file stays `structured` with the digest an empty header has always produced (review cycle 2).
- `structure_value_unencodable` — a value this entry's own digest cannot encode, such as a YAML `.nan` or `.inf` under an undocumented key, which `_valid_metadata` skips without a documented type to check (#730) and `json.dumps(…, allow_nan=False)` then refuses. The digest also covers the body's inline commands, so the reason names the structure rather than the frontmatter. The limit is the digest's; there is nothing for an author to repair.

Both were already `unresolved` with `sha256: null`, and both still are, so the `status`, the blocking issue, the refusal and the coverage `limit` are what they were; only the `reason` string in `instruction_structure` and in that sentence is more specific. No file that was `structured` becomes `unresolved`: the two exits are decided the way the parser on the next line decides them, and a file that parses to a mapping — an empty one included — keeps its digest. A reader that switches on `status` is unaffected; one that matched the exact string `frontmatter_invalid` to mean "this file is broken" was matching a claim this engine could not make.

**Compatibility.** `host_comparison` is a closed object, so a reader validating against the published `docs/verifier-schema.v0.19.json` rejects a `0.20` artifact's `coverage`; that schema stays frozen. The current reader reads a `0.19` (or `0.18`) artifact as `0.20` with `coverage: null`, which is what that build knew, and refuses one that claims `coverage`. A `diff --json` consumer sees `capability_diff_schema_version: "0.3"` and one new top-level key. `read_sources_only` is an added member of an object no published schema describes, and it is a constant, so a reader of the `0.20` shape that ignores unknown members is unaffected.

<a id="host-diff-review-json-795"></a>

## Migration Note: 1.1.0 — the row semantics the text shows, published (verifier `0.20`, capability diff `0.3`, contract v40, #795)

Once the text named a permission rule's disposition and printed an engine-linked replacement or move as one change, the two surfaces described one run differently. On a twenty-row change carrying one `deny` → `allow` move and one narrowed rule, `diff` printed `18 change(s) from 20 rows, 1 widening what the agent may do (⚠).`, while `diff --json` published twenty rows whose `direction` was only `added` (18) and `removed` (2), with no disposition, no `moved` or `narrowed`, and `expands: true` on two of them. Neither answer was wrong about a grant — each row is one grant that left or arrived — but [`CLAUDE.md`](CLAUDE.md) tells an agent to parse `--json` and calls that shape the contract, so a script read two widenings from the run whose text said one, and no script could reproduce the sentence a human was reading.

The presentation is now published beside the rows, additively. **Every row keeps the values and the row count it had on `1.0.0`**: nothing is joined, dropped, reordered or re-worded there, and the host-config and cold-start benchmark replays reproduce their published run-of-record scores byte for byte.

```json
"review": {
  "changes": [
    {"row_indexes": [0, 19], "severity": "medium", "direction": "moved",
     "subject": "claude-code .claude/settings.json",
     "before": "deny: Bash(git push *)", "after": "allow: Bash(git push *)",
     "change": null, "why": "the same rule moved from deny to allow; runs without a prompt",
     "expands": true}
  ],
  "summary": {"rows": 20, "changes": 18, "widenings": 1},
  "question": "Review question: Does the team intend these 18 declared capability changes (from 20 rows)?",
  "reproduce_command": "agents-shipgate diff --base 6ecb5ded…"
}
```

- **`rows[].disposition`.** The permission list a rule is declared under — `allow`, `ask` or `deny` — and `null` for every other grant kind. It is the fact the text has printed since slice 1 (`allow: Bash(npm *)`), read from the grant, not derived from the cell. A permission grant's identity is its disposition and its rule text, so a row that has both sides has one disposition. It is published on every route that publishes rows, `check`'s boundary result included, where the rule's arguments are redacted and the disposition is not.
- **`review`.** One block per comparison, `null` when it was not recorded: a verifier from before `0.20`, an incomparable comparison, which presents no change and asks nothing, or a caller that publishes none (`check`).
  - **`changes[]`** is what the text prints, in the order it prints it: `row_indexes` (the positions in this comparison's `rows` it stands for, ascending — one, or two for a pair the engine linked), `severity`, `direction`, `subject`, `before`, `after`, `change` (the field-level difference printed in place of `before → after`, as for an MCP server whose launch changed, else `null`), `why` and `expands`.
  - **`direction`** is the word the text uses. For a single row it is the row's own `added`, `removed`, `changed` or `widened`. For a joined pair it is `widened` or `narrowed`, the direction the permission lattice decided (#816), or `moved`, the same rule text that left one disposition and arrived in one other. `narrowed` and `moved` name no row: they exist only where two rows are read as one change, which is why they are here and not on a row.
  - **`before`/`after`** are the cells the text prints, so a permission rule reads `allow: Bash(npm *)` and an added MCP server reads `billing (command name npx; env keys BILLING_TOKEN)`. The redaction rules of slice 1 apply unchanged: no value, no command path, and a URL only in the engine's sanitized form.
  - **`expands`** is per change: a joined pair is one widening, printed once and counted once. The rows keep their own `expands`, which is per grant, and both halves of a `deny` → `allow` move carry it.
  - **`summary`** is `{rows, changes, widenings}` — exactly the three numbers `diff`'s summary line prints, with `rows` the published row count.
  - **`question`** is the review question verbatim, `null` where the text asks none (no change). **`reproduce_command`** is the copyable `agents-shipgate diff --base <sha>`, `null` where the text offers none — a provided diff or a `check` comparison, which names no base commit. For a commit head it is run after checking out `head_commit`, which the comparison already publishes. It is published on a result with no change too, because the text prints it there ([#812 follow-up](#host-comparison-coverage-812)); an incomparable comparison publishes no `review` at all, and its text builds the same line from the commits the comparison already names.
- **What the block cannot say.** The contract refuses a block that disagrees with its rows: the changes must stand for every published row exactly once, `summary` must count them, and **a joined change whose two sides read alike is refused**. So a route that redacts a rule's arguments cannot publish `allow: Bash(<redacted-arguments>) → allow: Bash(<redacted-arguments>)`; `check` and a provided diff publish their rows alone, as their text prints them, one entry per row.
- **Where.** Top-level `review` in `shipgate diff --json` (capability diff `0.3`) and `host_comparison.review` in `verifier.json` (verifier schema `0.20`, [`docs/verifier-schema.v0.20.json`](docs/verifier-schema.v0.20.json)). The two are the same object for the same comparison. Neither version had shipped before 1.1.0, which publishes both — `1.0.0` shipped `0.2` and `0.19` — so both are extended in place rather than bumped again, alongside #812's `coverage`. `check`'s boundary result (`shipgate.agent_boundary_result/v3`) gains the row member only; the control envelope's `capability_rows` is untouched.
- **Text.** Unchanged, line for line, for every run this build produces. The renderer now reads the published block where a comparison carries one, so a `HostComparison` validated back from `verifier.json` prints the entries that artifact published instead of its bare rows; a comparison with no block, such as the one `check` builds from its own rows, still renders through `review_changes`, and so does a copy whose rows a caller sliced.
- **What does not change.** `comparison_status`, `incomparable_reasons`, `rows` and every row value, `unchanged_limits`, `coverage`, the inventory digests, saved host-grants baselines and drift payloads, `check`'s decision, the control envelope's `capability_rows`, and every control state, permission and next action. `minimum_control_contract_version` stays `21`, and runtime contract v40, unpublished before 1.1.0, is extended in place.
- **Compatibility.** `host_comparison` is a closed object, so a reader validating against the published [`docs/verifier-schema.v0.19.json`](docs/verifier-schema.v0.19.json) rejects a `0.20` artifact for `review` as it already does for `coverage`; that schema stays frozen. A row is *not* closed in any published schema, so the same `0.19` reader, and a `1.0.0` reader of `docs/agent-boundary-result-schema.v3.json`, still validate a row carrying `disposition`. The current reader reads a `0.19` (or `0.18`) artifact with `review: null` and no disposition, which is what that build knew, and refuses one that claims either. A `diff --json` consumer sees `capability_diff_schema_version: "0.3"` and one more new top-level key.

<a id="host-diff-review-text-795"></a>

## Migration Note: 1.1.0 — concrete permission and MCP changes, and a review question, in host-diff text (#795)

This is slice 1, the text. No schema, contract, member, row, row value, row count, check id, exit code or `minimum_control_contract_version` moved with it: `diff --json`, `verifier.json`'s `host_comparison`, `check`'s `rows` and the control envelope's `capability_rows` published exactly what they published before, and the host-config and cold-start benchmark replays were unchanged. What changed is the human text of `diff`, `verify --format text`, the manifest-free PR comment (`pr-comment.md`) and `check --format text`, which now read the rows through one function, `review_changes` in `core/capability_diff_rows.py`. Slice 2 publishes the same facts, so a machine consumer reads them too; the row values and the row count stay untouched there as well. See [the JSON migration note](#host-diff-review-json-795).

- **Permission rules name their disposition.** A rule cell reads `allow: Bash(npm *)`, `deny: …` or `ask: …` where it read `Bash(npm *)`.
- **A replacement or move the engine established is one change.** An allow rule the permission lattice decided another replaced, the pair behind `permission_widened` or a decided narrowing (#816), prints as one entry with its before and after and the direction `widened` or `narrowed`: `allow: Bash(npm test:*) → allow: Bash(npm *)`. The exact same rule text that left one disposition and arrived in one other, in one host and source, prints as `moved`: `deny: Bash(git push *) → allow: Bash(git push *)`. The entry carries the more severe of the two rows' severities and ⚠ when either row expands. Nothing else is joined: an undecided replacement, a case edit, rule text that left one settings file and arrived in another, or a rule removed from or added to two dispositions stays as its rows. A rule both a replacement and a move could claim joins the replacement. `--json` keeps the removal and the addition as two rows with `direction` `added` and `removed`, and, since slice 2, publishes the joined change, its direction and the two rows it stands for in `review`.
- **Redacting routes never join.** `check` and a provided diff print `Bash(<redacted-arguments>)`, so a joined replacement would read as identical sides. Those routes keep both rows, with dispositions.
- **MCP servers name their published launch facts.** An added or removed server reads `billing (command name npx; env keys BILLING_TOKEN)` or `linear (url https://mcp.linear.app/<redacted-path>; header keys Authorization)`; a changed one reads its difference, `gh: command name npx → docker; env keys +GH_HOST`. Only the grant's already-published `transport`, `endpoint`, `env_keys` and `header_keys` are read, never a value. A command server's `endpoint` is the name of its command's first word, never its path, so the fact is labelled `command name`: `npx`, `./npx` and `./tools/npx` all read `command name npx`. Command names, key names and endpoints pass through the #802 label redaction, and at most five names are listed before "and N more". A URL prints only in the sanitized form the engine publishes for `http`, `https`, `ws`, `wss` and `sse` URLs: scheme, host and optional port, and no path but `/` or `/<redacted-path>`. That sanitizer returns any other value as written, so `${SLACK_MCP_BASE}/hooks/…`, a URL without a scheme or a custom scheme's URL reads `url not shown`; a change from or to one names the printable side, `url https://hooks.example.com/<redacted-path> → not shown`, and a change between two reads `url changed (not shown)`. The grant's JSON `endpoint` is unchanged. When none of those facts differ, the entry says what was compared and that the change is elsewhere, instead of printing `gh → gh` or calling the command unchanged: `npx` → `./npx` with the same arguments reads `gh: no difference in the command name npx, env key names or header key names; the change is in a detail this output does not show, such as the command's path or arguments`, and a URL server ends `such as the URL's query or another setting`. Showing arguments is #819.
- **`diff`'s count.** The summary counts entries: `3 change(s) from 4 rows, 3 widening what the agent may do (⚠).` The `from N rows` clause appears only when an entry joins rows. Those three numbers are `review.summary`'s `changes`, `rows` and `widenings`, so a script never counts them from the text.
- **`verify`, the PR comment and `check` text print ⚠** before an entry that widens, as `diff` does.
- **A review question and references.** An explicitly comparable result with at least one entry ends with `Review question: Does the team intend this declared capability change?` (or `these N … changes?`), with `(from M rows)` before the `?` when an entry joins rows, so the question agrees with the row count in the control headline beside it; `capability`, because an entry may be an MCP server, a hook, a workflow grant or instructions as well as a permission rule. Where the comparison names a base commit and its head is a commit or the working tree, two lines follow: `Compared: base <8> → head <8>, agents-shipgate <version>.` (or `→ working tree at HEAD <8>`) and a copyable `agents-shipgate diff --base <base sha>`, prefixed for a commit head by "check out <head sha>, then run". `check` text and a provided diff name no base commit and print the question alone. In the PR comment the command is inline code and a blank line ends the entry list, and separates the two lines from the headline on a refusal whose coverage names no source, so Markdown never renders them as one paragraph (review cycle 4). Slice 1 left no-change, not-compared-only and incomparable answers without either line; they now print the two reference lines as well — except where the run named no base commit, `shallow_history` and an unfetched base among them, which carry neither line, as they carry no block — and still no question, since there is no change to ask about ([#812 follow-up](#host-comparison-coverage-812)). On a refused comparison, which opens `Cannot compare against <ref>: …` or `Host capability comparison unavailable: …`, the first line is labelled `Inputs:` rather than `Compared:` — the same commits, the same version and the same reproduction, named as what that run was handed rather than as a comparison it did not make (review cycle 1).
- **Read back from JSON.** The reviewer view is computed where the rows are built and is not a row field, so equality and serialization ignore it. Slice 1 therefore printed each published row as it was — without dispositions, joins or MCP facts — when a `HostComparison` was validated back from `verifier.json`; since slice 2 the comparison carries what it presented, so re-reading it prints the entries it published.

<a id="relative-out-current-directory-818"></a>

## Migration Note: 1.1.0 — a relative `--out` resolves against the current directory, and printed artifact paths open from the caller (#818)

No schema, contract, refusal code or `minimum_control_contract_version` moves, and no error kind, exit code or JSON field is added. What changes is where `verify` and `scan` write for a relative `--out` typed outside the directory they used to join it to, how `verify --format json` and `scan` spell the artifact paths they print, the `--out` in the commands `verify` emits, and how `audit --host` answers an `--out` that names a directory.

**Why.** Three commands resolved a relative `--out` against three directories, and none said which. `verify` joined it to the Git root of `--workspace`; `scan` joined it to the manifest's directory, as it joins the manifest's own `output.directory`; `audit --host` resolved it against the current directory, as `agent control --reports-dir` does. From a sibling directory, `verify --workspace ../repo --out rel` wrote an untracked `rel/` into the scanned checkout, which the next run there counted among its changed files. It printed `rel/verifier.json`, which does not exist from the caller, and `agent control --workspace ../repo --reports-dir rel` in the same shell read `./rel`, a different directory. `fixture run <name> --out rel` joined `rel` to the fixture's temporary copy and then removed the copy, so the report directory it printed was already gone. `audit --host --out <existing directory>` exited `4` as `other_error`, naming a temporary file the caller never typed.

**The rule.** An output location typed on the command line is the caller's path: absolute as given, relative against the current directory. That holds for `verify --out`, `scan --out`, `fixture run --out`, `audit --host --out` and `agent control --reports-dir`. An omitted one keeps its default: `agents-shipgate-reports` under `--workspace` for `verify` and `agent control`, the manifest's `output.directory` (relative to the manifest) for `scan`, and no file for `audit --host`. `verify`, `scan` and `fixture run` take a directory; `audit --host` takes a file. Each `--help` states the base, the kind and the default. The output-directory rule of [#804](#output-directory-repository-content-804) judges the directory the spelling reaches, so `--out .` typed inside `docs/` or `.claude/` is refused like `--out <repo>/docs`.

**Not silent.** Where a relative `--out` now names a different directory than `1.0.0` did, the command prints one line on stderr before it runs: for `verify`, anywhere but the Git root (for `--preview` outside Git, anywhere but `--workspace`); for `scan`, anywhere but the manifest's directory. The line reads `note: --out rel resolves against the current directory, so this run writes to <new>. Agents Shipgate 1.0 resolved it against the Git root (<old>); pass that absolute path to keep writing there.`, naming `--workspace` in place of the Git root for `--preview` outside Git and the manifest's directory for `scan`. Stdout, the verdict and the exit code do not change. Nothing is printed for an absolute `--out`, for an omitted one, or from the directory `1.0.0` resolved against.

**Printed paths.** `verifier.json` keeps `artifacts`, `head_report_json` and `base_report_json` relative to its `workspace` (the Git root) when they lie inside the repository, and absolute otherwise, so a reports directory stays portable. `verify --format json` prints the same object with each relative one spelled for the invoking shell: relative to the current directory when beneath it, absolute otherwise, the rule `verify --format control` and `agent control` already use for envelope artifact paths (#575). Run from the Git root, stdout and the file are identical, as before. `scan`'s printed `Reports:` list follows the same rule, while `report.json`'s `generated_reports` stays relative to the manifest's directory. `fixture run` prints the absolute report directory.

**Emitted commands.** `fix_task.verification_command`, the `allowed_next_commands` and repair commands built from the same request, and the `verify` commands `--preview` routes to name `--out` absolutely whenever they carry it, so they write to the same directory from wherever they are run. They already named `--workspace` absolutely. `agent control`'s recovery for a superseded pointer is the producing run's own command with every other token kept and `--out` naming the directory it read, absolutely, unless that command already publishes there from any directory. `audit --host`'s recovery commands already named `--out` absolutely.

**`audit --host --out <directory>`.** Refused before the inventory is read, and before `--save-baseline` writes anything: exit `2`, `config_error`, `--out <dir> is a directory; --out takes a file path, such as <dir>/host-grants.json. Nothing was written.` `next_actions[0]` is the same request with only `--out` changed to that file, as a `command`. When that file already exists it is a `review` step with no command, so following it never replaces a file nobody named.

**Compatibility.**
- **A run from the Git root (`verify`) or from the manifest's directory (`scan`)** is unchanged. That covers the GitHub Action's `verify` mode, which runs `--workspace .` from the checkout root, and its `scan` mode with the default `config`. It also covers the Claude Code Stop hook, which passes no `--out`, every documented invocation, and `verify --workspace <nested project>` run from the root.
- **The GitHub Action's `scan` mode with a `config` below the checkout root** now writes `output_dir` beneath the checkout root, where the Action's later steps (outputs, annotations, upload) read it. Before, it wrote beside the manifest and those steps found nothing.
- **A script that runs `verify --workspace <repo> --out <relative>` from another directory** now writes beneath its own current directory, and says so on stderr. Pass `<repo>/<relative>` to keep the old location. An `agent control --reports-dir <relative>` in the same script now reads the directory that `verify` wrote.
- **A script that runs `scan -c <path>/shipgate.yaml --out <relative>` from another directory** likewise. With `--workspace`, each manifest's reports now go to `<cwd>/<relative>/<project>` rather than `<manifest directory>/<relative>/<project>`.
- **A consumer of `verify --format json` stdout run outside the Git root** that joined `artifacts` values to `workspace`: a relative value is now relative to the current directory. Open stdout's paths as given, or read `verifier.json` for the repository-relative spelling.
- **An in-process caller of `run_verify` or `run_preview`** that passes a relative `out` now has it resolved against the process's current directory. `run_scan`'s `output_dir` keeps its manifest-relative meaning; only the `scan` and `fixture run` commands anchor `--out`.
- **A `fix_task.verification_command` recorded by `1.0.0`** names a non-default `--out` relative to the Git root, which resolves elsewhere when the command is typed anywhere else. `agent control` does not replay that spelling in a recovery: for a superseded pointer it names the recorded command with `--out` replaced by the absolute directory it read, the #804 recovery removes `--out`, and a recovery rebuilt from the request names the directory absolutely (#575). What presents a recorded verifier as it is, such as `agent handoff --from` and the `pr-comment.md` that run wrote, still prints the command as recorded; re-run `verify` to record an absolute one.
- **A consumer that treated `audit --host --out <directory>` as exit `4` with `other_error`** now sees `2` with `config_error`. An `--out` that cannot be written for any other reason is still `4`.
- **Not changed here:** `verify --config` stays relative to `--workspace`, and `--baseline`, `--policy-pack` and `--diff-from` to the Git root: they name repository inputs, not output locations. `audit --host --baseline-file` stays relative to `--workspace`. The #804 refusal still names an output directory by its repository path. `baseline save`, `capability export` and `agent handoff` already resolved `--out` against the current directory.

<a id="permission-rule-direction-816"></a>

## Migration Note: 1.1.0 — permission rule direction: `:*` rules, moved rules and one MCP tool (#816)

This extends host-grants `0.6` and runtime contract `40` in place, as #693 and #714 did: neither had shipped in a tagged release before 1.1.0, and published `1.0.0` emits host-grants `0.5` and contract `39`. No schema, member, check id, evidence kind or `minimum_control_contract_version` moves. What changes is the value of existing fields for three rule shapes: drift `expansion_signals`; row `direction`, `expands`, `severity` and `why` in `diff`, `verify`'s `host_comparison`, `check` rows and the control envelope's `capability_rows`; a one-tool MCP allow grant's `wildcard`, `access` and `risk`, and so what counts wildcard grants (the `audit --host` Markdown wildcard warning and `org`'s `wildcard_permission_rule_count`); and `check` decisions, with the `host_settings_narrowed` diagnostic and the `SHIP-VERIFY-TRUST-ROOT-TOUCHED` excuse #661 ties to it. Most rows in the table below remove a warning or a blocking decision, but not all: two `Bash(npm run test:unit)` rows and the `Bash(git log *)` moved-in row add a warning or review, and two rows lose a `permission_widened` name. Each reading follows Claude Code's permissions documentation (https://code.claude.com/docs/en/permissions, "Wildcard patterns" and "MCP").

| Edit | `expansion_signals` before → after | ⚠ rows before → after | `check` before → after |
| --- | --- | --- | --- |
| `Bash(npm:*)` → `Bash(npm test:*)` | `allow_rule_added: …Bash(npm test:*)` → none | 1 → 0 | `require_review` (`…-PERMISSION-ALLOW-EXPANDED`) → `allow` |
| `Bash(npm test:*)` or `Bash(npm test *)` → `Bash(npm test)` | `allow_rule_added: …Bash(npm test)` → none | 1 → 0 | `require_review` → `allow` |
| `Bash(npm test:*)` → `Bash(npm:*)` | gains `permission_widened: …Bash(npm test:*) -> Bash(npm:*)` | 1 → 1 | `require_review` (unchanged) |
| `Bash(npm run test:*)` → `Bash(npm run test:unit)` | none → `allow_rule_added: …Bash(npm run test:unit)` | 0 → 1 | `allow` (`host_settings_narrowed`) → `require_review` (`…-PERMISSION-ALLOW-EXPANDED`) |
| `Bash(npm run test:unit)` added beside `Bash(npm run test:*)` | `allow_rule_added: …Bash(npm run test:unit)` (unchanged) | 1 → 1 | `allow` (`host_settings_narrowed`) → `require_review` (`…-PERMISSION-ALLOW-EXPANDED`) |
| `Bash(npm run test:unit)` → `Bash(npm run test:*)` | loses `permission_widened: …Bash(npm run test:unit) -> Bash(npm run test:*)` | 1 → 1 | `require_review` (unchanged) |
| allow `Bash(git status *)` → `Bash(git status --short *)`, with `Bash(git log *)` moved from `deny` to `allow` | loses `allow_rule_added: …Bash(git status --short *)` | 3 → 2 | `require_review` (unchanged) |
| allow `Bash(git *)` with deny `Bash(git log *)` → allow `Bash(git log *)` alone | gains `allow_rule_added: …Bash(git log *)` | 1 → 2 | `require_review` (`…-PERMISSION-DENY-REMOVED`, unchanged) |
| allow `Bash(git status *)` with deny `Bash(git *)` → allow `Bash(git *)` alone | loses `permission_widened: …Bash(git status *) -> Bash(git *)` | 2 → 2 | `require_review` (unchanged) |
| `mcp__github__get_issue` added | `wildcard_allow_added` → `allow_rule_added` | 1 → 1, `high` → `medium` | `block` (`…-PERMISSION-WILDCARD-ALLOW`) → `require_review` (`…-PERMISSION-ALLOW-EXPANDED`) |
| `mcp__github` → `mcp__github__get_issue` | `wildcard_allow_added: …mcp__github__get_issue` → none | 1 → 0 | `block` → `allow` |

- **`:*` on a `Bash` rule.** A trailing `:*` is read as the trailing ` *` it spells, and a trailing ` *` that is a rule's only wildcard also covers the bare command, so `Bash(npm:*)` covers `npm`, `npm test` and `npm test --watch` but not `npmx`. Only a `:*` that ends the pattern counts. A `:*` with nothing or whitespace before it (`Bash(:*)`, `Bash(npm :*)`) is left undecided, so it keeps its add signal. Other tools keep a colon as argument text: `WebFetch(domain:*)` ↔ `WebFetch(domain:example.com)` and `Bash(npm *)` ↔ `Bash(npm test:*)` answer as before. A settings change whose only effect is such a narrowing is now recorded as `host_settings_narrowed` and does not raise `SHIP-VERIFY-TRUST-ROOT-TOUCHED` (#661).
- **A command that continues a `:*` rule's last word with a colon.** The same page makes the space before a trailing star part of the rule, so `Bash(npm run test:*)`, which is `Bash(npm run test *)`, does not cover `npm run test:unit`, and `Bash(bundle exec rake db:*)` does not cover `bundle exec rake db:migrate`. Read as text, the prefix `npm run test:` covered such a command, and now it does not: replacing the rule with the command, or adding the command beside it, is an expansion. The added row is ⚠ with `expands: true`, drift has `allow_rule_added` for it, and `check` moves from `allow` to `require_review` with `SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED`. The change is no longer recorded as `host_settings_narrowed`, so #661 no longer excuses its `SHIP-VERIFY-TRUST-ROOT-TOUCHED`. The reverse, `Bash(npm run test:unit)` → `Bash(npm run test:*)`, keeps its ⚠, `allow_rule_added` and `check` decision, but the two rules are now incomparable, so it is no longer named `permission_widened`. The space spelling `Bash(npm run test *)` answered both ways already, and `Bash(npm run test*)`, with no space, covers `npm run test:unit`.
- **Moved rules.** A rule whose identical text left one disposition and arrived in another in the same host and source is a moved rule. Rules are matched by exact text, never by likeness or case. Before the one-out, one-in pairing, a rule moved *into* `allow` from `deny` or `ask` is always set aside, and keeps its own signals (`allow_rule_added`, and `deny_rule_removed` or `ask_rule_removed`). A rule moved *out of* `allow` is set aside only when another allow rule also left, which is then the replaced one. When it is the only allow rule that left, it is the replaced rule: it was granted at the base, so the arrival is compared with it as before. `Bash(npm *)` → `Bash(npm test *)` with `Bash(npm *)` moved to `deny` or `ask` stays "3 change(s)." and `allow`, and `Bash(git status *)` → `Bash(git *)` with `Bash(git status *)` moved to `deny` keeps its `permission_widened` signal. One consequence runs the other way: when the only allow rule that arrived is one that moved from `deny` or `ask`, it is no longer paired with the allow rule that left, so its `allow_rule_added` is never suppressed as a narrowing (the table's `Bash(git log *)` row: `git log` was denied at the base and is allowed now). For the same reason, when that moved-in rule is wider than the allow rule that left, the edit is no longer named `permission_widened`: allow `Bash(git status *)` with deny `Bash(git *)` → allow `Bash(git *)` keeps both ⚠ rows, `allow_rule_added` and `deny_rule_removed` for `Bash(git *)`, and `require_review`, and loses `permission_widened: …Bash(git status *) -> Bash(git *)`.
- **One MCP tool.** `mcp__<server>__<tool>`, and `mcp__<server>__<prefix>*`, are scoped: the grant is `wildcard: false`, `access: execute`, `risk: medium`, and the row reads "runs without a prompt". `mcp__<server>`, `mcp__<server>__*`, and any token whose server segment is empty or holds a glob, or whose tool segment is empty, opens with `*` or holds `[`, `]`, `?`, `{` or `}`, stay whole-surface grants: `high`, "matches every target of this kind, without a prompt", and blocking. `mcp__<server>` is compared as `mcp__<server>__*`, so narrowing it to one tool is no longer an expansion, and widening one tool to it is named `permission_widened`.

**Compatibility.**
- **A saved baseline holding a one-tool MCP allow rule** (every baseline `1.0.0` wrote, and a `0.6` baseline saved from a source tree before this change) recorded it as `wildcard: true`/`high`. The grant id is unchanged, so drift reports one `changed` grant for it, with no expansion signal and `expands: false`, and `--fail-on-drift` exits `20` once. Review the row and re-save the baseline; a `1.0.0` baseline must first be moved aside, as [the hook loading basis note](#hook-loading-basis-714) describes. `:*` and moved rules change no grant field, so baselines holding them are unaffected.
- **A consumer counting ⚠ rows, `expands: true` or `expansion_signals`** sees fewer for the narrowings above, one more signal for a `:*` widening, one more ⚠ row and `allow_rule_added` for a rule moved into `allow` under a removed broader allow rule, and one more ⚠ row, `expands: true` and `allow_rule_added` when a command that continues a `:*` rule's last word with a colon replaces that rule (`Bash(npm run test:*)` → `Bash(npm run test:unit)`). Two edits lose their `permission_widened` signal while their ⚠ rows, other signals and `check` decision stay the same: `Bash(npm run test:unit)` → `Bash(npm run test:*)`, whose rules are now incomparable, and a rule moved into `allow` that is wider than the allow rule that left (allow `Bash(git status *)` with deny `Bash(git *)` → allow `Bash(git *)`).
- **A gate reading `check` decisions** sees the moves in the table: `require_review` → `allow` for a `:*` narrowing alone, `block` → `require_review` for one MCP tool, and `block` → `allow` for narrowing a whole-server MCP grant to one tool. One move asks for more review: `allow` (with `host_settings_narrowed`) → `require_review` (`SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED`) when a command that continues a `:*` rule's last word with a colon replaces the rule or is added beside it (`Bash(npm run test:*)` → `Bash(npm run test:unit)`, `Bash(bundle exec rake db:*)` → `Bash(bundle exec rake db:migrate)`); like any settings change that is not a decided narrowing, it also raises `SHIP-VERIFY-TRUST-ROOT-TOUCHED`.
- **A reader counting wildcard grants** no longer counts a one-tool MCP allow rule. The `audit --host` Markdown line "⚠ N wildcard allow rule(s) above low risk" leaves it out, and is not printed when it was the only one (`Read` plus `mcp__github__get_issue` printed it before and prints no such line now); `org`'s `wildcard_permission_rule_count` drops by one for each such rule.
- **Not changed.** Rewriting one spelling into the other (`Bash(npm *)` → `Bash(npm:*)`) grants nothing new, but the lattice decides direction, not equivalence: the added spelling keeps `allow_rule_added` and `check` still requires review. `PowerShell` rules, which the same page documents with the `:*` suffix, are still read as text. Recorded benchmark runs keep the rows the engine printed when they ran, and every vendored host-config and cold-start case replays to its recorded outcome unchanged.

<a id="partial-clone-diff-objects-missing-817"></a>

## Migration Note: 1.1.0 — `diff` refuses a partial clone missing its base objects (#817)

No schema, contract or `minimum_control_contract_version` moves, no new exit code is added, and no JSON field is added. This adds one agent-mode error kind, `objects_missing`, and corrects the hydration example `verify` prints for its `objects_missing` diff status.

**Why.** In a partial clone (`git clone --filter=blob:none` or `--filter=tree:0`) the checkout fetches the head's objects and nothing else. `diff` reads its base side from Git with lazy fetching disabled, so when the base differs from the head it could not copy the base tree: it exited `1` with a `ConfigError` traceback (a treeless clone, a `CalledProcessError` one), printed nothing on stdout, and emitted no agent-mode line. In the same blobless clone, `verify`, `verify --preview` and `check` already answered with a structured, fail-closed result. `verify`'s remediation suggested `git fetch --refetch origin`, which applies the clone's configured `remote.<name>.partialclonefilter` again, fetches no blob, and leaves every one of those commands failing the same way.

**The refusal.** When the base archive fails and a walk of the base commit's objects shows objects the clone's promisor remote still owes, `diff` exits `2`, prints one line on stderr naming the side it could not read, and in agent mode emits `{"error": "objects_missing", "exit_code": 2, ...}`. `next_actions` holds one `kind: "command"` action per promisor remote the clone's configuration names, in configuration order: `git -C <workspace> fetch --refetch --no-filter <remote>`. A remote is named only when Git accepts its name as a remote name (the `refs/remotes/<name>/` test `git remote add` applies, so `up/stream` is named), it has a `remote.<name>.url`, and it does not start with `-`, which `git fetch` would read as an option. No other name, `origin` included, is put in its place: when no promisor remote qualifies, `next_actions` is one `kind: "review"` action and the example ends in a `<remote>` placeholder. The message ends with the sentence `verify` reports as `diff_status.remediation` for the same reason, with the first command (or the placeholder) as its example. Shipgate leaves that command to the operator and fetches nothing itself: every Git read sets `GIT_NO_LAZY_FETCH=1`, as before.

**Compatibility.**
- **A consumer that switches on `error` and falls through on unknown kinds** is unaffected. One that treated every `diff` failure as exit `1` now sees `2` for this case, the same exit a shallow checkout's refusal uses.
- **`verify`'s `diff_status.remediation` text for `objects_missing`** now reads `git fetch --refetch --no-filter origin` and says "Shipgate runs Git with GIT_NO_LAZY_FETCH=1" where it said "Verification runs with". The reason, `fetch_repairable` and every control route are unchanged.
- **Not changed here:** a shallow checkout is still refused first, as `config_error` with `git fetch --unshallow`; after that fetch a partial clone can still report `objects_missing`. Missing objects with no promisor behind them, as in a corrupt repository, are not reported as `objects_missing`, and fail as before. `verify` without `--preview` in a treeless clone still exits `4` with `internal_error`, as it did before.

<a id="workspace-read-cause-813"></a>

## Migration Note: 1.1.0 — a workspace that cannot be read is named, and refuses every Git-bound pointer (#813)

No schema, contract, error kind, refusal code or `minimum_control_contract_version` moves, and no JSON field is added. What changes is the text of some refusals, the `next_actions` `agent control` prints for them, and which pointers read as current when the workspace cannot be observed.

**Why.** The live-workspace reader behind `agent control`, `verify --format control`/`text` and the human-review decision reader discarded every failure to read the repository. Each refusal it caused said only that "the current set of uncommitted changes could not be determined", and `agent control` routed each one back to the producing `verify`. In a repository whose own Git configuration the worktree readers refuse — Git LFS (`filter=lfs` on tracked files), git-crypt (`filter.git-crypt.*`, `diff.git-crypt.textconv`), a local `diff.*` driver, a non-empty `.git/info/attributes` — that `verify` published a pointer the next `agent control` refused the same way, so an agent following `next_actions` looped forever; `verify --head HEAD` passed and was refused too. Separately, when the reader could not observe the workspace at all (no readable `.git`, or Git refusing the checkout), every pointer short of `complete` was returned without a currency check: a `review_publishable` pointer, with commit, push and update_pr, exited `0` over a tracked edit and an untracked file it never saw.

**The cause.** Every refusal a workspace that could not be read produces now leads with why: the configuration key or path Git's own refusal names (`filter.lfs.clean`, `assets/logo.bin`, `diff.lockb.textconv`, `.git/info/attributes`), a diff read's classified reason, or another Shipgate error's own sentence. Remediation clauses written for a `verify` of refs ("Commit the intended changes and verify refs") are dropped, because they do not clear this read. The sentence is redacted (a tracked file named like a token prints as `[REDACTED:github_token]`) and capped, never carries a configuration value or file content, and text an error from outside Shipgate carries is replaced by its type name. The refusal for a reports directory holding repository content (#804) is redacted the same way. `verify`'s own `diff_status` for local `diff.*` configuration now names the keys, never their values.

**The next action.** `agent control` chooses `next_actions[0]` by the cause:
- **Git configuration the worktree readers refuse:** `kind: "review"`, with no command. It names the cause, says that re-running `verify`, with or without `--head`, does not change the answer in this repository, and names `agents-shipgate diff --workspace <path>` as the read-only route. It never advises removing the configuration.
- **A reports directory holding repository content:** unchanged (#804).
- **An uncommitted change beyond a static read bound, or a Git timeout:** the producing `verify` command, with `why` saying to commit or shrink the change first.
- **Anything else:** unchanged, the producing `verify` command.

**Every Git-bound pointer refuses.** When the workspace cannot be observed at all, `agent control` exits `4` for every pointer that binds a HEAD, a base, a merge base, an overlay or a snapshot kind, whatever its control state: `workspace_unverifiable`, or `workspace_unverified` for a `complete` pointer as before. A pointer binding none of these — a `scan`, or `verify --preview` run outside a Git checkout — has nothing to compare and still reads. `read_current_control(live=None)`, a caller that asks for no currency, keeps its behaviour.

**Compatibility.**
- **A consumer that assumed an `agent control` refusal always carries a `kind: "command"` action** now sees `kind: "review"` for repository configuration causes. Route it to a person rather than rerunning.
- **A `review_publishable`, `agent_action_required` or `human_review_required` pointer read from a workspace that is not a readable Git checkout** exits `4` instead of `0`. Restore the checkout (or fix what makes Git refuse it) and read again.
- **An in-process caller of `agents_shipgate.cli.current_workspace.live_workspace`** receives a `LiveWorkspaceUnavailable` carrying the cause where it received `None`; pass it to `read_current_control(live=...)` unchanged. `read_current_control` accepts it beside `LiveWorkspace` and `None`.
- **A repository with Git LFS-tracked files, git-crypt, local `diff.*` configuration or a non-empty `.git/info/attributes`** still gets no current answer from `verify`/`agent control`; only the refusal and its route change. Supporting such repositories is not part of this change.
- **Not changed here:** global and system Git configuration are still not read, so an LFS filter configured only globally triggers nothing on its own. The refresh still reads the diff body to collect changed paths, so an uncommitted change over the body bound still refuses.

<a id="output-directory-repository-content-804"></a>

## Migration Note: 1.1.0 — an output directory that holds repository content is refused (#804)

No schema, contract, error kind, refusal code, exit code or `minimum_control_contract_version` moves. What changes is which `verify --out` values are accepted, and which pointers read as current.

**Why.** `verify` leaves its output directory out of every working-tree read it makes — the change set, the worktree overlay its pointer binds, the manifest-free host comparison and the static input census — and every refresh leaves the reports directory out of the change set it checks, so that a run's own reports never count as part of the change. The rule assumed the directory held nothing else. On published `1.0.0`, `verify --base main --out .claude` over a widened `.claude/settings.json` answered `complete`, `permissions.merge: true` and `merge_verdict: mergeable` with no findings, where the default output directory answers `human_review_required` with `SHIP-HOST-BOUNDARY-PERMISSION-ALLOW-EXPANDED`. That held for a committed widening, an uncommitted one, a new untracked settings file, and a deleted settings file whose deletion removed its deny rules (`SHIP-HOST-BOUNDARY-PERMISSION-DENY-REMOVED`), staged or committed. It held too for a trust-root file that carries the name of a Shipgate artifact: an untracked `.claude/commands/packet.md` under `--out .claude/commands`, a staged `.claude/skills/verification-inputs/SKILL.md` under `--out .claude/skills`, and an untracked `.agents-shipgate/capabilities.lock.json` under `--out .agents-shipgate`. `--out docs`, or `--out tools` for a manifest-named tool source, kept a `complete` pointer current across every later edit there. A symlink into the repository, and a case-variant spelling where the filesystem folds case, reached the same directories.

**The rule.** An output directory may be left out of the change set when leaving it out can hide nothing:

- it lies outside the repository;
- Git holds nothing beneath it: it is gitignored, empty, or does not exist yet, and no compared commit held anything there;
- everything Git holds beneath it is an uncommitted Shipgate artifact, untracked or staged, and no such path is a trust root: a file named like a report, pointer, verifier, packet, capability-lock, suggestion skeleton, Action payload, `skill lint`/`security`/`review` report or other artifact a Shipgate command writes into a reports directory, directly beneath it, or a file under its `verification-inputs/`.

These hold repository content:
- anything committed beneath the directory at `HEAD`, whatever its name;
- anything a compared commit held there that `HEAD` no longer does, and any committed path the index and the working tree no longer hold. A `git rm .claude/settings.json` that removes deny rules leaves no directory to find content in, and was hidden just the same. A worktree run compares the merge base it diffs against, including the base a manifest-free run's host comparison detects, which can be a local `main` in a repository with no remote. An archived `--head` run compares only the evaluated head: it diffs the merge base against that head in full, directory included, so a removal is in its own change set and nothing is hidden;
- any staged path, or untracked path Git does not ignore, that is not such an artifact;
- any path a trust root classifies (`core/trust_roots.TRUST_ROOT_SURFACES`), whatever its name, because a name cannot tell a generated `packet.md` from a slash command;
- a directory inside a trust root that Git does not ignore, even while it is empty, because every report a run wrote there would be a trust-root file;
- the repository root and its ancestors.

Git's own inventory decides this (`ls-tree` for committed content; `ls-files` and `check-ignore` with the exclude rules the change-set inventory uses). Staged generated reports stay allowed, because `verify` treats them as an advisory warning rather than content. The directory is judged by physical identity, so a symlinked or case-variant spelling is classified as the directory it reaches.

**The writer.** `verify`, `verify --head`, `verify --preview` and manifest-free `verify` refuse such a directory before the pointer is invalidated or anything is written: exit `2`, `config_error`, a message naming the directory and what it holds, and a `review` next action. The commits are resolved for this the way the run resolves them, including an auto-detected base. The existing refusals for `--out` at the workspace root or overlapping a named input are unchanged and still come first. `verify --preview` in a directory that is not a Git checkout classifies nothing, as there is no change set to leave anything out of.

The way out depends on the directory, and the message, the next action and `agent control`'s recovery all name the same one:
- **Any directory other than the workspace's default reports directory:** omit `--out`, or name a directory that is gitignored or outside the repository. Do not gitignore, untrack or move the files the directory holds to get past the refusal: done to `.claude` or `docs`, that takes real inputs out of every decision.
- **The default `agents-shipgate-reports`**, recognized by physical identity whether or not `--out` names it (the Action passes `--out agents-shipgate-reports`): omitting `--out` is never offered. Stray untracked or staged files that are not artifacts can be moved out, or the directory gitignored, as `init` does. Committed files beneath it need a change that untracks them (`git rm -r --cached agents-shipgate-reports`) and gitignores the directory; until that change merges, a worktree `verify` of it still finds the removal against its merge base, so pass `--out` naming a directory that is gitignored or outside the repository. The Action's `--head` run of that change passes.

**The readers.** `agent control`, `verify --format control` and the human-review decision reader classify the reports directory on every read, with the same rule, against `HEAD`, and against the merge base a worktree pointer records. They refuse any pointer in such a directory as `workspace_unverifiable`, whatever its control state. That includes a directory that held only artifacts when the run wrote it and gained content afterwards. A classification that cannot run refuses too, rather than falling back to a read that checks only completion. For this refusal, `agent control`'s recovery is the producing run's own `fix_task.verification_command` with only `--out` removed, so it publishes into the workspace's default reports directory with the same `--config`, base, `--head`, policy and baseline options; a rebuilt bare `verify` dropped `--base`, skipped a local base, and read a `human_review_required` change as `complete`. When the refused directory is the default one, the recovery is a `review` step instead.

**Compatibility.**
- **The default `agents-shipgate-reports`, and any directory that is gitignored, lies outside the repository, or holds only uncommitted Shipgate artifacts outside a trust root with nothing committed beneath it**, behave as before. One spelling improves: a case variant of such a directory beneath the repository (`--out <repo>/REPORTS` for an existing `reports`) is now excluded under the name Git stores. Before, Git matched nothing, and the run read its own reports as changes and exited `3`.
- **A `--out` naming `docs`, `.claude` or any directory beneath it, `.agents-shipgate`, a tool-source directory or any other directory with repository content** now exits `2`. That includes the GitHub Action's `output_dir` input: the Action runs `verify --head`, whose decision was never hidden this way, but it now refuses such a directory too.
- **A directory with a committed `.gitignore` of its own** (`out/.gitignore` holding `*`) holds committed content, and is refused.
- **A default `agents-shipgate-reports` with committed files in it**, or with committed files a worktree change removes, is refused the same way; see the way out above. Generated reports that are only staged stay allowed, and `verify` still only warns about them.
- **A pointer `1.0.0` published into such a directory** stops reading as current after upgrade: `agent control` exits `4` with `workspace_unverifiable`. Re-run `verify` into another directory.
- **A reports directory that also holds unrelated untracked files Git does not ignore**, such as notes beside the reports, now refuses every refresh. Move them out; for the default reports directory, gitignoring it also works.
- **The Claude Code Stop hook** passes no `--out`, so it publishes into the default reports directory. When that directory is refused, the hook's `verify` exits `2`, and the hook reports every exit other than `0` and `20` as advisory context without blocking the Stop, as it already did when the CLI could not start. So a change that would have soft-blocked the Stop on `agent_action_required` ends the turn with that context instead. Resolve the refusal the context names; the hook is not a trust boundary, and CI still decides.
- **Not changed here:** `diff` and `check` take no output directory. `scan` leaves nothing out of a change set, although the same readers refuse a `scan` pointer in such a directory. `verification prepare`/`assemble` do not leave their artifacts root out of the Git change set, and are not covered by the writer refusal. A `--workspace` that is not a Git checkout reads only a pointer that binds no Git identity, whatever `--reports-dir` names ([#813](#workspace-read-cause-813)). A gitignored output directory is still left out of the static input census as a whole, so a manifest-named input beneath it (`tool_sources: path: gen/tools.json` with `--out gen` and `gen/` gitignored) is read by the run but not bound to the pointer, and a later edit to it does not make the pointer stale; keep inputs out of the output directory.

<a id="workflow-label-redaction-contract-v40-802"></a>

## Migration Note: 1.1.0 — redacted workflow job, step, trigger and scope labels (contract v40, #802)

This extends host-grants `0.6` and runtime contract `40` in place, as #693 did: neither had shipped in a tagged release before 1.1.0. No member is added or removed. Only values change: a label that holds credential-shaped text, the entries of scope names that collide (below), and `check`'s `evidence.old` when the earlier level is not one GitHub accepts. Published `1.0.0` already printed a job id verbatim in `permission_contexts[].job`, `reusable_calls[].job`, the `write_scopes` and `effective_write_scopes` prefixes and the row text, a trigger in `triggers`, a permission scope name in `permission_contexts[].permissions` and the write-scope entries, and a job id and scope name in `check`'s `evidence.job` and `evidence.scope`; #771 and #693 added `step_actions[].job` and `.step` and the `job/step` and `job/destination` labels in `why`. A job id GitHub accepts may be shaped like a token (`ghp_` followed by 36 characters), and a step name may carry registry credentials (`Pull docker://ci:<password>@gcr.io/proj/img`), so each of those surfaces republished them.

- **What is redacted.** Every workflow label — a job id, a step's `id` or `name`, an `on:` trigger and a permission scope name — is published by one rule: the report redactor (known token shapes such as `ghp_…`, `AKIA…`, `xoxb-…`), then the host sanitizer (credential assignments such as `token=…`, and URLs), then the userinfo of every `scheme://…@` token inside the label, whatever it holds, which becomes `scheme://<redacted>@`. As for a step reference, the userinfo is everything before the token's last `@` once a trailing `@algorithm:hex` digest is set aside, so a password holding `/`, `:` or `@` is covered. `Pull docker://ci:<password>@gcr.io/proj/img` publishes as `Pull docker://<redacted>@gcr.io/proj/img`, and a job id `ghp_…` as `[REDACTED:github_token]`.
- **Where.** The label is computed once, where the workflow grant is built, and used in every field that names the job, trigger or scope: `permission_contexts[].job` and its `permissions` keys, `reusable_calls[].job`, `step_actions[].job` and `.step`, `triggers`, and the `write_scopes` and `effective_write_scopes` entries. The inventory, saved baselines, drift, `diff`, `check` rows, manifest-free `verify`, `verifier.json`, the PR comment and the control envelope read those fields, so they print the same label. `check`'s own workflow evidence (`evidence.job` and `evidence.scope` on `SHIP-HOST-BOUNDARY-WORKFLOW-WRITE-ALL` and `-PERMISSIONS-EXPANDED`) uses the same rule; it is not read from the grant but derived from the raw declarations, which it still compares, and two violations whose redacted evidence is identical dedupe into one, with the same rule, path and decision. `evidence.old` on `-PERMISSIONS-EXPANDED` publishes only `read`, `write` or `none`; an earlier level holding any other text, which GitHub rejects, is `null`, as a non-string level already was. The decision is unchanged.
- **`config_sha256` follows the published labels.** It is computed over the redacted projection, so no grant digest is taken over a raw token-shaped label. A grant whose labels hold no credential-shaped text and no `scheme://…@` userinfo keeps its digest; a step named `Clone ssh://git@github.com/org/repo` does not, because its label is redacted. The workflow file's artifact `redacted_sha256` is not changed; see the limits below.
- **What still compares.** A redacted label is still a label: one token-shaped job id, alone in its workflow, identifies its job, so its permissions, reusable call and step references compare as before and nothing refuses. A GitHub workflow holding one keeps complete coverage, and an unrelated change in the same pull request, such as a Claude Code shell permission, keeps its row.
- **Labels that publish alike refuse.** When two distinct job ids or two triggers in one workflow, or two scope names in one `permissions` mapping that a job's permissions are read from, publish alike, they would compare as one, so the inventory records a blocking `unsupported` coverage issue ("distinct job ids in this workflow publish alike once credential-shaped text is redacted, so they cannot be compared apart; rename or remove one so each publishes a distinct label"), as it does for a redacted step reference (#767). `audit --host` lists that message under its coverage issues, and for an unchanged workflow `diff --json` and `verifier.json` carry it as `unchanged_limits[].detail`; the `diff` and `verify` text and the PR comment name only the source and the `unsupported` kind, as for every unchanged limit (#721). Scope names that publish alike become one entry holding the widest level among them, so the grant never reads a declared `write` as `read`. Scope names collide within one mapping only: two token-shaped scope names that publish alike in two different jobs are not a collision, because each job's scopes are compared apart. A top-level mapping that no job inherits is read only into `write_scopes`, which is neither compared nor part of `config_sha256`, so its scope names are published redacted and never collide. Step labels are not compared, so two steps whose labels publish alike refuse nothing. While such a workflow exists, changed or not, the limit costs every route that cannot name it, on every run:
  - `check` is `incomparable` with no rows. On an unchanged workflow the reason is `unchanged_limits_not_representable`, and `SHIP-AGENT-BOUNDARY-INPUT-INCOMPLETE` requires review, because its boundary result cannot carry a limit (#721). A pull request that only edits `.claude/settings.json` therefore gets no rows from `check`.
  - `audit --host --save-baseline` exits `2`: a baseline cannot acknowledge missing evidence.
  - `audit --host --drift` is `incomparable` with `current_inventory_incomplete`, and `--fail-on-drift` exits `20`.
  - Only `diff` and `verify` compare past an unchanged workflow and name it in `unchanged_limits`; a changed one refuses there too.

  Renaming one of the two so their labels differ clears it. The pull request that renames it still compares against a base that holds the collision, so its `check`, `diff` and `verify` refuse once more with `base_inventory_incomplete`. Its head can be saved as a baseline, and once the rename is on the base branch every route compares again.
- **Ordinary names are unchanged.** `build`, `test`, `deploy-prod`, `release_notes`, `secret-scan`, `token-refresh`, the documented triggers and scopes, `Tag v1:beta@2` and `docker://image:tag@sha256:<hex>` are published as written.

**Compatibility and limits.**
- **Over-redaction is display-only, except for a collision.** A name matching any of the report redactor's token patterns is redacted even when it is not a credential. Examples, not the full list: `sk-` followed by 16 or more letters, digits, `_` or `-` (`sk-integration-tests-matrix`), `gh[opusr]_` or `github_pat_` followed by 20 or more, `xox[aboprs]-` followed by 10 or more, an AWS key prefix such as `AKIA` or `ASIA` followed by 16 uppercase letters or digits, and `(sk|rk|pk)_(live|test)_` followed by 16 or more. Userinfo after `scheme://` is redacted whether or not it holds a credential: `Clone ssh://git@github.com/org/repo` publishes as `Clone ssh://<redacted>@github.com/org/repo`. One such job id still compares. Two in one workflow that publish alike, such as `sk-integration-tests-matrix` and `sk-integration-tests-linux-arm`, collide: while both exist, `check`, `audit --host --save-baseline` and drift refuse on every run, even when the workflow is unchanged, as described above. Renaming one of them clears it.
- **Renaming a lone redacted label to another that publishes alike is not a row.** `ghp_A…` → `ghp_B…` publishes the same job, whose permissions, calls and step references still compare, and `config_sha256` does not tell the two apart, so `diff`, `verify` and `check` show no row for it. `check`'s own evaluation pairs jobs by their raw ids, so a renamed job has no earlier declaration of its own there and is compared with the top-level `permissions` instead: a `write-all` it declares raises `SHIP-HOST-BOUNDARY-WORKFLOW-WRITE-ALL`, which blocks, and a scope it declares `write` that the top level does not grant as `write` raises `SHIP-HOST-BOUNDARY-WORKFLOW-PERMISSIONS-EXPANDED` (`old: read`, `new: write` under a top-level `read`), which requires review, each with no row beside it. Drift reports the rename once, as an artifact change (next item).
- **The workflow file's artifact `redacted_sha256` is not changed.** As on `1.0.0`, it is a digest of the whole parsed file with only secret-named values redacted, so a job id, a trigger, a scope name and a step name enter it as written. It digests a file committed to the repository rather than a published label, and it tells two files apart where the grant cannot. So renaming a lone redacted label to another that publishes alike, or editing only the password inside a step name, is no row in `diff`, `check` or `verify`, but `audit --host --drift` reports it once: `has_drift: true` with 0 typed grant changes and one `artifact_changes` entry for the workflow, and `--fail-on-drift` exits `20`. Review it, then re-save the baseline.
- **Scheme-less userinfo in a step label is not read.** Only a whitespace-delimited token holding `scheme://` is read for userinfo, from its first `scheme://` wherever that opens in the token (`Deploy (docker://u:<password>@registry/img)` is redacted), so `ci:<password>@gcr.io` without a scheme is published as written unless its password matches a token pattern. A label is free text, and reading every `a:b@c` as userinfo would rewrite ordinary prose. The scan is linear in the label, so a label as long as the workflow file costs no more than reading it.
- **A token joined to a name by `_`, a letter or a digit is not recognised.** Most of the report redactor's patterns (GitHub, AWS, Stripe, Slack) open at a word boundary, and `_` is a word character, so `deploy_ghp_…` and `deploy_AKIA…` are published as written while `deploy-ghp_…` publishes as `deploy-[REDACTED:github_token]`. The OpenAI pattern excludes only a preceding letter or digit, so `deploy_sk-…` is redacted. This predates #802 and is shared by every surface those patterns redact, so it is not widened here.
- **A `0.6` baseline saved from a source tree before this change** that holds a credential-shaped label drifts once: the relabelled job reads as a changed grant, a write scope under it may read as a widening, and the row repeats the raw label from that baseline file. Review it, then re-save the baseline. A baseline holding only ordinary labels is unaffected.
- **Validators pinned to the `0.6` schemas** accept every payload: no member changed. The regenerated `0.6` schemas describe the labels.
- **`minimum_control_contract_version`** stays `21`.

<a id="reusable-workflow-secret-mappings-contract-v40-693"></a>

## Migration Note: 1.1.0 — named reusable-workflow secret mappings (contract v40, #693)

This extends host-grants `0.6` and runtime contract `40` in place rather than minting `0.7`/`41`. Neither had shipped in a tagged release before 1.1.0: published `1.0.0` emits contract `39` and host-grants `0.5`, and the `0.5` schemas stay untouched. A reusable call gains two members, each present only when set, so a call with no named mapping and an ordinary target keeps the shape #771 gave it:

```json
{
  "reusable_calls": [
    {
      "job": "deploy", "uses": "./.github/workflows/deploy.yml", "secrets_inherit": false,
      "secret_mappings": [
        {"destination": "credential", "source": "PRODUCTION_TOKEN", "form": "secret", "unresolved_reason": null}
      ]
    }
  ]
}
```

- **What is read.** For a job with a reusable `uses:`, each `secrets:` entry whose whole value is `${{ secrets.NAME }}` (any spacing or YAML quoting) is `form: secret`, with `destination` the called workflow's secret input and `source` the name `NAME`. `${{ github.token }}` is the same source as `${{ secrets.GITHUB_TOKEN }}`: GitHub's contexts reference calls `github.token` "functionally equivalent to the GITHUB_TOKEN secret", so both are `source: "GITHUB_TOKEN"` and migrating between the spellings is quiet. The secret's value is never read. `secrets: inherit` is unchanged: `secrets_inherit: true`, no `secret_mappings`.
- **What is compared.** Each call's set of mappings. Adding or removing a destination, or pointing one at a different source name, is a `changed` row on the workflow naming `job/destination`. Its `why` says which case it is, and that a name does not establish the secret's privilege, whether the caller has it, or what the called workflow does with it, so this mapping change is not itself counted as a widening. No expansion signal is raised, so `expands` is `false`; `secrets: inherit` keeps its own widening signal. Reordering the entries is quiet. **Source names are compared case-insensitively** — GitHub's secrets reference says names "are case insensitive when referenced" and are stored uppercase — so `staging_token` → `STAGING_TOKEN` is not a remap; the name is still published as written. The **destination** is the callee's `workflow_call` secret id, which GitHub does not document as case-insensitive, so it is compared as written and a case-only edit there reads as a removal plus an addition.
- **Unresolved values are a named limit, not a blocking one.** Any other entry is `form: unresolved` with `source: null`, and nothing of its value is published or digested: `literal_value`, `expression` (including `${{ secrets['NAME'] }}`, `${{ inputs.x }}`, `${{ secrets.A || secrets.B }}` and the uppercase context spelling `${{ SECRETS.X }}`), or `not_a_string`. A `secrets:` that is neither `inherit` nor a mapping is one entry with `destination: null` and `secrets_not_a_mapping`. Each records a **non-blocking** `unsupported` coverage issue naming its `job/destination`, the way an unread `envFile` does. GitHub coverage therefore stays `complete`: `check`, `--save-baseline`, drift and every other row on that file behave exactly as they did on `1.0.0`. The entries are still compared on `(destination, form, unresolved_reason)` with no value, so adding one, removing one, or moving one between forms is a row. Only an edit *between two values of the same unreadable form* is not reported, and the named limit says where that is. Blocking on it instead would have made one `${{ github.token }}` call refuse every host row in the pull request.
- **Redacted values (#767).** A job's reusable `uses:` now passes through the same redaction as a step reference. When it is rewritten, the call carries `uses_redacted: true`. A destination or source name that is rewritten makes the entry `unresolved` / `redacted`. Both record the blocking coverage issue. Before this change, `org/repo/.github/workflows/x.yml@token=aaaaaaaa` → `@token=bbbbbbbb` compared as unchanged with no row and no limit, on published `1.0.0` and on contract `40` as #771 left it. A token such as `ghp_…` in a reusable target, which `1.0.0` published verbatim, is now redacted.

**Compatibility.**
- **A committed `0.4` or `0.5` baseline holding a reusable call** is incomparable with `baseline_reusable_workflow_secret_mappings_unavailable` beside `baseline_workflow_step_actions_unavailable`; it never read mappings. Migrate as [the #771 note](#workflow-step-action-references-contract-v40-771) describes.
- **A `0.6` baseline saved from a source tree between #771 and this change** has no `secret_mappings`. It reads as "none declared", so a named mapping it held shows as an added destination: a row, never silence. Review and re-save it.
- **A workflow whose reusable call passes a literal or another unsupported value** keeps complete GitHub coverage and costs nothing on any route: `check`, `audit --host --save-baseline`, `diff`, `verify` and drift read as they did on `1.0.0`. The only new output is one non-blocking `unsupported` issue in the inventory, naming the `job/destination` whose value is not compared. `${{ github.token }}` costs nothing at all: it is read as `GITHUB_TOKEN`.
- **Validators pinned to earlier `0.6` schema bytes** from the source tree reject the new members. The regenerated `0.6` schemas carry them.
- **`minimum_control_contract_version`** stays `21`.

<a id="workflow-step-action-references-contract-v40-771"></a>

## Migration Note: 1.1.0 — workflow step action references (contract v40, #771)

Host-grants inventory, baseline and drift schemas `0.6` add one member to a workflow grant, present only when a step declares a listed reference. In a `0.6` grant its absence means the steps were read and declare none:

```json
{
  "step_actions": [
    {"job": "test", "step": "steps[0]", "uses": "actions/checkout@main", "form": "remote", "unresolved_reason": null}
  ]
}
```

- **What is read.** Each mapping step with a `uses:` key, in file order. `owner/repo[/path]@ref` is `remote` and `docker://…` is `docker`; the reference is compared as declared text and never fetched, so a branch, tag and SHA are equally opaque. `step` is the step's `id`, else its `name`, else `steps[N]` (zero-based). It is evidence for finding the step, not part of the comparison.
- **What is compared.** Each job's multiset of references. An added, removed or changed reference is one `changed` row on the workflow, naming `job/step` on each side, with `expands: false`. Reordering, renaming or re-id-ing steps that declare the same references is quiet; no execution-order dependency is evaluated. Permission widening and narrowing are still decided by the permission contexts alone.
- **What is not read.** A local `./…` reference is not listed and does not count as inspected: composite actions stay unread (#701).
- **Unresolved values.** An expression (`${{ … }}`), a string outside both forms, and a non-string value are listed as `unresolved` with `unresolved_reason` (`expression`, `unsupported_reference`, `not_a_string`). Their text is compared; what an expression evaluates to is not. A job whose `steps` is not a list, or a step that is not a mapping, is listed as `unresolved` (`steps_not_a_list`, `step_not_a_mapping`) with `uses: null`, so an absent `step_actions` still means the steps were read and declare nothing.
- **Redacted values.** A value the redactors rewrite is `redacted` and also records a blocking `unsupported` coverage issue. That covers known token shapes (`ghp_…`, `AKIA…`, `xoxb-…`), credential assignments, and registry or URL userinfo. Userinfo is everything before the last `@` once a trailing `@algorithm:hex` digest is set aside, so a password holding `/`, `:` or `@` is covered: `docker://user:password@registry/…` (in any letter case, and any other `scheme://`) is published as `docker://<redacted>@registry/…`, and a value with no scheme whose text before its last `@` holds a `:` or `@` is published as `<redacted>@<ref>`. A commit SHA, a tag, a port, `owner/repo/path@ref` and `docker://image[:tag]@sha256:<hex>` are not rewritten. Two such values could publish the same text, and a digest of either would be a digest of the credential, so the comparison refuses rather than read a distinct change as equal (#767).

**Compatibility.**
- **A committed `0.4` or `0.5` baseline holding a workflow grant** is loaded but incomparable. It never read step references, so its silence is not evidence that none changed. Upgrading changes what a gate built on it sees:
  - `audit --host --drift` reports `comparison_status: incomparable`, `incomparable_reasons: ["baseline_workflow_step_actions_unavailable"]`, `has_drift: null` and `next_action: null`. With `--fail-on-drift` it exits `20`.
  - `preflight` raises a `high`, `actor: human` `host_grant_drift` signal whose reason names `baseline_workflow_step_actions_unavailable`.
  - `audit --host --save-baseline` refuses to overwrite it: `Refusing to overwrite existing host-grants baseline <path>: unsupported_baseline_schema. The file was left unchanged.`

  To migrate, work from a checkout of the reviewed default branch. Re-saving acknowledges every grant that checkout declares, so never re-save from a branch under review.
  1. Run `shipgate audit --host` and review the inventory, including each workflow's `step_actions`.
  2. Move the old baseline aside, keeping it as evidence: `git mv .agents-shipgate/host-grants.json .agents-shipgate/host-grants.v0.5.json` (use your `--baseline-file` path if you set one).
  3. Run `shipgate audit --host --save-baseline` to write a `0.6` baseline at the default path.
  4. Run `shipgate audit --host --drift`; it now reports `comparison_status: comparable` with `has_drift: false`. Commit the new baseline through normal review.
- **A `0.4` or `0.5` baseline with no workflow grant** stays comparable. Every workflow the current inventory holds is then an added grant, and no side claims its references were compared. That is true of drift only: `audit --host --save-baseline` refuses to overwrite any baseline older than `0.6`, with or without a workflow grant, and exits `2` with `unsupported_baseline_schema`. Move the old file aside and re-save, as the refusal says (steps 2 and 3 above).
- **Git-backed `diff`, `check` and manifest-free `verify`** read both refs with the current reader and need no migration.
- **Validators pinned to the `0.5` schemas** reject a `0.6` inventory, baseline or drift payload. The `0.5` schema files stay published.
- **`minimum_control_contract_version`** stays `21`.

---

<a id="hook-loading-basis-714"></a>

## Migration Note: 1.1.0 — hook loading basis (#714)

No schema, contract or `minimum_control_contract_version` moves. What changes is the value of existing fields on `hook` grants, and which of them earn an expansion signal. A parsed hook file proves the file exists, not that a host loads it.

| Hook | `access` / `risk` | `hook_added` / `hook_changed` signal | Row |
| --- | --- | --- | --- |
| Declared in Claude Code settings, or Codex `.codex/hooks.json` | `execute` / `high` (unchanged) | yes (unchanged) | expands (unchanged) |
| Selected by a plugin that the repository's project settings (`.claude/settings.json` or `.claude/settings.local.json`) enable with `enabledPlugins` `true`, from a marketplace they register in `extraKnownMarketplaces` as a relative `directory` or `file` source inside the repository that lists the plugin with an in-repository source | `execute` / `high` (as in `1.0.0`) | yes (as in `1.0.0`) | expands, and says the project settings enable the plugin |
| Selected by a plugin in the repository without that enablement: `hooks/hooks.json` at a root a `.claude-plugin/plugin.json` or a `./`-sourced `.claude-plugin/marketplace.json` entry names, a `./` path in `hooks`, or inline hooks | `execute` / `medium` | no | a row that says plugin installation or enablement is not established |
| A Claude Code hook file nothing selects, such as `.claude/hooks/hooks.json` | `unknown` / `unknown` | no | a row that says loading is not established |

- **Identity is unchanged.** `grant_id` and `config_sha256` do not depend on the basis, so the same file is the same grant before and after. An inline marketplace hook's `source` is `<marketplace>#plugins.<name>`.
- **Enablement is read only where the repository proves it.** A `github`, `git`, `url` or `settings` marketplace source, an absolute, home-relative or escaping path, a plugin the marketplace does not list, and a value other than `true` leave the plugin's hooks at `execute`/`medium`. The `<marketplace>` in an `enabledPlugins` key matches the `extraKnownMarketplaces` key that registers the marketplace or the registered `marketplace.json`'s own `name`; neither is documented as the one Claude Code matches, so both are read, and the `name` alone never registers a marketplace. A `true` in either project settings file counts even when the other sets `false`, because a `false` in `.claude/settings.local.json` is one machine's opt-out. That file is usually uncommitted, but Claude Code reads it whenever it exists, so reading it errs toward showing the hook. User settings, installation state and workspace trust are never read.
- **The basis is read only from these exact pairs.** A plugin hook's `source` is a hook file, a manifest or a marketplace entry, never a settings layer, which separates it from a settings hook with the same pair. A hook-file grant with none of the plugin pairs was recorded without a basis and is not described as selected. `1.0.0` recorded every hook file as `execute`/`high`, the enabled-plugin pair, and no field can tell them apart without a schema change. The engine reads a basis only from the current side of a change, and a removal row, the only row built from a baseline's grant, names no basis. So nothing a `1.0.0` baseline holds is described as selected or enabled.
- **A saved baseline that recorded an unselected hook file as `execute`/`high`** stays comparable. Drift reports one `changed` grant for each of its events, with no expansion signal, so `--fail-on-drift` exits `20` once. Review the row, then move the old file aside (`git mv .agents-shipgate/host-grants.json .agents-shipgate/host-grants.v0.5.json`) and re-save the baseline: `--save-baseline` refuses to overwrite any baseline older than `0.6`, including every baseline `1.0.0` wrote. Nothing is hidden, and nothing is reported as a widening. Separately, #771 makes a `0.4` or `0.5` baseline holding a workflow grant incomparable; see [its migration note](#workflow-step-action-references-contract-v40-771).
- **Plugin manifests, marketplaces and the hook files they select are newly read.** A manifest or marketplace enters the inventory only when it declares `hooks`, and only that member is digested, so a version bump is not drift. Every repository that has one drifts once against a `1.0.0` baseline, because that manifest or marketplace is a new artifact and a new observed source. Two shapes:
  - **A hook file `1.0.0` never read** — one a plugin selects outside the Claude Code registry paths, such as `plugins/demo/hooks/hooks.json` — is also a new grant. It carries a `hook_added` expansion signal only where the project settings enable the plugin, because such a hook was loaded all along.
  - **A hook file `1.0.0` did read**, such as a `.claude/hooks/hooks.json` an enabled plugin selects, keeps `1.0.0`'s `execute`/`high` and the same `grant_id`. The grant does not change, so `--fail-on-drift` exits `20` reporting **0 typed grant change(s)**, with no rows and no expansion signal. The added artifact and observed source are in `artifact_changes` and `coverage_changes` in `audit --host --drift --json`; the Markdown summary counts typed grant changes only.

  In both shapes, read the `--json` payload, then move the old file aside (`git mv .agents-shipgate/host-grants.json .agents-shipgate/host-grants.v0.5.json`) and re-save the baseline from a checkout of the reviewed default branch. `--save-baseline` refuses to overwrite any baseline older than `0.6`, including every baseline `1.0.0` wrote, so re-saving over one exits `2` with `unsupported_baseline_schema` and leaves it unchanged. Re-saving from a branch under review acknowledges every grant that branch declares.
- **A hook file is no longer read as a settings file.** A `permissions`, `enabledPlugins` or `sandbox` key inside a hook file used to publish grants no host grants. It publishes none now, and a baseline holding one reports it removed.
- **New limits in `audit --host`, `diff` and `verify`.** These are blocking coverage limits:
  - a plugin manifest that cannot be parsed or is not an object;
  - a manifest `hooks` member of the wrong type;
  - a manifest reference that is not a `./` path inside the plugin;
  - a manifest reference to a file not named `hooks.json` or `<name>-hooks.json`;
  - a read limit of a hook file only a plugin selects.

  `diff` and `verify` name a parse or shape limit (`parse_failed`, `unsupported`) that both sides share on an unchanged file, and refuse any other. A read limit is never named as unchanged: an untouched plugin hook file over the read bound makes `diff` and `verify` incomparable even on a README-only change, as an oversize settings file already did in `1.0.0`. The same problems in a marketplace entry are named without blocking, and so is a `metadata.pluginRoot` that is not a `./` path inside the marketplace. So are a reference to a missing file, a reference into a directory the reader never walks (`node_modules`, `.venv`, …) and a selected file with no `hooks` object. A reference beneath a link that leaves the workspace gets only the blocking limit on that link.
- **`check` decides as `1.0.0` did for plugin references.** Its boundary result cannot name a limit, and it routes no plugin manifest, marketplace or plugin-selected hook file, so a plugin-reference limit never enters its input completeness. An untouched malformed plugin manifest no longer turns a README-only change into `require_review`. Its host comparison leaves out a plugin-reference limit both sides share on an untouched source, so an unrelated row stays in `rows` and `capability_rows`. A limit only one side carries, or one on a source the change touched, makes that comparison `incomparable` (`base_inventory_incomplete` or `head_inventory_incomplete`), with no rows. A head that breaks a plugin manifest therefore gives `allow` with `control_state: complete`, as `1.0.0` did, and an incomparable host comparison instead of a "removed" row built from the unread manifest. A head-broken `.claude/settings.json` still gives `require_review`: `check` routes that file. A changed `.claude/hooks/hooks.json` still routes to protected-surface review; only its rows change.

---

<a id="link-read-through-at-boundary-paths-contract-v39-700"></a>

## Migration Note: 1.0.0 — link read-through at boundary paths (contract v39, #700)

Host-grants inventory, baseline and drift schemas `0.5` add one optional member to an artifact:

```json
{
  "path": ".claude/skills/helper/SKILL.md",
  "resolved_through": [".agents/skills/helper/SKILL.md"]
}
```

- **When it appears.** On an artifact read through a symlink that resolves inside the repository, and nowhere else. Two kinds of link qualify:
  - a file link that a host adapter names, such as `CLAUDE.md`, `AGENTS.md` or `.mcp.json`;
  - a directory link at a location an adapter names by a fixed prefix, such as `.claude/skills` or `.cursor/skills`, or above an exact path.

  The artifact keeps the link's own path, because that is what the host reads. `resolved_through` lists each in-tree path the resolution landed on, ending at the file read.
- **Bound to the read.** Each link's text, and each component's kind, comes from the same identity-bound read session. So a link retargeted, or a target swapped, before the read finishes fails the snapshot. A comparison's base tree materializes the target's bytes, so both sides read the same file.
- **Still a coverage limit.** Each of these stays `unreadable`, and the comparison refuses as before:
  - an absolute, escaping or dangling target;
  - a link as an intermediate component;
  - a chain longer than eight hops;
  - a linked directory that contains a link, points into its own ancestor, or sits under a skipped directory such as `node_modules` or `.venv`;
  - a directory link that could only hide a `**/` match.
- **Change detection.** Retargeting a linked boundary path changes `resolved_through`, so drift reports an artifact change. A change to the target's content is compared under the link's path.

**Compatibility.**
- **A `0.4` baseline** is still loaded and compared. A `0.4` inventory refused every boundary link, and an incomplete inventory cannot be saved, so no `0.4` baseline holds an artifact that `0.5` would describe differently.
- **Validators pinned to the `0.4` schemas** reject a `0.5` inventory, baseline or drift payload. The `0.4` schema files stay published.
- **`minimum_control_contract_version`** stays `21`.

<a id="host-capability-rows-in-the-control-envelope-contract-v38-662"></a>

## Migration Note: 1.0.0 — host capability rows in the control envelope (contract v38, #662)

`shipgate.agent_control/v1` gains one optional member:

```json
"capability_rows": {
  "comparison_status": "comparable",
  "incomparable_reasons": [],
  "rows": [
    {
      "subject": "claude-code .claude/settings.json",
      "before": "—",
      "after": "Bash(*)",
      "direction": "added",
      "severity": "critical",
      "why": "matches any command of this kind, without a prompt",
      "expands": true
    }
  ],
  "omitted_rows": 0,
  "unchanged_limit_count": 0
}
```

- **Source.** The block copies the host comparison the producer already published. For `check` that is `rows`, `comparison_status` and `incomparable_reasons` on `agent-boundary-json`. For `verify --format control` and `agent control` it is `host_comparison` in `verifier.json`. When no comparison ran, the member is omitted rather than set to `null`.
- **Cap.** At most five rows are included. Rows with `expands: true` come first, then the rest, each group in the producer's order. `omitted_rows` counts the rows that were cut. Whenever it is non-zero, `rows` holds exactly five. `why` is capped at 400 UTF-8 bytes like the envelope's other prose. `subject`, `before` and `after` are never abridged.
- **Status.**
  - An `incomparable` block copies its reasons and carries no rows, with both counts at `0`.
  - A `comparable` block carries no reasons.
  - Empty `rows` on a `comparable` block means "no row in the covered comparison", not "safe".
  - `unchanged_limit_count` counts unchanged partial surfaces the comparison did not read (#721); `verifier.json` names them.
- **Authority.** None. `control_state`, `permissions`, `next_action` and `human_review` are the same with or without the block.

**Compatibility.** The envelope is a closed object, and its published schema was pinned by hash. Contract v38 widens it in place under the same identifier rather than publishing a `v2`; the owner chose this so a consumer keeps one envelope identifier. The effects are:

- A reader that validates envelopes against the v37 `docs/agent-control-schema.v1.json` rejects any envelope carrying `capability_rows`. That happens only when a host comparison ran, and the reader fails closed. Re-fetch the schema.
- Readers that parse without validating, or that ignore unknown members, are unaffected.
- An envelope with no host comparison is byte-identical to v37.
- `minimum_control_contract_version` stays `21`, because the `AgentControl` union is unchanged, as it was for v23–v25.

<a id="unchanged-comparison-limits-contract-v37-721"></a>

## Migration Note: 1.0.0 — unchanged comparison limits (contract v37, #721)

Verifier schema `0.19` adds `host_comparison.unchanged_limits`, a list of
`{host, limit, source, detail}`. `limit` is `unsupported`, `parse_failed` or
`experimental_coverage`. It is non-empty only on a `comparable` comparison, and
an `incomparable` one names none.

A comparison used to refuse whenever either inventory was partial or
experimental, even when the surface that made it so was untouched. A limit is
now named instead of refusing when all three hold:

- it is present with the same kind, host and source on both sides;
- it is a per-source `unsupported` or `parse_failed` issue, or experimental host
  coverage;
- its source is byte-identical at base and head, by Git object ID for a commit
  and by unfiltered hash for a working tree.

Anything else still refuses, including an `unreadable` source. An unchanged
symlink whose in-tree target changed must not read as unchanged (#700).

`shipgate diff --json` moves to capability diff `0.2` with the same
`unchanged_limits` list. Its incomparable reason for an incomplete head is now
`head_inventory_incomplete`, matching `verify`.

`check --format agent-boundary-json` is unchanged:
`shipgate.agent_boundary_result/v3` has no field for a limit. Where `diff` and
`verify` would compare past one, `check` reports
`incomparable` / `unchanged_limits_not_representable`.

A `0.18` verifier artifact reads as `0.19` with no limits, which is what that
build knew. One that claims `unchanged_limits` is refused. The published `0.18`
schema stays frozen. `audit --host --save-baseline` still refuses an incomplete
inventory.

<a id="mcp-url-capability-digest-723"></a>

## Migration Note: 1.0.0 — MCP URL capability digest (#723)

A URL-based MCP server's query parameters now enter its `config_sha256`, and
its file's `redacted_sha256`. A change carried in the query now produces a row:
removing `read_only=true`, adding `features=`, or pointing at a different
project. Before this change only the redacted URL was hashed, so none of these
was visible.

The URL path stays out of the digest. A webhook-style path is itself a secret,
and rotating one must stay quiet, so a capability carried only in the path
(`/read` → `/admin`) is still not seen.

Published fields are unchanged. `endpoint` still replaces the path with
`<redacted-path>` and drops the query, and nothing in the inventory, baseline
or rows carries a path, query or secret. A secret-named query parameter
contributes its name to the digest, never its value, and `env` and `headers`
contribute nothing. A server with no URL query keeps its earlier digest.

A host-grants baseline saved by an earlier build reports each URL server that
has a query as `changed` once, because its digest now covers more.
Review that row, then re-save the baseline. No schema version moves: the
digest's inputs changed, not the inventory's shape.

<a id="manifest-free-host-review-contract-v35-684"></a>

## Migration Note: 1.0.0 — manifest-free host review (contract v35, #684)

Verifier schema `0.18` adds `host_comparison`: advisory rows from the existing
host inventory comparator, the compared commits and captured workspace identity,
inventory digests, source paths, and explicit comparison health. It never supplies
an application `release_decision`, a successful release receipt, or merge authority.
A missing or malformed manifest does not become an invented policy: existing
manifest routes remain governed by the verifier, and explicit strict/application
policy inputs retain their existing failure behavior when no manifest exists.
The published `0.17` schema remains frozen; reading an older verifier does not
invent host evidence.

Boundary result `shipgate.agent_boundary_result/v3` adds `rows`,
`comparison_status`, `incomparable_reasons`, and `comparison_scope`. Existing
local boundary policy decisions remain authoritative. Supplied diffs compare
only their changed host files; unresolved content is incomparable, not an empty
successful comparison. `check` continues to hide permission arguments, including
in its new rows; use the named source file for the exact rule. The older v2
schema and deprecated Codex v2 projection remain frozen.

Interactive `check` now defaults to text; detected agent mode defaults to the
current boundary JSON. Automation should always select `--format
agent-boundary-json` or `--format agent-control-json` explicitly. These existing
explicit formats are retained, and `--format text` is available in either mode.

<a id="workflow-capability-comparison-contract-v34-685"></a>

## Migration Note: 1.0.0 — workflow capability comparison (contract v34, #685)

Host inventory, baseline and drift advance from `0.3` to `0.4`. Workflow
grants now carry per-job `permission_contexts`, `effective_write_scopes` and
`reusable_calls` (job, target
and `secrets_inherit`). Their fingerprint describes this static permission
projection; the separate artifact digest still records workflow content.
Editing a script therefore remains artifact drift without becoming a
permission-change row. Effective writes respect explicit job overrides,
including `{}`; removing an override can reveal inherited write access or
restore unknown repository defaults. Equivalent inherited and explicit grants
are quiet, and overridden workflow defaults do not become job authority.

New inherited-secret recipients are named in capability rows, including a
changed reusable target/ref. This is a declaration that the caller forwards
its available secrets, not proof of the callee's actions or access to every
organization secret. Named secret mappings, environment approvals, dynamic
expressions and transitive callee behavior are outside this comparison.
See [GitHub's reusable workflow contract](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations).

The `0.1`–`0.3` readers and schemas remain unchanged. A legacy baseline is
readable but deliberately incomparable: it did not record reusable recipients
and cannot prove their absence. Preserve the old evidence and explicitly
review a replacement baseline. Git-backed `diff` scans both refs with the
current reader and needs no saved-baseline migration. Runtime contract v34
advertises the new versions; the operational control contract stays unchanged.

<a id="qualification-coverage-diagnostics-v6-520"></a>

## Migration Note: 1.0.0 — qualification coverage diagnostics (v6, #520)

`EvidenceGap.recovery` is optional explanatory metadata (#561) on the existing
open gap object. `kind` distinguishes `input_unavailable`, `reader_limitation`
and `unresolved`; `reason` records the loader's typed evidence. Current SDK
coverage and its limits are documented in [source recovery evidence](docs/qualification-coverage.md#source-recovery-evidence).
An absent field remains absent when older rows are read. Report v0.43,
packet v0.18, verifier v0.16 and qualification v6 retain their versions;
decision-bearing action kinds, declaration authorship and control permissions
are unchanged. This classification never supplies missing evidence or makes an
actual IE count as a successful qualification outcome.

`shipgate.safety_qualification` advances v5 → v6 to add `coverage_misses[]`
and `intervals[].applicability` to the existing result. Both are required on
v6; the reader checks their counts, cases and applicability against recorded
outcomes and policy. All six legacy metric values and `passed` booleans retain
their meaning. Expected-IE is explicitly `not_applicable` only when its
denominator and policy floor are both zero; it is not a coverage success.
Actual IE still loses the applicable exact-outcome score.

V5 remains readable and retains its envelope on round-trip. Existing v1/v2/v4
beta/test artifacts normalize to v5 as before; they cannot name `pre_1_0`.
Old artifacts omit the new fields, which means unrecorded rather than zero.
The typed reader rejects new fields mislabeled as an older closed grammar.
V3 remains unsupported. Corpus/receipt-index v4, report v0.43, policy labels,
thresholds and release permissions are unchanged. See
[qualification coverage](docs/qualification-coverage.md) for denominators,
unscored cases and the limits of named gap evidence.

<a id="migration-note-unreleased-report-1-0-freeze"></a>

## Migration Note: 1.0.0 — the report contract is frozen at 1.0

Runtime contract `33 → 34`; `report_schema_version` moves **`0.43` → `1.0`**;
the minimum control contract stays at `21`. Packet `0.18`, verifier `0.17`,
receipt, capability-lock, attestation, preflight and host-grants schemas keep
their versions: renumbering every schema would be work without compatibility.

**The shape did not change.** `docs/report-schema.v1.0.json` and
`docs/report-schema.v0.43.json` are byte-identical apart from `$id`, `title`
and the `report_schema_version` constant. A consumer written against `0.43`
needs no change. `0.43` is the last pre-freeze version and stays published as a
frozen reference.

**What changed is the promise.** `1.x` is additive-only; a change that cannot
be expressed additively needs `2.0`; a deprecation cycle counts shipped
releases, not time on `main`; every published schema URL keeps its bytes. The
stable/provisional inventory of report fields, CLI, exit codes, Action and
control surfaces is in
[`docs/report-1-0-contract.md`](docs/report-1-0-contract.md), checked against
the runtime by `tests/test_report_1_0_contract.py`.

**Pre-freeze reports are no longer engine input.** `scan --diff-from`,
`apply-patches`, `explain-finding`, `findings`, `scenario suggest` and `evidence-packet` refuse a `0.x`
report by name, with a stable `reason_code` and a regeneration route, instead
of validating it against a model whose defaults would stand in for blocks it
never recorded. Projection readers (`explain-finding`, `findings`,
`scenario suggest`, and `evidence-packet`) accept newer `1.x` minors under the
additive promise. Comparison (`scan --diff-from`) and mutation
(`apply-patches`) refuse newer minors with an upgrade route: this build cannot
compare or apply evidence recorded under a contract it does not have.
Nothing is converted, and no artifact gains current authority by conversion or
by a restamped digest. Every superseded schema stays published, so archived
reports remain validatable.

**Qualification.** The production `beta` policy's
`required_report_schema_version` moves to `1.0`, equal to what the engine
emits. Issuance of the `pre_1_0` tier is retired: the runner will not produce
one, and `--policy-tier pre-1.0` is refused by name. The `pre_1_0` policy
itself, its thresholds and every reader of it remain, and it deliberately keeps
its historical `0.43` pin so an artifact already scored against it is still
named correctly rather than demoted to the unnamed `test` tier. No scoring
floor moved; no old evidence is re-read as `beta` qualification.

The freeze is conditional. A breaking change restarts it and invalidates every
qualification receipt collected against `1.0`.

---

<a id="migration-note-unreleased-human-review-decision"></a>

## Migration Note: 1.0.0 — external review decisions stay separate from the gate

Runtime contract `30 → 31`; the minimum control contract stays at 21.
The standalone `shipgate.human_review_decision/v1` and
`shipgate.human_review_evaluation/v1` schemas describe an externally signed
decision and its read-only applicability evaluation. They are core integration
surfaces, not new CLI outputs. Existing durable schema bytes, artifact paths,
the action union and release/control authority remain unchanged.

The evaluator reconstructs the current request from validated evidence,
requires external host key trust and trusted reviewer eligibility, and keeps
accepted/rejected/disputed outcomes separate. It writes no file. A returned
`applicable` result is neither persistence nor permission; the integration must
retain it and the signed decision separately and re-evaluate before use.
See [the decision contract](docs/human-review-decision.md) for the independent
signature domain, trust boundary and static-artifact guarantee.

<a id="migration-note-unreleased-human-review-request"></a>

## Migration Note: 1.0.0 — a review question gets a checkable postcondition

Runtime contract `29 → 30`; `minimum_control_contract_version` stays at 21.
The new standalone `shipgate.human_review_request/v1` artifact binds one
complete-evidence documentation-quality question to the existing verification
identity and full review scope. It does not change `HumanControlAction.expects`,
the shared action union, or any existing durable schema. The verifier's
extensible artifact map and terminal receipt bind the new optional file.

See [the request contract](docs/human-review-request.md) for the seven-surface
compatibility matrix, actor eligibility, accepted/rejected/disputed meanings,
and exact static-artifact boundary. A request grants no authority; until an
external authenticated decision is evaluated, existing human-owned control
remains in force. Existing signed push authorization remains push-only.


<a id="migration-note-unreleased-declaration-review"></a>

## Migration Note: 1.0.0 — changed declarations become reviewer evidence

`contract_version` moves **28 → 29**, `report_schema_version` moves
**0.42 → 0.43**, packet schema moves **0.17 → 0.18**, verifier schema moves
**0.15 → 0.16**. Those artifact bumps carry the declaration-review projection.
Runtime v28
already identifies the capability-delta contract, so this release does not
reuse it for a second public surface. `minimum_control_contract_version` stays
at `21`: `AgentControl` and `shipgate.agent_control/v1` are byte-identical.

**Base-vs-head action declaration changes are explicit reviewer evidence.**
`release_decision.evidence_coverage.semantic_coverage.declaration_review`
classifies added, removed, and semantically modified declaration rows as
`evidence_consistent`, `unverified`, or `acknowledged_override`. A removed row
is always unverified. Missing or conflicting action identity, unresolved or
ambiguous selectors, semantic evidence gaps, and comparison failure cannot
earn `evidence_consistent`.

The same bounded projection feeds report Markdown, packet JSON/Markdown/HTML,
the PR comment, annotations, and the GitHub step summary. Machine consumers
must branch on `enabled`, `base_comparison_requested`,
`base_comparison_available`, `changed_count`, `summary`, and the typed row
fields; they must not infer “no declaration change” from an unavailable
comparison. A requested comparison that cannot run is rendered explicitly.

The schemas are additive. Older report, packet, and verifier documents remain
frozen and readable under their original identifiers. Declaration review is a
reviewer projection, not a second gate: `release_decision.decision` remains the
only release decision signal.

---

<a id="migration-note-unreleased-capability-delta-attestation"></a>

## Migration Note: 1.0.0 — the capability delta becomes a published attestation

`contract_version` moves **27 → 28**. `minimum_control_contract_version` stays
at `21`, `report_schema_version` is unchanged, and no already-published schema
document changes: both the `AgentControl` union and `shipgate.agent_control/v1`
are byte-identical to v27. What moves is that a new artifact exists and the
contract advertises it.

**`verify` writes `agents-shipgate-reports/capability-delta-attestation.json`.**
It is an [in-toto](https://github.com/in-toto/attestation) Statement whose
`predicateType` is
`https://threemoonslab.com/agents-shipgate/capability-delta/v1` and whose
`predicate.delta` is the frozen `shipgate.capability_payload/v1` **delta view**,
unchanged. Full specification:
[`docs/capability-delta-attestation.md`](docs/capability-delta-attestation.md);
frozen schema:
[`docs/capability-delta-attestation-schema.v1.json`](docs/capability-delta-attestation-schema.v1.json).

**Nothing gates on it.** The attestation carries no verdict, no severity and no
per-subject release impact, by design: publishing an impact in an interchange
format invites a consumer to gate on it, which is a second verdict by another
name. `release_decision.decision` remains the only release gate, and no exit
code, verdict, finding, or check id changes.

**Six additive fields on the runtime contract**, published on both
`agents-shipgate contract --json` and `.well-known/agents-shipgate.json`:
`capability_payload_schema_version`, `capability_payload_schema_path`,
`capability_delta_attestation_schema_version`,
`capability_delta_attestation_schema_path`,
`capability_delta_predicate_type`, and
`capability_delta_attestation_artifact`. `external_integration_surfaces[]` gains
`capability_payload` and `capability_delta_attestation`, and `artifacts{}` gains
`capability_delta_attestation`. `shipgate.capability_payload/v1` itself was
frozen in the previous cycle and deliberately left unregistered while nothing
emitted it; registering a schema no artifact carries would have been a contract
bump with nothing behind it, so both halves land here.

**Two things a consumer must branch on, not probe for.**

- **The artifact is absent for a worktree run.** It attests a committed tree,
  and a `verify` invoked without `--head` evaluates a worktree snapshot whose
  bytes are in no tree object. Such a run writes no attestation and appends a
  note to `verifier.base_notes[]` saying why. The GitHub Action always passes an
  explicit `--head`, so CI runs are unaffected. Absence is not "the delta was
  empty".
- **`predicate.verification.status` is `bound` or `unbound`.** Only `bound`
  carries `input_set_id` and `subject_id`, and the schema refuses every other
  combination. A consumer that requires the chain into
  `verification-receipt.json` checks the status. `verify` always emits `bound`.

**`shipgate.capability_delta_attestation/v1` is closed, like its payload.**
Every object forbids extra properties and every vocabulary is a closed enum, so
there is no compatible in-place widening: any addition, removal, or change of
meaning is `…/capability-delta/v2`, published beside `v1`, with `v1` remaining
readable for at least one minor cycle. This is deliberately the opposite promise
from `report.json`, which is open by construction and additive within a version.

**Verifying one requires nothing of ours.**
[`tools/verify-capability-delta.py`](tools/verify-capability-delta.py) is
stdlib-only and applies every published rule; the rule ids it reports are listed
on the spec page. It enforces the closed v1 vocabularies itself rather than
deferring to a JSON Schema library, so the default command rejects
out-of-contract content without one.

The statement is emitted **unsigned** in `v1`: a passing run establishes
self-consistency and that the subject is the state the delta describes, not
authorship. Wrap the bytes in a DSSE envelope, or trust the transport.

Two things a consumer must not confuse. `verification.status: "bound"` is the
producer's claim; `--receipt <path>` is the check, joining the identities and
the artifact-manifest digest against a receipt you supply.
`--require-receipt-binding` refuses an `unbound` statement and nothing more.

---

<a id="migration-note-unreleased-verifier-explanations"></a>

## Migration Note: 1.0.0 — verifier explanations name the cause that acted

That projection-only change moved no schema or runtime-contract version. It
landed against the v0.42 report schema with the typed `unattested_surface` gap
and optional `EvidenceGap.policy_id`; consumers must continue to branch on
typed fields rather than matching prose.

- Every completed blocked verifier run now routes its plain headline through
  the same deterministic blocker picker already used by the adoption and
  self-approval branches. `verifier.headline`, `control.reason`, and
  `control.next_action.why` therefore append `Most severe: <title>.` even when
  the diff introduces no manifest and touches no trust root. The picker is
  unchanged: severity, then check id, then title. Runs with no blocker are
  byte-identical on this clause.
- A genuinely incomplete enumeration remains `incomplete_surface`. A
  lower-confidence extraction without an enumeration defect is now the distinct
  `unattested_surface` gap; only an explicit adapter fact of
  `surface: enumerated` earns the positive “enumerated surface” explanation.
  Its remedy asks for reviewed attestation rather than asking the adapter to
  enumerate tools it already found.
- Policy-evidence gap prose no longer prefixes `why` with an engine-owned
  `builtin-*` policy id. Public `SHIP-*` and organization-defined check ids
  remain as stable labels. `mixed_policy_evidence` names the authoritative and
  heuristic evidence in tension and the reviewed action that closes the gap;
  exact identity remains in optional `EvidenceGap.policy_id`, including an
  engine-owned id; the `builtin-*` prohibition applies to adopter prose, not
  structured identity. Machine consumers keep using the structured gap kind,
  subject, policy id, source, and target path.
- Blocker titles are budgeted by UTF-8 bytes before the plain headline is
  composed. The generated verdict and cause clause stay whole; lower-priority
  context is retained only as complete sentences when it fits in the 400-byte
  control-envelope prose budget.

<a id="migration-note-unreleased-embedded-trigger-routing"></a>

## Migration Note: 1.0.0 — embedded trigger advice is consumed by verifier control

No schema or runtime-contract version moves, and standalone
`agents-shipgate trigger --json` output is unchanged. When the same trigger
evaluation is embedded in `verifier.json`, `verify-run.json`, or preview
output, `trigger.next_action.kind` preserves the evaluated state (`command`,
`input_required`, `stop`, or `none`) while its command is cleared and the row
carries `authoritative: false` and
`authoritative_path: "control.next_action"`. Commands on embedded
`trigger.matched_rules[]` are cleared as well. Its explanation says the
verifier already consumed the trigger route.

This prevents `verify --preview --json` from publishing a self-referential
preview command above the initialize/verify command that actually governs the
run. Embedded `trigger` remains relevance evidence; `control.next_action` and
`control.allowed_next_commands` remain the only operational route. Consumers
that used embedded `trigger.next_action.command` must switch to
`control.next_action.command`. Consumers of the standalone trigger command do
not change.

---

<a id="migration-note-unreleased-pre-1-0-evidence-bar"></a>

## Migration Note: 1.0.0 — the pre-1.0 release evidence bar

`shipgate.safety_qualification` advances **v4 → v5**. The corpus
(`shipgate.safety_corpus/v4`) and receipt-index
(`shipgate.safety_receipt_index/v4`) envelopes **do not move**: their grammar is
unchanged, and these versions track grammar rather than release batches. No
`contract_version`, `report_schema_version`, or published adopter-facing schema
document changes.

Two grammar changes force the bump. `qualification_tier` gains `pre_1_0`
alongside `beta` and `test`, and the result now carries a cross-field
invariant: `production_qualified` is true exactly when a `qualified` artifact
claims the `beta` tier. A v4 reader admits neither, so a genuine `pre_1_0`
artifact must not claim to be v4.

**Reader behaviour.** The v5 reader accepts `shipgate.safety_qualification/v1`,
`/v2` and `/v4` **only when the payload uses the vocabulary that envelope can
express** — that is, `qualification_tier` of `beta` or `test`. Such a payload is
read as v5, because it is a v5 payload with identical meaning. A payload
carrying `pre_1_0` under a legacy envelope is *rejected*, not upgraded: an
unconditional upgrade would recreate the v4/`pre_1_0` combination this bump
exists to eliminate, leaving an old v4 reader able to receive an artifact it
cannot parse. Both release gates enforce the pairing — the standard-library
sealer on raw JSON, since it never parses the envelope otherwise. Nothing emits
v4 any more, and v3 is still not read, as before.

**What this is for.** `0.x` tags are now governed by a named 38-case `pre_1_0`
policy, decided under
[#341](https://github.com/ThreeMoonsLab/agents-shipgate/issues/341) and recorded
in [`docs/release-evidence-policy-decision.md`](docs/release-evidence-policy-decision.md).
It reduces evidence *coverage* only: the zero-unsafe-auto-pass rule, per-case
receipts, the holdout fraction, the κ floor and `static_only` are unchanged, and
every exact-match floor is the production rate rounded up. `1.0` and later still
require the 80-case `beta` artifact, and there is no promotion shortcut.
<a id="migration-note-unreleased-setup-error-envelope"></a>

## Migration Note: 1.0.0 — no corpus case targets `insufficient_evidence`

**No schema version moves.** `shipgate.safety_qualification` stays at v5, the
corpus and receipt-index envelopes are unchanged, and no field is added,
renamed, or removed. `insufficient_evidence` remains in
`ReleaseDecisionStatus`, the verifier still emits it, and
`minimum_insufficient_evidence_exact` remains a required field of the
`requirements` block.

**What changes is the contents of the two named policies.** No stratum targets
`insufficient_evidence` any more, so both tiers lose seven cells: `beta` goes
28 cells / 100 cases → 21 / 80, and `pre_1_0` 28 / 56 → 21 / 38. Four
`pre_1_0` `blocked` cells hold one case rather than two, because no second real
case exists for them. The floors that count those cases follow:
`minimum_insufficient_evidence_exact` becomes `0` in both tiers,
`minimum_blocked_exact` becomes `10` for `pre_1_0`, and
`minimum_qualified_origins` becomes `32` / `16` — the same 40% share of a
smaller corpus, not a smaller share.

**No rate moved**, which is the property to check if you are reading this to
find out whether the bar got easier. Every exact-match floor is still
production's rate applied to the population it governs, rounded up, and
`test_the_pre_1_0_policy_is_never_laxer_than_production_per_rate` fails if a
floor is one case laxer than that.

**Who is affected.** Nobody consuming a published artifact: none exists yet at
either tier. A qualification artifact built against the previous shape is
rejected by both release gates — with `case profile/outcome strata do not match
the <tier> policy` and a case-count error — and must be rebuilt. The reasoning
is recorded in
[`docs/release-evidence-policy-decision.md`](docs/release-evidence-policy-decision.md)
§ Amendment 3, under
[#520](https://github.com/ThreeMoonsLab/agents-shipgate/issues/520).

## Migration Note: 1.0.0 — the setup control envelope reaches both streams

`contract_version` moves **26 → 27**. `minimum_control_contract_version` stays
at `21`, `report_schema_version` is unchanged, and no published schema document
changes: both the `AgentControl` union and `shipgate.agent_control/v1` are
byte-identical to v26. What moves is where an already-published object appears.

**Every agent-mode error line from `detect`, `init`, and `doctor` now carries
`control`.** v24 gave those commands the envelope on their `--json` payload and
left the error stream out; `doctor`'s failure routes picked it up during that
rollout, and `detect`'s and five of `init`'s did not. So whether a caller
that routes on `control` could route at all depended on which setup command had
failed and on which of its failures — and the run that most needs a route is
the one that printed no payload to carry it. The `next_action` and
`next_actions[]` **fields** are unchanged on those lines, and are derived from
the same selected route as `control.next_action`; two of their command *values*
move, and both are listed under "Routing behaviour" below.

**What a consumer may rely on across every setup error line**, enforced by the
published schema and not only by the producer: `decision_source: "setup"`, a
`decision` from `setup_complete | setup_incomplete | setup_not_applicable`,
every field of `permissions` false, and `control_state` never `complete`.
Setup authorizes nothing, on either stream.

**`execution` is not a stream marker, and must not be read as one.** The
`error` field is what says a line is an error. `execution` answers a different
question — whether the command reached an answer about the workspace — and both
values occur on error lines:

| `execution` | When | Examples |
| ----------- | ---- | -------- |
| `"failed"` | The command could not reach an answer. | An unparseable `--control-pack` or `--agent-instructions` value, discovery that could not be bounded, a manifest that could not be opened or decoded, a generated manifest that failed validation. |
| `"succeeded"`, with a non-zero `exit_code` | The command reached an answer and that answer is a refusal it can route past. | `config_already_exists` (`init --write` declining to overwrite), and the unresolved-scope `config_error`. |

Do not infer authority from either value: the row above that says `"succeeded"`
still carries `permissions` all false, because it is a setup envelope.

**Two error lines still carry no `control`, by design.** The shared
`--workspace` refusal (`config_error`, exit 2, emitted by every command that
takes a `--workspace`) fires because that workspace does not exist, so there is no setup
subject for `input_id` to address; and `environment_error` is emitted before
Agents Shipgate is running and carries `environment` instead. `scan`, `verify`,
and `check` error lines are also unchanged: the first two answer through their
control pointer, and `check` — which publishes no pointer — through
`--format agent-control-json`.

**Routing behaviour: three changes, none of them additive.**

**1.** `init --write` over
a manifest that already exists published `next_action.kind: "edit"` on
`shipgate.yaml` with `expects: "The manifest reflects the desired tool sources,
agent declared_purpose, and policies"`. That postcondition was already
satisfied whenever the route was reached: a manifest that does *not* load is
claimed by the repair route above it. On this contract `next_action` **is** the
step, so the route could not change the answer — an envelope-only caller opened
the file, found nothing to change, re-ran, and received the identical action.
It is now `next_action.kind: "command"` naming the `doctor` invocation for the
manifest on disk, which reports what that manifest still owes. Exit code 2 and
the "already exists — edit it directly or remove it before re-running init
--write" sentence are unchanged; a consumer that branched on
`next_action.kind == "edit"` here now sees `"command"` — **except** when this
invocation asked for a manifest configuration the existing manifest does not
carry. `init --write --control-pack <id>` over a manifest selecting a different
pack routes to a reconciliation naming `policies.control_pack` and the exact
value, because both onward routes would otherwise advance under the pack that
is *there* and the request would be lost without anything saying so. The same
reconciliation is published for a scoped **candidate** project whose manifest
selects a different pack, where the candidate's `doctor` route had the same
effect.

"Asked for" is read from the argument parser rather than inferred by comparing
against the default. An explicit `--control-pack default` over a
`financial-strict` manifest is a request — and the only one that can *only*
weaken — so a default-comparison could not see it.

**The owner of that route is the direction, not the caller.** A governed coding
agent writes its own argv, so command-line arguments are not authenticated
human provenance for loosening a gate. A transition that keeps at least every
obligation the manifest has today is `agent_action_required` with a typed
`edit`. One that drops any obligation, or that names a pack this build cannot
resolve, is `human_review_required` with no command and names what it would
remove. The comparison is `weakened_pack_obligations`, shared with the
`control_pack_weakened` check rather than restated.

**2.** Every recovery command `init` publishes now repeats the **whole**
invocation with only the invalid value corrected. They were built from the
smaller flag list that a rerun in a *different* workspace may repeat, so
`init --write --minimal --control-pack <bad>` emitted a recovery without
`--minimal` — following it wrote an auto-detected manifest instead of the legacy
template that was asked for. `--minimal`, `--allow-unresolved-scope`,
`--agent-instructions-kit`, and a non-default `--max-python-files` now ride
along. Command *values* change; no field is added or removed.

**3.** The `internal_error` route for a generated manifest that fails validation
named a bare `agents-shipgate init --minimal`. It now names the same invocation
with `--minimal` added — the workspace, `--write`, and `--json` are carried
through, so following it writes the fallback template where the caller asked
for it rather than in the process directory. This is a `next_action` /
`next_actions[0].command` value change on a route that is now also
`control.next_action.command`.

---

<a id="migration-note-unreleased-adopter-vocabulary"></a>

## Migration Note: 1.0.0 — adopter-facing output stops naming internal fields

No version moves: `contract_version`, `report_schema_version`,
`minimum_control_contract_version`, and every published schema document are
unchanged. No field is removed or retyped and no error kind is added. What
changes is the *wording* of strings a person is expected to read and act on,
three published field values, one additive field on the agent-mode error
envelope, and one narrowed manifest value — each detailed below.

**The rule.** Internal identity vocabulary — `source_type`, `source_id`,
`native_locator`, observation ids, fingerprints, and derived `tool_v…` /
`agent_v…` identifiers — may appear as evidence in machine-read artifacts. It
may not appear in anything whose purpose is to tell a person what to do next:
console output, the agent-mode `next_action` / `next_actions[]` / `message`,
`agent-handoff.json` prose, `fix_task.instructions[]`, and PR comment text.
`report.json` evidence blocks, `tool_catalog`, `identity_assessment`, and the
verification artifacts are unchanged — they are the identity model and are
supposed to be precise. `tests/test_adopter_vocabulary.py` enforces the rule.

**Messages that changed.** All are prose; none is a documented identifier.

| Where | Before | After |
|---|---|---|
| `input_parse_error`, one artifact read twice for a source | `Duplicate tool observation identity: source_type='google_adk_function', source_id='google_adk:agent.py', native_locator='agent.py#map_account'` | `'agent.py' was read twice as one tool source, so the tool 'map_account' arrived twice. Remove the repeated shipgate.yaml entry naming 'agent.py', then re-run the scan.` |
| `input_parse_error`, one artifact defining a tool twice | *(the same message — the two causes were indistinguishable)* | `'tools.json' defines the tool 'pay' more than once, so one tool source produced it twice. Remove the duplicate definition from 'tools.json', then re-run the scan.` |
| source warning, one tool in several bindings | `Tool observation obs_v1_3f9a1c2b… appears in multiple bindings: b1, b2` | `Tool 'process_order' from 'agent.py' is claimed by more than one tool_identity.bindings entry: 'b1', 'b2'. …` |
| source warning, binding member that resolves to the wrong count | `member source_id='orders_b', tool='process_order' matched 0 observations` | `… matched 0 tools in that source, not exactly one — correct the member at shipgate.yaml#tool_identity.bindings so it names one tool` |
| `SHIP-DIAG-UNKNOWN-ADAPTER-SOURCE-TYPE` title | `Unknown adapter source_type 'acme' …` | `No adapter handles tool_sources[].type 'acme' in shipgate.yaml …` |
| `incomplete_tool_identity` gap `accepted_values` | `["unique_source_id", "stable_native_locator"]` | `["unique_source_id", "stable_source_path"]` |

**One gap subject changes value.** A binding gap whose issue names no tool
falls back to the agent, and the fallback was the derived agent id. It is now
the same kind of label a tool subject already is:

| Report | `evidence_gaps[].subject` before | after |
|---|---|---|
| `samples/conductor_agent` | `agent_v1:7205d836e4b3fee257d90695` | `durable_order_agent [conductor_workflows]` |
| `samples/support_refund_agent` | `agent_v1:7cb237a00d64b7400f4adc3b` | `refund_agent [openai_sdk_static]` |

`release_decision.reason`, `agent_summary.first_recommended_action.why`, the
CLI `Improve evidence:` line, and the GitHub step summary all print that
subject, so they change with it. **A consumer that joined on
`evidence_gaps[].subject` for these rows should read the agent id from
`binding_surface_facts.agents[].agent_id` instead** — `subject` is documented
as a display label, and this was the last kind of id still in it. The
report's conservation invariant now refuses *any* derived id in a gap subject,
matched by shape, so neither kind can return.

**One field is added.** The agent-mode `input_parse_error` envelope may carry
`details`, an object holding the diagnostic identifiers that left the message —
for the duplicate-tool failure: `failure`, `cause`, `source_type`, `source_id`,
`native_locator`, `tool_name`, `source_file`, and `manifest_path`; plus
`manifest_placeholders` (the manifest fields still holding `CHANGE_ME`, which
is what the placeholder recovery now routes on instead of searching the failure
text) and `evaluated_ref` / `manifest_in_ref` for a failure evaluated against a
ref. It is absent when a failure carries no such payload, and it is never
required to act on the error. Route on `details.failure` and `details.cause`
rather than on message text; `docs/errors.json` documents them.

`details.manifest_path` is the manifest the run actually read. It matters
because the emitted `edit` action names it: a `scan --workspace <repo>` that
discovers a sole nested `services/billing/shipgate.yaml`, or a `verify` with no
`--config`, previously published `path: "shipgate.yaml"` — a different file in
the caller's working directory. A consumer reconstructing the manifest from the
invocation should read this field instead.

**Two published values move.** The `incomplete_tool_identity` gap's
`next_action.path` is now `shipgate.yaml#tool_sources`, matching the repair its
`expects` and `accepted_values` describe; it was `shipgate.yaml#tool_identity`,
which sent an agent routing on `path` to a different section than the sentence
beside it. And the gap kinds where a *reference* failed to resolve —
`unresolved_agent_binding`, `unresolved_bound_tool`, `incomplete_handoff_graph`
— are now subjected by their `source_pointer` rather than by the agent that
made the reference, which is the healthy one: a handoff to a missing worker
was subjected `root [sdk]` and carried that name into the verdict and the fix
task.

**`next_actions[].path` is the one field a caller may open verbatim, and it
now always is.** An `edit` action names the manifest the run actually read:
`--workspace` discovery can select a nested `services/billing/shipgate.yaml`,
`verify` and `verification prepare` default `--config`, and an archived
`verify --base/--head` reads a temporary tree that is deleted before the error
is handled. The `CHANGE_ME` placeholder recovery, whose `path` was previously
the literal string `shipgate.yaml`, is included.

Two consequences follow from that rule rather than from any single fix.
A **declared artifact** is never published as a `path` at all — it has no one
base (the manifest's for most sources, the *entrypoint's* for an inventory a
framework file mounts), so the duplicate-inside-an-artifact recovery is a
`review` action that names the artifact in its sentence. And a failure
**evaluated against a ref that is not checked out** publishes no `path`
either: the archive is gone and the working tree may already hold the fix, so
the action names the commit and the path within it (`details.evaluated_ref`,
`details.manifest_in_ref`). Paths inside `details` are recorded as the loader
saw them and are *not* verbatim-openable; `docs/errors.json` says so.

**One manifest value is canonicalized.** `ArtifactPathConfig.path` — every
declared artifact under a framework block — is normalized, so `./tools.json`
and `tools.json` are one declaration. Declaring both used to read one file
twice and produce *two* canonical tools, with no duplicate detected and no
warning. A path containing a backslash is left exactly as written, and `..`
segments are preserved so the containment checks downstream still see what the
author wrote.

**One manifest value is narrowed.** `tool_sources[].id` is stripped, and a
blank id is now a `config_error` (exit 2) at manifest load instead of an
`input_parse_error` (exit 3) during the scan. The id is the key
`tool_inventories[].source_id` and `tool_identity.bindings[].members[].source_id`
join on, and both of those were already stripped where they are declared — so
`id: " orders "` matched neither and silently completed nothing. A manifest with
a blank or padded id was already not doing what it looked like it was doing;
now it says so at the point the value is read.

`docs/manifest-v0.1.json` carries the rule too, as `"pattern": "\\S"` on
`ToolSourceConfig.id`: the published schema is part of the manifest contract,
and a consumer validating against it would otherwise accept an id the runtime
refuses. A parity test asserts the two agree on empty, whitespace-only, and
padded ids.

**`verify` now routes this failure the way `scan` does.** Both commands, and
the verifier assembly path, share one recovery resolver, so a duplicate tool
source yields an `edit` action naming the manifest instead of the generic
"inspect the file referenced in the error" review action. The error kind and
exit code (3) are unchanged.

---

<a id="migration-note-unreleased-effect-coverage"></a>

## Migration Note: 1.0.0 — effect coverage, and the schemas that stayed frozen

Two capability schemas move: `capability_lock_schema_version` `0.6` → `0.7` and
`capability_lock_diff_schema_version` `0.7` → `0.8`. `report_schema_version`
(`0.42`), `packet_schema_version` (`0.17`), `verifier_schema_version` (`0.14`),
`contract_version`, and the manifest `version: "0.1"` are unchanged.

**Four published documents are restored to the bytes they were published with.**
`declaration_below_inferred_evidence` had been written into
`docs/packet-schema.v0.12.json`, `docs/verifier-schema.v0.9.json`,
`docs/capability-lock-schema.v0.6.json`, and
`docs/capability-lock-diff-schema.v0.7.json` while each kept its version
identifier. A consumer pinned to any of them was rejecting artifacts that
document is supposed to describe. The value now reaches only the current
document of each family, and the two capability schemas — which had no
successor version at all — gain one.

**A lock written under `0.6` still loads.** It is advanced to `0.7` on read, the
way a `0.12` packet and a `0.9` verifier artifact already are. Nothing needs
regenerating.

**A declaration accounts for an inferred observation on two conditions, not
one.** It must rank at or above the observation under both published effect
orders, *and* oblige at least that observation's built-in controls. Rank alone
is not enough: `financial_write` outranks `external_communication` but requires
no confirmation, which is what communicating outward requires. A repository
declaring a higher-risk effect across categories — `financial_write` on a tool
inferred to communicate externally — now raises
`declaration_below_inferred_evidence` where it was previously silent, and the
row names the controls rather than telling the reviewer to raise an effect that
is already higher. Coverage reads every policy-eligible claim on the action, so
a `risk_tags: [financial_action]` entry accounts for an inferred
`financial_write` exactly as the built-in control evaluator already treats it.

Nothing that gated before stops gating: the policy-applicability predicate now
asks the same question, and its new form is a strict superset of the rank
comparison it replaces.

**The row's repair may now be a `risk_tags` edit rather than an `effect` one.**
Where no single observed effect covers every uncovered observation *and* the
declared value, `next_action.expects` asks for
`action_surface.actions[].risk_tags`, `accepted_values` carries risk-tag values
rather than effect values, and the scaffold template includes the filled
`risk_tags` list. A consumer that assumed this row's `accepted_values` is always
the effect vocabulary should read `next_action.declaration_template` for the
shape.

**`acknowledged_overrides` emits one row per suppressed observation.** An
override that waives two inferred readings previously produced one row naming
only the strongest. The field set is unchanged; a consumer counting rows per
action may now see more than one, keyed by `subject_id` with distinct
`inferred_effect` values.

---

<a id="migration-note-unreleased-gap-subject-labels"></a>

## Migration Note: 1.0.0 — every gap subject is a label, never a raw id

No version moves: `contract_version`, `report_schema_version`,
`minimum_control_contract_version`, and every published schema document are
unchanged. No field is added, removed, or retyped — `subject` is still a
required string and `subject_id` already shipped in report schema `0.35`. What
changes is which value lands in which field.

**`evidence_gaps[].subject` is a display label everywhere now.** The policy
evidence gaps — every row of `report.policy_evidence_gaps`, also merged into
`release_decision.evidence_coverage.evidence_gaps` — put the raw canonical tool
id in the label and left `subject_id` null:

| Gap kind | `subject` before | `subject` after | `subject_id` |
|---|---|---|---|
| `inferred_policy_applicability` and the other kinds raised per finding | `tool_v2_2c9ee6ae…` | `send_email_preview [openai_sdk_static]` | was `null`, now `tool_v2_2c9ee6ae…` |
| `mixed_policy_evidence` and the other kinds raised per action | `support.search_kb [tool_v2_445a251a…]` | `support.search_kb [support_mcp_tools]` | was `null`, now `tool_v2_445a251a…` |
| any kind raised per policy-pack rule | `create_refund [tool_v2_6dcebe42…]` | `create_refund [api]` | was `null`, now `tool_v2_6dcebe42…` |

The label reaches readers: `evidence_gap_headline` prints it in the CLI's
`Improve evidence:` line, in the decision reason, and in the GitHub step
summary, where a 64-hex digest names nothing anyone can open.
`samples/support_refund_agent` had `support.search_kb` in one gap list under
both spellings at once.

**A consumer that joined `evidence_gaps[].subject` against
`tool_catalog[].tool_id` should join on `subject_id`,** which is the field
documented for identity and is now populated on these rows for the first time.
Joining on the label was always ambiguous — two catalog ids can render the same
`name [provider]`. The report's own conservation invariant now refuses a raw
canonical id *anywhere* in any gap's `subject` — matched by shape, so a
wrapped id (`create_refund [tool_v2_6dcebe42…]`) and an id that resolves to no
catalog row are refused too. The raw form cannot return for a kind that happens
to be exempt today.

**Labels are resolved from the tool catalog by `tool_id`.** Two of these
emitters previously rendered from their own fields, which diverged from the
catalog for the same tool: `ActionFact.provider` is
`_normalize_token(provider or source_id or source_type)`, so a source id of
`my api` produced `create_refund [my_api]` on an action-policy gap and
`create_refund [my api]` on a catalog-backed one. A tool whose id resolves to
no catalog row is labelled by its name, or failing that by the check id that
raised the gap — never by the id itself, which stays in `subject_id`.

Subjects that never named a catalog tool — agent ids, source-loader warning
text — are unchanged, and keep `subject_id: null`.

---

<a id="migration-note-unreleased-absent-input"></a>

## Migration Note: 1.0.0 — an absent input is refused, not misreported

No version moves: `contract_version`, `report_schema_version`,
`minimum_control_contract_version`, and every published schema document are
unchanged. No new error kind and no new exit code — the refusals below all use
the existing `config_error` / exit `2` slot published in
[`docs/errors.json`](docs/errors.json).

**`--workspace` must name an existing directory, on every command that takes
it.** A path that does not exist is an invocation error, decided before any
output directory is resolved and before anything is written. This changes
observable behavior in three ways, all of them narrowing a case that
previously "succeeded":

- `verify --preview` no longer exits 0 for a `--workspace` that does not
  exist. Its documented "always exits 0" holds for every workspace it
  *evaluates*; an absent path is not one, and preview previously created the
  entire path and wrote its artifact set into it before answering
  ([#389](https://github.com/ThreeMoonsLab/agents-shipgate/issues/389)).
- `detect`, `check`, `trigger`, `mcp audit`, and `install-hooks` previously
  exited 0 with a payload describing a workspace that was not there; they now
  exit 2 with `config_error`.
- `init --write`, `audit --host`, and `verification prepare`/`worker`
  previously raised an unhandled `FileNotFoundError` (exit 1); they now exit 2
  with `config_error`.

A caller that passes an existing `--workspace` — every documented invocation —
is unaffected. Commands whose `--workspace` is optional still accept its
absence, which means "discover instead", not "a workspace that is missing".

**Three manifest states, three messages.** An absent manifest, an empty one,
and a present-but-not-a-mapping one previously produced one identical
`Config file must contain a YAML object: <path>` string while routing to two
different actions, so `control.reason` and `control.next_action` could
disagree about whether the file existed
([#384](https://github.com/ThreeMoonsLab/agents-shipgate/issues/384)). The
messages are now:

| State | Message | `next_action.kind` |
|---|---|---|
| absent | `Config file not found: <path> in <cwd>.` (plus the `init --write` hint for `shipgate.yaml`) | `verify` |
| empty | `Config file is empty: <path>.` | `edit` |
| not a mapping | `Config file must contain a YAML object: <path>` | `edit` |

Routing is unchanged — it always distinguished the cases. `ConfigError` and
exit `2` are unchanged for all three. Consumers that pattern-matched on the
`must contain a YAML object` text to detect a *missing* manifest must switch
to the diagnostic id (`SHIP-DIAG-MISSING-MANIFEST`) or to
`control.next_action`, which is what the recovery hint in `docs/errors.json`
already directs.

**A manifest type mismatch is a `config_error`, not an `internal_error`.**
`raise TypeError` inside a Pydantic validator propagates past the
config-loading boundary; the manifest schema now raises `ValueError`
throughout, so a YAML mapping where a list belongs produces
`Invalid shipgate.yaml:` with the offending field path and an `edit` route,
the same as any other manifest validation failure
([#387](https://github.com/ThreeMoonsLab/agents-shipgate/issues/387)). Error
text for these fields changed shape (it now names what was written, e.g.
"must be a list of artifact paths, but is a mapping"); the field path prefix
is the stable part.

---

<a id="migration-note-unreleased-doctor-environment"></a>

## Migration Note: 1.0.0 — `doctor --json` reports the environment that answered

No version moves: `contract_version`, `report_schema_version`,
`minimum_control_contract_version`, and every published schema document are
unchanged. This is additive on two surfaces and adds one error kind.

**Every `doctor --json` payload gains an `environment` field**, and so does
every `doctor` agent-mode error line — including the discovery failure, where
`--json` prints no payload at all. It states the running interpreter, the
launcher and any `agents-shipgate` / `shipgate` console script on `PATH`, where
the imported package came from, the installed / imported / source-tree versions,
and `mismatches[]`. Every existing field on those payloads is unchanged, and
`environment` is deliberately **not** folded into `control.input_id`: it carries
absolute machine-specific paths, and `input_id` is the identity of what `doctor`
decided about a manifest, which the environment does not change. The same
manifest therefore keeps the same `input_id` on every machine, as before.
Field-by-field contents are in
[`docs/diagnostics.md`](docs/diagnostics.md#which-shipgate-answered-the-environment-block).

**New agent-mode error kind `environment_error`, exit code 4** — the existing
"other agents-shipgate error" code, not a new one. It is emitted by the
repository launcher before any Shipgate code is running (an unsupported
interpreter, or one that cannot import the package or its dependencies), so it
is the only kind that carries no `control` envelope; it carries `environment`
instead. Published in [`docs/errors.json`](docs/errors.json), whose
`schema_version` stays `0.1`. A consumer that switches on `error` and falls
through on unknown kinds is unaffected.

**New public helper `agents_shipgate.invocation.render_cli_override`** — the
host-rules inverse of how `AGENTS_SHIPGATE_CLI` is parsed. It exists because
that variable now has a writer: the repository launcher announces itself through
it. `join_argv` remains POSIX on every platform and is unchanged; the two are
not interchangeable, and the docstrings say which parser each one pairs with.

The launcher itself, `./shipgate`, is a development entry point in the source
tree. It is not part of the wheel and nothing under `src/` imports it, so it
changes nothing for an installed Agents Shipgate.

---

<a id="migration-note-unreleased-setup-control-envelope"></a>

## Migration Note: 1.0.0 — one control vocabulary across the setup commands

Runtime contract `23 → 24`. `minimum_control_contract_version` **stays at 21**,
and the `AgentControl` union is byte-identical to v21.

A typed `edit` action was added to that union for setup routing and then removed
*from the union*. The union is embedded by six durable published schemas —
verifier, agent-handoff, preflight, agent-result, agent-boundary-result, and
verify-run — so widening it widened all six under unchanged identifiers, and five
of those artifacts record no `contract_version` for a consumer holding a stored
payload to disambiguate with.

The two surfaces therefore say the same step differently, and a reader needs both
halves:

- **The emitted envelope** (`shipgate.agent_control/v1`, stdout only) publishes
  the edit itself: `control.next_action` is `{"kind": "edit", "path": …,
  "expects": …, "command": null}`, declared as `SetupEditAction` on this document
  alone. **`control.next_action.path` is the file to open**, exact and
  unnormalized. This is the field to route on.
- **The shared `AgentControl` object** those durable artifacts embed cannot hold
  that variant, so it carries the `configure` command that *checks* the edit,
  with the file named in `why`.

Routing an envelope-only consumer at the check alone was tried and withdrawn: it
re-ran `doctor` against an unchanged file and returned the identical action
forever.

What v24 does widen is `shipgate.agent_control/v1` itself — new `operation`
values, `decision_source: "setup"`, and closed per-source `decision`
vocabularies. That document is emitted on stdout and never written as an
artifact, so there are no stored envelopes to disambiguate and its new
operations cannot appear in anything a v21 consumer holds.

**Runtime contract v25 uses the same split once more.** `verify` publishes
`control.next_action.kind: "confirm_declarations"` on a working-tree run whose
verdict is `insufficient_evidence` and at least one open declaration question is
one the scan can answer from its own evidence. It is not published on a
ref-bound run: `apply-patches` mutates the checkout, so the exact rerun would
re-scan the commit the edit is not in yet. The action carries the exact
`apply-patches` command plus `questions[]` (each row tagged `authorable_by`,
capped at six, human-owned rows first, with `agent_authorable` /
`human_authorable` as the real totals). The shared `AgentControl` again holds
the same step as the `repair` command it truthfully is, so it is unchanged and
`minimum_control_contract_version` stays at `21`. Authority does not move with
the route: `permissions` is publish-only — `edit`, `commit`, `push`,
`update_pr` true; `merge` and `report_complete` false — because writing a
declaration touches the trust root and a person still merges it.

**Two routing behaviours change**, and neither is additive:

- `init --write` no longer names a runnable `scan` in `next_action` when the
  manifest it wrote still holds an unresolved human-owned declaration. Both
  `next_action` and `next_actions[0]` now carry the same human review route the
  control envelope does. This is the point of the change: the previous pairing
  published an executable command that would carry an unfilled
  `agent.declared_purpose` into a release decision, beside a control state that
  authorized nothing (#325).
- A remediation with no faithful argv form — a leading `NAME=VALUE` assignment,
  or a `<placeholder>` — routes the *envelope* to a human rather than into
  `control.next_action.command`. `next_actions[]` is unaffected: `NextAction`
  already withholds its computed `executable`/`args` pair in that case and lets
  the rendered string stand, which is an option the envelope's command field
  does not have.

**`detect --json`, `init --json`, and every `doctor --json` payload gain a
`control` field.** It holds the same `shipgate.agent_control/v1` envelope that
`verify --format control`, `check --format agent-control-json`, and
`agents-shipgate agent control` already emit — same schema document, same
`control_state`, same six-way `permissions` vector. Additive: every existing
field on those payloads, including `next_action` and `next_actions[]`, is
unchanged, and the agent-mode *error* line still carries the ranked-action
fields rather than a control object.

**Setup control is distinguishable from gate control, in both directions.**
These commands run before a release decision exists, so their envelope carries
`decision_source: "setup"` and a `decision` from the closed vocabulary
`setup_complete | setup_incomplete | setup_not_applicable`. The published JSON
Schema requires `decision_source: "setup"` to come from `detect`/`init`/`doctor`
*and* requires those operations to report no other source, so a reader switching
on `decision_source` can never take a setup verdict for
`release_decision.decision`.

**A setup envelope authorizes nothing.** Setup reads no diff, so all six
`permissions` are `false`, no setup envelope binds an artifact or a
`current_control_id`, and `control_state: "complete"` is unreachable for these
operations because `CompleteControlEnvelope.operation` is fixed to
`verify`/`check`. A successful `init` is not permission to commit, merge, or
report a task complete.

**Unresolved human-owned manifest placeholders route to a human.** When
`shipgate.yaml` still carries an unresolved `declared_purpose`,
`prohibited_actions`, policy, or permission value, the setup control state is
`human_review_required` and the action names the exact file, line, and field.
These are declarations a person makes; the same rule already governs
`do_not_auto_assert` and `baseline save --owner/--reason`. Placeholders an agent
can legitimately resolve from the repository — a tool-source path, a project
name — stay coding-agent work.

**`next_action` can be `kind: "edit"` — on setup operations only.** A typed
coding-agent step carrying `path` and `expects` and no `command`, for work that
is unambiguously the agent's and has no executable form. It is declared as
`SetupEditAction` on the *envelope* rather than in the shared `AgentControl`
union, so the six durable schemas that embed that union are untouched, and both
layers reject an edit route on any operation but `detect`/`init`/`doctor`.

**Human-owned declarations cover every `do_not_auto_assert` surface with a
manifest spelling.** Unresolved placeholders under `agent_bindings`,
`tool_identity`, `action_surface`, `permissions`, `policies`, `checks`,
`baseline`, `human_ack`, `risk_overrides`, and `organization`, and the leaf
fields `declared_purpose`, `prohibited_actions`, `owner`, `reason`, `expires`,
`approval`, `approval_required`, `authority`, `effect`, `safeguards`,
`confirmation`, and `idempotency`, all route to a human. Anything else — a
tool-source path, a project name — stays coding-agent work.

**`scan` is not part of this rollout.** `agent control` on a `scan` generation
reports `decision: null` / `decision_source: "none"`, exactly as it did before,
with a `reason` stating that the verdict is *withheld* rather than absent. A
scan pointer records no HEAD, no worktree overlay, and no input set, so no
artifact in that directory can show its verdict still describes the workspace —
editing the manifest, a `tools.json` it references, a policy pack, or a baseline
leaves the pointer reading cleanly. Publishing a verdict from `scan` needs a
complete, reconfirmable input snapshot threaded through report generation and
pointer publication; that is a separate change, and #323's scan half stays open
until it lands. `verify` is where a verdict a reader can check comes from.

**`shipgate.current_control/v1` is unchanged.** An earlier revision of this
branch added an optional `policy_snapshot_path` to `workspace_identity`.
`current_control_id` hashes the whole pointer with `exclude_none=False`, so
adding any field — even one nobody sets — re-hashes every pointer already on
disk and makes it unreadable by the release that introduced it. Nothing in the
pointer moved.

**Setup routes that changed.** `detect` hands a configured workspace to `doctor`
rather than declaring setup complete: it does not read the manifest, so
asserting completion from the presence of a file contradicted `init` and
`doctor`, which return `human_review_required` for the same manifest while a
declaration is unresolved. `doctor`'s emitted `verify` command carries both
`--workspace` and an absolute `--config`, because `verify` resolves a relative
config against the workspace it is given and the two composed into a path that
does not exist. `init`'s agent-mode error line carries the same selected route
as its stdout payload, rather than an independently composed one.

**Placeholder locations come from the parsed document.** The line scanner they
replaced tracked indentation, so the flow spelling
`agent: {name: bot, declared_purpose: [CHANGE_ME]}` — which the loader accepts —
was reported at path `agent` and classified as coding-agent work. Ownership does
not depend on how someone spelled their YAML. A sequence element is now reported
as `<field>[<index>]` rather than by its own text, so
`agent.declared_purpose.CHANGE_ME` becomes `agent.declared_purpose[0]`; the
`placeholders[]` field on `init --json` carries the new spelling, and `doctor
--json` publishes that field for the first time.

**`decision_source` constrains `decision`.** Each source admits only its own
engine's vocabulary (`release_decision` → the four release decisions,
`agent_boundary` → `allow`/`warn`/`require_review`/`block`, `setup` → the three
setup verdicts), and each operation admits only the engine that decides it
(`check` → `agent_boundary`, `verify`/`preview`/`scan` → `release_decision`,
`detect`/`init`/`doctor` → `setup`). Both in Pydantic and in the published
schema. Naming the engine was half the job: without this, a merge-authorizing
`complete` envelope could report `decision_source: "release_decision"` beside a
boundary verdict, or beside an arbitrary string.

---

<a id="migration-note-unreleased-invocation-spelled-commands"></a>

## Migration Note: 1.0.0 — commands spelled for the invocation that emitted them

Runtime contract `22 → 23`. `minimum_control_contract_version` **stays at 21**:
the `AgentControl` union is unchanged, and v23 changes only how the commands
inside it are spelled. A consumer written against v21 control fields keeps
reading them unchanged.

**Every emitted command names the entry point that started the process.** A
console-script run emits exactly what it emitted before — `agents-shipgate …`
or `shipgate …`, byte for byte. A `python -m agents_shipgate` run emits
`<sys.executable> -m agents_shipgate …`, spelled by interpreter path because a
bare `python` resolves through `PATH` and can land on a different interpreter.
`AGENTS_SHIPGATE_CLI` overrides both and is parsed with the host's own rules
(POSIX `shlex` elsewhere, MS C-runtime argv rules on Windows, so a value like
`C:\Tools\agents-shipgate.exe` keeps its backslashes). `command` never contains
`__main__.py`.

This covers `next_action` / `next_actions[]` on every command, the agent-mode
error line's own `command` field, preflight signals' `related_command`, matched
trigger rules, and the control and repair commands the verifier and boundary
publish.

**`command` is a POSIX rendering on every platform, not a host-shell promise.**
There is one renderer and one parser, and they must agree — a string rendered
by one set of rules and parsed by another silently changes the values it
carries. Uniform POSIX quoting round-trips Windows paths exactly, because a
single-quoted `'C:\repo'` keeps its backslashes. It does **not** make the
string safe to paste into `cmd.exe` or PowerShell, where single quotes are not
quoting; nothing would. Recover argv instead:

```python
subprocess.run(shlex.split(command))   # exact on every surface and every host
```

**New on `next_actions[]`: `executable[]` and `args[]`.** A shell-independent
projection of `command`, runnable as `[*executable, *args]`. Both are computed
from `command` and ignored on input, and are recomputed on every read, so they
cannot describe a command the action no longer holds. They are **omitted, not
`null`**, when the command has no faithful argv form — a leading `NAME=VALUE`
assignment, or any unquoted shell metacharacter (operators, redirection and
therefore `<placeholder>` syntax, substitution, globs; only single quotes make
those inert). Every action that cannot carry an argv is therefore byte-for-byte
what it was.

`NextAction` is published with `extra="forbid"`, so these two properties are
**not** additive for a strict consumer validating against the v22 shape — that
is what this contract version carries. The model accepts its own serialization:
the pair is stripped on input and recomputed, so a round-trip validates and a
pair edited in transit is replaced by the one its command implies.

The argv pair is scoped to `next_actions[]`. The operational control contracts
(`control.next_action`, `allowed_next_commands`, verifier repairs,
`fix_task.verification_command`) publish the command string only, and
`shlex.split` is the documented recovery there. Extending the pair into them
changes the `AgentControl` union, which would raise
`minimum_control_contract_version` and force a down-projection for the frozen
`shipgate.codex_boundary_result/v2` schema; that is tracked in
[#369](https://github.com/ThreeMoonsLab/agents-shipgate/issues/369).

**Durable artifacts are unaffected.** `report.json`, `report.md`, and
`packet.*` stay canonical: `docs/architecture.md` makes *same inputs → same
report* non-negotiable, and process entry is not an input. Published contract
vocabulary (`primary_commands`, `.well-known/agents-shipgate.json`) is
canonical for the same reason.

---

<a id="migration-note-unreleased-compact-control-envelope"></a>

## Migration Note: 1.0.0 — the compact control envelope

Runtime contract `21 → 22`. `minimum_control_contract_version` **stays at 21**:
v22 adds a projection of the `AgentControl` union and does not change the union
itself, so every consumer written against v21 control fields keeps reading them
unchanged. The local downstream contract schema advances `9 → 10`.

New: `shipgate.agent_control/v1`
([`docs/agent-control-schema.v1.json`](docs/agent-control-schema.v1.json)), a
compact control envelope emitted on stdout by three commands. It answers the
whole routing question in one object — tool execution status, the release or
boundary decision and which engine produced it, the control state, the six-way
`permissions` vector, who acts next, the exact next action, and the
content-addressed path and hash of every artifact `current-control.json` binds
(`check` publishes no pointer and binds none) — within a published budget of
`agent_control_budget_bytes` (6144 as of contract v25, re-derived when the `confirm_declarations` route added a question list) — a measured target, not an enforced cap.
It is **not** written to disk, and it decides
nothing: every field is copied from a producer that already published it.

Three CLI changes, one of which is a default:

- `agents-shipgate verify --format control` — **added**. `--format json` and
  `--json` are unchanged and still emit the full `verifier.json` artifact, and
  agent-mode auto-detection still resolves to `json`. Flipping that default is
  a compatibility event and belongs to the command-by-command rollout.
- `agents-shipgate check --format agent-control-json` — **added**.
  `agent-boundary-json` remains the default and is unchanged.
- `agents-shipgate agent control` — **default output changed** from the raw
  `shipgate.current_control/v1` pointer to the envelope. `--format pointer`
  returns the previous output byte for byte. The pointer deliberately records
  no route, so a caller reading it still had to open the handoff to learn what
  to do next; the envelope joins the pointer's currency guarantee to the route
  the bound verifier already published. This command first shipped in
  `0.16.0b7` and has not appeared in a tagged release.

`verify --format text` now prints the control state, the next actor, and the
permission vector *before* the existing `Agents Shipgate verify: <verdict>`
line. That line, and every line after it, is unchanged.

Both entry points apply the same currency test. `verify --format control` reads
its own published pointer through the generation-safe protocol, validated
against the live workspace, and **withholds authority** when the workspace has
moved past what the run evaluated — a `--head` run in a dirty worktree reports
`human_review_required` with the refusal as its reason rather than `complete`.
The exit code is unaffected: withholding authority is not failing the run. The
route is read from the verifier bytes captured inside that protocol, so a
pointer can never be reported beside another generation's decision.

`artifacts[].path` is relative to the directory the command was invoked from,
falling back to an absolute path when the reports directory sits outside it. A
reader can open it exactly as given.

Terminal authority is constrained by provenance. A `complete` envelope is only
representable from `verify` (with a named `current_control_id` and a non-empty
`artifacts` map, decided by `release_decision`) or from `check` (with neither,
decided by `agent_boundary`); `scan` and `preview` cannot complete at all. A
`verify` route keeps `verify_required: true`. Both rules are published in the
JSON Schema as well as enforced in Python, so a schema-only consumer and an
in-process one accept the same set.

`verify --format control` reports only *this* invocation's generation: if
another run publishes over the directory while this one is reporting, the
identities no longer match and authority is withheld rather than borrowed. The
currency comparison re-observes the workspace after the pointer is confirmed,
so a commit landing mid-read refuses the pair.

`input_id` names the input the control was assessed against — the boundary
`audit_id`, or the verifier `request_id` — and the `complete` variant requires
it, so terminal authority can always be traced to its subject. `pending_review[]`
carries review obligations that survive a non-terminal route. Human-readable
output renders control characters visibly and keeps each field on one line;
JSON keeps the exact bytes. `agents-shipgate agent control` now reports a
*current but routeless* generation (a `scan` pointer) as an ordinary envelope
with exit 0 and merge denied, rather than exiting non-zero — a non-zero exit
keeps its documented meaning that no control identity is current.

Unrelated fix in the same change: `.shipgate/agent-contract.json` now upgrades
in place from any superseded managed version, not only from renders whose exact
hash was recorded. Repositories on local-contract schema 8 or 9 were reported as
`skipped_user_modified` and left un-upgraded.

Exit-code semantics are unchanged and now explicitly documented: the exit code
is the CI gate signal and depends on `ci.mode`. In advisory mode every decision
— `blocked` included — exits 0, and `review_required` has no exit code of its
own in either mode. `permissions.merge` is the only field that answers "may I
merge".

<a id="migration-note-unreleased-publish-vs-merge"></a>

## Migration Note: 1.0.0 — publish authority is not merge authority

Runtime contract `20 → 21`, and `minimum_control_contract_version` moves
`14 → 21` because the discriminated `AgentControl` union itself changes. No CLI
surface changed. Contract v20 published the current-control pointer and the
refresh protocol; this note is v21, and the two are independent — a consumer
reading `current-control.json` gets the same `permissions` vector described
here, on every state, so the one atomic read answers "what may I do now?"
rather than only "which run is current?".

Under contract v14–v19 a human route was one universal stop:
`control.state: "human_review_required"` with `must_stop: true` and
`allowed_next_commands: []`. For an agent working on a pull request that
denied commit, push, and PR updates — the very actions needed to produce the
evidence a human was being asked to review. The gate was correct and the
workflow was circular.

**Human review now gates merge and completion, not publication of review
evidence.** Two additive changes carry that:

- `control.permissions` — an object with the exact booleans `edit`,
  `commit`, `push`, `update_pr`, `merge`, `report_complete`. It is fixed by the
  state *and the route*, never set independently. `merge` and
  `report_complete` always equal `completion_allowed`, and `must_stop=true`
  authorizes nothing at all. The converse does not hold: an
  `agent_action_required` route that runs *before* any diff was read
  (`fetch_base`, `install`) authorizes only its own `next_action`, because
  Shipgate has no assessment to stand behind yet.

  It is required only on `review_publishable`, so a pre-contract-20 payload
  still parses; readers reconstruct an absent vector as *nothing authorized*
  rather than defaulting it to "publication allowed".
- `control.state: "review_publishable"` — a fourth state meaning "a human must
  approve the merge, and the agent may still publish the change for that
  review". `completion_allowed: false`, `must_stop: false`,
  `stop_reason: null`, a human `next_action` pinned to `kind: "review"`, and
  `allowed_next_commands` carrying **at most one** command: the exact rerun
  that regenerates the same evidence against the committed refs. Both
  constraints hold in generated JSON Schema, not only in Pydantic.

  Publication is a claim about an evaluated, bound change, so it additionally
  requires all of: a subject `verify` can replay (a caller-supplied diff never
  qualifies), input the evaluator actually read in full, and — on verifier,
  handoff, and verify-run — a succeeded run carrying a non-blocked release
  decision. Those are container-level invariants, enforced in both Pydantic and
  JSON Schema, because the control variant alone cannot see the substrate.

`human_review_required` keeps its exact old meaning and is now reserved for
results Shipgate cannot vouch for: a `blocked` release decision, a `block`
boundary decision, a run whose execution failed, unreadable or unbindable diff
input, an undeclared capability surface with no discovery route, preflight
protected-surface touches, and MCP audit blocks. Those still authorize nothing.

Migration:

- Consumers that switch on `control.state` **must** add a `review_publishable`
  branch. Unrecognized states must continue to fail closed — treat them as
  requiring human review. The installed Claude Code Stop hook does this: it
  ends the turn on `review_publishable`, states that commit/push/PR-update
  remain authorized, and names the rerun command.
- Consumers that read only `must_stop` and `completion_allowed` need no change
  and lose no safety. `completion_allowed` is still false, so "may I report
  this done?" is unchanged; `must_stop: false` now means what it always said —
  some agent action is authorized.
- Legacy artifacts are unaffected. A payload without `control`, or a
  pre-v20 payload, normalizes to `human_review_required`. `review_publishable`
  is only ever produced by an emitter that asserted the publication fact.
- The deprecated `shipgate.codex_boundary_result/v2` projection stays byte-
  frozen: it omits `control.permissions` and renders `review_publishable` as
  `human_review_required` with `must_stop: true`, exactly as `pending_review[]`
  stays off that format. Use `--format agent-boundary-json` for the current
  contract.
- `agents-shipgate contract --json` gains `agent_control_permissions[]`.

**Every schema that carries a control advances, and the prior file is frozen.**
`control.permissions` is a new property and the published variants are
`additionalProperties: false`, so a payload emitted by this release does not
validate against the schema published under the previous identifier. Adding the
field without moving the identifier would have made one version name mean two
incompatible shapes, so:

| Schema | Was | Now |
|---|---|---|
| verifier | `0.7` | `0.8` |
| agent handoff | `shipgate.agent_handoff/v6` | `shipgate.agent_handoff/v7` |
| verify-run | `shipgate.verify_run/v3` | `shipgate.verify_run/v4` |
| shared agent result | `agent_result_v2` | `agent_result_v3` |
| agent boundary result | `shipgate.agent_boundary_result/v1` | `shipgate.agent_boundary_result/v2` |
| preflight | `0.3` | `0.4` |
| downstream local contract | `7` | `8` |

Each previous `docs/*-schema.*.json` is unchanged and remains a frozen
reference; artifacts emitted under those identifiers still parse. `verify --format
agent-boundary-json` and `shipgate check` keep their flag spellings — only the
`schema_version` string moved.

`shipgate.codex_boundary_result/v2` is deliberately **not** in that table. It is
a frozen deprecated contract and now has its own snapshotted control union
rather than inheriting the live one, so it publishes exactly what it always did.

Audit ids do not rotate. `audit_id` identifies the assessment, so the schema
token it hashes is pinned to the value established ids were issued under
instead of tracking the live wire version — a stored id survives an additive
schema bump.

---

<a id="migration-note-unreleased-diff-status"></a>

## Migration Note: 1.0.0 — diff input health

Verifier schema `0.6 → 0.7` and trigger catalog `0.2 → 0.3`. That change did
not move `contract_version` (see the note above, which does); no CLI surface
changed.

`verifier.json` gains a top-level `diff_status` block that reports whether the
compared change set was actually read: `completeness` (`complete` / `partial` /
`unavailable`), a `reason` token (`not_attempted`, `refs_missing`,
`merge_base_missing`, `unrelated_histories`, `objects_missing`,
`metadata_limit_exceeded`, `body_limit_exceeded`, `git_timeout`,
`git_failed`), a bounded path-redacted `detail`, the
`remediation`, and `fetch_repairable`. Verifier v0.6 remains a frozen reference
and its artifacts still parse.

The trigger evaluator gains `input_status` and `evaluation_status`, and
`should_run`, `run_shipgate`, `skip`, and `skip_reason` become nullable.
**Consumers that switch on `should_run` must handle `null`**: it means the diff
was not read in full, so no verdict exists. Treating `null` as falsy is safe —
it routes to "do not claim this PR is irrelevant" — but reporting it as "skip"
is not. `next_action.kind` gains `"input_required"`; treat unrecognized kinds as
"no command is authorized".

Before this change, a shallow clone with no reachable merge base and a partial
clone with unfetched blobs both surfaced as one message, and the trigger then
evaluated the empty inputs those failures left behind and reported
`skip_reason: "no_match"` — "nothing in this PR signals a tool-surface change" —
about a PR the verifier never read. On a workspace without `shipgate.yaml` the
failure was not surfaced at all: preview routed to "Shipgate is not configured
in this workspace". Both are fixed, and a diff whose body cannot be read now
keeps the changed paths that were collected successfully instead of discarding
them.

---

<a id="migration-note-0-16-0b7"></a>

## Migration Note: 0.16.0b7

Runtime contract `18 → 19` grades the LOCAL boundary stop. Under contract
v14–v18 every `require_review` boundary violation projected
`control.state: "human_review_required"` with `must_stop: true` — a
CLAUDE.md prose edit and a critical grant expansion were operationally
identical. As of v19, a `require_review` violation set that is entirely
low/medium risk projects `control.state: "agent_action_required"` with the
exact verify command, and the review obligation is carried in the new
additive `pending_review[]` field on
`shipgate.agent_boundary_result/v1` (each entry: `check_id`, `rule_id`,
`path`, `risk_level`, `title`, `reviewers`, `note`). The deprecated
`codex-boundary-json` format grades identically but its frozen v2 schema
does not carry the new field.

The graded band is deliberately narrow and fail-closed. These keep the
`human_review_required` stop at any scored risk: any `block` action or
`critical` risk in the set; `BOUNDARY-INPUT-INCOMPLETE` and parse-failure
evidence (unparseable content is not reviewable content — only the
parseable `unknown_host_config_key` case is band-eligible);
`CODEX-AGENTS-SHIPGATE-REQUIREMENT-REMOVED` (gate weakening); experimental
adapter surfaces; and every violation touching a gate-governing trust-root
class (`manifest`, `policy`, `ci_gate`, `shipgate_state`), which preserves
the composite-diff guarantee that a safe manifest append bundled with an
unsafe manifest edit still routes to a human.

The v14 control invariants are unchanged: `must_stop` equals
`state == "human_review_required"`, `completion_allowed` equals
`state == "complete"`, and conversation-level acknowledgement cannot clear
control state. PR-time `release_decision` branching is byte-identical —
the graded rows still land in `review_items` and route to a human at the
merge gate; only the local turn-level stop is relaxed.

The installed Claude Code **Stop hook** now follows `verifier.control.state`
instead of the release label: `complete` ends the turn silently,
`agent_action_required` blocks the stop once and names the one exact
remaining command, and `human_review_required` prints a hand-off notice and
lets the turn end — a Stop-hook block forces continued agent work, which
`must_stop` semantics forbid. Unparseable or unrecognized verifier output
warns, is never cached, and is never treated as passing. The cold-start
no-manifest case advises `verify --preview` instead of forcing
continuation. Reinstall hooks (`agents-shipgate install-hooks --target
claude-code --write`) to pick up the new behavior.

<a id="migration-note-0-16-0b6"></a>

## Migration Note: 0.16.0b6

Runtime contract `16 → 18` lands in two additive steps. Contract v17 makes
verification identity and artifact closure content-addressed. A successful
`verify` now emits a verification plan,
decision-free unit result, artifact manifest, and terminal receipt. The
receipt is written last and binds the resolved Git subject, hashed inputs,
engine requirement, executor, assembled decision, and every referenced
artifact. Git snapshots use exact blobs rather than `git archive`; cache reuse
cannot change public artifacts; and expiry-sensitive policy evaluation uses a
declared evaluation date rather than a worker wall clock.

Contract v18 adds a fail-closed human-authorization overlay for one exact
coding-agent operation. A trusted host derives an unsigned
`shipgate.human_authorization_request/v1` from a validated prior receipt, the
current request/subject/decision/tree identities, and the complete ordered
review set. The host or authenticator — not Agents Shipgate and not the coding
agent — signs the request with Ed25519. Agents Shipgate ships no private key and
no signing/approval CLI. The corresponding trust policy must live outside the
evaluated workspace and be protected from writes by the coding agent. On
POSIX, its only lookup location is the OS account home's fixed path
`~/.config/agents-shipgate/human-authorization-trust-policy.json`; `HOME` and
`XDG_CONFIG_HOME` are ignored for this lookup. The
request records the source receipt, artifact set, engine, and executor so a
host signer can require trusted-CI provenance or rerun verification. The
request-building command copies the receipt and artifact-set IDs from the
validated prior receipt, but later verify and execute passes do not transport
that prior closure and therefore treat those two IDs as signer-authenticated
provenance labels. They independently cross-check the engine and executor and
rebuild every operational identity. A content-addressed closure detects
mutation but is not an authenticity claim.
It exposes the evaluated base commit and merge base; the signer must review the
source commit's complete ancestry and reachable history, not only its final
tree. Guarded execution caps the serialized source graph at 512 MiB and 120
seconds, while the mandatory host broker remains responsible for tighter
resource quotas. The serialized-pack limit does not bound expanded-object
indexing memory or CPU; production brokers need a cgroup, container, or
equivalent host quota. Authorization-eligible plans require an exact effective
`plugins_enabled=false` mode, and the protected executor rejects enabled
third-party plugins before engine validation so plugin entry points never enter
the broker TCB.

A second `agents-shipgate verify --no-plugins --authorization <external-grant>` recomputes
the verification identities and accepts the grant only when its signature,
trusted principal, repository scope, validity window, request, subject, trees,
decision, complete review set, and typed operation still match. The v1
operation binds the exact evaluated commit, a canonical credential-free HTTPS
repository endpoint, one full destination ref, and explicit
`--force-with-lease=<ref>:<expected-oid>`. Synthetic PR merge receipts are not
eligible to authorize pushing a different parent commit. Only a
successfully evaluated `review_required` result can then project
`control.state: "agent_action_required"` with that one exact command. The
release decision remains `review_required`; `merge_verdict` remains
`human_review_required`; `can_merge_without_human` and `completion_allowed`
remain false. The one published command is the guarded
`agents-shipgate authorization execute` consumer, which revalidates the
receipt from an immutable snapshot, trust policy, clock, engine, repository,
and commit immediately before copying and fsck-validating the reachable Git
graph in an isolated store. It disables replacement objects, hooks,
configuration, and HTTP redirects before the signed push. Invalid, expired,
stale, or mismatched grants fail closed and publish zero allowed commands. If
the remote ref moves after approval, Git rejects the push because the signed
operation carries the previously reviewed lease OID.

This release defines and consumes the authorization protocol; it does not ship
a Codex, Claude Code, or other UI signing adapter. A host integration must
authenticate the human, keep the Ed25519 private key outside agent reach,
attest or rerun the source verification, present the complete request for
review, and return the signed grant. It must also isolate the trust policy,
launcher environment, interpreter, entire virtual environment and
`site-packages` tree (including startup `.pth` files), dependencies,
credentials, and installed distribution from coding-agent writes. Same-UID
file modes alone are insufficient; without a host-enforced
write boundary authorization stays disabled. Editable installs rooted in the
authorized repository are ineligible. V1 is push-only.

Report advances `0.33 → 0.34`, packet `0.11 → 0.12`, verifier `0.4 → 0.6`,
verify-run v2 → v3, handoff v4 → v6, attestation `0.4 → 0.5`, registry
`0.3 → 0.4`, organization evidence bundle v1 → v2, generated downstream
contract `5 → 7`, and safety qualification envelopes v3 → v4. New
verification-plan, unit-result, artifact-manifest, and receipt schemas begin
at v1. The authorization request, signed grant, verifier evaluation, and trust
policy also begin at v1 and share
[`docs/human-authorization-schema.v1.json`](docs/human-authorization-schema.v1.json).
Previous schemas remain frozen readers; they are not emitted by default.

`verify-run.run_id` remains for one compatibility cycle as an exact alias of
`request_id`; it is no longer independently derived. GitHub Actions evaluate
the immutable `${{ github.sha }}` by default. A default `pull_request`
synthetic-merge receipt carries no executable source authority; authorization
requires a separate verification of the actual PR head commit. Current Action
outputs include the validated receipt path and request, receipt, decision, and
artifact-set IDs.

The v1 portable protocol has one deterministic evaluate task. Workers validate
their installed engine and immutable transported inputs and emit normalized IR,
but cannot emit a release decision. The verifier remains the sole policy
engine; the assembler re-closes that decision over the supplied unit. This is
execution validation, not distributed scan/policy evaluation, arbitrary
sharding, or parallel speedup. See
[`docs/verification-reproducibility.md`](docs/verification-reproducibility.md).

---

<a id="migration-note-0-16-0b5"></a>

## Migration Note: 0.16.0b5

Runtime contract `15 → 16` and report schema `0.32 → 0.33` make policy
applicability evidence explicit. Semantic claims now carry a typed evidence
basis and stable claim ID. Policy predicates carry tri-state status, effective
confidence, contributing claim IDs, and evidence bases; policy findings carry
a stable `support_hash`.

Rule severity, `block: true`, manual risk escalation, and rule-declared
confidence cannot upgrade heuristic, unknown, partial, or conflicting
evidence. Such applicability creates a non-waivable evidence gap outside the
Finding model and routes to `insufficient_evidence`. `--no-heuristics`,
baselines, suppressions, acknowledgements, and severity overrides cannot hide
these gaps. Strict mode emits exit `20`; advisory mode retains exit `0`.

Packet advances `0.10 → 0.11`, verifier `0.3 → 0.4`, handoff v3 → v4,
policy pack `0.3 → 0.4`, capability standard `0.4 → 0.5`, capability lock
`0.5 → 0.6`, lock diff `0.6 → 0.7`, action snapshot `0.3 → 0.4`, baseline
`0.7 → 0.8`, and the generated downstream local contract to schema `5`.
The safety corpus, receipt-index, and qualification envelopes advance to v3.
All preceding schemas remain frozen references.

Finding fingerprints remain stable because typed support is outside legacy
`Finding.evidence`. Baseline v0.8 additionally binds supported findings to
their `support_hash`; an older baseline can still be read, but it cannot accept
a newly supported policy/control finding without being regenerated and
reviewed.

---

<a id="migration-note-0-16-0b4"></a>

## Migration Note: 0.16.0b4

Runtime contract `14 → 15` replaces the Codex-labelled multi-host check with
the host-neutral `shipgate.agent_boundary_result/v1` contract. The canonical
format is `--format agent-boundary-json`; `--agent` is caller identity only,
and every recognized changed host surface is evaluated regardless of that
value. `control.state` remains the operational signal and the minimum control
contract remains `14` because the discriminated `AgentControl` union is
unchanged.

The old `--format codex-boundary-json` spelling remains a deprecated `0.16.x`
compatibility projection of the same assessment and continues to emit the
frozen `shipgate.codex_boundary_result/v2` shape. It has no independent policy
or decision logic.

Host-grants inventory, baseline, and drift advance to `0.2`; the trigger
catalog advances to `0.2`; and the generated downstream local contract
advances to schema `4`. Report `0.32`, packet `0.10`, verifier `0.3`, handoff
v3, preflight `0.3`, capability standard `0.4`, and capability lock/diff
`0.5/0.6` are unchanged.

Zero-config means no `shipgate.yaml`, not proof of runtime-effective
authority. Repository scope is deterministic and default. The opt-in
`audit --host --scope local-static` reads only supported static local sources;
both scopes publish coverage and excluded sources. Session approvals,
invocation flags, UI state, remote managed settings, runtime enforcement,
agent execution, and tool behavior remain outside the contract.

---

<a id="migration-note-0-16-0b3"></a>

## Migration Note: 0.16.0b3

Runtime contract `13 → 14` replaces independently derived completion, stop,
verification, and human-review booleans with one discriminated `AgentControl`
state shared by check, preflight, verify, handoff, MCP, verify-run, and GitHub
Action projections. Current consumers require
`minimum_control_contract_version: "14"` and switch on `control.state`:
`complete`, `agent_action_required`, or `human_review_required`. (Contract v20
adds `review_publishable` and raises that floor — see the migration note at the
top of this file.)

Boundary result advances to `shipgate.codex_boundary_result/v2`, verifier to
`0.3`, agent handoff to `shipgate.agent_handoff/v3`, preflight to `0.3`,
verify-run to `shipgate.verify_run/v2`, and the generated downstream local
contract to schema `3`. The corresponding prior schemas remain frozen
references; current emitters have no legacy-output switch. Report `0.32`,
packet `0.10`, capability standard `0.4`, and capability lock/diff `0.5/0.6`
are unchanged.

The control variants enforce
`completion_allowed == (state == "complete")` and
`must_stop == (state == "human_review_required")`. Pending verification,
installation, safe repair, and input recovery are coding-agent work, never a
human stop. Conversation-level acknowledgement cannot clear control state;
only a new verifier artifact can do so. `release_decision.decision` remains the
only release verdict.

Verify v0.3 also separates `execution` (`not_run | succeeded | skipped |
failed`) from `applicability` (`not_evaluated | verified | not_applicable |
failed`). `can_merge_without_human` is true only for a verified `passed`
result or a completed deterministic `not_applicable` skip. The Action adds
`agent_control_state` and `agent_control_reason`; its legacy control booleans
remain exact derived mirrors for one compatibility cycle.

The public Python models `AgentController`, `VerifierNextAction`, and
`VerifierHumanReview` remain deprecated reader compatibility surfaces for
frozen verifier v0.1/v0.2 artifacts. The retired `build_agent_controller`
projector is removed; verifier v0.3 derives control only through
`derive_agent_control`.

---

<a id="migration-note-0-16-0b2"></a>

## Migration Note: 0.16.0b2

Runtime contract `12 → 13`, report `0.30 → 0.31`, packet `0.9 → 0.10`,
capability standard `0.3 → 0.4`, capability lock `0.4 → 0.5`, lock diff
`0.5 → 0.6`, and action snapshot `0.2 → 0.3` add an explicit static
agent-to-tool binding trust root. Extracted declarations now enter
`tool_catalog[]`; only tools proven reachable from the selected root enter
`tool_inventory[]`, actions, checks, capability facts, and locks.

`agent_bindings` declarations are exact, reviewed, closed-world evidence.
They cannot erase positive structural edges, and coding agents must not infer
or auto-apply them. Missing, partial, dynamic, ambiguous, or conflicting
binding evidence is unsuppressible and prevents `passed`. Pre-v0.31 binding
surfaces and pre-v0.5 capability locks must be regenerated before comparison.

---

<a id="migration-note-0-16-0b1"></a>

## Migration Note: 0.16.0b1

The tool-identity hardening follow-up advances runtime contract `11 → 12`,
report `0.29 → 0.30`, packet `0.8 → 0.9`, capability standard `0.2 → 0.3`,
capability lock `0.3 → 0.4`, lock diff `0.4 → 0.5`, policy pack `0.2 → 0.3`,
verifier `0.1 → 0.2`, action snapshot `0.1 → 0.2`, and agent handoff
`shipgate.agent_handoff/v1 → /v2`. The prior files remain frozen references.

Tools are no longer deduplicated by a name-derived ID. Each extracted
observation receives a source-scoped `obs_v1_…` identity and each canonical
capability a full `tool_v2_…` identity. Same-name tools from different
providers remain separate. Cross-source observations join only through an
exact, reviewed `tool_identity.bindings[]` declaration; invalid or conflicting
bindings join nothing and prevent `passed`.

One-to-one manifest selectors now accept `tool_id`, `provider`, `source_type`,
and `source_id`. A bare name that matches more than one provider applies
nowhere and becomes an unsuppressible identity evidence gap. A canonical tool
answers to the `tool_id` **and** the `source_type`/`source_id` of **any
observation bound into it**, not only its primary's — including the `tool_id`
each observation carried while it was still unbound: a reviewed binding (or a
`tool_inventories[].source_id` completion) must not silently rekey the identity
that an already-written row names, and Shipgate's own action scaffold emits
`tool`, `tool_id`, and `source_id` together. Both source qualifiers, when given
together, must be satisfied by the same observation, so a selector cannot pair
one member's type with another member's id. This rule holds for every selector
consumer — action rows, `policies.*` entries, and `checks.ignore` — not only
`tool_identity` resolution. Alias ids are for resolution only and never enter
the catalog partition. Finding
fingerprints are v2 and include the canonical `tool_id` instead of display
name. A v1 baseline fingerprint may match only when that legacy name resolves
to exactly one current tool identity; the old broad check-ID/name fallback is
removed. Reports before v0.30 and capability locks before v0.4 are not
identity-comparable and must be regenerated.

The built-in Conductor OSS workflow adapter additively advances the report
schema `0.31 → 0.32` by defining the required `frameworks.conductor` summary.
Report v0.31 remains frozen as the root-reachable binding contract. Manifest
schema `0.1`, packet schema `0.10`, and runtime contract `13` are unchanged.
`conductor` is now a reserved built-in `tool_sources[].type`; installations
with a third-party adapter using that source type must rename the plugin type.

`0.16.0b1` is a pre-release of the `0.16` contract line. It deliberately
tightens the meaning of `release_decision.decision: "passed"`: every in-scope
action must now have complete, conflict-free static surface, effect, and
authority evidence; all applicable controls must have been evaluated; and no
policy condition may require review. This is a conservative static-evidence
statement, not proof of runtime behavior or enforcement.

The runtime contract advances `10 → 11`. The versioned artifacts advance:

- report schema `0.28 → 0.29`;
- Release Evidence Packet schema `0.7 → 0.8`;
- capability standard `0.1 → 0.2`;
- capability lock schema `0.2 → 0.3`; and
- capability lock diff schema `0.3 → 0.4`.

Report v0.29 adds normalized semantic assessments to action and capability
facts plus
`release_decision.evidence_coverage.semantic_coverage`. Semantic gaps are
structured under `evidence_gaps[]`, but are intentionally not Findings: a
baseline, suppression, waiver, severity override, `--no-heuristics`, or human
acknowledgement cannot convert missing evidence into a pass. Any semantic gap
prevents `passed`; `unknown`, `inferred`, `protocol_default`, `partial`, or
`conflicting` required evidence routes to `insufficient_evidence`. Known
unscoped or ambient authority routes to `review_required`. In strict mode a
semantic `insufficient_evidence` decision exits `20`; advisory mode still exits
`0` while preserving the non-pass decision in JSON.

Every emitted v0.29 release decision makes the boundary machine-readable with
`static_analysis_only=true`, `runtime_behavior_verified=false`, and the
canonical `static_verdict_disclaimer`. Packet v0.8 §1 mirrors those values.
They are additive parser defaults for old artifacts, but current emitters must
always serialize them.

Manifest `action_surface.actions[]` now accepts reviewed `authority` evidence.
Agents Shipgate never auto-writes effect or authority declarations. See
[`docs/passed-verdict-contract.md`](docs/passed-verdict-contract.md) for the
complete pass contract and migration workflow.

Contract v11 also adds the stable `action_effect` and `action_authority` IDs to
`do_not_auto_assert[]`. Agents may route the corresponding evidence-gap next
actions, but these declarations are human assertions and must never be
invented or auto-filled.

The v0.29 report, v0.8 packet, v0.3 capability lock, and v0.4 lock-diff schemas
remain frozen references. Regenerate current reports and capability locks
before comparison; a pre-v0.30 artifact cannot establish current identity and semantic pass
eligibility.

---

<a id="migration-note-0-15-0"></a>

## Migration Note: 0.15.0

`0.15.0` continues the `0.x` contract line from `0.14.0` with **no breaking
changes**. `contract_version` advances `9 → 10`, purely additively:
`verify_required` joins `agent_result_control_fields` and appears on the Codex
boundary result (and the shared `agent-result-schema.v1.json`). Consumers
pinned to `contract_version >= 9` keep working unchanged; a consumer that wants
the machine-readable check→verify deferral reads the new field. The
`report.json` schema is unchanged at `0.28`. New checks
(`SHIP-CAP-CONFIG-BINDING-REMOVED`, `SHIP-CAP-CONFIG-BINDING-CHANGED`) are
additive and only fire on dynamic-toolkit config bindings.

---

<a id="migration-note-0-14-0"></a>

## Migration Note: 0.14.0

`0.14.0` continues the `0.x` contract line from `0.13.0`. It is a minor
release that nonetheless makes deliberate breaking changes to the
agent-controller surface — permitted under `0.x` semantics — cleaning up
overlapping contracts instead of preserving every earlier surface. (An
earlier draft of this work was briefly labelled `1.0.0-alpha`; that label was
withdrawn because the report schema is not yet frozen, and the same changes
ship here as `0.14.0`.)

Breaking changes from the `0.13.0` line:

- `agents-shipgate verify` no longer writes
  `agents-shipgate-reports/agent-result.json`. Agents should read
  `verification-receipt.json` first, then `agent-handoff.json`,
  `verifier.json`, `verify-run.json`, and finally
  `report.json.release_decision.decision`.
- `agents-shipgate verify --format agent` was removed. Use
  `--format json` to print the full `VerifierArtifact`.
- Non-preview `agents-shipgate verify --config <path>` now fails closed when
  `<path>` is missing. The old lenient path could trigger-skip and exit `0`;
  the new behavior exits `2`, emits `merge_verdict: "unknown"` and
  `applicability: "unknown"`, writes only lightweight verifier/controller
  artifacts, and does not write `report.json` or run a head scan.
  `agents-shipgate verify --preview` is unchanged and still treats a missing
  config as an onboarding/relevance condition with exit `0`.
- `shipgate check --format agent-json` was removed. Use
  `shipgate check --format codex-boundary-json`; the output
  `schema_version` is now `shipgate.codex_boundary_result/v1`.
- The GitHub Action input `fail_on_decisions` was renamed to
  `fail_on_merge_verdicts`, with values from
  `blocked | human_review_required | insufficient_evidence | unknown |
  mergeable`.
- GitHub Action outputs derived only from `agent-result.json`
  (`agent_result_json`, `agent_decision`, `risk_level`, `audit_id`,
  `required_reviewers`, and `policy_snapshot_sha256`) were removed.
  New outputs include `verify_run_json`, `run_id`,
  `agent_controller_must_stop`, `agent_controller_stop_reason`, and
  `agent_controller_completion_allowed`.
- The runtime contract payload is now `contract_version: "9"`.
  It adds `primary_commands{}` so agents can discover the three prominent
  flows (`shipgate check`, `agents-shipgate verify`, and
  `shipgate audit --host`) without treating supporting/adoption commands as
  first-look guidance. `verify_local` remains in `commands{}` as a supporting
  compatibility command, not a promoted primary flow.
  Report JSON now uses `report_schema_version: "0.28"`. v0.28 moves
  policy-pack owner/reviewer/approval routing metadata to
  `findings[].policy_routing` so `Finding.evidence` stays limited to
  deterministic rule-match evidence. v0.27 remains the frozen schema that
  added policy-pack distribution metadata
  (`loaded_policy_packs[].{source,sha256,sha256_status,owner}`) over
  v0.26 evidence-gap rows and `suggested-inventory.json`.
- `agents-shipgate verify` writes
  `agents-shipgate-reports/agent-handoff.json`
  (`shipgate.agent_handoff/v1`), a compact projection for coding agents. It
  mirrors `report.json.release_decision.decision`,
  `verifier.json.merge_verdict`, and
  `verifier.json.agent_controller.completion_allowed`; it never computes a
  second verdict.

`report.json.release_decision.decision` remains the only release gate.
`verifier.json.merge_verdict` is the controller projection for agents and
PR automation; it is not a second release gate.

## What WILL NOT change in the `1.x` line

### CLI command surface

These commands and flags are stable across the `1.x` line. A deliberate
breaking change bumps `contract_version`, carries a migration note in this
file, and follows the deprecation cycle above.

| Command | Stable flags |
|---|---|
| `agents-shipgate scan` | `-c`, `--config`, `--out`, `--format`, `--ci-mode`, `--fail-on`, `--baseline`, `--diff-from`, `--changed-files`, `--no-plugins`, `--strict-plugins`, `--no-heuristics`, `--verbose`, `--workspace`, `--packet`/`--no-packet`, `--packet-format` |
| `agents-shipgate verify` | `--workspace`, `--config`, `--base`, `--no-base`, `--head`, `--ci-mode`, `--fail-on`, `--baseline`, `--baseline-mode`, `--diff-from`, `--authorization`, `--out`, `--format` (`text`, `json`), `--policy-pack`, `--no-plugins`, `--strict-plugins`, `--no-heuristics`, `--suggest-patches`, `--verbose` |
| `agents-shipgate authorization request` | `--receipt`, `--artifacts-root`, `--remote`, `--destination-ref`, `--expected-lease-oid`, `--out`, `--json` — builds an unsigned challenge only; never signs or approves it |
| `agents-shipgate authorization execute` | `--workspace`, `--receipt`, `--artifacts-root`, `--json` — guarded consumer; must be launched by a host-protected broker/runtime |
| `agents-shipgate evidence-packet` | `--from`, `--out`, `--format`, `--json` |
| `agents-shipgate scenario suggest` | `--from`, `--out` |
| `shipgate check` | `--agent`, `--workspace`, `--format` (`codex-boundary-json`), `--diff`, `--base`, `--head`, `--config`, `--policy` |
| `agents-shipgate init` | `--workspace`, `--write`, `--json`, `--claude-code` (v0.13+) |
| `agents-shipgate doctor` | `-c`, `--config`, `--workspace`, `--json`, `--verbose` |
| `agents-shipgate contract` | `--json` |
| `agents-shipgate preflight` | `--workspace`, `--config`, `-c`, `--changed-files`, `--diff`, `--capability-request`, `--base-preflight`, `--json` |
| `agents-shipgate explain` | `<check_id>`, `--no-plugins`, `--json` |
| `agents-shipgate explain-finding` (v0.12+) | `<fingerprint>`, `--from`, `--no-plugins`, `--json` |
| `agents-shipgate findings` (v0.20+) | `--from` (default: `agents-shipgate-reports/report.json`), `--provenance-kind`, `--include-suppressed`, `--json` |
| `agents-shipgate trigger` (v0.11+) | `--workspace`, `--changed-files`, `--diff`, `--base`, `--head`, `--manifest-present`/`--no-manifest-present`, `--user-requested`, `--list-rules`, `--json` |
| `agents-shipgate bootstrap` | `--workspace`, `--confidence`, `--no-ci`, `--no-apply`, `--json` |
| `agents-shipgate capability export` | `--config`/`-c`, `--out`, `--report-out`, `--report-copy`/`--no-report-copy`, `--json`, `--no-plugins`, `--verbose` |
| `agents-shipgate capability diff` | `--base`, `--head`, `--out`, `--json` |
| `agents-shipgate list-checks` | `--json`, `--no-plugins` |
| `agents-shipgate baseline save` | `-c`, `--config`, `--out`, `--owner` (v0.13+), `--reason` (v0.13+), `--expires` (v0.13+), `--apply-to-existing` (v0.13+) |
| `agents-shipgate baseline verify` (v0.11+) | `--baseline`, `--audit-log`, `--strict`, `--json`, `--verbose` |
| `agents-shipgate baseline status` (v0.13+) | `--baseline`, `--as-of`, `--require-owner`, `--require-expiry`, `--max-age-days`, `--json` — advisory exit `0` with no gate flags; any gate flag exits `20` on violations |
| `agents-shipgate fixture list` | `--json` |
| `agents-shipgate fixture run` | `<name>`, `--ci-mode`, `--out` |
| `agents-shipgate fixture copy` | `<name>`, `--to` |
| `agents-shipgate fixture verify` | `<name>` |
| `agents-shipgate mcp-serve` | no stable flags |
| `agents-shipgate self-check` | `--json` |
| `agents-shipgate agent handoff` | `--from`, `--report`, `--verify-run`, `--out`, `--json` |

### Provisional CLI command surface

The org/fleet governance commands are preview surfaces in the current
`0.14.x` line. They are documented, deterministic, local-only, and included in
`agents-shipgate contract --json` / `.well-known/agents-shipgate.json` for
design-partner discovery, but their flags and schemas are not stable
command-contract commitments yet. They remain consumers of `verify` artifacts;
`report.json.release_decision.decision` is still the only release gate.

| Command | Preview flags |
|---|---|
| `agents-shipgate attest` | `--from`, `--out`, `--redact`/`--no-redact`, `--config`, `--org-id`, `--repo`, `--service`, `--tier`, `--pr-number`, `--workflow-run-id`, `--actor`, `--merge-sha`, `--verify-run`, `--event-time`, `--source-url`, `--branch`, `--base-sha`, `--head-sha`, `--ci-context`, `--json` |
| `agents-shipgate org status` | `--config`/`-c`, `--workspace`, `--baseline`, `--host-baseline`, `--as-of`, `--json` |
| `agents-shipgate org policy-packs` | `--config`/`-c`, `--workspace`, `--json` |
| `agents-shipgate org bundle` | `--config`/`-c`, `--workspace`, `--from`, `--out`, `--attestation`, `--registry`, `--as-of`, `--json` |
| `agents-shipgate registry ingest` | `--attestation`, `--registry`, `--repo`, `--json` |
| `agents-shipgate registry query` | `--registry`, `--repo`, `--org-id`, `--service`, `--tier`, `--actor`, `--verdict`, `--capability-id`, `--trust-root-touched`, `--policy-weakened`, `--human-ack-required`/`--human-ack-not-required`, `--human-ack-satisfied`/`--human-ack-not-satisfied`, `--json` |
| `agents-shipgate registry report` | `--registry`, `--bypass`, `--json`, `--fail-on-bypass` |
| `agents-shipgate registry summary` | `--registry`, `--json` |
| `agents-shipgate registry verify` | `--registry`, `--json`, `--fail-on-issue` |
| `shipgate audit --host` | `--workspace`, `--host`, `--json`, `--out`, `--save-baseline`, `--baseline`, `--drift`, `--fail-on-drift` |

`agents-shipgate feedback export` is introduced in v0.11 for design-partner
feedback loops. Its current flags are `--from`, `--redact`/`--no-redact`,
`--out`, and `--json`. Treat the command and `feedback_schema_version: "0.1"`
payload as provisional during the v0.11 design-partner cycle; the schema file is
published so consumers can validate it, and any incompatible change must bump
`feedback_schema_version`.

### Agent-Native Protocol

`shipgate check --format codex-boundary-json` emits
`shipgate.codex_boundary_result/v2`, the stable local Codex-boundary
control schema generated at
[`docs/codex-boundary-result-schema.v2.json`](docs/codex-boundary-result-schema.v2.json).
Agents act on `control.state` and `control.next_action`; `decision` is a
diagnostic boundary outcome. Human approval, policy
waivers, baselines, severity downgrades, suppressions, and trace evidence are
not agent-repairable authority gaps.

Full PR verification uses `agents-shipgate verify`. The single
agent-controller artifact is
`agents-shipgate-reports/verifier.json`; it leads with
`control.state`, `execution`, `applicability`, `merge_verdict`,
`can_merge_without_human`, and `fix_task`. `verify-run.json` records stable run
identity and input hashes for reproducibility. `report.json` remains the
release-gate artifact.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Pass — advisory mode or strict mode with no `fail_on` matches |
| `2` | Manifest config error (missing/typo/invalid) |
| `3` | Input parse error (malformed YAML/JSON, file too large, path traversal blocked) |
| `4` | Other Agents Shipgate error |
| `6` | Baseline integrity failure (v0.11+) — `agents-shipgate baseline verify --strict` detected `SHIP-BASELINE-INTEGRITY-MISMATCH`. Only the standalone `baseline verify` command emits this code; `scan` continues to use `20` for gate failure regardless of integrity-mode. |
| `20` | Gate failure. Strict-mode scan/verify: ≥ 1 unsuppressed finding hit `fail_on`, or ≥ 1 active unbaselined finding sets `blocks_release`. Also emitted by opt-in governance gate flags (v0.13+): `baseline status --require-owner`/`--require-expiry`/`--max-age-days` on violations, and `audit --host --drift --fail-on-drift` on host-grant drift. Without those flags the commands are advisory and exit `0`. |

### Runtime contract JSON

`agents-shipgate contract --json` emits the installed CLI's local contract.
Only the JSON form is stable; human-readable output is informational and may
change in any minor release. The command is local-only: it does not scan a
workspace, write files, call tools, perform network checks, or look up releases.

Stable JSON fields:

- `contract_version` — version of the contract-command payload shape.
- `minimum_control_contract_version` — minimum contract version whose
  `AgentControl` state is authoritative; currently `"21"`.
- `cli_version` — installed Agents Shipgate version.
- `report_schema_version` — current report schema version from
  `ReadinessReport`.
- `packet_schema_version` — current packet schema version from
  `EvidencePacket`.
- `capability_lock_schema_version` — current stable capability lock schema
  emitted by `agents-shipgate capability export`.
- `capability_lock_diff_schema_version` — current stable semantic diff schema
  emitted by `agents-shipgate capability diff`.
- `preflight_schema_version` — current proactive preflight routing schema.
- `capability_standard_version` — current capability standard version.
- `governance_benchmark_catalog_schema_version` — current benchmark catalog
  schema version.
- `governance_benchmark_result_schema_version` — current benchmark result
  schema version.
- `external_integration_surfaces[]` — stable non-gating integration and
  research surfaces exposed by the contract.
- `gating_signal` — always `release_decision.decision` in this contract.
- `agent_result_schema_version` — legacy local-agent protocol schema retained
  for compatibility with existing in-repo protocol and MCP surfaces. It is not
  emitted by `agents-shipgate verify`.
- `agent_result_schema_path` — checked-in JSON Schema path for that local
  legacy control object.
- `agent_result_control_fields[]` — ordered fields coding agents must switch on
  when reading the frozen legacy local-agent protocol.
- `agent_control_fields[]`, `agent_control_permissions[]`, and
  `agent_control_states[]` — current discriminated
  control contract vocabulary.
- `verifier_schema_version` — schema version for
  `agents-shipgate-reports/verifier.json`.
- `trigger_catalog_schema_version` — schema version of the published trigger
  catalog (`docs/triggers.json`) and, with it, of the run/skip verdict the
  evaluator emits.
- `verify_run_schema_version` — schema version for
  `agents-shipgate-reports/verify-run.json`.
- `human_authorization_request_schema_version`,
  `human_authorization_schema_version`,
  `human_authorization_evaluation_schema_version`, and
  `human_authorization_trust_policy_schema_version` — v1 versions for the
  unsigned challenge, externally signed grant, fail-closed verifier result,
  and external trust policy.
- `human_authorization_trust_policy_default_path` — the fixed POSIX
  OS-account-home trust-policy path; `HOME` and `XDG_CONFIG_HOME` do not
  redirect it.
- `human_authorization_schema_path` — checked-in schema family path for those
  four authorization objects.
- `agent_handoff_schema_version` — schema version for
  `agents-shipgate-reports/agent-handoff.json`.
- `agent_handoff_schema_path` — checked-in JSON Schema path for the handoff
  artifact.
- `agent_handoff_artifact` — default emitted handoff artifact path.
- `codex_boundary_result_schema_version` — schema version emitted by
  `shipgate check --format codex-boundary-json`.
- `current_control_schema_version` / `current_control_schema_path` /
  `current_control_artifact` — schema version, checked-in JSON Schema path, and
  default artifact path for `agents-shipgate-reports/current-control.json`, the
  one atomic entry point naming the control identity that is current.
- `agent_control_schema_version` / `agent_control_schema_path` /
  `agent_control_budget_bytes` — schema version, checked-in JSON Schema path,
  and published size budget in bytes for the compact `shipgate.agent_control/v1`
  control envelope. The budget is a target that representative output meets, not
  an enforced maximum: a long `required_reviewers` list or an unusually long
  exact command may exceed it, and neither is truncated to fit. The envelope is stdout only: it is emitted by `verify
  --format control`, `check --format agent-control-json`, and `agents-shipgate
  agent control`, and is never written to the reports directory. It is a
  projection of the authoritative control state and never gates independently
  of `release_decision.decision`.
- `agent_refresh_triggers[]` — the boundaries at which a consumer must re-read
  `current_control_artifact` before acting. A control state cached across any of
  them is not authority.
- `current_control_fallback_read_order[]` — documented read order for consumers
  built before the pointer existed. Its absence is evidence of an older
  producer, never permission to act on a cached decision.
- `agent_read_order[]` — cross-artifact machine read order for coding agents:
  `current-control.json` first (`current_control_id`, `lifecycle_state`,
  `control.state`), then `verification-receipt.json`,
  `agent-handoff.json.control.state`, then `verifier.json.control.state`,
  `verify-run.json`, then
  `report.json.release_decision.decision`.
- `agent_interface_operations[]` — stable verification-operation vocabulary
  for the handoff artifact only. Authorization `request` and `execute` are
  entries in `commands{}`; they are not handoff operation enum values.
- `exit_code_policy{}` — stable machine-readable exit-code meanings for
  agent-facing commands.
- `mcp_tools[]` — read-only MCP tool names exposed by `agents-shipgate
  mcp-serve`.
- `manual_review_signals[]` — stable report/packet fields an agent should read
  when surfacing human review work.
- `primary_commands{}` — the prominent flow map for local boundary checks, PR
  verification, and host-grant audits. Values promote `shipgate check`,
  `agents-shipgate verify`, and `shipgate audit --host`; local verify remains
  available under `commands{}`.
- `commands{}` — compatibility/supporting commands for local `shipgate check`
  control, preview, default local agent workflow install, local verify, PR
  verify, and contract introspection.
- `default_paths{}` — default manifest, report directory, and local contract
  paths used by generated downstream agent instructions.
- `artifacts{}` — stable report artifact paths an agent should inspect first.
- `verifier_read_order[]` — ordered field path list for `verifier.json`.
- `merge_verdicts[]` — stable verifier verdict vocabulary.
- `release_decisions[]` — stable release-gate decision vocabulary.
- `do_not_auto_assert[]` — authority/evidence categories an agent must not
  synthesize to make a gate pass.

Package versions and schema versions are intentionally separate contract
counters. `agents-shipgate` may bump `report_schema_version`,
`baseline_schema_version`, or `packet_schema_version` inside a package release
when the JSON contract changes. Consumers that need a specific report or packet
shape should check `agents-shipgate contract --json` instead of inferring schema
support from the package version alone.

Signal paths use dotted notation; `[]` denotes an array field.

### Preflight JSON fields (stable)

`agents-shipgate preflight --workspace . --plan - --json` is the primary
proactive, static-only planning surface for coding agents. Legacy shorthands
such as `--changed-files`, `--diff`, and `--capability-request` remain
compatible. Preflight does not inspect runtime tool calls, start an MCP server,
or claim merge safety. `release_decision.decision` remains the only release gate.

The stable top-level fields in the v0.3 preflight result are:

- `preflight_schema_version` — currently `"0.3"`.
- `control` — the shared `AgentControl` operational projection.
- `workspace` and `config` — resolved workspace and manifest path context.
- `protected_surfaces[]` — canonical trust-root surfaces with `kind`, `pattern`,
  `scope_type`, `present`, and `present_paths`.
- `forbidden_file_edits[]` — standing whole-file deny-list for autonomous
  agents; this is not a general allow-list.
- `forbidden_actions[]` — shortcuts agents must not take to clear a gate.
- `required_evidence[]` — deterministic evidence requirements for a
  `--capability-request` high-risk action proposal.
- `changed_files[]` and `protected_surface_touches[]` — optional path review
  projection from `--changed-files` and/or `--diff`. A touch's existing
  `requires_human_review` flag may be false only for a resolvable, append-only
  proposal that adds valid built-in `tool_sources` coverage without changing
  any other manifest value or existing source row.
- `requires_human_review` — true when a requested protected touch requires
  pre-edit human routing or the plan lacks high/critical required evidence.
- `policy_snapshot_hash`, `trust_root_graph_hash`, and `trust_root_graph` —
  deterministic hashes/projection for policy and trust-root drift review.
- `policy_drift` and `trust_root_graph_diff` — populated when
  `--base-preflight` is supplied.
- `first_next_action` — compatibility mirror of `control.next_action`.
- `notes[]` — non-gating diagnostics such as missing manifest context.
- `signals[]` — deterministic rows with `id`, `kind`, `severity`, `actor`,
  `subject`, `path`, `reason`, `recommendation`, and `related_command`.
- `requires_verify`, `verification_command`, and `allowed_next_commands[]` —
  verifier routing hints only; they are not merge verdicts.
- `plan_summary` — deterministic counts for the supplied plan and resulting
  signals.
- `host_grant_drift` — optional host-grant drift payload when a host baseline
  is present or explicitly supplied.

Preflight distinguishes proposal authorship from approval. A coding agent may
author the exact coverage-increasing manifest proposal described above and is
then routed to `verify`; path-only plans, custom adapters, `trust`/`optional`
fields, source edits/removals/reordering, non-contained or symlinked paths, and
mixed manifest changes remain human-routed. The exception never asserts
action effect, authority, binding, policy, or approval evidence. The resulting
`shipgate.yaml` diff is still a protected-surface change, and committed
verification continues to require human review before merge or execution.

### JSON report fields (stable)

In `agents-shipgate-reports/report.json`, the following are guaranteed:

- `report_schema_version` — frozen at `1.0`. Bumps minor on additive changes; a breaking change needs a new major, which restarts the freeze. The `0.43 → 1.0` promotion was itself additive-empty: the two schemas are byte-identical apart from `$id`, `title` and this constant. See [`docs/report-1-0-contract.md`](docs/report-1-0-contract.md)
- `release_decision.{decision, reason, blockers, review_items, evidence_coverage, baseline_delta, fail_policy}` (v0.8+)
- `release_decision.evidence_coverage.semantic_coverage.{total_actions, pass_eligible_actions, gap_count, review_concern_count, reason_counts}` (v0.29+) — zero-tolerance semantic pass coverage. It is derived from normalized action assessments and contributes directly to the release decision; it is not suppressible or baseline-able.
- `release_decision.evidence_coverage.semantic_coverage.declaration_questions.{total, answered, open, open_by_dimension, open_questions[]}` (v0.37+) — the same action surface counted as a questionnaire. A *question* is one `(action, dimension)` a reviewed `action_surface.actions` row has to answer; only `effect` and `authority` are counted, and an action whose effect the scan established by itself never enters `total`. `answered` counts dimensions that gap when the same action is re-resolved without its declaration, so it can neither credit a declaration nobody needed nor be raised by restating what the scan already knew. `total == answered + open`, `open_by_dimension` sums to `open`, and `open_questions[]` is the answer order (highest-acting action first, `effect` before `authority`), joining to `evidence_gaps[]` on `(subject_kind, subject_id)`. Purely a projection: no branch of the release decision reads it, and its ordering cannot change a verdict. v0.38 restates the unit as *one blank a reviewer fills*: `open_questions[].answer_path` names the manifest block that answers the question, and actions answered by the same block are one question. `open_questions[].subject_kind` (`action` | `tool_source`) says which id space `subject_id` is in — an authority question every action of a source shares is answered in that source's `tool_sources[].authority` block, so it is asked, counted, and rendered once. v0.41 adds `open_questions[].authorable_by`, the conjunction of the same tag on every `evidence_gaps[]` row the question folds: a block whose effect the scan can propose but whose authority it cannot is still a human's edit.
- `release_decision.evidence_coverage.policy_gap_count` and `policy_evidence_gaps[]` (v0.33+) — zero-tolerance policy-applicability gaps for heuristic-only, mixed, unknown, or conflicting predicates. They remain outside Findings and cannot be suppressed, baselined, acknowledged, severity-overridden, or hidden by `--no-heuristics`.
- `release_decision.{static_analysis_only,runtime_behavior_verified,static_verdict_disclaimer}` (v0.29+) — explicit machine boundary for every verdict. Current emitted values are `true`, `false`, and the canonical disclaimer that the static scan did not execute the agent or prove runtime behavior, tool routing, credential enforcement, or safety.
- `release_decision.evidence_coverage.evidence_gaps[]` (v0.26+; semantic gap kinds added v0.29) — deterministic, human-routed remediation rows. v0.29 adds `incomplete_surface`, `missing_effect_evidence`, `inferred_effect_only`, `conflicting_effect_evidence`, `missing_authority_evidence`, `partial_authority_evidence`, `conflicting_authority_evidence`, and `invalid_semantic_annotation`, plus next-action kinds `declare_action_effect`, `declare_action_authority`, `provide_complete_inventory`, and `resolve_semantic_conflict`. Semantic declaration placeholders always carry `suggested_patch_kind="manual"`, `auto_apply=false`, and `requires_human_review=true`; they never enter `apply-patches`. v0.36 adds `declaration_below_inferred_evidence` — a declaration weaker than the (non-policy-eligible) evidence inferred for the same action. It routes to `resolve_semantic_conflict` and is closed either by raising the declared effect or by adding `action_surface.actions[].override` (`evidence` + `reason`), which keeps the action pass-eligible and records one acknowledged review concern. Each acknowledgement is also emitted as a row in `release_decision.evidence_coverage.semantic_coverage.acknowledged_overrides[]` (v0.36+) naming the action, both readings, the hint source, any source evidence that agrees, and the reviewer's evidence and reason — the packet's §1 and the PR comment render it, because a count is not a review surface. The acknowledgement is consumed by policy applicability as well, so applying it reaches the review route rather than trading one gap for another. v0.37 adds `next_action.observed_readings[]` (`{effect, sources[], observed}`) on effect rows — the distinct readings this scan's non-declaration evidence supports — and, where those readings support one conservative answer, `next_action.declaration_template` carries that answer **pre-filled** instead of a `<REVIEW_REQUIRED>` blank. A pre-filled value is a proposal, not an assertion: it is drawn from the closed `ActionEffect` vocabulary and never from source content, it is never weaker than any reading, and it is offered only where something was observed — a protocol default, or a heuristic reading of `read`, keeps the blank. `suggested_patch_kind`, `auto_apply`, and `requires_human_review` are unchanged, and nothing applies a template: only a reviewed edit to the manifest makes any of it operative. v0.37 also re-routes `partial_authority_evidence`: it is raised when the *source's* authority evidence is ambiguous or incomplete, and the resolver preserves it whatever the manifest declares ("reviewed authority cannot replace ambiguous or incomplete source authority alternatives"). Its `next_action.kind` is therefore `provide_source` with no declaration template, rather than a `declare_action_authority` block that could not close the row it was printed on. It is excluded from `declaration_questions` for the same reason. v0.38 adds `subject_kind` (`action` | `tool_source`, default `action`) to every row: a `missing_authority_evidence` row for actions whose source is configured in `tool_sources[]` is emitted **once for the source**, with `subject_kind: tool_source`, a `why` naming how many actions wait on it, `next_action.path` pointing at `shipgate.yaml#tool_sources[id=…].authority`, and a `declaration_template` that is a `tool_sources` entry (its `scopes` live inside the `authority` mapping, because a source has no sibling permission list). `next_action.kind` stays `declare_action_authority` — it names the claim being asked for, which no agent may assert on a human's behalf, and the contract's `do_not_auto_assert[]` is keyed on exactly that. Conflicts stay per action: `conflicting_authority_evidence` is raised about one action whose published evidence disagrees with the reviewed block, and only a reader of that action can say which of the two is wrong. **v0.41 adds `next_action.authorable_by`** (`coding_agent` | `human`, default `human`) — who may write the *first draft* of the answer. It is `coding_agent` only where the scan filled every blank in `declaration_template` **and** the gap kind is not one that asks a person to look again (`declaration_drift` restates a confirmed answer beside a moved pin and stays `human`, because an agent re-stamping the pin would close the request the row exists for). Such a row also carries `suggested_patch_kind: "declare_action"` and a `next_action.patch` that is *exactly* the template, split into the keys naming the action and the fields written — the schema rejects any other pairing, and `target_path` is relative to `manifest_dir` so the row reads the same in the packet, the SARIF file, and a cached base scan. `auto_apply` stays `false` and `requires_human_review` stays `true` on every row: the manifest is the trust root, so a declaration reaches the gate only through a human merge whoever typed it. The patch is outside `apply-patches --kinds` by default and is applied by the `confirm_declarations` control route that proposes it.
- `release_decision.fail_policy.{ci_mode, fail_on, new_findings_only, would_fail_ci, exit_code}`
- `release_decision.blockers[].{id, fingerprint, check_id, severity, title, baseline_status, blocks_release, capability_refs, capability_trace_refs}` and `release_decision.review_items[].{id, fingerprint, check_id, severity, title, baseline_status, blocks_release, capability_refs, capability_trace_refs}` (reference-only — both arrays share the same item shape; full Finding payload is in `findings[]`; `capability_refs` is v0.24+ audit metadata and is empty when no capability-policy subject matched; `capability_trace_refs` is v0.25+ local trace-evidence audit metadata and is empty when no local trace row matched)
- `capability_facts[].{id, tool_name, source_type, source_ref, capability, risk_tags, auth_scopes, owner, included_reason, control_status, related_findings}` (v0.9+)
- `capability_facts[].semantic_assessment` and `action_surface_facts.actions[].semantic_assessment` (v0.29+) — the normalized static claims, issues, conservative effect, authority mode, and pass-eligibility bit consumed by the gate. The action effect, capability effect, and assessment conservative effect must agree.
- `declared_intentions[].{id, kind, text, source, intent_tags}` (v0.9+)
- `misalignments[].{id, kind, severity, tool_name, capability_refs, intention_refs, finding_refs, policy_requirement, gap, release_implication}` (v0.9+)
- `release_consequence.{decision, summary, blocker_misalignment_count, review_misalignment_count, fail_policy}` (v0.9+)
- `suggested_scenarios[].{id, scenario_type, title, given, expected_control, source_misalignments, source_findings}` (v0.9+)
- `tool_surface_facts.{tools, scopes, controls, policies}` (v0.10+) — deterministic current facts used for static tool-surface comparison
- `tool_surface_diff.{enabled, base, summary, tools, high_risk_effects, scopes, controls, metadata_changes, policy_drift, finding_deltas, notes}` (v0.10+) — lower-level explanatory diff data only; it never changes `release_decision.decision` or exit behavior by itself
- `summary.{critical_count, high_count, medium_count, low_count, info_count, suppressed_count, status, human_review_recommended}`
- `findings[].{id, fingerprint, check_id, severity, category, title, recommendation, suppressed}`
- `findings[].tool_name` (string or null)
- `findings[].source.{type, ref, location}` (when available)
- `findings[].source.{path, start_line, end_line, start_column, pointer}` (v0.11+) — minimal source provenance for the common tool-source loaders (OpenAPI, MCP, OpenAI tool artifacts, Anthropic tool artifacts). Optional and additive: keys are emitted only when the loader populates them. Reviewers can use `path` + `start_line` to jump to evidence; `pointer` is an RFC 6901 JSON pointer into the source file. JSON inputs do not carry line numbers in v0.11.
- `findings[].agent_action` (v0.12+) — deterministic projection of `patches`, `autofix_safe`, and `requires_human_review`. Enum: `auto_apply | propose_patch_for_review | escalate_to_human | suppress_with_reason | informational`. The first four cover the actionable cases; `informational` covers suppressed findings or non-actionable advisories. `suppress_with_reason` is reserved for future check classes that explicitly mark themselves as suppressible — the v0.12 deterministic projection does not emit it. New consumers should read `agent_action` first and treat the underlying flags as advisory.
- `agent_summary.{verdict, headline, blocker_count, review_item_count, auto_appliable_patches, needs_human_review, first_recommended_action}` (v0.12+) — top-level deterministic projection of `release_decision` + per-finding `agent_action`. Lets a coding agent read one block instead of traversing arrays. `first_recommended_action` is `{kind: "command" | "info", command: string | null, why: string}`; the `command` form carries an actual CLI invocation, the `info` form is a "surface this to the user" hint. Same inputs always produce the same output; this block cannot disagree with the underlying `release_decision` and `findings[].agent_action`. Two narrower evidence-gap guarantees (v0.16+), stated at the scope they actually hold: (a) when `release_decision.decision` is `insufficient_evidence` **and** at least one `evidence_coverage.evidence_gaps[]` row is *addressable*, `release_decision.reason`, the short-form `Improve evidence:` line, and `first_recommended_action.why` name the same selected row and the same target or command; (b) on **every** verdict, `first_recommended_action.why` never says "no machine-applicable fix is available" while any gap is addressable. A row is **addressable** when `next_action.path` contains at least one character that actually renders (whitespace, controls, and Unicode Default_Ignorable code points render as nothing) **or** `next_action.command` is publishable exactly as authored — a command carrying any control, bidi, or invisible code point, or any whitespace other than U+0020, is suppressed rather than rewritten. Rendering escapes line-breaking and bidi characters as `<U+XXXX>` and never deletes anything; paths and commands are additionally never whitespace-folded. Outside (a) the three surfaces intentionally differ — on `insufficient_evidence` with no addressable gap the reason keeps threshold wording, and under `review_required` the reason stays severity-driven — so consumers must not infer alignment there; see [`docs/agent-contract-current.md`](docs/agent-contract-current.md#read-these-first-for-release-gating). `evidence_coverage.source_warning_count` stays the raw loader-warning count that feeds the `insufficient_evidence` threshold; rendered surfaces (`report.md`, `packet.md`/`packet.html`, CLI `--verbose`, `verify`'s fix-task remedies) group warnings of a recognized mechanism for readability without changing it.
- `codex_plugin_surface.{plugins, marketplaces, skills, apps, mcp_server_stubs, hook_stubs, mcp_inventory_files, component_path_issues, warnings}` (v0.13+) — static Codex plugin package and marketplace facts. Only explicit MCP inventory tools enter `tool_inventory[]`; apps, hooks, skills, and MCP server declarations stay in this surface block.
- `findings[].provenance_kind` (v0.15+) — records *how a finding was produced*; independent of `confidence`, which records how *sure* we are. It is a reviewer triage/filter signal only: it never changes `release_decision`, severity, fingerprints, baselines, or CI exit behavior. Use `agents-shipgate findings --from agents-shipgate-reports/report.json --provenance-kind keyword_heuristic,regex_heuristic,runtime_trace --json` to filter active findings by provenance class. Enum: `static_declaration | ast_extraction | keyword_heuristic | regex_heuristic | policy_pack | runtime_trace`. `static_declaration` covers manifest, MCP, OpenAPI schema facts, and declarative framework inputs like ADK YAML agent configs or LangChain/CrewAI inventory JSON files — high-trust structural data. `ast_extraction` covers findings against Tools parsed from user Python source by a framework extractor (LangChain function/structured tools, CrewAI function/class tools, ADK Python toolsets); these are subject to extraction error and agents that distrust AST quality can filter them as a class. Framework checks that fire against both AST-extracted and declaratively loaded tools (ADK's per-tool checks) pick the label per tool from `tool.source_type`. `keyword_heuristic` covers token-list matches (broad scope, read-only prompts, free-text parameter names); `regex_heuristic` covers regex matches (secrets, prompt injection); `policy_pack` covers findings emitted by externally loaded policy packs; `runtime_trace` covers findings derived from declared local trace artifacts. Built-in checks set the value via the required kwarg on the `tool_finding`/`agent_finding` helpers; third-party plugin checks that construct `Finding(...)` directly and omit the field are coerced to `static_declaration` by `annotate_remediation` so the wire schema stays satisfied. Required + non-nullable on the wire; the field is Python-Optional only so older v0.12/v0.13 reports loaded by `explain-finding` and minimal synthetic test fixtures keep working.
- `findings[].blocks_release` (v0.16+) — explicit release-policy blocking bit. Starting in v0.33, a policy may set it only when `finding.support.blocking_eligible=true`; rule metadata cannot upgrade underlying evidence.
- `findings[].support` (v0.33+) — typed predicate status, effective confidence, policy/block eligibility, stable claim IDs, evidence bases, predicate rows, and `support_hash`. Finding fingerprints remain unchanged; baseline v0.8 separately requires an equal support hash for supported findings.
- `findings[].capability_refs` and optional `findings[].capability_policy_evidence` (v0.24+) — capability-native policy evidence for built-in policy checks and policy packs. `capability_refs` is required + always present (empty when no capability-policy subject matched). `capability_policy_evidence` is nullable and carries the matched capability identity, effect, authority, controls, hashes, matched predicates, and source provenance when present. These fields are explanatory only: they are not finding fingerprint inputs, do not affect baselines, and do not introduce a second gate.
- `findings[].policy_routing` (v0.28+) — optional policy-pack owner, reviewers, and approval-routing metadata. It is non-enforcing reviewer/audit metadata: it is not part of `Finding.evidence`, does not affect fingerprints, suppressions, baselines, `blocks_release`, or `release_decision`. Policy-pack `match` predicates and `block: true` remain the only policy-pack inputs that affect findings and release gating.
- `findings[].capability_trace_refs` and top-level `capability_runtime_evidence` (v0.25+) — opt-in local trace/provenance evidence linked to `CapabilityFactV1`. Trace refs are required + always present on findings (empty when no local trace row matched). The top-level block carries deterministic summary counts, matched/unmatched trace rows, source provenance, and notes. It is explanatory only: it is not a finding fingerprint input, does not affect baselines or run IDs, does not change capability lock export/diff schemas, and does not introduce a second gate.
- `action_surface_facts.actions[]` (v0.16+) — deterministic current action snapshot: action id, operation, effect, normalized risk tags, scopes, approval policy, safeguards, evidence, input fields, and stable hashes.
- `action_surface_diff.{enabled, base, summary, added, removed, modified, notes}` (v0.16+) — reviewer-facing delta for what the agent can do vs. a prior report or v0.4 baseline. Policy findings derived from this diff can set `findings[].blocks_release=true` and affect `release_decision.decision` and strict-mode exit behavior.
- `release_decision.contribution_rules[].{finding_id, fingerprint, check_id, category, rule, rationale}` (v0.17+) — deterministic per-finding audit of how each finding contributed to the release decision. Required + always present. Exactly one row per `report.findings` entry, including suppressed findings. v0.33 adds `rule="unsupported_evidence"`, always with `category="excluded"`, for typed support that is not policy-eligible.
- `baseline.{matched_count, new_count, resolved_count, path}` (when `--baseline` is used)
- `tool_inventory[].{name, source_type, source_ref, risk_tags, auth_scopes, owner, confidence}`
- `loaded_policy_packs[].{id, name, version, path, source, sha256, sha256_status, owner, rule_count}` (v0.27+) — deterministic policy-pack distribution and ownership metadata for organization audit. `sha256_status` is `"verified"` when a manifest pin matched and `"unpinned"` otherwise. Hash mismatch still fails closed during pack loading; this metadata never introduces a second release verdict.
- `loaded_plugins[].{name, value, distribution, version, check_id}`
- `loaded_plugins[].{validation_status, validation_errors, runtime_errors}` (v0.17+ / M5; `dynamic_default_not_supported` added v0.18) — plugin validation provenance, required + present on every entry. `validation_status` is one of `valid | load_failed | bad_signature | bad_metadata | dynamic_default_not_supported | id_collision | bad_floor`; the two error lists are always present and empty for clean plugins. Invalid plugins still appear in this array (with `check_id: null` for entries that failed before metadata parsing), so reviewers can see what was skipped without reading scanner logs. Plugin findings whose `check_id` does not match the declared metadata are dropped at runtime and recorded under `runtime_errors`. `dynamic_default_not_supported` (v0.18+) rejects plugins declaring `AGENTS_SHIPGATE_METADATA.dynamic_default=True` — plugins have no path to wire into `core/dynamic_defaults.py`'s aggregator, so a swing check would never receive a manifest-effective default and would be silently bypassable.
- `policy_audit.severity_overrides_applied[].{check_id, default_severity, applied_severity, manifest_path, reason, tier_crossed, direction, expires}` (v0.17+ / M1) — top-of-report audit envelope for severity overrides applied during scan. Always present on emitted scans (empty when no overrides applied); required + non-nullable on the wire. `direction` is one of `downgrade | upgrade | same`. `tier_crossed=true` indicates the override crossed a severity tier boundary (critical / high / medium-low); tier-crossing downgrades require a matching `checks.acknowledge_overrides` entry, which is reflected in `reason`. `expires` is an ISO-8601 date carried from the matching acknowledgement (or the rich-form override entry); on/past this date the manifest fails to load with exit 2. Verify computes the hard-expiry date as the later of its wall-clock date and the content-bound `evaluation_date`; commit timestamps can never extend trust.
- `privacy_audit.{enabled, rules_version, sensitive_field_inventory_version, redacted_occurrence_count, redacted_paths, output_surfaces, notes}` (v0.18+) — top-level audit envelope proving the default-on privacy layer ran before public artifacts were emitted. `redacted_paths[]` contains `{path, count, kinds}` aggregate rows only; it never includes raw values or raw-value hashes. Redaction is best-effort pattern/key based and does not claim complete secret-scanner coverage.
- `reviewer_summary.{verdict, headline, tool_surface_changes, capability_misalignments, action_surface_changes, evidence_matrix_gaps, severity_overrides_applied, severity_overrides_tier_crossed, privacy_redactions, baseline_integrity_issues, first_recommended_surface}` (v0.20+) — top-level deterministic projection of the reviewer lens surfaces and audit envelopes; the reviewer-side parallel to `agent_summary`. Required + always present on emitted scans (mirroring the `agent_summary` contract). `verdict` mirrors `release_decision.decision` and is added/removed in lockstep with `AgentSummary.verdict` and `ReleaseDecisionStatus`. `first_recommended_surface` is `{kind, name, path, why}` where `kind` ∈ `{release_decision, lens, audit, evidence_matrix}` and `name` ∈ `{tool_surface_diff, capability_intent_diff, action_surface_diff, evidence_matrix, policy_audit, privacy_audit, baseline_integrity, release_decision}`; the pointer is `null` only when verdict is `passed` AND every count above is zero. The priority order encoded by `first_recommended_surface` is documented in [`docs/agent-contract-current.md`](docs/agent-contract-current.md). Same inputs always produce the same output; this block cannot disagree with the underlying lens/audit data.
- `heuristics_filter.{enabled, excluded_provenance_kinds, filtered_finding_count, filtered_by_kind}` (v0.21+) — top-level audit envelope describing the `--no-heuristics` CLI filter pass. Required + always present on emitted scans regardless of whether the flag was set (envelope shape is stable). When `enabled` is `False` the count fields are zero and no findings have been mutated by the filter. When `enabled` is `True`, every finding whose `provenance_kind` is in `excluded_provenance_kinds` has been marked `suppressed=True` with `suppression_reason="filtered by --no-heuristics"` BEFORE the release decision is built — those findings remain in `findings[]` for transparency but no longer gate release. `excluded_provenance_kinds` is the stable list `["keyword_heuristic", "regex_heuristic"]` (the only two `ProvenanceKind` values describing token/regex matches; `static_declaration`, `ast_extraction`, `policy_pack`, and `runtime_trace` are never filtered). The filter never un-suppresses a finding; manifest-driven suppression reasons are preserved verbatim when they overlap with the filter (the envelope still counts the overlap so reviewers see the filter's effective scope).
- `verifier_summary.{verdict, by_severity, by_reason_code, capability_delta_summary, protected_surface_touched, policy_weakened, human_ack_required, human_ack_satisfied, top_reason_codes}` (v0.22+) — top-level **composition** for the AI-coding-workflow verifier; the controller-facing one-fetch surface. Required + always present on emitted scans. Derives no independent verdict: `verdict` mirrors `release_decision.decision` and moves in lockstep with `AgentSummary.verdict` / `ReviewerSummary.verdict` / `ReleaseDecisionStatus`. `by_severity` / `by_reason_code` are active-finding histograms (the complete per-code map); `capability_delta_summary` (`{added, removed, broadened, narrowed}`) equals the `capability_change` member-list lengths by construction; `top_reason_codes[]` is the ranked top-five highlight (`{reason_code, count}`, ranked severity desc → count desc → code asc — the full set stays in `by_reason_code`). This block cannot introduce a finding-independent blocker.
- `capability_change.{enabled, added, removed, broadened, narrowed}` (v0.22+; semantic metadata v0.23+) — diff-derived capability delta projected over `action_surface_diff` / `tool_surface_diff`. Required + always present (`enabled: false` with empty lists when no base diff is available). Each member is `{id, direction, subject_kind, tool, action, scope, before_scope, after_scope, before_capability_id, after_capability_id, changed_hashes, semantic_direction, semantic_changes, risk_tags, release_impact, provenance_kind, confidence, rationale, related_finding_ids}`; member lists are sorted by `(subject_kind, tool, action, scope, id)`. A reviewer-facing projection — it never gates on its own.
- `protected_surface_changes[]` (v0.22+) — list of touched release trust roots, each `{path, kind, glob, related_finding_ids}`, sorted by `(kind, path)`. Derived from active `SHIP-VERIFY-*` findings, so every `related_finding_ids` entry resolves to a real `findings[]` id and the rollup cannot disagree with the gate. Always present (empty `[]` on a plain scan or when no trust root is touched).
- `effective_policy.{ci_mode, fail_on, suppressed_check_ids, waiver_scopes, severity_overrides, baseline_integrity_mode, baseline_fingerprints, ci_gate_present}` (v0.22+) — normalized (not text-diff) snapshot of the release-policy surface for base-vs-head weakening comparison. Required + always present. Every list/dict is emitted sorted (`fail_on` by severity tier rank) for byte-stable output; derived from the manifest **as declared on disk** plus accepted-debt fingerprints. `--ci-mode` / `--fail-on` override the run — top-level `ci_mode` / `fail_on` and the exit code — and are deliberately excluded here, so this block is a function of the tree alone and a base-vs-head weakening comparison never reads one side's invocation flags as the other side's policy.
- `human_ack.{required, satisfied, acks, outstanding}` (v0.22+) — declared human-acknowledgement state. Required + always present (default `required=false`, `satisfied=true`, empty lists). Within the static boundary, acknowledgement is declared evidence only — never inferred. A trust-root weakening (`SHIP-VERIFY-POLICY-WEAKENED`, `-POLICY-BASE-ABSENT`, `-CI-GATE-REMOVED`, `-BASELINE-OR-WAIVER-EXPANDED`) makes a surface `required`; `satisfied` only when a matching `human_ack` entry exists in `shipgate.yaml`. `acks[]` are `{owner, reason, affected_surface, expires, source}`; `outstanding[]` lists required-but-unacknowledged surfaces. The ack section lives in `shipgate.yaml` (a trust root) so adding one trips `SHIP-VERIFY-TRUST-ROOT-TOUCHED`.

During `0.x`, secondary projections are supporting/provisional even when their
field shapes are documented for additive compatibility. CI gates on
`report.json.release_decision.decision`; PR controllers use
`verifier.json.control.state`, `execution`, `applicability`, and
`merge_verdict`.
`reviewer_summary`, `verifier_summary`, `capability_review`, runtime
trace/evidence fields, Release Evidence Packets, and non-gating capability diff
projections are explanatory surfaces, not independent policy engines.

### Privacy and redaction

Reports, packets, SARIF, Markdown, GitHub step summaries, `explain-finding`
payloads, and JSON logs are redacted by default. The sanitizer runs locally and
does not upload artifacts. Redaction uses the shared rules in
`agents_shipgate.core.privacy` and the report-field inventory in
[`docs/report-sensitive-fields.json`](docs/report-sensitive-fields.json).
False positives are allowed in favor of privacy; local routing metadata such as
source paths, JSON pointers, and scopes remains structurally present with only
secret-like substrings replaced.

Changed-file paths are published verbatim, by decision (#742). `check` and
`verify` name each file the change touches exactly as Git names it: in
`changed_files[]`, `trigger.changed_files[]`, `affected_files[].path`,
`violations[].path`, `violated_rules[].path` and `host_coverage[].paths[]` of
`check --format agent-boundary-json` and its text output, and in
`changed_files[]` and `trigger.changed_files[]` of `verifier.json` and
`agent-handoff.json`. These paths are locators: a reviewer, an agent or CI
opens, quotes and reviews the named file, and the pull request's own diff
already shows the same names. A credential-shaped file name is therefore
disclosed by the change itself, and redacting it here would break the locator
without hiding it. The host inventory is the path surface that redacts: `audit
--host`, its saved baseline, drift, `diff` and `verify`'s `host_comparison`
redact each path component with the shared sanitizers and add a short digest of
the exact source (#590). Do not commit a file whose name carries a secret.

v0.18 changes public fingerprints for findings whose identity evidence contains
a recognized secret pattern because the public `findings[].fingerprint` is now
computed from redacted evidence. During `--baseline` scans, Shipgate also checks
the pre-v0.18 raw fingerprint in memory so existing baselines continue matching
without emitting raw hashes. After reviewing the v0.18 report, re-run
`agents-shipgate baseline save` to migrate the baseline to redacted public
fingerprints and remove the compatibility dependency.

### Severity-override floor

`checks.severity_overrides` continues to accept the legacy scalar form
(`SHIP-XYZ: medium`) and additionally accepts a rich form
(`SHIP-XYZ: { severity, reason, expires }`). Reviewers should prefer the
rich form for any tier-crossing or release-critical override.

Some built-in checks declare a per-check **hard floor**
(`CheckMetadata.floor_severity`). When set, a manifest override that
resolves to a weaker severity than the floor is rejected as a config
error (exit 2). The floor is hard — `acknowledge_overrides` does NOT
bypass it. Use `agents-shipgate list-checks --json` to inspect each
check's floor.

`checks.acknowledge_overrides[]` (v0.17+) — required for severity
overrides whose application crosses a severity tier boundary as a
downgrade. Stable shape: `{check_id, reason, expires?}`. Within-tier
downgrades (e.g., medium → low) and any upgrade never require ack.
Tiers (stable within `0.x`): `critical / high / medium-low`. Expired
ack entries are a manifest config error.

**Dynamic-severity check classes** (v0.17+; formalized v0.18). Catalog
checks whose emitted finding severity depends on user-declared
manifest values declare `CheckMetadata.dynamic_default=True`. Today
the only such built-in is `SHIP-ACTION-POLICY-VIOLATION` (emits at
`action_surface.policies[].severity`). Policy-pack rule IDs flow
through the same `extra_known_check_defaults` mechanism but live
outside the catalog. The severity-override resolver uses
`max(catalog default, manifest-effective default)` as the
tier-crossing comparison base, so a `severity: critical` action
policy with override `high` cannot appear same-tier against the
catalog's `high` default. The
`policy_audit.severity_overrides_applied[].default_severity` row
reports the effective (dynamic-aware) default so reviewers see the
real before/after.

Two contract rules pin the design (v0.18):

- Built-in checks marked `dynamic_default=True` MUST also declare
  `floor_severity` — enforced by a `CheckMetadata` model validator.
  A swing check without a floor has no safety net against silent
  downgrade bypass.
- Plugins cannot declare `dynamic_default=True` — the plugin
  validation pipeline rejects them with status
  `dynamic_default_not_supported`. Plugins have no path to wire into
  `core/dynamic_defaults.py`'s aggregator and so would never receive
  the manifest-effective default needed for tier-crossing comparison.

Adding a new built-in dynamic-severity check requires (1) setting
`dynamic_default=True` in `CHECK_METADATA` (forces the floor), and
(2) adding an aggregator overlay branch in
`core/dynamic_defaults.py:dynamic_check_defaults`. The seed loop in
step 1 of that aggregator auto-includes every `dynamic_default=True`
catalog entry, so the resolver's internal-consistency guard cannot
false-positive on user input that overrides a swing check without
declaring the corresponding manifest section.

### Scenario Suggestion YAML

`agents-shipgate scenario suggest --from agents-shipgate-reports/report.json`
projects `report.json.suggested_scenarios[]` into
`suggested-scenarios.yaml`. It is a concrete fan-out of the JSON report's
scenario contract, not a separate scenario engine.

Stable YAML fields:

- `scenarios[].{id, scenario_type, derived_from, finding_id, source_scenario_id, source_misalignment_id, tool, adversarial_goal, expected_control}`

Suppressed findings are omitted. Baseline-matched findings are included because
they represent accepted debt, not resolved risk. `adversarial_goal` text may
evolve in minor releases; the field itself remains stable. Rows follow the
source `suggested_scenarios[]` order, then sort within each source scenario by
severity, check ID, tool, finding ID, and misalignment ID.

#### `release_decision.decision` vs `summary.status`

These are **intentionally different signals**, kept apart for backwards compatibility:

| Field | Baseline-aware? | Recommended for release gating? |
|---|---|---|
| `release_decision.decision` | yes — baseline-matched criticals appear in `review_items`, not `blockers` | **yes (v0.8+)** |
| `summary.status` | no — any unsuppressed critical flips status to `release_blockers_detected` | preserved for v0.7 callers |

#### Release decision truth table

The classification below is the contract for how every active finding lands in `release_decision.{blockers, review_items}[]` and which `contribution_rules[].rule` (v0.17+) fires for it. Starting in v0.33, a finding with typed `support` is considered active for release contribution only when `support.policy_eligible=true`; otherwise it is excluded with `rule="unsupported_evidence"` before severity or `blocks_release` is consulted. Suppressed findings are excluded with `rule="suppressed"` after that evidence-eligibility check. Legacy findings without typed support retain the table's established behavior.

Notation: `fail_on` is `release_decision.fail_policy.fail_on` after `ci_mode` resolution (advisory → empty, strict → `["critical"]`, plus any explicit `--fail-on` override). `blocker_severities` = `{critical} ∪ fail_on`. `review_tier` = `{critical, high, medium}` (or any severity when `requires_human_review=true`).

| `blocks_release` | severity | baseline_status | severity in `blocker_severities`? | severity in `review_tier`? | category | `rule` | strict-mode exit |
|---|---|---|---|---|---|---|---|
| true | any | new / null | n/a | n/a | **blocker** | `policy_block_new` | 20 |
| true | any | matched | n/a | yes | review_item | `policy_baseline_accepted` | 0 (with `--baseline-mode new-findings`) |
| true | any | matched | n/a | no | excluded | `policy_baseline_accepted` | 0 (with `--baseline-mode new-findings`) |
| true | any | resolved | n/a | n/a | excluded | (not produced; resolved findings are absent from the active set) | 0 |
| false | any | new / null | yes | n/a | **blocker** | `severity_block_new` | 20 |
| false | any | matched | yes | yes | review_item | `severity_baseline_accepted` | 0 (with `--baseline-mode new-findings`) |
| false | any | matched | yes | no | excluded | `severity_baseline_accepted` | 0 (with `--baseline-mode new-findings`) |
| false | any | new / null | no | yes | review_item | `review_required` | 0 |
| false | any | matched | no | yes | review_item | `review_required` | 0 |
| false | any | new / null / matched | no | no | excluded | `sub_threshold` | 0 |

**Why baseline-matched policy findings drop to `review_items`, not `blockers`.** `blocks_release=true` represents an explicit *policy* decision (Action Surface Diff rule, `action_surface:` manifest entry, or policy-pack rule with `block: true`) that the finding must block release **on first appearance**. A baseline accepts technical debt that already passed prior review — the project agreed to ship with that finding present. Treating baselined policy debt as a hard blocker would defeat the purpose of `baseline save`. The baseline-aware drop is symmetric for severity-driven blockers and policy blockers: both land in `review_items` once accepted into the baseline, both become hard blockers if newly introduced.

**Why `severity ∈ blocker_severities + matched + below review_tier` lands in `excluded`, not `review_items`.** A finding whose severity isn't in `{critical, high, medium}` (and which doesn't carry `requires_human_review=true`) has nothing for a human reviewer to act on per the v0.8 contract — it's been baselined and isn't severe enough to warrant attention. v0.17 records this in the audit so the (rare) edge case isn't silently invisible, but the `blockers[]`/`review_items[]` lists themselves are unchanged.

**Why exit code 20 depends on `--baseline-mode`.** `release_decision.{blockers, review_items}[]` always include the full set computed against `report.findings` (with suppressed excluded). The strict-mode exit code, however, is computed from `baseline_filtered_active(report, new_findings_only=...)` — when `--baseline-mode new-findings` is set (the default for the GitHub Action when `baseline:` is provided), baseline-matched policy and severity blockers are filtered out before the exit check, so exit is `0`. With `new_findings_only=False`, a matched policy blocker still triggers exit 20. The `release_decision` block remains baseline-aware in all cases; only the exit-code path changes mode.

Concretely: a scan with one baseline-matched critical and zero new findings produces `summary.status = "release_blockers_detected"` AND `release_decision.decision = "review_required"`. Both are correct under their respective contracts. New consumers should read `release_decision.decision`.

#### Evidence-only decision states

Finding blockers take precedence over evidence quality. If
`release_decision.blockers[]` is non-empty, the decision is `blocked` even when
the scan also has low-confidence tools or source warnings.

Starting in report v0.29, semantic evidence has zero tolerance. Every action
must carry a pass-eligible normalized effect and authority assessment. One
`unknown`, `inferred`, `protocol_default`, `partial`, `conflicting`, invalid,
or incomplete required dimension prevents `passed`, regardless of the number
of fully described actions. These gaps are recorded under
`evidence_coverage.evidence_gaps[]` and
`evidence_coverage.semantic_coverage`; they are not Findings and therefore
cannot be suppressed, baselined, severity-overridden, waived by
`--no-heuristics`, or satisfied by `human_ack`. A semantic gap routes to
`insufficient_evidence`; a known unscoped or ambient authority concern routes
to `review_required`.

When there are no blockers, `insufficient_evidence` means the static inputs are
not strong enough for Shipgate to gate release confidently. It does **not**
prove the agent is unsafe. The evidence is considered below threshold when
low-confidence tools are at least `max(1, ceil(tool_count × 0.5))`, or when
source-loader warnings exceed `3`. One to three source warnings without
blockers route to `review_required` so a human still sees the degraded source
coverage.

**Active high/critical findings take precedence over the IE label.** When the
evidence is below threshold *and* there is an active (non-baseline-accepted)
high- or critical-severity review finding, the decision is `review_required`
rather than `insufficient_evidence`. Both verdicts are
equally non-mergeable (`can_merge_without_human` is false either way), but
`review_required` points the reviewer at a specific, named finding instead of
the vaguer "we couldn't see enough." The evidence gap is **not** lost: the
underlying counts remain in `release_decision.evidence_coverage`
(`low_confidence_tool_count`, `source_warning_count`, `evidence_gaps[]`), so a
consumer must read those fields rather than the verdict label alone to know
whether evidence was degraded. `insufficient_evidence` still fires when the only
signal is weak evidence with no named high/critical concern; `blocked` still
takes precedence over both. The precedence is therefore:
`blocked` → `review_required` (active high/critical) → `insufficient_evidence`
→ `review_required` (other) → `passed`.

The intended recovery for a degraded-evidence case — whichever of the two
verdicts it lands on — is the structured action on the *selected* row of
`release_decision.evidence_coverage.evidence_gaps[]`: the first row whose
`next_action.path` names a visible target or whose `next_action.command` is
publishable as authored, falling back to the first row when no row offers
either (v0.16+; before that it was always row 0). For supported frameworks,
that action names the generated local inventory artifact and the exact
`<framework>.tool_inventories` manifest route, **including the `source_id` that
binds the inventory to the source the gap is keyed to**. An inventory referenced
without `source_id` is an independent source: its entries are added beside the
extracted tools rather than completing them, so the gap stays open and the
catalog grows — following the route without that field is not the prescribed
recovery. Only unidentified or unsupported source shapes receive generic
MCP/OpenAPI/inventory guidance. Apply the reviewed
evidence route and rerun the scan. When the decision is `review_required`
because of an active high/critical finding, also resolve that finding.
`agents-shipgate verify` keeps both cases human-routed
(`fix_task.actor = "human"`): a degraded-evidence case never opens an automated
coding-agent fix path, regardless of which verdict it carries.

### Check IDs

Once a check ID ships in a tagged release (`SHIP-POLICY-APPROVAL-MISSING`, `SHIP-ADK-GUARDRAIL-EVIDENCE-MISSING`, etc.), it will not be:

- Renamed
- Removed (only deprecated, with at least one minor-version cycle)
- Repurposed (the conditions under which it fires may *narrow* but never broaden in a way that breaks existing suppressions)

New check IDs may be added in any minor release. If your CI pins severities by check ID, expect new checks to surface as new findings.

### Check catalog metadata

`agents-shipgate list-checks --json`, `agents-shipgate explain <CHECK_ID>
--json`, and `docs/checks.json` expose `CheckMetadata.mvp_tier` for
display/triage only. Current values are `core`, `adapter`, `evidence`,
`lifecycle`, and `hygiene`. This field does not affect check execution,
severity, fingerprints, baselines, `release_decision`, or CI exit behavior.

### Static Python extraction

OpenAI Agents SDK, CrewAI, and LangChain/LangGraph AST extractors share the
same runtime/context parameter skip list: `self`, `cls`, `ctx`, `context`,
`config`, `runtime`, `run_manager`, and `callbacks`. Those names are treated as
framework plumbing and are omitted from normalized tool input schemas. Google
ADK uses its own static extractor skip list: `self`, `ctx`, `context`, and
`tool_context`. For OpenAI Agents SDK sources, file and directory mode both emit
manifest-relative POSIX `source_ref` values; directory mode scans only immediate
`*.py` files in sorted order.

### Fingerprint algorithm

`fingerprint = "fp_" + sha256(check_id | tool_name | canonical_evidence)[:16]`

Where `canonical_evidence`:
- Sorts dict keys recursively
- Sorts list items by JSON repr
- **Excludes** the `default_severity` audit-evidence key (so applying `severity_overrides` does not change identity)
- **Excludes** the `source_provenance` evidence key (so adding local HITL provenance does not rotate existing baselines or suppressions)

Fingerprints are stable across runs on the same input. They are the identity primitive used by suppressions and baselines.

### Trust-model invariants

The scanner does not, under any circumstances:

- Execute or import user code (the SDK loaders use `ast.parse` only)
- Make HTTP requests
- Connect to MCP servers
- Invoke LLMs
- Send telemetry

The no-execute / no-import property is enforced by two complementary
tests on every CI run, not by convention:

- **[`tests/test_adapter_static_only.py`](tests/test_adapter_static_only.py)** —
  AST scan of every `.py` file under `src/agents_shipgate/` (v0.18+
  widened scope from `src/agents_shipgate/inputs/` only). The scan
  rejects:
  - Bare-name calls to `exec` / `eval` / `__import__` / `compile`.
  - Attribute calls to `importlib.import_module`,
    `importlib.util.spec_from_file_location`,
    `importlib.util.module_from_spec`,
    `importlib.machinery.SourceFileLoader`,
    `runpy.run_path`, `runpy.run_module`,
    `subprocess.{run, call, Popen, check_call, check_output}`,
    `os.system`, `os.popen`, and every variant under the
    `os.exec*` / `os.spawn*` / `os.posix_spawn*` prefixes.
  - Module imports of `runpy`, `subprocess`, `importlib`,
    `importlib.util`, `importlib.machinery`, and `builtins` — in any
    `import X`, `import X as Y`, `import X.child`, or
    `from X.child import …` form.
  - Wildcard `from os import *`.

  `importlib.metadata` is intentionally allowed: the plugin registry
  uses it for entry-point discovery, and discovery happens against the
  *installed* environment, not user workspace files. `importlib.resources`
  is allowed (v0.18+) at the import line **only** so name-aliases get
  built; every `importlib.resources.<attr>(...)` call site is forbidden
  via the `importlib.resources.` prefix in `FORBIDDEN_ATTR_CALL_PREFIXES`
  and must carry a per-call-site `ALLOWED_EXCEPTIONS` entry with snippet
  pinning. This covers `files`, `read_text`, `read_binary`, `path`,
  `open_text`, `open_binary`, `is_resource`, `contents`, `as_file`, and
  any future addition under the module — all of which take an
  anchor-package argument and could bypass the dynamic-import lint if
  left unrestricted. Aliased re-exports (`import os as oo`,
  `from os import system as sh`, `import os; import pathlib as os`) are
  resolved through union-of-bindings alias maps so a later import
  cannot erase an earlier forbidden binding. The lint runs as a
  dedicated CI step labeled *Trust-model invariant lint* before the
  main test suite so a regression is visible at the top of CI logs.

  **Meta-CLI surfaces (allowlisted, audited).** First-party meta-CLI
  surfaces are pinned **per call site** in
  [`tests/test_adapter_static_only.py::ALLOWED_EXCEPTIONS`](tests/test_adapter_static_only.py)
  by a four-tuple `(relative_path, surface, line, snippet)` where
  `snippet` is the canonical `ast.unparse` of the offending AST node.
  Each entry carries a prose rationale and pins a single call:

  - **`cli/bootstrap.py`** — one `subprocess.run` call shells the
    installed agents-shipgate CLI to chain
    `detect → init → scan → apply-patches`.
  - **`cli/discovery/artifacts.py`** — one `subprocess.run` call resolves
    the repository root with `git rev-parse`; one `subprocess.Popen`
    boundary incrementally drains the fixed `git ls-files` inventory under
    hard byte and wall-clock limits. Both use a sanitized Git environment,
    read repository metadata only, and never invoke a shell or fetch.
  - **`triggers.py`** — one
    `importlib.resources.files('agents_shipgate')` call resolves the bundled
    trigger catalog. Optional Git-backed trigger input delegates to the
    verifier's audited collector described below; this module has no
    subprocess surface of its own.
  - **`cli/verify/git.py`** — one shared `subprocess.run` boundary invokes
    local Git plumbing for exact base/head and working-tree orchestration,
    plus `pack-objects`, `index-pack`, and `fsck` to materialize an isolated,
    object-ID-validated snapshot. One shared `subprocess.Popen` boundary
    incrementally drains fixed Git diff, changed-path, attribute, inventory,
    and retained-manifest reads under hard output and wall-clock bounds. The
    bound is fail-closed: timeout, overflow, read/write failure, or an
    unexpected return code yields no trusted result. Neither process boundary
    fetches or executes user code; both use sanitized environments and list
    argv without a shell.
  - **`core/authorization_execution.py`** — one shared `subprocess.run`
    boundary consumes the fixed protected Git argv, and one audited
    `subprocess.Popen` boundary parent-streams `pack-objects` with hard stdout,
    stderr, and wall-clock limits. Both use a host-protected `/usr/bin/git`,
    sanitized environment, isolated/fsck-validated object graph, fixed list
    argv, exact force-with-lease, and no shell. They are explicit operational
    executor surfaces, not part of static tool extraction.
  - **`cli/fixture.py`** — one `subprocess.run` helper invokes local
    `git init`, `git config`, `git add`, `git commit`, and `git update-ref`
    against a temporary bundled fixture copy so
    `fixture run ai_generated_refund_pr` can produce verifier artifacts.
    This allowlisted meta-CLI surface uses fixed argv, no shell, no network
    fetch, and no user-code execution.
  - **`fixtures.py`** — one `importlib.resources.files('agents_shipgate')`
    call to resolve the bundled fixture directory.
  - **`cli/discovery/agent_instructions/adoption_kit.py`** — one
    `importlib.resources.files('agents_shipgate')` call to resolve bundled
    first-party adoption-kit files from the installed wheel. Downstream
    customization is explicit repo-local file reading through
    `--agent-instructions-kit`, never dynamic imports or network fetches.
  - **`cli/trigger.py`** — imports `subprocess` only to catch
    `subprocess.CalledProcessError` from the shared
    verifier Git collector reached through `triggers._git_diff_context`. The
    `agents-shipgate trigger` subcommand issues no subprocess call of its own.
  - **`cli/self_check.py`** — one `__import__(module_name)` call
    validates that supplied modules import cleanly. Runs only under
    `agents-shipgate self-check`, never during scan.

  Per-call-site pinning means **adding a second occurrence of an
  already-allowlisted surface in the same file STILL requires a new
  entry**. Changing the call's argv shape (the `snippet` changes)
  also fails the test, forcing a reviewer to confirm the change is
  benign. The literal-anchor invariant for
  `importlib.resources.files('agents_shipgate')` is enforced by
  snippet pinning: a future `files(user_var)` call would not match.

  Three contract tests pin the audit trail:
  `test_allowlist_entry_matches_real_surface` (every entry matches a
  real violation on all four fields),
  `test_no_unallowlisted_forbidden_surface_in_scanner` (every
  observed violation has a matching entry), and
  `test_allowed_exceptions_pin_subprocess_per_call_site` (the
  multi-call files have distinct entries per call site, regression-
  testing the structural fix from the v0.18 PR #2 review).
- **[`tests/test_fixture_no_import.py`](tests/test_fixture_no_import.py)** —
  per-adapter live-load tests. Each adapter (LangChain, CrewAI, OpenAI Agents
  SDK, Google ADK, MCP, OpenAPI, Anthropic, OpenAI API, n8n, Codex plugin) is
  driven against a fixture whose Python content (or a sibling `trap.py`, for
  declarative adapters) raises `RuntimeError` at module load. Each test
  additionally snapshots `sys.modules` and asserts no module whose `__file__`
  resolves under the fixture root ends up cached after the scan — a stronger
  property than relying on the runtime raise alone.

If a contributor introduces a real need for one of the forbidden surfaces,
update this section in the same PR. The intent is not "we tried to forbid X"
— it is that X is *structurally absent* from the scanner's parsing path.

Plugins are off by default. `AGENTS_SHIPGATE_ENABLE_PLUGINS=1` enables loading; `--no-plugins` overrides at the CLI level. When loaded, every plugin is enumerated in `report.loaded_plugins`, and every third-party adapter (v0.20+) is enumerated in `report.loaded_adapters`.

Plugin validation (v0.17+ / M5). Every entry point is checked against five load-time gates before it can run:

1. **load** — `entry_point.load()` must not raise.
2. **signature** — the loaded object must be callable and accept exactly one required positional parameter (`ScanContext`); extra defaulted positional / keyword-only parameters are allowed.
3. **metadata** — `AGENTS_SHIPGATE_METADATA` must be present and parseable as `CheckMetadata`. Both `id` and `check_id` are accepted as the identifier key (v0.17 alias); newer plugins should prefer `check_id` for symmetry with `Finding.check_id`.
4. **id_collision** — the plugin's check ID must not shadow a built-in (including legacy aliases) or a previously-registered plugin.
5. **bad_floor** — `floor_severity` must not exceed `default_severity` on the same metadata block.

Plugins that pass every gate run with the same trust as built-ins. Runtime validation additionally drops findings whose `Finding.check_id` does not match the plugin's declared `id`/`check_id`, drops non-`Finding` items, and captures any exception raised during the plugin call into `loaded_plugins[].runtime_errors`. The scan continues regardless; `--strict-plugins` elevates any non-`valid` plugin or non-empty `runtime_errors` to exit code 4.

#### Third-party adapter discovery (v0.20+)

Third-party adapters register through the `agents_shipgate.adapters` Python entry-point group and provide a class (or instance) satisfying the `ToolSourceAdapter` Protocol — a `source_type: str` ClassVar, a `scope: Literal["per_source", "per_scan"]` ClassVar, an `artifact_class: type | None` ClassVar, and a `load(source, base_dir, manifest)` method returning `LoadedAdapterResult`. Discovery is gated by the same `AGENTS_SHIPGATE_ENABLE_PLUGINS=1` env var as plugin checks; `--no-plugins` forces it off.

Every discovered entry point is checked against four load-time gates before it can register on the scan's adapter registry:

1. **load** — `entry_point.load()` must not raise. Captured as `validation_status="load_failed"`.
2. **bad_protocol** — the loaded value (a class is instantiated with no args; an instance is used directly) must have all three ClassVars (`source_type` non-empty string, `scope`, `artifact_class`) and a callable `load` method that accepts the three positional arguments `(source, base_dir, manifest)`: at least three positional slots (or `*args`), no more than three required positional parameters, and no required keyword-only parameters. Captured as `validation_status="bad_protocol"`.
3. **bad_scope** — `scope` must be exactly `"per_source"` or `"per_scan"`. Out-of-range values would be silently skipped by the dispatcher. Captured as `validation_status="bad_scope"`.
4. **source_type_collision** — the adapter's `source_type` must not shadow a built-in (`mcp`, `openapi`, `langchain`, etc.) or another third-party adapter discovered earlier in the same scan. **This is the load-bearing trust rule** — without it, a malicious plugin could displace a built-in adapter and intercept every scan targeting that source type. Captured as `validation_status="source_type_collision"`.

**Per-scan registry contract.** Adapters that pass every gate register on a **per-scan clone** of the global `REGISTRY` (built at the start of each `run_scan` / `inspect_sources` via `AdapterRegistry.clone()`), NOT on the global itself. The global stays builtin-only across the lifetime of the process. This guarantees two trust invariants:

- **`--no-plugins` is per-scan honest.** A later in-process scan with `plugins_enabled=False` sees a fresh builtin-only clone — no third-party adapters carried over from a prior enabled scan.
- **Collision detection is per-scan honest.** The collision set is the clone's builtins-only state, so two consecutive scans of the same valid third-party adapter both classify as `validation_status="valid"`, never as `source_type_collision` against the adapter's own previous registration.

The dispatcher walks the per-scan registry in the same pass-1 (per-source, in `tool_sources[]` declared order) / pass-2 (per-scan, in canonical registry order) loops it uses for built-ins. Two trust mechanisms protect the dispatch path:

- **Artifact-class smuggling prevention.** The dispatcher's `_absorb` step fires `TypeError` if any adapter (built-in or third-party) declares one `artifact_class` but returns an artifact of another type. This is the structural counterpart to the `Finding.check_id` smuggling rule for plugin checks.
- **Runtime-error capture for third-party adapters.** Third-party adapters that raise at runtime do NOT abort the scan. The dispatcher routes their `load()` call through `run_validated_adapter` (from `inputs/adapter_validation.py`), which catches every exception, captures it into `loaded_adapters[].runtime_errors` on the matching row, and signals the dispatcher to skip absorbing the (None) result. Built-in adapters keep the direct call shape — a built-in raising means the scanner itself is broken and must abort loudly.

`doctor` (`inspect_sources`) uses the same per-scan registry clone + discovery + dispatcher path as `scan`, so manifests referencing third-party `tool_sources[].type` values are introspectable. The doctor payload surfaces `loaded_adapters[]` alongside the existing `policy_packs` field.

`--strict-plugins` (v0.17+) covers BOTH plugin and adapter failures from v0.20+ — any non-`valid` `loaded_plugins[]` row, any non-empty `loaded_plugins[].runtime_errors`, any non-`valid` `loaded_adapters[]` row, OR any non-empty `loaded_adapters[].runtime_errors` elevates the scan to exit code 4. Default behavior remains lenient — failures are recorded in the respective provenance arrays and the scan proceeds.

**Manifest `tool_sources[].type`.** The field is `str` (relaxed from a closed `Literal` in v0.20) so manifests can reference third-party per-source adapters by name. Built-in source types are enumerated in `BUILTIN_TOOL_SOURCE_TYPES` for documentation and tooling; per-scan-only built-ins (`n8n`, `openai_api`, `anthropic_api`, `validation`) are still rejected at manifest-load time with a routable error pointing the user to the dedicated top-level manifest section. Unknown source types — both genuine third-party names with no registered adapter and typos of built-in names — fail with `ConfigError` (exit 2) when the dispatcher's `AdapterRegistry.require` cannot resolve them. The exit-2 contract is unchanged from prior releases; the failure layer (manifest-load vs dispatch) may differ.

### Manifest Schema

The manifest schema version (`version: "0.1"`) is independent of the CLI
version and package version. Manifest schema changes follow their own
deprecation cycle, and the manifest loader is intentionally strict: older CLIs
reject unknown top-level fields instead of silently ignoring release policy.
Manifests that use `action_surface:` require a CLI whose
`agents-shipgate contract --json` reports `report_schema_version >= 0.16`.

`tool_sources[].authority` (`{mode, auth_type, credential_mode, scopes, reason}`)
is additive and requires `report_schema_version >= 0.38`. It declares one
reviewed authority for every action the source contributes; an
`action_surface.actions[]` row that declares its own `authority` overrides it
for that action. Both sites share one set of mode co-requirements and one
conflict rule against published source evidence, and existing manifests are
unaffected — a source with no `authority` block resolves exactly as before.

`tool_sources[].binding` (`{complete: true, reason}`) is additive and changes
no published schema, so there is no `report_schema_version` floor to check for
it. A CLI that predates the field *rejects* it — the manifest loader is strict,
so the run stops with a routable `ConfigError` (exit 2) naming the unknown key
rather than ignoring a reviewed claim. It declares that every tool the source
contributes is part of the surface under review, which is what a tool server
publishing its own `tools/list` asserts; the effect is only ever to widen the
analysed surface, and a source with no `binding` block resolves exactly as
before. It does not replace `agent_bindings.declarations`, which remains the
way an agent declares the subset of a catalog it wires.

`policies.control_pack` (`default` | `financial-strict` | `read-only-agent`)
is additive and changes no published schema, so there is no
`report_schema_version` floor to check for it. A CLI that predates the field
*rejects* it — the manifest loader is strict, so the run stops with a routable
`ConfigError` (exit 2) naming the unknown key rather than ignoring a release
rule. To detect support before writing it, read `control_pack.available` from
`agents-shipgate init --json`; a CLI without control packs emits no
`control_pack` key at all. It selects which controls
each action effect requires. Omitting it means `default`, which is exactly the
rule set every prior release applied, so existing manifests keep their
verdicts. Every built-in pack requires at least what `default` requires — a
pack can add obligations and can never drop one — so a report that passes
under any pack would also have passed under `default`. Selecting a pack does
not change what a *declaration* means: the obligation lattice deciding whether
a declared effect covers an inferred one stays the built-in table.

Obligations for effects with no dedicated control check (`write`,
`privileged_data_access`, `identity_access`) are reported through
`SHIP-ACTION-POLICY-VIOLATION` at `high`, with the rule named in
`findings[].evidence.policy_id` as `control-pack:<effects>` (effects joined by
`+` where one rule covers several). The pack name is deliberately **not** in
that id: `policy_id` is a fingerprint input, and two packs requiring the same
controls for the same effect state one rule, so naming the pack would re-open
a baseline entry on a move that changed nothing. `control-pack:` is a reserved
prefix — `action_surface.policies[].id` rejects it at manifest load, the same
way `SHIP-` is reserved for built-in check ids.

Like the four dedicated control families, these are mandatory current-surface
controls: a `checks.ignore` entry records the exception without waiving the
blocker. That is decided from `findings[].evidence.control_pack`, which only
the engine writes, and never from the `policy_id` string alone.

`effective_policy.control_pack` (v0.40+) publishes the pack in force, so a
base-vs-head comparison can see the gate weakened by a *rule* change and not
only by a severity one. `SHIP-VERIFY-POLICY-WEAKENED` gains
`kind: control_pack_weakened`: one finding per pack move, carrying
`removed_controls[] = {effect, controls}` for every effect the head pack
requires less of. A snapshot with no `control_pack` predates the field and is
compared as `default` — a build without control packs could not have loaded a
manifest naming one. A snapshot naming a pack this build cannot resolve is a
different case and is not read as "no weakening": it routes to
`SHIP-VERIFY-POLICY-BASE-ABSENT` with `kind: control_pack_unrecognized`, the
reason code that says the comparison could not be made.

Control findings carry `findings[].evidence.control_pack` and
`findings[].evidence.control_effects` (the effects the rule actually matched,
not its whole category). Both are excluded from the finding fingerprint, so a
baseline recorded before the fields keeps matching.

`run_id` covers the pack's **id, version, and canonical obligations**: two
manifests selecting different packs enforce different policy and are different
runs, including where neither produces a control finding, and a release that
changes what `default` requires moves it too.

Moving to a stricter pack changes the `missing` list of a control finding
whose requirements grew, and therefore its fingerprint — a baseline entry that
accepted the narrower gap stops matching and the finding re-opens. A move
between two packs that require the *same* controls for an effect re-opens
nothing, because the rule did not change.

### Baseline Integrity (v0.5)

Baseline schema bumps to `0.5`. The wire shape adds an optional
`findings[].provenance` block per entry recording when and by which scanner
the entry was added:

```json
{
  "fingerprint": "fp_…",
  "check_id": "SHIP-…",
  "tool_name": "…",
  "severity": "high",
  "title": "…",
  "provenance": {
    "scanner_version": "0.13.0",
    "run_id": "agents_shipgate_…",
    "recorded_at": "2026-05-15T14:23:00Z",
    "reason": null,
    "expires": null
  }
}
```

`provenance` is optional on the wire so older v0.2/v0.3/v0.4 baselines still
load. The integrity check flags legacy-no-provenance entries as
`SHIP-BASELINE-INTEGRITY-MISMATCH` until they are re-stamped by re-running
`agents-shipgate baseline save`. `provenance.reason` and `provenance.expires`
are reviewer-set and free-form / ISO-8601 date respectively.

Each `agents-shipgate baseline save` appends one JSON line to
`<baseline-dir>/baseline-audit.log`. The log row is **stable**:

- `audit_schema_version: "0.1"`
- `timestamp` — ISO-8601 UTC
- `run_id` — scan's run_id (matches `BaselineProvenance.run_id` for any
  fingerprints added in this save)
- `scanner_version` — Agents Shipgate version that wrote the row
- `baseline_path` — string path saved at the time of the row
- `hash_before` — `"sha256:…"` of the prior baseline file content, or `null`
  when this was the first save
- `hash_after` — `"sha256:…"` of the new baseline file content
- `added_fingerprints[]`, `removed_fingerprints[]` — sorted deltas

The audit log is append-only and intentionally co-located with the baseline so
a single `.agents-shipgate/` directory carries both. Commit both files
together; reviewers can `git log .agents-shipgate/baseline-audit.log` to see
when fingerprints joined the baseline.

`manifest.baseline.integrity_mode` controls behavior when `scan --baseline X`
detects an integrity issue. Stable values:

- `off` — no integrity checks. Back-compat escape hatch for repos that have
  not migrated to v0.5 baselines yet.
- `warn` (default in v0.11) — integrity findings emitted but
  `blocks_release: false`; release decision is unaffected.
- `strict` — `SHIP-BASELINE-INTEGRITY-MISMATCH` carries
  `blocks_release: true` and `agents-shipgate baseline verify` exits `6` on
  the same condition.

New stable check IDs (v0.11+):

- `SHIP-BASELINE-INTEGRITY-MISMATCH` (critical) — file hash mismatch, missing
  audit log, audit log empty or malformed, entry references unknown `run_id`,
  or entry loaded from a legacy schema without provenance.
- `SHIP-BASELINE-ENTRY-EXPIRED` (high) — `provenance.expires` < today.
- `SHIP-BASELINE-ENTRY-STALE` (low) — deprecated check ID in the entry, or
  the entry matched no active finding (scan-aware; resolved-not-pruned).

Integrity findings bypass `checks.ignore` (suppression) and
`checks.severity_overrides`. Silencing tamper detection would defeat the
trust property the audit log defends. They flow through the regular report
pipeline otherwise (fingerprinting, baseline-status assignment, remediation
annotation).

The audit log is **tamper-evident, not tamper-proof**: a well-resourced
adversary who atomically rewrites both the baseline JSON and the audit log
defeats `verify`. The goal is to make casual or accidental edits observably
wrong in code review.

### Verify Orchestrator

`agents-shipgate verify` is the canonical ongoing-PR command. It evaluates the
published trigger catalog against the local diff, optionally scans a locally
available base tree into an isolated temporary directory, and then runs exactly
one authoritative head scan. When `--head` is provided, the head scan uses an
isolated archive of that ref; when omitted, it scans the checked-out workspace.
`report.json.release_decision.decision` remains the only release gate;
`verifier.json` is an orchestration artifact.

`verify` never fetches. Callers that want base diff enrichment must make the
base ref available before invoking the command, for example with
`actions/checkout` `fetch-depth: 0` or an explicit `git fetch origin <base>` in
CI. If the requested base ref or PR diff context is unavailable, verify records
`base_status` in `verifier.json`, skips a head-only scan, emits
`merge_verdict: "unknown"`, and exits 2. If the base tree is available but the
base manifest or base scan is unavailable, verify records `base_status`, disables
diff enrichment, and leaves the head release decision and exit code unchanged.

Before any trigger-skip can return success, non-preview `verify` also requires
the resolved `--config` path to exist. A missing config is a configuration
failure, not a docs-only or no-trigger success: verify writes `verifier.json`,
`verify-run.json`, `agent-handoff.json`, and `pr-comment.md` with
`head_status: "failed"`, `head_exit_code: 2`, `merge_verdict: "unknown"`,
`execution: "failed"`, `applicability: "failed"`,
`control.state: "agent_action_required"`, and
`can_merge_without_human: false`; it writes no
`report.json` and runs no head scan. The first next action directs agents to
fix the config path or run `agents-shipgate verify --preview --json` before
initializing.

The head scan writes `report.md`, `report.json`, `report.sarif`, `packet.json`,
`verifier.json`, `verify-run.json`, `agent-handoff.json`, `pr-comment.md`,
`verification-plan.json`, `verification-unit-result.json`,
`verification-artifacts.json`, and, last, `verification-receipt.json`.
`verify` intentionally requests packet
JSON only, regardless of manifest `output.packet.formats`; `pr-comment.md` is
the human PR surface. Use `agents-shipgate scan` when you want the manifest's
full packet renderer set (`packet.md`, `packet.html`, or `packet.pdf`).

These artifacts go to `--out`, or to `agents-shipgate-reports` under
`--workspace` when it is omitted. A relative `--out` resolves against the
current directory, like `scan --out`, `audit --host --out` and `agent control
--reports-dir`; `verifier.json` names each artifact inside the repository
relative to its `workspace`, and `verify --format json` prints those paths
relative to the current directory when beneath it and absolute otherwise
([migration note](#relative-out-current-directory-818)). That directory is left
out of every working-tree read the run makes so that its own reports are not
part of the change. An output directory inside the repository must therefore hold nothing
else Git would report: `verify`, `--head`, `--preview` and manifest-free
`verify` exit `2` with `config_error`, before writing anything, when it holds a
committed path (at `HEAD`, or one a worktree change removes), a staged or
untracked unignored path that is not a Shipgate artifact, or any trust-root
path, or when it lies inside a trust root Git does not ignore; every reader
refuses a pointer in such a directory as `workspace_unverifiable`. A gitignored
directory, a directory outside the repository, and a directory holding only
uncommitted Shipgate artifacts outside any trust root, with nothing committed
beneath it, are accepted. The directory is judged by physical identity, so a
symlinked or case-variant spelling is classified as the directory it reaches
([migration note](#output-directory-repository-content-804)).

`agents-shipgate verify --preview --json` is a lightweight relevance check: it
runs no scan, requires no manifest, exits 0, and emits a `verifier.json` with
`mode: "preview"`, `execution: "not_run"`,
`applicability: "not_evaluated"`, and
`control.state: "agent_action_required"`; `control.next_action` carries the
next recommended action. The handoff uses `operation: "verify_preview"` and no
release decision. That action may be `detect`/`initialize` for
relevant unconfigured repos, or `verify` for configured repos. Use it as the
first touch on a repo or PR before committing to a full scan.

`verifier.json` is governed by [`docs/verifier-schema.v0.16.json`](docs/verifier-schema.v0.16.json).
Verifier v0.1 through v0.15 remain frozen references — a published schema
identifier never gains an emitted field, so `0.9` carries
`capability_review.policy_weakening_proven` and `0.8` keeps the bytes every
consumer pinned to it already validates against. Artifacts declaring `0.8`
and earlier still read: the field defaults to `false`, which is exactly what
"this artifact recorded no base-vs-head comparison" means. It remains an orchestration artifact: `release_decision.decision` in
`report.json` is still the only release gate. Release and merge fields remain
mirrors or deterministic projections of report data; the v0.6 authorization
evaluation and the v0.7 `diff_status` block are operational overlays that
cannot change them. Stable additive
fields a consumer may read:

- `control` — the schema-enforced `complete | agent_action_required |
  review_publishable |
  human_review_required` operational projection. The same serialized object is
  emitted by verifier, handoff, and verify-run.
- `execution` — `"not_run" | "succeeded" | "skipped" | "failed"`.
- `diff_status` (v0.7+) — how completely the compared change set was read, and
  why not when it was not. `completeness` is `"complete" | "partial" |
  "unavailable"`; `reason` is `null` exactly when `completeness` is
  `"complete"`, and otherwise one of `not_attempted`, `refs_missing`,
  `merge_base_missing`, `unrelated_histories`, `objects_missing`,
  `metadata_limit_exceeded`, `body_limit_exceeded`, `git_timeout`,
  `git_failed`. `merge_base_missing` and `unrelated_histories` are
  deliberately distinct: the first is a shallow checkout that truncated a
  merge base which does exist, and deepening restores it; the second is two
  roots with no common ancestor, which no fetch can create — `fetch_repairable`
  is the field to branch on. `detail` is a bounded, path-redacted excerpt of
  Git's own diagnostic; `remediation` names the repair; `fetch_repairable`
  says whether making refs or objects available locally can fix it.
  **`"complete"` is the only value that licenses reading a negative `trigger`
  result.** Anything else means the evidence the verdict would rest on was
  missing — it is never evidence that a PR is unrelated to agent capabilities.
  `null` means the artifact predates v0.7 and carries no input-health
  evidence, which a consumer must treat as unknown, never as complete. New
  `reason` values may be added additively; treat an unrecognized reason as
  "the diff was not read in full".
- `static_analysis_only`, `runtime_behavior_verified`, and
  `static_verdict_disclaimer` — locked to `true`, `false`, and the canonical
  static-only disclaimer. When an embedded release decision is present, the
  verifier model rejects any disagreement.
- `merge_verdict` — `mergeable` / `human_review_required` /
  `insufficient_evidence` / `blocked` / `unknown`. A deterministic projection of
  `release_decision.decision` (`passed`→`mergeable`,
  `review_required`→`human_review_required`,
  `insufficient_evidence`→`insufficient_evidence`, `blocked`→`blocked`, missing
  decision→`unknown`). It cannot disagree with the gate. Switch on the enum with
  an `unknown`/`human_review_required` fallback for unrecognized future values.
- `applicability` — `"not_evaluated"` / `"verified"` /
  `"not_applicable"` / `"failed"`; whether
  Shipgate evaluated the change. Disambiguates a `mergeable` verdict
  (`"not_applicable"` means the head scan was skipped — *not* "verified safe").
  Locked to `"verified"` whenever a `release_decision` is present.
- `can_merge_without_human` — `bool`; whether the PR can merge without human
  review.
- `decision` — mirror of `release_decision.decision` (or `null` when no scan
  ran).
- `headline` — single-sentence, PR-comment-friendly summary (or `null`).
- `authorization` — the
  `shipgate.human_authorization_evaluation/v1` result. `accepted` is possible
  only for a successful `review_required` evaluation and must carry the same
  one exact command as `control.next_action` and
  `control.allowed_next_commands`. `not_requested`, `not_applicable`, and
  `rejected` carry no command authority; rejection reason codes are evidence,
  never instructions.
- `human_review` and `first_next_action` — compatibility mirrors of
  `control.human_review` and `control.next_action` for one cycle.
- `trust_root_touched` — `bool`; `true` when the PR changed a release-gate trust
  root (`shipgate.yaml`, the Shipgate CI workflow, `AGENTS.md`/`CLAUDE.md`,
  policy packs, prompts, baselines, waivers, and the other surfaces listed under
  the trust-root protection design). Backed by the deterministic
  `SHIP-VERIFY-TRUST-ROOT-TOUCHED` check, whose findings flow through the normal
  decision engine.
- `capability_review` — deterministic reviewer-facing projection of
  `capability_change`, with `{trust_root_touched, policy_weakened,
  policy_weakening_proven, capability_changes_added, capability_changes_removed,
  capability_changes_modified, top_changes[]}`. `policy_weakened` is the
  fail-closed routing flag — raised whenever the release policy may have gotten
  weaker, including when no base policy existed to compare against;
  `policy_weakening_proven` (added `0.16`, default `false`) is the narrower
  fact that a base-vs-head comparison actually ran and found the head weaker.
  It is never `true` without `policy_weakened`. Gate on the former; say the
  policy was weakened only on the latter. `top_changes[]` carries the
  highest-signal capability deltas with `{id, title, impact, rationale,
  related_finding_ids}`. `impact` mirrors the gate; this block never introduces a
  finding-independent blocker. Treat it as supporting/provisional reviewer
  context, not as the controller's primary verdict.
- `mode` — `"advisory"` / `"strict"` / `"skipped"` / `"preview"`.

`verifier.json` also carries `trigger` — the run/skip evaluation, catalog
schema `0.3`. Read `trigger.evaluation_status` before `trigger.should_run`:
when it is `"not_evaluated"`, `should_run`, `run_shipgate`, `skip`, and
`skip_reason` are all `null` because the diff was not read in full (see
`diff_status`), and `next_action.kind` is `"input_required"`. `skip_reason` is
one of `stop_conditions`, `skip_rule`, `dry_run_only`, `no_match` — and
`no_match` is never emitted for inputs that were not fully read. A `run`
verdict *is* still published from partial evidence: rule matching is monotone,
so more evidence can only add matches. `matched_rules` says what carried it —
a `force_run` match rests on the manifest being present, not on anything the
diff showed. It also carries `base_status`,
`head_status`, `base_ref`, `head_ref`, `changed_files`, `base_notes`, the full
embedded `release_decision`, and an `artifacts` map
(`{verifier_json, pr_comment, report_json, report_markdown, report_sarif,
packet_json}`). The corresponding GitHub Action outputs are `merge_verdict`,
`can_merge_without_human`, `agent_control_state`, `agent_control_reason`,
`trust_root_touched`, and
`capability_changes_{added,modified,removed}`; the original `decision`,
`blocker_count`, `review_item_count`, `ci_would_fail`, and legacy control
boolean outputs are preserved as exact derived mirrors for one cycle.

Successful base reports are cached under git metadata
(`git rev-parse --git-path agents-shipgate/base-scans/...`), not under the
working tree or report output directory. The cache is a local-iteration
optimization, safe to miss on ephemeral CI, and verify prunes stale entries
best-effort after writes. Cache access never follows a link below the metadata
directory Git selected; a linked, non-directory or unopenable cache component
makes the cache unavailable for that run, and verify regenerates the base from
Git and names the component in `base_notes` (#638; limits in
[`docs/verification-reproducibility.md`](docs/verification-reproducibility.md#base-scan-cache-namespace)).

### Verify Check IDs

New stable check IDs (v0.22+, category `verify` — trust-root protection
for AI coding workflows). All emit **only** when a `VerificationContext`
is present (`scan --changed-files …` or the `verify` command); a plain
`scan` emits nothing. Like `SHIP-VERIFY-TRUST-ROOT-TOUCHED` (v0.21), they
are category `verify`, so they bypass `checks.ignore` suppression and
declare a `floor_severity` (a manifest override below the floor is a
config error, exit 2). They are ordinary `Finding`s routed through
`release_decision` — never a second verdict.

- `SHIP-VERIFY-POLICY-WEAKENED` (high, floor high) — base-vs-head normalized
  effective policy weakened: CI mode downgraded, fail-on severity set
  loosened, or a severity override lowered across a tier. The claim is
  base-relative, so it fires only when a base snapshot exists to compare
  against.
- `SHIP-VERIFY-POLICY-BASE-ABSENT` (medium, floor medium, added `0.16`) — the
  fail-safe for the missing base. A policy/manifest trust root was touched and
  no base effective-policy snapshot was available, so no weakening claim can
  be made in either direction; the change routes to human review rather than
  passing silently. Two evidence kinds: `manifest_introduced` (git proves the
  base carries no manifest at all — a first adoption, and the only case that
  reports `verifier_summary.policy_weakened: false`) and
  `base_snapshot_unavailable` (no base report was obtainable; the direction is
  unprovable, so `policy_weakened` stays raised). Before `0.16` both kinds
  emitted under `SHIP-VERIFY-POLICY-WEAKENED`; that id keeps firing for every
  proven base-relative weakening and is not deprecated. Severity, category,
  the `human_ack` requirement on the `policy` surface, `protected_surface_changes`
  rows, and the resulting release decision are unchanged by the split, and
  three compatibility rules keep it that way:

  - **Configuration written against the pre-split id still reaches this
    check.** `checks.severity_overrides` (and any other configured match) for
    `SHIP-VERIFY-POLICY-WEAKENED` applies to `SHIP-VERIFY-POLICY-BASE-ABSENT`
    as well, so a repository that had raised the no-base fail-safe to
    `critical` still gets `critical` and still blocks. An override written
    against the new id wins over the umbrella. Floor validation is unchanged:
    an override against `SHIP-VERIFY-POLICY-WEAKENED` is still checked against
    its own `high` floor.
  - **Reports written before the split still reproject to what they meant.**
    Every read path (`verifier_summary.policy_weakened`, `capability_review`,
    `protected_surface_changes`, `human_ack`, the adoption fix-task route, and
    the Action's findings fallback) accepts either id with the same evidence
    kinds. Nothing re-emits the old id for a no-base run.
  - **Fail-closed routing and the human-facing claim are separate.**
    `policy_weakened` stays raised whenever the direction could not be
    established; the new `capability_review.policy_weakening_proven` is the
    narrower fact that a base-vs-head comparison actually ran and found the
    head weaker. Copy is selected from the narrower one, so an unprovable
    direction is never reported as a proven weakening while the conservative
    route is preserved.
- `SHIP-VERIFY-BASELINE-OR-WAIVER-EXPANDED` (high, floor high) — a new
  suppression, a widened waiver scope, or a larger accepted-debt baseline
  versus the base report.
- `SHIP-VERIFY-CI-GATE-REMOVED` (critical, floor high) — a Shipgate CI
  workflow path is in the changed files and no longer exists on disk (the PR
  deleted the gate).
- `SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED` (medium, floor medium) —
  **deprecated in 1.1.0 (#516)**. New scans emit no
  findings for this ID. It remains registered with the same metadata for
  historical reports, configured overrides and suppressions, for at least one
  minor-version cycle after the deprecation ships. It is not repurposed as a
  structural check. Existing structured host readers, the skill-command mention heuristic and
  generic trust-root protection remain active; #545 owns the remaining prose-only
  routing boundary. No release decision enum or severity changes.
- `SHIP-VERIFY-TRIGGER-CATALOG-DRIFT` (medium, floor medium) — the trigger
  catalog that decides when Shipgate runs changed; routed to human review to
  rule out gate evasion.

### Tool-Surface Diff

`agents-shipgate scan --diff-from <path>` accepts a prior `report.json` or a
v0.4 baseline JSON with `tool_surface_facts` and `action_surface_facts`. If both `--baseline` and
`--diff-from` are provided, `--baseline` continues to drive finding baseline
status, strict-mode filtering, and `release_decision.baseline_delta`;
`--diff-from` drives `tool_surface_diff` and `action_surface_diff`.

If `--diff-from` is absent and `--baseline` points at a v0.4 baseline with
surface facts, the baseline snapshot is used as the diff reference. v0.3
baselines can still enable `tool_surface_diff` but not `action_surface_diff`.
Older v0.2 baselines still load for accepted-debt gating, but they cannot
enable either surface diff and emit disabled diff notes instead.

The diff is static evidence only. It does not fetch branches in the CLI,
infer runtime routing, or execute tools. Action Surface Diff policy findings
can affect release gating through `findings[].blocks_release`; Tool Surface
Diff remains explanatory only.

### Release Evidence Packet (v0.18)

`agents-shipgate-reports/packet.json` is a supporting/provisional reviewer
artifact governed by [`docs/packet-schema.v0.18.json`](docs/packet-schema.v0.18.json).
v0.12 adds request, subject, input-set, engine-requirement, and decision IDs
while preserving the report release decision as the only gate. v0.11 and
earlier packets validate against their matching frozen schemas. v0.11 added
typed policy support; v0.9 added provider-scoped tool identities; v0.8 added
report v0.29 semantic
coverage and evidence-gap remediation; v0.7 added capability-linked
local trace evidence under `human_in_the_loop`; v0.6 added the top-level
`evidence_matrix` section and the
optional `ReleaseDecisionItem.source` and `ReleaseDecisionItem.policy_evidence_source`
pointers for reviewer-grade dual-source provenance on top of v0.5. Within `0.x`:

- `packet_schema_version` is a real field on every emitted packet; minor bumps are additive.
- `release_decision.{static_analysis_only,runtime_behavior_verified,static_verdict_disclaimer}` mirrors the report release decision exactly; current emitted values are `true`, `false`, and the canonical static-verdict disclaimer.
- The reviewer sections (release_decision, evidence_matrix, capability_intent, high_risk_surface, tool_surface_diff, action_surface_diff, approval_coverage, idempotency_risk, scope_coverage, memory_isolation, human_in_the_loop, dynamic_scenarios, not_proven) are always present.
- `evidence_matrix.rows[]` is a compact, packet-only review summary derived from public `report.json` fields. It never contributes to `release_decision`, CI exit behavior, severity, suppression, baseline matching, or `agent_summary`; its blocker and review-item references are copied from `release_decision`.
- The 13 `evidence_matrix.rows[].domain` identities are stable within `0.x`. Adding source paths or check mappings is additive; removing a row, renaming a domain, or dropping an existing check/source mapping requires a packet schema bump.
- `human_in_the_loop.runtime_control_disclaimer` is always present and applies to covered and gap states: local HITL evidence is not runtime-enforcement proof.
- `human_in_the_loop.source_provenance[]` is deterministic, local-only provenance for validation evidence when available. Packets rebuilt from `report.json` may set `provenance_mode: "unavailable"` when no finding-level provenance survived.
- `human_in_the_loop.capability_trace_summary` and `human_in_the_loop.capability_trace_refs` are deterministic audit metadata for declared local trace artifacts. They do not prove runtime enforcement and never contribute to the packet verdict.
- `release_decision.verdict` always derives from `release_decision.decision`. CI behavior (`fail_policy`) is rendered separately as metadata, never as the verdict.
- `not_proven.unconditional` always lists the four canonical disclaimers verbatim — prompt robustness, runtime behavior, model correctness, adversarial resistance.
- The packet is a local artifact (`agents-shipgate-reports/packet.{md,json,html}`, optionally `packet.pdf` with the `[pdf]` extras). There is no hosted/SaaS surface.

### Fixture names

Fixture names listed by `agents-shipgate fixture list` are stable. Names will not be renamed. New fixtures may be added.

`ai_generated_refund_pr` is the verify-native demo fixture. It creates a
temporary base/head git history and writes `verifier.json`, `verify-run.json`,
`agent-handoff.json`, `verification-receipt.json`, `report.json`, and
`pr-comment.md` for a blocked
refund-capability PR.

### Agent handoff artifact

`agents-shipgate-reports/agent-handoff.json` is the preferred compact
machine-readable handoff object for coding agents and CI agents. The current
schema is
[`docs/agent-handoff-schema.v8.json`](docs/agent-handoff-schema.v8.json) with
`schema_version: "shipgate.agent_handoff/v8"`. v1 through v7 remain frozen
references.

The handoff artifact is derived only from `verifier.json`, `verify-run.json`,
and `report.json`. It mirrors `release_decision.decision`,
`verifier.json.merge_verdict`, and
the byte-identical `verifier.json.control` object. Handoff v8 also mirrors the
verifier's `authorization` evaluation; it cannot upgrade or reinterpret it. Its
`gate.{static_analysis_only,runtime_behavior_verified,static_verdict_disclaimer}`
also mirrors the verifier/report boundary; construction fails if any mirror
disagrees. It never computes a separate release verdict, does not contain
LLM-generated prose, and does not replace
`report.json.release_decision.decision` as the release gate.

Use `agents-shipgate agent handoff --from agents-shipgate-reports/verifier.json
--report agents-shipgate-reports/report.json --verify-run
agents-shipgate-reports/verify-run.json --json` to re-render the same artifact
from existing local outputs. The command exits `0` when a valid handoff is
emitted, `3` for missing or invalid input artifacts, and `4` for internal
errors; it does not mirror the gate result.

### Feedback export

`agents-shipgate feedback export` derives a small local artifact from
`agents-shipgate-reports/verifier.json`. The current schema is
[`docs/feedback-schema.v0.1.json`](docs/feedback-schema.v0.1.json). Current
v0.1 fields:

- `feedback_schema_version`
- `source_verifier`
- `redacted`
- `merge_verdict`
- `can_merge_without_human`
- `decision`
- `mode`
- `trigger`
- `first_next_action`
- `fix_task`
- `capability_review`
- `finding_ids`
- `reviewer_feedback_requested`
- `artifacts`

The export is a design-partner and false-positive triage aid. It is derived
from verifier projections and does not include raw finding evidence. With
`--redact` (the default), local artifact paths are reduced to filenames so the
artifact does not leak usernames or confidential workspace directory names.

### Attestation

`agents-shipgate attest` derives a deterministic, local attestation from
`agents-shipgate-reports/verifier.json` (enriched from the sibling `report.json`
when present). The current schema is
[`docs/attestation-schema.v0.5.json`](docs/attestation-schema.v0.5.json). It
records the verdict, the report-derived capability delta, optional local
organization/CI context, detailed declared `human_ack` entries, a
policy-snapshot hash, content hashes of the verify artifacts, and capability
lock/diff hash bindings and the terminal receipt graph when verify emitted
those artifacts. It carries no
wall-clock timestamp — it is content-addressed by git SHAs and artifact hashes,
so re-deriving from the same inputs is byte-identical. It does not gate;
`release_decision.decision` remains the only gate. Current v0.5 fields:

`org bundle` accepts previously generated attestation files through the frozen
reader path. The emitted bundle projects a current v0.5 attestation and binds
the same request, receipt, decision, and artifact-set IDs.

With `--redact` (the default), `source_verifier`, capability lock/diff paths,
and artifact paths are reduced to filenames. Redaction does not remove explicit
organization/CI identity fields (`org.repo`, `org.actor`, `org.merge_sha`,
`org.workflow_run_id`, etc.); omit the corresponding flags or CI context when
those identities should not be recorded.

- `attestation_schema_version`
- `cli_version`
- `org` (`org_id`, `repo`, `service`, `tier`, `pr_number`, `workflow_run_id`, `actor`, `merge_sha`)
- `source_verifier`
- `redacted`
- `run_id`, `verify_run_sha256`
- `event_time`, `source_url`, `branch`, `base_sha`, `head_sha`
- `base_ref`, `head_ref`, `base_tree_sha`, `head_tree_sha`, `mode`
- `verdict` (`merge_verdict`, `decision`, `applicability`, `can_merge_without_human`)
- `capability` (`added`, `modified`, `removed`, `trust_root_touched`, `policy_weakened`, `change_ids`)
- `capability_lock` (`path`, `sha256`, `capability_lock_schema_version`, `semantic_capability_set_hash`, `evidence_set_hash`, `source_set_hash`, `capability_count`)
- `capability_diff` (`path`, `sha256`, `capability_lock_diff_schema_version`, base/head semantic hashes, `summary`) or `null`
- `human_ack` (`required`, `satisfied`, `outstanding`, `acks`)
- `policy_snapshot_sha256`
- `policy_packs[]` (`id`, `name`, `version`, `path`, `sha256`, `status`, `rule_count`)
- `artifact_sha256`

### Capability Lock And Diff

`agents-shipgate capability export` writes a stable local static capability
envelope to `.agents-shipgate/capabilities.lock.json` and, by default, a
byte-identical generated mirror at
`agents-shipgate-reports/capabilities.lock.json`. The current lock schema is
[`docs/capability-lock-schema.v0.8.json`](docs/capability-lock-schema.v0.8.json)
and emitted locks carry `capability_lock_schema_version: "0.8"` plus
`experimental: false`. Prior schemas stay frozen; a lock declaring `0.6` or
`0.7` is advanced to `0.8` on read, so nothing needs regenerating.

`agents-shipgate capability diff` compares two lockfiles and emits added,
removed, `reidentified`, semantic `changed`, and `evidence_changed` rows. The
current diff schema is
[`docs/capability-lock-diff-schema.v0.9.json`](docs/capability-lock-diff-schema.v0.9.json)
and emitted diffs carry `capability_lock_diff_schema_version: "0.9"` plus
`experimental: false`. `reidentified` is the scope/resource case: scope is part
of capability identity, so a scope escalation changes the id and is paired by
agent/provider/operation/tool identity instead of being reported as unrelated add/remove
churn.

The lock is an enumerable-tools envelope. Dynamic toolkit scope bounds are
disclosed by `source.toolkit_bound_count` but are not emitted as capability facts
yet. `cli_version` is provenance and may change on scanner upgrades; it is not
part of the semantic capability-set hash. Runtime trace evidence, findings, and
gate verdicts are intentionally excluded from capability locks and semantic lock
hashes.

Capability lock/diff artifacts are deterministic and carry no wall-clock
timestamp. They are stable non-gating artifacts for external integrations and
research; they are not emitted in `report.json` and do not gate.
`release_decision.decision` remains the only gate. Capability standard v0.2
adds each fact's optional normalized `semantic_assessment`; newly emitted v0.3
locks populate it. The v0.2 lock and v0.3 diff schemas remain frozen references
for archived artifacts. Legacy experimental
`capability_lock_schema_version: "0.1"` lock files remain readable by
`agents-shipgate capability diff`; the old combined schema remains a frozen
reference at
[`docs/capability-lock-schema.v0.1.json`](docs/capability-lock-schema.v0.1.json).
The public standard is documented in
[`docs/capability-standard.md`](docs/capability-standard.md).

`agents-shipgate verify` also writes the head static lock to
`agents-shipgate-reports/capabilities.lock.json` after a successful head scan.
When `--base` is provided and the base scan can be materialized, verify writes
`agents-shipgate-reports/base.capabilities.lock.json`,
`agents-shipgate-reports/capability-lock-diff.json`, and
`agents-shipgate-reports/capability-lock-diff.md`, and the PR comment includes a
compact semantic capability diff summary. If the base scan-derived lock is
unavailable, verify falls back to the reviewed committed lock at
`.agents-shipgate/capabilities.lock.json`; if both are unavailable, it records a
note and falls back to the existing `capability_review.top_changes[]` projection
without changing the release gate.

### Capability Payload (`shipgate.capability_payload/v1`)

`shipgate.capability_payload/v1` is the **frozen shared payload** of two planned
surfaces: the exported capability delta published as a standalone attestation,
and the committed capability state. It is published now, ahead of either, so
both serialize one structure instead of two. The schema is
[`docs/capability-payload-schema.v1.json`](docs/capability-payload-schema.v1.json)
and the spec is
[`docs/capability-payload.md`](docs/capability-payload.md).

**Nothing emits it yet.** No command writes it, no artifact carries it, and
`contract_version`, `report_schema_version`, the runtime contract JSON, and
`.well-known/agents-shipgate.json` are all unchanged by its publication. The
capability lock and lock diff above are unchanged. It is non-gating;
`release_decision.decision` remains the only gate.

**Validation is two stages, and the schema file is stage one.** Pydantic
cross-field rules do not appear in a generated JSON Schema, so every rule that
needs a *recomputation* is unexpressible there. Everything JSON Schema can
express has been pushed into the published file — required keys, closed enums,
the `view` discriminator, key/id/digest patterns, safe-range integers, strict
scalar types, non-empty and unique lists, the transition/sides/direction
coupling, the presence/transition coupling including which changes each side may
carry, and the permission shapes the classifier can produce. The rest is stage
two, enumerated in the spec page and in the schema's own `description`. A
consumer that runs only stage one does not have the guarantees below.

**The reference parser accepts exactly the schema's language.**
`CapabilityPayloadV1` uses strict scalars, so it does not coerce `"2"` to an
integer or `"false"` to a boolean where the published schema would refuse them.

What a consumer may rely on:

- One document, two views, discriminated on `view` (`state` | `delta`), both
  validated by the one schema file.
- **Every field of every object is required** — in the schema's `required`
  arrays, not merely in prose. A version field or a discriminator a consumer may
  omit and have repaired is not one. Fields are nullable where the evidence may
  not exist; null means "not stated", never "false".
- `subject.key` identifies a row, is unique across `subjects[]`, and is
  **recomputed from the row's own identity** on parse:
  `"capsubj_" + sha256(canonical_json({agent, provider, tool_id}))[:16]`, not
  from the subject kind. Two rows cannot split one logical tool between them.
- **Canonical bytes are fully specified**, because the format is for consumers
  that are not this program: UTF-8 and never escaped, object keys sorted, no
  insignificant whitespace, integers only inside the I-JSON safe range
  (`|n| ≤ 9007199254740991`), no non-finite numbers, and no fallback
  serialization. Every object key is ASCII — enforced, not assumed:
  `capability_id` is the one dynamic key and is constrained to
  `^cap_[0-9a-f]{16}$`, because Python orders keys by code point and RFC 8785 by
  UTF-16 code unit and the two disagree above the BMP. Within those constraints
  this agrees with RFC 8785, and the spec page publishes cross-language vectors.
- `summary`, each `subjects[].transition`, each `changed_dimensions`, each
  `semantic_direction` and `semantic_changes`, and a `state`'s three digests are
  **recomputed on parse**. A payload that disagrees with its own rows is
  rejected, not corrected. In particular `semantic_direction` is *derived from
  the two carried records* — it is the direction of what this payload publishes,
  not a producer's assertion — and `evidence_only` means exactly "the two
  records are equal apart from provenance".
- The two state refs are bound to the membership rows:
  `head.subject_count - base.subject_count` equals added minus removed subjects,
  and the same equation holds for capability counts over `added` / `removed`
  record transitions.
- `transition` is a statement about the subject's presence, carried as
  `present_in_base` / `present_in_head`, not about the kinds of its changes: a
  tool that keeps one operation and loses another is `modified`, because it is
  still there. Presence also bounds the changes — a subject absent from base can
  only carry `added` ones.
- A change entry cannot contradict its records: `changed` requires the same
  `capability_id` and `identity_hash` on both sides and `reidentified` requires
  different ones; `changed_dimensions` must be exactly the digests that differ;
  a membership change carries the matching direction and no dimensions; and
  `evidence_only` requires the two published records to be semantically equal —
  so a published permission expansion can never be labelled provenance-only.
- `permission` publishes the lattice's semantic half from the same classifier as
  `mcp audit`, in a shape the classifier can actually produce (`read` never
  pairs with a side-effecting class, `destructive` always carries `write`,
  unknown side effects always carry `unknown`). An unmeasured profile is
  `unavailable` with side effects unknown — never read-only.
- `capabilities[].capability_id` is the internal `CapabilityFactV1.id`
  verbatim, and is unique across the whole payload — provenance is keyed by it.
- `analysis_coverage` names the subjects the analysed surface left out. A
  `state` carries one coverage block; a **`delta` carries both sides** plus the
  recomputed `newly_outside_analysis` / `no_longer_outside_analysis`, because
  one snapshot cannot tell a newly added unbound tool from one that was already
  unbound — and only the first is something a reviewer of that diff must act on.
  `status` is `not_requested | unavailable | complete`, **neither of the first
  two means zero**, only `complete` may name subjects, and a comparison is only
  as established as its weaker side. The lists are deliberately not disjoint
  from `subjects[]`: a tool that lost its binding belongs to both.
- A state publishes three digests over its own published content —
  `capability_set_digest` (semantics), `evidence_set_digest` (provenance,
  including each record's `evidence_hash`), and `analysis_coverage_digest`.
  Together they bind everything the state publishes, so two payloads with
  matching refs are the same published state. A `state` verifies its own three
  on parse; a `delta`'s `base`/`head` refs name states it does not carry, so
  those are taken on trust — except each side's coverage digest, which the delta
  does carry and does check.
- A `delta` with no subject rows must name two states whose **capability and
  evidence** digests agree and whose counts are equal. `analysis_coverage_digest`
  is deliberately excluded: a change that only moves what could not be analysed
  has no subject rows by construction, and that coverage-only delta must stay
  expressible.
- No wall clock. Two projections of the same static inputs are byte-identical,
  in any process — permission class ordering is total, so nothing inherits
  hash-randomized set iteration.
- Every model forbids unknown properties. The fields the payload deliberately
  does not publish, and the reason for each, are listed in the spec page and in
  `agents_shipgate.core.capability_payload`.

**Evolution: `v1` is closed, and that is the whole policy.** This departs from
the additive-within-a-version rule the `report.json` schema follows, because the
two schemas make opposite promises. `report.json` is open by construction and
its consumers ignore what they do not know. This payload is closed by
construction — `additionalProperties: false` everywhere, closed enums
everywhere — so a `v1` validator *rejects* a new optional field and a new enum
value rather than ignoring them, and promising additivity would be promising
something the shipped validators do not do. Therefore: any addition, removal, or
change of meaning is `/v2`, published as a new schema file beside this one; `v1`
stays committed and readable for at least one minor cycle after `v2` lands
(the deprecation rule applied to a whole version, which is the unit here); and a
producer migrates by emitting both for a cycle rather than bending `v1`.

### Workflow-evidence capture

`agents-shipgate feedback capture` records a deterministic, local, replayable
*scenario* from a verify before/after pair — one real pilot loop turned into
benchmark fuel. The current schema is
[`docs/scenario-schema.v0.1.json`](docs/scenario-schema.v0.1.json). It does not
gate. With `--redact` (the default) it keeps only provenance (sha256, length,
diffstat) of the prompt / diff / transcript — never raw content — so it is safe
to share. Current v0.1 fields:

- `scenario_schema_version`
- `redacted`
- `prompt_class`
- `human_decision` (`merged` / `rejected` / `changes_requested` / `none` / null)
- `before` / `after` — per-side state (`merge_verdict`, `decision`,
  `applicability`, `can_merge_without_human`, `trust_root_touched`,
  `policy_weakened`, `capability`)
- `transition` — `verdict_before`, `verdict_after`, `resolved`,
  `introduced_trust_root_touch`, `introduced_policy_weakening`, and
  `suspected_gate_bypass` (`mergeable` while a trust-root touch or policy
  weakening is present — impossible for a valid verifier)
- `evidence` — `prompt` / `diff` / `transcript` provenance
- `source`

### Agent-skill paths

The following paths are part of the public agent surface and will not move within `0.x`:

- [`prompts/`](prompts/) — task-shaped recipes, individual filenames are stable
- [`.claude/commands/shipgate.md`](.claude/commands/shipgate.md) — Claude Code `/shipgate` slash command
- [`skills/agents-shipgate/SKILL.md`](skills/agents-shipgate/SKILL.md) — Claude Code skill. Frontmatter `name` is fixed at `agents-shipgate` (deliberately distinct from the `/shipgate` command so the skill cannot preempt it). Trigger phrases in `description` may broaden additively but will not narrow.
- [`skills/agents-shipgate/prompts/`](skills/agents-shipgate/prompts/) and [`skills/agents-shipgate/ci-recipes/`](skills/agents-shipgate/ci-recipes/) — bundled supporting files the skill references via relative paths. Filenames listed in `SKILL.md` are stable.

The body content of these files may change to reflect new prompts; the entry-point paths will not.

`agents-shipgate skill lint`, `agents-shipgate skill security`, and
`agents-shipgate skill review` are supporting/provisional review helpers in
`0.x`. They may inform skill and instruction review, but they are not the CI
release gate and should not be treated as a substitute for
`report.json.release_decision.decision`.

---

## What MAY change additively in any minor release

These are not stable — assume they may grow but not shrink:

- **Risk-tag taxonomy.** New tags may appear (e.g. `infrastructure_change`, `code_execution`). Existing tags' meanings will not change.
- **`capability_facts[].capability` vocabulary.** Values are an open vocabulary seeded from risk tags plus review sentinels such as `wildcard_tool_surface` and `unknown`.
- **Report `frameworks.{name}` blocks.** New framework summaries (e.g. `frameworks.langchain`) may appear, and new count keys may be added to an existing summary (e.g. `frameworks.google_adk.tool_binding_count`). Tool counts such as `function_tool_count` count tool *definitions*: one tool bound to three agents is one tool and three bindings, and only the binding count moves with the wiring.
- **Manifest fields.** New optional fields under existing sections.
- **Check default severities.** May tighten over time. To pin a severity for your repo, use `checks.severity_overrides`.
- **`release_decision.decision` enum values.** New states (e.g., `insufficient_evidence` added at `report_schema_version` 0.14) may be added. Consumers that switch on the enum MUST fall back to `review_required` for unrecognized values — that is the safe default. Existing values' meanings will not change. New states do not change CI exit codes (exit 20 still requires a `fail_on` match on actual findings).
- **`agent_summary.verdict` enum values.** Mirror `release_decision.decision`; same additivity and fallback rule.
- **`reviewer_summary.verdict` enum values.** Mirror `release_decision.decision` and `agent_summary.verdict`; same additivity and fallback rule. The three enums move in lockstep — adding a value to one without the others is a contract violation.
- **`reviewer_summary.first_recommended_surface.{kind, name}` enum values.** New surface kinds and names may be added (e.g., when a sixth reviewer lens or fourth audit envelope ships). Consumers that switch on `name` MUST fall back to "ignore the pointer and read every documented surface" for unrecognized values. The priority order between surfaces may also be revised additively when a new surface is added — the contract is the deterministic projection, not the specific ranking.
- **`verifier_summary.verdict` enum values** (v0.22+). Mirrors `release_decision.decision`; same additivity and fallback rule. It joins `agent_summary.verdict` and `reviewer_summary.verdict` in the lockstep set — adding a value to one without the others is a contract violation.
- **`capability_change` member enum values** (v0.22+; semantic direction v0.23+): `direction` (`added | removed | broadened | narrowed`), `semantic_direction` (`added | removed | broadened | narrowed | mixed | unknown | evidence_only`), `subject_kind` (`tool | action | scope | policy | ci | baseline | agent_instruction | manifest | unknown`), and `release_impact` (`none | informational | review_required | blocks_release | insufficient_evidence`). New values may be added additively; consumers that switch on them MUST fall back to a conservative default (treat unknown `release_impact` as `review_required`, unknown `subject_kind` as `unknown`, unknown `semantic_direction` as `unknown`).
- **`protected_surface_changes[].kind`** (v0.22+) — the trust-root surface bucket (e.g. `manifest`, `policy`, `ci_gate`, `agent_instructions`, `trigger_catalog`). New buckets may be added as new trust-root classes ship; treat unknown kinds as "a protected surface was touched — review it".

---

## What MAY change in any minor release

These are explicitly NOT part of the public contract:

- **Internal module layout** under `src/agents_shipgate/`. Importing from non-public modules will break.
- **Legacy internal schema imports** such as `agents_shipgate.core.models`,
  `agents_shipgate.config.schema`, `agents_shipgate.core.patches`, and
  `agents_shipgate.packet.models`. Public wire-contract models live under
  `agents_shipgate.schemas.*`; internal scan/domain containers live under
  `agents_shipgate.core.*` and are not a stable consumer API.
- **Markdown report layout.** Section ordering, exact wording, and table format may change. Parse the JSON report instead.
- **Risk classifier keyword sets** in `core/risk_hints.py`. False positives are tuned over time. To pin specific behavior, use `risk_overrides.tools.{tool}.{tags,remove_tags}` in your manifest.
- **Default `init` template.** The starter manifest format may grow new sections.
- **`CheckMetadata.evidence_fields`** content. New keys may be added to a check's evidence dict.

If you need stability guarantees beyond what's listed here, please open an issue describing the use case.

---

## Versioning

We follow [SemVer](https://semver.org/) loosely:

- **Patch** (`x.y.Z`): bug fixes only. No new features, no breaking changes.
- **Minor** (`x.Y.0`): new features (new checks, new input loaders, new flags). Adheres to this contract.
- **Major** (`X.0.0`): may break the contract. Will be announced with a migration guide.

Artifact schemas version independently of the package. `report_schema_version`
is frozen at `1.0` and follows the `1.x` rules above; a deprecation cycle is
counted in shipped releases, never in elapsed time on unreleased `main`.

The current version is in [`pyproject.toml`](pyproject.toml). Changelog is in [`CHANGELOG.md`](CHANGELOG.md).

---

## Reporting a contract violation

If you encounter behavior that contradicts this document — for example, an unsuppressed finding for a deprecated check ID, or a stable JSON field that disappeared — please [open an issue](https://github.com/ThreeMoonsLab/agents-shipgate/issues/new) with:

1. The version of `agents-shipgate` (`agents-shipgate --version`)
2. The expected behavior per this document
3. The observed behavior (output, error message, JSON fragment)

Stability bugs are prioritized.
