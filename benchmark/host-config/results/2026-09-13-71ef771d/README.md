# Host-config precision, 2026-09-13, candidate `71ef771d`

A scripted engineering measurement of comparison correctness on merged pull requests. It does not measure adoption or comprehension, and it makes no claim about any repository listed.

| | |
|---|---|
| Population | `../../selection.json`, frozen 2026-09-13T03:39:28Z: 50 merged public PRs, one per repository: 10 `.claude/settings.json`, 10 `.mcp.json`, 10 `.github/workflows/*`, 7 `.cursor/mcp.json`, 7 `.codex/config.toml`, 6 `.vscode/mcp.json` |
| Candidate | `agents_shipgate-0.16.0-py3-none-any.whl` built with `python -m build --wheel` from `71ef771d` (not the locked release build), sha256 `b226bb12…` |
| Environment | macOS (Darwin 25.6.0), Python 3.12 |
| Per case | `git fetch --depth 2` of the merge commit, checkout, then one command: `shipgate diff --workspace <checkout> --base <merge commit^1> --json` (1.3–38.0 s, median 3.4 s) |

## Result

**Precision meets its bar. Widening recall and the benign rate do not.**

| Metric | Result | Bar |
|---|---|---|
| Row precision | **52/52 = 1.000** | ≥ 0.95 |
| Widening recall | **42/63 = 0.667** | ≥ 0.90 |
| Benign zero-row rate | **5/6 = 0.833** | ≥ 95% |
| Comparable | 25/50 | — |

| Kind | Comparable | Rows correct | Widenings named | Benign quiet |
|---|---|---|---|---|
| `.claude/settings.json` | 7/10 | 37/37 | 30/31 | 1/2 |
| `.codex/config.toml` | 2/7 | 1/1 | 1/6 | 1/1 |
| `.cursor/mcp.json` | 2/7 | 0/0 | 0/4 | 2/2 |
| `.github/workflows/*` | 8/10 | 7/7 | 5/9 | 1/1 |
| `.mcp.json` | 6/10 | 7/7 | 6/10 | 0/0 |
| `.vscode/mcp.json` | 0/6 | 0/0 | 0/3 | 0/0 |

**Every miss is a refused comparison.** All 21 missed widenings and the one benign miss (`near/nearcore#15295`) belong to incomparable cases. In each of the 25 comparable cases every expected widening was named, and no row on the case file was wrong.

## The same population on the #727 candidate

`e98e8cb6`, the pull request branch for #721, was measured the same day with the driver that records `audit --host --json` for each refusal. It made `blueprintui/blueprintui#395` and `openbootdotdev/openboot#136` comparable, and both name their limit in `unchanged_limits`. Precision 54/54, widening recall 45/63 = 0.714, benign 5/6, comparable 27/50. That candidate is not merged, so this directory stays the run of record. The cause column below comes from that run's audit.

## Every refusal, mapped

| PR | File | Cause | Issue |
|---|---|---|---|
| `00PrabalK00/OpenRosWarehouse#34` | `.mcp.json` | in-tree symlinks (6 unreadable paths, e.g. `src/ui_ws/src/map_editor`) | #700 |
| `MetaMask/metamask-mobile#29139` | `.claude/settings.json` | in-tree symlinks (1 unreadable path, e.g. `android/app/src/main/assets/branch.json`) | #700 |
| `amelioro/ameliorate#936` | `.vscode/mcp.json` | `.vscode/mcp.json` is invalid JSON (a correct refusal) | — |
| `bencherdev/bencher#673` | `.claude/settings.json` | in-tree symlinks (1 unreadable path, e.g. `changelog.md`) | #700 |
| `blueprintui/blueprintui#395` | `.cursor/mcp.json` | unchanged skills refused as `frontmatter_invalid`, `frontmatter_unknown_fields`; compared past on the #727 candidate, which names them in `unchanged_limits` | #721, #730 |
| `cuthbertLab/music21#1869` | `.codex/config.toml` | in-tree symlinks (1 unreadable path, e.g. `CLAUDE.md`) | #700 |
| `divinevideo/divine-mobile#6815` | `.codex/config.toml` | undocumented skill frontmatter keys | #730 |
| `dotCMS/core#36281` | `.cursor/mcp.json` | in-tree symlinks (8 unreadable paths, e.g. `.claude/skills/angular-developer`); Cursor rule globs written unquoted; a skill with no frontmatter; undocumented skill frontmatter keys; VS Code MCP coverage is `experimental` | #700, #729, #730, #731 |
| `equinor/design-system#4222` | `.vscode/mcp.json` | VS Code MCP coverage is `experimental` | #731 |
| `getsentry/sentry-cocoa#7885` | `.codex/config.toml` | in-tree symlinks (2 unreadable paths, e.g. `.claude/skills`) | #700 |
| `getsentry/sentry-mcp#883` | `.mcp.json` | in-tree symlinks (3 unreadable paths, e.g. `.claude/skills`); VS Code MCP coverage is `experimental` | #700, #731 |
| `getsentry/warden#301` | `.cursor/mcp.json` | in-tree symlinks (4 unreadable paths, e.g. `.claude/skills`) | #700 |
| `hashicorp/design-system#3991` | `.vscode/mcp.json` | VS Code MCP coverage is `experimental` | #731 |
| `iterate/iterate#1916` | `.mcp.json` | in-tree symlinks (8 unreadable paths, e.g. `.claude/skills/shadcn`); undocumented skill frontmatter keys | #700, #730 |
| `jasonjgardner/blockbench-mcp-plugin#39` | `.vscode/mcp.json` | undocumented skill frontmatter keys; VS Code MCP coverage is `experimental` | #730, #731 |
| `leboncoin/spark-web#3005` | `.mcp.json` | in-tree symlinks (14 unreadable paths, e.g. `.claude/agents/component-reviewer.md`) | #700 |
| `loft-lang/loft#605` | `.github/workflows/lib-branch-report.yml` | in-tree symlinks (4 unreadable paths, e.g. `docs`) | #700 |
| `meshtastic/Meshtastic-Apple#1882` | `.vscode/mcp.json` | VS Code MCP coverage is `experimental` | #731 |
| `near/nearcore#15295` | `.claude/settings.json` | in-tree symlinks (64 unreadable paths, e.g. `CLAUDE.md`) | #700 |
| `openbootdotdev/openboot#136` | `.github/workflows/claude-code-review.yml` | unchanged skills refused as `frontmatter_invalid`; compared past on the #727 candidate, which names them in `unchanged_limits` | #721 |
| `ossrs/srs#4670` | `.codex/config.toml` | in-tree symlinks (36 unreadable paths, e.g. `.claude/CLAUDE.md`) | #700 |
| `sourcegraph/docs#1480` | `.vscode/mcp.json` | VS Code MCP coverage is `experimental` | #731 |
| `stackrox/stackrox#16905` | `.cursor/mcp.json` | in-tree symlinks (92 unreadable paths, e.g. `.mcp.json`) | #700 |
| `tsz-org/tsz#12183` | `.codex/config.toml` | in-tree symlinks (2 unreadable paths, e.g. `.agents/skills/tsz-emit`) | #700 |
| `vtex/openapi-schemas#1583` | `.cursor/mcp.json` | Cursor rule globs written unquoted | #729 |

## Replay

`tests/test_host_config_replay.py` pins each case's outcome with only the selected file present. There, 44 of 50 cases compare, 77/77 rows are correct, 60/63 widenings are named and 6/6 benign cases are quiet. The three missed widenings and the six refusals are the `.vscode/mcp.json` cases (#731), and one of those is the invalid file. That bounds what removing every out-of-file refusal would recover. It is not a measured population rate. The next live run against a candidate carrying those fixes is what counts.

Third-party strings in `runs.json` and `../../cases/` mask machine-local absolute paths; secret-shaped values are redacted as described in `../../vendor.py`.
