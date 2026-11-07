# Control Evidence

`agents-shipgate registry report --control-map` turns the
[registry ledger](organization.md#attestations-and-registry) into an evidence
extract for one audit window: for each evidence item of a framework, which
rows bear on it, their counts, and every gap. It writes the extract of exactly
those rows beside the view, so an assessor can recompute every count from the
extract alone instead of trusting the summary.

It states rows, counts and gaps. Judging them against a control is the
assessor's decision, and nothing in the output says a control is met.
`release_decision.decision` remains the only release gate.

```bash
agents-shipgate registry report \
  --control-map docs/control-maps/aiuc-1.yaml \
  --registry registry.jsonl \
  --since 2026-01-01 --until 2026-06-30 \
  --expected-interval-days 2 \
  --out control-evidence/
```

`--out` writes four files:

| File | What it holds |
| --- | --- |
| `control-evidence.json` | The view ([schema](control-evidence-schema.v0.1.json)): per item, the cited row ids, counts and gaps; the map's path and sha256; the extract's sha256; the chain head of each repository in the ledger read; the recompute command. |
| `control-evidence.md` | The same view for a reader. |
| `ledger-extract.jsonl` | The cited ledger lines, byte for byte as the ledger stores them, plus, for a repository the window holds no observation of, its last observation outside the window, so a recompute still considers it. |
| `control-map.yaml` | The map the view was made with, its bytes unchanged, so it hashes to the view's `map.sha256`. |

The files are written as exact UTF-8 bytes with `\n` line ends on every
platform, Windows included, so `ledger-extract.jsonl` and `control-map.yaml`
hash to the `extract.sha256` and `map.sha256` the view names.

Without `--out`, `--json` prints the view and the default prints one line per
item. The command reads only the files it names and exits `0` whatever it
finds: it is evidence, not a gate. A map it cannot read, or a missing window,
exits `2`.

## Control maps

A map is data ([schema](control-map-schema.v0.1.json)). Each entry names an
evidence item of a framework, says in one line what the ledger shows for it,
and lists which row kinds bear on it and which gap rules apply. Nothing in the
engine names a framework. An item the ledger cannot speak to is listed with no
evidence and says so, and the view prints it as not evidenced instead of
leaving it out.

[`control-maps/aiuc-1.yaml`](control-maps/aiuc-1.yaml) is the first map. It
was written against the April 2026 edition of the AIUC-1 evidence list, for
requirements B006 (Prevent unauthorized AI agent actions) and E004 (Assign
accountability):

| Item | What the ledger shows |
| --- | --- |
| B006.1 Agent service access restrictions | Scheduled comparisons of the coding-agent host grants (MCP servers, permission rules, hooks, workflow scopes) with the acknowledged baseline: [drift observations](post-approval-monitoring.md). |
| B006.2 Agent security monitoring and alerting | Not evidenced: it asks for runtime monitoring, and the ledger is static. |
| B006.3 Execution-level safeguards | Tool definition integrity: attestations binding the reviewed lock's `definition_set_hash`, and observations whose capability diff names definitions that moved since. |
| E004.1 Change approval policy and records | The pull request's reviews in each attestation ([`attest --reviews`](organization.md#who-approved-the-change)). The policy itself is the team's document. |
| E004.2 Code signing implementation | Not evidenced: attestations and observations are unsigned. |

Check the current edition of a framework before relying on a map's ids and
titles; a map names the edition it was written against in `source` and `note`.

## Gap rules

A map names rules; the engine implements them, so two maps that name one rule
mean one computation. Each gap is printed with its rows, `none` when it finds
nothing, or `not_checked` when it cannot be applied.

| Rule | Reads | Found when |
| --- | --- | --- |
| `no_rows_in_window` | either kind | the window holds no row of the cited kind |
| `observation_interval` | observations | a repository went longer than `--expected-interval-days` without an observation, as [`registry report --drift`](post-approval-monitoring.md#reading-the-record) computes it; `not_checked` without the flag |
| `observation_incomparable` | observations | the host comparison could not be made, also when the same run's capability diff found drift |
| `inventory_issues` | observations | the audit reported sources it could not read |
| `capability_comparison_absent` | observations | the run recorded no capability diff |
| `definition_digests_absent` | attestations | the bound capability lock carries no `definition_set_hash` (exported before capability standard 0.6) |
| `approval_not_supplied` | attestations | the attestation was made without `--reviews` |
| `approval_not_approved` | attestations | the reviews say `not_approved`, `changes_requested` or `approved_stale` |

Rows whose `event_time` cannot be read are counted as `undated` and placed in
no window.

## Recomputing it

`control-evidence.json` carries the command. With `--out` it names the two
files written there, `control-map.yaml` and `ledger-extract.jsonl`, so run it
from that directory, wherever the map was read from; its `controls` block
equals the view's. That establishes the view summarizes those rows. That the rows are the
ledger's is a second check: every extract line is a line of the ledger, and
`agents-shipgate registry verify` checks the full ledger's hash chain, whose
heads at the time of the view are listed in `chain_heads`. Keep the ledger
where its history is protected (see
[post-approval monitoring](post-approval-monitoring.md#record-each-run)).

Two runs over the same ledger and map, with the same arguments, write the same
bytes on any machine: the output carries no clock and no host path the caller
did not pass.
