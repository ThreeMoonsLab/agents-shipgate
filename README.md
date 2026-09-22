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

**Your coding agent changed what your AI agent can do — Agents Shipgate shows you what changed before it merges.**

**The deterministic merge gate for AI-generated agent capability changes.**

Local-first and static by default — no agent execution, tool calls, LLM calls, or network access.

<!-- Canonical tagline: The deterministic merge gate for AI-generated agent capability changes. -->

Agents Shipgate is an open-source CLI and GitHub Action for local-first, static
Tool-Use Readiness review. It scans MCP, OpenAPI, OpenAI Agents SDK, Anthropic
Messages API, Google ADK, LangChain/LangGraph, CrewAI, OpenAI API, Codex repo
config, Codex plugin, n8n, and Conductor OSS workflow artifacts, then writes a
deterministic **Tool-Use Readiness Report** before your agent gets
production-like permissions.

## What did this PR change?

Start with a pull request you already have that changes what a coding agent
may do: `.claude/settings.json`, `.mcp.json`, Codex, Cursor or VS Code MCP
configuration. No manifest, policy, saved baseline, skill or account is
needed, and nothing is written to your repository. From the PR branch, with its
base branch available in your clone:

```bash
pipx install agents-shipgate
agents-shipgate diff
```

`diff` compares your working tree with its merge base on the repository's
default branch (`origin/HEAD`, `origin/main` or `origin/master`) and prints one
entry per changed grant or replaced rule. If the PR targets another branch, pass
`--base origin/<that-branch>`; in a fork clone, fetch `upstream` and pass
`--base upstream/<pr-base>`. On a PR that widens a Claude Code allow rule,
drops a denial and adds an MCP server:

```text
Agent capability diff  origin/main (943fd29b) -> working tree

⚠ high    added    claude-code .mcp.json
                  billing (command name npx; env keys BILLING_TOKEN)
                  an MCP tool surface the agent may call has changed

⚠ medium  widened  claude-code .claude/settings.json
                  allow: Bash(npm test:*) → allow: Bash(npm *)
                  the new rule matches everything the old rule matched; runs without a prompt

⚠ low     removed  claude-code .claude/settings.json
                  deny: Bash(rm -rf:*) → gone
                  removes a denial the agent was subject to

3 change(s) from 4 rows, 3 widening what the agent may do (⚠).
Static configuration only: this is what the files permit, not what the agent did. No verdict is implied.

What this run established:
  only sources this entry read or tried to read are listed, so this is not the whole change: a changed file it does not read is absent
  .claude/settings.json (claude-code): compared; 3 rows
  .mcp.json (claude-code): compared; 1 row

Review question: Does the team intend these 3 declared capability changes (from 4 rows)?
Compared: base 943fd29b → working tree at HEAD 8982b16e, agents-shipgate 1.0.0.
Reproduce in that working tree: agents-shipgate diff --base 943fd29b6e483d27ad9eb52c6d909357c9f2541b
```

The answer is one of these, and they mean different things: named changes,
like these; `No static host-grant changes detected.` when no compared grant
differs; or `Cannot compare against <base>: <reason>` when an input could not be
read, which is an input limit and never a quiet pass. Either of the first two
can open with `Not compared:` and a list of sources the change did not touch
and `diff` could not read; nothing is claimed about those. Every answer then
says `What this run established`: which sources were compared and how many rows
each gave, which changed with no compared grant changing (so a zero-row `env`
or `apiKeyHelper` edit is not mistaken for no change: a file is unchanged only
when its bytes are), which changed with no row attributed to them, which only
one side read or published, and, when
the comparison was refused, which source left an inventory incomplete. That
block lists only sources this entry read, and says so on its first line: a
changed file it does not read is absent from it, so it is never the whole
account of the change. When more items are computed than it prints, it names
how many it left out and that they rank below the ones it kept. The
entries are for a reviewer to act on, not merge authority: each names the rule
with its disposition, a replaced rule's before and after, and an MCP server's
command name or redacted URL and key names, then one review question. Every
answer, a zero-row one and a refusal included, ends with the compared commits
and the command that reproduces the comparison. `--json` publishes the same entries,
counters, question and command beside the rows, so a script and a reader
describe one run the same way. The
[quickstart](docs/quickstart.md#review-a-host-configuration-change) shows each
answer, the `--base <ref>` recovery when no base can be detected, and the
[surfaces `diff` does not read](docs/host-boundary-support.md#known-unread-surfaces).
**Not yet released:** the output above is from this repository's source tree,
which still reports version `1.0.0`, run in a clone. The published `1.0.0` from
PyPI names the same changes as four rows, without the dispositions, the joined
replacement, the MCP launch details, the `What this run established` block, the
review question and the reference lines.

When the answer is useful and you want it on every pull request, add
[`examples/github-actions/14-host-only-advisory-pr.yml`](examples/github-actions/14-host-only-advisory-pr.yml):
the same comparison as an advisory PR comment, with no manifest or saved
baseline, and a failing job only for setup errors.

## One capability change, one verdict

If your repository builds its own tool surface — MCP or OpenAPI exports,
framework tool definitions — the same engine gates that too, with a manifest.
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

These excerpts are from a source checkout. For an older install, see
[which build you get](docs/quickstart.md#which-build-you-get).

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

Host configuration alone needs none of this: [What did this PR
change?](#what-did-this-pr-change) is the whole route. [Route
H](docs/quickstart.md#route-h--no-manifest) adds a snapshot audit and an
optional committed baseline for jobs that want them, and `v1.0.0`'s discovery
routes host-only repositories there through `host_boundary_candidates`.
Filename detection never establishes verified permissions.

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
> **Status: `v1.0.0`, advisory.** The published release makes no qualification
> claim. Its defaults are advisory, and blocking CI is a policy you opt into
> explicitly. The decision engine is deterministic; the accuracy evidence is
> small-n and incomplete, and the parts below their bars are stated here rather
> than in a footnote. On the fixed host-configuration corpora measured for 1.0,
> change-row precision is 70/70, widening recall 55/65, benign zero-row 5/6 and
> comparable coverage 41/50 — nine of fifty comparisons answer `Cannot compare`;
> recall and the benign rate remain below their original bars, recorded as such
> in [ROADMAP.md](ROADMAP.md#publication-and-evidence) rather than relabeled as
> passes. The real-history numbers that follow are older, measured on
> 2026-07-08 on the released `v0.15.0` engine and not re-run on `v1.0.0`: on
> the **19 unique labeled engine-engaged PRs** mined from **8 distinct** real
> agent repos, the gate **never auto-passed an unsafe
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

**Two lines, two promises.** The advisory line publishes rows a reviewer
reads, and no authority to block anything by default; `v1.0.0` is an advisory
release. The gate line publishes blocking verdicts and keeps every
qualification bar. Each `v*` version is declared on exactly one line in
[`.github/release-channels.json`](.github/release-channels.json). Neither line
waits on the other, and
[`docs/release-cadence`](docs/distribution.md#package-channels) measures both
separately — one number could not have shown that the documented workflow was
two months out of reach.

| Line | Carries | Install | Promises | Cadence |
| --- | --- | --- | --- | --- |
| **Advisory** | `diff`, `check`, `audit --host`, drift, advisory PR comments | `pipx install agents-shipgate` (`v1.0.0`), or an unqualified preview pre-release | plain-language capability rows; **no blocking authority** unless you configure a blocking policy | 14 days |
| **Qualified gate** | blocking verdicts backed by qualification evidence, receipts, attestations | a `v*` release declared on the qualified line; `v1.0.0` is not one | every bar in [`release-evidence-policy-decision.md`](docs/release-evidence-policy-decision.md) | on evidence only |

**Read [which build you get](docs/quickstart.md#which-build-you-get) before you
start.** The newest published release is `v1.0.0`, which implements runtime
contract `39` — the agent control envelope, `current-control.json` and
`--format agent-boundary-json` included — and is what `pipx install
agents-shipgate` installs. It ships on the advisory channel and makes no
qualification claim. The quickstart says what an older `v0.15.0` install lacks,
and what the unqualified preview does and does not come with.

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
exists only as code — TypeScript, Go or Python registration sites; framework
tool definitions; Codex repo config; prompts, scopes, approval and confirmation
policies, prohibited actions, `shipgate.yaml`; or the CI release gate itself.

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
- **[`docs/report-schema.v1.0.json`](docs/report-schema.v1.0.json)** — the current report schema, `1.0`, frozen. Reports carry `report_schema_version: "1.0"`; `0.43` is the last pre-freeze version and stays published as a frozen reference at [`docs/report-schema.v0.43.json`](docs/report-schema.v0.43.json). What `1.x` may and may not change is in [`docs/report-1-0-contract.md`](docs/report-1-0-contract.md); the handoff, verifier, receipt, capability-lock and attestation schemas are listed with it in [`docs/INDEX.md`](docs/INDEX.md).
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
- The manifest remains `version: "0.1"` so existing configs keep working. Current reports carry `report_schema_version: "1.0"`; every narrowing decision is recorded in `surface_exclusions` and reachable by the release decision, while v0.43 remains frozen for archived reports.

## Trust Model

**Agents Shipgate does not import user code, run agents, call tools, call LLMs, connect to MCP servers, make network calls, or collect telemetry by default.**

See [Trust model](docs/trust-model.md) and [Security policy](SECURITY.md) for
the default local-only guarantees and the disclosure process.

For support channels, the proposed 1.x maintenance policy and the release duties
still requiring owner confirmation, see [Maintenance and support](MAINTAINERS.md).

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
