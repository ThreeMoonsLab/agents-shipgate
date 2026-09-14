# Cold start, 2026-09-14, candidate `e5ec2311`

A scripted engineering measurement of installation and routing mechanics. It does not measure a person's time-to-first-value, comprehension or adoption, and it makes no claim about any repository listed.

| | |
|---|---|
| Population | `../../selection.json`, frozen 2026-09-13T02:45:27Z: the same 30 public repositories as the earlier runs, 10 each for root `.claude/settings.json`, `.mcp.json` and `.cursor/mcp.json` |
| Candidate | `agents_shipgate-1.0.0-py3-none-any.whl`, the wheel Release Engine Smoke run [34808373786](https://github.com/ThreeMoonsLab/agents-shipgate/actions/runs/34808373786) built and exercised on `e5ec2311` (main after #760–#763), sha256 `071abe4f…`. An advisory release cut from that commit publishes this wheel. |
| Environment | macOS (Darwin 25.6.0), Python 3.12 |
| Per case | fresh full `git clone`, checkout of the pinned head; fresh venv plus `pip install ./<wheel>` (2 install commands, 4.9–5.6 s, timed separately); then one comparison command, `shipgate diff --workspace <clone> --base <base> --json` |

## Result

**26 of 30 succeeded, against a bar of 24.** The run on `9d7d145d` also succeeded on 26, the run on `294d0443` on 25, and the first run, on `8d43106f`, on 17.

| | Count |
|---|---|
| Comparable **and** correct, within supported scope | **26** (25 of 29 changed cases, 1 of 1 no-change case) |
| Comparable and correct, but out of scope fixed before the run | 1 |
| Incomparable | 3 |
| Comparable with a wrong or missing row | 0 |
| Over 2 comparison commands, or over 5 minutes | 0 (every case used 1 comparison command; comparison 1.3–13.1 s, median 2.6 s; clone 0.6–45.2 s, median 2.6 s) |

| Root file | Cases | Succeeded |
|---|---|---|
| `.claude/settings.json` | 10 | 8 |
| `.mcp.json` | 10 | 9 |
| `.cursor/mcp.json` | 10 | 9 |

Every comparison that ran was correct: all 27 comparable cases named each expected change and nothing else.

## What moved since `9d7d145d`

No outcome. Every case kept its stop point, scope, expected changes and rows; only the timings differ. The engine changes since that run, #754's JSON-with-comments reading of `.vscode/mcp.json`, #759's version bump and #760's `preflight` reason, touch no file this population selects.

## Every stop point, mapped

| Case | Outcome | Cause |
|---|---|---|
| `haru/redmine_wiki_extensions` | incomparable | one unchanged root symlink, `redmine_wiki_extensions`, whose target is an absolute path outside the repository. External targets stay blocking by the #700 decision. |
| `theinterfold/interfold` | incomparable | four unchanged links under `templates/default/.interfold/support/` and `examples/CRISP/.interfold/support/`, pointing at in-tree scripts. They are outside every boundary path, and a link outside boundary paths is still refused unless it points at an in-tree file (#688). |
| `dadederk/RetroRacing` | incomparable | unchanged directory links under 35 agent-tool skill folders, such as `.adal/skills/`, `.trae/skills/` and `.codex/skills/`, none of them a registered boundary location (#688). Its four `.claude/skills/` links are read through. |
| `JGaldo-beep/transmi-cli` | unclassified | the base declared a server at the top level of `.mcp.json`, and the head moved it under `mcpServers`. The harness could not classify the stray key before the run. The comparison was comparable, with one row. |

## Replay

`tests/test_cold_start_replay.py` pins each case's outcome with only the selected file present. That checks the comparison of each file; it does not reproduce reach. The population rate comes only from the live run recorded here.

Third-party strings in `../../cases/` mask machine-local absolute paths as described in `../../vendor.py`. `run.py` records `runs.json` verbatim, so after the run every string in it was passed through the same `mask_local_paths`, and re-scoring the masked file reproduced `scores.csv` byte for byte. `tests/test_benchmark_results_privacy.py` refuses a committed `runs.json` that carries an unmasked home or temporary path.
