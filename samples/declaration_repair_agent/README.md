# Declaration repair agent

The repair sibling of [`google_adk_cold_start_agent`](../google_adk_cold_start_agent/).
That fixture is a cold start — nothing is declared, so every question it
renders is a blank. This one is the step after: a manifest that has already
declared, controlled and owned every action, held short of `passed` by two
**challenged** declarations.

```bash
agents-shipgate scan -c samples/declaration_repair_agent/shipgate.yaml
```

Nothing here is executed. The two tools are read from
[`tools.json`](tools.json) as an MCP inventory, and no server is contacted.

## What it pins

A challenged row — `declaration_below_inferred_evidence`, a declared `effect`
that does not account for everything the scan read — is the one questionnaire
shape a cold start cannot produce, because a cold start has no declaration to
challenge. It is also the shape [#424](https://github.com/ThreeMoonsLab/agents-shipgate/issues/424)
was about.

That row publishes two routes. Where no single effect covers every reading,
the route it names is *"declare the uncovered category as a reviewed risk
tag"* — and applying that instruction verbatim used to replace the review-tier
row with a **blocking** `conflicting_effect_evidence` whose message blamed the
reviewer's own manifest. A published remedy that could not close the row it
was printed on.

So this fixture is a loop rather than a snapshot:

| | verdict | open declaration questions |
| --- | --- | --- |
| as committed | `insufficient_evidence` | 2 |
| after pasting both published blocks | `passed` | 0 |

`expected/suggested-declarations.yaml` is the committed half — the questionnaire
an adopter actually reads here. `test_pasting_the_committed_repair_blocks_reaches_passed`
is the other half: it takes the blocks out of *that file*, merges each into the
action it names, rescans, and requires `passed`. Neither half is a fixture
assembled in-test, which is the point — the class this guards shipped once
already under a green suite that only ever asked its questions of hand-built
tools.

Each block publishes both of the row's routes, so pasting one means choosing
between them, exactly as its comments say: keep the pre-filled `risk_tags:` and
delete the `override:` stanza, or delete the tags and fill the override in.
This fixture takes the first route, and `override:` is the only part of either
block still carrying `<REVIEW_REQUIRED>` — the test asserts that no sentinel
survives its merge, so "keep the tags" really is a complete answer rather than
one that quietly needs more review.

## The two actions

| Action | Read as | Declared | Published repair |
| --- | --- | --- | --- |
| `support.delete_case_message` | `external_communication`, `destructive` | `effect: external_communication` | `risk_tags: [destructive]` |
| `billing.cancel_invoice_email` | `external_communication`, `financial_write`, `destructive` | `effect: external_communication`, `risk_tags: [financial_write]` | `risk_tags: [financial_write, destructive]` |

Both are `source_type: mcp` with keyword-inferred readings, which is the shape
of the first-contact MCP evaluation flow — the reading that decides whether a
maintainer runs this tool a second time.

The second row is the one that earns its place twice. It already carries a tag
the reviewer wrote, and `risk_tags` is one YAML key, so a block that names it
**replaces** it. A repair naming only the newly uncovered category would tell
this reviewer to delete their own reviewed `financial_write` tag, and the next
scan would reopen the row asking for it back — the #424 defect surviving inside
the case #424's own repair creates. The committed golden must publish the
complete value, and
`test_the_published_repair_keeps_the_tag_the_reviewer_already_wrote` reads it
out of the golden and says so.

## Why everything else is already answered

The manifest declares every control both actions owe *after* the repair as
well as before — `destructive` adds approval, confirmation and rollback to
what `external_communication` and `financial_write` already obliged — and sets
an owner for each action. That is deliberate: it leaves the two challenged
rows as the only thing between this repository and `passed`, so the paste test
measures the repair and nothing else.

Every control line here earns its place, checked by dropping each one and
rescanning: without it the **repaired** state blocks or falls back to
`review_required`, so the loop's endpoint would no longer be `passed`. Note
that most of them are obliged only *after* the repair — `rollback` and the
confirmation policy come from `destructive`, which is the category the repair
adds — so dropping one changes nothing about the committed scan and everything
about what pasting the blocks buys. Two spellings that would have been
redundant are deliberately absent: `policies.require_approval_for_tools`
(already satisfied by `approval.required` on each row) and
`safeguards.idempotency` on the row (already satisfied by
`policies.require_idempotency_for_tools`, which is what
`SHIP-SIDEFX-IDEMPOTENCY-MISSING` reads).

The owners are set through `risk_overrides.tools[].owner` with no `tags:`
entry. A `risk_overrides.tags` entry is the manifest's *other* route into the
effect dimension (it arrives as `risk_hint:manual`), and this fixture pins the
action-row route; keeping the two apart also keeps this golden clear of
[#460](https://github.com/ThreeMoonsLab/agents-shipgate/issues/460).

## Regenerating

From the repository root:

```python
from pathlib import Path
from agents_shipgate.cli.scan.orchestrator import run_scan

sample = Path("samples/declaration_repair_agent")
run_scan(
    config_path=sample / "shipgate.yaml",
    output_dir=Path("expected"),          # resolved under the manifest dir
    formats=["json", "markdown"],
    ci_mode="advisory",
    packet_enabled=False,
)
```

Then replace the absolute repository path in `expected/report.json` with
`<REPO>`, and delete the `expected/current-control.json` the scan also writes —
it is not committed for this sample. Read the diff on
`expected/suggested-declarations.yaml` before committing: a change in the
`risk_tags:` values a block publishes is a change to the remedy this tool hands
an adopter.
