# Variant 00 — No hints

Run the prompt against the archetype repo as-is. **Do not** add any Shipgate-related files. Do not pre-install Shipgate in the agent's environment. Do not give the agent the Shipgate name in the prompt.

This variant measures discovery without help — the hardest case and the most informative one. If the score moves on this variant after a docs change, the change is doing real work. If it doesn't, the bottleneck is upstream-framework authority, not in-repo prose.

## Setup

```bash
cd benchmark/repos/<archetype>/
git clean -fdx                         # ensure no leftover Shipgate files
git checkout .                          # ensure clean tree
```

That's it. The repo is ready.

## What to watch for

- Did the agent find Shipgate via web search, package registry, or framework docs?
- If it found it: which surface tipped it off (`pyproject.toml` name, blog post, framework example)?
- Did it propose `pipx install agents-shipgate` directly, or `uvx`, or the GitHub Action, or the zero-install detector script?

The notes column on the CSV row should answer the second question — that's the load-bearing data for prioritizing upstream-PR work.
