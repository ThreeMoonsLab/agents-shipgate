# Google ADK cold-start agent

The cold-start sibling of [`google_adk_agent`](../google_adk_agent/). That
fixture answers every declaration question it is asked, so its questionnaire is
`0 of 0` and no committed artifact beside it ever renders one. This fixture
stops partway on purpose: it reaches `insufficient_evidence` with **ten open
declaration questions**, and ships the questionnaire an adopter actually reads
as a byte-compared golden.

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

The first four are what pin the band. `update_case_index` leads
`assemble_case_timeline`, and `list_case_attachments` trails both, against
alphabetical order in each case — so flattening `name_shape_band` changes the
committed file. That is the half of #419's fix a bounded-versus-unbounded
fixture alone would not hold.

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
import os, shutil, tempfile
from pathlib import Path
from agents_shipgate.cli.scan import run_scan

sample = Path("samples/google_adk_cold_start_agent")
out = Path(tempfile.mkdtemp())
run_scan(
    config_path=sample / "shipgate.yaml",
    output_dir=out,
    formats=["json", "markdown"],
    ci_mode="advisory",
    packet_enabled=False,
)
for name in ("report.json", "report.md", "suggested-declarations.yaml"):
    shutil.copy(out / name, sample / "expected" / name)
golden = sample / "expected" / "report.json"
golden.write_text(
    golden.read_text(encoding="utf-8").replace(os.getcwd(), "<REPO>"),
    encoding="utf-8",
)
PY
```

Scan into an absolute temporary directory and copy, rather than writing
straight into `expected/`: a scan also emits `current-control.json`, and this
fixture deliberately does not commit one — the hash-bound pointer path is
covered by [`conductor_agent`](../conductor_agent/), and a second copy of it
would have to be rebound after the `<REPO>` substitution on every regeneration.
