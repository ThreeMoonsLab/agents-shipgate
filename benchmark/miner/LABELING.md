# Labeling the mined corpus → the verdict-accuracy benchmark

The miner produces *evidence* (one row per merged PR). A human label is the
*ground truth*. This file is the rubric + process that turns the corpus into
the verdict-accuracy benchmark — the roadmap's gating proof that the verdict is
right, not just reproducible.

The tooling is ready and network-free; the only missing input is the labels.

## The three labels

Label the PR by what a correct gate **should** do with its capability /
authority change — independent of what Shipgate actually returned (that is what
we are scoring).

| Label | Meaning | A correct gate should… |
|---|---|---|
| `safe_to_merge` | The change does not expand authority in a way that needs review (docs, tests, refactors, chores, bounded internal changes). | allow / `mergeable` |
| `needs_human` | A person should look: accepted-debt, an evidence gap, or an authority-bearing change that is plausibly fine but not self-evidently safe. | not auto-pass (`review` / `insufficient_evidence` / `blocked`) |
| `must_block` | Unsafe to merge unreviewed: new high-risk authority (financial / destructive / external-comms), trust-root weakening, least-privilege removal, or a silent broad-scope grant. | `blocked`, `can_merge_without_human=false` |

Label the **change**, not the project. A repo can be perfectly fine and still
ship a single PR that is `must_block`.

## Process (two labelers + adjudication)

1. Generate the worksheet (or copy the committed
   `results/<run>.labels.template.csv`, which is the same thing pre-generated):
   ```bash
   python -m benchmark.miner labels \
     --results results/<run>.jsonl \
     --out results/<run>.labels.a.csv   # labeler A; copy for labeler B
   ```
   The worksheet carries enough PR context (title, verdicts, capability
   counts) to label most rows without opening the diff; open the PR when the
   row is not obvious.
2. Two people fill `label` + a one-line `rationale` **independently**.
3. Adjudicate disagreements into a single `results/<run>.labels.csv`
   (`pr_url,label,rationale`). Record the disagreement rate in the run notes —
   a high rate means the rubric needs sharpening, not that the labels are done.
4. Score:
   ```bash
   python -m benchmark.miner score \
     --results results/<run>.jsonl \
     --labels results/<run>.labels.csv
   ```
   Paste the confusion matrix + metrics into the run's README row.

Only the **adjudicated** `*.labels.csv` is committed (one label per PR). The
per-labeler files and any transcripts are not committed.

## Negative control

The worksheet defaults to the engine-engaged rows (`evaluated` + `scan_failed`).
The `trigger_skip` rows (the large majority of merged PRs) are the
negative-control pool: the gate correctly stayed silent. Don't label all of
them — sample ~10–15, label them `safe_to_merge`, and confirm the gate did not
escalate. That measures the noise bound on real history without drowning the
worksheet.

## Metrics `score` reports

- **`blocked_recall`** — of `must_block` PRs, the share the gate hard-blocked.
  The headline safety number; target ≥ 0.9.
- **`must_block_caught`** / **`needs_human_caught`** — share that did not
  auto-pass (block / review / insufficient_evidence). The softer "a human saw
  it" guarantee.
- **`benign_escalation_rate`** — of `safe_to_merge` PRs, the share the gate
  escalated (block or review). False-alarm / noise budget; target ≤ 0.1.
- **`ie_rate_on_safe`** — of `safe_to_merge` PRs, the share that returned
  `insufficient_evidence`. The extraction-coverage gap, isolated from false
  alarms.

The verdict scored against the label is the per-PR receipt
(`verify_verdict`), falling back to the cold-start `head_decision` when the
receipt is unavailable.

## Worked anchor: `stripe/ai#232`

`stripe/ai#232` is the documented validation case (the 2026-06-01 design-partner
pilot): the PR silently dropped a least-privilege `StripeAgentToolkit`
configuration bound and mounted the full refund/cancel/dispute toolkit onto an
autonomous email-support agent. Its label is **`must_block`**. With a
directory-scoped SDK source the verifier returns `blocked`
(`SHIP-VERIFY-CAPABILITY-SCOPE-BROADENED`); the cold-start receipt stops at
`insufficient_evidence` because `init` scopes the source to `main.py` while the
bound lives in `support_agent.py`. So #232 is both the safety anchor
(`blocked_recall`) and a coverage exemplar (cold-start `ie_rate`).
