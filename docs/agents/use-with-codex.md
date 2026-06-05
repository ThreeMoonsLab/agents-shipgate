# Use Agents Shipgate with Codex

Agents Shipgate ships a skill-only Codex plugin so users can install it from
the Codex plugin experience, start a new thread, invoke `$agents-shipgate`, and
have Codex run the existing Shipgate CLI workflows correctly. The plugin gives
Codex the workflows for verify, bootstrap, report reading, advisory CI, and
finding triage; the scanner itself still runs through the `agents-shipgate`
CLI installed in the local environment.

For repos that prefer committed instructions instead of a plugin install,
OpenAI Codex also reads repo-level `AGENTS.md` instructions and repo-scoped
Codex Skills under `.agents/skills/<name>/`.

| Surface | What it does | Source path in this repo |
|---|---|---|
| Codex plugin | Installable plugin package for Codex with the Agents Shipgate skill bundled. | [`plugins/agents-shipgate/`](../../plugins/agents-shipgate/) |
| Marketplace | Repo marketplace entry that lets Codex discover and install the plugin. | [`.agents/plugins/marketplace.json`](../../.agents/plugins/marketplace.json) |
| `AGENTS.md` snippet | Tells Codex when Shipgate is relevant and names the canonical commands. | [`docs/target-repo-agent-snippets.md`](../target-repo-agent-snippets.md) §`AGENTS.md` |
| Codex skill | Repo-scoped skill Codex can invoke explicitly with `$agents-shipgate` or implicitly when the task matches. | [`.agents/skills/agents-shipgate/`](../../.agents/skills/agents-shipgate/) |
| Reusable prompts | Longer copy-paste recipes for agents that do not use skills. | [`prompts/README.md`](../../prompts/README.md) |

## Install From Codex

The v1 plugin is distributed through this repository's Codex marketplace and
Codex workspace sharing. In Codex, add or select the Agents Shipgate
marketplace, install **Agents Shipgate**, start a new thread, and invoke:

```text
$agents-shipgate verify this agent PR and summarize the merge verdict.
```

To add the repo-backed marketplace, use:

```bash
codex plugin marketplace add ThreeMoonsLab/agents-shipgate
```

When testing from a local checkout instead of GitHub, use the checkout root:

```bash
codex plugin marketplace add /path/to/agents-shipgate
```

The plugin can also be shared from the Codex app after installation. Shared
users install it from **Plugins** > **Shared with you**, then start a new
thread before invoking `$agents-shipgate`.

The plugin manifest points to dedicated privacy and terms pages in this repo.
Public/OpenAI-curated listing, if pursued later, is a separate platform
submission using the same skill-only package.

### Migrating From The Beta Marketplace

Early testers may have installed the old `agents-shipgate-beta` marketplace.
For GA, remove the beta marketplace and install from the GA marketplace name:

```bash
codex plugin remove agents-shipgate
codex plugin marketplace remove agents-shipgate-beta
codex plugin marketplace add ThreeMoonsLab/agents-shipgate
codex plugin add agents-shipgate@agents-shipgate
```

## Runtime CLI Prerequisite

The Codex plugin supplies workflow instructions, not the scanner binary. Before
asking Codex to scan or verify a repo, make sure the CLI is available and
`agents-shipgate --version` reports `0.11.0` or newer:

```bash
pipx install agents-shipgate
pipx upgrade agents-shipgate  # plain install is a no-op over a stale build
agents-shipgate --version
```

If `pipx` is unavailable, use:

```bash
python -m pip install -U "agents-shipgate>=0.11"
agents-shipgate --version
```

When `$agents-shipgate` runs and the CLI is missing or older than 0.11.0,
Codex should ask for an install or upgrade instead of continuing to `detect`,
`init`, `scan`, or `verify`.

## Codex Plugin Smoke

Before treating a release as Codex-installable, run this smoke from a machine
with a working Codex CLI/app:

```bash
codex plugin marketplace add /path/to/agents-shipgate
codex plugin list --marketplace agents-shipgate
codex plugin add agents-shipgate@agents-shipgate
agents-shipgate --version
codex exec --sandbox read-only \
  '$agents-shipgate Do not run shell commands. Do not edit files. Reply with exactly: LOADED agents-shipgate'
```

Passing evidence:

- `plugin list` shows `agents-shipgate@agents-shipgate`.
- `plugin add` reports the plugin was added from `agents-shipgate`.
- `agents-shipgate --version` reports `0.11.0` or newer.
- the installed plugin cache contains `skills/agents-shipgate/SKILL.md`.
- the `codex exec` response is `LOADED agents-shipgate`.

## Install In Your Agent Repo

From the root of the project where Codex should run Shipgate:

```bash
pipx install agents-shipgate
pipx upgrade agents-shipgate
agents-shipgate --version
agents-shipgate init --workspace . --write --agent-instructions=agents-md,codex-skill
```

To install every supported agent surface at once:

```bash
agents-shipgate init --workspace . --write --agent-instructions=all
```

The `codex-skill` target writes `.agents/skills/agents-shipgate/`. It is
idempotent and safe to rerun; user-edited skill files are not overwritten. Use
this path when a repo wants durable checked-in instructions in addition to, or
instead of, the installable Codex plugin.

## Customize Generated Skill Content

The installed package ships the default skill content offline. A downstream
repo can override selected files without patching the wheel by adding
`.agents-shipgate/adoption-kit.yaml`:

```yaml
schema_version: 1
targets:
  codex-skill:
    overrides_dir: .agents-shipgate/adoption-kit/codex-skill
```

Files under the override directory are relative to
`.agents/skills/agents-shipgate/`, for example `SKILL.md`,
`references/recipes.md`, or `assets/advisory-pr-comment.yml`. Pass a
different config path with `--agent-instructions-kit <path>`.

## Sync Generated Skill Content

The installable plugin skill is a rendered copy of
`adoption-kits/codex-skill/`. When changing that kit, sync both generated
locations:

- `.agents/skills/agents-shipgate/`
- `plugins/agents-shipgate/skills/agents-shipgate/`

For version-rendered files such as `assets/advisory-pr-comment.yml`, update the
rendered copies together, bump `EXPECTED_CODEX_SKILL_RENDER_SHA256`, append the
outgoing file hashes to
`adoption-kits/codex-skill/.agents-shipgate-kit-metadata.json`
`prior_render_sha256`, and rerun the renderer and plugin package tests.

## Verify

Open Codex in the project and run these checks:

1. Install the Agents Shipgate plugin from Codex, start a new thread, and ask:
   "$agents-shipgate verify this agent PR and summarize the merge verdict."
   Codex should load the plugin skill, require `agents-shipgate >=0.11.0`, then
   read `agents-shipgate-reports/verifier.json` and lead with `merge_verdict`;
   it then reads `agents-shipgate-reports/report.json` for
   `release_decision.decision`.
2. Ask: "prepare this agent repo for production release and add appropriate
   CI preflight checks." Codex should use the AGENTS.md snippet or the
   `agents-shipgate` skill, run `agents-shipgate verify --preview --json` or
   `agents-shipgate detect --workspace . --json`, and continue only when
   Shipgate is relevant.
3. In a repo that already has `shipgate.yaml`, ask Codex to finish an
   agent-tool change. Before its final response, Codex should run
   `agents-shipgate verify --workspace . --config shipgate.yaml --base origin/main --head HEAD --ci-mode advisory --format json`
   or report the exact `agents-shipgate trigger` skip verdict.

   For local uncommitted work, omit `--base`/`--head` so uncommitted edits are
   scanned. For committed PR/CI refs, make the base ref available first because
   `verify` never fetches.

If these pass, Codex can install, invoke, and operate the Shipgate workflow.

## Verify An Agent PR

On any PR that changes agent tools, MCP exports, OpenAPI specs, prompts,
permissions, policies, CI gates, or `shipgate.yaml`, Codex should run the
verifier before claiming the work is done:

```bash
agents-shipgate verify --base origin/main --head HEAD --json
```

Then read `agents-shipgate-reports/verifier.json` and **lead with
`merge_verdict`** (`mergeable` / `human_review_required` /
`insufficient_evidence` / `blocked` / `unknown`). It is a deterministic
projection of `release_decision.decision`, which remains the gate in
`agents-shipgate-reports/report.json`. Read `capability_review.top_changes[]`
next to see the highest-signal tool/action access changes, and check
`trust_root_touched`, `policy_weakened`, and `fix_task`.

Codex must not claim completion when `merge_verdict` is `blocked`,
`insufficient_evidence`, or `human_review_required` unless the user has
explicitly accepted the human-review requirement. Follow `fix_task` as the
repair boundary. When `first_next_action.actor` or `fix_task.actor` is `human` —
approval, confirmation, idempotency, broad-scope, prohibited-action,
acknowledgement, waiver, baseline, or policy decisions — Codex surfaces the item
for a person rather than resolving it.

And Codex must **never** weaken `shipgate.yaml`, the Shipgate CI workflow,
`AGENTS.md`, policy packs, baselines, waivers, or suppressions just to make
Shipgate pass — that edit is itself a trust-root change the gate will flag. See
[`../use-cases/ai-generated-agent-prs.md`](../use-cases/ai-generated-agent-prs.md)
for the full PR-verification walkthrough.

## What The Skill Covers

The Codex plugin is intentionally skill-only in v1. It loads a concise
`SKILL.md` first, then only reads references when needed:

- `references/recipes.md` — relevance, bootstrap, advisory CI, fixing,
  explaining, suppressing, strict promotion, and version upgrades.
- `references/report-reading.md` — release decision, `agent_summary`,
  `findings[].agent_action`, and the manual-review boundary.
- `assets/advisory-pr-comment.yml` — first-time GitHub Action template.

Codex must preserve the same safety boundary as every other agent:

- It may install, preview/detect, init, verify, scan, summarize, add advisory CI, apply
  high-confidence mechanical patches, and add `agents-shipgate-reports/` to
  `.gitignore`.
- It must not invent approval, confirmation, idempotency, broad-scope,
  prohibited-action, or runtime-trace evidence.
- It must not bypass the verifier by suppressing findings, lowering severity,
  expanding baselines or waivers, removing Shipgate CI, or weakening agent
  instructions. Verify-mode `SHIP-VERIFY-*` checks route those trust-root
  changes to human review.

For Claude Code, see [`use-with-claude-code.md`](use-with-claude-code.md).
For Cursor, see [`use-with-cursor.md`](use-with-cursor.md).
