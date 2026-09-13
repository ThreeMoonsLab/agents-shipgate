# Cold start, 2026-09-13, candidate `8d43106f`

A scripted engineering measurement of installation and routing mechanics. It does not measure a person's time-to-first-value, comprehension or adoption, and it makes no claim about any repository listed.

| | |
|---|---|
| Population | `../../selection.json`, frozen 2026-09-13T02:45:27Z: 30 public repositories, 10 each for root `.claude/settings.json`, `.mcp.json` and `.cursor/mcp.json` |
| Candidate | `agents_shipgate-0.16.0-py3-none-any.whl` built from `8d43106f` with the locked release build, sha256 `f59a271a…` |
| Environment | macOS (Darwin 25.6.0), Python 3.12 |
| Per case | fresh full `git clone`, checkout of the pinned head; fresh venv plus `pip install ./<wheel>` (2 install commands, timed separately); then one comparison command, `shipgate diff --workspace <clone> --base <base> --json` |

## Result

**17 of 30 succeeded. The bar is 24, so this candidate does not meet it.**

| | Count |
|---|---|
| Comparable **and** correct, within supported scope | **17** (16 of 29 changed cases, 1 of 1 no-change case) |
| Comparable and correct, but out of scope fixed before the run | 2 |
| Incomparable | 11 |
| Comparable with a wrong or missing row | 0 |
| Over 2 comparison commands, or over 5 minutes | 0 (comparison wall time 2.1–13.1 s) |

Every comparison that ran was correct: all 19 comparable cases named each expected change and nothing else. The failures are reach. In **11 of 11** incomparable cases, the file that made the base inventory incomplete was **not changed by the commit under review**.

## Every stop point, mapped

| Case | Outcome | Cause | Issue |
|---|---|---|---|
| `haru/redmine_wiki_extensions` | incomparable | unchanged root symlink to an absolute external path | #700, #721 |
| `theinterfold/interfold` | incomparable | unchanged symlinks under `.interfold/support/` | #700, #721 |
| `dadederk/RetroRacing` | incomparable | unchanged skill-directory symlinks (80 blocking issues) | #700, #721 |
| `absolute-aungkomyint/athapyar-htote-web` | incomparable | unchanged `.claude/skills/*` symlink; skill frontmatter | #700, #721, #722 |
| `vmihalis/hacker-bob` | incomparable | unchanged skill with undocumented `skill:` key | #722, #721 |
| `future-architect/uzomuzo-oss` | incomparable | unchanged skills without `name` (documented default); `.claude/rules/agents.md` | #722, #721 |
| `justinstimatze/winze` | incomparable | unchanged command whose `argument-hint` uses the documented bracket form (a YAML list); skill `version`/`author` | #722, #721 |
| `IgnacioBarEsp/PlanearIA` | incomparable | unchanged skills with `version`; command with `name`/`category`/`tags` | #722, #721 |
| `BigSimmo/Database` | incomparable | unchanged skill whose frontmatter is invalid YAML (a correct refusal); when isolated, the changed Supabase URL query has no row | #721, #723 |
| `solal3105/grandsprojets` | incomparable | no blocking issue; unchanged `.vscode/mcp.json` makes coverage `experimental` | #721 |
| `Kpoiut/ruleblast` | incomparable | no blocking issue; unchanged `.vscode/mcp.json` makes coverage `experimental` | #721 |
| `open-learning-exchange/myplanet` | out of scope | `extraKnownMarketplaces` changed; the one row names only the plugin | #720 |
| `JGaldo-beep/transmi-cli` | unclassified | base declared a server at the top level of `.mcp.json`, head moved it under `mcpServers`; the harness could not classify the stray key before the run | input shape; no engine issue |

## Replay

`tests/test_cold_start_replay.py` pins each case's outcome with only the selected file present. There, 10 of the 11 incomparable cases compare, and 9 of those are correct; `BigSimmo/Database` misses the URL query change (#723). That bounds what #721 alone would recover. It is not a measured population rate. The next live run against a candidate carrying #721, #722 and #723 is what counts.

Third-party strings in `runs.json` and `../../cases/` mask machine-local absolute paths (home and temporary directories). The masking is described in `../../vendor.py`.
