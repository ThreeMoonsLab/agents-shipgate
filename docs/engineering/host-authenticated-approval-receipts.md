# Design: host-authenticated in-session approval receipts

Status: proposed. Owner: unassigned. Filed from the 2026-07-26/27 coding-agent
UX reviews. Supersedes the unsigned local-receipt sketch that was reviewed and
**rejected** in July 2026; §"Why the unsigned version was rejected" records why,
so the same design is not re-proposed by accident.

Tracking issue: https://github.com/ThreeMoonsLab/agents-shipgate/issues/293

## The problem

A user asks their coding agent to add an MCP server to `.mcp.json`. The host
prompts, the user allows it, the edit lands — and `shipgate check` returns
`human_review_required` with `must_stop: true`. The user *is* the human. They
already said yes. There is no sanctioned way to record that, so the turn ends
with the agent telling the person who just approved the change that a human must
approve the change.

Contract v19's graded band removed this for low/medium rows. It deliberately did
**not** remove it for the rows that matter most here: `HOST-MCP-SERVER-ADDED` is
`high`, permission expansion is `high`, and gate-governing trust roots are
excluded at any risk. Those stay stops, which is correct — and it means the
stuck state is now narrow, well-defined, and worth solving properly.

## Why the unsigned version was rejected

The first proposal stored an unsigned receipt in the private Git directory and
let `check`/`preflight` consume it to clear routing. Three findings killed it:

1. **It is agent-forgeable.** The CLI's "am I being run by an agent" gate is an
   environment heuristic. A receipt that clears a human route, written by a
   command the agent can run, is self-approval with extra steps — and it would
   have cleared `block` and `critical` rows too, since nothing tied consumption
   to the graded band.
2. **Preflight evaluates the wrong tense.** Receipts bind bytes that already
   landed; preflight routes *proposed* edits. A receipt for the current file
   would clear review for arbitrary future content.
3. **It cannot reach CI, and must not reach reports.** The ledger lives in one
   developer's `.git`. Annotating `report.json` with it would make the same
   commit produce different artifacts on different machines, breaking the
   content-addressed reproducibility contract.

The lesson: an in-session approval is only worth anything if its *authenticity*
comes from the host, not from a file the agent can write.

## Proposal

A receipt is a statement by the **host** that a specific human answered a
specific permission prompt about a specific proposed edit.

### Binding

Bind to the host's own request identity, not to a path:

- `tool_use_id` — the exact tool call the host prompted about. Both PreToolUse
  and PostToolUse expose it, so the prompt and the landing can be correlated.
- `session_id`, and the host's own decision value.
- the proposed content hash (from the PreToolUse payload's edit), plus the
  post-edit hash observed at PostToolUse — so approving edit A cannot license
  edit B.
- `expires` evaluated against `trust_expiry_date()`, so a backdated commit
  cannot extend it.

### Authenticity

The receipt is only consumable when the host attests it. Options, in order of
preference:

1. **Host-signed attestation.** The host (or a broker it owns) signs the receipt
   with a key the agent cannot read, verified against a trust policy outside the
   evaluated workspace — the same shape contract v18 already established for
   `human_authorization` (fixed `~/.config/agents-shipgate/…` path, ownership
   and permission checks, no signing command shipped by Shipgate).
2. **Host-written, agent-unwritable location.** Weaker but real: a path the host
   writes and the agent's tool surface cannot, declared in the trust policy.

Absent either, the receipt is **annotation-only** — recorded, surfaced, never
consumed for routing. Fail-closed is the default, not the exception.

### Consumption, narrowly

- Only `check` and only for rows in an explicit `RECEIPT_ELIGIBLE_RULE_IDS`
  allow-list. `HOST-MCP-SERVER-ADDED` and `HOST-PERMISSION-ALLOW-EXPANDED` are
  the intended first members.
- Never for `block` actions, `critical` risk, gate-governing trust-root classes,
  incomplete or unparseable input, or gate-weakening rules — the v19 exclusions
  apply unchanged and are additionally enforced at the consumption site.
- A consumed receipt yields `agent_action_required` with the verify route and a
  `pending_review[]` entry annotated with the local approval. It never yields
  `complete`, and it never touches `release_decision`.
- **Not** consumed by preflight (wrong tense, see above) unless a future design
  binds a canonical proposed diff with old and new hashes.

### PR-time

Verify keeps its current semantics exactly. If reviewers should see that a local
human approved an edit, the carrier is the PR body or a host-side comment — not
`report.json`, `packet.json`, `human_ack`, attestations, or the registry.

## Prerequisites

- A host integration willing to attest. Shipgate ships no signer; without a host
  adapter this design produces annotation-only receipts, which is still an
  improvement over discarding the answer but does not clear the stuck state.
- Concurrency-safe ledger writes (parallel PostToolUse hooks are documented as
  possible): lock plus atomic replace, size and row caps, expiry pruning,
  containment checks on recorded paths.

## Acceptance

- A user-approved MCP-server addition, attested by the host, lets the turn
  finish with the obligation carried in `pending_review[]`.
- A receipt written by anything other than the attesting host changes no
  routing.
- A receipt for edit A does not clear edit B to the same file.
- Forging the ledger cannot alter a `block`, a `critical` row, or any
  gate-governing trust-root row.
- `report.json`, `verifier.json`, and packet artifacts are byte-identical with
  and without a ledger present.

## Interim state (today)

The stuck state stands, with two mitigations already shipped: the graded band
removed it for low/medium rows, and the Stop hook no longer forces continuation
when a human route is the honest answer — the turn ends with a hand-off instead
of a loop. The remaining gap is exactly the high-risk host-grant rows above.
