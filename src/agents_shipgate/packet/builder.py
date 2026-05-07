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

from agents_shipgate.config.schema import (
    AgentConfig,
    AgentsShipgateManifest,
    ChecksConfig,
    CiConfig,
    EnvironmentConfig,
    OutputConfig,
    PermissionsConfig,
    PoliciesConfig,
    ProjectConfig,
    RiskOverridesConfig,
)
from agents_shipgate.core.models import (
    AnthropicArtifacts,
    AuthInfo,
    Finding,
    OpenAIApiArtifacts,
    ReadinessReport,
    ReleaseDecision,
    ReleaseDecisionItem,
    Tool,
    ToolRiskHint,
    ToolSurfaceDiff,
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
    ToolSurfaceDiffSection,
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
HITL_GAP_CHECKS = (
    "SHIP-API-TRACE-APPROVAL-MISSING",
    "SHIP-API-TRACE-CONFIRMATION-MISSING",
    "SHIP-EVIDENCE-APPROVAL-TRACE-MISSING",
    "SHIP-EVIDENCE-OVERRIDE-REASON-MISSING",
    "SHIP-EVIDENCE-HIGH-RISK-EXCLUSION-MISSING",
    "SHIP-EVIDENCE-HITL-PROMOTION-CRITERIA-MISSING",
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
    tool_surface_diff: ToolSurfaceDiff | None = None,
    generated_at: str | None = None,
) -> EvidencePacket:
    """Build an ``EvidencePacket`` from in-memory scan data.

    Pure function. ``generated_at`` is intentionally NOT auto-filled:
    the packet's deterministic-artifact contract requires byte-equal
    output for byte-equal scan inputs. Callers that want a timestamp
    in the packet pass it explicitly (e.g. archival workflows); the
    default scan flow leaves it ``None`` so two scans of the same
    workspace produce identical ``packet.json`` files.
    """

    active = [f for f in findings if not f.suppressed]
    approval_declared = _approval_declared(manifest, api_artifacts, anthropic_artifacts)
    idempotency_declared = _idempotency_declared(
        manifest, api_artifacts, anthropic_artifacts
    )

    return EvidencePacket(
        generated_at=generated_at,
        run_id=run_id,
        project=project,
        agent=agent,
        environment=environment,
        release_decision=_build_release_decision(release_decision),
        capability_intent=_build_capability_intent(manifest, agent, tools, active),
        high_risk_surface=_build_high_risk_surface(
            tools, approval_declared, idempotency_declared
        ),
        tool_surface_diff=_build_tool_surface_diff(tool_surface_diff),
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


_REBUILT_FROM_REPORT_NOTE = (
    "This packet was rebuilt from report.json without the source manifest. "
    "Declared approval, idempotency, scope, and human-in-the-loop coverage "
    "in §4/§5/§6/§8 reflect only the gap findings; the manifest's declared "
    "policy is not preserved in report.json. Re-run "
    "`agents-shipgate scan` with the source workspace for a full-fidelity "
    "packet."
)


def build_packet_from_report(report: ReadinessReport) -> EvidencePacket:
    """Build a (degraded) ``EvidencePacket`` from a serialized
    ``ReadinessReport``.

    Used by ``agents-shipgate evidence-packet --from report.json`` so a
    reviewer with only a CI-archived report can still produce
    ``packet.{md,html,pdf}`` without re-running the full scan.

    The resulting packet has reduced fidelity in §4/§5/§6/§8 because
    ``report.json`` does not preserve the manifest's per-source policy
    rules (``approval_required``, ``idempotency_required``,
    ``permissions.scopes``, etc.). §10 carries an explicit note about
    the degradation so reviewers are not misled.

    The full-fidelity path is ``agents-shipgate scan``, which calls
    ``build_packet`` directly with in-memory manifest + artifacts.
    """

    if report.release_decision is None:
        raise ValueError(
            "report.json has no release_decision (only v0.8+ reports "
            "carry the release_decision block); cannot build a packet."
        )

    manifest = _stub_manifest_from_report(report)
    tools = _tools_from_inventory(report.tool_inventory)

    packet = build_packet(
        manifest=manifest,
        agent=report.agent,
        project=report.project,
        environment=report.environment,
        run_id=report.run_id,
        tools=tools,
        findings=report.findings,
        release_decision=report.release_decision,
        api_artifacts=None,
        anthropic_artifacts=None,
        source_warnings=list(report.source_warnings),
        tool_surface_diff=report.tool_surface_diff,
    )
    packet.not_proven.additional_residuals.append(_REBUILT_FROM_REPORT_NOTE)
    return packet


def _stub_manifest_from_report(report: ReadinessReport) -> AgentsShipgateManifest:
    """Construct a minimal ``AgentsShipgateManifest`` from a serialized
    report. Uses ``model_construct`` to skip Pydantic validation —
    we deliberately do not have a tool source list so the normal
    constructor would reject this manifest, but the fields the packet
    builder reads (``agent.declared_purpose`` / ``prohibited_actions``,
    empty ``policies`` / ``permissions``) are all populated correctly.
    """

    project_name = report.project.get("name") or "rebuilt-from-report"
    agent_dict = report.agent
    target = report.environment.get("target") or "local"
    return AgentsShipgateManifest.model_construct(
        version="0.1",
        project=ProjectConfig.model_construct(name=project_name),
        agent=AgentConfig.model_construct(
            name=agent_dict.get("name") or "unknown",
            declared_purpose=list(agent_dict.get("declared_purpose") or []),
            prohibited_actions=list(agent_dict.get("prohibited_actions") or []),
        ),
        environment=EnvironmentConfig.model_construct(target=target),
        tool_sources=[],
        policies=PoliciesConfig.model_construct(),
        permissions=PermissionsConfig.model_construct(scopes=[]),
        risk_overrides=RiskOverridesConfig.model_construct(tools={}),
        checks=ChecksConfig.model_construct(),
        ci=CiConfig.model_construct(),
        output=OutputConfig.model_construct(),
    )


def _tools_from_inventory(inventory: list[dict]) -> list[Tool]:
    """Reconstruct minimal ``Tool`` objects from the ``tool_inventory``
    dicts in ``report.json``.

    The inventory carries enough for §3 / §6 to function:
    ``name``, ``source_type``, ``risk_tags`` (with per-tag
    ``risk_tag_confidence``), and ``auth_scopes``. The reconstructed
    tools are not full-fidelity (no input/output schemas, no
    annotations) but ``is_high_risk_tool`` / ``risk_tags`` /
    ``tool.auth.scopes`` all work correctly.
    """

    tools: list[Tool] = []
    for item in inventory:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or ""
        if not name:
            continue
        scopes = list(item.get("auth_scopes") or [])
        tag_names = list(item.get("risk_tags") or [])
        per_tag_confidence = item.get("risk_tag_confidence") or {}
        default_confidence = item.get("confidence") or "medium"
        risk_hints = [
            ToolRiskHint.model_construct(
                tag=tag,
                source="report_inventory",
                confidence=per_tag_confidence.get(tag, default_confidence)
                if isinstance(per_tag_confidence, dict)
                else default_confidence,
                evidence={},
            )
            for tag in tag_names
        ]
        tools.append(
            Tool.model_construct(
                id=item.get("id") or name,
                name=name,
                source_type=item.get("source_type") or "unknown",
                description=None,
                source_id=None,
                source_ref=item.get("source_ref"),
                source_location=None,
                input_schema={},
                output_schema={},
                parameters=[],
                function_signature=None,
                annotations={},
                auth=AuthInfo.model_construct(
                    type=None,
                    scopes=scopes,
                    credential_mode=None,
                    source=None,
                ),
                risk_hints=risk_hints,
                owner=item.get("owner"),
                extraction_confidence=default_confidence,
                extraction={},
            )
        )
    return tools


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


def _build_tool_surface_diff(
    diff: ToolSurfaceDiff | None,
) -> ToolSurfaceDiffSection:
    if diff is None:
        return ToolSurfaceDiffSection(
            status="not_declared",
            notes=["No tool-surface diff was recorded."],
        )
    if not diff.enabled:
        return ToolSurfaceDiffSection(
            status="not_declared",
            enabled=False,
            base_kind=diff.base.kind,
            summary=diff.summary,
            notes=list(diff.notes),
        )
    return ToolSurfaceDiffSection(
        status="covered",
        enabled=True,
        base_kind=diff.base.kind,
        summary=diff.summary,
        highlights=_tool_surface_diff_highlights(diff),
        notes=list(diff.notes[:3]),
    )


def _tool_surface_diff_highlights(diff: ToolSurfaceDiff) -> list[str]:
    highlights: list[str] = []
    for item in diff.high_risk_effects:
        if item.kind == "added":
            highlights.append(f"New high-risk tag {item.tag} on {item.tool}")
    for item in diff.controls:
        if item.kind == "removed":
            highlights.append(f"Removed {item.control} for {item.tool}")
    for item in diff.tools:
        if item.kind == "added":
            highlights.append(f"Added tool {item.name}")
        elif item.kind == "removed":
            highlights.append(f"Removed tool {item.name}")
    for item in diff.policy_drift:
        highlights.append(f"{item.kind.title()} {item.policy_kind} {item.key}")
    return highlights[:5]


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

    # Per the §4 contract: only include rows for tools where Shipgate has
    # actual evidence of approval relevance — either the manifest declares
    # the tool requires approval, or a SHIP-POLICY-APPROVAL-MISSING /
    # SHIP-API-TRACE-APPROVAL-MISSING finding fired. High-risk tools that
    # need only confirmation (or no policy at all) are not approval gaps
    # and must not be reported as such.
    relevant_names = set(declared_by_source) | set(gap_by_tool)
    rows: list[ApprovalCoverageRow] = []
    seen: set[str] = set()
    for tool in sorted(tools, key=lambda t: t.name):
        if tool.name not in relevant_names:
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

    # Same rule as §4: only include rows where Shipgate has actual
    # idempotency evidence — declared or flagged. High-risk read-class
    # tools that don't need idempotency must not appear as gaps.
    relevant_names = set(declared_by_source) | set(gap_by_tool)
    rows: list[IdempotencyRow] = []
    seen: set[str] = set()
    for tool in sorted(tools, key=lambda t: t.name):
        if tool.name not in relevant_names:
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

    # ``stripe:*`` covers ``stripe:refunds:write``. Match the wildcard
    # semantics that ``checks/auth.py::_scope_covered`` uses for the
    # row-level "Declared" column.
    declared_set = set(declared)
    used_set = set(used_by_scope)

    covered_used: set[str] = set()
    for declared_scope in declared:
        for used_scope in used_set:
            if _scope_covers(declared_scope, used_scope):
                covered_used.add(used_scope)

    rows = [
        ScopeCoverageRow(
            scope=scope,
            declared=scope in declared_set or scope in covered_used,
            used_by_tools=sorted(used_by_scope.get(scope, [])),
        )
        for scope in sorted(declared_set | used_set)
    ]

    # Derive missing/unused from the *findings*, not from manifest
    # comparison. The auth and manifest-scope checks are authoritative
    # — they ran against the real manifest at scan time and recorded
    # what's missing/unused in their evidence. ``build_packet_from_report``
    # passes a stub manifest with empty ``permissions.scopes``; if §6
    # derived gaps from manifest comparison, an intentionally-empty
    # stub would invent missing-scope gaps that the original scan never
    # flagged.
    gap_findings = _findings_with_check(findings, SCOPE_GAP_CHECKS)
    missing_declared = sorted(_missing_scopes_from_findings(findings))
    unused_declared = sorted(_unused_scopes_from_findings(findings))

    if gap_findings or missing_declared:
        status: SectionStatus = "missing"
    elif declared and not unused_declared:
        status = "covered"
    elif declared and unused_declared:
        status = "partial"
    elif not declared and not used_by_scope:
        status = "informational"
    elif not declared and used_by_scope:
        # No manifest scopes (genuine or stubbed) and no findings either;
        # there's nothing actionable to report — the auth check would
        # have fired if anything were missing.
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


def _missing_scopes_from_findings(findings: list[Finding]) -> set[str]:
    """Pull the scope names flagged by SHIP-AUTH-SCOPE-COVERAGE-MISSING.

    The check records ``evidence.missing_scopes`` for every tool whose
    auth scopes are not covered by the manifest. Trusting this list
    over re-deriving it from the manifest is what lets the rebuilt-
    from-report path stay accurate when the stub manifest has empty
    ``permissions.scopes``.
    """

    out: set[str] = set()
    for finding in findings:
        if finding.check_id != "SHIP-AUTH-SCOPE-COVERAGE-MISSING":
            continue
        scopes = finding.evidence.get("missing_scopes")
        if isinstance(scopes, list):
            out.update(s for s in scopes if isinstance(s, str))
    return out


def _unused_scopes_from_findings(findings: list[Finding]) -> set[str]:
    """Pull scope names flagged by SHIP-MANIFEST-UNUSED-SCOPE.

    Each finding records the scope in ``evidence.scope``. Same
    rationale as ``_missing_scopes_from_findings``: trust the check
    over manifest re-derivation.
    """

    out: set[str] = set()
    for finding in findings:
        if finding.check_id != "SHIP-MANIFEST-UNUSED-SCOPE":
            continue
        scope = finding.evidence.get("scope")
        if isinstance(scope, str):
            out.add(scope)
    return out


def _scope_covers(declared_scope: str, required_scope: str) -> bool:
    """Mirror ``checks/auth.py::_scope_covered``: ``"*"`` covers
    everything; ``"prefix:*"`` covers any scope starting with
    ``"prefix:"``; otherwise an exact (case-insensitive) match is
    required."""

    declared = declared_scope.lower()
    required = required_scope.lower()
    if declared == "*" or declared == required:
        return True
    if declared.endswith(":*") and required.startswith(declared[:-1]):
        return True
    return False


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
    trace_findings = _findings_with_check(findings, HITL_GAP_CHECKS)
    is_configured = bool(approval_tools or confirmation_tools or manifest.validation)
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
