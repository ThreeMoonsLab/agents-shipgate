# The Agent-Native Merge Contract

Agents Shipgate is the deterministic merge gate for AI-generated agent
capability changes. Underneath that tagline is a protocol: the set of guarantees
that let a coding agent know — **without human interpretation** — whether it may
continue, must repair, or must stop for human authority.

This page is the map. It names the eight contracts that make up the protocol and
points each one at the artifact that already implements it. It documents what
exists; it does not propose new architecture. For the exact JSON field list, see
[`agent-contract-current.md`](agent-contract-current.md); for the stability
guarantees, see [`../STABILITY.md`](../STABILITY.md).

## The one principle everything rests on

**One decision engine.** `report.json.release_decision.decision` is the only
release gate, and **no agent-facing field gates independently of it**. Some
fields are direct projections of the decision (`merge_verdict`, the `decision`
mirror); others project from related head-scan substrate — `capability_review`
from `capability_change`, `applicability` from scan applicability
(decision-presence + `head_status`), the `agent_controller` deny-lists from the
trust-root surface list — but all are subordinate to the decision, and none
computes a second verdict. This is enforced structurally (construction-time
validators in
[`../src/agents_shipgate/schemas/verifier.py`](../src/agents_shipgate/schemas/verifier.py))
and covered by a totality test, so a field that drifts from the gate fails CI
rather than shipping.

Everything below is a contract *about how that one decision is surfaced and
protected* — never a new way to decide.

## The eight contracts

### 1. Trigger Contract — *should I run at all?*

- **Guarantee:** a diff is classified deterministically as relevant or not, so
  an agent neither wastes a run on a docs-only change nor skips a real
  capability change.
- **Implements it:** [`triggers.json`](triggers.json) (machine-readable mirror
  of the AGENTS.md trigger table) and `agents-shipgate verify --preview`.
- **Agent reads:** `run_shipgate` / `first_next_action` (`none` for irrelevant
  diffs, `detect`/`init` for relevant unconfigured repos, `verify` for
  configured ones).
- **Prevents:** silently skipping an MCP/OpenAPI/SDK surface change; running on
  prose.

### 2. Capability Change Contract — *what can the agent now do?*

- **Guarantee:** review is framed by the change in **capability**, not the
  change in files.
- **Implements it:** `capability_change` (report.json, v0.22+) →
  `capability_review` (verifier.json).
- **Agent reads:** `capability_review.top_changes[]` with `{id, impact,
  rationale, related_finding_ids}`.
- **Prevents:** reviewing "+20 lines in refund.py" instead of "a money-movement
  action was added".

### 3. Merge Verdict Contract — *may it merge?*

- **Guarantee:** a single machine verdict the agent can switch on, that cannot
  disagree with the gate.
- **Implements it:** `merge_verdict` (`mergeable` / `human_review_required` /
  `insufficient_evidence` / `blocked` / `unknown`), a projection of
  `release_decision.decision`; plus `applicability` (`verified` /
  `not_applicable` / `unknown`), derived from whether Shipgate evaluated the
  change (decision-presence + `head_status`) — orthogonal to the verdict, never
  a second gate.
- **Agent reads:** `merge_verdict`, `can_merge_without_human`.
- **Prevents:** a second verdict; and — via `applicability` — reading a
  `mergeable` that came from a *skipped* scan as "verified safe".

### 4. Repair Contract — *what may be fixed, and by whom?*

- **Guarantee:** mechanical gaps are separated from authority gaps, and every
  fix attempt ends with a fresh verdict.
- **Implements it:** `fix_task` `{actor, safe_to_attempt, instructions[],
  forbidden_shortcuts[], verification_command}` —
  [`../src/agents_shipgate/cli/verify/fix_task.py`](../src/agents_shipgate/cli/verify/fix_task.py).
- **Agent reads:** `fix_task`. `actor: coding_agent` + `safe_to_attempt: true`
  means "apply the listed mechanical fix, then re-run `verification_command`";
  `actor: human` means stop.
- **Prevents:** an agent inventing its way past an authority gap; a one-shot
  "fix" that never re-verifies.

### 5. Forbidden Action Contract — *what must never be done to pass?*

- **Guarantee:** the reward-hacking moves are named explicitly and stand on
  every verdict, including `mergeable` — green is never "anything goes".
- **Implements it:** `agent_controller.forbidden_actions[]` (the action-level
  deny-list) and `agent_controller.forbidden_file_edits[]` (a path-level
  deny-list of whole-file trust roots) —
  [`../src/agents_shipgate/cli/verify/agent_controller.py`](../src/agents_shipgate/cli/verify/agent_controller.py).
- **Agent reads:** `agent_controller.{forbidden_actions, forbidden_file_edits}`.
- **Prevents:** suppressing findings, lowering severity, fabricating
  approval/idempotency evidence, deleting the CI gate. The file deny-list is
  deliberately **not** an allow-list, and excludes `shipgate.yaml` /
  `.agents-shipgate` — their trust boundary is *key-level* (editing an action's
  scope is a legitimate fix; a `checks.ignore` / baseline expansion is not), so
  those are covered by `forbidden_actions` instead.

### 6. Human Authority Contract — *what only a human can grant?*

- **Guarantee:** human authority is *declared evidence*, never inferred — and it
  cannot be synthesized by the change under review.
- **Implements it:** `human_ack` (report.json declared state) plus the
  self-approval prohibition surfaced as `agent_controller.stop_reason =
  self_approval_prohibited` and `human_review.why`.
- **Agent reads:** `human_review.required`, `agent_controller.must_stop`,
  `agent_controller.stop_reason`.
- **Prevents:** a PR that adds its own `human_ack` (or weakens the policy) and
  then self-approves in the same change.

### 7. Trust Root Contract — *the judge cannot be weakened by the judged.*

- **Guarantee:** the rules that evaluate a change cannot be quietly weakened by
  that same change.
- **Implements it:** the hardcoded `TRUST_ROOT_SURFACES` and the `SHIP-VERIFY-*`
  checks (suppression-immune, severity-floor-protected) in
  [`../src/agents_shipgate/checks/verify.py`](../src/agents_shipgate/checks/verify.py).
  Touching a trust root forces human review; a clear weakening blocks.
- **Agent reads:** `capability_review.trust_root_touched`,
  `capability_review.policy_weakened`.
- **Prevents:** an agent editing `shipgate.yaml`, the CI gate, `AGENTS.md`, the
  policy pack, the baseline, or a waiver to make its own change pass.

### 8. Attestation Contract — *a durable record of what was released.*

- **Guarantee:** an agent-capability release leaves an attestation, not a
  memory: which capability shipped, under which verdict, acknowledged by whom.
- **Implements it:** `agents-shipgate attest` derives a deterministic, local,
  JSON-first attestation from `verifier.json` (+ the sibling `report.json`) —
  base/head SHAs, the verdict, the capability delta, declared `human_ack` state,
  a policy-snapshot hash, and content hashes of every verify artifact. It is
  content-addressed (no wall-clock timestamp) and does not gate. Schema:
  [`attestation-schema.v0.1.json`](attestation-schema.v0.1.json).
- **Agent reads:** the attestation is a durable record for humans and
  registries, not a control signal — the agent still acts on `agent_controller`
  (contracts 3–5).
- **Prevents:** "we think a human approved that refund tool last quarter" with
  no record to point at.

## The agent control loop

The four imperative questions collapse into one block,
`verifier.json.agent_controller`, which an autonomous agent can act on directly:

1. `completion_allowed` is `true` → the capability-change task is done; merge.
2. else `must_stop` is `true` → surface `user_message_template` and `stop_reason`
   to a human; **do not** edit anything in `forbidden_file_edits` or take any
   `forbidden_actions` to get past the gate.
3. else → apply the `fix_task` (mechanical), then re-run its
   `verification_command` and read the fresh verdict.

`completion_allowed` is locked to `can_merge_without_human`, so step 1 can never
contradict the gate.

## Where to read each surface

| Reader | Read first | Source of truth |
| --- | --- | --- |
| Coding agent (controller) | `verifier.json.agent_controller` → `merge_verdict` | `release_decision.decision` |
| Human reviewer | `pr-comment.md` | `release_decision.decision` |
| CI gate implementer | `report.json.release_decision.decision` | same |
| Discovery (agents/search) | [`../.well-known/agents-shipgate.json`](../.well-known/agents-shipgate.json) | — |

Field-level contract: [`agent-contract-current.md`](agent-contract-current.md).
Stability guarantees across `0.x`: [`../STABILITY.md`](../STABILITY.md).
