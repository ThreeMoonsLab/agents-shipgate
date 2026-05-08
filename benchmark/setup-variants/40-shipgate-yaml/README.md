# Variant 40 — Existing `shipgate.yaml`

Layer a `shipgate.yaml` onto the archetype repo before the run. The agent should detect that Shipgate is already adopted and run `scan`, not re-init.

## Setup

```bash
cd benchmark/repos/<archetype>/
cp ../../setup-variants/40-shipgate-yaml/shipgate.yaml.template shipgate.yaml
# Edit the manifest to match the archetype:
#   - replace {{REPO_NAME}} with the archetype's repo name
#   - set agent.name and agent.declared_purpose to match the archetype
#   - point tool_sources[].path at real files in this repo
agents-shipgate doctor -c shipgate.yaml --workspace .   # confirm it validates
git add shipgate.yaml
git commit -m "Add shipgate.yaml (benchmark variant)"
```

If `doctor` reports unresolved sources, fix the paths before committing — a broken manifest fails for reasons unrelated to discovery and pollutes the score.

## What this measures

This is the highest-help variant. The agent should run a single `scan` (or follow the bundled `add-shipgate-to-repo` recipe), recognize the existing manifest, and surface findings.

A common failure mode: the agent ignores the existing manifest and runs `init --write`, which refuses to overwrite. Catching that in the score is the point of this variant.
