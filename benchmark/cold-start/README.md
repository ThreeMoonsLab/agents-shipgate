# Cold-start harness

A scripted measurement of whether a supported repository reaches a **correct, covered host-capability comparison** from a fresh clone (#660). It measures installation and routing mechanics on a frozen population of public repositories. It does not measure a person's comprehension, willingness to adopt, or time-to-first-value, and it implies no adoption or endorsement by any repository listed.

## What counts

A case succeeds only when all of these hold:

- `shipgate diff` reports `comparable`;
- the expected change falls within the scope `docs/host-boundary-support.md` claims;
- every capability change the selected file made is named by a row, and every row on that file names one;
- at most two comparison commands, within five minutes.

An incomparable, failed, timed-out or unparsed comparison is never a success, whatever its row count. A source-confirmed unchanged surface with zero rows is a success.

## The pieces

| File | Role |
|---|---|
| `select.py` → `selection.json` | Freezes the population: 10 repositories each for root `.claude/settings.json`, `.mcp.json` and `.cursor/mcp.json`. Every candidate considered is recorded, with the reason for any rejection. |
| `expected.py` | Derives each case's expected changes from the two file versions and the hosts' documentation, never from `agents_shipgate`, and fixes the case's scope before anything runs. |
| `run.py` | The live driver. Per case it runs: a fresh `git clone`, checkout of the pinned head, a fresh virtual environment with `pip install ./<wheel>`, then `shipgate diff --workspace <clone> --base <base> --json`. It adds no manifest, baseline, policy, fetch or patch. |
| `score.py` | Scores runs against the expectations and writes the CSV. |
| `vendor.py` → `cases/` | Stores each case's two file versions, redacted, with their source links, for offline replay. |
| `census.py` | The independent census of host-configuration paths (#704). |
| `results/` | One CSV per candidate run, named by date and candidate. |

## Boundaries

- **Installation** is timed and counted separately from the comparison.
- **The comparison** starts after the clone and checkout. The count covers every command, including any recovery the CLI printed and the harness followed.
- **Selection rule.** The `selection.json` header records the rule: exact root file, public, not a fork or archived, at least two commits touching the file in the 90 days before the selection date. Head is the newest such non-merge commit; base is its parent. The population is not re-chosen after a run, and failed or unsupported cases stay in the denominator.
- **Expected changes cover the selected file only.** Rows on other files the commit touched are counted and reported, not scored.

## Replaying, and what replay does not measure

`tests/test_cold_start_replay.py` rebuilds each vendored case as a two-commit repository that holds **only the selected file**, runs the same comparison offline, and fails whenever a case's outcome differs from its committed `replay.json`.

That isolates one question: is the comparison of that file correct? It deliberately does not reproduce reach. In the live run, every incomparable case stopped on something *else* in the repository: a symlink, a skill's frontmatter, a VS Code MCP file. None of that is vendored, so those cases can replay as comparable. The live population rate comes only from `run.py`, and `results/` records it with every stop point mapped to an issue. Replay pins the reader's correctness on real-world file shapes, so a regression there fails CI.

To measure a candidate live:

```bash
python benchmark/cold-start/run.py benchmark/cold-start/selection.json ./agents_shipgate-<version>-py3-none-any.whl /tmp/cold-start benchmark/cold-start/results/<date>
python benchmark/cold-start/score.py benchmark/cold-start/results/<date>/runs.json benchmark/cold-start/results/<date>/scores.csv
```
