"""Markdown renderer for the Release Evidence Packet.

Pure string building; no external dependencies. Mirrors the
``_append_*`` helper pattern from ``report/markdown.py`` so the two
renderers are easy to read side-by-side.
"""

from __future__ import annotations

from pathlib import Path

from agents_shipgate.packet.models import (
    ApprovalCoverageSection,
    CapabilityIntentDiff,
    DynamicScenariosSection,
    EvidencePacket,
    HighRiskSurfaceSection,
    HumanInTheLoopEvidence,
    IdempotencyRiskSection,
    MemoryIsolationStatus,
    NotProvenSection,
    ReleaseDecisionSection,
    ScopeCoverageSection,
    SectionStatus,
)

_STATUS_LABEL: dict[SectionStatus, str] = {
    "covered": "covered",
    "partial": "partial",
    "not_declared": "not declared",
    "missing": "missing",
    "informational": "informational",
}


def render_packet_markdown(packet: EvidencePacket) -> str:
    """Return the packet rendered as Markdown."""

    lines: list[str] = []
    _append_header(lines, packet)
    _append_release_decision(lines, packet.release_decision)
    _append_capability_intent(lines, packet.capability_intent)
    _append_high_risk_surface(lines, packet.high_risk_surface)
    _append_approval_coverage(lines, packet.approval_coverage)
    _append_idempotency_risk(lines, packet.idempotency_risk)
    _append_scope_coverage(lines, packet.scope_coverage)
    _append_memory_isolation(lines, packet.memory_isolation)
    _append_human_in_the_loop(lines, packet.human_in_the_loop)
    _append_dynamic_scenarios(lines, packet.dynamic_scenarios)
    _append_not_proven(lines, packet.not_proven)
    return "\n".join(lines).rstrip() + "\n"


def write_packet_markdown(packet: EvidencePacket, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_packet_markdown(packet), encoding="utf-8")


def _append_header(lines: list[str], packet: EvidencePacket) -> None:
    project_name = packet.project.get("name") or "(unnamed project)"
    agent_name = packet.agent.get("name") or "(unnamed agent)"
    target = packet.environment.get("target") or "(unspecified)"
    lines.extend(
        [
            "# Release Evidence Packet",
            "",
            f"- Project: {project_name}",
            f"- Agent: {agent_name}",
            f"- Environment: {target}",
            f"- Run id: {packet.run_id}",
            f"- Generated at: {packet.generated_at}",
            f"- Packet schema: {packet.packet_schema_version}",
            "",
            (
                "This packet is a reviewer-shaped synthesis of a static "
                "Agents Shipgate scan. See §10 for what the packet does "
                "*not* prove."
            ),
            "",
        ]
    )


def _section_heading(number: int, title: str, status: SectionStatus) -> str:
    return f"## §{number} {title} — {_STATUS_LABEL[status]}"


def _append_release_decision(lines: list[str], section: ReleaseDecisionSection) -> None:
    lines.append(f"## §1 Release decision — {section.verdict}")
    lines.extend(
        [
            "",
            f"- Decision: `{section.decision}`",
            f"- Reason: {section.reason}",
            f"- Blockers: {len(section.blockers)}",
            f"- Review items: {len(section.review_items)}",
            "",
            "### CI gate behavior (informational)",
            "",
            (
                f"- ci_mode: `{section.fail_policy.ci_mode}`, "
                f"would_fail_ci: `{str(section.fail_policy.would_fail_ci).lower()}`, "
                f"exit code: `{section.fail_policy.exit_code}`"
            ),
            (
                "- Note: CI behavior is metadata about the run gate, not "
                "the verdict. The verdict above derives from "
                "`release_decision.decision`."
            ),
            "",
        ]
    )
    if section.blockers:
        lines.append("### Blockers")
        lines.append("")
        for item in section.blockers:
            lines.append(
                f"- `{item.check_id}` ({item.severity}): {item.title}"
            )
        lines.append("")
    if section.review_items:
        lines.append("### Review items")
        lines.append("")
        for item in section.review_items:
            lines.append(
                f"- `{item.check_id}` ({item.severity}): {item.title}"
            )
        lines.append("")


def _append_capability_intent(lines: list[str], section: CapabilityIntentDiff) -> None:
    lines.append(_section_heading(2, "Capability ↔ Intent diff", section.status))
    lines.append("")
    lines.append("### Declared")
    lines.append("")
    if section.declared_purpose:
        for purpose in section.declared_purpose:
            lines.append(f"- Purpose: {purpose}")
    else:
        lines.append("- (no declared_purpose in manifest)")
    if section.prohibited_actions:
        for prohibited in section.prohibited_actions:
            lines.append(f"- Prohibited: {prohibited}")
    lines.append("")
    lines.append("### Observed tools")
    lines.append("")
    if section.observed_tools:
        for tool in section.observed_tools:
            lines.append(f"- {tool}")
    else:
        lines.append("- (no tools observed)")
    lines.append("")
    if section.divergence_findings:
        lines.append("### Divergences")
        lines.append("")
        for item in section.divergence_findings:
            tool = (
                f" on `{_first_tool_for_finding(section, item.id)}`"
                if item.id
                else ""
            )
            lines.append(
                f"- `{item.check_id}`{tool}: {item.title}"
            )
        lines.append("")


def _first_tool_for_finding(section: CapabilityIntentDiff, finding_id: str | None) -> str:
    # Best-effort: rows store divergent tools; we don't keep a finding→tool
    # map here, so just return the joined divergent set when available.
    return ", ".join(
        sorted({tool for row in section.rows for tool in row.divergent})
    )


def _append_high_risk_surface(lines: list[str], section: HighRiskSurfaceSection) -> None:
    lines.append(_section_heading(3, "High-risk tool surface", section.status))
    lines.append("")
    lines.append(
        f"- Total tools: {section.total_tools} · High-risk: {section.high_risk_count}"
    )
    lines.append("")
    if section.tools:
        lines.append("| Tool | Source | Risk tags | Approval | Idempotency |")
        lines.append("|---|---|---|---|---|")
        for entry in section.tools:
            tags = ", ".join(entry.risk_tags) or "—"
            approval = "yes" if entry.has_approval_policy else "no"
            idem = "yes" if entry.has_idempotency_policy else "no"
            lines.append(
                f"| `{entry.name}` | {entry.source_type} | {tags} | {approval} | {idem} |"
            )
        lines.append("")
    else:
        lines.append("- No high-risk tools detected on this surface.")
        lines.append("")


def _append_approval_coverage(
    lines: list[str], section: ApprovalCoverageSection
) -> None:
    lines.append(_section_heading(4, "Approval policy coverage", section.status))
    lines.append("")
    if section.rows:
        lines.append("| Tool | Declared | Source | Gap finding(s) |")
        lines.append("|---|---|---|---|")
        for row in section.rows:
            declared = "yes" if row.declared else "no"
            source = row.source or "—"
            gaps = ", ".join(row.gap_finding_ids) or "—"
            lines.append(f"| `{row.tool}` | {declared} | {source} | {gaps} |")
        lines.append("")
    else:
        lines.append(
            "- No high-risk tools require approval policy review for this scan."
        )
        lines.append("")
    if section.gap_findings:
        lines.append("### Gap findings")
        lines.append("")
        for item in section.gap_findings:
            lines.append(f"- `{item.check_id}` ({item.severity}): {item.title}")
        lines.append("")


def _append_idempotency_risk(
    lines: list[str], section: IdempotencyRiskSection
) -> None:
    lines.append(_section_heading(5, "Idempotency / retry risk", section.status))
    lines.append("")
    retry_label = "declared" if section.retry_policy_declared else "not declared"
    lines.append(f"- Retry policy: {retry_label}")
    lines.append("")
    if section.rows:
        lines.append("| Tool | Declared | Source | Gap finding(s) |")
        lines.append("|---|---|---|---|")
        for row in section.rows:
            declared = "yes" if row.declared else "no"
            source = row.source or "—"
            gaps = ", ".join(row.gap_finding_ids) or "—"
            lines.append(f"| `{row.tool}` | {declared} | {source} | {gaps} |")
        lines.append("")
    else:
        lines.append(
            "- No write-class tools require idempotency review for this scan."
        )
        lines.append("")
    if section.gap_findings:
        lines.append("### Gap findings")
        lines.append("")
        for item in section.gap_findings:
            lines.append(f"- `{item.check_id}` ({item.severity}): {item.title}")
        lines.append("")


def _append_scope_coverage(lines: list[str], section: ScopeCoverageSection) -> None:
    lines.append(_section_heading(6, "Scope coverage", section.status))
    lines.append("")
    if section.declared_scopes:
        lines.append("### Declared scopes")
        lines.append("")
        for scope in section.declared_scopes:
            lines.append(f"- `{scope}`")
        lines.append("")
    else:
        lines.append("- No scopes declared in `permissions.scopes`.")
        lines.append("")
    if section.rows:
        lines.append("| Scope | Declared | Used by tools |")
        lines.append("|---|---|---|")
        for row in section.rows:
            declared = "yes" if row.declared else "no"
            used = ", ".join(f"`{tool}`" for tool in row.used_by_tools) or "—"
            lines.append(f"| `{row.scope}` | {declared} | {used} |")
        lines.append("")
    if section.unused_declared:
        lines.append("### Unused declared scopes")
        lines.append("")
        for scope in section.unused_declared:
            lines.append(f"- `{scope}`")
        lines.append("")
    if section.missing_declared:
        lines.append("### Used by tools but not declared")
        lines.append("")
        for scope in section.missing_declared:
            lines.append(f"- `{scope}`")
        lines.append("")
    if section.gap_findings:
        lines.append("### Gap findings")
        lines.append("")
        for item in section.gap_findings:
            lines.append(f"- `{item.check_id}` ({item.severity}): {item.title}")
        lines.append("")


def _append_memory_isolation(lines: list[str], section: MemoryIsolationStatus) -> None:
    lines.append(_section_heading(7, "Memory isolation", section.status))
    lines.append("")
    lines.append(f"- {section.notes}")
    lines.append("")


def _append_human_in_the_loop(
    lines: list[str], section: HumanInTheLoopEvidence
) -> None:
    lines.append(_section_heading(8, "Human-in-the-loop evidence", section.status))
    lines.append("")
    lines.append(f"- Configured: {'yes' if section.is_configured else 'no'}")
    lines.append(
        "- Human review recommended: "
        f"{'yes' if section.human_review_recommended else 'no'}"
    )
    lines.append("")
    if section.approval_required_tools:
        lines.append("### Approval-required tools")
        lines.append("")
        for tool in section.approval_required_tools:
            lines.append(f"- `{tool}`")
        lines.append("")
    if section.confirmation_required_tools:
        lines.append("### Confirmation-required tools")
        lines.append("")
        for tool in section.confirmation_required_tools:
            lines.append(f"- `{tool}`")
        lines.append("")
    if section.trace_findings:
        lines.append("### Trace evidence gaps")
        lines.append("")
        for item in section.trace_findings:
            lines.append(f"- `{item.check_id}` ({item.severity}): {item.title}")
        lines.append("")
    if (
        not section.is_configured
        and not section.human_review_recommended
    ):
        lines.append(
            "- No human-in-the-loop evidence configured — see §10."
        )
        lines.append("")


def _append_dynamic_scenarios(
    lines: list[str], section: DynamicScenariosSection
) -> None:
    lines.append(_section_heading(9, "Required dynamic scenarios", section.status))
    lines.append("")
    if section.scenarios:
        for scenario in section.scenarios:
            lines.append(f"- **{scenario.scenario}** — {scenario.why}")
            if scenario.finding_ids:
                lines.append(f"  - Related finding(s): {', '.join(scenario.finding_ids)}")
        lines.append("")
    else:
        lines.append(
            "- No additional dynamic scenarios are required from this scan."
        )
        lines.append("")


def _append_not_proven(lines: list[str], section: NotProvenSection) -> None:
    lines.append("## §10 What this packet did NOT prove")
    lines.append("")
    lines.append(section.headline)
    lines.append("")
    for item in section.unconditional:
        lines.append(f"- **{item.label}.** {item.body}")
    lines.append("")
    lines.append("### Per-run residuals")
    lines.append("")
    if section.source_warnings:
        lines.append("- Source warnings:")
        for warning in section.source_warnings:
            lines.append(f"  - {warning}")
    else:
        lines.append("- Source warnings: none")
    if section.low_confidence_tools:
        lines.append(
            "- Low-confidence tool extractions: "
            + ", ".join(f"`{name}`" for name in section.low_confidence_tools)
        )
    else:
        lines.append("- Low-confidence tool extractions: none")
    if section.suppressed_finding_ids:
        lines.append(
            "- Suppressed findings in effect: "
            + ", ".join(section.suppressed_finding_ids)
        )
    else:
        lines.append("- Suppressed findings in effect: none")
    if section.additional_residuals:
        for note in section.additional_residuals:
            lines.append(f"- {note}")
    lines.append("")
