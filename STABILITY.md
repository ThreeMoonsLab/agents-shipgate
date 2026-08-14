# Stability Contract · 0.16.0b7

What agents and CI integrations can rely on across versions of Agents Shipgate.

This document is the contract. If the runtime ever diverges from what's documented here, that's a bug — please file an issue.

Shipgate is pre-1.0. The CLI surface, exit codes, and `contract_version`
described here are stable within the `0.x` line, but the `report.json` schema
(`report_schema_version`, currently `0.34`) is still additive-versioned and
not yet frozen. A `1.0` line will not begin until the report schema reaches
`1.0` and holds without a breaking change. Pin a version (or the Action tag)
for reproducible CI.

---

<a id="migration-note-unreleased-setup-control-envelope"></a>

## Migration Note: unreleased — one control vocabulary across the setup commands

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

## Migration Note: unreleased — commands spelled for the invocation that emitted them

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

## Migration Note: unreleased — the compact control envelope

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
`agent_control_budget_bytes` (4096) — a measured target, not an enforced cap.
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

## Migration Note: unreleased — publish authority is not merge authority

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

## Migration Note: unreleased — diff input health

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
nowhere and becomes an unsuppressible identity evidence gap. Finding
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

## What WILL NOT change in the current `0.x` line

### CLI command surface

These commands and flags are stable across the current `0.16.x`
contract line. Future `0.x` versions may make deliberate breaking
changes only by bumping `contract_version` and updating this file.

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

- `report_schema_version` — bumps minor on additive changes, major on breaking
- `release_decision.{decision, reason, blockers, review_items, evidence_coverage, baseline_delta, fail_policy}` (v0.8+)
- `release_decision.evidence_coverage.semantic_coverage.{total_actions, pass_eligible_actions, gap_count, review_concern_count, reason_counts}` (v0.29+) — zero-tolerance semantic pass coverage. It is derived from normalized action assessments and contributes directly to the release decision; it is not suppressible or baseline-able.
- `release_decision.evidence_coverage.policy_gap_count` and `policy_evidence_gaps[]` (v0.33+) — zero-tolerance policy-applicability gaps for heuristic-only, mixed, unknown, or conflicting predicates. They remain outside Findings and cannot be suppressed, baselined, acknowledged, severity-overridden, or hidden by `--no-heuristics`.
- `release_decision.{static_analysis_only,runtime_behavior_verified,static_verdict_disclaimer}` (v0.29+) — explicit machine boundary for every verdict. Current emitted values are `true`, `false`, and the canonical disclaimer that the static scan did not execute the agent or prove runtime behavior, tool routing, credential enforcement, or safety.
- `release_decision.evidence_coverage.evidence_gaps[]` (v0.26+; semantic gap kinds added v0.29) — deterministic, human-routed remediation rows. v0.29 adds `incomplete_surface`, `missing_effect_evidence`, `inferred_effect_only`, `conflicting_effect_evidence`, `missing_authority_evidence`, `partial_authority_evidence`, `conflicting_authority_evidence`, and `invalid_semantic_annotation`, plus next-action kinds `declare_action_effect`, `declare_action_authority`, `provide_complete_inventory`, and `resolve_semantic_conflict`. Semantic declaration placeholders always carry `suggested_patch_kind="manual"`, `auto_apply=false`, and `requires_human_review=true`; they never enter `apply-patches`.
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
- `agent_summary.{verdict, headline, blocker_count, review_item_count, auto_appliable_patches, needs_human_review, first_recommended_action}` (v0.12+) — top-level deterministic projection of `release_decision` + per-finding `agent_action`. Lets a coding agent read one block instead of traversing arrays. `first_recommended_action` is `{kind: "command" | "info", command: string | null, why: string}`; the `command` form carries an actual CLI invocation, the `info` form is a "surface this to the user" hint. Same inputs always produce the same output; this block cannot disagree with the underlying `release_decision` and `findings[].agent_action`.
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
verdicts it lands on — is the first structured action in
`release_decision.evidence_coverage.evidence_gaps[]`. For supported frameworks,
that action names the generated local inventory artifact and the exact
`<framework>.tool_inventories` manifest route. Only unidentified or unsupported
source shapes receive generic MCP/OpenAPI/inventory guidance. Apply the reviewed
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

`agents-shipgate verify --preview --json` is a lightweight relevance check: it
runs no scan, requires no manifest, exits 0, and emits a `verifier.json` with
`mode: "preview"`, `execution: "not_run"`,
`applicability: "not_evaluated"`, and
`control.state: "agent_action_required"`; `control.next_action` carries the
next recommended action. The handoff uses `operation: "verify_preview"` and no
release decision. That action may be `detect`/`initialize` for
relevant unconfigured repos, or `verify` for configured repos. Use it as the
first touch on a repo or PR before committing to a full scan.

`verifier.json` is governed by [`docs/verifier-schema.v0.7.json`](docs/verifier-schema.v0.7.json).
Verifier v0.1 through v0.6 remain frozen references. It remains an orchestration artifact: `release_decision.decision` in
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
  capability_changes_added, capability_changes_removed,
  capability_changes_modified, top_changes[]}`. `top_changes[]` carries the
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
best-effort after writes.

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
  rows, and the resulting release decision are unchanged by the split.
- `SHIP-VERIFY-BASELINE-OR-WAIVER-EXPANDED` (high, floor high) — a new
  suppression, a widened waiver scope, or a larger accepted-debt baseline
  versus the base report.
- `SHIP-VERIFY-CI-GATE-REMOVED` (critical, floor high) — a Shipgate CI
  workflow path is in the changed files and no longer exists on disk (the PR
  deleted the gate).
- `SHIP-VERIFY-AGENT-INSTRUCTIONS-WEAKENED` (medium, floor medium) — an
  agent-instruction trust root changed; Shipgate cannot statically prove the
  instructions were not weakened, so it routes to human review.
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

### Release Evidence Packet (v0.12)

`agents-shipgate-reports/packet.json` is a supporting/provisional reviewer
artifact governed by [`docs/packet-schema.v0.12.json`](docs/packet-schema.v0.12.json).
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
[`docs/agent-handoff-schema.v6.json`](docs/agent-handoff-schema.v6.json) with
`schema_version: "shipgate.agent_handoff/v6"`. v1 through v5 remain frozen
references.

The handoff artifact is derived only from `verifier.json`, `verify-run.json`,
and `report.json`. It mirrors `release_decision.decision`,
`verifier.json.merge_verdict`, and
the byte-identical `verifier.json.control` object. Handoff v6 also mirrors the
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
[`docs/capability-lock-schema.v0.4.json`](docs/capability-lock-schema.v0.4.json)
and emitted locks carry `capability_lock_schema_version: "0.4"` plus
`experimental: false`.

`agents-shipgate capability diff` compares two lockfiles and emits added,
removed, `reidentified`, semantic `changed`, and `evidence_changed` rows. The
current diff schema is
[`docs/capability-lock-diff-schema.v0.5.json`](docs/capability-lock-diff-schema.v0.5.json)
and emitted diffs carry `capability_lock_diff_schema_version: "0.5"` plus
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
for archived artifacts. Old experimental
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

- **Patch** (`0.5.x`): bug fixes only. No new features, no breaking changes.
- **Minor** (`0.x.0`): new features (new checks, new input loaders, new flags). Adheres to this contract.
- **Major** (`1.0.0`): may break the contract. Will be announced with a migration guide.

The current version is in [`pyproject.toml`](pyproject.toml). Changelog is in [`CHANGELOG.md`](CHANGELOG.md).

---

## Reporting a contract violation

If you encounter behavior that contradicts this document — for example, an unsuppressed finding for a deprecated check ID, or a stable JSON field that disappeared — please [open an issue](https://github.com/ThreeMoonsLab/agents-shipgate/issues/new) with:

1. The version of `agents-shipgate` (`agents-shipgate --version`)
2. The expected behavior per this document
3. The observed behavior (output, error message, JSON fragment)

Stability bugs are prioritized.
