# Post-Approval Monitoring

A change to what a coding agent, or an agent's tools, may do is reviewed in a
pull request: `verify` judges it and `attest` records the verdict and, with
`--reviews`, who approved it. Some changes never pass through that review:

- a direct push, or an admin merge past required reviews;
- an MCP tools file, an `.mcp.json` or a settings file rewritten by a
  generator or a sync job outside a reviewed pull request;
- a plugin or marketplace source that changes what a host loads while the
  repository's root files stay the same.

`shipgate audit --host --drift` and `agents-shipgate capability diff` already
catch these by comparing the repository with its approved state, and
[`examples/github-actions/12-host-grant-drift.yml`](../examples/github-actions/12-host-grant-drift.yml)
runs the first on a schedule. What a scheduled run found used to live only in
its workflow log, which GitHub deletes after the retention period. This page
records each run as a row in the [registry](organization.md#attestations-and-registry),
next to the attestation rows and in the same hash chain, so the monitoring
itself can be shown later: that it ran, what it compared against, and what it
found.

## One observation

`registry ingest --observation` reads the JSON `audit --host --drift --json`
printed and, optionally, `--capability-diff` with the same run's
`capability diff` JSON. It appends one row with `row_kind:
"drift_observation"`:

| Field | What it holds |
|---|---|
| `event_time` | When the comparison ran, as the workflow declares it (`--event-time`, required, stored in UTC). The engine reads no clock. |
| `head_sha`, `workflow_run_id`, `branch`, `repo` | The commit compared and the run, from `--ci-context github-actions` or explicit flags. |
| `observation.status` | `drift` when either comparison found a change, `incomparable` when the host comparison could not be made, `clean` otherwise. Recomputed on read; a row whose status its comparisons do not support is reported as malformed. |
| `observation.host` | The baseline compared against (`baseline_file`, `baseline_sha256`), `current_sha256`, `comparison_status`, `has_drift`, and each change as `grant_id` or `artifact_id`, `kind`, `host` and `change` (`added`, `removed`, `changed`). Expansion signals are counted by kind (`wildcard_allow_added`, `deny_rule_removed`, …); `incomparable_reasons` keeps reason codes only. |
| `observation.capability` | With `--capability-diff`: both locks' identity (`semantic_capability_set_hash`, `definition_set_hash`, `capability_count`), the diff's summary counts, and the capability ids added, removed, re-identified, changed and with a changed tool definition. |
| `observation.source_sha256`, `observation.capability.source_sha256` | Hashes of the two input files, so the run's full payloads, kept as workflow artifacts, can be matched to the row. |

The row copies no grant value: no command, argument, URL, env value or rule
text, and no tool description. Those stay in the audit payload. A baseline
path that is absolute is reduced to its file name.

Ingesting one run's files twice is a no-op. Two runs that found the same
nothing print the same bytes and are still two rows, because their declared
time, run or commit differ: the record is that each one ran.

An observation is never a bypass candidate and has no verdict. `registry
summary` counts rows by `row_kind` and observations by status, and keeps its
release counts (`bypass_count`, `by_merge_verdict`, …) to attestation rows.

## What it establishes, and what it does not

One observation establishes that, at the declared time and on the named
commit, the static declarations the audit reads matched the acknowledged
baseline, and the exported capabilities matched the reviewed lock, or names by
id what moved.

It does not establish:

- **Runtime behaviour.** Like every Shipgate artifact it reads declarations,
  not what a session did. A tool that changes its behaviour behind an
  unchanged definition is not seen.
- **Whether a drift was authorised.** A change a reviewed pull request
  introduced still reads as drift until the baseline or lock is
  re-acknowledged in a reviewed pull request. Telling an approved change from
  an unapproved one needs Git history and the GitHub API, which the engine
  does not call: find the commit, its pull request, and that pull request's
  attestation row (`approval_status`, `capability_change_ids`).
- **Anything the audit does not read.** See the coverage each run reports
  (`issue_count`, `incomparable`).
- **That a run happened when it says.** `event_time` is declared by the
  workflow. The hash chain shows that rows were not edited, removed or
  reordered after a later row was written; it does not timestamp them. Keep
  the ledger where its history is protected (below).

## Record each run

The workflow below runs both comparisons daily and on every push to the
default branch, records the result on a dedicated `shipgate-ledger` branch,
and opens or updates one issue labelled `shipgate:drift` when a run finds
drift or cannot compare. It needs the first release that has `registry ingest
--observation` (see the [changelog](../CHANGELOG.md)); pin that release in the
two install steps.

One-time setup, by a person:

```bash
shipgate audit --host --save-baseline            # if not already committed
git add .agents-shipgate/host-grants.json && git commit -m "Acknowledge host grants"

git switch --orphan shipgate-ledger
git commit --allow-empty -m "Shipgate ledger"
git push origin shipgate-ledger
git switch -
```

Then protect `shipgate-ledger` with a ruleset that blocks force pushes and
deletion, so its history is the record. Teams with their own evidence store
can replace the push step with an upload there.

```yaml
name: post-approval-drift

on:
  schedule:
    - cron: "17 6 * * *"   # daily
  push:
    branches: [main]       # direct pushes and admin merges, as they land
  workflow_dispatch:

concurrency:
  group: shipgate-ledger   # one writer at a time
  cancel-in-progress: false

jobs:
  compare:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: read
    outputs:
      observed_at: ${{ steps.compare.outputs.observed_at }}
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install agents-shipgate
        run: python -m pip install "agents-shipgate==<first release with registry ingest --observation>"
      - name: Compare with the approved state
        id: compare
        run: |
          out="$RUNNER_TEMP/observation"
          mkdir -p "$out"
          shipgate audit --host --drift --json --out "$out/host-drift.json" > /dev/null
          echo "observed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$GITHUB_OUTPUT"
          if [ -f shipgate.yaml ] && [ -f .agents-shipgate/capabilities.lock.json ]; then
            agents-shipgate capability export -c shipgate.yaml --no-plugins \
              --out "$RUNNER_TEMP/capabilities.lock.json" --no-report-copy
            agents-shipgate capability diff \
              --base .agents-shipgate/capabilities.lock.json \
              --head "$RUNNER_TEMP/capabilities.lock.json" \
              --out "$out/capability-diff.json" > /dev/null
          fi
      - uses: actions/upload-artifact@v4
        with:
          name: observation
          path: ${{ runner.temp }}/observation/

  record:
    needs: compare
    runs-on: ubuntu-latest
    timeout-minutes: 5
    permissions:
      contents: write   # push the ledger branch
      issues: write     # open or update the drift issue
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: observation
          path: observation
      - uses: actions/checkout@v4
        with:
          ref: shipgate-ledger   # holds registry.jsonl only; no repository code
          path: ledger
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install agents-shipgate
        run: python -m pip install "agents-shipgate==<first release with registry ingest --observation>"
      - name: Record the observation
        id: record
        env:
          OBSERVED_AT: ${{ needs.compare.outputs.observed_at }}
        run: |
          args=(--observation observation/host-drift.json)
          if [ -f observation/capability-diff.json ]; then
            args+=(--capability-diff observation/capability-diff.json)
          fi
          agents-shipgate registry ingest "${args[@]}" \
            --registry ledger/registry.jsonl \
            --ci-context github-actions \
            --event-time "$OBSERVED_AT" \
            --json > ingest.json
          echo "status=$(jq -r '.observation_status' ingest.json)" >> "$GITHUB_OUTPUT"
          agents-shipgate registry verify --registry ledger/registry.jsonl --fail-on-issue
      - name: Push the ledger
        working-directory: ledger
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add registry.jsonl
          git diff --cached --quiet || git commit -q -m "shipgate: observation ${GITHUB_RUN_ID}"
          git push -q origin HEAD:shipgate-ledger
      - name: Open or update the drift issue
        if: steps.record.outputs.status != 'clean'
        env:
          GH_TOKEN: ${{ github.token }}
          GH_REPO: ${{ github.repository }}
          STATUS: ${{ steps.record.outputs.status }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
        run: |
          gh label create shipgate:drift --color B60205 \
            --description "Agent authority changed after approval" 2>/dev/null || true
          body="The comparison of ${GITHUB_SHA} with the approved state is \`${STATUS}\`. Run and observation artifact: ${RUN_URL}"
          number=$(gh issue list --label shipgate:drift --state open --json number --jq '.[0].number')
          if [ -n "$number" ]; then
            gh issue comment "$number" --body "$body"
          else
            gh issue create --title "Agent authority changed after approval" \
              --label shipgate:drift --body "$body"
          fi
```

Why it is split into two jobs: the job that reads the repository has
`contents: read` only, and the job that can write checks out nothing but the
ledger branch. Neither runs repository code: `audit --host` and `capability
export --no-plugins` read files and execute none of them.

When `audit --host --drift` cannot run at all (no committed baseline, an
unreadable one), the job fails and records nothing. That missed run shows up
as a failed workflow and as a gap in the report below.

## Reading the record

```bash
git fetch origin shipgate-ledger
git show origin/shipgate-ledger:registry.jsonl > registry.jsonl

agents-shipgate registry report --drift \
  --registry registry.jsonl \
  --since 2026-01-01 --until 2026-06-30 \
  --expected-interval-days 2 \
  --json
```

The report lists the observations whose `event_time` falls in the window
(UTC days, both inclusive) and counts `clean_count`, `drift_count`,
`incomparable_count`, `host_drift_count` and `capability_drift_count`, overall
and per repository in `repos[]`. `--repo` narrows it to one repository label.

With `--expected-interval-days N`, each stretch longer than `N` days with no
observation of a repository is listed in `gaps[]`: between two observations,
from the start of `--since` to the first, and from the last to the end of
`--until`. A repository the ledger has observed before but not in the window
is one gap across the whole window. Set `N` above the schedule's period:
GitHub starts scheduled runs late under load, and in a public repository it
disables a schedule after 60 days without activity, which is exactly the kind
of silence a gap is for. Keep `--until` no later than the day the report runs,
or the stretch after the last run is measured into the future. Observations
whose `event_time` cannot be read are counted in `undated_count` and placed in
no window.

`--fail-on-drift` exits `20` when the window holds a run with drift, a run
that could not compare, or a gap; without it the report exits `0` so a
scheduled job can archive it.

For an assessment, [`registry report --control-map`](control-evidence.md)
arranges the same rows, with the attestations, by a framework's evidence items
and writes the extract an assessor can recompute.

## Boundary

The engine compares local files, writes one local JSONL line and reads it
back. The workflow, not the engine, fetches and pushes the ledger, opens the
issue, and decides how long to keep the run artifacts that hold the full
payloads. An observation is evidence for a reviewer. It changes no verdict,
no `release_decision.decision` and no `verify` exit code.
