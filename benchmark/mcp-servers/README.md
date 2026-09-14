# Ten-server false-finding table (#658)

A scripted engineering measurement of existing-reader precision on fixed public MCP servers: of the findings a candidate build publishes for them, how many are false. It does not measure adoption, a server's actual runtime behaviour, or how often any finding appears across all servers.

| File | What it is |
|---|---|
| `selection.json` | The frozen population: ten servers, each pinned to a commit and a scan route, and the rule that chose them |
| `run.py` | The live driver. For each server it fetches the pinned commit, writes one bound `mcp_server_source` manifest over the route, and runs `shipgate scan` from a fresh virtual environment with the candidate wheel installed. It records every published finding and what the reader could not establish. |
| `labels.csv` | One label per published finding, keyed by server, check, tool and source path: `true` or `false`, the rule that decided it, and a note on the source that was read |
| `score.py` | Joins the run to the labels and writes the table. A finding without a label, or a label that matches no finding, fails rather than being guessed. |
| `results/` | One directory per candidate run: `runs.json`, `scores.csv` and a README with the table |

`tests/test_mcp_server_findings_table.py` re-scores the run of record against `labels.csv` and requires the published table, no missing or stale labels, and a false-finding rate under the 2% bar in #643. Third-party source is not copied into this repository, so CI does not re-run the servers. The live run repeats for each release candidate, and a false finding that is fixed becomes a case in the shared reader corpus.

## Selection

The rule uses only text committed before any scan:

1. The five servers #658 names: `github-mcp-server`, `mongodb-mcp-server`, `mcp-grafana`, `filesystem` and `postgres`. `filesystem` is `modelcontextprotocol/servers` `src/filesystem`. `postgres` is the reference server that now lives in `modelcontextprotocol/servers-archived`.
2. The other repositories named in the survey table in `docs/mcp-registration-idioms.md`: `awslabs/mcp`, `cloudflare/mcp-server-cloudflare` and `hashicorp/terraform-mcp-server`.
3. Filled to ten with the reference servers `modelcontextprotocol/servers` lists in its README, in listed order, skipping one already selected: `everything`, then `fetch`.

**Why not the #431 survey list.** The thirty-repository survey behind #431 would have been the natural population, but its list was never committed. Two ranking alternatives were tried and not used: the official MCP Registry's search, which is keyword-based and dominated by third-party entries, and GitHub stars for the `mcp-server` topic, which rank frameworks and applications above servers.

**Scan route.** Each server is scanned over the route `detect` suggested for its repository, or over its own directory in the two `modelcontextprotocol` repositories.

## Labels

A finding is `true` when its claim holds for the pinned source and `false` when it does not. Each rule is recorded in `labels.csv`:

| Rule | Label | What was checked |
|---|---|---|
| `count_exceeds_threshold` | true | the registered tool count exceeds the check's threshold |
| `returns_unstructured_text` | true | the tool's handler is annotated `-> str` |
| `description_shorter_than_minimum` | true | the registered description is below the 20-character minimum |
| `registration_api_not_read` | true | the server registers tools through a low-level request-handler API that no shipped idiom reads |
| `registration_names_not_literal` | true | the server registers through a supported idiom whose names are not literals, so none is enumerated |
| `description_names_reported_changes` | false | a read-only tool whose description names create, update or delete as kinds of change it reports |

## What the rate does not show

Each run also records what the reader could not establish, and the results README reports it beside the rate:

- **Registrations whose name is built at run time.** These are recorded in `surface_exclusions` and never guessed.
- **Tools whose description or published hints are not written as literals.** Their descriptions are recorded as unresolved and are not reported as missing.

A server with few findings can still be one the reader only partly sees.
