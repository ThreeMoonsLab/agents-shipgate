"""Pure function ``build_packet`` — maps in-memory scan data to an
``EvidencePacket``.

The builder is the only place that knows how to read findings,
manifest config, and per-source artifacts and assemble the ten reviewer
sections. It performs no I/O and never imports renderer code, so the
JSON shape stays a stable contract independent of how the packet is
later printed.

Each section helper is small and orthogonal — passing ``findings`` is
enough; the helpers do their own filtering by ``check_id``. Suppressed
findings are excluded from §1–§9 (only §10 surfaces them).
"""

from __future__ import annotations

from datetime import UTC, datetime

from agents_shipgate.config.schema import AgentsShipgateManifest
from agents_shipgate.core.models import (
    AnthropicArtifacts,
    Finding,
    OpenAIApiArtifacts,
    ReleaseDecision,
    ReleaseDecisionItem,
    Tool,
)
from agents_shipgate.core.risk_hints import is_high_risk_tool, risk_tags
from agents_shipgate.packet.disclaimer import (
    PACKET_NON_PROOF,
    PACKET_NON_PROOF_HEADLINE,
)
from agents_shipgate.packet.models import (
    ApprovalCoverageRow,
    ApprovalCoverageSection,
    CapabilityIntentDiff,
    CapabilityIntentRow,
    DynamicScenarioRequirement,
    DynamicScenariosSection,
    EvidencePacket,
    HighRiskSurfaceSection,
    HighRiskToolEntry,
    HumanInTheLoopEvidence,
    IdempotencyRiskSection,
    IdempotencyRow,
    MemoryIsolationStatus,
    NotProvenItem,
    NotProvenSection,
    ReleaseDecisionSection,
    ScopeCoverageRow,
    ScopeCoverageSection,
    SectionStatus,
    VerdictLabel,
)

_VERDICT_BY_DECISION: dict[str, VerdictLabel] = {
    "passed": "PASSED",
    "review_required": "REVIEW REQUIRED",
    "blocked": "BLOCKED",
}

CAPABILITY_INTENT_CHECKS = (
    "SHIP-SCOPE-TOOL-OUTSIDE-PURPOSE",
    "SHIP-SCOPE-PROHIBITED-TOOL-PRESENT",
    "SHIP-API-PROMPT-TOOL-SCOPE-MISMATCH",
)
APPROVAL_GAP_CHECKS = (
    "SHIP-POLICY-APPROVAL-MISSING",
    "SHIP-API-TRACE-APPROVAL-MISSING",
)
IDEMPOTENCY_GAP_CHECKS = (
    "SHIP-API-RETRY-WITHOUT-IDEMPOTENCY",
    "SHIP-SIDEFX-IDEMPOTENCY-MISSING",
)
SCOPE_GAP_CHECKS = (
    "SHIP-AUTH-SCOPE-COVERAGE-MISSING",
    "SHIP-MANIFEST-UNUSED-SCOPE",
)
TRACE_HITL_CHECKS = (
    "SHIP-API-TRACE-APPROVAL-MISSING",
    "SHIP-API-TRACE-CONFIRMATION-MISSING",
)


def build_packet(
    *,
    manifest: AgentsShipgateManifest,
    agent: dict,
    project: dict,
    environment: dict,
    run_id: str,
    tools: list[Tool],
    findings: list[Finding],
    release_decision: ReleaseDecision,
    api_artifacts: OpenAIApiArtifacts | None,
    anthropic_artifacts: AnthropicArtifacts | None,
    source_warnings: list[str],
    generated_at: str | None = None,
) -> EvidencePacket:
    """Build an ``EvidencePacket`` from in-memory scan data.

    Pure function. Caller (``run_scan``) supplies ``generated_at`` for
    reproducible test fixtures; otherwise the current UTC timestamp is
    used.
    """

    active = [f for f in findings if not f.suppressed]
    approval_declared = _approval_declared(manifest, api_artifacts, anthropic_artifacts)
    idempotency_declared = _idempotency_declared(
        manifest, api_artifacts, anthropic_artifacts
    )

    return EvidencePacket(
        generated_at=generated_at or datetime.now(UTC).isoformat(timespec="seconds"),
        run_id=run_id,
        project=project,
        agent=agent,
        environment=environment,
        release_decision=_build_release_decision(release_decision),
        capability_intent=_build_capability_intent(manifest, agent, tools, active),
        high_risk_surface=_build_high_risk_surface(
            tools, approval_declared, idempotency_declared
        ),
        approval_coverage=_build_approval_coverage(
            manifest, api_artifacts, anthropic_artifacts, tools, active
        ),
        idempotency_risk=_build_idempotency_risk(
            manifest, api_artifacts, anthropic_artifacts, tools, active
        ),
        scope_coverage=_build_scope_coverage(manifest, tools, active),
        memory_isolation=MemoryIsolationStatus(),
        human_in_the_loop=_build_human_in_the_loop(
            release_decision, manifest, api_artifacts, anthropic_artifacts, active
        ),
        dynamic_scenarios=_build_dynamic_scenarios(release_decision, active),
        not_proven=_build_not_proven(findings, source_warnings, tools),
    )


def _build_release_decision(decision: ReleaseDecision) -> ReleaseDecisionSection:
    verdict = _VERDICT_BY_DECISION.get(decision.decision, "REVIEW REQUIRED")
    return ReleaseDecisionSection(
        decision=decision.decision,
        verdict=verdict,
        reason=decision.reason,
        blockers=list(decision.blockers),
        review_items=list(decision.review_items),
        evidence_coverage=decision.evidence_coverage,
        baseline_delta=decision.baseline_delta,
        fail_policy=decision.fail_policy,
    )


def _build_capability_intent(
    manifest: AgentsShipgateManifest,
    agent: dict,
    tools: list[Tool],
    findings: list[Finding],
) -> CapabilityIntentDiff:
    declared_purpose = list(manifest.agent.declared_purpose)
    prohibited = list(manifest.agent.prohibited_actions)
    observed_tool_names = sorted({tool.name for tool in tools})

    divergence = _findings_with_check(findings, CAPABILITY_INTENT_CHECKS)
    rows = [
        CapabilityIntentRow(
            label="Declared purpose",
            declared=declared_purpose,
            observed=observed_tool_names,
            divergent=sorted(
                {f.tool_name for f in divergence if f.tool_name}
            ),
        ),
        CapabilityIntentRow(
            label="Prohibited actions",
            declared=prohibited,
            observed=[],
            divergent=sorted(
                {
                    f.tool_name
                    for f in findings
                    if f.check_id == "SHIP-SCOPE-PROHIBITED-TOOL-PRESENT"
                    and f.tool_name
                }
            ),
        ),
    ]

    if divergence:
        status: SectionStatus = "missing"
    elif declared_purpose or prohibited:
        status = "covered"
    else:
        status = "not_declared"

    return CapabilityIntentDiff(
        status=status,
        declared_purpose=declared_purpose,
        prohibited_actions=prohibited,
        observed_tools=observed_tool_names,
        rows=rows,
        divergence_findings=_to_decision_items(divergence),
    )


def _build_high_risk_surface(
    tools: list[Tool],
    approval_declared: set[str],
    idempotency_declared: set[str],
) -> HighRiskSurfaceSection:
    entries: list[HighRiskToolEntry] = []
    for tool in tools:
        if not is_high_risk_tool(tool):
            continue
        entries.append(
            HighRiskToolEntry(
                name=tool.name,
                source_type=tool.source_type,
                risk_tags=risk_tags(tool, min_confidence="medium"),
                has_approval_policy=tool.name in approval_declared,
                has_idempotency_policy=tool.name in idempotency_declared,
            )
        )
    entries.sort(key=lambda entry: entry.name)

    if not entries:
        status: SectionStatus = "informational"
    elif all(e.has_approval_policy for e in entries):
        status = "covered"
    elif any(e.has_approval_policy for e in entries):
        status = "partial"
    else:
        status = "missing"

    return HighRiskSurfaceSection(
        status=status,
        total_tools=len(tools),
        high_risk_count=len(entries),
        tools=entries,
    )


def _build_approval_coverage(
    manifest: AgentsShipgateManifest,
    api_artifacts: OpenAIApiArtifacts | None,
    anthropic_artifacts: AnthropicArtifacts | None,
    tools: list[Tool],
    findings: list[Finding],
) -> ApprovalCoverageSection:
    declared_by_source = _declared_with_sources(
        manifest_set=manifest.policies.approval_tools(),
        api_set=(api_artifacts.approval_tools() if api_artifacts else set()),
        anthropic_set=(
            anthropic_artifacts.approval_tools() if anthropic_artifacts else set()
        ),
    )

    gap_findings = _findings_with_check(findings, APPROVAL_GAP_CHECKS)
    gap_by_tool: dict[str, list[str]] = {}
    for finding in gap_findings:
        if finding.tool_name and finding.id:
            gap_by_tool.setdefault(finding.tool_name, []).append(finding.id)

    rows: list[ApprovalCoverageRow] = []
    seen: set[str] = set()
    for tool in sorted(tools, key=lambda t: t.name):
        if not is_high_risk_tool(tool) and tool.name not in declared_by_source:
            continue
        seen.add(tool.name)
        rows.append(
            ApprovalCoverageRow(
                tool=tool.name,
                declared=tool.name in declared_by_source,
                source=declared_by_source.get(tool.name),
                gap_finding_ids=sorted(gap_by_tool.get(tool.name, [])),
            )
        )
    # Tools called out by findings but not in the tool inventory (rare;
    # belt-and-braces) still surface as rows.
    for tool_name, ids in gap_by_tool.items():
        if tool_name in seen:
            continue
        rows.append(
            ApprovalCoverageRow(
                tool=tool_name,
                declared=tool_name in declared_by_source,
                source=declared_by_source.get(tool_name),
                gap_finding_ids=sorted(ids),
            )
        )

    status = _coverage_status(rows, has_gap_findings=bool(gap_findings))
    return ApprovalCoverageSection(
        status=status,
        rows=rows,
        gap_findings=_to_decision_items(gap_findings),
    )


def _build_idempotency_risk(
    manifest: AgentsShipgateManifest,
    api_artifacts: OpenAIApiArtifacts | None,
    anthropic_artifacts: AnthropicArtifacts | None,
    tools: list[Tool],
    findings: list[Finding],
) -> IdempotencyRiskSection:
    declared_by_source = _declared_with_sources(
        manifest_set=manifest.policies.idempotency_tools(),
        api_set=(api_artifacts.idempotency_tools() if api_artifacts else set()),
        anthropic_set=(
            anthropic_artifacts.idempotency_tools() if anthropic_artifacts else set()
        ),
    )
    retry_declared = bool(
        (api_artifacts.retry_policy() if api_artifacts else None)
        or (anthropic_artifacts.retry_policy() if anthropic_artifacts else None)
    )

    gap_findings = _findings_with_check(findings, IDEMPOTENCY_GAP_CHECKS)
    gap_by_tool: dict[str, list[str]] = {}
    for finding in gap_findings:
        if finding.tool_name and finding.id:
            gap_by_tool.setdefault(finding.tool_name, []).append(finding.id)

    rows: list[IdempotencyRow] = []
    seen: set[str] = set()
    for tool in sorted(tools, key=lambda t: t.name):
        if not is_high_risk_tool(tool) and tool.name not in declared_by_source:
            continue
        seen.add(tool.name)
        rows.append(
            IdempotencyRow(
                tool=tool.name,
                declared=tool.name in declared_by_source,
                source=declared_by_source.get(tool.name),
                gap_finding_ids=sorted(gap_by_tool.get(tool.name, [])),
            )
        )
    for tool_name, ids in gap_by_tool.items():
        if tool_name in seen:
            continue
        rows.append(
            IdempotencyRow(
                tool=tool_name,
                declared=tool_name in declared_by_source,
                source=declared_by_source.get(tool_name),
                gap_finding_ids=sorted(ids),
            )
        )

    status = _coverage_status(rows, has_gap_findings=bool(gap_findings))
    return IdempotencyRiskSection(
        status=status,
        rows=rows,
        gap_findings=_to_decision_items(gap_findings),
        retry_policy_declared=retry_declared,
    )


def _build_scope_coverage(
    manifest: AgentsShipgateManifest,
    tools: list[Tool],
    findings: list[Finding],
) -> ScopeCoverageSection:
    declared = list(dict.fromkeys(manifest.permissions.scopes))

    used_by_scope: dict[str, list[str]] = {}
    for tool in tools:
        for scope in tool.auth.scopes:
            used_by_scope.setdefault(scope, []).append(tool.name)
    for scopes in used_by_scope.values():
        scopes.sort()

    rows = [
        ScopeCoverageRow(
            scope=scope,
            declared=scope in declared,
            used_by_tools=sorted(used_by_scope.get(scope, [])),
        )
        for scope in sorted(set(declared) | set(used_by_scope))
    ]
    unused_declared = sorted(scope for scope in declared if scope not in used_by_scope)
    missing_declared = sorted(
        scope for scope in used_by_scope if scope not in set(declared)
    )

    gap_findings = _findings_with_check(findings, SCOPE_GAP_CHECKS)
    if gap_findings or missing_declared:
        status: SectionStatus = "missing"
    elif declared and not unused_declared:
        status = "covered"
    elif declared and unused_declared:
        status = "partial"
    elif not declared and not used_by_scope:
        status = "informational"
    else:
        status = "not_declared"

    return ScopeCoverageSection(
        status=status,
        declared_scopes=declared,
        rows=rows,
        unused_declared=unused_declared,
        missing_declared=missing_declared,
        gap_findings=_to_decision_items(gap_findings),
    )


def _build_human_in_the_loop(
    decision: ReleaseDecision,
    manifest: AgentsShipgateManifest,
    api_artifacts: OpenAIApiArtifacts | None,
    anthropic_artifacts: AnthropicArtifacts | None,
    findings: list[Finding],
) -> HumanInTheLoopEvidence:
    approval_tools = sorted(
        manifest.policies.approval_tools()
        | (api_artifacts.approval_tools() if api_artifacts else set())
        | (anthropic_artifacts.approval_tools() if anthropic_artifacts else set())
    )
    confirmation_tools = sorted(
        manifest.policies.confirmation_tools()
        | (api_artifacts.confirmation_tools() if api_artifacts else set())
        | (
            anthropic_artifacts.confirmation_tools()
            if anthropic_artifacts
            else set()
        )
    )
    trace_findings = _findings_with_check(findings, TRACE_HITL_CHECKS)
    is_configured = bool(approval_tools or confirmation_tools)
    human_review_recommended = decision.evidence_coverage.human_review_recommended

    if not is_configured and not human_review_recommended:
        status: SectionStatus = "not_declared"
    elif trace_findings:
        status = "partial"
    elif is_configured:
        status = "covered"
    else:
        status = "informational"

    return HumanInTheLoopEvidence(
        status=status,
        is_configured=is_configured,
        human_review_recommended=human_review_recommended,
        approval_required_tools=approval_tools,
        confirmation_required_tools=confirmation_tools,
        trace_findings=_to_decision_items(trace_findings),
    )


def _build_dynamic_scenarios(
    decision: ReleaseDecision,
    findings: list[Finding],
) -> DynamicScenariosSection:
    scenarios: list[DynamicScenarioRequirement] = []

    review_findings = [f for f in findings if f.requires_human_review]
    by_check: dict[str, list[Finding]] = {}
    for finding in review_findings:
        by_check.setdefault(finding.check_id, []).append(finding)

    for check_id, group in sorted(by_check.items()):
        scenarios.append(
            DynamicScenarioRequirement(
                scenario=f"Manual review for {check_id}",
                why=group[0].recommendation
                or "Static analysis cannot close this; reviewer must verify.",
                finding_ids=sorted(f.id for f in group if f.id),
            )
        )

    if decision.evidence_coverage.source_warning_count:
        scenarios.append(
            DynamicScenarioRequirement(
                scenario="Re-run scan after resolving source warnings",
                why=(
                    "Source loaders emitted warnings; some tool surfaces "
                    "may have been parsed with reduced confidence."
                ),
            )
        )
    if decision.evidence_coverage.low_confidence_tool_count:
        scenarios.append(
            DynamicScenarioRequirement(
                scenario="Verify low-confidence tool extractions",
                why=(
                    "One or more tools were extracted with low confidence; "
                    "confirm against the upstream source before release."
                ),
            )
        )

    if not scenarios:
        status: SectionStatus = "informational"
    else:
        status = "partial"

    return DynamicScenariosSection(status=status, scenarios=scenarios)


def _build_not_proven(
    findings: list[Finding],
    source_warnings: list[str],
    tools: list[Tool],
) -> NotProvenSection:
    suppressed_ids = sorted(f.id for f in findings if f.suppressed and f.id)
    low_confidence_tools = sorted(
        tool.name for tool in tools if tool.extraction_confidence == "low"
    )
    additional = [
        "Memory isolation is not modeled by the v0.1 manifest schema; "
        "no static evidence is available."
    ]
    return NotProvenSection(
        headline=PACKET_NON_PROOF_HEADLINE,
        unconditional=[
            NotProvenItem(label=label, body=body) for label, body in PACKET_NON_PROOF
        ],
        source_warnings=list(source_warnings),
        low_confidence_tools=low_confidence_tools,
        suppressed_finding_ids=suppressed_ids,
        additional_residuals=additional,
    )


def _findings_with_check(
    findings: list[Finding], check_ids: tuple[str, ...]
) -> list[Finding]:
    targets = set(check_ids)
    return [f for f in findings if f.check_id in targets]


def _to_decision_items(findings: list[Finding]) -> list[ReleaseDecisionItem]:
    items: list[ReleaseDecisionItem] = []
    for finding in findings:
        items.append(
            ReleaseDecisionItem(
                id=finding.id,
                fingerprint=finding.fingerprint,
                check_id=finding.check_id,
                severity=finding.severity,
                title=finding.title,
                baseline_status=finding.baseline_status,
            )
        )
    return items


def _declared_with_sources(
    *,
    manifest_set: set[str],
    api_set: set[str],
    anthropic_set: set[str],
) -> dict[str, str]:
    """Return ``{tool_name: source_label}`` preferring manifest > openai > anthropic."""

    out: dict[str, str] = {}
    for name in manifest_set:
        out[name] = "policies"
    for name in api_set:
        out.setdefault(name, "openai_api")
    for name in anthropic_set:
        out.setdefault(name, "anthropic")
    return out


def _approval_declared(
    manifest: AgentsShipgateManifest,
    api_artifacts: OpenAIApiArtifacts | None,
    anthropic_artifacts: AnthropicArtifacts | None,
) -> set[str]:
    return (
        manifest.policies.approval_tools()
        | (api_artifacts.approval_tools() if api_artifacts else set())
        | (anthropic_artifacts.approval_tools() if anthropic_artifacts else set())
    )


def _idempotency_declared(
    manifest: AgentsShipgateManifest,
    api_artifacts: OpenAIApiArtifacts | None,
    anthropic_artifacts: AnthropicArtifacts | None,
) -> set[str]:
    return (
        manifest.policies.idempotency_tools()
        | (api_artifacts.idempotency_tools() if api_artifacts else set())
        | (anthropic_artifacts.idempotency_tools() if anthropic_artifacts else set())
    )


def _coverage_status(rows: list, *, has_gap_findings: bool) -> SectionStatus:
    if not rows and not has_gap_findings:
        return "informational"
    if has_gap_findings:
        if any(getattr(row, "declared", False) for row in rows):
            return "partial"
        return "missing"
    if rows and all(getattr(row, "declared", False) for row in rows):
        return "covered"
    if rows and any(getattr(row, "declared", False) for row in rows):
        return "partial"
    return "missing"
