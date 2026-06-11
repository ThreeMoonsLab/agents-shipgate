# Hosted Plane — Design Boundary

> Status: **design note**, not a commitment. Per the README's pricing
> stance: the core manifest-first scanner, built-in checks, and report
> formats stay free OSS; anything hosted lives in a *separate optional
> product* and never moves core functionality behind a paywall.

## What the OSS core already produces (the interface)

Every artifact a hosted plane would aggregate already exists locally and
is deterministic:

| Local artifact | Hosted aggregation it enables |
|---|---|
| `attest` output / `registry ingest` rows | org-wide capability-release ledger ("who granted which agent what, under which verdict, acked by whom") |
| `feedback export` (redacted) | fleet-level verdict-quality metrics; benchmark scenario sourcing |
| capability locks + diffs | cross-repo capability search ("which repos can refund?") |
| baseline + audit JSONL | debt aging, suppression/override drift dashboards |
| host-audit inventory (`audit --host --json`) | org map of coding-agent grants (MCP servers, wildcard allows, workflow write scopes) |

**The hosted plane consumes these files; it never produces verdicts.**
The gate stays local and deterministic — a dashboard that could overrule
`release_decision.decision` would be a second decision engine, which is
an explicit non-goal.

## Candidate product surface (in priority order)

1. **Org capability ledger** — `registry query` as a service: ingest
   attestation rows from CI, answer "what changed across 40 repos this
   month, who acknowledged the trust-root changes." Wedge: the audit
   trail security teams currently reconstruct by hand.
2. **Policy-pack distribution** — versioned org packs with the `sha256`
   pin workflow built in (publish → repos pin → dashboard shows drift).
3. **Baseline/debt aging** — accepted-debt dashboards over baseline
   audit logs; nudge expiring acknowledgements.
4. **Host-grant map** — fleet view of `audit --host` inventories;
   wildcard-allow and `pull_request_target` heat map.

Explicitly *not* in scope: hosted scanning (the scan is local by trust
model), LLM-based review, runtime monitoring.

## Trust model implications

- Everything uploaded is already redacted by the local privacy pass;
  the feedback/attestation schemas carry no raw tool schemas or env
  values. The hosted plane adds **no new collection** — it receives only
  files a human could read in the repo.
- Ingest is push-only from CI (no callbacks into repos, no tokens with
  write scopes).
- Rows are content-addressed (registry `row_id`); the server cannot
  silently rewrite history without breaking hashes.

## Build trigger (when to start)

Start only when **both** are true:

1. ≥3 design partners independently ask for a cross-repo view of
   attestations/registry rows they are already producing locally.
2. The v1.0 report/verifier freeze has shipped (the hosted plane should
   consume a frozen contract, not chase a moving 0.x).

Until then, the local `registry` command is the product: it proves the
data model and keeps the OSS/commercial boundary honest.
