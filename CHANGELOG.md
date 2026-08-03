# Changelog

## Unreleased

- **A reviewer-requested version sync no longer stops the turn.** Editing an
  agent-instruction document was classified whole-file, so bumping a contract
  or schema number in `AGENTS.md` — work a human reviewer had just asked for —
  produced the same `critical`, human-routed stop as deleting the instruction
  that protects the gate. Because conversational acknowledgement never changes
  control state, the only thing an agent could do with that stop was ask the
  operator a question whose answer nothing reads. `preflight` and `check` now
  recognize one narrow shape they can prove is not a weakening: a diff over an
  agent-instruction **prose document** (`.md`/`.mdc`) whose removed and added
  lines pair 1:1 and are byte-identical once version literals are masked, where
  every new literal is a version this CLI itself publishes (sourced from the
  `contract --json` payload, so a contract bump cannot leave it recognizing
  stale numbers). Prose is provably preserved, so no instruction can have been
  removed, added, reordered, or reworded. A bare integer only counts as a
  version literal on a line that says `version`/`contract`/`schema`, which
  keeps "require at least N approvals" out of the exception. Machine-consumed
  surfaces (`.claude/settings.json`, `.mcp.json`, hooks), every other
  whole-file trust-root class (`ci_gate`, `policy`, `host_boundary`), and
  added/deleted/renamed files keep the standing route. As in PR #282, a
  per-record safe result never clears the path-wide guard: a document touched
  by more than one diff record stays human-routed in both orderings. The
  concrete diff still carries its verify-mode trust-root findings into review.
- **A protected-surface stop now says what would clear it.** A human-routed
  preflight signal stated only that a coding agent must not self-approve the
  edit, which gave the reader no way to tell a refusable plan from a missing
  input — the failure mode above. Each signal now names the concrete outcome:
  the refusal reason when a diff was assessed (`'v9999' is not a version this
  CLI publishes`, `an edited line changes prose outside its version literals`),
  or the missing input when preflight was handed paths without a diff. The text
  propagates to `control.reason`, `control.stop_reason`, and
  `first_next_action.why`. The recommendation also states the non-route
  explicitly — approval goes through the pull request, and the agent must not
  ask the operator to approve the edit in chat — so a stop can no longer be
  mistaken for a prompt to seek conversational sign-off.

- **A first adoption no longer reads as a policy weakening.** Adding the
  manifest to a repository that had none is the first verdict every new adopter
  sees, and it said "This PR weakens the release policy that evaluates it",
  carried a finding titled "Policy change cannot be proven safe (no base
  snapshot)", and — because a missing-manifest base was classified as a safe
  recovery — shipped no `fix_task` at all, so nothing named the act that would
  clear it. `verify` now proves adoption from git (the comparison base carries
  no manifest under any name and no YAML that *parses* as one — a text probe
  missed a valid manifest with quoted keys — so neither a *moved* manifest nor
  a base that quietly keeps one can pass itself off as a first adoption) and says so: same check id, same `medium` severity, same
  `human_review_required` state, new evidence kind `manifest_introduced`, and a
  `fix_task` whose leading instruction names the exact configured manifest.
  Only when adoption is the sole gating concern does that instruction say to
  merge the adoption through a human-reviewed PR; blockers, insufficient
  evidence, and additional review items lead with their own stop condition.
  `check` gets the same correction locally, keyed on the diff carrying exactly
  one manifest record and that record being a plain addition. Adoption remains
  a human decision — only the claim about what happened changed.
- **The manifest a run actually loaded is a trust root.** The trust-root table
  only knew `**/shipgate.yaml`, so a repository run with `--config new-gate.yml`
  had no manifest trust root at all: the file defining its gate could be
  introduced or rewritten without a single finding, leaving the release
  substrate empty. With a clean scan that produced `passed` / `mergeable` /
  `complete` — beneath an adoption headline that said a human was required.
  Whatever a run loads as its gate is now classified as one, in both the
  trust-root check and the policy fail-safe — and in `check` and `preflight`,
  which classified it no better: a diff for a custom-named manifest returned
  `allow` with no violations locally and no protected touch in preflight — the
  local check dropped it from the diff entirely before any evaluator saw it —
  and both then recommended a verify command for the default `shipgate.yaml`.
  Identity is compared on normalized, containment-checked paths, so an
  equivalent spelling (`docs/x/../manifest.yaml`) cannot slip past, and
  preflight classifies the *source* side of a rename, which is where the gate
  sits when a diff moves it out from under itself. The
  classification is recorded on the boundary row so a gate-governing surface
  stays out of the graded agent route regardless of its name, and the evidence
  carries the changed path rather than the resolved config path, which for a
  committed-head run is a temporary archive location that would make two
  identical runs produce different fingerprints.
- **`check` detects which agent is running it.** `--agent` defaulted to `codex`
  and never consulted the harness variables Shipgate already reads to switch on
  agent mode, so every Claude Code and Cursor run recorded the wrong actor in
  its result and audit id. Detection now comes from one table that also defines
  those hints, so the two cannot drift; an explicit `--agent` still wins, and a
  plain shell still gets `codex`.
- **`check`, `audit`, and `preflight` honor the agent-mode error contract.**
  The skills and slash command tell agents that with
  `AGENTS_SHIPGATE_AGENT_MODE=1` a failing command emits a structured
  `next_action` line on stderr; these three printed prose only, so an agent that
  mis-invoked them had to parse English. Each error path now emits the line with
  its `exit_code`, and `preflight` no longer lets an unexpected failure escape
  as a bare traceback.
- **A rerun command that actually reruns.** The `fix_task` verification command
  omitted `--config`, substituted `origin/main` for a base the run never used,
  and always appended `--head HEAD`. In a repository with a nested manifest it
  re-ran against a different gate, and for an uncommitted first adoption it
  switched to the committed tree — where the new manifest does not exist — and
  exited 2. It now emits the config always, the base only when one was used,
  and no `--head` for a working-tree run — plus the rest of the evaluated
  request (policy packs, baseline, `--ci-mode`, plugin and heuristic modes, and
  an explicit `--no-base`), because a rerun that drops them evaluates a
  different question than the one whose findings it is meant to reproduce. The
  structured adoption repair now names the resolved config path instead of a
  hardcoded `shipgate.yaml`, and the command carries the resolved `--workspace`
  and a non-default `--out`, so a rerun from another directory evaluates the
  same checkout and writes to the same place. `check`'s recovery command is
  rebuilt from the failing request with only the invalid field corrected —
  a fixed command discarded actor, workspace, config, policy, and diff context
  — and a request whose diff came from stdin gets a review action instead of a
  command that cannot be replayed.
- **Preflight recovery keeps the request it failed on.** Every preflight error
  recommended a bare `agents-shipgate preflight --json`, discarding workspace,
  config, plan, diff, and capability request: following it after a failed
  targeted run evaluated the current repository with an empty plan and returned
  `control.state=complete`. The recovery action now reproduces the actual
  invocation, and offers no command at all when the request came from stdin or
  mixed `--plan` with the per-flag inputs — replaying a request-shape conflict
  can never satisfy its own `expects`. `check`'s recovery is one quoted
  serializer for every path, including diff-input failures, which previously
  joined user-controlled paths and refs unquoted into a published *authorized*
  command; commands the CLI emits for its own targets now name the workspace
  and the manifest, with the config rendered the way `verify` resolves it —
  relative to the repository root — so a nested manifest is no longer verified
  against the root gate.
- **Detached diffs never authorize checkout-dependent verification.** A diff
  supplied by file, stdin, or the read-only MCP adapter can be evaluated for
  diagnostics, but it is not proof of the bytes a later `verify` command would
  read. When such a result owes verification, control now stops with no
  allowed command and the summary says to rerun against the intended worktree
  or a complete ref range. MCP preflight also rejects a `plan` mixed with
  direct request fields instead of silently discarding one input source.
- **A failed baseline is never recovered by overwriting it.** A malformed,
  unknown-schema, or integrity-failed host-grants baseline recommended
  `--save-baseline` against the same path, which replaced the failed artifact
  with the *current* grants — acknowledging them unreviewed and destroying the
  evidence a human needed. Those now route to review. A genuinely absent
  baseline also routes to a human because creating the first baseline
  acknowledges the current grants; a failed read-only drift request never
  authorizes that state-changing decision.
- **Host-audit filesystem failures follow the catalog.** A `--baseline-file`
  naming a directory raised `IsADirectoryError` through typer as a traceback
  and exit 1. Filesystem failures on both `--baseline-file` and `--out` are now
  `other_error` with exit 4, as `docs/errors.json` specifies — they were
  briefly reported as `config_error`/2, which sends an agent back to re-read
  flags that were fine. The baseline *reader* needed the same treatment through
  its `__cause__`, since the loader converts every read `OSError` into
  `ValueError`; a genuinely missing baseline remains `config_error`/2 but now
  carries a review-only route for the first acknowledgement.
- **The audit id distinguishes the actor.** Detecting the calling agent changed
  the label in the result but not the digest — the central one omitted the
  actor and the legacy one hardcoded `codex` — so identical evaluations by
  Claude Code, Cursor, and Codex shared an `audit_id`, which is exactly the
  attribution problem actor detection exists to solve. Legacy replayable
  provided-diff Codex ids keep their established shape. Non-default actors add
  actor identity; worktree, ref-range, and detached evaluations also bind
  input/control replayability, so those ids intentionally rotate. Semantic
  control state is hashed without checkout-specific command paths.
- **Static control inputs now fail closed on identity and resource ambiguity.**
  Local check, preflight, host audit, installed hooks, and verifier Git
  collection bind exact non-symlink, singly-linked regular files; manifest,
  policy, baseline, trust-root, diff, and Git inventories have byte or entry
  ceilings. Executable filters, repository diff drivers, hidden index flags,
  source-like binary diffs, malformed/coherence-breaking diff records, and
  filesystem-portability collisions stop instead of silently dropping source
  text. Prior verifier output is excluded from a worktree request so an
  identical rerun does not hash its own artifacts.
- **Portable host instructions are protected consistently.** Boundary
  matching is case-insensitive and hierarchical for `AGENTS.md`,
  `AGENTS.override.md`, and `CLAUDE.md`; a case variant or nested copy cannot
  acquire authority only after checkout on another host. Symlink directories
  that could conceal a leading-`**/` trust root make host inventory and
  preflight incomplete and route to human review.
- **Mechanical repair authorization is subject-bound.** A coding-agent repair
  route now requires an applicable high-confidence non-manual patch against a
  worktree subject. A ref-bound verifier cannot authorize a patch command that
  would edit the checkout and then rerun the unchanged commit; it routes that
  repair to a human instead.
- **Adoption wording stands down when something was genuinely weakened.**
  Introducing the manifest while editing an existing policy file produced a
  `base_snapshot_unavailable` finding under a headline saying there was no
  prior gate to weaken, and dropped the `review_policy_weakening` repair. The
  pure-adoption wording now requires that nothing else needing review changed.
  The adoption proof itself also stopped resting on two basenames: a base that
  simply keeps an operational manifest under another name deletes nothing and
  matches no name check, so absence is now established by content.

- **A way out of `insufficient_evidence` (#292).** An abstention was
  unactionable in practice: the decision engine generated the exact manifest
  snippet each evidence gap wants, but those snippets were only reachable by
  walking `report.json`, so a three-line, one-time task looked like schema
  archaeology and repositories stayed abstained indefinitely.
  `suggested-declarations.yaml` now assembles them next to the report — merged
  per target, so two gaps on one tool produce one pasteable row instead of two
  invalid ones — and every gap that carries a template names the file in its
  `expects`. Every human-owned value stays `<REVIEW_REQUIRED>`, and the file
  states that a block still containing a sentinel closes nothing. Verified end
  to end: filling the scaffold clears `inferred_effect_only` and
  `missing_authority_evidence` and moves the verdict off abstention.
- **Unfilled scaffold placeholders are rejected by the manifest.** The scaffold
  states that a block still containing `<REVIEW_REQUIRED>` closes nothing, but
  the manifest only checked fields like `authority.auth_type` for
  non-blankness — so a pasted-but-unfinished block loaded and was assessed as
  reviewed evidence, moving a fixture from `insufficient_evidence` to
  `review_required` on placeholders alone. The loader now rejects the sentinel
  wherever it appears and names each unfilled path, so an unfinished scaffold
  cannot change a verdict.
- **The authority template was unfillable.** It offered `authority.mode`
  alone, but the manifest requires `auth_type` for every mode except `none`
  and non-empty `scopes` for `scoped` — a reviewer following it exactly got a
  config error. Surfacing the templates is what exposed it. The template now
  names the co-required fields, and a regression test validates the shipped
  shape against the manifest schema.
- **Evidence gaps say whether this diff caused them.** `verify` already scans
  the base; it now compares the base and head gap sets and reports whether the
  diff introduced a gap or inherited it, so a docs-only turn stops reading as
  an accusation about the current change. The verdict is deliberately
  unchanged: evidence coverage is a property of the whole evaluated surface,
  and a diff that appears to touch nothing is exactly what an unseeable
  capability change looks like, so the diff can never argue an abstention away.
  `docs/engineering/insufficient-evidence-cold-start.md` records why the
  diff-scoped variant was rejected.
- **Framework-correct low-confidence remedy.** The advice named
  `tool_inventories` for every framework, but only four have that key;
  `openai_agents_sdk` — the quickstart framework — has none, so readers were
  sent after a key the schema rejects. The remedy now names the real key when
  one exists and the supported alternative when it does not.

- **Graded local boundary stop (UX P0, contract v19, `0.16.0b7`).** The
  local `shipgate check` previously projected every `require_review`
  boundary violation onto the same `human_review_required` +
  `must_stop: true` control state as a `block` — a CLAUDE.md comment, an
  unknown `.claude/settings.json` key, and a critical grant expansion were
  operationally identical, which routinely hard-stopped coding agents on
  user-requested benign edits. Contract v19 routes a `require_review` set
  that is entirely low/medium risk to
  `control.state: "agent_action_required"` with the exact verify command;
  the review obligation is preserved in the new additive `pending_review[]`
  field on `shipgate.agent_boundary_result/v1` and re-asserted by PR-time
  verify, whose `release_decision` branching is byte-identical. The band is
  fail-closed: `block` actions, `critical` risk, incomplete or unparseable
  input, gate-weakening rules, experimental surfaces, and every
  gate-governing trust-root class (`manifest`, `policy`, `ci_gate`,
  `shipgate_state`) keep the human stop. Root/case-variant
  `AGENTS.md`/`AGENTS.override.md`/`CLAUDE.md` instruction identities do too,
  preserving the composite-diff guarantee from the agent-authored proposal
  work. The deprecated
  `codex-boundary-json` format grades identically; its frozen v2 schema
  does not carry the new field.
- **Stop hook follows `control.state` (`0.16.0b7`).** The installed Claude
  Code Stop hook blocked the agent's stop on any non-`passed` release
  decision — but a Stop-hook block forces the agent to KEEP working, which
  is exactly wrong for `human_review_required` (`must_stop: true` means
  "end the turn and hand off to a human"). The hook now mirrors the
  operational contract: `complete` ends the turn silently,
  `agent_action_required` blocks once and names the one exact remaining
  command, `human_review_required` prints a hand-off notice and lets the
  turn end. Unparseable or unrecognized verifier output warns loudly, is
  never cached by the verified-signature short-circuit, and is never
  treated as passing; the cold-start no-manifest case advises
  `verify --preview` instead of forcing continuation. Reinstall hooks to
  pick up the new behavior.
- **Own-repo CI verify gates on `blocked,unknown` again (`0.16.0b7`).** The
  0.16.0b3-era expansion of `fail_on_merge_verdicts` to include
  `human_review_required` and `insufficient_evidence` (#274) made every
  trust-root-touching PR — including routine release pin sweeps that bump the
  plugin manifests — permanently red: no verifier mechanism can clear a
  `human_review_required` merge verdict, and the verdict's own semantics are
  "release is allowed but the human reviewer should weigh in." Both advisory
  workflows now fail only on `blocked` and `unknown` (fail-closed against
  parse/contract breakage); review routing remains visible in the uploaded
  verifier artifact and the PR reviewer stays the deciding human. The
  GitHub Action's own defaults are unchanged.
- **Version advances (`0.16.0b7`).** Runtime contract `18 → 19`. All other
  schema versions are unchanged; `pending_review[]` is additive on the
  regenerated `agent-boundary-result-schema.v1.json`, and
  `minimum_control_contract_version` stays `14` — the `AgentControl` union,
  its fixed `must_stop`/`completion_allowed` literals, and the release
  gating signal are untouched.
- **Reproducible verification identity (P0, `0.16.0b6`).** Verify now binds
  the resolved Git subject, exact input blobs, evaluation date, behavior
  options, installed engine-content and dependency/adapter/plugin/policy set, normalized task,
  executor, assembled decision, and complete artifact set through
  content-addressed IDs. Exact Git objects are materialized with `ls-tree` and
  `cat-file`; base-cache reuse is invisible to public artifacts and guarded by
  a content hash.
- **Terminal receipts and portable execution boundary.** Successful verify runs emit
  `verification-plan.json`, a decision-free `verification-unit-result.json`,
  `verification-artifacts.json`, and, last, `verification-receipt.json`.
  Workers validate their installed engine and transported inputs but cannot
  assert a verdict; the verifier remains the sole policy engine and the
  assembler re-closes its decision. `verification
  prepare|worker|assemble|reproduce` exposes the portable v1 protocol without
  claiming distributed policy evaluation, arbitrary sharding, or parallel
  speedup.
- **Externally rooted exact-operation authorization (contract v18).** A trusted
  host can derive an unsigned authorization request from a host-attested
  `review_required` receipt, authenticate a human, and return a short-lived
  Ed25519 grant for one exact force-with-lease Git push. Agents Shipgate ships
  no signing or approval command. A second verification recomputes the complete
  request, decision, review set, repository, and tree identities before an
  accepted grant exposes only the guarded `authorization execute` consumer;
  the release decision, merge verdict, and completion authority remain
  unchanged. The executor revalidates current evidence and expiry immediately
  before using an isolated Git object store. Authorization requires an exact
  plugins-disabled engine, rejects third-party plugin loading in the broker,
  and parent-streams the Git pack with bounded stdout, stderr, and time.
  Authorization remains disabled
  without a host-protected trust policy, launcher, interpreter, entire virtual
  environment and `site-packages` tree, dependencies, credentials, and
  separately installed distribution; same-UID modes and editable workspace
  installs are not a trust boundary.
- **Identity and authorization contract versions.** Runtime contract advances
  to v18; report to v0.34; packet to v0.12; verifier to v0.6; verify-run to v3;
  handoff to v6; attestation to v0.5; registry to v0.4; organization evidence
  bundle to v2;
  downstream local contract to v7; and safety qualification formats to v4.
  Verification plan, unit-result, artifact-manifest, and receipt schemas begin
  at v1. The authorization request, signed grant, verifier evaluation, and
  external trust-policy schemas also begin at v1. Prior schemas remain frozen
  readers.
- **Immutable CI subject.** The GitHub Action evaluates `github.sha` by default
  and treats the default pull-request synthetic merge as authorization-
  ineligible. Push authorization requires a separate verification of the exact
  PR head commit. The Action exports receipt, request, decision, and
  artifact-set identities only after validating every terminal artifact hash.
- **Non-forgeable trust decay.** The content-bound commit evaluation date
  remains reproducibility provenance, but cannot extend reviewer-owned trust.
  Baseline, acknowledgement, and severity-override expiry use the later of that
  date and the verifier wall clock, so a forged backdated commit fails closed.
- **Agent-authored coverage proposals (contract v18 clarification).** Preflight
  and local control now distinguish proposal authorship from approval for one
  narrow manifest shape: an exact append-only addition of valid built-in
  `tool_sources` rows may be authored by a coding agent and routed to verify.
  Existing rows, all other manifest values, authority-bearing fields, custom
  adapters, and unsafe paths remain human-routed; the concrete trust-root diff
  still requires reviewer approval. Conventional test/golden fixtures are no
  longer inferred as undeclared deployed surfaces unless the manifest
  explicitly declares them. No schema or runtime-contract version changes.
- **Codex marketplace coverage and plugin-path containment.** Local plugin
  packages reached through a declared Codex marketplace now count as declared
  tool surfaces for local-control routing, and detect/init plus the zero-install
  detector no longer propose redundant direct-package rows for those roots.
  Direct-package loading now hard-rejects a source whose
  `.codex-plugin/plugin.json` symlink target escapes the manifest directory;
  marketplace entries with the same escape are skipped and cannot grant
  coverage or supply verification bytes. Malformed, non-UTF-8, oversized,
  remote, or escaping marketplace inputs stay fail-closed. No schema or
  runtime-contract version changes.

- **Evidence-basis policy gate (P0, `0.16.0b5`).** Semantic claims and risk
  hints now carry a typed evidence basis, stable claim IDs, and derived policy
  eligibility. Policy-pack and action-policy predicates evaluate to
  `matched | not_matched | indeterminate | conflicting`; rule severity,
  confidence, `block: true`, manual tags, and risk overrides cannot upgrade
  heuristic or incomplete evidence into an authoritative finding.
- **Non-waivable policy applicability gaps.** Heuristic-only, mixed, unknown,
  or conflicting applicability is emitted outside Findings and routes to
  `insufficient_evidence`. Baselines, suppressions, severity overrides,
  acknowledgements, and `--no-heuristics` cannot hide it. Supported findings
  expose deterministic predicate support and a `support_hash`; baseline v0.8
  requires that hash to remain equal. Pre-v0.8 baselines cannot supply that
  binding, so supported findings re-gate as new until a human reviews the new
  evidence and re-runs `agents-shipgate baseline save`.
- **Evidence contract versions.** Runtime contract advances to v16; report to
  v0.33; packet to v0.11; verifier to v0.4; handoff to v4; policy pack to
  v0.4; capability standard to v0.5; lock/diff to v0.6/v0.7; action snapshot
  to v0.4; downstream local contract to v5; and safety qualification formats
  to v3. Existing finding fingerprints and all prior schema files remain
  frozen.

- **Complete zero-config multi-host boundary (`0.16.0b4`).** The local
  boundary check now evaluates every recognized changed Codex, Claude Code,
  Cursor, VS Code MCP, shared instruction, and GitHub workflow surface through
  one static assessment. `--agent` identifies the caller and can no longer be
  used as a coverage selector. Untracked, deleted, malformed, unreadable,
  symlinked, and oversized protected inputs fail closed instead of disappearing
  from the result.
- **Host-neutral boundary contract.** Runtime contract advances to v15 and the
  canonical check format becomes `agent-boundary-json` with schema
  `shipgate.agent_boundary_result/v1`. The frozen
  `shipgate.codex_boundary_result/v2` projection remains available through the
  deprecated `codex-boundary-json` spelling for the `0.16.x` line. The shared
  `AgentControl` contract remains v14.
- **Evidence-bearing host inventory.** Host inventory, baseline, and drift
  advance to v0.2 with typed redacted grants, artifact parse status, per-host
  coverage, explicit excluded scopes, and incomparable migration for v0.1
  baselines. Repository scope remains deterministic and default; the explicit
  `local-static` audit scope reads supported local configuration without
  executing hosts, helpers, tools, user code, or network calls.
- **Boundary beta hardening.** Visible permission-mode and sandbox grant values
  now use the same recursive secret redaction as their hashes, so inventories,
  saved baselines, and drift reports cannot persist raw credential-bearing
  strings. Incomparable host baselines route preflight and organization status
  to human review instead of appearing clean. Protected-path classification is
  case-insensitive and retains nested Codex, MCP, and GitHub-workflow copies.
- **Correction to the original host-governance claim.** Earlier documentation
  overstated the first host-audit cut as the effective/current grant set. The
  contract is static and scope-bound: repository results cover
  repository-declared surfaces, while local-static results still exclude
  session approvals, invocation flags, UI state, remote managed settings, and
  runtime enforcement.

- **Unambiguous agent control contract (P0, `0.16.0b3`).** Check, preflight,
  verify, handoff, MCP, verify-run, and GitHub Action projections now share one
  schema-enforced `AgentControl` state: `complete`, `agent_action_required`, or
  `human_review_required`. Pending verification, installation, safe repair, and
  input recovery no longer coexist with completion or a human stop;
  conversation-level acknowledgement cannot clear a control obligation.
- **Control contract versions.** Runtime contract advances to v14; boundary
  result to `shipgate.codex_boundary_result/v2`; verifier to v0.3; handoff to
  `shipgate.agent_handoff/v3`; preflight to v0.3; verify-run to
  `shipgate.verify_run/v2`; and the downstream local contract to schema v3.
  Prior schema files remain frozen. Report v0.32, packet v0.10, capability
  standard v0.4, and capability lock/diff v0.5/v0.6 are unchanged by this
  control-contract milestone.
- **Execution, applicability, and mergeability are separate.** Verifier v0.3
  publishes execution (`not_run | succeeded | skipped | failed`) separately
  from applicability (`not_evaluated | verified | not_applicable | failed`).
  `can_merge_without_human` is true only for a verified `passed` result or a
  completed deterministic non-applicable skip. GitHub Action outputs add
  `agent_control_state` and `agent_control_reason`; legacy booleans remain exact
  derived mirrors for one compatibility cycle.

- **Conductor OSS workflow JSON adapter.** A built-in, per-scan `conductor`
  source statically enumerates literal MCP calls and records MCP discovery,
  LLM tool advertisements, HUMAN checkpoints, nested control-flow tasks, and
  local sub-workflows. Dynamic or unresolved tool surfaces emit
  `SHIP-CONDUCTOR-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE`; unsupported worker,
  HTTP, A2A, provider-native, and runtime-generated capabilities remain
  explicit evidence gaps. Report schema advances from the frozen binding
  contract v0.31 to v0.32; manifest v0.1, packet v0.10, and runtime contract
  v13 remain unchanged. `conductor` is now a reserved built-in
  `tool_sources[].type` and may conflict with a third-party adapter that
  previously used the same source type.
- **Root-reachable agent binding graph (P0, `0.16.0b2`).** Tool catalogs no
  longer become an agent's capability surface by extraction alone. Framework
  adapters emit static tool and handoff edges, `agent_bindings` supports exact
  reviewed closed-world declarations, and partial, dynamic, ambiguous, or
  conflicting graphs prevent `passed`. Reports separate `tool_catalog[]` from
  root-reachable `tool_inventory[]` and publish binding facts, diffs, coverage,
  evidence gaps, and human-routed remediation.
- **Binding contract versions.** Runtime contract advances to v13; report to
  v0.31; packet to v0.10; capability standard to v0.4; capability lock/diff to
  v0.5/v0.6; action snapshot to v0.3; and safety qualification formats to v2.

- **Provider-scoped canonical tool identity (P0).** Tool observations now get
  deterministic source-scoped IDs and same-name tools from different
  providers remain distinct. Cross-source evidence joins only through exact,
  reviewed `tool_identity.bindings[]`; invalid bindings and ambiguous
  one-to-one selectors apply nowhere and route to `insufficient_evidence`.
- **Identity-safe policies, diffs, traces, and debt.** Action declarations,
  controls, risk overrides, suppressions, packets, tool/action diffs,
  capability lineage, trace matching, and finding fingerprints consume the
  canonical tool identity. Fingerprint v2 hashes `tool_id`; legacy baseline
  matches are accepted only for an unambiguous current identity. Pre-v0.30
  reports and pre-v0.4 capability locks must be regenerated before diffing.
- **Identity contract versions.** Runtime contract advances to v12; report to
  v0.30; packet to v0.9; capability standard to v0.3; capability lock/diff to
  v0.4/v0.5; policy pack to v0.3; verifier to v0.2; action snapshot to v0.2;
  and agent handoff to `shipgate.agent_handoff/v2`.

- **Evidence-backed `passed` verdict (`0.16.0b1`).** `passed` now requires
  complete, conflict-free static surface, effect, and authority evidence for
  every in-scope action, evaluation of all applicable controls, and no policy
  condition requiring review. Unknown, inferred-only, protocol-defaulted,
  partial, invalid, or conflicting semantics route to
  `insufficient_evidence`; known ambient/unscoped authority routes to review.
  Semantic gaps are not Findings and cannot be suppressed or baselined. This
  remains a static claim, not proof of runtime behavior or enforcement.
- **Normalized semantic evidence contract.** Report schema v0.29 and packet
  schema v0.8 add per-action/per-capability assessments, semantic coverage,
  and typed human-routed gap remediation. Manifest action declarations can
  provide reviewed `effect` and `authority` evidence; Agents Shipgate never
  auto-writes those assertions. Contract v11 exposes this boundary as
  `do_not_auto_assert: [action_effect, action_authority, ...]`.
- **Machine-readable static-verdict boundary.** Report release decisions,
  verifier artifacts, and agent-handoff gates now expose
  `static_analysis_only: true`, `runtime_behavior_verified: false`, and the
  canonical `static_verdict_disclaimer`; packet v0.8 §1 mirrors the report.
- **Capability standard v0.2.** Capability lock schema advances to v0.3 and
  diff schema to v0.4 so static capability facts carry the normalized semantic
  assessment. Runtime contract advances to v11; the source-tree package is
  `0.16.0b1` while install examples remain pinned to the latest published tag,
  `v0.15.0`, until the beta is released.
- **Qualification trust boundary is explicit.** Beta promotion verifies an
  internally consistent production-qualification summary, its configured
  Sigstore identity, and exact wheel/tag binding. Organizational signer
  independence, blind labeling, receipt replay, and the four-week,
  three-design-partner rollout remain governed external controls rather than
  guarantees made by the promotion code. The machine policy enforces a
  combined minimum of 40 real-history, rejected/reverted, or design-partner
  origins.

## 0.15.0 - 2026-07-07

- **First real-history accuracy numbers, published.** The 2026-W26 mined
  corpus (120 merged PRs from stripe/agent-toolkit, block/goose, and
  pydantic-ai) is now labeled (two independent AI labelers, disagreement
  0/10, third-pass adjudicated — pending human spot-check) and scored. On the
  10 PRs the gate engaged, it never wrongly passed an authority-bearing change
  (`needs_human_caught` 1.0, `benign_escalation_rate` 0.0) but also never
  cleanly passed a safe one (`ie_rate_on_safe` 0.5, plus a since-fixed scan
  crash). Full confusion matrix and method in
  [`benchmark/miner/README.md`](benchmark/miner/README.md); the README status
  banner now carries the numbers instead of "none published yet". Real history
  contributes no `must_block` rows, so blocked-recall stays with the
  constructed-adversarial stratum.
- **Config-bound dynamic-toolkit capability detection.** New checks
  `SHIP-CAP-CONFIG-BINDING-REMOVED` (high, suppression-immune) and
  `SHIP-CAP-CONFIG-BINDING-CHANGED` (review item) close the pilot blind spot
  where a diff removed or retargeted a factory's config binding — silently
  expanding the effective tool surface — without any capability delta showing
  in the diff. A conservative same-file config tracer (json/yaml/toml loads,
  `os.environ`, in-file pydantic settings) feeds them; `config → unknown`
  never fires, guarding against false positives.
- **Duplicate `action_surface` action_id collisions degrade instead of
  crashing.** A base reference serialized by a pre-#226 engine could still
  crash `scan`/`verify` at diff time with `Config error: Duplicate
  action_surface action_id`; it now degrades to a source warning
  (review_required), matching the OpenAPI fix in #226. This eliminated the
  four `scan_failed` rows in the W26 corpus.
- **Claude Code plugin marketplace.** The repo now doubles as a Claude Code
  plugin marketplace (`/plugin marketplace add ThreeMoonsLab/agents-shipgate`,
  then `/plugin install agents-shipgate@agents-shipgate`) — the symmetric
  counterpart of the existing Codex marketplace. The plugin is skill-only
  (the auto-triggering skill + the namespaced `/agents-shipgate:shipgate`
  command); the scanner stays in the separately installed CLI and hooks stay
  on the explicit `install-hooks` path. Byte-identity with the canonical
  skill/command sources is test-pinned. Fixed in passing (caught by
  `claude plugin validate`): the canonical `SKILL.md` and `/shipgate`
  command shipped YAML frontmatter with an unquoted `:` in `description`,
  which Claude Code loads as silently-empty metadata — breaking
  description-based skill auto-triggering for every existing install. Both
  are now quoted, all byte-identical copies synced, and a regression test
  parses the frontmatter.

- **Contract v10 (additive): machine-readable `verify_required` on the Codex
  boundary result.** `shipgate check` already escalated to `warn` and routed
  to `verify` when a diff touched a tool surface it cannot gate; that deferral
  now also sets a top-level boolean `verify_required` on
  `shipgate.codex_boundary_result/v1`, and `verify_required` joins
  `agent_result_control_fields` in the runtime contract. Agents switch on the
  field instead of parsing warning prose; the observable pair is
  `decision="warn"` with `verify_required=true` — "no boundary rule fired,
  but capability is not yet gated: run verify before completion" (the
  escalation means a plain `allow` always has `verify_required=false`). The
  field lives on the shared `AgentResultV1` base, so the legacy
  `agent-result-schema.v1.json` carries it too and
  `agent_result_control_fields` validates against both schemas. Additive
  over v9: consumers pinned to `contract_version >= 9` keep working.

## 0.14.0 - 2026-06-30

- **Versioning: the `1.0.0-alpha` line is withdrawn; this work ships as
  `0.14.0`.** An earlier draft of this cycle briefly carried `1.0.0a1`. That
  label was withdrawn: the `report.json` schema (`report_schema_version:
  "0.28"`) is still additive-versioned and not yet frozen, the package is still
  `Development Status :: 4 - Beta`, and no real-world detection-accuracy
  baseline has been published — none of which support a `1.0` line. `0.14.0`
  continues the `0.x` contract line from `0.13.0` and carries the same
  agent-controller cleanup (see
  [STABILITY.md](STABILITY.md#migration-note-0-14-0)). A `1.0` line will begin
  only when the report schema reaches `1.0` and holds without a breaking change.
- **Non-preview `verify` now fails closed on a missing `--config`.**
  `agents-shipgate verify --workspace . --config missing.yaml --json` exits
  `2` with `merge_verdict: "unknown"`, `applicability: "unknown"`, and
  `can_merge_without_human: false`; it writes lightweight verifier/controller
  artifacts but no `report.json` and runs no head scan. This replaces the old
  lenient path where a missing config could trigger-skip and exit `0`.
  `verify --preview --config missing.yaml --json` is unchanged and remains the
  setup/relevance path with exit `0`.
- **Shipgate now has a separate self-dogfood PR workflow.** The root
  `shipgate.yaml` remains the public Codex-plugin marketplace self-scan, while
  `shipgate-self.yaml` and `.github/workflows/agents-shipgate-self.yml` run an
  advisory static-only local-action gate on pull requests with
  `fail_on_merge_verdicts: blocked`, artifact upload enabled, and PR comments
  disabled. This does not scan Shipgate's Python scanner implementation; tests,
  coverage, audit, SBOM, and release signing remain that assurance path.
- **A named high concern now routes to review, not `insufficient_evidence`.**
  When a scan turns up an *active* (not baseline-accepted) high/critical review
  finding, the release decision is now `review_required` even if low-confidence
  extraction would otherwise have produced `insufficient_evidence`. Both
  verdicts are equally non-auto-mergeable, but `review_required` points the
  human at a specific, actionable finding (e.g. the new
  `SHIP-SCOPE-TOOLKIT-UNBOUNDED`) instead of the vaguer "we couldn't see
  enough." `blocked` still outranks everything; IE still fires when the only
  signal is thin extraction. The 2026-06-01 Stripe pilot's silent/IE case now
  surfaces as a routed review. `evidence_gaps` are preserved on the report
  either way, so the extraction-coverage signal is not lost.

## 0.13.0 - 2026-06-12

- **Accepted-debt exception workflow (baseline schema 0.6).** `baseline save`
  gains `--owner`, `--reason`, and `--expires` so the approval metadata the
  v0.5 provenance contract documented as "reviewer-set" is finally settable
  without hand-editing the file (which trips the integrity hash). Metadata is
  stamped on newly-accepted entries; `--apply-to-existing` fills the fields
  into existing entries that lack them — never overwriting a previously-set
  value and preserving each entry's original `recorded_at`/`run_id` history.
  Approval is declared, never inferred, matching the `human_ack` contract.
  New `baseline status` reports accepted-debt aging (owner, age, expiry,
  expiring-soon/expired/unowned summary; `--as-of` pins the date for
  reproducible CI output) and turns into an org governance gate with
  `--require-owner` / `--require-expiry` / `--max-age-days N` — exit 20 on
  violations, advisory exit 0 without gate flags. Expired entries violate
  `--require-expiry`, and entries without provenance fail every active gate
  (unknown history is ungoverned debt, not exempt debt). Legacy 0.2–0.5
  baselines still load; re-saving upgrades them to 0.6.
- **Host-grant drift detection.** `audit --host --save-baseline` records the
  current coding-agent host grants (MCP servers, Claude Code permission rules
  and hooks, workflow scopes, Codex config presence) as the acknowledged state
  in `.agents-shipgate/host-grants.json` (content-only and byte-idempotent —
  no timestamps or machine paths; the directory is already a verify trust-root
  surface, so PR edits to the snapshot stay release-visible). `audit --host
  --drift` deterministically diffs current grants against that baseline with
  per-category added/removed/changed buckets plus `expansion_signals` naming
  the authority-broadening shapes (new or **changed** server, wildcard allow
  added, `deny` or `ask` rule **removed**, hook added or **changed**, workflow
  write scope or `pull_request_target` gained). MCP server and hook entries
  carry a `config_sha256` over their full configuration; inside
  `env`/`headers` only values under secret-looking keys (shared sensitive-key
  vocabulary: token, secret, password, api_key, authorization, …) are redacted
  before hashing, so editing what an existing server or hook can do — args,
  commands, matchers, URL, key sets, or a grant-shaping value like
  `READ_ONLY=false` — is drift while credential rotation is not; the
  baseline's stored `inventory_sha256` is verified at load time and
  hand-edited or malformed baselines fail closed with exit 2. Advisory by default; `--fail-on-drift`
  exits 20 for scheduled CI gates — recipe at
  `examples/github-actions/12-host-grant-drift.yml`. Catches authority changes
  that land outside PR review, where the diff-time `SHIP-HOST-BOUNDARY-*`
  checks cannot see them.
- **`check` defers tool-surface changes to `verify` (coverage boundary).**
  `shipgate check` is boundary-scoped and does not compute the capability
  delta, so a clean boundary result over a diff that changes a
  manifest-declared `tool_sources[].path` no longer returns `allow` — it
  returns `decision="warn"` routing `first_next_action` to `verify`, with a
  `diagnostics[].code="capability_change_requires_verify"` marker and a
  `trace[].step="coverage"` event. Completion is still allowed, but `check`
  no longer green-lights a capability change only `verify` gates, so the local
  loop cannot disagree with `release_decision.decision`. Docs/test/boundary-only
  diffs are unaffected (still `allow`); no `agent_result_v1` schema change.
- **Agent-mode auto-detection.** Agent mode now auto-enables when a known
  coding-agent harness environment is detected (Claude Code exports
  `CLAUDECODE=1`, Cursor `CURSOR_TRACE_ID`), so structured `next_action`
  errors no longer require remembering `AGENTS_SHIPGATE_AGENT_MODE=1`. An
  explicit `AGENTS_SHIPGATE_AGENT_MODE=0` still forces it off.
- **Compact agent stdout for `verify`.** `verify --format agent` (new) prints
  the compact `agent_result_v1` payload (the same artifact written to
  `agents-shipgate-reports/agent-result.json`) on stdout, so one `verify`
  call closes the agent loop without a second file read. Bare `verify --json`
  resolves to this agent surface for verify runs (and to the full verifier
  JSON for `--preview`, whose relevance answer lives in the `trigger`
  block); `verify --format json` is unchanged. Inside a detected
  coding-agent environment, zero-flag `verify` defaults to the agent format.
- **Base auto-detection for `verify`.** When `--base` is omitted, verify
  auto-detects the default branch (`origin/HEAD`, `origin/main`,
  `origin/master`, `main`, `master`) and uses it for diff context — but only
  when the detected ref points at a different commit than the head, so a
  clean checkout of the default branch keeps today's working-tree behavior.
  The detection never fetches. `--no-base` disables it; an explicit `--base`
  always wins. The auto-detected ref is recorded in `base_notes`.
- **`init --claude-code` one-shot setup.** A single flag wires the full
  Claude Code surface: the `CLAUDE.md` managed block, the
  `.claude/skills/agents-shipgate/` skill bundle, the Claude Code hooks, and
  an `agents-shipgate verify --json` alias appended to Makefile /
  `package.json` scripts when those files exist. Idempotent, dry-run without
  `--write`, and reported under the additive `claude_code` key in
  `init --json` output.
- **Pre-commit hooks now run the verifier.** The `agents-shipgate` and
  `agents-shipgate-strict` pre-commit hook entries switch from unconditional
  `scan` to the trigger-gated `verify` flow (the `files:` regex pre-gate is
  unchanged), so local commits get the same merge-verdict surface as CI and
  diff-only trigger rules are evaluated once the hook fires.
- **`fix_task.patches[]`.** When `verify --suggest-patches` routes the repair
  to the coding agent, the fix task now carries the machine-applicable
  suggested patches (`{finding_id, check_id, patch}` with the discriminated
  set/append/remove-pointer payloads) so the agent gets concrete edits, not
  just prose instructions. Manual patches stay excluded and the field is
  additive — repair aid, never a gate input.
- **`fix_task` names low-confidence sources on `insufficient_evidence`.** The
  verify fix task for an `insufficient_evidence` verdict no longer dead-ends
  at the threshold sentence: it names each low-confidence source (count,
  source type, ref) with the explicit-inventory remedy and quotes up to
  three source warnings. Complements the report-layer
  `evidence_coverage.evidence_gaps[]` (schema v0.26); the route stays human
  because declaring an inventory asserts authority a coding agent must not
  invent. Deeper adapter-level config-bound toolkit detection is designed in
  `docs/engineering/config-bound-capability-detection.md`.
- **Claude Code adoption surfaces reworked.** The README gains a
  "Use with Claude Code" section, `docs/agents/use-with-claude-code.md` opens
  with the recommended one-command `init --claude-code` setup, and the
  `agents-shipgate` skill description triggers on change artifacts (MCP
  servers/tools, tool decorators, permission scopes, approval policies, agent
  CI) instead of product-name phrases only.
- **Cold-start dead ends now print an executable next action.** Human-mode
  CLI error paths surface the same ranked recovery step that agent mode
  emits as JSON: `scan`/`doctor`/`verify` config errors print a
  `next: …` / `why: …` hint (e.g. `next: agents-shipgate detect …` on a
  missing manifest), and the `init --write` → `scan` CHANGE_ME placeholder
  failure routes to the manifest edit instead of the generic missing-file
  advice — in both human and agent mode. `verify` also gains agent-mode
  structured errors (`AGENTS_SHIPGATE_AGENT_MODE=1`) and scan-parity
  flag-error vs run-error handling, so flag mistakes are never answered
  with manifest diagnostics. Hints are suppressed in agent mode to keep
  the `docs/errors.json` single-JSON-line contract. Driven by the
  2026-06-10 cold-start funnel test
  (`marketing/cold-start-funnel-test-2026-06-10.md`).

- Add the GTM plan of record (`marketing/gtm-strategy.md`), launch kit,
  design-partner outreach kit, and launch blog draft; README shows the
  verifier PR-comment verdict ("What your PR sees") and links the
  coding-agent install path from the quickstart.

- **Agent-native protocol.** `shipgate check --agent
  {codex,claude-code,cursor} --workspace . --format agent-json` is now the
  canonical one-command agent path. It returns the stable
  `agent_result_v1` contract with explicit completion, stop, repair,
  human-review, policy-provenance, source-artifact, and exit-code fields.
- **`agent_result_v1` policy provenance is required in 0.13.0 producers.**
  The schema name stays `agent_result_v1`; all in-tree producers now emit the
  required `policy` object plus `policy_snapshot_sha256`. Consumers validating
  older v0.12.0 objects should treat this as the 0.13.0 schema publication
  point and update together with the package version.
- **MCP server mode narrowed to `shipgate.check`.** The optional
  `[mcp]` server is now a read-only static adapter that accepts caller-provided
  diff text and returns exact `agent_result_v1`. The v0.12.0 preview tools
  (`shipgate_preview`, `shipgate_verify`, `shipgate_explain_finding`) were
  never listed in `STABILITY.md`; they are removed in favor of the single
  agent protocol command/tool.
- Policy weakening detection now compares parsed before/after policy YAML
  from reconstructed file content when available, so quoted scalars, inline
  comments, and hunks that omit the rule id still block.
- `shipgate check --head <ref>` or `--base <ref>` alone now fails closed with
  a structured CLI error. Provide both refs, or omit both to check local
  uncommitted changes.

## 0.12.0 - 2026-06-09

- **Actionable `insufficient_evidence` (report schema v0.26).**
  `release_decision.evidence_coverage.evidence_gaps[]` now lists one
  structured remediation row per low-confidence tool / source warning
  (`{kind, subject, source_type, source_ref, why, next_action}`), and scan
  writes an advisory `suggested-inventory.json` skeleton next to
  `report.json` whenever low-confidence tools exist — in the same
  MCP-export shape every `tool_inventories` manifest key loads. Pure
  projection of the existing coverage counts; thresholds, decisions, and
  fingerprints are unchanged.
- **Local capability-release ledger (`registry` v0.1).**
  `agents-shipgate registry ingest --attestation <file>` appends a
  normalized, content-addressed row to a JSONL ledger (idempotent);
  `registry query` filters by repo / verdict / capability id /
  trust-root flag. The v0 substrate for the cross-repo attestation
  registry; design boundary for any hosted aggregation documented in
  `docs/hosted-plane-design.md`, and the v1.0 report consolidation
  proposal in `docs/report-v1-consolidation-rc.md`.
- **Host capability governance v0 (`SHIP-HOST-BOUNDARY-*`).** New
  diff-aware, suppression-immune check family covering coding-agent host
  grants: MCP server additions/changes in `.mcp.json` /
  `.cursor/mcp.json` / `.vscode/mcp.json`, Claude Code
  `permissions.allow` expansion (wildcard-shaped rules like `Bash(*)`
  **block**; scoped expansions route to human review), `permissions.deny`
  removal, hook changes, GitHub workflow permission expansion
  (`write-all` blocks; read→write routes to review), and new
  `pull_request_target` triggers. Policy mirror at
  `policies/host-boundary.shipgate.yaml`; concepts and reviewer guidance
  in `docs/mcp-governance.md`. Trust-root classification now also covers
  `.claude/settings.json` / `.claude/settings.local.json` /
  `.cursor/mcp.json` / `.vscode/mcp.json`.
- **`audit --host` zero-config inventory.** One read-only command that
  answers "what is my coding agent currently allowed to do in this
  repo?" — MCP servers (env *keys* only, never values), permission rules
  with wildcard flags, hooks, and workflow write scopes /
  `pull_request_target` — as one page of Markdown or `--json`. Works
  without `shipgate.yaml`.
- **Policy packs v0.2: conditional composition + org distribution.**
  `match` gains `all_of` / `any_of` / `none_of` combinators (flat fields
  stay implicitly ANDed — fully backward compatible) and parameter
  predicates gain declared-bound comparisons (`maximum_above`,
  `minimum_below`), so rules like "financial action with amount unbounded
  or above 1000 must declare approval" are now declarative.
  `checks.policy_packs` entries accept an optional `sha256` content pin
  that fails the scan closed when a shared/org pack is tampered with.
  Schema frozen at `docs/policy-pack-schema.v0.2.json`.
- **MCP server mode (optional `[mcp]` extra).** `agents-shipgate
  mcp-serve` exposes `shipgate_preview`, `shipgate_verify`, and
  `shipgate_explain_finding` over stdio so shell-less agents can query
  the verifier in-loop. Pure projection layer: no network, no mutating
  tools, no second gate (`docs/mcp-server.md`).
- **PreToolUse boundary hook for Claude Code.** `install-hooks --target
  claude-code` now also registers a `PreToolUse` hook: editing a
  protected trust-root surface routes the tool call to the human
  (`permissionDecision: "ask"`, or `deny` via
  `AGENTS_SHIPGATE_PRETOOLUSE_DECISION`) with an explanation — the
  authority boundary surfaces in-session, before the edit, instead of at
  PR time. The protected-surface list is rendered at install time from
  the verify check's `TRUST_ROOT_SURFACES`, so hook and gate cannot
  drift.
- **Native GitHub Check Run support.** New Action inputs `check_run` /
  `check_run_name` publish the merge verdict as a Check Run
  (`mergeable` → success, `blocked` → failure, human-routed verdicts →
  neutral) with up to 50 line-level annotations from `report.sarif`
  (`scripts/github_check_run.py`; requires `checks: write`). New recipes:
  `examples/github-actions/09-risk-labels-and-reviewers.yml` (risk labels
  + trust-root reviewer routing from existing outputs) and
  `10-check-run-annotations.yml`.
- **`agent_weakens_gate` fixture.** One-command trust-root demo
  (`agents-shipgate fixture run agent_weakens_gate`): the head commit
  deletes the repo's Shipgate CI workflow — the cheapest reward-hack —
  and the verifier returns `merge_verdict: blocked` with
  `can_merge_without_human: false` via the suppression-immune
  `SHIP-VERIFY-CI-GATE-REMOVED` / `SHIP-CODEX-BOUNDARY-CI-GATE-REMOVED`
  checks.
- **Privacy hardening.** The redaction passthrough for already-redacted
  values now honors only marker kinds Shipgate itself emits, so scanned
  values formatted like `[REDACTED:...]` can no longer smuggle payloads
  past forced sensitive-key redaction. Added symlink-escape regression
  tests for input loading and `apply-patches` containment.
- Add a GitHub/verify `agent-result.json` artifact that uses the existing
  `agent_result_v1` schema instead of introducing a second agent-result
  contract. The Action exposes `agent_decision`, `risk_level`, `audit_id`,
  `required_reviewers`, and `policy_snapshot_sha256`, and the opt-in
  `fail_on_decisions` input now fails closed when configured but no compact
  agent decision is available.
- Phase 7 makes capability diff the default verifier review primitive when a
  reviewed base lock is committed: `verify` emits head capability locks plus
  semantic diff JSON/Markdown review artifacts when available, and attestation
  output moves from schema `0.1` to `0.2` to bind capability lock/diff hashes.
- SARIF results now prefer stable policy rule IDs when a finding carries one,
  while preserving the built-in Shipgate `check_id` in properties. Existing
  GitHub code-scanning alerts keyed by the previous rule ID may close/reopen
  on the first upgrade run.
- Add the repo's advisory self-dogfood Shipgate workflow, product-hardening
  gap-closure docs, Agent Workflow Evidence schemas, and the AgentPR Governance
  case catalog / acceptance spec.

## 0.11.0 - 2026-05-31

- **Verifier adoption-loop release prep.** Public docs and discovery metadata now
  lead with the verify-first adoption path, pinned `v0.11.0` snippets, verifier
  artifacts, merge verdicts, `fix_task`, and explicit Action merge-policy
  examples. Adds the verify-native `ai_generated_refund_pr` fixture for the
  blocked refund PR demo and introduces the provisional
  `agents-shipgate feedback export` command plus
  `docs/feedback-schema.v0.1.json` for redacted design-partner feedback loops.

- **Verifier PR comment v2 + additive Action outputs.** The GitHub Action now
  defaults to the verifier workflow (`verify_mode: verify`) and the
  capability-review PR comment (`pr_comment_style: capability-review`) for the
  next minor release. The comment starts from
  `release_decision.decision`, renders a top capability-change table, surfaces
  trust-root warnings, separates required human/coding-agent work, and links the
  generated artifacts. The v1 findings-oriented comment remains available for
  one minor release cycle with `pr_comment_style: findings`.
  - New Action outputs are additive:
    `should_run`, `trigger_action`, `trigger_rule_ids`, `verifier_verdict`,
    `trust_root_touched`, `policy_weakened`, `capability_changes_added`,
    `capability_changes_modified`, and `capability_changes_removed`.
  - Existing outputs are preserved; `decision` remains the preferred release
    gating output.
  - `verifier.json` now includes a derived `capability_review` projection
    over `report.capability_change` and `report.verifier_summary`. It is
    reviewer-facing only and cannot disagree with the head scan's
    `release_decision`.

- **New large-scale sample + asserted latency budget.**
  Adds `samples/large_multi_framework_agent/` — a production-shape retail-ops
  AI assistant with ~65 tools across five tool sources (payments OpenAPI,
  fulfillment OpenAPI, CRM MCP, internal warehouse MCP, OpenAI Agents SDK).
  Exercises the pipeline (loaders → checks → release decision → reports +
  packet + privacy redaction) at realistic load, well beyond the 5–15 tool
  range covered by the existing samples. The manifest declares *partial*
  governance coverage on purpose so the scan surfaces a realistic mix of
  blockers, review items, and audit-envelope activity (~10 critical
  approval gaps, ~70 review items, severity overrides, suppressions,
  manual risk hints).
  New `tests/test_large_sample.py` (12 cases) asserts:
  - **Latency budget of 10.0 s wall-clock per scan** (typical: 1–2 s on a
    2024 laptop). The release gate lives on the CI critical path; a
    silent regression that doubles scan time would be felt by every
    adopter. The budget is generous to absorb CI variance — if the
    typical time exceeds half the budget, the sample has grown or the
    pipeline has regressed.
  - **Structural shape**: all 5 sources contribute tools; tool count in
    [50, 100]; findings in [40, 200]; decision blocked; at least one
    critical `SHIP-POLICY-APPROVAL-MISSING`; scope-coverage fires;
    severity-override audit envelope populated; contribution rules
    exhaustive over findings; privacy/reviewer/heuristics audit
    envelopes emitted.
  No committed `expected/report.{md,json}` goldens (intentional — pinning
  50+ findings × 20+ report sections through every schema bump is high
  cost, low signal). Auto-discovered as `agents-shipgate fixture run
  large_multi_framework_agent`; NOT added to `self-check`'s default
  fixture set so install verification stays fast.

- **`init --write` now ensures `agents-shipgate-reports/` is gitignored.**
  Closes a long-standing DX gap: the reports directory created by the first
  `scan` would silently appear in `git status` (and could be committed by an
  agent running `git add -A`). On every `init --write` we now also write a
  managed block to `.gitignore`:
  - File missing → created with just the block.
  - File present without our markers and without an existing
    `agents-shipgate-reports/` line → managed block appended (separated by
    one blank line; user content preserved byte-for-byte).
  - File present with our markers → upserted (unchanged / updated / migrated
    on version bump; refused on a newer version).
  - File present with `agents-shipgate-reports/` (or `/agents-shipgate-reports`
    / `agents-shipgate-reports` / `/agents-shipgate-reports/`) already on its
    own line → no-op (`already_present`). Normalization mirrors what
    gitignore itself does: trailing whitespace is stripped (gitignore
    ignores it on patterns), but **leading whitespace is not** — a line
    like ` agents-shipgate-reports/` (one leading space) is a broken
    pattern that git does not honor, so we fall through and append our
    managed block. Mid-line `#` is *not* treated as a comment introducer
    (gitignore only treats line-leading `#` as a comment, so
    `agents-shipgate-reports/  # legacy line` is a literal pattern that
    matches nothing — we again fall through and append). The same
    leading-whitespace rule applies to `!`-negations:
    ` !agents-shipgate-reports/` is not honored by git, so we don't treat
    it as `skipped_negated` either.
  - File present with `!agents-shipgate-reports/` → no-op
    (`skipped_negated`). Explicit user opt-outs are respected.
  - File present with ambiguous markers (e.g. duplicate blocks) → no-op
    (`skipped_ambiguous`).
  Idempotent on both LF and CRLF hosts (CRLF is preserved when writing,
  and the marker regex tolerates a trailing `\r` so the second `init
  --write` recognizes the existing block rather than appending a
  duplicate). Also runs when the manifest already exists so
  repos that adopted Shipgate before this CLI version get the line on their
  next `init --write`. Failure modes (symlinked `.gitignore` chain, path is
  not a regular file, write error) emit an `error`/`skipped_*` outcome but
  never block `init` — exit code is unchanged from prior versions.

  The outcome is surfaced in `--json` output as a new
  `gitignore: {status, path, message, block_version}` field. A human-readable
  one-line message prints to stdout (or stderr for skip/error statuses);
  `unchanged` and `already_present` are quiet so the success path stays
  scannable. New module: `agents_shipgate.cli.discovery.gitignore_block`.
  New tests: `tests/test_init_gitignore.py` (48 cases covering pure
  parsing, upsert, variant detection, CRLF parse + two-run CRLF
  idempotency, mid-line-`#` no-stripping, leading-whitespace rejection
  (space + tab + on negations), trailing-whitespace acceptance, and
  end-to-end through the CLI).

- **MVP readiness polish.** Check metadata now carries public `mvp_tier`
  triage labels; the OpenAI Agents SDK static extractor can scan a directory of
  immediate `*.py` files; and CLI / GitHub summaries lead with the
  baseline-aware decision, headline, evidence coverage, and next action.
  - `mvp_tier` is metadata only. It does not affect check execution, severity,
    fingerprints, baselines, `release_decision`, or CI exit behavior.
  - OpenAI Agents SDK single-file and directory modes now both emit
    manifest-relative POSIX `source_ref` values. The extractor delegates to the
    shared Python static helper, so runtime/context parameters named `self`,
    `cls`, `ctx`, `context`, `config`, `runtime`, `run_manager`, or `callbacks`
    are omitted from normalized input schemas.
  - CLI top findings now show the highest-impact 3 active findings, prioritized
    by release blockers then review items. `list-checks` plain text includes
    `mvp_tier` as a third tab-separated column; use `--json` for stable
    programmatic consumption.

- **v0.21 — `--no-heuristics` CLI flag closes the round-3 / round-4 E5
  carryover.** `Finding.provenance_kind` has shipped on every report since
  v0.15 as required+non-nullable wire metadata but had no consumer for
  four review cycles. v0.21 lands the consumer the field was always
  designed for: a security/GRC-friendly filter that excludes findings
  whose provenance is `keyword_heuristic` or `regex_heuristic` from the
  active release-gating set.
  - New `--no-heuristics` flag on `agents-shipgate scan` (stable in
    0.x). When set, findings whose `provenance_kind` is in
    `NO_HEURISTICS_EXCLUDED_PROVENANCE_KINDS` (today: `keyword_heuristic`
    and `regex_heuristic`) are marked `suppressed=True` with
    `suppression_reason="filtered by --no-heuristics"` BEFORE the
    release decision is built. Filtered findings remain in `findings[]`
    for transparency; they no longer gate release. The KEEP list is
    `static_declaration`, `ast_extraction`, and `policy_pack` —
    declared/parsed-shape findings and explicit external rules stay in
    scope.
  - New top-level `report.heuristics_filter` audit envelope. Required +
    always present on emitted scans regardless of whether the flag was
    set (parallel to `privacy_audit` shape). Fields: `enabled`,
    `excluded_provenance_kinds: list[str]`, `filtered_finding_count`,
    `filtered_by_kind: dict[str, int]`. Earns the contract weight of
    `Finding.provenance_kind` by giving it a first-class consumer.
  - Manifest-driven suppression wins on overlap: a finding the user
    explicitly suppressed via `checks.ignore` keeps the user's reason
    text even when its provenance_kind would have triggered the
    filter. The audit envelope still counts the overlap so reviewers
    see the filter's effective scope.
  - `ReviewerSummary` lens/audit counts already reflect the post-filter
    active set (the filter runs before `build_reviewer_summary`); no
    new field added to `ReviewerSummary` — the dedicated envelope is
    the right audit home.
  - Schema bump: `report_schema_version: "0.20"` → `"0.21"`. v0.20 moves
    to frozen-reference; existing v0.20 consumers ignore the new field.
  - Contract-stamp pin in `docs/architecture.md` bumped to date
    `2026-05-23`, report `v0.21`, packet `v0.6` (unchanged). The
    `test_architecture_doc_contract_stamp_matches_runtime` regression
    test moves in lockstep.
  - 12 new tests in `tests/test_no_heuristics.py` covering: pure-
    function filter semantics (KEEP / FILTER classifications per
    provenance_kind), envelope shape parity across enabled=True/False,
    manifest-suppression preservation, contract-list completeness
    (every value in `NO_HEURISTICS_EXCLUDED_PROVENANCE_KINDS` is a
    real `ProvenanceKind`; KEEP+EXCLUDE partition is exact), end-to-
    end `run_scan(no_heuristics=True)`, CLI subprocess smoke test,
    monotone-non-increasing reviewer-summary lens counts under
    filtering.
  - **Decision recorded.** Round-4 review's E5 carryover offered ship-
    or-retire on `provenance_kind`. We ship. Retiring would have forced
    a deprecation cycle on a stable-contract field used by every
    report since v0.15; shipping the flag earns the weight and serves
    a real audience (security/GRC reviewers triaging declared-only
    findings before promotion).

- **v0.21 — CI coverage gate raised from 75% → 85% (E7 from round-4 review).**
  Both `.github/workflows/ci.yml` and `.github/workflows/release.yml` now
  pass `--cov-fail-under=85`. Aggregate coverage on `main` at the time of
  the bump is ~88%, so the gate is +10pp tighter with ~3pp headroom for
  day-to-day movement. The bump catches the next time a refactor lands
  materially less-covered code without corresponding tests. No source
  change required to land — the gate is simply closer to the actual
  signal. Per-file coverage is not enforced; the aggregate floor only
  rises in step with what's already proven on `main`.

- **v0.21 — decompose `inputs/n8n.py` into `inputs/n8n/` package (E8 from
  round-3 review).** The largest input adapter (1493 lines monolithic)
  is now a 6-module package with per-concern boundaries; the public
  surface (`N8nAdapter`, `load_n8n_artifacts`) is unchanged via
  `__init__.py` re-exports. No behavior change — all 30 `tests/test_n8n.py`
  cases pass byte-identical; M3 trust-lint passes; M5 plugin validation
  passes; adapter-discovery contract test (PR #111) passes.
  - `_common.py` (300 LOC) — constants (`N8N_NODE_TYPE_RE`,
    `FROM_AI_RE`, `N8N_SOURCE_TYPES`, `BUILTIN_N8N_PREFIXES`,
    `HTTP_METHODS`), `_NodeItem` and `_Edge` data classes, leaf string
    / path / hash / redaction helpers, node-kind classification.
  - `_secrets.py` (122 LOC) — secret scanning of parameters / notes /
    `pinData` / `staticData` against the v0.19 global `SECRET_PATTERNS`
    layer.
  - `_auth_risk.py` (148 LOC) — credential references, `AuthInfo`
    synthesis, risk-hint heuristics, HTTP path hint.
  - `_tools.py` (492 LOC) — Tool extraction for the 5 flavours (ai,
    workflow, code, http, mcp_client) + projected `mcp`, schema
    extraction (`$fromAI(...)` macro, `inputSchema`, `outputSchema`,
    `parameters.fields`), MCP Client Tool selection mode, tool-artifact
    recording.
  - `_workflows.py` (464 LOC) — workflow file loading, shape detection,
    `_extract_workflow` orchestrator, connection-graph edges, node-record
    builders, dynamic-surface emission.
  - `_adapter.py` (249 LOC) — `N8nAdapter`, `load_n8n_artifacts`, and
    auxiliary loaders (`_load_inventory_ref`, `_load_credential_stubs`,
    `_load_structured_refs`, `_artifact_paths`, `_credential_entries`).
  - Dependency direction is a DAG at module-load time:
    `_common ← _secrets, _auth_risk ← _tools ← _workflows ← _adapter`.
    `_tools` calls back into `_workflows` for record builders and
    dynamic-surface emission via late imports inside the call sites
    that need them — keeps the static import graph one-way.
  - `tests/test_public_surface_contract.py::test_supported_inputs_match_adapter_class_vars_bidirectionally`
    updated from `glob("*.py")` to `rglob("*.py")` so adapter
    sub-packages are scanned (the contract test was written when n8n
    was a single file).
  - Closes round-3 evolution item E8; brings the largest input adapter
    in line with the typical adapter file size (mcp.py 148, openapi.py
    343, langchain.py 305). Largest sub-module now is `_tools.py` at
    492 LOC.

- **Adoption kit rendering externalized.** Codex and Claude Code
  `--agent-instructions` skill bundles now render from packaged
  `adoption-kits/` files instead of Python string constants. Downstream repos
  can provide `.agents-shipgate/adoption-kit.yaml` or
  `--agent-instructions-kit <path>` for local overrides, and generated skill
  directories now carry `.agents-shipgate-kit.json` sidecars for managed
  migrations.

- **v0.20 — third-party adapter entry-point discovery (E4 from round-3 review).**
  Opens the same extension surface for adapters (input loaders) that M5
  already opened for check plugins. Discovery is gated by the existing
  `AGENTS_SHIPGATE_ENABLE_PLUGINS=1` env var and `--no-plugins` CLI flag.
  - New entry-point group: `agents_shipgate.adapters`. A third-party
    package declares an adapter class (or instance) in its
    `pyproject.toml` under
    `[project.entry-points."agents_shipgate.adapters"]`; the class must
    satisfy the `ToolSourceAdapter` Protocol — `source_type` ClassVar,
    `scope` ClassVar (`per_source` or `per_scan`), `artifact_class`
    ClassVar, and a `load(source, base_dir, manifest)` method.
  - New module `src/agents_shipgate/inputs/adapter_validation.py` with
    four load-time gates: `load_failed`, `bad_protocol`, `bad_scope`,
    and **`source_type_collision`** — the load-bearing trust rule
    rejecting any third-party adapter whose `source_type` shadows a
    built-in or another already-registered third-party adapter.
  - New top-level `discover_third_party_adapters(registry, *,
    plugins_enabled, loaded_adapters)` in `inputs/protocol.py` walks
    `entry_points("agents_shipgate.adapters")`, validates each entry,
    and registers the valid ones onto the supplied registry. Both
    valid and invalid records surface in
    `report.loaded_adapters[]` so reviewers can see what was skipped.
  - New report field `loaded_adapters: list[dict[str, Any]]` parallel
    to `loaded_plugins[]`. Items carry `name`, `value`, `distribution`,
    `version`, `source_type`, `validation_status`,
    `validation_errors[]`, `runtime_errors[]`. Required + present on
    every emitted scan (empty list when `--no-plugins` or no
    third-party adapters are installed). The schema generator marks
    each item's eight fields as required.
  - `--strict-plugins` (v0.17+) extended to cover adapter failures.
    Any non-`valid` `loaded_adapters[]` row OR non-empty
    `loaded_adapters[].runtime_errors` now elevates the scan to exit
    code 4 alongside the existing plugin failures.
  - `--no-plugins` flag help text updated to mention third-party
    adapter discovery is also disabled.
  - `run_validated_adapter` (in `adapter_validation.py`) provides a
    runtime safety wrapper for callers that want to capture
    exceptions into `loaded_adapters[].runtime_errors` instead of
    propagating them. The dispatcher's existing `_absorb` artifact-
    class check already fires `TypeError` for artifact smuggling;
    runtime wrapping is opt-in for future adapter-execution paths.
  - 21 new tests in `tests/test_adapter_entry_point_discovery.py`:
    each of the four gates + valid-class + valid-instance + env-var
    gating + `--no-plugins` overrides + collision-with-each-builtin
    parametrize + collision-between-third-parties + `--strict-plugins`
    end-to-end + runtime safety net (exception capture, wrong return
    type, artifact smuggling).
  - STABILITY.md gains a new "Third-party adapter discovery (v0.20+)"
    subsection under "Trust-model invariants" documenting the four
    gates + the `source_type_collision` load-bearing rule.

- **v0.20 — top-level `reviewer_summary` block.** Adds a deterministic
  projection of the reviewer lens surfaces (`tool_surface_diff`,
  capability/intent diff, `action_surface_diff`, evidence matrix) and
  audit envelopes (`policy_audit`, `privacy_audit`, baseline integrity
  findings). Parallels v0.12's `agent_summary` for the reviewer side:
  `agent_summary` answers "what should an agent do next?" and
  `reviewer_summary` answers "what should a reviewer look at first?".
  - Schema: bumped `report_schema_version` 0.19 → 0.20. The new block
    is required + non-nullable on the wire (Pydantic-Optional only for
    legacy test helpers). v0.19 schema is preserved at
    `docs/report-schema.v0.19.json`.
  - Fields: `verdict` (mirrors `release_decision.decision`), `headline`
    (≤200 chars, PR-comment-friendly), per-lens activity counts
    (`tool_surface_changes`, `capability_misalignments`,
    `action_surface_changes`, `evidence_matrix_gaps`), per-audit
    counts (`severity_overrides_applied`,
    `severity_overrides_tier_crossed`, `privacy_redactions`,
    `baseline_integrity_issues`), and `first_recommended_surface`
    (deterministic pointer or `null` on a clean scan).
  - `first_recommended_surface` priority: blocked → release_decision,
    insufficient_evidence → release_decision, then action_surface_diff
    > baseline_integrity > tier-crossed policy_audit >
    capability_intent_diff > tool_surface_diff > privacy_audit >
    evidence_matrix > null. Encoded in `_pick_first_recommended_surface`
    and pinned by `test_reviewer_summary.py`.
  - Projection invariants: pure (no I/O, no LLM calls), deterministic
    (same inputs → byte-identical output, asserted by
    `test_build_reviewer_summary_is_deterministic`), cannot disagree
    with the underlying lens/audit data.
  - STABILITY.md + docs/agent-contract-current.md: new bullets +
    enum-additivity rule mirroring `agent_summary.verdict`.

- **Docs: refresh `docs/architecture.md` to v0.19 reality.** The doc
  was stuck at pre-v0.6 conceptually — it described `core/models.py`
  as the shared model home (deleted in PR #95), framed adapters as
  free-function `load_<name>_artifacts(...)` (pre-v0.11 pattern), and
  did not mention the `schemas/` layer, the five reviewer lenses
  (tool surface / capability-intent / action surface / policy audit
  / evidence matrix), the three audit envelopes (policy audit,
  privacy audit, baseline audit log), the AST trust lint, plugin
  validation gates, severity-override floor, baseline integrity, or
  the privacy redaction layer. Refresh covers the v0.19 pipeline
  end-to-end, names every module, cross-links to `STABILITY.md` for
  each contract, and pins exit code `6` (strict `baseline verify`
  failure). No code change.

- **v0.18 / PR #1 trust-hardening: `dynamic_default` contract in
  `CheckMetadata`.** Formalizes the M1 dynamic-severity contract closed
  in v0.17.
  - `CheckMetadata.dynamic_default: bool = False` opts a check into the
    swing-severity category — its emitted finding severity depends on
    user-declared manifest values rather than the static catalog
    default. The severity-override resolver must receive the
    manifest-effective default via `extra_known_check_defaults`;
    otherwise tier-crossing comparison runs against the static catalog
    default and an aggressive override can silently bypass the gate.
  - A new model validator rejects `dynamic_default=True` without
    `floor_severity` — a swing check without a floor has no safety net.
  - `SHIP-ACTION-POLICY-VIOLATION` now declares `dynamic_default=True`
    and `floor_severity="medium"`. Two distinct contracts apply to
    existing manifests; both produce loud `ConfigError` (exit 2):
    - **Hard floor (no bypass).** Manifests resolving the check below
      `medium` — i.e., to `low` or `info` — are rejected by the
      `floor_severity` validator. `acknowledge_overrides` does NOT
      bypass the floor; the only remedies are to raise the override to
      `medium` or above, or remove the override entirely.
    - **Tier-crossing requires ack.** Downgrading from the catalog
      default `high` to the floor `medium` crosses the high → normal
      tier boundary. This case is allowed only with an
      `acknowledge_overrides` entry that supplies a reason; without one
      it is rejected with a tier-boundary error (not a floor error).
    Manifests currently overriding `SHIP-ACTION-POLICY-VIOLATION` to
    `low`/`info` cannot fix the regression by adding an ack — they must
    raise the override severity. Manifests overriding to `medium`
    without an ack pass once the ack is added.
  - `cli/scan.py:_dynamic_check_defaults` is the new canonical
    aggregator. It seeds every catalog check carrying
    `dynamic_default=True` with its static default (step 1), overlays
    manifest-effective values for action-surface policies (step 2), and
    adds policy-pack rule IDs (step 3). The seed loop guarantees the
    resolver's internal-consistency guard cannot false-positive on user
    input that overrides a swing check without declaring the
    corresponding manifest section.
  - A contract test `test_dynamic_default_aggregator_completeness`
    fails the moment someone adds a new `dynamic_default=True` catalog
    entry without ensuring the aggregator covers it.
  - Future checks emitting at manifest-declared severity must (A) set
    `dynamic_default=True` in `CHECK_METADATA` and (B) add an aggregator
    overlay branch in `cli/scan.py:_dynamic_check_defaults`. The
    contract test enforces both.
- **v0.18 / PR #1 plugin gate: `dynamic_default_not_supported`.**
  - New plugin-validation status rejects plugins declaring
    `AGENTS_SHIPGATE_METADATA.dynamic_default=True`. Plugins have no
    path into the scan dispatcher's aggregator and so could never
    receive the manifest-effective default needed for tier-crossing
    comparison; emitting at that severity directly is the supported
    path (with the floor contract still applying via
    `CheckMetadata.floor_severity`).
  - The gate runs **before** `_coerce_metadata()` so a plugin declaring
    `dynamic_default=True` without `floor_severity` lands in
    `dynamic_default_not_supported` rather than being mis-classified
    as `bad_floor` by the new `CheckMetadata` model validator.
- **v0.18 / PR #2 review follow-up: per-call-site allowlist pinning.**
  PR #91 review caught two structural holes in the v0.18 trust lint
  extension:
  - **P1**: the allowlist matched on `(relative_path, surface)` only,
    so one entry blanket-permitted every occurrence of a surface in
    a file. A future unreviewed `subprocess.run(...)` added to an
    already-allowlisted file would slip past silently.
  - **P2**: `importlib.resources` was globally exempted, so
    `files(name)` calls produced no violation. The current uses
    pass a literal `'agents_shipgate'` anchor, but a future
    user-controlled anchor would bypass the dynamic-import lint.

  Both are closed by tightening the allowlist contract:
  - `AllowedException` now carries `line: int` and `snippet: str`
    (canonical `ast.unparse` of the offending node) in addition to
    `relative_path` and `surface`. `_violation_allowed` matches on
    all four fields. Adding a new `subprocess.run` call to an
    already-allowlisted file now requires a new entry; changing an
    existing call's argv shape changes the `snippet` and fails the
    contract test.
  - `importlib.resources.` joins `FORBIDDEN_ATTR_CALL_PREFIXES`, and
    `importlib.resources` joins `TRACKED_NON_FORBIDDEN_MODULES`. The
    earlier draft of this PR only forbade `importlib.resources.files`,
    which left `read_text`, `read_binary`, `path`, `open_text`,
    `open_binary`, `is_resource`, `contents`, `as_file`, and any
    future addition under the module as a parallel bypass — each
    takes the same anchor-package argument and would have been
    silently allowed. The prefix entry catches the whole family.
    `from importlib.resources import <attr>; <attr>(...)` and
    `import importlib.resources as res; res.<attr>(...)` both
    resolve to canonical `importlib.resources.<attr>` and trip the
    prefix. Both first-party call sites in `triggers.py` and
    `fixtures.py` (currently `files`-only) are individually pinned
    with the literal `'agents_shipgate'` anchor in the snippet — a
    future `files(some_user_anchor)` or `read_text(some_user_anchor,
    ...)` call would change the snippet and fail the test.
  - `Violation` gains `snippet: str` captured via `ast.unparse(node)`.
  - New regression test
    `test_allowed_exceptions_pin_subprocess_run_per_call_site`
    asserts that multi-call files (triggers.py, artifacts.py) have
    distinct entries per call site, so the P1 bypass cannot
    reappear via consolidation.
  - New regression test `test_allowed_exceptions_have_no_duplicates`
    asserts no two entries cover the same call site.
  - Negative-control: injecting a 4th `subprocess.run` into
    `triggers.py` now fails the contract test with the precise
    `(line, surface, snippet)` triple. Injecting
    `files(user_var)` in place of `files('agents_shipgate')` fails
    similarly.

- **v0.18 / PR #2 trust-hardening: static AST lint widened to entire scanner.**
  Previously `tests/test_adapter_static_only.py` AST-scanned only
  `src/agents_shipgate/inputs/`; the public claim in STABILITY.md and
  README is broader ("the scanner does not execute or import user code").
  The lint now structurally enforces the broader claim.
  - Scope widened: scanner now walks every `.py` file under
    `src/agents_shipgate/` via `rglob`. The legacy
    `test_invariant_lint_covers_every_adapter_module` was paranoid for
    the 18-file `inputs/` case and no longer scales to ~80 files — the
    new contract test
    `test_no_unallowlisted_forbidden_surface_in_scanner` is the
    replacement, asserting a definitive PASS/FAIL signal over the whole
    sweep.
  - Four legitimate first-party meta-CLI surfaces are allowlisted via a
    new `ALLOWED_EXCEPTIONS` tuple of `AllowedException` entries, each
    with prose rationale:
    - `cli/bootstrap.py` `subprocess.run(...)` — chains
      `detect → init → scan → apply-patches` against Shipgate's own CLI.
    - `cli/discovery/artifacts.py` `subprocess.run(["git", ...])` —
      probes the user repo for file inventory.
    - `triggers.py` `subprocess.run(["git", "diff", ...])` — trigger
      evaluation reads diff content.
    - `cli/self_check.py` `__import__(name)` — validates that supplied
      modules are installed. Runs only under
      `agents-shipgate self-check`.
  - Two contract tests prevent allowlist rot:
    `test_allowlist_entry_matches_real_surface` (every entry must
    correspond to a real surface) and
    `test_no_unallowlisted_forbidden_surface_in_scanner` (every forbidden
    surface must be allowlisted or eliminated).
  - `importlib.resources` added to `ALLOWED_FORBIDDEN_MODULE_IMPORTS`
    for bundled-package files (e.g. `fixtures.py`, `triggers.py`).
    `importlib.metadata` remains allowed for plugin/adapter discovery.
  - `_scan_source` now returns structured `Violation` objects
    (`line`, `surface`, `message`) instead of preformatted strings, so
    callers can route by `surface` against `ALLOWED_EXCEPTIONS`.
  - STABILITY.md "Trust-model invariants" widened to cite the entire
    scanner package and adds a "Meta-CLI surfaces (allowlisted,
    audited)" subsection documenting each of the four entries.

- **v0.17 / M1 trust-hardening: severity-override floor + audit.**
  - `core.models.CheckMetadata` gains an optional `floor_severity` field
    (Severity | None). 16 release-critical built-in checks now declare a
    hard floor:
    - `SHIP-POLICY-APPROVAL-MISSING` (critical → floor "high")
    - `SHIP-ACTION-{FINANCIAL-WRITE-CONTROL-MISSING, DESTRUCTIVE-ROLLBACK-MISSING,
      WILDCARD-SCOPE, EFFECT-ESCALATED, APPROVAL-REMOVED}` (critical → floor "high")
    - `SHIP-AUTH-{MISSING-SCOPE, MANIFEST-BROAD-SCOPE, TOOL-BROAD-SCOPE,
      SCOPE-COVERAGE-MISSING}` (high → floor "medium")
    - `SHIP-SCOPE-{TOOL-OUTSIDE-PURPOSE, PROHIBITED-TOOL-PRESENT}` (high → floor "medium")
    - `SHIP-INVENTORY-{WILDCARD-TOOLS, LOW-CONFIDENCE-PRODUCTION-SURFACE}` (high → floor "medium")
    - `SHIP-POLICY-CONFIRMATION-MISSING` (high → floor "medium")
    - `SHIP-SIDEFX-IDEMPOTENCY-MISSING` (high → floor "medium")
  - Any `checks.severity_overrides` entry that resolves below the floor
    is rejected as a manifest config error (exit 2). The floor is hard;
    no acknowledgement bypasses it. **Breaking** for manifests that
    previously downgraded these checks below their new floor — fix by
    raising the override to floor-or-above, or removing the override.
  - `checks.severity_overrides` accepts both the legacy scalar form
    (`SHIP-XYZ: medium`) and a new rich form
    (`SHIP-XYZ: { severity, reason, expires }`). Reason flows into the
    new audit row; expires gives reviewers a time-bounded override.
  - New `checks.acknowledge_overrides[]` block. Required for any
    severity override whose application crosses a severity tier
    boundary (critical ↔ high, high ↔ medium/low/info) as a downgrade.
    Tier-crossing **upgrades** never require ack (strictly more
    conservative). Same-tier downgrades (medium → low) don't require ack.
    For checks emitted with manifest-declared severity (action-surface
    policies via `SHIP-ACTION-POLICY-VIOLATION`, policy-pack rules)
    the resolver compares against the strongest declared severity
    across the manifest, not the static catalog default — so a
    `severity: critical` action policy with override `high` is
    correctly tier-crossing and requires ack.
  - Expired `acknowledge_overrides` entry raises a manifest config error
    (exit 2) — no advisory-mode bypass. Same hard contract applies to
    `expires` on rich-form `severity_overrides` entries.
  - New top-level `report.policy_audit` block surfacing every applied
    override:
    `policy_audit.severity_overrides_applied[].{check_id,
    default_severity, applied_severity, manifest_path, reason,
    tier_crossed, direction, expires}`. Always emitted on scans (empty
    envelope when no overrides applied); required + non-nullable on
    the wire (mirrors the v0.12 `agent_summary` pattern). Lands at
    `report_schema_version: "0.17"` alongside M8's
    `release_decision.contribution_rules[]` — both audits are additive
    and share the same schema bump.
  - Markdown report renders a new "Policy Audit" section between
    Release Decision and Summary when overrides exist. GitHub step
    summary adds a one-liner counting overrides + tier-crossed +
    upgrades/downgrades.
  - New module `core/severity_overrides.py` owns floor/tier/ack/expiry
    resolution as a pure function; `core/findings.py::apply_severity_overrides`
    still consumes a flat `dict[str, Severity]` so existing direct
    callers and tests stay byte-compatible.
  - `AgentsShipgateManifest.severity_overrides()` still returns the
    flat scalar projection for back-compat; new
    `severity_override_entries()` returns the rich shape and
    `acknowledge_overrides()` returns the ack list.
- Added `release_decision.contribution_rules[]` — a deterministic
  per-finding audit of how each finding contributed to the release
  decision (M8 of the Trust Hardening Pass). Bumps
  `report_schema_version` to `0.17` (shared with M1's `policy_audit`).
  Exactly one row per `report.findings` entry (including suppressed)
  with `category` ∈ `{blocker, review_item, excluded}` and `rule` ∈
  `{policy_block_new, severity_block_new, policy_baseline_accepted,
  severity_baseline_accepted, review_required, sub_threshold,
  suppressed}`. The new `STABILITY.md` "Release decision truth table"
  documents which `(rule, category)` pair fires for every
  `(blocks_release, severity, baseline_status, fail_on)` combination.
  Additive only: no semantic change to `decision`, `blockers[]`,
  `review_items[]`, `fail_policy.exit_code`, or strict-mode exit codes —
  the audit reflects existing behavior, it does not modify it. The
  field defaults to `[]` for legacy reports loaded via
  `explain-finding` so consumers never need an existence check.
- Replaced the hardcoded `if/elif` source-dispatch in `cli/scan.py` with a
  real `ToolSourceAdapter` Protocol and `AdapterRegistry`. Every loader
  (MCP, OpenAPI, OpenAI Agents SDK, Google ADK, LangChain, CrewAI, n8n,
  Codex plugin, OpenAI API, Anthropic API) is now an adapter class that
  registers with `agents_shipgate.inputs.protocol.REGISTRY`. The scan
  pipeline returns a typed `ArtifactBag` so framework artifacts retain
  their concrete types into `ScanContext`. Framework adapters now fire
  correctly when configured via top-level manifest sections without a
  matching `tool_sources` entry. Internal refactor — no behavior change
  for users.
- Added minimal source provenance to findings. `agents-shipgate scan` now
  emits `report_schema_version: "0.11"` with optional structured location
  keys on `findings[].source`: `path`, `start_line`, `end_line`,
  `start_column`, and `pointer` (RFC 6901). Populated for the common
  tool-source loaders (OpenAPI, MCP, OpenAI tool artifacts, Anthropic
  tool artifacts) when the source file is YAML; JSON inputs carry `path`
  and `pointer` but no line. SARIF emits the position via
  `physicalLocation.region.startLine` (and `endLine` / `startColumn`
  when present), with the JSON pointer under
  `properties.shipgatePointer`. Capability-Intent Diff markdown appends
  `(at path:line)` to misalignment rows when provenance is available.
  `run_id` explicitly excludes the new provenance fields so YAML line
  drift cannot churn the hash. Reports without populated provenance
  remain byte-identical to v0.10 because `report_json_payload` strips
  unset keys.
- Added JSON-first tool-surface diff for PR review. `agents-shipgate scan`
  now emits `report_schema_version: "0.10"` with always-present
  `tool_surface_facts` and `tool_surface_diff` fields. The diff explains
  added/removed/changed tools, high-risk tag changes, scope drift, enforcement
  control changes, policy drift, finding deltas, and accepted debt without
  changing `release_decision.decision`, strict/advisory exit behavior, or SARIF.
- Added `agents-shipgate scan --diff-from <path>` for comparing against a prior
  `report.json` or v0.3 baseline JSON. `--baseline` still controls finding
  baseline status and strict-mode filtering; `--diff-from` controls only
  `tool_surface_diff`.
- Baseline files now save as schema `0.3` with optional `tool_surface_facts`.
  Schema `0.2` baselines continue to load for accepted-debt matching but cannot
  enable surface diff by themselves.
- GitHub Action adds `diff_from`, `diff_base`, and `diff_enabled`. Setting
  `diff_base: target` performs a best-effort target-branch scan with the
  PR-side installed package and falls back to a disabled diff note on fetch,
  config, schema, or scan failures.
- Release Evidence Packet schema bumped to `0.2` with a compact
  `tool_surface_diff` section derived from the report JSON.
- Added optional manifest-level HITL validation evidence mode under
  `validation:`. The scanner now reads local approval traces, override logs,
  high-risk auto-approval exclusions, and promotion criteria to structure
  evidence gaps for reviewers; it does not generate those runtime artifacts or
  certify readiness.
- Tightened HITL evidence wording and provenance. `SHIP-EVIDENCE-*` findings
  now describe missing or incomplete local review evidence without implying
  runtime controls are absent, and include deterministic
  `evidence.source_provenance[]` entries. `source_provenance` is excluded from
  finding fingerprints, so adding provenance does not rotate existing HITL
  baselines or suppressions.
- Release Evidence Packet schema bumped to `0.3` with
  `human_in_the_loop.runtime_control_disclaimer`,
  `human_in_the_loop.source_provenance[]`, and
  `human_in_the_loop.provenance_mode`.
- Added `samples/hitl_evidence_covered_agent`, a refund-domain fixture with
  local approval trace, override log, high-risk exclusion, and promotion
  criteria evidence.
- Added four `SHIP-EVIDENCE-*` checks. Existing baselines may surface these as
  new findings after upgrade when a manifest opts into `validation:`.
- Add `agents-shipgate scenario suggest` (target: `0.9.1`), a YAML export that
  fans out `report.json.suggested_scenarios[]` into concrete
  per-finding/per-tool dynamic validation steps.
- Added ranked next-action diagnostics: `detect --json` and `doctor --json`
  now emit `diagnostics: [...]` and `next_actions: [...]` blocks alongside
  the existing single-string `next_action` field. Coding-agent callers can
  recover from common first-run failures (missing manifest, zero tools,
  unresolved `CHANGE_ME`, missing source files, MCP/OpenAPI artifact-only
  workspaces, dynamic toolsets, production targets without permissions, and
  three negative-control cases) without consulting human-facing docs. Errors
  emitted under `AGENTS_SHIPGATE_AGENT_MODE=1` carry the same `next_actions`
  array. Diagnostic catalog and schema in [docs/diagnostics.md](docs/diagnostics.md).
- Behavior change: when a required `tool_sources[].path` does not
  resolve (file missing OR resolves outside the manifest directory),
  `agents-shipgate doctor --json` exits **0** with
  `unresolved_sources: [...]` and a `SHIP-DIAG-MISSING-SOURCE-FILE`
  diagnostic so an agent gets a routable next action. The non-JSON
  `agents-shipgate doctor` form prints the same diagnostic in
  human-readable form and exits **3** so interactive users still see a
  loud failure. `agents-shipgate scan` is unchanged — it still raises
  `InputParseError(3)` on the same condition regardless of `--json`.
- `DetectResult` gains a `workspace_signals` block (Python file count,
  `pyproject.toml`/`requirements.txt` presence, conventional dir hits) used
  by the new diagnostic resolvers to discriminate negative-control cases.
  The block is additive; existing fields are unchanged.

## 0.8.0 - 2026-05-05

- Report schema bumped to `v0.8`. New top-level required `release_decision` block:
  `{decision, reason, blockers, review_items, evidence_coverage, baseline_delta, fail_policy}`.
  - `decision` is one of `"blocked" | "review_required" | "passed"` and is the
    recommended release-gate signal for v0.8+ consumers.
  - `blockers` and `review_items` are reference-only entries
    (`id, fingerprint, check_id, severity, title, baseline_status`) — full
    Finding payloads stay in `findings[]`.
  - `release_decision` is **baseline-aware**: matched criticals appear in
    `review_items` (accepted debt), not `blockers`. Critical severity is
    **policy-independent** — even advisory CI surfaces a new critical as a
    blocker (with `would_fail_ci=false`).
  - `release_decision.fail_policy.exit_code` matches the process exit code
    one-for-one across all `ci_mode` × `fail_on` × `--baseline` combinations.
- `summary.status` is preserved byte-for-byte for backwards compatibility
  with v0.7 consumers. It stays baseline-blind (a baseline-matched critical
  still flips status to `release_blockers_detected`). The intentional
  divergence from `release_decision.decision` is documented in
  [STABILITY.md](STABILITY.md#release_decisiondecision-vs-summarystatus).
- `docs/report-schema.v0.8.json` added; `v0.7.json` retained as a frozen
  reference. JSON-schema validation catches missing `release_decision` on
  any emitted report.
- Markdown / GitHub Action / CLI summaries now lead with the Release
  Decision block (Decision → Reason → Blockers → Review items → Evidence
  coverage → Baseline delta → Fail policy). SARIF output is unchanged.
- GitHub Action exposes four new outputs: `decision`, `blocker_count`,
  `review_item_count`, `ci_would_fail`. Existing outputs (`status`,
  `critical_count`, `baseline_*`, `adk_*`, `report_*`, `exit_code`)
  unchanged.
- The release verdict path remains deterministic and LLM-free: no agent
  execution, tool call, model call, MCP connection, network access, or
  telemetry is added for v0.8.
- `exit_code_for_report()` refactored to share `effective_fail_on()` and
  `baseline_filtered_active()` helpers with `build_release_decision()`,
  so the standalone exit code and `release_decision.fail_policy.exit_code`
  cannot drift. New regression test pins this across the matrix.

## 0.7.0 - 2026-05-01

Adoption activation: makes the v0.6 features visible to humans and AI
coding agents on real repos, plus exposes per-check remediation
metadata so agents can route findings without re-walking the catalog.

- Agent-facing docs surface:
  - New "Should I run Shipgate on this PR?" trigger table in
    `AGENTS.md` with the soft-stop rule (don't skip MCP/OpenAPI-only
    repos that surface as `is_agent_project: false`).
  - New `docs/agent-recipes.md` — copy-pasteable AI-agent workflows
    for the canonical 4-call flow.
  - New `docs/autofix-policy.md` — four classes (safe / medium /
    manual / never), catalog-vs-Finding contract, strict derivation
    rule, three patch states, unknown-check-id fallback,
    `apply-patches --confidence` table, decision tree.
  - New `docs/minimal-real-configs.md` — per-framework references to
    runnable `samples/*` fixtures (no inline snippets to drift).
  - `docs/INDEX.md` cleanup: stale `report-schema.v0.5.json` link
    removed; current schema link now `report-schema.v0.7.json`.
  - `docs/quickstart.md` adds a "second 60 seconds" real-repo path.
- `CheckMetadata` extensions:
  - New `autofix_safe`, `requires_human_review`, `suggested_patch_kind`
    fields on every check (45 entries). `docs_url` populated for every
    check pointing at a stable `### SHIP-...` anchor in
    `docs/checks.md`. 7 new per-check sections added to `docs/checks.md`
    so every check has a stable anchor.
  - Catalog-level safety bools stay conservative — even checks whose
    generator usually produces a safe non-manual patch (stale-manifest
    removals, scope coverage) keep `autofix_safe: false` /
    `requires_human_review: true` because the generator can fall back
    to `ManualPatch` in edge cases (ambiguous duplicates, etc.).
    `suggested_patch_kind` is informational — describes what the
    generator targets when conditions are clean.
- `Finding` extensions + derivation:
  - Same four optional fields on every Finding, populated by
    `annotate_remediation` during scan. Three patch states handled
    distinctly:
    - `patches: None` (no `--suggest-patches`) → seed from
      CheckMetadata; safe-closed fallback for unknown check IDs
      (policy packs, third-party plugins).
    - `patches: []` (--suggest-patches ran but generator emitted
      nothing) → safe-closed shape with `suggested_patch_kind: "none"`.
      Does NOT fall back to catalog (the report carries no patches).
    - `patches: [...]` (non-empty) → strict derivation rule:
      `autofix_safe: true` ONLY when EVERY emitted patch is non-manual
      AND high-confidence. Mixed states fall to safe-closed.
  - `docs_url` always sourced from CheckMetadata (patches don't carry
    per-instance documentation URLs).
- Report schema bumped to `v0.7` per
  [STABILITY.md](STABILITY.md#stability-contract) ("`report_schema_version`
  bumps minor on additive changes"). `docs/report-schema.v0.7.json`
  added; `v0.6.json` retained as a frozen reference.
- `_run_id` excludes the four new derived fields plus `patches` so
  toggling `--suggest-patches` (or future enrichment fields) doesn't
  shift the hash. New regression test pins this.
- Plugin-loading isolation: every code path that reads the catalog
  during scan honors the scan's `plugins_enabled` setting, including
  the `_attach_patches` recommendation lookup.
  `AGENTS_SHIPGATE_ENABLE_PLUGINS=1 agents-shipgate scan --no-plugins`
  no longer loads plugins.
- Onboarding prompt rewrite: `prompts/add-shipgate-to-repo.md` now
  leads with the canonical 4-call flow (`detect → init --write --ci →
  scan --suggest-patches → apply-patches --json`) and includes the
  decision tree from `docs/autofix-policy.md`. Soft-stop rule
  documented inline. `apply-patches --json` flag added so the
  reporting step has structured data to read.
- Dual-copy prompt parity: byte-identical mirror between
  `prompts/` and `skills/agents-shipgate/prompts/` enforced by
  `tests/test_prompt_parity.py` so the two surfaces can't drift.
- Test coverage: 314 tests pass. New test files:
  `tests/test_remediation_metadata.py`,
  `tests/test_finding_remediation.py`,
  `tests/test_docs_links.py`,
  `tests/test_prompt_parity.py`,
  `tests/test_v07_metadata_roundtrip.py`.

## 0.6.0 - 2026-04-30

Agent-friendly adoption: compresses Shipgate setup into a single
tool-using turn for AI coding agents.

- Added `agents-shipgate detect` — read-only command that classifies a
  workspace as an agent project and reports which framework(s) it uses,
  with confidence and per-framework evidence.
- `agents-shipgate init` now auto-detects by default. Generated
  manifests are schema-valid (validated before write) and include
  framework-specific tool sources and config blocks (LangChain, CrewAI,
  Google ADK, OpenAI Agents SDK, Anthropic, OpenAI API). The legacy
  CHANGE_ME-heavy template is preserved under `--minimal`.
- Added `agents-shipgate init --ci` — opt-in flag that writes
  `.github/workflows/agents-shipgate.yml`. Orthogonal to `--write`:
  each gets its own overwrite-refusal check. Detects cross-workflow
  shipgate references and skips with a distinct message.
- Added `agents-shipgate scan --suggest-patches` — attaches Patch
  objects to every active finding (machine-applicable for the safe
  subset; ManualPatch for everything else). `Finding.patches` is
  absent when the flag is not set; non-opting JSON consumers see no
  contract change.
- Added `agents-shipgate apply-patches` — applies patches from a scan
  JSON report. File-grouped, single SHA per file, dry-run by default,
  containment-checked against the report's new `manifest_dir` field.
- v0.6 patch generators (manifest-target only):
  - High-confidence `RemovePointerPatch` for the 3 stale-manifest
    checks (SUPPRESSION, POLICY, RISK-OVERRIDE).
  - Medium-confidence `AppendPointerPatch` for
    `SHIP-AUTH-SCOPE-COVERAGE-MISSING` (NOT applied at default
    `--confidence high` — adding scopes can encode policy choices).
  - Permanent `ManualPatch` (with anti-pattern instructions) for
    `SHIP-API-TRACE-{APPROVAL,CONFIRMATION}-MISSING` — flipping
    approved/confirmed in a trace patches the evidence, not the agent.
- Bumped report schema to v0.6 (additive: optional `Finding.patches`
  array; new top-level `manifest_dir`). v0.5 schema retained for
  reference.
- Anthropic-specific glob coverage in `init`: tools and policies
  matching `tools/anthropic-tools.json` and
  `policies/anthropic-policy.yaml` now populate the `anthropic:` block
  automatically.
- Added end-to-end agent task `02_three_command_flow` exercising the
  full `detect → init → scan → apply-patches` pipeline.
- Added `ruamel.yaml>=0.18` as a dependency for round-trip-preserving
  YAML edits in `apply-patches`.

## 0.5.1 - 2026-04-29

- Polished launch-facing docs after the v0.5.0 release.
- Updated active examples and discovery metadata to the v0.5.1 release tag.
- Added curated launch marketing and presentation assets while excluding them
  from PyPI source distributions.
- Fixed stale baseline-mode CLI help text.

## 0.5.0 - 2026-04-28

- Added static LangChain/LangGraph and CrewAI Python adapters with manifest
  source types, supplemental inventories, framework report blocks, fixtures,
  and self-check coverage.
- Added framework-specific checks for dynamic LangChain/CrewAI tool surfaces
  and missing function-tool metadata.
- Promoted GitLab CI and CircleCI to first-class integration recipes with
  advisory, strict baseline, artifact, multi-config, and tool-source trigger
  examples.
- Added report schema v0.5 for additive LangChain/CrewAI framework fields.
- Added a framework adapter checklist for future static framework support.
- Deduplicated `source_warnings`; baselines from 0.4.x may report a small
  number of resolved warning entries on first run after upgrade.

## 0.4.0 - 2026-04-27

- Added declarative YAML policy packs with manifest, CLI, report, SARIF, and GitHub Action support.
- Split `SHIP-API-OPERATIONAL-READINESS` into atomic OpenAI API operational readiness check IDs.
- Kept `SHIP-API-OPERATIONAL-READINESS` as a deprecated compatibility alias for suppressions, severity overrides, baseline matching, and check metadata.
- Removed the legacy top-level `check_severity_overrides` alias; use `checks.severity_overrides`.
- Added report schema v0.4 with `loaded_policy_packs` and stabilized Google ADK warnings in the framework surface.
- Added an internal framework adapter seam and documented runtime inventory as design-only.

## 0.3.0 - 2026-04-26

- Added static Google ADK support through `tool_sources[].type: google_adk` and supplemental `google_adk` manifest artifacts.
- Added ADK Python AST and Agent Config YAML extraction for agents, function tools, toolsets, callbacks/plugins, sub-agents, eval references, and explicit local inventories.
- Added six ADK readiness checks covering dynamic toolsets, unfiltered MCP toolsets, missing function metadata, long-running contracts, guardrail evidence, and production eval coverage.
- Added SARIF output via `--format sarif` and GitHub Action SARIF/baseline/ADK outputs.
- Added report schema v0.3 with a generic `frameworks.google_adk` surface summary.
- Added reusable local trace normalization for explicit trace/eval artifacts.

## 0.2.0 - 2026-04-26

- Added manifest-aware checks, deterministic report metadata, check severity overrides, `fail_on`, `init`, `doctor`, `explain`, multi-config scan support, and check entry-point hooks.
- Renamed the project to Agents Shipgate and hardened v0.1 release-readiness behavior.

## 0.1.0

- Initial Agents Shipgate MVP.
- Manifest-first scan over local MCP JSON, OpenAPI specs, and optional OpenAI Agents SDK AST metadata.
- Markdown and JSON reports.
- Advisory and strict CI modes.
- GitHub composite action.
