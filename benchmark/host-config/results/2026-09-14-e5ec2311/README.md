# Host-config precision, 2026-09-14, candidate `e5ec2311`

A scripted engineering measurement of comparison correctness on merged pull requests. It does not measure adoption or comprehension, and it makes no claim about any repository listed.

| | |
|---|---|
| Population | `../../selection.json`, frozen 2026-09-13T03:39:28Z: the same 50 merged public PRs, one per repository: 10 `.claude/settings.json`, 10 `.mcp.json`, 10 `.github/workflows/*`, 7 `.cursor/mcp.json`, 7 `.codex/config.toml`, 6 `.vscode/mcp.json` |
| Candidate | `agents_shipgate-1.0.0-py3-none-any.whl`, the wheel Release Engine Smoke run [34808373786](https://github.com/ThreeMoonsLab/agents-shipgate/actions/runs/34808373786) built and exercised on `e5ec2311` (main after #760–#763), sha256 `071abe4f…`. An advisory release cut from that commit publishes this wheel. |
| Environment | macOS (Darwin 25.6.0), Python 3.12 |
| Per case | `git fetch --depth 2` of the merge commit, checkout, then one command: `shipgate diff --workspace <checkout> --base <merge commit^1> --json` (0.8–34.8 s, median 2.2 s); an incomparable case also records `audit --host --json` at both commits |

This directory replaces `../2026-09-13-9d7d145d/` as the run of record that `tests/test_host_config_replay.py` re-scores.

## Result

**Precision meets its bar. Widening recall and the benign rate do not, as the owner decision below records.**

| Metric | Result | Bar | Run of record `9d7d145d` |
|---|---|---|---|
| Row precision | **70/70 = 1.000** | ≥ 0.95 | 69/69 |
| Widening recall | **55/65 = 0.846** | ≥ 0.90 | 54/64 |
| Benign zero-row rate | **5/6 = 0.833** | ≥ 95% | 5/6 |
| Comparable | 41/50 | — | 40/50 |

| Kind | Comparable | Rows correct | Widenings named | Benign quiet |
|---|---|---|---|---|
| `.claude/settings.json` | 9/10 | 39/39 | 32/32 | 1/2 |
| `.codex/config.toml` | 5/7 | 4/4 | 4/6 | 1/1 |
| `.cursor/mcp.json` | 5/7 | 3/3 | 2/4 | 2/2 |
| `.github/workflows/*` | 9/10 | 8/8 | 7/9 | 1/1 |
| `.mcp.json` | 7/10 | 10/10 | 6/10 | 0/0 |
| `.vscode/mcp.json` | 6/6 | 6/6 | 4/4 | 0/0 |

**Every miss is still a refused comparison.** All 10 missed widenings belong to six incomparable cases:
- `iterate#1916`: 3
- `sentry-cocoa#7885`: 2
- `loft#605`: 2
- `OpenRosWarehouse#34`, `stackrox#16905` and `warden#301`: 1 each

The one benign miss, `near/nearcore#15295`, is incomparable too. In each of the 41 comparable cases, every expected widening was named and no row on the case file was wrong.

## What moved since `9d7d145d`

One case, and it is the one the previous run predicted. `amelioro/ameliorate#936` commits `//` comments in `.vscode/mcp.json`. #754 reads that file as JSON with comments, in the engine and in the harness's independent expectation, so the case is now in scope and comparable. It adds one correct row, which names the added `ameliorate` MCP server, and one named widening. Both denominators grow by one: precision 69/69 → 70/70, recall 54/64 → 55/65.

Every other case keeps its stop point, rows and expectation. Two cases' raw expectations differ from the previous `runs.json` only because that file's machine-local paths were masked after the run and these were masked the same way; see *Replay*.

## Every refusal, mapped

The cause column comes from the `audit --host --json` each refusal records in `runs.json`, checked against each merge commit's tree.

"Inert" below describes a link. It points inside the repository, contains no further link, and no path it creates matches a registered host location or pattern: not the link itself, and not any file reached through it. Only links at boundary paths are read through, so an inert link outside them still refuses the whole comparison (#688).

| PR | File | Cause |
|---|---|---|
| `00PrabalK00/OpenRosWarehouse#34` | `.mcp.json` | 6 inert directory links under `src/ui_ws/src/`, to in-tree packages |
| `getsentry/sentry-cocoa#7885` | `.codex/config.toml` | `.claude/skills` links to `../.agents/skills`, which is absent at the merge commit (a dangling link) |
| `getsentry/sentry-mcp#883` | `.mcp.json` | `.cursor/skills` links to `.agents/skills`; `.cursor/skills` is not a registered boundary location, so the link is inert |
| `getsentry/warden#301` | `.cursor/mcp.json` | 2 inert links: `.cursor/skills` and `plugins/warden/skills/warden` |
| `iterate/iterate#1916` | `.mcp.json` | 2 dangling links under `.cursor/skills/`, to `.opencode/skills/*`, which is absent |
| `loft-lang/loft#605` | `.github/workflows/lib-branch-report.yml` | `docs` is an inert directory link to `doc` |
| `near/nearcore#15295` | `.claude/settings.json` | `pytest/tests/loadtest/locust/res/congestion.wasm` is a dangling link |
| `ossrs/srs#4670` | `.codex/config.toml` | 13 link sources under `.claude/memory`, `.codex/memory`, `.codex/skills`, `.kiro/`, `.openclaw/` and `memory`. Ten are inert, one dangles, and two targets contain further links. |
| `stackrox/stackrox#16905` | `.cursor/mcp.json` | 9 link sources: seven inert proto directory links under `qa-tests-backend/` and `tests/performance/`, and a link chain under `central/tlsconfig/testdata/` that ends at a missing file |

## What would move the rates

This is analysis of the refusals above, not a measurement.

The eight refusals that hold a missed widening or the benign miss come only from inert or dangling links, meaning links whose created paths reach no registered host location. If such a link stopped refusing the comparison, those eight cases could compare, and they hold all 10 missed widenings and the benign miss. `ossrs/srs#4670` would still refuse, because two of its link targets contain further links; it has no expected widening.

**Owner decision (2026-09-13): inert and dangling links keep refusing the comparison.** Recall and the benign rate are therefore reported below their bars for 1.0, not reached by changing what counts as a coverage limit.

## Replay

`tests/test_host_config_replay.py` pins each case's outcome with only the selected file present. That checks the comparison of each file; it does not reproduce reach. The test also re-scores this directory's `runs.json` against `scores.csv`, so the published rates stay reproducible.

`run.py` records third-party configuration verbatim, and two repositories commit a home directory into theirs. After the run, every string in `runs.json` was passed through `mask_local_paths` from `../../../cold-start/vendor.py`, the same masking the vendored cases use; re-scoring the masked file reproduced `scores.csv` byte for byte. `tests/test_benchmark_results_privacy.py` now refuses a committed `runs.json` that carries an unmasked home or temporary path. Secret-shaped values in `../../cases/` are redacted as described in `../../vendor.py`.
