# Host-config precision, 2026-09-13, candidate `294d0443`

A scripted engineering measurement of comparison correctness on merged pull requests. It does not measure adoption or comprehension, and it makes no claim about any repository listed.

| | |
|---|---|
| Population | `../../selection.json`, frozen 2026-09-13T03:39:28Z: the same 50 merged public PRs, one per repository: 10 `.claude/settings.json`, 10 `.mcp.json`, 10 `.github/workflows/*`, 7 `.cursor/mcp.json`, 7 `.codex/config.toml`, 6 `.vscode/mcp.json` |
| Candidate | `agents_shipgate-0.16.0-py3-none-any.whl` built with `python -m build --wheel` from `294d0443` (main after #721, #730, #731 and #700 step one), sha256 `82780c1a…` (not the locked release build) |
| Environment | macOS (Darwin 25.6.0), Python 3.12 |
| Per case | `git fetch --depth 2` of the merge commit, checkout, then one command: `shipgate diff --workspace <checkout> --base <merge commit^1> --json` (1.3–42.9 s, median 3.0 s); an incomparable case also records `audit --host --json` at both commits |

This directory replaces `../2026-09-13-71ef771d/` as the run of record that `tests/test_host_config_replay.py` re-scores.

## Result

**Precision meets its bar. Widening recall and the benign rate still do not.**

| Metric | Result | Bar | Run of record `71ef771d` |
|---|---|---|---|
| Row precision | **65/65 = 1.000** | ≥ 0.95 | 52/52 |
| Widening recall | **54/64 = 0.844** | ≥ 0.90 | 42/63 = 0.667 |
| Benign zero-row rate | **5/6 = 0.833** | ≥ 95% | 5/6 |
| Comparable | 36/50 | — | 25/50 |

| Kind | Comparable | Rows correct | Widenings named | Benign quiet |
|---|---|---|---|---|
| `.claude/settings.json` | 9/10 | 39/39 | 32/32 | 1/2 |
| `.codex/config.toml` | 3/7 | 4/4 | 4/6 | 1/1 |
| `.cursor/mcp.json` | 4/7 | 2/2 | 2/4 | 2/2 |
| `.github/workflows/*` | 9/10 | 8/8 | 7/9 | 1/1 |
| `.mcp.json` | 6/10 | 7/7 | 6/10 | 0/0 |
| `.vscode/mcp.json` | 5/6 | 5/5 | 3/3 | 0/0 |

**Every miss is a refused comparison.** All 10 missed widenings belong to six incomparable cases:
- `iterate#1916`: 3
- `sentry-cocoa#7885`: 2
- `loft#605`: 2
- `OpenRosWarehouse#34`, `stackrox#16905` and `warden#301`: 1 each

The one benign miss, `near/nearcore#15295`, is incomparable too. In each of the 36 comparable cases, every expected widening was named and no row on the case file was wrong.

**The widening denominator is 64, one more than the run of record's 63.** `open-learning-exchange/myplanet#16644` changed `extraKnownMarketplaces`. Scoring derives each case's scope from the documented support table, and #720 put that key in scope. So the case now has two expected changes and two expected widenings instead of one, and both are named.

## What moved since `71ef771d`

Eleven cases became comparable:

| Cases | Fix |
|---|---|
| `MetaMask/metamask-mobile#29139`, `bencherdev/bencher#673` | #700 step one: a link to an in-tree file is not a coverage limit |
| `blueprintui/blueprintui#395`, `openbootdotdev/openboot#136` | #721: unchanged limits are named, not refused |
| `divinevideo/divine-mobile#6815` | #730: undocumented skill keys are digested |
| `equinor/design-system#4222`, `hashicorp/design-system#3991`, `meshtastic/Meshtastic-Apple#1882`, `sourcegraph/docs#1480`, `jasonjgardner/blockbench-mcp-plugin#39` | #731: `.vscode/mcp.json` is supported; the last case also needed #730 |
| `vtex/openapi-schemas#1583` | #729: Cursor rule globs written unquoted |

## Every refusal, mapped

The cause column comes from the `audit --host --json` each refusal records in `runs.json`. Every link-caused refusal is `unreadable` on an in-tree symlink. A link at a boundary path is refused because the reader does not yet read through it, and reading through is #700 step two. After #700 step one, a link outside boundary paths still counts only when its target is not an in-tree file.

| PR | File | Cause | Issue |
|---|---|---|---|
| `00PrabalK00/OpenRosWarehouse#34` | `.mcp.json` | 6 links under `src/ui_ws/src/`, outside boundary paths | #700 |
| `amelioro/ameliorate#936` | `.vscode/mcp.json` | the file is invalid JSON (a correct refusal) | — |
| `cuthbertLab/music21#1869` | `.codex/config.toml` | `CLAUDE.md` is a link | #700 step two |
| `dotCMS/core#36281` | `.cursor/mcp.json` | 6 links under `.claude/skills/` | #700 step two |
| `getsentry/sentry-cocoa#7885` | `.codex/config.toml` | `.claude/skills` and `CLAUDE.md` are links | #700 step two |
| `getsentry/sentry-mcp#883` | `.mcp.json` | `.claude/skills`, `.cursor/skills` and `CLAUDE.md` are links | #700 step two |
| `getsentry/warden#301` | `.cursor/mcp.json` | 4 links: `.claude/skills`, `.cursor/skills`, `CLAUDE.md` and `plugins/warden/skills/warden` | #700 step two, #700 |
| `iterate/iterate#1916` | `.mcp.json` | 8 links: `.claude/skills/shadcn`, two under `.cursor/skills/`, and five `AGENTS.md` files (the root and four under `apps/`) | #700 step two |
| `leboncoin/spark-web#3005` | `.mcp.json` | 12 links, all under `.claude/skills/` | #700 step two |
| `loft-lang/loft#605` | `.github/workflows/lib-branch-report.yml` | `docs` is a link, outside boundary paths | #700 |
| `near/nearcore#15295` | `.claude/settings.json` | `CLAUDE.md` is a link; so is `pytest/tests/loadtest/locust/res/congestion.wasm`, outside boundary paths | #700 step two, #700 |
| `ossrs/srs#4670` | `.codex/config.toml` | 16 links: `.claude/CLAUDE.md`, `.claude/memory`, `.claude/skills`, `.codex/memory`, `.codex/skills`, three under `.kiro/`, seven under `.openclaw/`, and `memory` | #700 step two, #700 |
| `stackrox/stackrox#16905` | `.cursor/mcp.json` | 10 links: `.mcp.json` itself, two under `central/tlsconfig/testdata/`, and seven links on proto paths under `qa-tests-backend/` and `tests/performance/` | #700 step two, #700 |
| `tsz-org/tsz#12183` | `.codex/config.toml` | `.agents/skills/tsz-emit` and `AGENTS.md` are links | #700 step two |

## Replay

`tests/test_host_config_replay.py` pins each case's outcome with only the selected file present. That checks the comparison of each file; it does not reproduce reach. The test also re-scores this directory's `runs.json` against `scores.csv`, so the published rates stay reproducible.

Third-party strings in `runs.json` and `../../cases/` mask machine-local absolute paths, including paths inside a repository's own committed configuration. Secret-shaped values are redacted as described in `../../vendor.py`.
