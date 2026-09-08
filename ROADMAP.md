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

### Now — build from the completed repairs and measure the remaining failure

1. **Use the repaired entry paths; keep distribution claims exact.**
   [#506](https://github.com/ThreeMoonsLab/agents-shipgate/issues/506),
   [#485](https://github.com/ThreeMoonsLab/agents-shipgate/issues/485),
   [#497](https://github.com/ThreeMoonsLab/agents-shipgate/issues/497),
   [#498](https://github.com/ThreeMoonsLab/agents-shipgate/issues/498),
   [#398](https://github.com/ThreeMoonsLab/agents-shipgate/issues/398) and
   [#484](https://github.com/ThreeMoonsLab/agents-shipgate/issues/484) are complete
   on `main`. The next bounded reader repairs are also implemented:
   [#538](https://github.com/ThreeMoonsLab/agents-shipgate/issues/538) preserves
   known ADK connection changes; [#539](https://github.com/ThreeMoonsLab/agents-shipgate/issues/539)
   corrects FastMCP signature evidence; [#533](https://github.com/ThreeMoonsLab/agents-shipgate/issues/533)
   preserves reviewed identity during adoption. These fixes do not enumerate
   remote tools, invent deployment authority or qualify a new release.

2. **Measure outcomes before treating the repairs as adoption gains.**
   [#312](https://github.com/ThreeMoonsLab/agents-shipgate/issues/312) compares
   the fixed W27 history after the behavior changes: 19 PRs across five
   repositories, the same SHAs and labels, and unchanged 1.0 safety-catch bars.
   The [W37 result](benchmark/miner/README.md#2026-w37-re-eval--the-fixed-cold-start-workflow-no-longer-reaches-verify)
   records **0/19 verifier results**, `must_block_caught` **0/2** and
   `needs_human_caught` **1/3** with legacy scan fallback. Both required 1.0
   bars fail. [#563](https://github.com/ThreeMoonsLab/agents-shipgate/issues/563)
   must establish reviewed scope; [#564](https://github.com/ThreeMoonsLab/agents-shipgate/issues/564)
   must represent new/renamed project inputs at base. These deferred repairs
   can proceed in parallel, then the same SHAs and labels must be remeasured.
   The measurement obligation is complete; the release regression is open.
   No unsafe auto-pass was observed. A lower false-alarm rate caused by fewer
   answers is not an improvement.
   The delivered [#521](https://github.com/ThreeMoonsLab/agents-shipgate/issues/521)
   pilot runbook measures a different question: whether a consenting external
   reviewer understands and acts on the result, then uses it on the next
   eligible change. Its [results ledger](docs/design-partner-pilot-results.md)
   still records zero external attempts and zero repeat-use observations.
   [ADOPTERS.md](ADOPTERS.md) remains a separate opt-in registry. Do not infer
   external value from these engineering PRs, fixture runs or a closed pilot
   implementation issue.

### Next — complete the review and make its claims trustworthy

3. **Finish the authenticated human decision loop from its delivered pieces.**
   [#536](https://github.com/ThreeMoonsLab/agents-shipgate/issues/536) publishes
   a bounded, content-bound request; [#537](https://github.com/ThreeMoonsLab/agents-shipgate/issues/537)
   validates an externally signed decision against complete current evidence
   and an external reviewer/key trust policy. [PR #556](https://github.com/ThreeMoonsLab/agents-shipgate/pull/556)
   presents the question in existing GitHub output and retains a workflow
   summary when comment publication is refused. These complete the request,
   evaluation and presentation slices, not [#337](https://github.com/ThreeMoonsLab/agents-shipgate/issues/337)
   or its parent [#504](https://github.com/ThreeMoonsLab/agents-shipgate/issues/504).
   First resolve [#555](https://github.com/ThreeMoonsLab/agents-shipgate/issues/555):
   a coding agent can post a GitHub review that appears as a `User`, so a
   review event alone cannot prove an independent human decision. Establish
   the independent signer boundary before acquisition, persistence and
   continuation in #337. Accepted decisions still do not grant merge authority
   or clear blocked, critical, gate-governing or incomplete-evidence results.
   [#293](https://github.com/ThreeMoonsLab/agents-shipgate/issues/293) independently
   needs a host willing to attest a session.

4. **Separate delivered evidence from the remaining claims.**
   [#518](https://github.com/ThreeMoonsLab/agents-shipgate/issues/518) remains
   the coordinating principle, with these concrete implementation boundaries:

   | Existing issue | Delivered slice | Next dependency; completion condition |
   |---|---|---|
   | [#515](https://github.com/ThreeMoonsLab/agents-shipgate/issues/515) | [PR #558](https://github.com/ThreeMoonsLab/agents-shipgate/pull/558) compares retained finding evidence and discloses missing support; fingerprint matches no longer imply causality in prose. | [#557](https://github.com/ThreeMoonsLab/agents-shipgate/issues/557) must prove relevant dependency coverage before a standing finding can be excluded from the default gate. |
   | [#516](https://github.com/ThreeMoonsLab/agents-shipgate/issues/516) | [PR #549](https://github.com/ThreeMoonsLab/agents-shipgate/pull/549) deprecates the path-only instruction-weakening finding while preserving its catalog compatibility. | [#545](https://github.com/ThreeMoonsLab/agents-shipgate/issues/545) covers remaining generic/prose heuristics. Structured permissions and executable hooks stay protected. |
   | [#328](https://github.com/ThreeMoonsLab/agents-shipgate/issues/328) | [PR #546](https://github.com/ThreeMoonsLab/agents-shipgate/pull/546) reports internal generator defects as product errors and preserves atomic setup writes. | [#543](https://github.com/ThreeMoonsLab/agents-shipgate/issues/543), [#547](https://github.com/ThreeMoonsLab/agents-shipgate/issues/547) and [#561](https://github.com/ThreeMoonsLab/agents-shipgate/issues/561) retain unresolved identity/input reasons and establish truthful recovery ownership. |
   | [#357](https://github.com/ThreeMoonsLab/agents-shipgate/issues/357) | [PR #560](https://github.com/ThreeMoonsLab/agents-shipgate/pull/560) distinguishes provisional effects, structural evidence and reviewed declarations across existing summaries. | Complete; other identity, binding and authority gaps remain visible. |
   | [#520](https://github.com/ThreeMoonsLab/agents-shipgate/issues/520) | #519 migrated the approved label/policy model; #538 names ADK connection changes; [PR #562](https://github.com/ThreeMoonsLab/agents-shipgate/pull/562) records per-profile actual-IE misses, unscored cases and expected-IE applicability. | #561 must distinguish product repair from supplied input using evidence. #509/#456 still own the corpus and signed qualification. |

   **Order and parallel work.** The request → decision evaluator → PR
   presentation chain has landed. Remaining chains are #555 → #337 → #504,
   #557 → #515, and #545 → #516. They can proceed independently; #561 can
   progress alongside them using the existing report evidence. The current
   implementation pass files these newly discovered gaps for later work rather
   than silently expanding each PR. Generic receipt-closure work
   [#552](https://github.com/ThreeMoonsLab/agents-shipgate/issues/552), discovery
   parity [#553](https://github.com/ThreeMoonsLab/agents-shipgate/issues/553),
   the intermittent reason mismatch [#548](https://github.com/ThreeMoonsLab/agents-shipgate/issues/548)
   and the documented temporary-path recovery [#550](https://github.com/ThreeMoonsLab/agents-shipgate/issues/550)
   remain separately tracked. None authorizes weakening a gate or inventing a
   declaration to finish the queue.

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

The approved current policy is the 38-case pre-1.0 / 80-case beta policy
implemented by [#519](https://github.com/ThreeMoonsLab/agents-shipgate/issues/519).
The historical 56/100 titles are not the live release bar. #508's labeling work
is complete; the selected inventory and candidate receipts remain
[#509](https://github.com/ThreeMoonsLab/agents-shipgate/issues/509) under
[#456](https://github.com/ThreeMoonsLab/agents-shipgate/issues/456).

Before final candidate scoring, repair
[#559](https://github.com/ThreeMoonsLab/agents-shipgate/issues/559): hash and parse
the same artifact bytes. That work can run alongside the review, attribution
and #563/#564 historical-workflow repairs. Resolve the failed catch bars
without relabeling, then freeze the candidate tree and wheel, collect matching
receipts, score against the unchanged approved policy, sign the result, and
complete the
negative release rehearsal [#510](https://github.com/ThreeMoonsLab/agents-shipgate/issues/510).
A source implementation or the #312 historical comparison is not signed
qualification. An explicit catch-rate regression remains a release blocker.
[#511](https://github.com/ThreeMoonsLab/agents-shipgate/issues/511) is non-gating
participant validation; [#512](https://github.com/ThreeMoonsLab/agents-shipgate/issues/512)
is the separate production corpus obligation under the current policy. These
chains do not prevent learning from an explicitly identified advisory preview.

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
omission. #520 records actual coverage misses without removing the shipped
`insufficient_evidence` verdict or reducing an applicable safety threshold.
Knowing a remote connection still does not establish its downstream effects
or deployed authority.
