# Organization Governance

Agents Shipgate keeps the release gate local and deterministic while adding
organization-level evidence around it: pinned policy packs, exception hygiene,
host-grant drift, release attestations, org evidence bundles, and an append-only
local registry.

`report.json.release_decision.decision` remains the only merge/release gate.
`agents-shipgate org status` is an opt-in governance gate for scheduled CI and
platform-team audits.

## Manifest

```yaml
organization:
  id: acme
  repo: github.com/acme/support-agent
  service: support-agent
  tier: production
  teams:
    agent-platform:
      reviewers: ["@acme/agent-platform"]
    security:
      reviewers: ["@acme/security"]
  exception_policy:
    require_owner: true
    require_reason: true
    require_expiry: true
    max_age_days: 180
  audit:
    registry: .agents-shipgate/registry.jsonl
```

The fields are structural only. Shipgate does not call GitHub, resolve teams,
or fetch organization policy.

## Policy Packs

Treat organization packs like dependencies: vendor or sync them into the repo,
then pin their content.

```yaml
checks:
  policy_packs:
    - id: org-release
      path: vendor/shipgate-policies/org-release.yaml
      source: github.com/acme/shipgate-policies@v3
      sha256: "<sha256>"
```

`org status` reports sourced packs without a `sha256` as
`policy_pack_unpinned`, and reports changed content as
`policy_pack_pin_mismatch`. Normal `scan` still fails closed on a pin mismatch,
for an `optional: true` pack too.
For a read-only policy-pack inventory, run:

```bash
agents-shipgate org policy-packs --config shipgate.yaml --json
```

Policy-pack rules may carry routing metadata:

```yaml
owner: agent-platform
reviewers: [security]
approval:
  required: true
  teams: [agent-platform]
  min_approvals: 1
```

These fields do not change rule matching, approval enforcement, fingerprints, or
release decisions. They are emitted as `findings[].policy_routing`
reviewer/audit metadata and are validated against `organization.teams` when
teams are declared. Shipgate does not call GitHub or verify whether those
approvals happened; use deterministic predicates and `block: true` for release
gating.

Starter packs live in [`../policies/templates/`](../policies/templates/).

## Status

```bash
agents-shipgate org status --config shipgate.yaml --json
```

The command evaluates local artifacts:

- accepted debt in `.agents-shipgate/baseline.json`
- manifest suppressions
- severity overrides and override acknowledgements
- `human_ack`
- policy-pack pin state
- host-grant drift when `.agents-shipgate/host-grants.json` exists
- configured registry path presence

It exits `20` only when explicit organization governance violations exist. It
does not create a second release verdict.

When `organization.exception_policy.require_owner` or
`require_expiry` is enabled, suppressions and severity override entries can
carry `owner` and `expires` metadata alongside their existing `reason`.

## Attestations And Registry

Emit a deterministic local attestation from a verify run:

```bash
agents-shipgate attest \
  --from agents-shipgate-reports/verifier.json \
  --config shipgate.yaml \
  --ci-context github-actions \
  --out agents-shipgate-reports/attestation.json
```

`attest` validates the `verification-receipt.json` beside `verifier.json`
before it copies `request_id`, `receipt_id`, `decision_id` and
`artifact_set_id` into the attestation, and `org bundle` does the same before
it publishes them. A run that states a verdict without a valid receipt from
the same verify run exits `3` with a `Verification receipt error` line naming
the receipt; rerun `verify` rather than editing either file. See
[verification reproducibility](verification-reproducibility.md#time-and-github-actions).

### Who approved the change

`org.actor` is who triggered the run and `human_ack` is what the manifest
declares; neither is an approval by a second person. GitHub holds that as the
pull request's reviews. The workflow fetches them and `attest --reviews`
records them in the attestation's `approval` block (attestation `0.6`):

```yaml
on:
  pull_request_review:
    types: [submitted]

permissions:
  contents: read
  pull-requests: read   # the reviews API only

# … after the steps that check out the pull request's head commit
# (ref: ${{ github.event.pull_request.head.sha }}), install agents-shipgate
# (a release with --reviews) and verify that commit into
# agents-shipgate-reports/ (--head ${{ github.event.pull_request.head.sha }}) …
      - name: Fetch the pull request's reviews
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          gh api --paginate --slurp \
            "repos/${{ github.repository }}/pulls/${{ github.event.pull_request.number }}/reviews" \
            | jq 'add // []' > "$RUNNER_TEMP/reviews.json"
      - name: Attest with the approval
        run: |
          agents-shipgate attest \
            --from agents-shipgate-reports/verifier.json \
            --ci-context github-actions \
            --reviews "$RUNNER_TEMP/reviews.json" \
            --out agents-shipgate-reports/attestation.json
```

What the block establishes:

- `status` is `approved` when someone other than the pull request's author
  approved the attested head commit and no reviewer's latest review requests
  changes; `approved_stale` when the standing approvals are on an earlier
  commit; `changes_requested` when any reviewer's latest decisive review
  requests changes; `not_approved` otherwise; and `not_supplied` on every
  attestation made without `--reviews`. A reviewer's standing is their latest
  `APPROVED`, `CHANGES_REQUESTED` or `DISMISSED` review; comments do not
  change it.
- An approval by the pull request's author or a bot account never counts. It
  is listed under `ignored_approvals` with the reason.
- `source_sha256` hashes the reviews file, and every review in it must name
  this repository's pull request: a file from another pull request is refused
  (exit `3`), never partly used.
- The file must be outside the repository: a path inside the Git work tree
  around the current directory or `verifier.json`, or inside
  `GITHUB_WORKSPACE`, is refused (exit `3`), because the change under review
  could write it. So is a link committed in the repository.
- `--reviews` needs `--ci-context github-actions` and an event payload with a
  pull request (`pull_request` or `pull_request_review`): the number, its
  author and its head commit come from the event.
- `head_sha` is the commit the verify run evaluated, read from the plan its
  `verification-receipt.json` binds, and the reviews are evaluated against
  it. It must be the pull request's head: verify that commit, as above. A
  run of the default `pull_request` checkout evaluates GitHub's synthetic
  merge commit instead, and is refused (exit `3`), as are artifacts from
  another revision, a `--head-sha` naming another commit, a run over
  uncommitted working-tree changes, and a `verifier.json` with no receipt
  beside it. An approval of one commit is never recorded against another.

The approval is evidence. It never changes `verdict`, `release_decision`,
the exit code or the bypass report. `registry ingest` copies it into the row
(`approval_status`, `approval`), and `registry summary` counts rows by
`approval_status`. Rows from earlier attestations carry `null` there, and their
row ids and chain hashes are unchanged. Ingest validates the block as
`attest` writes it and refuses (exit `3`) one whose `status` its own rows do
not support. A ledger row whose block does not validate, or whose
`approval_status` is not the block's `status`, is skipped by every registry
reader and reported by `registry verify`.

Append attestations to a local JSONL ledger:

```bash
agents-shipgate registry ingest \
  --attestation agents-shipgate-reports/attestation.json \
  --registry .agents-shipgate/registry.jsonl \
  --repo acme/support-agent

agents-shipgate registry report --bypass --json

agents-shipgate registry summary --registry .agents-shipgate/registry.jsonl --json
agents-shipgate registry verify --registry .agents-shipgate/registry.jsonl --json
```

The bypass report lists rows where `merge_verdict != "mergeable"` and the
attestation does not carry satisfied human acknowledgement. It exits `0` by
default so scheduled jobs can archive the report; pass `--fail-on-bypass` when
CI should exit `20` if `bypass_count > 0`. Both `registry query --json` and
`registry report --bypass --json` include `skipped_count` and `skipped_rows[]`
for malformed ledger lines. `registry summary --json` answers fleet-level
counts by repo/service/tier/verdict plus bypass, trust-root, policy-weakening,
and human-acknowledgement counts. `registry verify --json` checks row IDs,
duplicates, malformed rows, and optional per-repo hash-chain links.

Build one compact artifact for CI upload or future read-only hosted ingestion:

```bash
agents-shipgate org bundle \
  --config shipgate.yaml \
  --from agents-shipgate-reports/verifier.json \
  --out agents-shipgate-reports/org-evidence-bundle.json \
  --json
```

The bundle includes organization metadata, attestation payload, registry-row
projection, org status, policy-pack pin records, host-grant summary, artifact
hashes, and privacy/redaction status. It names
`gating_signal: release_decision.decision` and never computes a verdict.

`attest --redact` is path-scoped. It shortens local artifact paths, but it does
not remove explicit organization or CI identity fields such as `org.repo`,
`org.actor`, `org.workflow_run_id`, or `org.merge_sha`. Omit `--ci-context` or
the corresponding explicit flags when those identities should not be recorded.

## GitHub Actions

Use [`../examples/github-actions/13-org-governance.yml`](../examples/github-actions/13-org-governance.yml)
for a copyable scheduled workflow. The public Action can now emit
`attestation.json`, `org-evidence-bundle.json`, `host-grants.json`, and
`org-status.json` with:

```yaml
with:
  attestation: "true"
  org_bundle: "true"
  host_audit: "true"
  org_status: "true"
  registry_repo_label: acme/support-agent
```

## Boundary

Do not put hosted scanning, dynamic policy fetching, GitHub review verification,
or LLM policy decisions in this layer. The local scanner emits deterministic
artifacts; any hosted or cross-repo view consumes those artifacts.
