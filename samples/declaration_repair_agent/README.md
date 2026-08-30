# Declaration repair agent

The repair sibling of [`google_adk_cold_start_agent`](../google_adk_cold_start_agent/).
That fixture renders the questionnaire a cold start produces — mostly blanks,
one pre-filled proposal, and one challenged *authority* row printed as a note.
This one is the step after, and it adds the single shape the sibling has no
example of: a challenged **effect** row and the `declare_risk_tags` repair it
publishes. Its manifest has already declared, controlled and owned every
action, and is held short of `passed` by two of those rows.

```bash
agents-shipgate scan -c samples/declaration_repair_agent/shipgate.yaml
```

Nothing here is executed. The two tools are read from
[`tools.json`](tools.json) as an MCP inventory, and no server is contacted.

## What it pins

`declaration_below_inferred_evidence` — a declared `effect` that does not
account for everything the scan read — is the shape
[#424](https://github.com/ThreeMoonsLab/agents-shipgate/issues/424) was about,
and the one shape no committed artifact rendered before this fixture.

The sibling is not short of declarations, of pre-filled answers, or even of
challenged rows: it declares two actions, pre-fills `effect: financial_write`
on a third, and prints a note for a challenged `conflicting_authority_evidence`
row. What it has no example of is a declaration challenged in the **effect**
dimension — its questionnaire contains zero `risk_tags:` blocks, so the repair
route below appears in it nowhere.

`effect_repair` answers that row one of two ways: raise `effect:` to something
that covers every reading, or — where no single effect does — *"declare the
uncovered category as a reviewed risk tag"*. Both actions here are the second
case, and applying that instruction verbatim used to replace the review-tier
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

A block also offers a second, different choice — not the repair route above,
but what a reviewer *does* about the reading. Account for it by keeping the
pre-filled `risk_tags:` and deleting the `override:` stanza, or reject it by
deleting the tags and filling the override in. This fixture takes the first,
and `override:` is the only part of either block still carrying
`<REVIEW_REQUIRED>` — the test asserts that no sentinel survives its merge, so
"keep the tags" really is a complete answer rather than one that quietly needs
more review.

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
well as before, and sets an owner for each. That is deliberate: it leaves the
two challenged rows as the only thing between this repository and `passed`, so
the paste test measures the repair and nothing else.

Per `BUILTIN_EFFECT_OBLIGATIONS`, what the repair actually adds is smaller than
it looks — most of these controls are owed by the *declared* effect before any
tag is added:

| | owed before the repair | newly owed by adding `destructive` |
| --- | --- | --- |
| `support.delete_case_message` | `confirmation.required`, `safeguards.audit_log` | `approval.required`, `safeguards.rollback` |
| `billing.cancel_invoice_email` | `approval.required`, `confirmation.required`, `safeguards.audit_log`, `safeguards.idempotency` | `safeguards.rollback` |

So `confirmation.required` is **not** something the repair introduces —
`external_communication` already obliged it on both rows — and `approval` is
new only on the first, because `financial_write` already obliged it on the
second. `safeguards.rollback` is the one control that is new on both.

Every control line here still earns its place, checked by dropping each one and
rescanning: without it the **repaired** state blocks or falls back to
`review_required`, so the loop's endpoint would no longer be `passed`. Two
spellings that would have been redundant are deliberately absent:
`policies.require_approval_for_tools` (already satisfied by
`approval.required` on each row) and `safeguards.idempotency` on the row
(already satisfied by `policies.require_idempotency_for_tools`, which is what
`SHIP-SIDEFX-IDEMPOTENCY-MISSING` reads).

The owners are set through `risk_overrides.tools[].owner` with no `tags:`
entry. A `risk_overrides.tags` entry is the manifest's *other* route into the
effect dimension (it arrives as `risk_hint:manual`), and this fixture pins the
action-row route; keeping the two apart also keeps this golden clear of
[#460](https://github.com/ThreeMoonsLab/agents-shipgate/issues/460).

## Regenerating

Run from the repository root, after any change that moves values:

```bash
python - <<'PY'
import json
from pathlib import Path
from agents_shipgate.cli.scan import run_scan

sample = Path("samples/declaration_repair_agent")
expected = sample / "expected"
run_scan(
    config_path=sample / "shipgate.yaml",
    output_dir=Path("expected"),
    formats=["json", "markdown"],
    ci_mode="advisory",
    packet_enabled=False,
)
(expected / "current-control.json").unlink(missing_ok=True)

golden = expected / "report.json"
payload = json.loads(golden.read_text(encoding="utf-8"))
payload["manifest_dir"] = f"<REPO>/{sample.as_posix()}"
payload["generated_reports"] = {
    fmt: Path(written).as_posix()
    for fmt, written in payload["generated_reports"].items()
}
golden.write_text(json.dumps(payload, indent=2), encoding="utf-8", newline="\n")

# The scan's own writers use the platform newline. Rewrite every golden with
# an explicit LF, whoever produced it.
for name in ("report.md", "suggested-declarations.yaml"):
    path = expected / name
    path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
PY
```

This is the sibling's recipe with the sample path swapped, and every
normalization in it is load-bearing for the same reasons — the four notes under
[`google_adk_cold_start_agent` § Regenerating the goldens](../google_adk_cold_start_agent/README.md)
apply here verbatim. In short:

- the path rewrite is **structural**, because a textual `<REPO>` replace is a
  silent no-op on Windows: `json.dumps` escapes the separators, so the file
  holds `C:\\repo\\samples\…` while `os.getcwd()` is `C:\repo\samples\…` and
  the two never match — leaving an absolute `manifest_dir` that fails
  `test_sample_expected_report_json_uses_repo_placeholder_for_manifest_dir` on
  the machine that produced the golden;
- `generated_reports` needs `.as_posix()`, or a Windows run commits
  `expected\report.json` and churns against every other platform;
- all three goldens are rewritten with an explicit `newline="\n"`, because
  every writer involved opens text mode with `newline=None` and
  `.gitattributes` pins `samples/**/expected/** -text`, so Git stores whatever
  bytes were produced. No byte comparison can see it — `read_text` normalizes
  CRLF on the way in — which is why
  `test_sample_expected_goldens_are_committed_with_lf_newlines` reads raw bytes.

An earlier draft of this section gave the first two as prose and omitted the
third entirely, which made the repo's designated recovery path — named in
`test_repair_scaffold_matches_its_golden`'s own failure message — the
counterexample to that guard's docstring (#465 review).

Read the diff on `expected/suggested-declarations.yaml` before committing: a
change in the `risk_tags:` values a block publishes is a change to the remedy
this tool hands an adopter. Read `report.json`'s
`evidence_gaps[].next_action.expects` beside it, since that sentence names the
same list on the surface an adopter reads first.
