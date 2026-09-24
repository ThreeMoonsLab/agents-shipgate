# Engineering review record

PR scope: Days 1–5 roadmap, baseline and comparison design. Reviews are coding-agent
self-reviews, not independent human review or qualification evidence.

## Round 1 — evidence and product scope

Found and addressed:

- Fresh-run ledger read stdout only; normalize successful verify fields from
  the actual `verifier.json` so null projections cannot contradict the table.
- Remove truncated stderr command dumps from curated data; retain the exact
  refusal message. Raw reports stay local.
- Distinguish the 27 reused-config head scans from eight fresh exact-ref verifies;
  document that config hashes alone cannot recreate all historical inputs.
- Mark the old roadmap scheduling as historical and explicitly retain #830.
- Keep #610 as reproduced/open and #580/#655 as design, not delivered runtime.

## Round 2 — acceptance and contract review

Checked the 54 ledger rows against the aggregate; all 27 PRs have both engines
and identical recorded source/config identity. Checked six successful fresh
verifier artifacts and two gitlink refusals. Counts are 23 empty inventories per
engine, not 22 (the review-required empty case must also count). Checked added,
relocated, unconfigured, incomplete and ambiguous scope semantics; advisory
origin cannot become reviewed-base evidence. No further blocking design findings.

Lint and diff whitespace checks pass. Full-suite outcome is recorded in the PR
once the required run completes; research data is not a verifier receipt.
