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

Agents Shipgate is an open-source CLI and GitHub Action for local-first, static
Tool-Use Readiness review. It scans MCP, OpenAPI, OpenAI Agents SDK, Anthropic
Messages API, Google ADK, LangChain/LangGraph, CrewAI, OpenAI API, Codex repo
config, Codex plugin, n8n, and Conductor OSS workflow artifacts, then writes a
deterministic **Tool-Use Readiness Report** before your agent gets
production-like permissions.

## One capability change, one verdict

A coding agent adds a refund tool to a support agent. The MCP export goes from
one read-only tool to two, and the new one carries a wildcard Stripe scope:

```diff
   { "name": "support.search_kb",
     "annotations": { "readOnlyHint": true, "idempotentHint": true },
     "auth": { "type": "oauth2", "scopes": ["support:kb:read"] } },
+  { "name": "stripe.create_refund",
+    "annotations": { "readOnlyHint": false, "destructiveHint": true },
+    "auth": { "type": "oauth2", "scopes": ["stripe:*"] } }
```

The manifest still declares one scope, `support:kb:read`, and no approval
policy. The diff looks fine to a human skimming it. Run the committed sample:

```bash
uvx agents-shipgate fixture run ai_generated_refund_pr
```

```text
Merge verdict: blocked
Decision: blocked
Can merge without human: false
```

`report.md` names what changed and why it is held:

```text
Decision: blocked
Reason: 4 active findings block release.

Blockers (4):
- CRITICAL SHIP-POLICY-APPROVAL-MISSING — stripe.create_refund lacks a declared approval policy
- CRITICAL SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING — stripe.create_refund has destructive capability without required controls
- CRITICAL SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING — stripe.create_refund has financial write capability without required controls
- CRITICAL SHIP-ACTION-WILDCARD-SCOPE — stripe.create_refund declares a broad action scope
```

**The process exited zero.** In advisory mode `would_fail_ci=false`, and the
change is still `blocked`. Exit status is a CI policy choice; the verdict is
the answer. Teaching the two apart is the first thing
[the quickstart](docs/quickstart.md) does.

Now the move every reviewer fears — the agent deletes the Shipgate CI gate so
its own PR passes:

```bash
uvx agents-shipgate fixture run agent_weakens_gate
```

→ `merge_verdict: blocked`, `can_merge_without_human: false`. The gate-removal
checks are suppression-immune: the cheapest reward-hack is also the most
visible one. More replayable incident shapes are in the
[incident-shape suite](docs/incidents/README.md).

One engine decides (`report.json.release_decision.decision`); everything else —
`merge_verdict`, PR comments, Check Runs, Action outputs — is a deterministic
projection of it. Five-minute version:
[`docs/mental-model.md`](docs/mental-model.md).

## What your PR sees

The same run writes `pr-comment.md`, the comment the GitHub Action posts.
Abridged from the artifact:

```text
## Agents Shipgate

### Human summary
- Merge verdict: `blocked`
- Can merge without human: `false`
- Release gate: `blocked`
- Capability delta (analysed surface): 2 subjects across 6 changes (+1 added, 2 modified, -0 removed)
- Top capability changes by subject:
  - `stripe.create_refund`: added action — blocks release; added tool — blocks
    release; broadened action destructive — high-risk effect destructive added;
    blocks release; …
- Static-verdict boundary: This verdict covers deterministic static evidence
  only. Agents Shipgate did not execute the agent or prove runtime behavior,
  tool routing, credential enforcement, or safety.
- Next actor: `human`
```

A second fenced block carries `control` and `fix_task` for the coding agent.
The exact rendering depends on the build you installed — see
[which build you get](docs/quickstart.md#which-build-you-get).

## What static evidence cannot prove

Both fixtures above are constructed, with a clear-cut answer, chosen to show
the gate working. Real PRs are messier. When a change builds its tool surface
dynamically — a toolkit factory, a config-bound allowlist, tools assembled at
runtime — static extraction often cannot enumerate the result, and Shipgate
returns `insufficient_evidence` and routes to a human rather than emit a
confident wrong verdict. That is the intended failure mode, not a bug; reducing
how often it fires on real dynamic code is active work.

Every result carries its own coverage limit. Nothing here proves runtime
behavior, tool routing, credential enforcement, or safety — only what the
declared and statically discoverable surface says. See
[Limitations](#limitations) and [ROADMAP.md](ROADMAP.md).

> [!IMPORTANT]
> **Status: pre-1.0 (beta).** The decision engine is deterministic and stable.
> The accuracy evidence is small-n and incomplete, and the parts that are zero
> are stated here rather than in a footnote. On the **19 unique labeled
> engine-engaged PRs** mined from **8 distinct** real agent repos and re-run on
> the released `v0.15.0` engine, the gate **never auto-passed an unsafe
> change** (`must_block_caught` / `needs_human_caught` = 1.0). **But it routes
> to review, it does not block:** real-history `blocked_recall` is still
> **0.0** — both `must_block` PRs return `human_review_required` /
> `review_required`. That release also escalated 4 of 14 safe PRs to review
> (`benign_escalation_rate` 0.286, largely a cold-start whole-repo-surface
> artifact on large repos). Reliable blocked-recall and zero benign escalation
> are proven on the constructed-adversarial stratum, not on real history, and
> the labels are AI-adjudicated (0 disagreement) pending human spot-check.
> Treat it as an advisory gate while this work closes. Full numbers:
> [`benchmark/miner/README.md`](benchmark/miner/README.md) and
> [ROADMAP.md](ROADMAP.md).

## Install

```bash
pipx install agents-shipgate
```

`pipx upgrade agents-shipgate` refreshes a stale copy; a plain install is a
no-op over one. Alternatives — `pip`, `uv`, and zero-install `uvx` — are in
[`docs/quickstart.md`](docs/quickstart.md#install). Your agent project does
**not** need Python 3.12; the CLI installs separately.

**Read [which build you get](docs/quickstart.md#which-build-you-get) before you
start.** The newest published release is `v0.15.0`, which implements runtime
contract `10`. Parts of the workflow this repository documents — the agent
control envelope, `current-control.json`, `--format agent-boundary-json` —
landed after that tag and are not in it. The quickstart names, per step, which
channel provides what, and what the unqualified preview does and does not come
with.

## Where to go next

| You are | Start at | It gives you |
| --- | --- | --- |
| A human evaluating one PR | [`docs/quickstart.md`](docs/quickstart.md) | One review end to end: what changed, why it matters, what was not established, and the next action |
| A coding agent | [`AGENTS.md`](AGENTS.md), [`llms.txt`](llms.txt), [For coding agents](#for-coding-agents) | Machine-readable contracts, the read order, and the boundary you must not cross |
| Adding this to CI | [`docs/integrations.md`](docs/integrations.md), [`examples/github-actions/`](examples/github-actions/) | Advisory-first workflows, then explicit merge policies |
| Adopting it in a repo | [`docs/quickstart.md`](docs/quickstart.md#run-it-on-your-own-repository) | The zero-manifest host route and the manifest route, and how to tell which one you are on |
| Contributing | [`CONTRIBUTING.md`](CONTRIBUTING.md), [`ROADMAP.md`](ROADMAP.md) | Surface discipline, the check catalog, and what is planned |

Not sure it applies at all? The stdlib-only detector answers in one fetch, with
no install:

```bash
curl -sSL https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/tools/shipgate-detect.py \
  | python3 - --workspace . --json
```

See [`docs/zero-install.md`](docs/zero-install.md) for how to read its answer.

## Who this is for

- **Agent builders** — review MCP, OpenAPI, and SDK tool definitions before merging changes that expand the tool surface.
- **Platform teams** — add release gates for approval, scope, idempotency, and baseline drift to PR review.
- **Security and GRC reviewers** — get static release evidence without running agents or importing user code.

Run it when a PR adds or changes agent tool surfaces or the policy evidence
around them: MCP exports and OpenAPI specs; an MCP server whose tool surface
exists only as code; framework tool definitions; Codex repo config; prompts,
scopes, approval and confirmation policies, prohibited actions, `shipgate.yaml`;
or the CI release gate itself.

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

Framework adapters parse Python AST only — they never import framework packages
or user modules. Dynamic or prebuilt toolsets produce warnings or
`insufficient_evidence` findings unless you provide explicit MCP, OpenAPI, or
local tool-inventory inputs. Framework-by-framework minimal manifests, with a
runnable sample repo for each adapter, are in
[`docs/minimal-real-configs.md`](docs/minimal-real-configs.md).

## Why this exists

Once an AI agent can refund, email, cancel, deploy, or modify a record, every
tool change becomes a release event. Code review catches code; eval suites
catch behavior; observability catches runtime. None of them answer the release
question: *given the tool surface declared in this PR, do we have explicit
approval policies, scope coverage, idempotency evidence, and review readiness
for every action?*

Agents Shipgate produces a deterministic answer to that question, before
promotion. The promise is deliberately narrow: a static merge gate for
AI-generated agent capability changes, run at PR time. Broader lifecycle ideas
are roadmap work, not claims this scanner makes today. Comparisons against
tests, code review and runtime traces are in
[`docs/category.md`](docs/category.md).

## For coding agents

Human readers can skip this section; it exists so coding agents can find the
repo's machine-readable contracts quickly.

- **[`AGENTS.md`](AGENTS.md)** — canonical agent-facing instructions: install, run, common tasks, JSON-mode flags, error semantics.
- **[`llms.txt`](llms.txt)** / **[`llms-full.txt`](llms-full.txt)** — short index of every machine-readable surface, and the long-form one-fetch concatenation.
- **[`.well-known/agents-shipgate.json`](.well-known/agents-shipgate.json)** — discovery metadata: tagline, install commands, schema URLs, gating signal, exit codes, trigger-catalog URL.
- **[`docs/triggers.json`](docs/triggers.json)** — machine-readable mirror of the AGENTS.md trigger table. Apply the rules to a PR diff to decide whether to run the verifier.
- **[`tools/shipgate-detect.py`](tools/shipgate-detect.py)** — zero-install, stdlib-only detector returning the same structural verdict as `agents-shipgate detect --json`. Pinned to the CLI by [`tests/test_zero_install_detector.py`](tests/test_zero_install_detector.py).
- **[`docs/agent-contract-current.md`](docs/agent-contract-current.md)** — the single source of truth for current schema versions, which JSON fields to read, the command-scoped artifact lifecycle, and the signed human-authorization protocol. Other surfaces link here instead of restating it.
- **[`docs/agent-native-merge-contract.md`](docs/agent-native-merge-contract.md)** — the eight contracts (trigger, capability change, merge verdict, repair, forbidden action, human authority, trust root, attestation) each mapped to the artifact that implements it.
- **[`docs/agent-autofix-boundary.md`](docs/agent-autofix-boundary.md)** — what an agent may fix mechanically, and what it must never assert. Read this before touching a declaration.
- **[`docs/agents/`](docs/agents/README.md)** — per-host entry points, the local control protocol, and the feedback loop.
- **[`docs/checks.json`](docs/checks.json)** + **[`docs/checks.md`](docs/checks.md)** — machine-readable and prose check catalogs.
- **[`docs/report-schema.v0.43.json`](docs/report-schema.v0.43.json)** — the current report schema, `0.43`. Reports carry `report_schema_version: "0.43"`; the handoff, verifier, receipt, capability-lock and attestation schemas are listed with it in [`docs/INDEX.md`](docs/INDEX.md).
- **[`STABILITY.md`](STABILITY.md)** — what will not break across `0.x` versions.

Read `agents-shipgate-reports/current-control.json` first — it names which run
is current, and `agents-shipgate agent control --workspace .` validates it
against every artifact it binds and against the live repository, refusing the
read when HEAD or the working tree has moved since the decision. The full read
order, and what each artifact carries, is in
[`docs/agent-contract-current.md`](docs/agent-contract-current.md).

Run `agents-shipgate contract --json` before relying on hard-coded schema or
gating assumptions; the permission-scoped agent-control model requires
`minimum_control_contract_version: "21"`. Every command has a `--json` form.
Errors emit a structured `next_action` line on stderr when agent mode is active
— set `AGENTS_SHIPGATE_AGENT_MODE=1`, or rely on auto-detection inside a coding
agent harness (Claude Code exports `CLAUDECODE=1`, Cursor `CURSOR_TRACE_ID`).
`AGENTS_SHIPGATE_AGENT_MODE=0` forces it off.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Pass (advisory mode or strict-no-blockers) |
| `2` | Manifest config error |
| `3` | Input parse error (file missing, malformed, path traversal blocked) |
| `4` | Other Agents Shipgate error |
| `20` | Strict-mode gate failure |

Exit zero is not merge permission. Read the verdict.

## Limitations

Agents Shipgate is a static, manifest-first scanner. It is intentionally narrow:

- It does not run agents, call tools, invoke LLMs, or verify model availability by default (static-by-default; see [Trust Model](#trust-model) and [`ALLOWED_EXCEPTIONS`](tests/test_adapter_static_only.py)).
- It does not verify runtime behavior, latency, prompt quality, or routing decisions.
- It does not replace dynamic security testing or human security review of the underlying systems.
- It only inspects what is declared in `shipgate.yaml`, local OpenAPI specs, MCP exports, MCP server source registrations, Anthropic/OpenAI API artifacts, optional SDK AST metadata, static Google ADK/LangChain/CrewAI/n8n/Conductor OSS inputs, Codex repo config, and static Codex plugin package metadata; tools that are not declared or statically discoverable are not scanned.
- The manifest remains `version: "0.1"` so existing configs keep working. Current reports carry `report_schema_version: "0.43"`; every narrowing decision is recorded in `surface_exclusions` and reachable by the release decision, while v0.42 remains frozen for archived reports.

## Trust Model

**Agents Shipgate does not import user code, run agents, call tools, call LLMs, connect to MCP servers, make network calls, or collect telemetry by default.**

See [Trust model](docs/trust-model.md) and [Security policy](SECURITY.md) for
the default local-only guarantees and the disclosure process.

## Pricing and open-source stance

Agents Shipgate is and will remain free OSS for individuals and teams running
it on their own infrastructure. The core manifest-first scanner, built-in
checks, and Markdown/JSON reports are intended to remain open source. We do not
collect telemetry and do not require an account. If hosted dashboards, SSO,
org-wide baselines, approval workflows, or trace-based evidence emerge, they
should live in a separate optional product rather than moving core OSS
functionality behind a paywall.

Teams shipping production-like tool-using agents can apply to the
[Three Moons Lab design partner program](https://threemoonslab.com/design-partners/)
(mirrored at [`docs/design-partners.md`](docs/design-partners.md)). The pilot
runbook is
[`docs/design-partner-verifier-pilot.md`](docs/design-partner-verifier-pilot.md);
what the pilot has actually observed, including the counts that are still zero,
is in
[`docs/design-partner-pilot-results.md`](docs/design-partner-pilot-results.md).

## Adopters

Agents Shipgate is local-first and static by default, and it collects nothing:
no telemetry, no analytics, no account. It therefore cannot count its own
users. [`ADOPTERS.md`](ADOPTERS.md) is the opt-in public registry that stands
in for that: one line per adopter, added by the adopter, naming what they gate
and whether it runs locally, as advisory CI, or as blocking CI. There is **no
automatic collection of any kind** behind it — every entry is user-initiated
and consenting, private repositories are listed at organization granularity,
and an entry is removed on request without a reason. Private design-partner observations are a separate ledger under
separate consent and never become public entries on their own.

Every adoption number this project publishes traces to a named entry there —
no entry, no claim — and maintainer dogfooding is counted apart from external
adoption. Today that is **0 external adopter entries and 1 maintainer
dogfooding entry** (as of 2026-09-06). If you run it,
[add yourself](ADOPTERS.md#add-yourself); there is an optional
[badge](ADOPTERS.md#badge) that links back to the registry, and it implies
nothing about which tier you run.

## Docs

[`docs/INDEX.md`](docs/INDEX.md) is the full index. The most-used pages:

- [Quickstart — one review, end to end](docs/quickstart.md)
- [The 5-minute mental model](docs/mental-model.md)
- [Check catalog](docs/checks.md) · [Manifest v0.1](docs/manifest-v0.1.md) · [Policy packs](docs/policy-packs.md)
- [Baseline workflow](docs/baseline.md) · [Integration recipes](docs/integrations.md) · [Troubleshooting](docs/troubleshooting.md)
- [Sample reports and fixtures](samples/README.md) · [Golden PRs](examples/golden-prs/README.md)
- [Agent entry points](docs/agents/README.md) · [Current agent contract](docs/agent-contract-current.md)
- [Trust model](docs/trust-model.md) · [Privacy and redaction](docs/privacy.md) · [Terms](docs/terms.md)
- [Distribution surfaces](docs/distribution-surfaces.md) — every surface this engine is published through, and the test that proves each one

The marketing site at [threemoonslab.com](https://threemoonslab.com/) carries
the same concepts in search-optimised form:
[quickstart](https://threemoonslab.com/quickstart/),
[check catalog](https://threemoonslab.com/checks/),
[glossary](https://threemoonslab.com/glossary/), and
[design partners](https://threemoonslab.com/design-partners/). The in-repo docs
are the canonical contract.

### Moved out of this README

This file was a 900-line reference. The material is intact, in these places:

| Was | Now |
| --- | --- |
| `#60-seconds-watch-it-block-two-prs` | [One capability change, one verdict](#one-capability-change-one-verdict) |
| `#verify-first-quickstart`, `#local-boundary-check`, `#pr-and-local-verification`, `#host-grant-audit`, `#how-to-read-your-first-result` | [`docs/quickstart.md`](docs/quickstart.md) — the three prominent flows are under [Run it on your own repository](docs/quickstart.md#run-it-on-your-own-repository), the verdict tables under [Verdict reference](docs/quickstart.md#verdict-reference) |
| `#verify-your-repo`, `#adopt-in-one-turn-scan-helper` | [`docs/quickstart.md`](docs/quickstart.md#run-it-on-your-own-repository) |
| `#temporary-external-repository-review` | [`docs/quickstart.md`](docs/quickstart.md#temporary-external-repository-review) |
| `#authorize-one-exact-coding-agent-action` | [`docs/agent-contract-current.md`](docs/agent-contract-current.md#trusted-human-authorization-for-one-exact-command) |
| `#use-in-ci` | [`docs/integrations.md`](docs/integrations.md), [`examples/github-actions/`](examples/github-actions/) |
| `#what-it-produces` | [`docs/agent-contract-current.md`](docs/agent-contract-current.md#read-these-first-for-release-gating) |
| `#copy-this-into-your-coding-agent`, `#use-with-your-coding-agent` | [`docs/target-repo-agent-snippets.md`](docs/target-repo-agent-snippets.md), [`docs/agents/README.md`](docs/agents/README.md) |
| `#sample-reports`, `#findings-gallery`, `#see-it-block-a-pr` | [`samples/README.md`](samples/README.md), [`examples/golden-prs/README.md`](examples/golden-prs/README.md) |
| `#why-not-just` | [`docs/category.md`](docs/category.md) |
| `#framework-notes` | The AST-only boundary is under [What it scans](#what-it-scans); the per-framework manifests are in [`docs/minimal-real-configs.md`](docs/minimal-real-configs.md) |
| `#not-sure-if-shipgate-applies` | [Where to go next](#where-to-go-next), and [`docs/zero-install.md`](docs/zero-install.md) |
| `#use-this-when` | Folded into [Who this is for](#who-this-is-for) |
