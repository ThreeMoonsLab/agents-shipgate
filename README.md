<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/readme-header-dark.png">
    <img src="assets/readme-header.png" alt="Agents Shipgate · the deterministic merge gate for AI-generated agent capability changes" width="100%">
  </picture>
</p>

# Agents Shipgate

[![PyPI](https://img.shields.io/pypi/v/agents-shipgate)](https://pypi.org/project/agents-shipgate/)
[![Python](https://img.shields.io/pypi/pyversions/agents-shipgate)](https://pypi.org/project/agents-shipgate/)
[![GitHub Action](https://img.shields.io/badge/GitHub%20Action-marketplace-blue)](https://github.com/marketplace/actions/agents-shipgate)
[![License](https://img.shields.io/pypi/l/agents-shipgate)](LICENSE)
[![CI](https://github.com/ThreeMoonsLab/agents-shipgate/actions/workflows/ci.yml/badge.svg)](https://github.com/ThreeMoonsLab/agents-shipgate/actions/workflows/ci.yml)

**Your coding agent changed what your AI agent can do — Agents Shipgate tells you whether it can merge.**

**The deterministic merge gate for AI-generated agent capability changes.**

Local-first and static by default — no agent execution, tool calls, LLM calls, or network access.

<!-- Canonical tagline: The deterministic merge gate for AI-generated agent capability changes. -->

> [!IMPORTANT]
> **Status: pre-1.0 (beta).** The decision engine is deterministic and stable.
> This source tree is `0.16.0b7`; install and GitHub Action examples remain
> pinned to the latest published tag, `v0.15.0`, until the beta is released.
> Real-history accuracy numbers (small n, published in full in
> [`benchmark/miner/README.md`](benchmark/miner/README.md)): across 361 mined
> rows from **8 distinct** real agent repos, 336 (93%) organically skip the
> trigger; of the **19 unique labeled engine-engaged PRs**, re-run on the
> released **v0.15.0** engine (`2026-W27-reeval`), the gate **never auto-passed
> an unsafe change** — both real `must_block` PRs and every `needs_human` PR are
> held for human review (`must_block_caught` / `needs_human_caught` = 1.0). **But
> it routes to review, it does not block:** real-history `blocked_recall` is
> still **0.0** — both `must_block` PRs return `human_review_required` /
> `review_required`, not `blocked`. v0.15.0 cleared the 4 scan crashes and moved
> both `must_block` PRs off abstention (`insufficient_evidence` → review), at the
> cost of escalating 4 of 14 safe PRs to review (`benign_escalation_rate` 0.286 —
> largely a cold-start whole-repo-surface measurement artifact on large repos).
> Reliable blocked-recall (1.0) and zero benign escalation are proven on the
> constructed-adversarial stratum, not yet on real history. Labels are
> AI-adjudicated (0 disagreement), pending human spot-check. Treat it as an
> advisory gate while this work closes — see [ROADMAP.md](ROADMAP.md).

## 60 seconds: watch it block two PRs

Claude Code adds `stripe.create_refund` to your support agent and opens a
PR. The diff looks fine to a human skimming it. Should it merge?

```bash
uvx agents-shipgate fixture run ai_generated_refund_pr
```

→ `merge_verdict: blocked` — the new refund capability has no declared
approval policy and no idempotency evidence. The verifier explains both
blockers and routes the PR to a human.

Now the move every reviewer fears — the agent deletes the Shipgate CI
gate to make its PR pass:

```bash
uvx agents-shipgate fixture run agent_weakens_gate
```

→ `merge_verdict: blocked`, `can_merge_without_human: false`. The
gate-removal checks are suppression-immune: the cheapest reward-hack is
also the most visible one.

**…and here's the failure mode.** These two cases are constructed fixtures with
a clear-cut answer, chosen to show the gate working. Real PRs are messier: when
a change builds its tool surface dynamically — a toolkit factory, a config-bound
allowlist, tools assembled at runtime — static extraction often can't enumerate
the result, and Shipgate returns `insufficient_evidence` and routes to a human
rather than emit a confident wrong verdict. That is the intended failure mode,
not a bug; reducing how often it fires on real dynamic code is active work (see
[ROADMAP.md](ROADMAP.md)).

One engine decides (`report.json.release_decision.decision`); everything
else — `merge_verdict`, PR comments, Check Runs, Action outputs — is a
deterministic projection of it. Five-minute version:
[`docs/mental-model.md`](docs/mental-model.md).

Agents Shipgate is an open-source CLI and GitHub Action for local-first,
static Tool-Use Readiness review. It scans MCP, OpenAPI, OpenAI Agents SDK,
Anthropic Messages API, Google ADK, LangChain/LangGraph, CrewAI, OpenAI API,
Codex repo config, Codex plugin, n8n, and Conductor OSS workflow artifacts, then writes a deterministic **Tool-Use
Readiness Report** before your agent gets production-like permissions.

Within agent release readiness, Agents Shipgate's wedge is Tool-Use
Readiness: the tool surface, schemas, scopes, approval policies, idempotency,
and blast radius reviewed at PR time.

**Website:** [threemoonslab.com](https://threemoonslab.com/) —
[quickstart](https://threemoonslab.com/quickstart/),
[glossary](https://threemoonslab.com/glossary/),
[check catalog](https://threemoonslab.com/checks/), and
[design partners](https://threemoonslab.com/design-partners/).

Static-by-default — no agent execution, no LLM calls, no MCP server connections,
no scanner network calls, no scanner telemetry. Audited exceptions are pinned
in [`tests/test_adapter_static_only.py::ALLOWED_EXCEPTIONS`](tests/test_adapter_static_only.py).
Apache-2.0.

## What your PR sees

When a PR changes what your agent can do, the GitHub Action posts the merge
verdict as a PR comment. This is the comment for the first demo PR above —
the coding-agent diff that adds `stripe.create_refund` to a support agent
(abridged from the verbatim `pr-comment.md` artifact):

> ### Agents Shipgate result: block
>
> Decision: `block` · Risk: `critical` · Required reviewers: `agent-platform`, `security`
>
> | Impact | Change | Subject | Why |
> |---|---|---|---|
> | blocks release | action added | `stripe.create_refund` | Capability added. |
> | blocks release | action broadened | `stripe.create_refund` | high-risk effect financial_action added |
> | blocks release | scope broadened | `stripe.create_refund:stripe:*` | scope added |
>
> **Required before merge** — Actor: Human (human authority required — a coding
> agent must not self-resolve):
> 1. Declare an approval policy for `stripe.create_refund` or remove this tool
>    from the release.
> 2. Declare `approval.required`, `safeguards.audit_log`, and
>    `safeguards.idempotency` for this financial write action.
> 3. Replace wildcard/admin scopes with operation-specific scopes.
>
> Then re-verify: `agents-shipgate verify --base origin/main --head HEAD --json`

The same `uvx agents-shipgate fixture run ai_generated_refund_pr` command
above writes this comment verbatim to `reports/pr-comment.md`.

## Verify-first quickstart

Install once:

```bash
pipx install agents-shipgate
```

Then start from one of three prominent flows.

### Local Boundary Check

Coding agents run `shipgate check` before reporting an agent-capability change
complete. Parse the stdout `shipgate.agent_boundary_result/v1` object. The
`--agent` value identifies the caller; every recognized changed host surface is
evaluated regardless of that value:

```bash
shipgate check --agent codex --workspace . --format agent-boundary-json
shipgate check --agent claude-code --workspace . --format agent-boundary-json
shipgate check --agent cursor --workspace . --format agent-boundary-json
```

Switch on `control.state`; follow `control.next_action`,
`control.allowed_next_commands`, and `control.human_review`. Treat `decision`
as diagnostic context, not as the operational control signal, and never infer
control from prose. `shipgate check` is necessary but not sufficient for
capability-expanding diffs: if a change adds dynamic, undeclared, or otherwise
ambiguous tool capability, do not treat `decision="allow"` as merge readiness;
run `agents-shipgate verify` and read `release_decision.decision`.

### PR And Local Verification

When a PR changes what your agent can do, run the deterministic verifier on the
diff and read its merge verdict before you merge. For committed PR/CI refs,
make the base ref available first because `verify` never fetches:

```bash
agents-shipgate verify --workspace . --config shipgate.yaml \
  --ci-mode advisory --format json --base origin/main --head HEAD
```

For local, uncommitted work, omit `--base`/`--head`. Verify scans the worktree
only after it safely selects a remote default or proves HEAD is already at the
default commit:

```bash
agents-shipgate verify --workspace . --config shipgate.yaml \
  --ci-mode advisory --format json
```

If that committed comparison scope cannot be proved, verify exits 2 before
the head scan and returns nonterminal control: fetch missing remote history or
ask a human to select `--base <ref>`. Use `--no-base` only when a person or
calling workflow intentionally chooses head/worktree-only verification; a
coding agent must not add it merely to clear the failure.

If a repo is not configured yet, use the verify flow's preview entry point:

```bash
agents-shipgate verify --preview --json
```

The short `shipgate verify` alias remains invokable for compatibility, but
agent-facing PR-gate guidance uses `agents-shipgate verify`.

### Host-Grant Audit

Before changing local MCP servers, Codex/Claude/Cursor permission rules,
hooks, workflow scopes, or other host grants, capture the host inventory:

```bash
shipgate audit --host --json --out agents-shipgate-reports/host-grants.json
```

The default audit is deterministic `repository` scope. Use
`--scope local-static` only when you explicitly want supported user and
file-based managed configuration included. Both modes publish per-host
coverage and excluded sources; neither proves session or runtime authority.
See the [static host-boundary support matrix](docs/host-boundary-support.md).

The release gate is `agents-shipgate-reports/report.json` →
`release_decision.decision` (`blocked | review_required | insufficient_evidence | passed`).
The PR/control surface is `agents-shipgate-reports/verifier.json` →
`merge_verdict` (`mergeable | human_review_required | insufficient_evidence |
blocked | unknown`), a deterministic projection of the release decision.
Validate `verification-receipt.json` first; then read `agent-handoff.json`
(`control.state`, then `gate.merge_verdict`), followed by the
authoritative control substrate `verifier.json` for `control`, `merge_verdict`,
`applicability`, `can_merge_without_human`,
`control.next_action`, and `fix_task`. `capability_review.top_changes` is
supporting/provisional reviewer context.

Zero-setup demos of both verdicts are in
[60 seconds](#60-seconds-watch-it-block-two-prs) above; `uvx` runs them with no
persistent install. To upgrade the CLI, use `pipx upgrade agents-shipgate` - a
plain install is a no-op over a stale build. Your agent project does **not**
need Python 3.12; the CLI installs
separately. To verify your own repo and write the standard
`agents-shipgate-reports/` directory, see [Verify your repo](#verify-your-repo)
below.

![Sample Tool-Use Readiness Report showing 2 critical, 14 high, and 2 medium findings on the support_refund_agent fixture, including a missing approval policy on stripe.create_refund.](assets/sample-report.png)

## How to read your first result

For PR verification, validate `verification-receipt.json`, then read
`agent-handoff.json.gate.merge_verdict`:

| Merge verdict | Meaning | Next step |
|---|---|---|
| `blocked` | Active, unaccepted blockers exist. | Fix blockers or remove the risky capability. |
| `insufficient_evidence` | Static evidence is too weak to gate release confidently. | Add better sources and rerun; do not auto-merge. |
| `human_review_required` | A person must review accepted debt, trust-root changes, or authority-bearing gaps. | Surface the required review; a coding agent must not self-approve it. |
| `mergeable` | No active blocker or review signal was found. | Keep verifier/report artifacts with the PR record. |
| `unknown` | Verify could not produce a reliable head scan or diff context. | Fix setup, fetch the base ref, or rerun with usable inputs. |

Then read `report.json.release_decision.decision`, the source-of-truth gate:

| Decision | Meaning | Next step |
|---|---|---|
| `blocked` | Active, unaccepted blockers exist. | Fix the blockers or remove the risky tool surface. |
| `insufficient_evidence` | The scan cannot confidently gate release from the available static evidence. This does not prove the agent is unsafe. | Provide clearer sources such as an MCP export, OpenAPI spec, explicit local tool inventory, or broader OpenAI SDK source path, then rerun. |
| `review_required` | Human review is needed, often for accepted debt or evidence gaps below the blocked threshold. | Review the listed items before promotion. |
| `passed` | No active blocker or review signal was found. | Keep the report artifact with the PR/release record. |

Common review signals include missing confirmation, missing idempotency
evidence, broad-scope permissions, prohibited-action policy gaps, and
trust-root changes such as weakened CI or manifest policy.

### Authorize one exact coding-agent action

A `human_review_required` result normally stops the coding agent. Runtime
contract v18 adds one narrow continuation path for a trusted host: after a
person reviews a host-attested receipt and its complete review set, the host can
sign an exact authorization request with Ed25519. The private key, human
authentication, and signing operation stay outside Agents Shipgate and outside
the evaluated workspace; Agents Shipgate ships no signing or approval command.
On POSIX, the verifier reads the trust policy only from the OS account home's
fixed path
`~/.config/agents-shipgate/human-authorization-trust-policy.json`. Changing
`HOME` or `XDG_CONFIG_HOME` does not redirect that lookup. The fixed lookup
prevents environment-based path substitution; the coding host must still make
the file and its parent path unwritable by the agent. The guarded executor is
POSIX-only in v1; this authorization route remains disabled on Windows.
Authorization-eligible verification must also bind an effective
`plugins_enabled=false` engine. Use `--no-plugins` for both verification
passes when the environment enables third-party plugins; the protected broker
never imports third-party check or adapter code.

First build the unsigned challenge from the validated receipt. It derives the
reviewed source commit and complete review set; you supply the exact destination
and the remote OID that the reviewer observed:

```bash
agents-shipgate authorization request \
  --receipt agents-shipgate-reports/verification-receipt.json \
  --artifacts-root agents-shipgate-reports \
  --remote origin \
  --destination-ref refs/heads/<branch> \
  --expected-lease-oid <reviewed-remote-oid> \
  --out agents-shipgate-reports/human-authorization-request.json
```

That command does not approve or sign anything. The external host must present
the complete request to the authenticated human and produce the signed grant.
The request binds the source `receipt_id`, `artifact_set_id`, engine, and
executor so the signer can require a trusted-CI attestation or rerun
verification itself. Content addressing detects changed bytes; it does **not**
authenticate an agent-supplied receipt. A signer must never treat a
self-consistent receipt from the coding agent as trusted provenance.
The request also exposes the evaluated base commit and merge base. Because a
Git commit transitively binds every parent and reachable object, the signer
must inspect the complete source ancestry—not only the final tree diff—before
authorizing its push. The guarded executor snapshots that full graph with a
512 MiB pack ceiling and a 120-second process timeout; the host broker should
apply tighter disk, memory, and CPU quotas for its deployment. The compressed
pack ceiling does not bound expanded-object indexing memory or CPU, so a
production broker should use a cgroup, container, or equivalent host quota.

Rerun verification with the externally produced grant:

```bash
agents-shipgate verify --workspace . --config shipgate.yaml \
  --base origin/main --head HEAD --ci-mode advisory --format json \
  --no-plugins \
  --authorization /path/outside/workspace/human-authorization.json
```

The verifier recomputes the request, subject, decision, trees, and full review
set, then checks the signature, trusted principal, repository scope, and TTL.
The v1 grant can authorize only one exact Git push. The source must be the
exact commit whose tree was verified; a synthetic PR merge receipt cannot
authorize pushing a different parent commit. The operation binds a canonical,
credential-free HTTPS destination, one full destination ref, and an explicit
expected remote OID in `--force-with-lease`. If accepted, `control.state`
becomes `agent_action_required` and exposes only a guarded
`agents-shipgate authorization execute` command. That consumer validates the
receipt from one immutable byte snapshot, grant, current trust policy,
wall-clock expiry, engine, repository, and commit again immediately before the
push. It copies the reachable Git graph into an isolated object store, checks
its object IDs with `git fsck`, disables hooks, configuration, replacement
objects, and HTTP redirects, and then pushes only that snapshot. The raw
`git push` is never an
allowed command. This does **not** turn
the PR into a pass: `release_decision.decision` stays `review_required`,
`merge_verdict` stays `human_review_required`, and both
`can_merge_without_human` and `completion_allowed` stay false. Invalid,
expired, stale, or mismatched grants fail closed with zero commands. If the remote ref
moves after approval, Git rejects the push because the signed command carries
the previously reviewed lease OID.

GitHub's default `pull_request` checkout evaluates a synthetic merge commit;
request push authorization only after a separate committed verification whose
`--head` is the actual PR head SHA. This release defines the signed protocol,
verifier consumer, and guarded executor. A Codex,
Claude Code, or other coding-host signing adapter must still authenticate the
human, protect the key from the agent, attest or rerun the source verification,
and produce the grant; no current UI integration is implied.

Authorization is enabled only when the coding host enforces a real write
boundary around the trust policy, signing key, clean launcher environment,
Python interpreter, entire virtual environment and `site-packages` tree
(including startup `.pth` files), dependencies, credentials, and separately
installed Agents Shipgate distribution.
Filesystem ownership/mode checks alone cannot distinguish an unrestricted
same-UID coding agent from its human user. Editable installs whose package code
resolves inside the authorized repository are therefore ineligible. If the
host cannot enforce these boundaries, it must leave the result at
`human_review_required` and expose no authorization command. Authorization v1
is intentionally push-only; reviewed patch application is a later operation
type, not an implied capability of this grant.

## Not sure if Shipgate applies?

Run the zero-install detector from the repo you are reviewing. It is a
stdlib-only first touch for engineers and coding agents that need a yes/no
relevance signal before installing anything:

```bash
curl -sSL https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/tools/shipgate-detect.py \
  | python3 - --workspace . --json
```

Continue to [Verify your repo](#verify-your-repo) when the output has
`is_agent_project: true`, non-empty `suggested_sources`, non-empty
`codex_plugin_candidates`, or the workspace already has `shipgate.yaml`.

## Sample reports

Open a report first if you want to see the output shape before installing:

| Sample | Markdown | JSON |
|---|---|---|
| `support_refund_agent` | [`report.md`](samples/support_refund_agent/expected/report.md) | [`report.json`](samples/support_refund_agent/expected/report.json) |
| `simple_openai_api_agent` | [`report.md`](samples/simple_openai_api_agent/expected/report.md) | [`report.json`](samples/simple_openai_api_agent/expected/report.json) |
| `simple_langchain_agent` | [`report.md`](samples/simple_langchain_agent/expected/report.md) | [`report.json`](samples/simple_langchain_agent/expected/report.json) |

The `support_refund_agent` fixture also includes a reviewer-shaped Release
Evidence Packet in [`packet.md`](samples/support_refund_agent/expected/packet.md),
[`packet.json`](samples/support_refund_agent/expected/packet.json), and
[`packet.html`](samples/support_refund_agent/expected/packet.html).

## Copy this into your coding agent

```text
Add a Tool-Use Readiness release gate for this tool-using AI agent with Agents Shipgate.
Use only the prominent Shipgate flows as first-look commands:
shipgate check --agent codex --workspace . --format agent-boundary-json
shipgate check --agent claude-code --workspace . --format agent-boundary-json
shipgate check --agent cursor --workspace . --format agent-boundary-json
agents-shipgate verify --workspace . --config shipgate.yaml --ci-mode advisory --format json
agents-shipgate verify --workspace . --config shipgate.yaml \
  --base origin/main --head HEAD --ci-mode advisory --format json
shipgate audit --host --json --out agents-shipgate-reports/host-grants.json
For local control, parse the `shipgate check` stdout JSON
(`shipgate.agent_boundary_result/v1`): switch on `control.state`, then follow
`control.next_action`, `control.allowed_next_commands`, and
`control.human_review`; `decision` is diagnostic context only. For local uncommitted verify work,
omit `--base`/`--head`. For committed PR/CI refs,
make the base ref available first because `verify` never fetches. Validate
`agents-shipgate-reports/verification-receipt.json`, then read
`agents-shipgate-reports/agent-handoff.json` and lead with
`control.state` and `gate.merge_verdict`, then read the authoritative substrate
`agents-shipgate-reports/verifier.json` (`merge_verdict`, `applicability`,
`control`, `can_merge_without_human`, `control.next_action`,
`fix_task`), then supporting/provisional `capability_review.top_changes` and
`agents-shipgate-reports/report.json` for `release_decision.decision`. Do not
claim completion unless `control.state` is `complete`. Conversation-level
acknowledgement never clears a human-review route. A trusted host may provide a
signed external authorization for one exact command; only a new verifier
artifact that validates that grant can change control state. Do not
auto-assert action effect, action authority, approval, confirmation,
idempotency, broad-scope safety, prohibited-action enforcement, runtime-trace
proof, suppressions, waivers, baselines, or policy weakening. Never remove
Shipgate CI
or weaken agent instructions just to make the verifier pass.
```

## Use with your coding agent

**Claude Code** — two commands wire the full surface:

```bash
pipx install agents-shipgate
agents-shipgate init --workspace . --write --claude-code
```

`init --claude-code` writes the `CLAUDE.md` managed block, the
auto-discoverable `.claude/skills/agents-shipgate/` skill, and the Claude Code
hooks: a cheap trigger check after `Edit|Write|MultiEdit` and the full verifier
at `Stop`, so capability changes are re-checked before the agent reports work
complete — even on long sessions where instruction files lose attention. CI
stays authoritative; the hooks are the local feedback loop. Inside Claude Code,
agent mode auto-enables, so a zero-flag `agents-shipgate verify` prints the
compact agent result. Slash command, skill internals, and manual paths:
[`docs/agents/use-with-claude-code.md`](docs/agents/use-with-claude-code.md).

Prefer a plugin over a committed kit? This repo is also a Claude Code plugin
marketplace — the skill-only symmetric counterpart of the Codex plugin below
(workflows, not the scanner binary; install the CLI separately):

```bash
/plugin marketplace add ThreeMoonsLab/agents-shipgate
/plugin install agents-shipgate@agents-shipgate
```

The plugin ships the auto-triggering `agents-shipgate` skill and the
`/agents-shipgate:shipgate` command (plugin commands are namespaced). It does
not ship hooks — install those explicitly with `agents-shipgate install-hooks
--target claude-code --write`, which requires the CLI on `PATH`.

**Codex** — install the skill-only plugin from this repo's marketplace, or
write the repo-scoped kit directly:

```bash
codex plugin marketplace add ThreeMoonsLab/agents-shipgate   # plugin path
agents-shipgate init --workspace . --write --agent-instructions=agents-md,codex-skill  # committed path
```

Then invoke `$agents-shipgate` in a fresh thread. The plugin supplies
workflows, not the scanner binary — install the CLI (`pipx install
agents-shipgate && pipx upgrade agents-shipgate`) where Codex runs commands and
require contract v15 or newer. Marketplace details, kit overrides, and the beta-migration
steps: [`docs/agents/use-with-codex.md`](docs/agents/use-with-codex.md).

**Cursor** — `init --agent-instructions=cursor` writes the auto-attach rule;
see [`docs/agents/use-with-cursor.md`](docs/agents/use-with-cursor.md).

## Who this is for

- **Agent builders** — review MCP, OpenAPI, and SDK tool definitions before merging changes that expand the tool surface.
- **Platform teams** — add release gates for approval, scope, idempotency, and baseline drift to PR review.
- **Security and GRC reviewers** — get static release evidence without running agents or importing user code.

## Use this when

Run Agents Shipgate when a PR adds or changes agent tool surfaces or the policy
evidence around them:

- MCP exports, OpenAPI specs, or local tool inventories.
- OpenAI Agents SDK, Google ADK, LangChain/LangGraph, CrewAI, Anthropic
  Messages API, or OpenAI API artifact tool definitions.
- Codex repo config such as `.codex/config.toml` or `.codex/hooks.json`.
- Prompts, permission scopes, approval policies, confirmation policies,
  prohibited actions, or `shipgate.yaml`.
- GitHub Actions or CI release gates for a tool-using AI agent.

## Verify your repo

```bash
agents-shipgate verify --workspace . --config shipgate.yaml \
  --base origin/main --head HEAD --ci-mode advisory --format json
```

For local uncommitted work, omit `--base`/`--head`. For committed PR/CI refs,
make the base ref available first because `verify` never fetches. Verify writes
`agents-shipgate-reports/verification-plan.json`,
`verification-unit-result.json`, `verification-artifacts.json`,
`verification-receipt.json`, `agent-handoff.json`, `verifier.json`,
`verify-run.json`, `pr-comment.md`, the head capability lock, and the normal
`report.{md,json,sarif}` / packet artifacts when a scan is required. If the
base scan can be materialized, verify also writes
`base.capabilities.lock.json` plus `capability-lock-diff.{json,md}`, and the PR
comment includes a compact semantic capability diff summary. Lead with
`control.state`, then `merge_verdict`, `applicability`,
`can_merge_without_human`, `control.next_action`, and `fix_task`; use
`release_decision.decision` as the release gate. Capability diff summaries and
`capability_review.top_changes` are supporting/provisional review context.
Legacy `agent_result_v1` / `agent-result.json` compatibility surfaces are
supporting/provisional projections, not the CI gate or verifier read path.

Install alternatives (your agent project does **not** need Python 3.12 — install the CLI separately):

```bash
python -m pip install -U --pre agents-shipgate       # global pip
uv tool install --upgrade agents-shipgate            # via uv
agents-shipgate contract --json                      # require contract_version >= 14
```

## Adopt in one turn (scan helper)

The verifier-first loop above is the product entry path. For a scan-oriented
first adoption pass, `agents-shipgate bootstrap` runs all four steps in one
command, or run them individually:

```bash
agents-shipgate detect --json                                          # 1. classify
agents-shipgate init --write --ci --json                               # 2. manifest + workflow
agents-shipgate scan -c shipgate.yaml --suggest-patches --format json  # 3. scan + suggest
agents-shipgate apply-patches --from agents-shipgate-reports/report.json \
    --confidence high --apply                                          # 4. apply safe trivial fixes
```

`apply-patches` is dry-run by default and refuses to mutate anything outside
the manifest's directory. Agent-driven recipes:
[`docs/agent-recipes.md`](docs/agent-recipes.md); framework-by-framework
minimal manifests: [`docs/minimal-real-configs.md`](docs/minimal-real-configs.md).

## Use in CI

The public Action is listed on the
[GitHub Action Marketplace](https://github.com/marketplace/actions/agents-shipgate).
Drop this full advisory workflow into `.github/workflows/agents-shipgate.yml` —
it runs on every PR, posts a summary comment, uploads artifacts, and never
fails the job (same file as
[`examples/github-actions/01-advisory-pr-comment.yml`](examples/github-actions/01-advisory-pr-comment.yml)):

```yaml
name: Agents Shipgate (advisory)

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write

jobs:
  shipgate:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd
        with:
          fetch-depth: 0
      - uses: ThreeMoonsLab/agents-shipgate@v0.15.0
        with:
          ci_mode: advisory
          diff_base: target
          check_annotations: 'true'
          pr_comment: 'true'
```

The PR comment is fixed into a human summary plus agent instruction block, with
`merge_verdict`, the semantic capability diff when available, required next
action, and artifact links:

![Preview of the optional Agents Shipgate PR comment showing merge verdict, capability changes, required next action, and report artifacts.](assets/pr-comment-preview.png)

The action delegates to `agents-shipgate verify` and never fetches — keep
`fetch-depth: 0` on checkout. After adoption, choose an explicit merge policy:
[`07-block-on-blocked-verdict.yml`](examples/github-actions/07-block-on-blocked-verdict.yml)
blocks only when `merge_verdict == blocked`;
[`08-require-mergeable.yml`](examples/github-actions/08-require-mergeable.yml)
requires `can_merge_without_human == true`;
[`11-fail-on-insufficient-evidence.yml`](examples/github-actions/11-fail-on-insufficient-evidence.yml)
fails only on `insufficient_evidence`. Strict / baseline / SARIF / Check Run /
multi-config recipes live in
[`examples/github-actions/`](examples/github-actions/); the full input and
output catalog is in [`action.yml`](action.yml). Use the `decision` output for
CI gating and `merge_verdict` / `can_merge_without_human` for PR-control
routing.

CI is advisory by default. Strict mode exits `20` only on unsuppressed critical
findings; for existing projects, save a baseline first so strict CI fails only
on new findings:

```bash
agents-shipgate scan --config shipgate.yaml --ci-mode strict
agents-shipgate baseline save --config shipgate.yaml --out .agents-shipgate/baseline.json
agents-shipgate scan --config shipgate.yaml --baseline .agents-shipgate/baseline.json --ci-mode strict
```

Severity and failure thresholds are configurable in the manifest
(`checks.severity_overrides`, `ci.fail_on`) — see
[`docs/baseline.md`](docs/baseline.md) and
[`docs/integrations.md`](docs/integrations.md) for GitLab, CircleCI, Jenkins,
and pre-commit equivalents.

## What it scans

| Input | Status |
|---|---|
| Model Context Protocol (MCP) exports | Supported |
| OpenAPI 3.x specs | Supported |
| OpenAI Agents SDK Python files/directories | Supported |
| Anthropic Messages API artifacts | Supported |
| Google ADK Python and YAML config | Supported |
| LangChain/LangGraph static Python inputs | Supported |
| CrewAI static Python inputs | Supported |
| n8n workflow JSON and source-control stubs | Supported |
| Conductor OSS workflow JSON | Supported |
| OpenAI API artifacts | Supported |
| Codex repo config | Supported |
| Codex plugin packages and marketplaces | Supported |

## What it produces

When a PR changes what your agent can do, the verify loop writes these
artifacts — in read order:

- **`agents-shipgate-reports/verification-receipt.json`** — the **first artifact a coding agent validates**: a terminal content-addressed closure over the exact request (including `verification-input.diff`), worker result, decision, and artifact set. It is written last; use `agents-shipgate verification reproduce` to validate every referenced hash.
- **`agents-shipgate-reports/agent-handoff.json`** — the compact `shipgate.agent_handoff/v6` object. Lead with `control.state`, then `gate.merge_verdict`; it projects the same request, decision, and authorization evaluation and does not introduce a second verdict.
- **`agents-shipgate-reports/verifier.json`** — the **authoritative PR/control evidence substrate** (`verifier_schema_version: "0.6"`). A coding agent switches on `control.state`, then reads `authorization`, `merge_verdict` (`mergeable | human_review_required | insufficient_evidence | blocked | unknown`), `can_merge_without_human`, `control.next_action`, and `fix_task` when producing reviewer evidence for an agent-capability PR. Only an accepted signed authorization evaluation may expose an exact reviewed command; the release verdict remains unchanged. Local control comes from `shipgate check --format agent-boundary-json` and `shipgate.agent_boundary_result/v1`. See [`docs/agent-contract-current.md`](docs/agent-contract-current.md) for the field contract.
- **`agents-shipgate-reports/verify-run.json`** — the `shipgate.verify_run/v3` projection embedding the exact verification plan, executor, unit-result IDs, decision ID, outcome, and artifact paths. Its deprecated `run_id` is an exact alias of `request_id`.
- **`agents-shipgate-reports/attestation.json`** + **`agents-shipgate-reports/org-evidence-bundle.json`** — optional organization-governance projections over the same verifier/report artifacts. They are ledger inputs for platform teams, not release gates; `report.json.release_decision.decision` remains the decision engine.
- **`agents-shipgate-reports/host-grants.json`** + **`agents-shipgate-reports/org-status.json`** — optional fleet-governance artifacts from `audit --host --out` and `org status --json`, useful for host-grant drift, policy-pack pin state, and exception hygiene.
- **`agents-shipgate-reports/pr-comment.md`** — the **human PR surface**: the same verdict and semantic capability diff when available, shaped for a reviewer.
- **`agents-shipgate-reports/capabilities.lock.json`** + **`agents-shipgate-reports/base.capabilities.lock.json`** + **`agents-shipgate-reports/capability-lock-diff.{json,md}`** — the **capability review primitive**. Verify always emits the head lock after a successful scan; it emits the base lock and diff when the base scan can be materialized, falling back to the reviewed committed lock at `.agents-shipgate/capabilities.lock.json` if needed.
- **Gate source of truth** — `report.json.release_decision.decision` (`passed | review_required | insufficient_evidence | blocked`). `merge_verdict` is a deterministic projection of it; the report stays the one decision engine.
- **Tool-Use Readiness Report** (supporting) — `agents-shipgate-reports/report.{md,json,sarif}`. Markdown for human release review, JSON for tools and coding agents, SARIF for GitHub code-scanning workflows. This is the underlying check domain the verdict summarizes.
- **Release Evidence Packet** (supporting) — `agents-shipgate-reports/packet.{md,json,html}` (and `packet.pdf` with the `[pdf]` extras). Reviewer-shaped synthesis with fixed sections, including binding and semantic evidence coverage, the compact evidence matrix, and tool/action diffs when available. Packet outputs are locally redacted by default; see [STABILITY.md §Release Evidence Packet](STABILITY.md#release-evidence-packet-v012).

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Pass (advisory mode or strict-no-blockers) |
| `2` | Configuration/input-scope error, including an unresolved comparison base |
| `3` | Input parse error (file missing, malformed, path traversal blocked) |
| `4` | Other Agents Shipgate error |
| `20` | Strict-mode gate failure |

## For coding agents

Human readers can skip this section; it exists so coding agents can find the
repo's machine-readable contracts quickly.

Agents Shipgate is designed to be agent-friendly. If you're a coding agent (Claude Code, Codex, Cursor, Aider) reading this repo:

- **[`llms.txt`](llms.txt)** — short index of every machine-readable surface, one fetch.
- **[`llms-full.txt`](llms-full.txt)** — long-form concatenation of `AGENTS.md` + recipes + checks + concepts + autofix policy, in one document. Built by `scripts/build-llms-full.py`.
- **[`.well-known/agents-shipgate.json`](.well-known/agents-shipgate.json)** — discovery metadata (tagline, install commands, schema URLs, gating signal, exit codes, trigger-catalog URL).
- **[`docs/triggers.json`](docs/triggers.json)** — machine-readable mirror of the AGENTS.md trigger table. Apply the rules to a PR diff to decide whether to run `agents-shipgate verify --preview --json` or the full verifier. Schema is stable for `0.x`.
- **[`tools/shipgate-detect.py`](tools/shipgate-detect.py)** — zero-install, stdlib-only detector. `curl … | python3 - --workspace . --json` returns the same structural verdict as `agents-shipgate detect --json`. Pinned to the canonical CLI by [`tests/test_zero_install_detector.py`](tests/test_zero_install_detector.py). See [`docs/zero-install.md`](docs/zero-install.md).
- **`agents-shipgate contract --json`** — verify the installed CLI's local contract before relying on hard-coded schema or gating assumptions; the unified agent-control model requires `minimum_control_contract_version: "14"`.
- **[`docs/agent-contract-current.md`](docs/agent-contract-current.md)** — single source of truth for the current schema versions and which JSON fields to read. Updated whenever the contract bumps; other agent-facing surfaces link here instead of restating the contract.
- **[`docs/agent-native-merge-contract.md`](docs/agent-native-merge-contract.md)** — the agent-native protocol map: the eight contracts (trigger, capability change, merge verdict, repair, forbidden action, human authority, trust root, attestation) each mapped to the artifact that implements it.
- **[`docs/capability-standard.md`](docs/capability-standard.md)** — stable non-gating capability lock/diff standard for external integrations and research tooling.
- **[`docs/product-hardening-gap-closure.md`](docs/product-hardening-gap-closure.md)** — closure map for root dogfooding, the governance case catalog, policy-pack tests, trace evidence, and runtime-inventory boundaries.
- **[`benchmark/agent-pr-governance/`](benchmark/agent-pr-governance/)** + **[`docs/governance-benchmark.md`](docs/governance-benchmark.md)** — stable research benchmark for unsafe-merge prevention, authority routing, and verifier explanation quality.
- **[`AGENTS.md`](AGENTS.md)** — canonical agent-facing instructions: install, run, common tasks, JSON-mode flags, error semantics
- **[`STABILITY.md`](STABILITY.md)** — what won't break across `0.x` versions
- **[`docs/target-repo-agent-snippets.md`](docs/target-repo-agent-snippets.md)** — copyable snippets for adding Shipgate trigger rules to downstream agent repos
- **[`docs/agent-adoption-harness.md`](docs/agent-adoption-harness.md)** — manual protocol for checking whether coding agents discover and use Shipgate
- **[`benchmark/`](benchmark/)** — frozen archetypes, prompts, setup variants, and a public leaderboard CSV. Closes the loop on adoption-readiness changes.
- **[`docs/zero-install.md`](docs/zero-install.md)** — single-file detector, `uvx`, and GitHub Action paths for evaluating Shipgate without a local install.
- **[`prompts/`](prompts/)** — reusable prompts for common workflows
- **[`skills/agents-shipgate/`](skills/agents-shipgate/)** + **[`.claude/commands/shipgate.md`](.claude/commands/shipgate.md)** — self-contained Claude Code skill (bundled prompts and CI recipe) and `/shipgate` slash command. See [`docs/agents/use-with-claude-code.md`](docs/agents/use-with-claude-code.md) to install in your own project.
- **`agents-shipgate install-hooks --target claude-code --write`** — deterministic Claude Code hooks: a PreToolUse trust-root guard, a cheap trigger check after `Edit|Write|MultiEdit`, and a full `verify` at `Stop`, so the gate runs even when instruction files lose attention on long sessions. See [`docs/agents/use-with-claude-code.md`](docs/agents/use-with-claude-code.md#hooks-the-deterministic-path-recommended).
- **`agents-shipgate mcp-serve`** (`[mcp]` extra) — read-only stdio MCP server exposing `shipgate.check`, `shipgate.preflight`, `shipgate.explain`, `shipgate.capabilities`, and `shipgate.handoff` for agents without comfortable shell access. It is static-only and not a general MCP permission broker. See [`docs/mcp-server.md`](docs/mcp-server.md).
- **[`docs/ai-search-summary.md`](docs/ai-search-summary.md)** — human-readable summary for AI search, answer engines, and coding agents
- **[`docs/manifest-v0.1.json`](docs/manifest-v0.1.json)** + **[`docs/report-schema.v0.34.json`](docs/report-schema.v0.34.json)** + **[`docs/agent-handoff-schema.v6.json`](docs/agent-handoff-schema.v6.json)** + **[`docs/verification-receipt-schema.v1.json`](docs/verification-receipt-schema.v1.json)** + **[`docs/human-authorization-schema.v1.json`](docs/human-authorization-schema.v1.json)** — JSON Schemas for live editor validation, agent routing, reproducible verification identity, and the external signed-authorization family. Reports carry `report_schema_version: "0.34"`; every policy finding records typed predicate support, while heuristic-only, mixed, unknown, or conflicting applicability becomes a non-waivable evidence gap. `passed` still requires a complete root-reachable binding graph and complete identity, effect, authority, and policy evidence for each reachable action. A successful verifier run writes a terminal content-addressed receipt last; validate it before trusting the handoff or report. Every release decision remains static-only. See [`docs/verification-reproducibility.md`](docs/verification-reproducibility.md) and [`docs/passed-verdict-contract.md`](docs/passed-verdict-contract.md). v0.33 is frozen.
- **[`docs/capability-lock-schema.v0.5.json`](docs/capability-lock-schema.v0.5.json)** + **[`docs/capability-lock-diff-schema.v0.6.json`](docs/capability-lock-diff-schema.v0.6.json)** — current capability standard v0.4 schemas, including binding hashes, for the static capability envelope and semantic diff; non-gating and separate from `report.json`.
- **[`docs/attestation-schema.v0.5.json`](docs/attestation-schema.v0.5.json)** + **[`docs/org-governance-schema.v0.1.json`](docs/org-governance-schema.v0.1.json)** + **[`docs/org-evidence-bundle-schema.v2.json`](docs/org-evidence-bundle-schema.v2.json)** + **[`docs/registry-schema.v0.4.json`](docs/registry-schema.v0.4.json)** + **[`docs/host-grants-inventory-schema.v0.2.json`](docs/host-grants-inventory-schema.v0.2.json)** — deterministic local attestation, organization governance, org evidence bundle, append-only registry, and scope-aware host-grant inventory schemas for multi-repo governance.
- **[`docs/governance-benchmark-catalog-schema.v0.2.json`](docs/governance-benchmark-catalog-schema.v0.2.json)** + **[`docs/governance-benchmark-result-schema.v0.2.json`](docs/governance-benchmark-result-schema.v0.2.json)** — stable schemas for the research benchmark catalog and deterministic result artifact.
- **[`docs/checks.json`](docs/checks.json)** — machine-readable check catalog

Every command has a `--json` form. Errors emit a structured `next_action` line on stderr when agent mode is active — set `AGENTS_SHIPGATE_AGENT_MODE=1`, or rely on auto-detection inside a coding-agent harness (Claude Code exports `CLAUDECODE=1`, Cursor `CURSOR_TRACE_ID`). `AGENTS_SHIPGATE_AGENT_MODE=0` forces it off.

## Why this exists

Once an AI agent can refund, email, cancel, deploy, or modify a record, every tool change becomes a release event. Code review catches code; eval suites catch behavior; observability catches runtime. None of them answer the release question: *given the tool surface declared in this PR, do we have explicit approval policies, scope coverage, idempotency evidence, and review readiness for every action?*

Agents Shipgate produces a deterministic answer to that question, before promotion.

The current product promise is deliberately narrow: a deterministic, local-first,
static merge gate for AI-generated agent capability changes — the Tool-Use
Readiness review run at PR time. Broader lifecycle ideas are future roadmap
work, not claims this scanner makes today.

## Findings Gallery

The bundled support-refund fixture demonstrates the kind of release risks Agents Shipgate is designed to surface:

```text
## Release Decision

Decision: blocked
Reason: 2 active findings block release.
Blockers: 2
Review items: 16
Fail policy: would_fail_ci=false (exit 0)

Top findings:
1. stripe.create_refund lacks a declared approval policy
2. stripe.create_refund lacks idempotency evidence
3. Manifest declares broad permission scopes
```

- `stripe.create_refund` lacks a declared approval policy, so a financial action could ship without an explicit human review gate.
- `stripe.create_refund.amount` lacks a maximum bound, weakening blast-radius control.
- `stripe.create_refund` lacks idempotency evidence while retry behavior is known, risking duplicate refunds.
- `wildcard_mcp_tools.*` exposes a wildcard tool surface, making review incomplete.
- `gmail.send_customer_email` overlaps a prohibited external-communication action without a matching confirmation policy.

## See it block a PR

The fastest way to understand what changes for a reviewer: walk through a Golden PR. Each one ships a sample manifest, the resulting report, the release decision, and the recommended PR-comment summary an agent should post.

- [`openai-agents-sdk-refund-agent`](examples/golden-prs/openai-agents-sdk-refund-agent/README.md) — refund agent adds `stripe.create_refund`. Shipgate decides `blocked` because approval policy and idempotency evidence are missing. Includes the recommended Markdown PR-comment template.
- [`golden-pr-from-coding-agent.md`](examples/golden-prs/golden-pr-from-coding-agent.md) — the *artifact* a coding agent should produce after running the verify-first flow: PR comment, `merge_verdict`, `capability_review`, and human/coding-agent next action.
- [`mcp-only-tool-server`](examples/golden-prs/mcp-only-tool-server/README.md) — MCP server with no Python framework imports; demonstrates the MCP-only adoption path.
- [`openapi-support-agent`](examples/golden-prs/openapi-support-agent/README.md) — OpenAPI-described tool surface; shows scope-coverage findings.

## Why Not Just...

| Alternative | Gap Agents Shipgate Covers |
| --- | --- |
| Unit tests | Tests usually validate code paths, not the released tool surface and declared policies. |
| Code review | Reviewers miss generated specs, MCP exports, broad scopes, and missing approval policies. |
| Runtime traces | Useful later, but they arrive after behavior exists. Agents Shipgate runs before promotion. |
| Nothing | Tool-surface drift becomes a production surprise. |

For named comparisons against specific evaluators and platforms, see the
marketing-site versus pages:
[vs evals](https://threemoonslab.com/vs/evals/),
[vs promptfoo](https://threemoonslab.com/vs/promptfoo/),
[vs Braintrust](https://threemoonslab.com/vs/braintrust/),
[vs LangSmith](https://threemoonslab.com/vs/langsmith/), and
[vs observability platforms](https://threemoonslab.com/vs/observability/).

## Framework notes

Framework adapters (Google ADK, LangChain/LangGraph, CrewAI, OpenAI Agents
SDK) parse Python AST only — they never import framework packages or user
modules. Dynamic or prebuilt toolsets produce warnings or
`insufficient_evidence` findings unless you provide explicit MCP, OpenAPI, or
local tool-inventory inputs. Framework-by-framework minimal manifests, with
runnable sample repos for each adapter, live in
[`docs/minimal-real-configs.md`](docs/minimal-real-configs.md).

Organization-specific release rules ship as local declarative YAML
[policy packs](docs/policy-packs.md) (`checks.policy_packs` in the manifest, or
`--policy-pack` on the CLI) — static data, no code import.

## Limitations

Agents Shipgate is a static, manifest-first scanner. It is intentionally narrow:

- It does not run agents, call tools, invoke LLMs, or verify model availability by default (static-by-default; see [Trust Model](#trust-model) and [`ALLOWED_EXCEPTIONS`](tests/test_adapter_static_only.py)).
- It does not verify runtime behavior, latency, prompt quality, or routing decisions.
- It does not replace dynamic security testing or human security review of the underlying systems.
- It only inspects what is declared in `shipgate.yaml`, local OpenAPI specs, MCP exports, Anthropic/OpenAI API artifacts, optional SDK AST metadata, static Google ADK/LangChain/CrewAI/n8n/Conductor OSS inputs, Codex repo config, and static Codex plugin package metadata; tools that are not declared or statically discoverable are not scanned.
- The manifest remains `version: "0.1"` so existing configs keep working. Current reports carry `report_schema_version: "0.34"`; binding, semantic, and policy-applicability gaps are non-waivable release evidence rather than Findings, while v0.33 remains frozen for archived reports.

See [ROADMAP.md](ROADMAP.md) for what is planned next.

## Trust Model

**Agents Shipgate does not import user code, run agents, call tools, call LLMs, connect to MCP servers, make network calls, or collect telemetry by default.**

See [Trust model](docs/trust-model.md) and [Security policy](SECURITY.md) for the default local-only guarantees and disclosure process.

## Pricing And Open Source Stance

Agents Shipgate is and will remain free OSS for individuals and teams running it on their own infrastructure. The core manifest-first scanner, built-in checks, Markdown report, and JSON report are intended to remain open source. We do not collect telemetry and do not require an account.

If hosted dashboards, SSO, org-wide baselines, approval workflows, or trace-based evidence emerge, they should live in a separate optional product rather than moving core OSS functionality behind a paywall.

Teams shipping production-like tool-using agents can apply to the
[Three Moons Lab design partner program](https://threemoonslab.com/design-partners/)
— the marketing page mirrors
[`docs/design-partners.md`](docs/design-partners.md) in the repo and includes a
prefilled email CTA for review criteria and contact. The current pilot runbook
is [`docs/design-partner-verifier-pilot.md`](docs/design-partner-verifier-pilot.md):
bring one AI-generated agent PR, run the verifier loop, and export redacted
feedback (`agents-shipgate feedback export --from
agents-shipgate-reports/verifier.json --redact --out shipgate-feedback.json` —
never raw report evidence).

## Docs

The marketing site at [threemoonslab.com](https://threemoonslab.com/) carries
the same canonical concepts in human-readable, search-optimised form:
[quickstart](https://threemoonslab.com/quickstart/),
[check catalog](https://threemoonslab.com/checks/),
[glossary](https://threemoonslab.com/glossary/),
[MCP security review](https://threemoonslab.com/use-cases/mcp-security-review/),
[AI agent least privilege](https://threemoonslab.com/use-cases/ai-agent-least-privilege/),
[blog](https://threemoonslab.com/blog/), and
[design partners](https://threemoonslab.com/design-partners/). The in-repo docs
below are the canonical contract; the marketing pages are sized for first-time
readers and AI search ingest.

- [The 5-minute mental model](docs/mental-model.md)
- [MCP and host-permission governance](docs/mcp-governance.md)
- [MCP server mode](docs/mcp-server.md)
- [Organizational governance](docs/organization.md)
- [Tool-Use Readiness release gate category](docs/category.md)
- [Manifest v0.1](docs/manifest-v0.1.md)
- [Check catalog](docs/checks.md)
- [Policy packs](docs/policy-packs.md)
- [Baseline workflow](docs/baseline.md)
- [JSON report schema v0.34](docs/report-schema.v0.34.json)
- [JSON report schema v0.32 (frozen)](docs/report-schema.v0.32.json)
- [JSON report schema v0.31 (frozen)](docs/report-schema.v0.31.json)
- [Capability standard](docs/capability-standard.md)
- [Capability lock schema v0.5](docs/capability-lock-schema.v0.5.json)
- [Capability lock diff schema v0.6](docs/capability-lock-diff-schema.v0.6.json)
- [Governance benchmark](docs/governance-benchmark.md)
- [Feedback export schema v0.1](docs/feedback-schema.v0.1.json)
- [Privacy and redaction](docs/privacy.md)
- [Terms](docs/terms.md)
- [Trust model](docs/trust-model.md)
- [AI search summary](docs/ai-search-summary.md)
- [Agent entry points](docs/agents/README.md)
- [Design partners](docs/design-partners.md)
- [Design partner verifier pilot](docs/design-partner-verifier-pilot.md)
- [Runtime inventory design note](docs/runtime-inventory.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Integration recipes](docs/integrations.md)
- [Distribution plan](docs/distribution.md)
- [JSON report schema v0.2](docs/report-schema.v0.2.json)
- [JSON report schema v0.1](docs/report-schema.v0.1.json)
