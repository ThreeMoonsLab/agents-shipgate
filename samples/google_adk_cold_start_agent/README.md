# Google ADK cold-start agent

The cold-start sibling of [`google_adk_agent`](../google_adk_agent/). That
fixture answers every declaration question it is asked — `2 of 2 answered`,
nothing open — so a scan of it writes no `suggested-declarations.yaml` at all
and no committed artifact beside it ever renders one. This fixture stops
partway on purpose: it reaches `insufficient_evidence` with **ten open
declaration questions**, and ships the questionnaire an adopter actually reads
as a byte-compared golden. It also ships
[`expected/cold-report.md`](expected/cold-report.md), rendered from a temporary
Git repository where the agent sources are committed and `shipgate.yaml` is
not, to pin the surface-first first-contact order from #463. The ordinary
[`expected/report.md`](expected/report.md) remains the committed-manifest,
verdict-first golden.

For the step after this one, see
[`declaration_repair_agent`](../declaration_repair_agent/): a manifest that has
already declared everything and is held short of `passed` by two rows whose
declared `effect` is challenged by the evidence. That is the one questionnaire
shape this fixture has no example of — its own golden holds zero `risk_tags:`
blocks, so the `declare_risk_tags` repair route appears here nowhere (#424).

```bash
agents-shipgate scan -c samples/google_adk_cold_start_agent/shipgate.yaml
```

Nothing here is executed. The ADK entrypoint is read as source, the MCP
inventory and the OpenAPI spec are read as files, and no server is contacted.

## What it pins

`expected/suggested-declarations.yaml` is the point of the fixture. Before it
existed, the file an adopter is told to edit had no golden at all — its header,
its `Question N of M` banners, its reading lines, its proposal annotations and
its progress counter were covered only by in-process unit tests, and the
ordering that decides all of them was pinned by nothing committed (#425).

Each action exists for one rung of that order. The questionnaire ranks by the
**ceiling** of what an answer can establish, not by the floor the scan already
inferred (#419), and among the actions nothing has bounded the only thing left
to prefer one blank over another is the shape of the name:

| Action | Why it is here | Asked as |
| --- | --- | --- |
| `update_case_index` | nothing read; the name bands as mutating (`+1`) | question 2 |
| `assemble_case_timeline` | nothing read; the name says nothing (`0`) | question 3 |
| `ops.export_case_bundle` | its declared authority contradicts what the MCP export publishes | question 4, then **5 as a note with no block** |
| `ops.queue_backfill` | only the MCP protocol default stands in for evidence (`0`) | questions 6–7 |
| `list_case_attachments` | nothing read; the name bands as retrieving (`-1`) | question 8 |
| `issue_goodwill_refund` | read as a financial write, so its block arrives with a proposed answer | question 9 |
| `support.get_update_history` | a structurally proven read whose *name* looks like a write | question 10 |
| `ops.append_case_note` | established structurally (`POST`), so it is never asked about | not asked |
| `record_case_outcome` | declared in `shipgate.yaml`, so its two questions are answered | counted, not open |

`update_case_index`, `assemble_case_timeline` and `list_case_attachments` are
the three that pin the band, and they are the fixture's whole answer to the
second half of #419. Nothing was read about any of them, so the only thing
separating their questions is the shape of the name: the first leads and the
third trails, against alphabetical order in both directions. Flatten
`name_shape_band` and the committed file changes — which a
bounded-versus-unbounded fixture alone would not catch.

`ops.export_case_bundle` carries a declaration that is **wrong on purpose**:
the MCP export publishes an `ops:cases:read` OAuth scope and the manifest row
says the action needs no credential. A reviewed declaration does not overrule
published evidence silently, so the questionnaire counts the question and
prints a note in its numbered place — the repair is to correct one of the two
statements, not to fill in a blank. It is also what pins the interleaving: the
note sits at 5, between that action's own block at 4 and the next subject's at
6, rather than after every block in the file.

The last two rows are as load-bearing as the rest. `ops.append_case_note`
proves the denominator excludes what the scanner settled by itself, and
`record_case_outcome` is what makes the counter read
`2 of 12 answered; 10 open` rather than `0 of 10` — the progress sentence
rendered at a state that is neither empty nor finished.

`support.get_update_history` is [#419](https://github.com/ThreeMoonsLab/agents-shipgate/issues/419)
wearing its own hat. Its effect is proven read-only by a `readOnlyHint` the
manifest trusts, so it sorts **last**; its name bands as mutating, so the
moment the ordering goes back to asking whether a side effect was *measured*
rather than *bounded*, it sorts **first** — a proven read jumping ahead of an
action nothing has bounded, with a repository-chosen name breaking the tie.
That is the regression `expected/suggested-declarations.yaml` catches by
byte comparison.

## What could move the verdict out from under it

`insufficient_evidence` is what two tests pin, and it is not reached by the
evidence gaps alone. A blocker, or an *active high or critical review concern
on a proven-reachable capability*, both outrank it — so the verdict also
depends on this fixture's one finding,
`SHIP-ADK-EVAL-COVERAGE-MISSING` (it declares no `google_adk.eval_sets`),
staying below that tier. It is `medium` today.

Left in deliberately rather than silenced with an eval file: a repository that
has not declared its eval coverage is exactly what a cold start looks like, and
shaping the fixture around its own test would hide a real part of the report.
But if raising that check's severity ever turns this sample
`review_required`, two tests fail —
`test_sample_expected_report_json_is_current`, which asserts the decision
string, and `test_cold_start_markdown_report_matches_golden`, whose golden
carries it as a line of prose — and this paragraph is the reason.

## Not covered here

One rung of the order is missing: an action whose only reading is a heuristic
the resolver may not act on — a keyword-inferred `read`, which is unbounded
because [#357](https://github.com/ThreeMoonsLab/agents-shipgate/issues/357)
forbids a heuristic from establishing a read-only action. It cannot be reached
from an ADK repository: `READ_ONLY_KEYWORDS` are consulted only for the source
types in `core/risk_hints.py::_KEYWORD_GATED_SOURCE_TYPES`, and no ADK source
type is one of them. Adding a LangChain or CrewAI source to reach it would
leave its tools unbound from the ADK root agent and out of the action surface
entirely, and forcing them in with a closed-world `agent_bindings.declarations`
raises `conflicting_binding_evidence`, which would take over the headline the
questionnaire is here to own. `test_a_heuristic_cannot_propose_that_an_action_is_read_only` and
`test_an_unbounded_action_outranks_every_bounded_one` in
`tests/test_declaration_questionnaire.py` cover that rung in process.

## Regenerating the goldens

Run from the repository root, after any change that moves values:

```bash
python - <<'PY'
import json
from pathlib import Path
from agents_shipgate.cli.scan import run_scan

sample = Path("samples/google_adk_cold_start_agent")
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

Four things that look like details and are not.

**Newlines are forced, not inherited.** `Path.write_text(..., encoding="utf-8")`
opens in text mode with `newline=None`, so on Windows every `\n` is written as
`\r\n` — by the recipe *and* by `write_json_report`, the Markdown writer and the
questionnaire writer. `.gitattributes` pins `samples/**/expected/** -text`
precisely so Git hands those bytes over unchanged, so a golden regenerated on
Windows would be committed as CRLF against everyone else's LF. The tests cannot
see it: `read_text()` applies universal newlines and normalizes CRLF back to LF
on the way in, so every byte comparison in this repo passes on a file whose
bytes moved. `newline="\n"` on all three is what makes the artifact the same
artifact everywhere, and
`test_sample_expected_goldens_are_committed_with_lf_newlines` reads the raw
bytes so the guard does not share the blindness.

**The path normalization is structural, not textual.** An earlier version did
`text.replace(os.getcwd(), "<REPO>")`, which works on POSIX and is a no-op on
Windows: `json.dumps` escapes the separators, so the file holds
`C:\\repo\\samples\\…` while `os.getcwd()` is `C:\repo\samples\…` and the two
never match. The golden then keeps an absolute `manifest_dir` and fails
`test_sample_expected_report_json_uses_repo_placeholder_for_manifest_dir` — on
the machine that produced it. Assigning the field a POSIX value is correct on
both, and `Path(written).as_posix()` does the same for `generated_reports`,
which would otherwise commit `expected\report.json` and churn against every
other platform.

`json.dumps(payload, indent=2)` is exactly what `write_json_report` uses — no
`sort_keys`, no trailing newline — so the round trip is byte-identical and
nothing but the two normalized fields moves.

**`output_dir` is the relative `"expected"`**, which `run_scan` resolves under
the manifest directory rather than under the process directory. The report
records where it wrote itself, so scanning into an absolute temporary directory
and copying the files back bakes a contributor's `/var/folders/…/tmp…` path
into `generated_reports` — a value no test compared, which is exactly why it
sat there churning until #425's review.

**The `unlink` is not tidying.** A scan also publishes `current-control.json`,
and this fixture deliberately does not commit one: the hash-bound pointer path
is covered by [`conductor_agent`](../conductor_agent/), and a second copy would
have to be rebound *after* the normalization every time, since that rewrite
changes both the length and the digest of `report.json`.
