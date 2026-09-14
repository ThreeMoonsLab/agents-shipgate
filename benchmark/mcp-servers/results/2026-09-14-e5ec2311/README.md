# MCP server findings, 2026-09-14, candidate `e5ec2311`

A scripted engineering measurement of existing-reader precision on ten fixed public MCP servers. It does not measure a server's runtime behaviour, adoption, or how often a finding appears across all servers, and it makes no claim about any project listed.

| | |
|---|---|
| Population | `../../selection.json`, frozen 2026-09-13: ten servers, each pinned to a commit and a scan route |
| Candidate | `agents_shipgate-1.0.0-py3-none-any.whl`, the wheel Release Engine Smoke run [34808373786](https://github.com/ThreeMoonsLab/agents-shipgate/actions/runs/34808373786) built and exercised on `e5ec2311` (main after #760–#763), sha256 `071abe4f…`. An advisory release cut from that commit publishes this wheel. |
| Environment | macOS (Darwin 25.6.0), Python 3.12 |
| Per server | fetch at the pinned commit, one bound `mcp_server_source` manifest over the route, then `shipgate scan --format json` from a fresh virtual environment with the wheel installed (0.71–6.86 s per scan) |

## Result

**76 published findings, 1 false: a false-finding rate of 1.3%, under the 2% bar in #643.** Every finding carries a label in `../../labels.csv`, and `tests/test_mcp_server_findings_table.py` re-scores this run against those labels.

| Server | Tools | Findings | True | False |
|---|---|---|---|---|
| `github-mcp-server` | 114 | 2 | 2 | 0 |
| `mongodb-mcp-server` | 50 | 0 | 0 | 0 |
| `mcp-grafana` | 109 | 1 | 1 | 0 |
| `filesystem` | 14 | 0 | 0 | 0 |
| `postgres` | 0 | 1 | 1 | 0 |
| `awslabs-mcp` | 262 | 67 | 67 | 0 |
| `mcp-server-cloudflare` | 80 | 1 | 1 | 0 |
| `terraform-mcp-server` | 61 | 2 | 1 | 1 |
| `everything` | 0 | 1 | 1 | 0 |
| `fetch` | 0 | 1 | 1 | 0 |
| **TOTAL** | 690 | 76 | 75 | 1 |

| Check | True | False |
|---|---|---|
| `SHIP-SCHEMA-FREEFORM-OUTPUT` | 66 | 0 |
| `SHIP-INVENTORY-TOOL-SURFACE-TOO-LARGE` | 5 | 0 |
| `SHIP-INVENTORY-NOT-ENUMERABLE` | 3 | 0 |
| `SHIP-DOC-MISSING-DESCRIPTION` | 1 | 0 |
| `SHIP-MCP-ANNOTATION-CONTRADICTION` | 0 | 1 |

**The false finding.** It is `SHIP-MCP-ANNOTATION-CONTRADICTION` on `terraform-mcp-server`'s `get_plan_json_output`. The tool is read-only, and its description names create, update and delete as the kinds of change it reports. Keyword evidence cannot tell a tool that deletes from one that describes deletes, and #419 forbids letting a tool's name discount it. `tests/test_mcp_permissions.py::test_a_verb_in_a_retrieval_tools_description_still_contradicts_as_a_known_limit` pins this as a known limit.

**The true findings.**
- 66 `awslabs/mcp` handlers annotated `-> str`, each checked against source.
- Five tool surfaces over the 50-tool threshold.
- Three servers the reader cannot enumerate: `postgres` uses `setRequestHandler`, `fetch` uses `@server.list_tools()`, and `everything` passes names bound to constants.
- One 17-character description, `github-mcp-server`'s `create_gist`.

## What the reader could not establish

Asked of the candidate's own source reader, beside the rate. A server with few findings can still be one the reader only partly sees.

| Server | Registrations not enumerated | Descriptions unresolved | Hints unresolved | Tools with a partial surface |
|---|---|---|---|---|
| `github-mcp-server` | 43 | 5 | 27 | 40 |
| `mongodb-mcp-server` | 3 | 12 | 0 | 0 |
| `mcp-grafana` | 0 | 19 | 0 | 0 |
| `filesystem` | 0 | 13 | 0 | 0 |
| `postgres` | 0 | 0 | 0 | 0 |
| `awslabs-mcp` | 349 | 3 | 8 | 170 |
| `mcp-server-cloudflare` | 0 | 2 | 0 | 0 |
| `terraform-mcp-server` | 0 | 0 | 2 | 0 |
| `everything` | 18 | 0 | 0 | 0 |
| `fetch` | 0 | 0 | 0 | 0 |

- **Not enumerated.** A registration whose name is built at run time is recorded in `surface_exclusions`, never guessed. `awslabs/mcp` accounts for 349 of these and `github-mcp-server` for 43.
- **Descriptions unresolved.** A description written as a concatenation, a template with a substitution, an f-string or a variable is recorded as unresolved and not reported as missing (#755). Most are concatenations: `mcp-grafana` 19, `filesystem` 13, `mongodb-mcp-server` 12.

## What moved since `9946f80a`

Nothing but scan times. Every finding, label and per-server coverage count is identical to that run. The engine changes since then, #759's version bump and #760's `preflight` reason, touch neither the source reader nor these checks.

## Before the first run of record

The first bound scans at the same pins published mostly false findings. Three reader and check repairs landed before `9946f80a`:

- **#755.** An unresolvable description was reported as missing, false in 148 of 150 cases, including all 109 on `mcp-grafana`.
- **#756.** Topic nouns read as a contradiction of `readOnlyHint` gave 35 false contradictions on `github-mcp-server` and `terraform-mcp-server`.
- **#757.** `mongodb-mcp-server`'s eval-harness and mock tools were counted as server tools.

## Re-running

```bash
python benchmark/mcp-servers/run.py benchmark/mcp-servers/selection.json ./agents_shipgate-<version>-py3-none-any.whl /tmp/mcp-servers benchmark/mcp-servers/results/<date>-<candidate>
python benchmark/mcp-servers/score.py benchmark/mcp-servers/results/<date>-<candidate>/runs.json benchmark/mcp-servers/labels.csv benchmark/mcp-servers/results/<date>-<candidate>/scores.csv
```

A finding the labels do not cover fails `score.py`, so a candidate that publishes something new must be labelled before its table is committed. Machine-local paths in `runs.json` are masked with `../../../cold-start/vendor.py`'s `mask_local_paths`.
