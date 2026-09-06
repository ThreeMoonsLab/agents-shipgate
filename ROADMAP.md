# Roadmap

> **Naming.** This project is **Agents Shipgate** (display name) / `agents-shipgate` (package, CLI, repo). See [`AGENTS.md` § Naming (canonical)](AGENTS.md#naming-canonical) for the full convention.

**Latest release: `v0.15.0`**
([release page](https://github.com/ThreeMoonsLab/agents-shipgate/releases/latest))
— the **agent-native contract cleanup** cycle. This line is checked against the
actual release tag by the `release-tag-consistency` job in
[`ci.yml`](.github/workflows/ci.yml) on every push to `main`.

## What Agents Shipgate is

Agents Shipgate is the deterministic merge gate for AI-generated agent
capability changes. When a coding agent (Codex, Claude Code, Cursor — or a
human) opens a PR that changes what an AI agent can *do*, Agents Shipgate compiles the
diff into a capability delta, evaluates it against a protected release policy,
and returns a machine-readable verdict — `merge_verdict`,
`can_merge_without_human`, `first_next_action`, `fix_task` — so the agent knows
whether to **continue, repair, or stop for human authority**.

The release gate is one decision engine: `report.json.release_decision.decision`.
The current-control envelope supplies operational permissions and the next
action; verifier, handoff, PR and report surfaces project the same decision.
Execution success is not merge authority.

## Lead wedge (focus)

Two surfaces share one engine: **(A)** tool-surface readiness for teams building
tool-using agents, and **(B)** review of repository-declared changes to a coding
agent's host configuration, MCP bindings, permissions, hooks and CI authority.
**B remains the lead adoption hypothesis.** The first users to prove it with are
developers and platform/DevEx reviewers who already encounter these changes in
PRs. Running a coding agent alone does not establish a need for another gate.
A supplies deeper capability evidence where a partner builds the tool surface.

The immediate job is concrete: **show what this PR changed, name the evidence
and coverage limits, and let the reviewer finish the decision.** The supported
host-boundary route needs no manifest; an agent-builder route can use the
existing discovery and setup flow. Neither proves runtime-effective authority
or replaces human judgment about the business consequence.

Advisory use earns the next step. First-value and repeat-use evidence must
precede asking a team to make checks mandatory. Qualified behavior, tolerable
noise and a usable human decision path are prerequisites for blocking CI;
organization-wide adoption is an outcome to demonstrate, not an installation
default. New surface is governed by the [non-goals](#explicit-non-goals) and
[`CONTRIBUTING.md`](CONTRIBUTING.md#surface-discipline).

## Direction

The next work turns existing capability evidence into a workflow a team keeps.
The sequence below is a roughly 90-day product focus, not a promise to bypass
release qualification or to complete every open issue in that window.

### Now — remove first-contact failures and observe first value

1. **Make the documented entry paths work.** P0
   [#506](https://github.com/ThreeMoonsLab/agents-shipgate/issues/506) fixes
   generated CI/install pins that name unpublished builds; P0
   [#485](https://github.com/ThreeMoonsLab/agents-shipgate/issues/485) fixes the
   zero-install detector rejecting MCP source the installed CLI already reads.
   [#497](https://github.com/ThreeMoonsLab/agents-shipgate/issues/497) makes
   release/channel/contract and detector parity a continuing invariant.
   [#498](https://github.com/ThreeMoonsLab/agents-shipgate/issues/498) provides a
   short human path to one understandable review; neither a new documentation
   host nor schema archival blocks it. The observed scoped-root defect
   [#398](https://github.com/ThreeMoonsLab/agents-shipgate/issues/398) and measured
   FastMCP gap [#484](https://github.com/ThreeMoonsLab/agents-shipgate/issues/484)
   follow the already-supported entry repairs, without expanding into a general
   adapter campaign.

2. **Measure a useful review and the next eligible change.** P1
   [#521](https://github.com/ThreeMoonsLab/agents-shipgate/issues/521) extends the
   [existing pilot](docs/design-partner-verifier-pilot.md), using consenting
   external repositories and existing feedback artifacts. Record installation
   attempts, maintainer assistance, reviewer-understood first value, an actual
   decision, and use on a second eligible change. Ten minutes to first value is
   an experiment target. Failed attempts and repositories with no second change
   remain visible. [#475](https://github.com/ThreeMoonsLab/agents-shipgate/issues/475)
   records public consenting adopters separately, in
   [ADOPTERS.md](ADOPTERS.md) — opt-in, one row per adopter, with an
   eight-rule claims policy and a dated external count published in the file
   itself rather than restated here; dogfooding, stars and downloads do not
   prove external repeat use. Pilot preparation need not wait for the
   historical-corpus outreach in
   [#511](https://github.com/ThreeMoonsLab/agents-shipgate/issues/511). The
   counts, the dated enrollment shortfall and the standing continue/narrow/stop
   record are published in
   [docs/design-partner-pilot-results.md](docs/design-partner-pilot-results.md);
   every external denominator there is currently zero, and the reason is that
   the channel to invite on was an unmade decision rather than a recruiting
   gap. The released build cannot show a host-boundary change; the unqualified
   preview can. That choice is now recorded.

### Next — complete the review and make its claims trustworthy

3. **Close one authenticated human decision loop.**
   [#338](https://github.com/ThreeMoonsLab/agents-shipgate/issues/338) now has one
   remaining workflow: [#504](https://github.com/ThreeMoonsLab/agents-shipgate/issues/504)
   specifies a bounded, eligible review-required request; then
   [#337](https://github.com/ThreeMoonsLab/agents-shipgate/issues/337) delivers it
   through existing GitHub checks/comments. Name the change, evidence, actor,
   decision and exact continuation; bind the decision to the request, head,
   policy and review set, and retain it after merge. This does not wait for
   organization-wide adoption. Recording acceptance does not clear blocked,
   critical, gate-governing or incomplete-input results. The current push-only
   authorization overlay does not grant merge authority. Host-session receipts
   [#293](https://github.com/ThreeMoonsLab/agents-shipgate/issues/293) remain
   independently blocked on a host willing to attest.

4. **Answer the changed-capability question without manufacturing certainty.**
   [#518](https://github.com/ThreeMoonsLab/agents-shipgate/issues/518) coordinates
   diff attribution [#515](https://github.com/ThreeMoonsLab/agents-shipgate/issues/515),
   removal of unsupported prose-weakening judgments
   [#516](https://github.com/ThreeMoonsLab/agents-shipgate/issues/516), and the
   reader/coverage distinction
   [#520](https://github.com/ThreeMoonsLab/agents-shipgate/issues/520).
   Diff scope retains relevant dependency evidence; it is not a changed-files-only
   shortcut. Prose exclusions retain structured permissions, MCP and executable
   hooks in the same directories. A remote binding can be named without claiming
   its downstream tools were enumerated. Shipped verdicts, deprecation rules and
   missing-input protections remain in force until their owning changes land.
   [#440](https://github.com/ThreeMoonsLab/agents-shipgate/issues/440) finishes the
   reader-facing work through error attribution
   [#328](https://github.com/ThreeMoonsLab/agents-shipgate/issues/328), provisional
   versus proven claims [#357](https://github.com/ThreeMoonsLab/agents-shipgate/issues/357),
   and #520's concrete recovery path. Reuse existing projections and safe
   mechanical fixes; no extra summary or invented semantic declaration.
   [#312](https://github.com/ThreeMoonsLab/agents-shipgate/issues/312) remeasures
   fixed real-history cases, reporting usable outcomes and coverage failures
   alongside the unchanged hard safety bars.

### Later — expand only from observed repeat use

5. **Test whether a committed inventory solves a real remaining problem.**
   [#474](https://github.com/ThreeMoonsLab/agents-shipgate/issues/474) is narrowed
   to one MCP-consuming repository and a demonstrated configuration/inventory
   gap. Reuse supported host readers and the frozen capability payload before
   adding a lock-style artifact. Generated facts cannot invent reviewed effect,
   authority or deployment-binding claims. Organization-wide state and a hosted
   control plane need evidence that this small workflow is repeatedly useful;
   they are not prerequisites for it. Keep architecture/maintenance work bounded
   to the affected paths; unrelated parser breadth, broad refactors and a new
   benchmark database do not outrank a reproducible adoption failure.

### Release evidence remains a separate obligation

The existing v0.16.0 qualification chain remains
[#508](https://github.com/ThreeMoonsLab/agents-shipgate/issues/508) (freeze labels)
→ [#509](https://github.com/ThreeMoonsLab/agents-shipgate/issues/509) (tree/wheel-bound
receipts and signed qualification), plus the negative release rehearsal
[#510](https://github.com/ThreeMoonsLab/agents-shipgate/issues/510), under
[#456](https://github.com/ThreeMoonsLab/agents-shipgate/issues/456). #520's labeling
work must be reconciled through the approved policy process; this roadmap does
not change a corpus threshold, waive a receipt or promote an unqualified preview.
#511 remains non-gating participant validation; the larger corpus
[#512](https://github.com/ThreeMoonsLab/agents-shipgate/issues/512) remains the
separate 1.0 obligation. These dependencies do not prevent learning from an
explicitly identified advisory preview.

### Build on what already exists

Current `main` contains the multi-host zero-manifest check, static preflight,
current-control routing, local review, capability diffs, GitHub Action/PR output,
SARIF, baselines, mechanical patches and opt-in feedback capture. The composing
adoption walk [#327](https://github.com/ThreeMoonsLab/agents-shipgate/issues/327)
and placeholder routing [#325](https://github.com/ThreeMoonsLab/agents-shipgate/issues/325)
are complete; the remaining distribution and human-decision problems are tracked
above rather than reopening those delivered scopes.

[Local attestations, pinned organization policy packs, evidence bundles and the
append-only local registry](docs/organization.md) are implemented, not merely
design sketches. They do not provide a hosted control plane or authenticate a
GitHub review. [Workflow evidence](docs/agent-workflow-evidence.md) and the
[governance replay corpus](benchmark/agent-pr-governance/) already supply the
feedback/replay machinery; further raw-bundle replay depends on consenting pilot
inputs. A redacted bundle without the raw diff can replay recorded invariants,
not rerun the omitted source scan.

Implemented on `main`, available in a preview and qualified in a stable release
are distinct claims. The latest stable release remains the tag stated above;
use [the distribution channel contract](docs/distribution.md) for availability.

### Explicit non-goals

- **More framework adapters is not the roadmap.** The differentiation to prove
  is trustworthy capability review and resistance to gate weakening. New
  adapters (AutoGen, Semantic Kernel, LlamaIndex, additional language surfaces)
  are accepted only when a real workflow needs one — they do not advance the
  core thesis and are not a priority. The measured #484 gap is a bounded
  adoption repair, not permission for an adapter expansion program.
- **No second release verdict.** Release-decision surfaces project
  `release_decision.decision`. Setup routing and opt-in organization audits
  answer their own scoped questions and grant no independent release authority.
- **No agent execution, LLM calls, MCP connections, network access, or scanner
  telemetry** in the default static path. Runtime inventory stays an explicit,
  trust-gated, opt-in command — never part of default CI.
- **No speculative distribution or governance platform.** A single binary,
  Docker image, IDE extension, hosted scan or runtime certification is not
  scheduled without a measured workflow failure that existing entry points
  cannot resolve. Publication of pilot or adopter evidence remains opt-in.

## Release history

Releases `v0.2` through `v0.13.0` are complete. Highlights:

- **`v0.13.0` — Agent-native protocol.** `shipgate check`; the shared
  `agent_result_v1` contract for Codex, Claude Code, and Cursor; deterministic
  policy discovery; repair-loop routing; and the read-only `shipgate.check`
  MCP adapter.
- **`v0.12.0` — Verifier cycle.** `agents-shipgate verify`; `verifier.json`
  (`merge_verdict`, `can_merge_without_human`, `first_next_action`, `fix_task`,
  `capability_review`); `pr-comment.md`; diff-aware trust-root checks
  (`SHIP-VERIFY-*`: policy-weakened, baseline/waiver-expanded, CI-gate-removed,
  agent-instructions-weakened, capability-scope-broadened); `human_ack`;
  `capability_change`; and the agent-adoption harness with
  adversarial-obedience and verify-restraint scoring.
- **`v0.8.0` — Release decision engine.** `release_decision.decision` as the
  single, baseline-aware gating signal across CLI, JSON, Markdown, PR comments,
  and Action outputs.
- **`v0.6.0`–`v0.7.0` — Agent-friendly adoption.** `detect`, auto-detecting
  `init`, `--suggest-patches` / `apply-patches`, and per-check autofix metadata
  (`autofix_safe`, `requires_human_review`).
- **`v0.3.0`–`v0.5.0` — Static framework coverage.** Google ADK,
  LangChain/LangGraph, CrewAI, SARIF output, external policy packs, and baseline
  diff mode — static-by-default throughout.
- **`v0.2` — Onboarding and CI.** `init`, `doctor`, `self-check`, fixtures,
  baseline save/apply, SBOM generation, and release signing.

## Static-by-default principles

All adapters are read-only: local file parsing only; no agent run, model call,
tool call, MCP connection, or network access. Callbacks, plugins, and guardrail
declarations are static evidence, not proof of runtime enforcement. A known
dynamic or remote binding does not prove its downstream tool inventory or side
effects. Missing coverage remains explicit and never becomes a safe result by
omission. #520 owns the proposed coverage/ground-truth distinction; until its
engine work lands, the existing warning and `insufficient_evidence` behavior
remains the implementation contract.
