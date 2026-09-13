# Cold start, 2026-09-13, candidate `294d0443`

A scripted engineering measurement of installation and routing mechanics. It does not measure a person's time-to-first-value, comprehension or adoption, and it makes no claim about any repository listed.

| | |
|---|---|
| Population | `../../selection.json`, frozen 2026-09-13T02:45:27Z: the same 30 public repositories as the first run, 10 each for root `.claude/settings.json`, `.mcp.json` and `.cursor/mcp.json` |
| Candidate | `agents_shipgate-0.16.0-py3-none-any.whl` built with `python -m build --wheel` from `294d0443` (main after #721, #730, #731 and #700 step one), sha256 `82780c1a…`. Not the locked release build the first run used. |
| Environment | macOS (Darwin 25.6.0), Python 3.12 |
| Per case | fresh full `git clone`, checkout of the pinned head; fresh venv plus `pip install ./<wheel>` (2 install commands, 5.3–6.6 s, timed separately); then one comparison command, `shipgate diff --workspace <clone> --base <base> --json` |

## Result

**25 of 30 succeeded. The bar is 24, so this candidate meets it.** The first run, on `8d43106f`, succeeded on 17.

| | Count |
|---|---|
| Comparable **and** correct, within supported scope | **25** (24 of 29 changed cases, 1 of 1 no-change case) |
| Comparable and correct, but out of scope fixed before the run | 1 |
| Incomparable | 4 |
| Comparable with a wrong or missing row | 0 |
| Over 2 comparison commands, or over 5 minutes | 0 (every case used 1 comparison command; wall time 2.2–15.7 s, median 3.9 s) |

| Root file | Cases | Succeeded |
|---|---|---|
| `.claude/settings.json` | 10 | 8 |
| `.mcp.json` | 10 | 8 |
| `.cursor/mcp.json` | 10 | 9 |

Every comparison that ran was correct: all 26 comparable cases named each expected change and nothing else. All four refusals are `unreadable` in-tree symlinks, and in each the commit under review changed only a supported configuration file.

## What moved since `8d43106f`

Seven of the first run's eleven refusals now succeed, and so does one case that was out of scope then. That is eight more successes:

- **#721:** an unchanged partial or experimental surface is named in `unchanged_limits` instead of refusing the comparison.
- **#722 and #730:** documented skill and command frontmatter, undocumented keys and frontmatter-less skills are read.
- **#731:** `.vscode/mcp.json` is supported rather than experimental.
- **#720:** `open-learning-exchange/myplanet` names the `extraKnownMarketplaces` change and is now in scope.

## Every stop point, mapped

| Case | Outcome | Cause | Issue |
|---|---|---|---|
| `haru/redmine_wiki_extensions` | incomparable | one unchanged root symlink, `redmine_wiki_extensions`, whose target is an absolute path outside the repository; external targets stay blocking by the #700 decision | #700 |
| `theinterfold/interfold` | incomparable | four unchanged symlinks under `templates/default/.interfold/support/` and `examples/CRISP/.interfold/support/`, outside any boundary path and not links to in-tree files | #700 |
| `absolute-aungkomyint/athapyar-htote-web` | incomparable | an unchanged `.claude/skills/code-documentation-doc-generate` symlink at a boundary path; reading through it is #700 step two | #700 |
| `dadederk/RetroRacing` | incomparable | 79 unchanged skill-directory symlinks (`.adal/skills/*`, `.codex/skills/*`, `.trae/skills/*` and others) at boundary paths; #700 step two | #700 |
| `JGaldo-beep/transmi-cli` | unclassified | the base declared a server at the top level of `.mcp.json`, and the head moved it under `mcpServers`; the harness could not classify the stray key before the run. The comparison was comparable, with one row. | input shape; no engine issue |

## Replay

`tests/test_cold_start_replay.py` pins each case's outcome with only the selected file present. That checks the comparison of each file; it does not reproduce reach. The population rate comes only from the live run recorded here.

Third-party strings in `runs.json` and `../../cases/` mask machine-local absolute paths (home and temporary directories), as described in `../../vendor.py`. Paths that appear inside a repository's own committed configuration are masked the same way.
