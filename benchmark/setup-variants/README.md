# Setup Variants

Each variant tells the tester how to layer a Shipgate hint onto a fresh checkout of an archetype repo. The variants are ordered by hint strength — the further down, the more help the agent has finding Shipgate.

| Variant | Hint | What the agent sees |
|---|---|---|
| [`00-no-hints/`](00-no-hints/) | None | Bare archetype repo. Discovery must happen via web search, package registries, or upstream framework docs. |
| [`10-agents-md/`](10-agents-md/) | `AGENTS.md` snippet | Repo root has `AGENTS.md` with a Shipgate trigger section. |
| [`20-claude-md/`](20-claude-md/) | `CLAUDE.md` | Repo root has `CLAUDE.md` referencing Shipgate (Claude Code primary cue). |
| [`30-cursor-rule/`](30-cursor-rule/) | `.cursor/rules/agents-shipgate.mdc` | Cursor's rules engine activates on tool-surface globs. |
| [`40-shipgate-yaml/`](40-shipgate-yaml/) | Existing `shipgate.yaml` | Repo has already adopted Shipgate; the agent should run an existing manifest, not re-init. |

The runner ([`../runner.md`](../runner.md)) tells the tester how to apply each.

## What's in this directory

Each variant directory has a `README.md` (what to do for the tester) and a `*.template` file (the file to copy into the archetype). Templates have placeholders like `{{REPO_NAME}}` that the tester fills in.

Source for the AGENTS.md / CLAUDE.md / Cursor / shipgate.yaml snippets: [`docs/target-repo-agent-snippets.md`](../../docs/target-repo-agent-snippets.md).

## Why this ordering matters

The headline benchmark metric is the delta between variant `00` and variant `10`. If the snippet variant scores significantly higher than no-hints, the in-repo discovery path is working. If they're flat, the bottleneck is upstream-framework authority — the agent isn't reading the snippet either, or the snippet isn't enough on its own.

The W2 baseline runs only `00-no-hints` and `10-agents-md` to keep the matrix small while establishing the discovery delta. Subsequent weeks expand to include `20`–`40`.
