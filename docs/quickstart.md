# Quickstart

One review, end to end, on a sample committed to this repository.

By the end you should be able to say four things about the change under
review, from the artifacts alone:

1. what capability it added, widened or removed;
2. why the top result matters;
3. what the run did **not** establish; and
4. what happens next, and who is allowed to do it.

The target is about 10 minutes. That is a target used to size this guide, not
a measured result — observed times are recorded in
[`design-partner-pilot-results.md`](design-partner-pilot-results.md).

Nothing here asks you to author a policy first. If you are evaluating a
coding-agent host boundary rather than building a tool surface, you never need
a manifest at all — see [Route H](#route-h--no-manifest).

## Which build you get

Read this before you install. Agents Shipgate is published through more than
one channel, and they do not all implement the same runtime contract. The
newest published release is **`v0.15.0`**, which implements **runtime contract
`10`**; the agent-control envelope that later sections of the in-repo
documentation describe landed after that tag.

| Channel | How you get it | Runtime contract | Has `control.*` / `current-control.json` | Accepts `check --format agent-boundary-json` | Qualification |
| --- | --- | --- | --- | --- | --- |
| Published release `v0.15.0` | `pipx install agents-shipgate` | 10 | **No.** `agent-handoff.json` is `shipgate.agent_handoff/v1`; read its `gate` block instead | **No.** That build accepts only `--format codex-boundary-json` | Qualified release |
| Unqualified preview | `gh release download preview-<version> --repo ThreeMoonsLab/agents-shipgate --pattern '*.whl'`, then `pip install ./<wheel>` | that of the source commit it was cut from | Yes | Yes | **None**, by construction — no adjudicated corpus, no qualification artifact, nothing signed. See [`release-evidence-policy-decision.md`](release-evidence-policy-decision.md) § Amendment 2 |
| Source checkout | `git clone`, then `./shipgate …` from the checkout | that of the checkout | Yes | Yes | Not a distributed build |

Pick one channel and stay on it for the whole walkthrough. **Everything in
[One review, end to end](#one-review-end-to-end) below runs on the published
release**; where a step needs a newer contract this page says so in the step
itself.

Whatever you install, ask it what it is before you trust a field name:

```bash
agents-shipgate --version
agents-shipgate contract --json
```

`contract --json` prints the `contract_version` the installed build actually
implements. Do not assume the floor this documentation was written against.

## Install

```bash
pipx install agents-shipgate
```

`pipx upgrade agents-shipgate` refreshes a stale copy — a plain install is a
no-op when an older build is already there. Alternatives:

```bash
python -m pip install agents-shipgate     # global pip
uv tool install agents-shipgate            # via uv
uvx agents-shipgate --help                 # one-shot via uv, no permanent install
python -m agents_shipgate --help           # run from a pip install without PATH
```

The CLI binary is `agents-shipgate`; a short alias `shipgate` is also
installed. Agents Shipgate requires Python 3.12 or newer. If your project uses
an older runtime, install the CLI with `pipx` or `uv` against a 3.12+
interpreter rather than into the project environment — your agent project does
not need Python 3.12 itself.

## Does this repository need Shipgate?

One fetch, no install, stdlib only:

```bash
curl -sSL https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/tools/shipgate-detect.py \
  | python3 - --workspace . --json
```

Continue when `is_agent_project: true`, or when `suggested_sources` or
`codex_plugin_candidates` is non-empty, or the workspace already has a
`shipgate.yaml`.

If `is_agent_project: false`, `suggested_sources: []`,
`codex_plugin_candidates: []`, **and `python_parse_truncated: false`**, this is
not the right tool for this repository. `python_parse_truncated: true` means the
Python parse stopped at its cap, so that negative describes the files that were
read rather than the repository — re-run with
`--max-python-files <workspace_signals.python_file_total>` before concluding
anything. [`zero-install.md`](zero-install.md) covers the `uvx` and GitHub
Action variants that also avoid a local install.

## One review, end to end

### 1. The change under review

[`samples/ai_generated_refund_pr`](../samples/ai_generated_refund_pr/) is a
committed two-commit history. The base is a support agent that can only search
a knowledge base. The head commit — the kind a coding agent opens — adds a
refund tool to the MCP export:

```diff
   { "name": "support.search_kb",
     "annotations": { "readOnlyHint": true, "idempotentHint": true },
     "auth": { "type": "oauth2", "scopes": ["support:kb:read"] } },
+  { "name": "stripe.create_refund",
+    "annotations": { "readOnlyHint": false, "destructiveHint": true },
+    "auth": { "type": "oauth2", "scopes": ["stripe:*"] } }
```

The manifest beside it still declares one scope, `support:kb:read`, and no
approval policy for the new tool.

### 2. Run it

```bash
agents-shipgate fixture run ai_generated_refund_pr
```

The fixture builds the base/head git history in a temporary directory and runs
`verify` across it. Nothing in your own repository is touched — not even a
report directory:

```text
Fixture: ai_generated_refund_pr
Mode: verify
Merge verdict: blocked
Decision: blocked
Can merge without human: false
Reports: /tmp/shipgate-fixture-ai_generated_refund_pr-<random>/ai_generated_refund_pr/reports
Verifier: …/reports/verifier.json
PR comment: …/reports/pr-comment.md
Static-verdict boundary: This verdict covers deterministic static evidence
only. Agents Shipgate did not execute the agent or prove runtime behavior,
tool routing, credential enforcement, or safety.
```

**The `Reports:` line names the directory it wrote**; every path below is
relative to it. `--out <absolute-path>` puts them somewhere you choose instead
— a relative `--out` resolves inside the fixture copy, not your shell's
directory.

### 3. What changed

Open `pr-comment.md` — the same text the GitHub Action posts on a pull
request. It leads with the capability delta, by subject:

```text
- Capability delta (analysed surface): 2 subjects across 6 changes (+1 added, 2 modified, -0 removed)
- Top capability changes by subject:
  - `stripe.create_refund`: added action — blocks release; added tool — blocks
    release; broadened action destructive — high-risk effect destructive added;
    blocks release; …
  - `support.search_kb`: broadened action (support:kb:read -> support:kb:read) —
    Capability binding_hash changed without a proven direction; review required
```

That is the answer to "what did this capability change": one tool added, one
existing tool whose binding moved. You did not have to read the identity model
to get it — the subject is the tool you would open.

### 4. Why the top result matters

`report.md` in that same directory orders findings by subject, most urgent
first:

```text
Decision: blocked
Reason: 4 active findings block release.

Blockers (4):
- CRITICAL SHIP-POLICY-APPROVAL-MISSING — stripe.create_refund lacks a declared approval policy
- CRITICAL SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING — stripe.create_refund has destructive capability without required controls
- CRITICAL SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING — stripe.create_refund has financial write capability without required controls
- CRITICAL SHIP-ACTION-WILDCARD-SCOPE — stripe.create_refund declares a broad action scope
```

The top blocker is not "a new tool appeared". It is that a tool which moves
real money can be called with no declared approval gate, under a `stripe:*`
scope the manifest never granted. Each finding names the evidence it rests on —
in this fixture, the MCP export at `tools.json#/tools/1`. Run
`agents-shipgate explain SHIP-POLICY-APPROVAL-MISSING` for the check's own
description, or see [`checks.md`](checks.md) for the catalog.

### 5. What the run did not establish

Every result carries its own coverage limit, and it is part of the answer:

```text
Evidence coverage: static (2/2 catalog tools reachable; 1 semantic review
concern(s); 2/2 actions pass-eligible; human review recommended)
```

and, on the PR comment:

```text
- Static-verdict boundary: This verdict covers deterministic static evidence
  only. Agents Shipgate did not execute the agent or prove runtime behavior,
  tool routing, credential enforcement, or safety.
```

Nothing here proves the refund tool behaves as described at runtime, that the
credential is really scoped, or that a human is really in the loop. It proves
what the declared and statically discoverable surface says, and it names the
one semantic concern it could not close.

When static extraction cannot enumerate a tool surface at all — a toolkit
factory, a config-bound allowlist, tools assembled at runtime — the release
decision is `insufficient_evidence` rather than a guess. That is the intended
failure mode. It routes to a person; it does not claim the agent is unsafe.

### 6. Exit zero is not merge permission

Ask the shell what the run returned:

```bash
agents-shipgate fixture run ai_generated_refund_pr > /dev/null; echo $?
```

```text
0
```

`report.md` says why:

```text
Fail policy: ci_mode=advisory, fail_on=[none], new_findings_only=false, would_fail_ci=false (exit 0)
```

Advisory mode never fails the job. The verdict is `blocked` anyway. **Exit
status is a CI policy choice; the verdict is the answer.** A gate wired to the
process exit code, in advisory mode, gates on nothing. [Advisory
CI](#advisory-ci) below is where you make the verdict load-bearing.

### 7. The next action, and who owns it

`verifier.json` carries `fix_task`. Four of its five instructions, and the
`allowed_repairs[]` array beside them, are elided here:

```json
{
  "actor": "human",
  "safe_to_attempt": false,
  "instructions": [
    "4 active findings block release.",
    "Declare an approval policy for stripe.create_refund or remove this tool from the release.",
    "Declare approval.required, confirmation policy, and safeguards.rollback for this destructive action.",
    "Declare approval.required, safeguards.audit_log, and safeguards.idempotency for this financial write action."
  ]
}
```

`actor: "human"` is the operative field, and `safe_to_attempt: false` says the
same thing to a machine. Every instruction is a declaration about approval,
control or safety — a claim only a person can make. A coding agent may report
this, and may not resolve it.

On a build that implements contract `21` or newer, the same answer arrives as
an explicit state machine. Read `current-control.json`
first — it names which run is current and refuses the read when HEAD or the
working tree has moved since the decision — then `agent-handoff.json`
(`control.state`, then `gate.merge_verdict`). Here `control.state` is
`human_review_required` and every entry in `control.permissions` is `false`.
On the published `v0.15.0` build those fields do not exist; read
`agent-handoff.json`'s `gate` block and `report.json`'s
`release_decision.decision` instead. See
[Which build you get](#which-build-you-get).

**Where a human-review route still lets an agent work.** Contract v20 separates
two states. `human_review_required` is a full stop. `review_publishable` means
the agent may still commit, push and update the pull request so the review can
happen — merge and completion stay denied. Updating a pull request is not
merging it.

**There is no in-repo approval mechanism.** A comment, a `human_ack` added by
the same change, or an acknowledgement in conversation does not clear a
human-review route. A trusted coding host can sign an external authorization
for one exact command; that protocol is described in
[`agent-contract-current.md`](agent-contract-current.md#trusted-human-authorization-for-one-exact-command),
Agents Shipgate ships no signing or approval command, and it does not turn the
verdict into a pass.

## Two kinds of fix

Findings split cleanly, and the split is in the artifact rather than in your
judgement.

### Mechanical — an agent may do it

Reversible, containment-checked, and safe to run without further approval:

```bash
agents-shipgate apply-patches \
  --from agents-shipgate-reports/report.json \
  --confidence high --apply
```

At `--confidence high` this fires on exactly three stale-manifest removals
(`SHIP-MANIFEST-STALE-{SUPPRESSION,POLICY,RISK-OVERRIDE}`) and refuses to
mutate anything outside the manifest's directory. It is dry-run by default.
Scope-coverage appends require an explicit `--confidence medium` and a reviewer.
Adding advisory CI and adding `agents-shipgate-reports/` to `.gitignore` are
mechanical too.

### Human-owned — declarations

An agent must never supply, infer or auto-assert an action's **effect**, its
**authority**, its **bindings**, the agent's **purpose**, or an approval,
confirmation, idempotency, broad-scope, or prohibited-action claim — including
by reading them out of a prompt, a README, or a docstring. Prose is not
evidence of authority. `agent.declared_purpose` and every value like it must be
supplied by a human, because Shipgate never invents a declaration nobody made;
`init --json` and `fix_task` mark each one with `actor: "human"`, naming the
fields and lines. The counterpart matters as much: `agent.name` and the
`tool_sources[]` rows are facts in the repository, and an agent that escalates
those to a person has stopped a turn for nothing.

[`agent-autofix-boundary.md`](agent-autofix-boundary.md) is the full boundary,
with the check-ID mapping and the exact phrasing an agent should hand to a
reviewer. [`autofix-policy.md`](autofix-policy.md) is the mechanical filter
behind `apply-patches`.

## Run it on your own repository

Two routes. Pick by what the repository is, not by which looks lighter.

### Route H — no manifest

For any repository that declares what its **coding agents** may do: `.mcp.json`,
`.claude/settings.json`, `.codex/`, `.cursor/`, hooks, workflow scopes. There is
no manifest and no policy authoring.

```bash
shipgate audit --host --json --out agents-shipgate-reports/host-grants.json
```

The default audit is deterministic `repository` scope. Use `--scope
local-static` only when you explicitly want supported user and file-based
managed configuration included. Both modes publish per-host coverage and
excluded sources; neither proves session or runtime authority. Inspect
`host_coverage`, `issues` and `excluded_scopes` before relying on the result,
and see the [static host-boundary support matrix](host-boundary-support.md).

Record a baseline once, on the default branch, then check drift per change:

```bash
shipgate audit --host --save-baseline      # then commit .agents-shipgate/
shipgate audit --host --drift --json --out shipgate-drift.json
```

**Never `--save-baseline` from the changed checkout to get past a missing
baseline.** That acknowledges the very expansion under review, and the drift
then reports nothing.

Before a coding agent reports an agent-capability change complete, it runs the
local boundary check:

```bash
shipgate check --agent codex --workspace . --format agent-boundary-json
shipgate check --agent claude-code --workspace . --format agent-boundary-json
shipgate check --agent cursor --workspace . --format agent-boundary-json
```

The `--agent` value is caller identity, not a coverage selector: every
recognized changed host boundary is evaluated on every invocation. Parse the
stdout `shipgate.agent_boundary_result/v2` object, switch on `control.state`,
and follow `control.next_action`, `control.allowed_next_commands` and
`control.human_review`. Treat `decision` as diagnostic context, never as the
control signal, and never infer control from prose. `check` is necessary but
not sufficient for a capability-expanding diff: if a change adds dynamic,
undeclared or otherwise ambiguous tool capability, do not read
`decision="allow"` as merge readiness — run `verify` and read
`release_decision.decision`. On the published `v0.15.0` build this command
accepts only `--format codex-boundary-json` and emits no `control` block; see
[Which build you get](#which-build-you-get).

### Route A — a repository that builds a tool surface

For repositories that define and declare a tool surface in-repo: MCP or OpenAPI
exports, framework tool definitions, a Codex plugin package.

```bash
agents-shipgate verify --preview --json                  # what would be read, before anything is written
agents-shipgate detect --json                            # classify the workspace
agents-shipgate init --write --ci --json                 # manifest + advisory workflow
```

`init --write --ci` produces a schema-valid `shipgate.yaml` with
framework-specific `tool_sources` populated, plus a GitHub Actions workflow.
Then verify the change:

```bash
# local, uncommitted work — omit --base/--head so working-tree edits are scanned
agents-shipgate verify --workspace . --config shipgate.yaml \
  --ci-mode advisory --format json

# committed PR/CI refs — make the base ref available first; verify never fetches
agents-shipgate verify --workspace . --config shipgate.yaml \
  --ci-mode advisory --format json --base origin/main --head HEAD
```

The short `shipgate verify` alias remains invokable for compatibility;
agent-facing PR-gate guidance uses `agents-shipgate verify`.

**`init` reports what it could not infer, and who owns each one.** `init
--write --json` returns a `placeholders[]` array, and each entry carries its
owner — follow that, rather than a list in any document. The split is real in
both directions:

- **A coding agent owns** the facts it can read out of the repository:
  `agent.name`, `project.name`, and the `tool_sources[]` rows. Sending these to
  a person stops a turn for work the agent owns.
- **A person owns** every declaration: purpose, prohibited actions, effect,
  authority, binding, approval, confirmation, idempotency, safeguards, accepted
  debt and its owner/reason/expiry, and the blocks that are declarations end to
  end (`action_surface`, `permissions`, `policies`, `agent_bindings`,
  `tool_identity`, `checks`, `baseline`, `human_ack`, `risk_overrides`,
  `validation`, `organization`). While one of these is unresolved, `init`
  returns `control.next_action.actor: "human"` and `permissions.edit: false`,
  because these values must be supplied by a human — Shipgate never invents a
  declaration nobody made.

Those names illustrate the rule; the `owner` on each `placeholders[]` entry is
what decides it.

Do not let a coding agent fill a human-owned value from a prompt, the main
agent file or the repository README. A purpose or authority claim lifted out of
prose is not a guess to be corrected later; the engine will treat it as
evidence, which is exactly the gap this tool exists to catch.

**When discovery reads no tool surface at all.** `init --json` reports
`tool_surface_origin`: `"detected"` when every source in the manifest was read
out of the workspace, `"scaffold"` when none was, and `null` when this run's
render reached neither disk nor the payload. On `"scaffold"` the `tool_sources`
block is a placeholder — `id`, `type` and `path` are all `CHANGE_ME`, all three
are in `placeholders[]`, and `manifest_message` says nothing was inferred. That
is the common outcome for an MCP server whose tools are registered in code
rather than exported to a file. **A scaffolded `tool_sources` block means this
repository is on Route H**: record the dead end and switch, rather than
inventing a manifest to fill.

### If the first real repository stalls

| Symptom | Next action |
| --- | --- |
| `detect` says `is_agent_project: false`, but `suggested_sources` includes MCP or OpenAPI files | Proceed to `init`. MCP/OpenAPI-only repos are valid tool-surface targets even without Python framework detection. |
| `detect` says `is_agent_project: false`, but `codex_plugin_candidates` is non-empty | Proceed to `init`. Codex plugin repos are valid static plugin-surface targets. |
| `doctor` shows zero tools | Check `tool_sources[].path`, MCP `tools[]`, OpenAPI `paths`, optional source warnings, and dynamic ADK/MCP toolsets. |
| Tools are created by factories, wrappers, or dynamic toolsets | Provide an explicit MCP export, OpenAPI spec, local tool inventory artifact, or a broader OpenAI SDK source directory when tools are static but split across files. |
| `init --write --json` returns `placeholders[]` entries | Read each entry's owner. Agent-owned ones the agent fills from the repository; human-owned ones go to a person. See [Route A](#route-a--a-repository-that-builds-a-tool-surface). |
| Install fails in a Python 3.10/3.11 project | Install the CLI outside the project env with `pipx` or `uv` using Python 3.12+. |
| Reports appear in `git status` | Add `agents-shipgate-reports/` to `.gitignore`; reports are local release-review artifacts. |

### Choose your first source

Point the manifest at the clearest tool boundary you already have:

| Source | Use when | Manifest path |
| --- | --- | --- |
| OpenAI Agents SDK Python | Tools are defined with `@function_tool` in local Python files. | `tool_sources[].type: openai_agents_sdk`; `path` may be one Python file or a directory |
| MCP export | You can export the MCP server's tool list to JSON. | `tool_sources[].type: mcp` |
| OpenAPI spec | The agent calls HTTP APIs described by OpenAPI 3.x. | `tool_sources[].type: openapi` |
| Codex plugin package | The repo contains `.codex-plugin/plugin.json` or `.agents/plugins/marketplace.json`. | `tool_sources[].type: codex_plugin` |

Google ADK, LangChain/LangGraph, CrewAI, n8n workflow JSON, Conductor OSS
workflow JSON, Anthropic Messages API artifacts, and simple OpenAI API
artifacts are supported inputs too. Start with the tool surface closest to the
release boundary. Framework-by-framework minimal manifests are in
[`minimal-real-configs.md`](minimal-real-configs.md); agent-driven recipes are
in [`agent-recipes.md`](agent-recipes.md).

## Verdict reference

The release gate is `report.json` → `release_decision.decision`:

| Decision | Meaning | Next action |
| --- | --- | --- |
| `blocked` | Active, unaccepted blockers exist. | Fix blockers or remove the risky tool surface. |
| `insufficient_evidence` | The scan cannot confidently gate release from the available static evidence; this does not prove the agent is unsafe. | Follow the first structured evidence-gap action. Supported frameworks name the generated local inventory and exact manifest route; unidentified source shapes receive the generic source guidance. Then rerun. |
| `review_required` | Human review is needed for accepted debt or evidence gaps below the blocked threshold. | Review the listed items before promotion. |
| `passed` | No active blocker or review signal was found. | Keep the report artifact with the PR/release record. |

`merge_verdict` is the PR-facing projection of that decision, on `verifier.json`
and `agent-handoff.json`:

| Merge verdict | Meaning | Next action |
| --- | --- | --- |
| `blocked` | Active, unaccepted blockers exist. | Fix blockers or remove the risky capability. |
| `insufficient_evidence` | Static evidence is too weak to gate release confidently. | Add better sources and rerun; do not auto-merge. |
| `human_review_required` | A person must review accepted debt, trust-root changes, or authority-bearing gaps. | Surface the required review; a coding agent must not self-approve it. |
| `mergeable` | No active blocker or review signal was found. | Keep verifier/report artifacts with the PR record. |
| `unknown` | Verify could not produce a reliable head scan or diff context. | Fix the setup, fetch the base ref, or rerun with usable inputs. |

Common review signals: missing confirmation, missing idempotency evidence,
broad-scope permissions, prohibited-action policy gaps, and trust-root changes
such as weakened CI or manifest policy.

## The second use

The first run is setup. The second is the one that tells you whether this is
worth keeping.

### The next capability-changing PR

Nothing from the first run has to be redone. On the next change that touches
tools, prompts, scopes, policy or the gate itself, run the same command with
the PR's refs:

```bash
agents-shipgate verify --workspace . --config shipgate.yaml \
  --ci-mode advisory --format json --base origin/main --head HEAD
```

On Route H, the same shape: `shipgate audit --host --drift` against the
baseline you already committed, then `shipgate check`. Not every PR needs a
run — [`triggers.json`](triggers.json) is the machine-readable rule set for
deciding, and `verify --preview --json` answers it for one workspace.

### Advisory CI

Drop this into `.github/workflows/agents-shipgate.yml`. It runs on every PR,
posts the verdict as a comment, uploads artifacts, and never fails the job:

```yaml
name: Agents Shipgate

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write

jobs:
  agents-shipgate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - id: shipgate
        uses: ThreeMoonsLab/agents-shipgate@v0.15.0
        with:
          config: shipgate.yaml
          ci_mode: advisory
          diff_base: target
          pr_comment: "true"
          shipgate_version: "0.15.0"
```

The action delegates to `verify` and never fetches — keep `fetch-depth: 0`.
Advisory mode is where you start, not where you stop: as § 6 showed, an
advisory job exits zero on a `blocked` verdict. Make the verdict load-bearing
with an explicit policy step:

```yaml
- name: Block only blocked verdicts
  if: steps.shipgate.outputs.merge_verdict == 'blocked'
  run: exit 1
```

```yaml
- name: Require no human authority gap
  if: steps.shipgate.outputs.can_merge_without_human != 'true'
  run: exit 1
```

GitLab, CircleCI, Jenkins and pre-commit equivalents, the full output catalog,
and the other merge policies are in [`integrations.md`](integrations.md) and
[`examples/github-actions/`](../examples/github-actions/).

### Baseline and strict mode

Strict mode exits `20` on unsuppressed critical findings. On an existing
project, save a baseline first so strict CI fails only on *new* findings:

```bash
agents-shipgate baseline save --config shipgate.yaml --out .agents-shipgate/baseline.json
agents-shipgate scan --config shipgate.yaml --baseline .agents-shipgate/baseline.json --ci-mode strict
```

[`baseline.md`](baseline.md) covers baseline lifecycle, suppression hygiene and
noise triage.

## Temporary external repository review

Assessing someone else's repository without adopting Shipgate into it is an
explicit opt-in. Point preview at the reserved manifest so it emits the
local-review setup route instead of the durable `init --write` route:

```bash
agents-shipgate verify --preview --workspace . \
  --config .agents-shipgate-local-review.yaml --json
agents-shipgate init --workspace . --local-review --json
agents-shipgate verify --workspace . \
  --config .agents-shipgate-local-review.yaml \
  --base origin/main --head HEAD --json
```

`--local-review` writes an ephemeral manifest at the workspace root so its
relative source paths still resolve, and privately excludes that file plus
`agents-shipgate-reports/` through `.git/info/exclude`. Tracked project files
stay untouched and generated reports stay out of `git status`. The JSON result
enumerates every effect plus an executable cleanup command;
`init --local-review --undo --json` removes those local setup effects while
preserving reports.

Verification marks the reserved manifest as `local_review` and always keeps the
result provisional. The same fail-safe applies to any differently named
uncommitted or Git-unproven manifest: `passed`, `mergeable`, and
human-authorization evidence all require a manifest that Git proves is present
in the evaluated repository tree. Git presence alone does not prove human
review. Durable adoption remains `init --write`.

## Export feedback

For a design-partner review or a false-positive report, export the small
redacted feedback artifact:

```bash
agents-shipgate feedback export \
  --from agents-shipgate-reports/verifier.json \
  --redact \
  --out shipgate-feedback.json
```

The export includes the merge verdict, top capability changes, finding IDs,
next action, `fix_task`, and reviewer prompts. It does not include raw finding
evidence.

## Next

- [`agent-recipes.md`](agent-recipes.md) — copy-pasteable AI-agent workflows for verify-first PRs and first adoption
- [`agent-autofix-boundary.md`](agent-autofix-boundary.md) — what an agent may fix, and what it must never assert
- [`minimal-real-configs.md`](minimal-real-configs.md) — framework-by-framework minimal manifest references
- [`manifest-v0.1.md`](manifest-v0.1.md) — manifest schema in prose form
- [`checks.md`](checks.md) — what the scanner looks for
- [`mental-model.md`](mental-model.md) — the five-minute version of the decision model
- [`category.md`](category.md) — what an "agent release gate" is, and what it is not
- [`faq.md`](faq.md) — common questions
- [`troubleshooting.md`](troubleshooting.md) — when a run does not do what you expected
- [`glossary.md`](glossary.md) — category vocabulary
