# Agents Shipgate Accepted Decisions

**Current index, reconciled 2026-09-08 for the v1.0 work in
[#493](https://github.com/ThreeMoonsLab/agents-shipgate/issues/493).** This file
replaces the v0.1 “Locked MVP Defaults” as contributor guidance. It links the
decisions that constrain implementation; the linked contracts retain their
schemas, rules and migration details. An accepted direction is not evidence
that every implementation obligation has shipped.

## Two adoption routes, one static boundary

**Accepted and implemented:** Route H reads supported coding-agent host grants
without a manifest. Route A verifies a repository's declared tool surface using
`shipgate.yaml`. A host inventory is not an undeclared application manifest,
and neither route proves what a live session can actually do. The current
[quickstart](quickstart.md) defines the route choice;
[host-boundary support](host-boundary-support.md) and
[minimal real configurations](minimal-real-configs.md) name the inputs each
route can establish.

This supersedes the v0.1 requirement that every operation needs a manifest.
It does not remove reviewed declarations from Route A or make an incomplete
discovery result negative evidence. The host-only discovery/adoption gap in
[#568](https://github.com/ThreeMoonsLab/agents-shipgate/issues/568) remains an
implementation obligation despite the direct host command already existing.

**Accepted:** deterministic static evidence is the default. Inputs are read
without executing target agents, importing target code, calling their tools or
asking an LLM. Supported adapters publish their coverage and extraction limits;
runtime behavior, business-risk acceptability and session-effective authority
are not inferred from those observations. The
[trust model](trust-model.md), [adapter boundary](determinism-boundary.md) and
[adapter checklist](framework-adapter-checklist.md) own that constraint.
Optional integrations do not silently expand the default evidence claim.

## One release decision; current evidence before operational authority

**Accepted and implemented:** `report.json.release_decision.decision` is the
release gate. Findings, report summaries, packets, PR prose, exit status and
capability attestations do not create competing release decisions. In
particular, advisory CI can exit successfully while review remains required,
and a baseline affects the release decision differently from the legacy
summary. [STABILITY](../STABILITY.md), the
[passed-verdict contract](passed-verdict-contract.md) and
[baseline contract](baseline.md) define those distinctions.

An agent acts on the current control state, its permissions and the exact next
action. A saved result or conversational acknowledgement cannot clear a human
route. Pointer generation, artifact integrity and live workspace/base identity
must still agree when the answer is used. The
[current agent contract](agent-contract-current.md) and
[verification identity protocol](verification-reproducibility.md) own the read
recipe; [current control](../src/agents_shipgate/core/current_control.py) and
[release assembly](../src/agents_shipgate/cli/scan/decision.py) implement it.
The compact-pointer versus receipt-only artifact obligation is tracked in
[#552](https://github.com/ThreeMoonsLab/agents-shipgate/issues/552); a successful
compact read must not be generalized into validation of every optional file.

This supersedes the v0.1 “critical-only strict CI” description as an account of
the gate. It does not change severity, baseline ownership or CI policy here.

## Declarations have owners; evidence can support a bounded draft

**Accepted and implemented:** purpose, effect/authority/binding claims and
policy exceptions are reviewed declarations. An agent may not fill unknown
answers, assert deployed wiring, lower a declaration below observed evidence,
or restamp drift to clear the question. Source-wide authority and binding blocks
have the same ownership as per-action claims. Rationale and executable rules
live in the [current agent contract](agent-contract-current.md),
[manifest contract](manifest-v0.1.md),
[autofix boundary](agent-autofix-boundary.md) and
[declaration questions](../src/agents_shipgate/core/declaration_questions.py).

The existing exception is narrow: when current control emits
`confirm_declarations`, the agent runs that exact command. It may write only
the rows the scan itself fully derived and marked `authorable_by: coding_agent`.
It supplies no missing answer and changes no human-owned authority, override,
binding or `declaration_drift` row. The resulting edit still needs fresh
verification and the existing human merge route. A drafting route is not
completion authority. This index adds no further exception.

## Capability change is the selected product promise

**Accepted direction; implementation remains incomplete:** the gate should
name which capability, scope, credential or bound changed, with evidence a
reviewer can open. The reviewer decides whether that change is acceptable in
their context. This is the owner's
[#518 principle](https://github.com/ThreeMoonsLab/agents-shipgate/issues/518),
not a new risk score or a replacement verdict introduced by this document.

- [#557](https://github.com/ThreeMoonsLab/agents-shipgate/issues/557) supplies
  dependency evidence before
  [#515](https://github.com/ThreeMoonsLab/agents-shipgate/issues/515) can change
  the default verdict scope. The first imported-guard profile shipped in
  [#599](https://github.com/ThreeMoonsLab/agents-shipgate/pull/599), with
  incomplete dependency coverage and no finding-exclusion eligibility.
  Fingerprint equality and unchanged support text are not proof that a changed
  helper cannot affect an action. Full attribution and the fixed-history
  safety measurement remain outstanding; diff-by-default is not delivered.
- [#545](https://github.com/ThreeMoonsLab/agents-shipgate/issues/545) must
  establish the prose/structure boundary before
  [#516](https://github.com/ThreeMoonsLab/agents-shipgate/issues/516) is complete.
  The accepted direction excludes unsupported judgments of prose weakening,
  while preserving structured grants, hooks, CI and malformed-input coverage.
  The current protection/routing rules are not removed by recording that
  direction here.
- [#520](https://github.com/ThreeMoonsLab/agents-shipgate/issues/520) defines
  reader abstention as a coverage miss. Named recovery evidence helps a user
  supply an input or understand a reader limit; it does not manufacture evidence
  or turn an insufficient-evidence outcome into a qualification success. See
  [qualification coverage](qualification-coverage.md).

## Compatibility and release qualification constrain the final candidate

**Accepted obligation, freeze pending:** [STABILITY](../STABILITY.md) keeps
manifest `version: "0.1"` distinct from report and runtime-contract versions.
The current report is additive-versioned; report 1.0 is not yet frozen.
[#569](https://github.com/ThreeMoonsLab/agents-shipgate/issues/569) owns that
freeze after the selected behavior converges, migration from the actual shipped
contract and supported preview, and historical artifact readability. A schema
conversion must not give an old receipt current authority. The
[distribution contract](distribution-surfaces.md) binds claims to the build
that actually supplies them.

The [approved release-evidence policy](release-evidence-policy-decision.md),
including its amendments, is the authority for the beta bar. Ordinary unit
tests, an unqualified preview or the pre-1.0 tier cannot qualify a 1.0 tag.
Human labels, independent signing and external adoption observations require
their actual evidence; a coding agent cannot supply them by declaration.
The [release runbook](release-runbook.md) owns the exact-candidate collection,
signing, rehearsal and publication procedure. Active pre-1.0 tier retirement
and historical readability must be reconciled in #569 before the final wheel.
[#572](https://github.com/ThreeMoonsLab/agents-shipgate/issues/572) and the
[roadmap](../ROADMAP.md) retain the release no-go and outstanding obligations.

**Conditional and excluded from the initial release claim:** authenticated
GitHub review/continuation still needs the independent signer boundary in
[#555](https://github.com/ThreeMoonsLab/agents-shipgate/issues/555) before
[#337](https://github.com/ThreeMoonsLab/agents-shipgate/issues/337) can deliver
that workflow. Existing [review requests](human-review-request.md),
[decision evaluation](human-review-decision.md) and PR presentation are useful
building blocks; an applicable evaluation is not persisted human approval or
merge permission. A GitHub `User` review can be agent-created. Do not describe
this integration as implemented merely because its protocols exist.

## What changed from the historical v0.1 record

The statuses below were reconciled on 2026-09-08. They are a replacement index,
not an instruction to restore the MVP defaults.

| Historical statement | Current status and replacement |
| --- | --- |
| Runtime Python 3.12; `src/agents_shipgate`; Typer | Retained foundation; the current minimum/dependencies live in [pyproject.toml](../pyproject.toml), and the contributor launcher is described in [CONTRIBUTING](../CONTRIBUTING.md). |
| Mandatory manifest | Superseded by the Route H / Route A distinction above. |
| MCP JSON and OpenAPI only; SDK enrichment optional | Superseded by the published [adapter coverage](determinism-boundary.md), including static source registrations and supported framework artifacts. |
| Critical-only strict gate | Superseded as a gate description by the single baseline-aware release decision and operational control contract above. |
| Markdown, JSON, SARIF; deterministic registry; no telemetry | Retained; [report contracts](agent-contract-current.md) and [collection policy](../README.md#adopters) describe the current outputs and collection boundary. |
| Manifest `version: "0.1"` | Retained; it is not the package or report version and must not be bumped to simulate the report freeze. |
| Policy packs and HTML deferred | Superseded: [policy packs](policy-packs.md) and [HTML evidence packets](../src/agents_shipgate/packet/html.py) are implemented. |
| Hosted dashboard, runtime gateway, deep import execution, runtime collection and LLM classification deferred | Historical deferrals confer no current implementation or release obligation. The [hosted-plane design](hosted-plane-design.md) is a proposal; default static limits remain governed by the trust model. |

Reconcile this index when an owning decision is superseded or its pending
implementation becomes the frozen contract. No ADR quota, new approval process
or blanket PR-size gate is introduced here.
