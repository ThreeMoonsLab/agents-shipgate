from __future__ import annotations

from agents_shipgate.ci.release_decision import build_release_decision
from agents_shipgate.core.domain import Tool
from agents_shipgate.core.surface_exclusions import build_surface_exclusions
from agents_shipgate.schemas.bindings import AgentBindingGraphAssessment, BindingSurfaceDiff
from agents_shipgate.schemas.codex_plugin import CodexPluginSurface
from agents_shipgate.schemas.common import Severity
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.report import (
    BaselineSummary,
    CapabilityRuntimeEvidence,
    EvidenceGap,
    Finding,
    HeuristicsFilter,
    LoadedPolicyPack,
    PolicyAudit,
    PrivacyAudit,
    ReadinessReport,
)
from agents_shipgate.schemas.surfaces import (
    ActionSurfaceDiff,
    ActionSurfaceFacts,
    ToolSurfaceDiff,
    ToolSurfaceFacts,
)

from .agent_summary import build_agent_summary
from .summaries import (
    recommended_actions,
    summarize_findings,
    summarize_tool_surface,
    tool_inventory,
)


def build_report(
    *,
    run_id: str,
    manifest: AgentsShipgateManifest,
    project: dict[str, object] | None = None,
    agent: dict[str, object],
    environment: dict[str, object],
    tools: list[Tool],
    tool_catalog: list[Tool] | None = None,
    binding_surface_facts: AgentBindingGraphAssessment | None = None,
    binding_surface_diff: BindingSurfaceDiff | None = None,
    findings: list[Finding],
    generated_reports: dict[str, str],
    ci_mode: str,
    fail_on: list[Severity] | None = None,
    new_findings_only: bool = False,
    loaded_policy_packs: list[LoadedPolicyPack] | None = None,
    loaded_plugins: list[dict[str, object]] | None = None,
    loaded_adapters: list[dict[str, object]] | None = None,
    source_warnings: list[str] | None = None,
    api_surface: dict[str, object] | None = None,
    anthropic_surface: dict[str, object] | None = None,
    frameworks: dict[str, object] | None = None,
    codex_plugin_surface: CodexPluginSurface | None = None,
    baseline: BaselineSummary | None = None,
    manifest_dir: str | None = None,
    tool_surface_facts: ToolSurfaceFacts | None = None,
    tool_surface_diff: ToolSurfaceDiff | None = None,
    action_surface_facts: ActionSurfaceFacts | None = None,
    action_surface_diff: ActionSurfaceDiff | None = None,
    capability_runtime_evidence: CapabilityRuntimeEvidence | None = None,
    policy_audit: PolicyAudit | None = None,
    privacy_audit: PrivacyAudit | None = None,
    heuristics_filter: HeuristicsFilter | None = None,
    policy_evidence_gaps: list[EvidenceGap] | None = None,
) -> ReadinessReport:
    report = ReadinessReport(
        run_id=run_id,
        manifest_dir=manifest_dir,
        project=project or manifest.project.model_dump(exclude_none=True),
        agent=agent,
        environment=environment,
        summary=summarize_findings(findings, tools),
        tool_surface=summarize_tool_surface(tools),
        tool_surface_facts=tool_surface_facts or ToolSurfaceFacts(),
        tool_surface_diff=tool_surface_diff or ToolSurfaceDiff(),
        action_surface_facts=action_surface_facts or ActionSurfaceFacts(),
        action_surface_diff=action_surface_diff or ActionSurfaceDiff(),
        binding_surface_facts=(
            binding_surface_facts
            or AgentBindingGraphAssessment(
                root_agent_id="legacy_direct",
                status="structural",
                pass_eligible=True,
            )
        ),
        binding_surface_diff=binding_surface_diff or BindingSurfaceDiff(),
        capability_runtime_evidence=(
            capability_runtime_evidence or CapabilityRuntimeEvidence()
        ),
        api_surface=api_surface,
        anthropic_surface=anthropic_surface,
        frameworks=frameworks or {},
        codex_plugin_surface=codex_plugin_surface,
        baseline=baseline,
        findings=findings,
        recommended_actions=recommended_actions(findings),
        generated_reports=generated_reports,
        loaded_policy_packs=loaded_policy_packs or [],
        loaded_plugins=loaded_plugins or [],
        loaded_adapters=loaded_adapters or [],
        tool_inventory=tool_inventory(tools),
        tool_catalog=tool_inventory(tool_catalog if tool_catalog is not None else tools),
        source_warnings=source_warnings or [],
        policy_evidence_gaps=policy_evidence_gaps or [],
        # v0.17 (M1): policy audit envelope. Always present on emitted
        # scans (empty when no overrides applied) so consumers can read
        # ``report.policy_audit.severity_overrides_applied`` without a
        # null check.
        policy_audit=policy_audit or PolicyAudit(),
        privacy_audit=privacy_audit,
        heuristics_filter=heuristics_filter or HeuristicsFilter(),
    )
    report.release_decision = build_release_decision(
        report=report,
        tools=tools,
        tool_catalog=tool_catalog if tool_catalog is not None else tools,
        ci_mode=ci_mode,
        fail_on=fail_on,
        new_findings_only=new_findings_only,
    )
    # v0.35: built from the decision, not beside it. Accounting asks whether
    # a gap row names the excluded subject, so the gaps have to exist first —
    # and deriving it here rather than at each narrowing site is what stops
    # the ledger and the verdict from drifting apart (#403).
    report.surface_exclusions = build_surface_exclusions(report)
    # v0.12: agent_summary is the deterministic projection of
    # release_decision + per-finding agent_action. Built last so it
    # picks up everything else. The JSON report path is threaded in
    # so first_recommended_action.command names the real on-disk
    # path the user just wrote (not the default — see #57 review P1.1).
    report.agent_summary = build_agent_summary(
        findings=findings,
        release_decision=report.release_decision,
        json_report_path=generated_reports.get("json"),
        # Threaded so agent_summary can detect below-IE-threshold evidence
        # (the ratio needs the total tool count) and put evidence
        # remediation ahead of auto-apply even when an active high/critical
        # finding elevated the verdict to review_required (Phase 2c).
        tool_count=len(tools),
    )
    # v0.20 NOTE: ``report.reviewer_summary`` is NOT built here. It
    # depends on ``report.misalignments`` which ``apply_capability_diff``
    # populates AFTER ``build_report`` returns (see cli/scan/final_report.py). Building
    # it here would project from incomplete state — ``capability_misalignments``
    # would always be 0 even on reports that later carry dozens of
    # misalignments. The scan pipeline calls ``build_reviewer_summary``
    # post-capability-diff so the projection sees the final report state.
    # Test fixtures that need a populated ``reviewer_summary`` should
    # also call ``build_reviewer_summary`` after they finish assembling
    # the report.
    return report
