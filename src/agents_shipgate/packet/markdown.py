"""Markdown renderer for the Release Evidence Packet.

Pure string building; no external dependencies. Mirrors the
``_append_*`` helper pattern from ``report/markdown.py`` so the two
renderers are easy to read side-by-side.

Every user-controlled string (project/agent names, tool names, finding
titles, recommendations, scope names, source warnings, etc.) passes
through ``_safe_markdown_text`` from the report renderer before being
emitted. Without this, content from tool sources could break tables or
render as HTML in Markdown viewers — and ``packet.md`` is the primary
reviewer-facing artifact.
"""

from __future__ import annotations

from pathlib import Path

from agents_shipgate.core.privacy import sanitize_packet
from agents_shipgate.core.source_warnings import group_source_warnings
from agents_shipgate.report.markdown import _safe_markdown_text
from agents_shipgate.schemas.packet import (
    ActionSurfaceDiffSection,
    ApprovalCoverageSection,
    CapabilityIntentDiff,
    DynamicScenariosSection,
    EvidenceMatrixSection,
    EvidencePacket,
    HighRiskSurfaceSection,
    HumanInTheLoopEvidence,
    IdempotencyRiskSection,
    MemoryIsolationStatus,
    NotProvenSection,
    ReleaseDecisionSection,
    ScopeCoverageSection,
    SectionStatus,
    ToolSurfaceDiffSection,
)
from agents_shipgate.schemas.report import ReleaseDecisionItem


def _escape(value: object) -> str:
    """Wrapper around ``_safe_markdown_text`` so the call site reads
    naturally (``_escape(name)`` rather than the private symbol).
    Every user-controlled string in this module flows through this
    function before reaching the rendered output."""

    return _safe_markdown_text(value)


def _escape_table_cell(value: object) -> str:
    """Markdown table cells additionally need pipe characters
    neutralized. ``_safe_markdown_text`` already escapes ``|``."""

    return _escape(value)


def _citation(source: object | None) -> str:
    """Render a ``SourceReference`` as a `` — path:line`` suffix when
    structured fields are available, otherwise the empty string.

    Used by packet §1 (Blockers / Review items) and §2 (Capability
    intent divergences) so reviewers see the originating evidence
    location inline. The path string flows through the same Markdown
    escaper as user-controlled tool names so a hostile manifest field
    cannot break tables.
    """
    if source is None:
        return ""
    path = getattr(source, "path", None)
    line = getattr(source, "start_line", None)
    if path and line is not None:
        return f" — `{_escape(f'{path}:{line}')}`"
    if path:
        pointer = getattr(source, "pointer", None)
        if pointer is not None:
            return f" — `{_escape(f'{path}#{pointer}')}`"
        return f" — `{_escape(path)}`"
    legacy = getattr(source, "location", None) or getattr(source, "ref", None)
    if legacy:
        return f" — `{_escape(legacy)}`"
    return ""


def _dual_citation(primary: object | None, secondary: object | None) -> str:
    """Render up to two source citations, deduped so renderer output
    never carries the same `path:line` twice.

    Defends against plugins (or future code paths) that populate both
    ``Finding.source`` and ``Finding.policy_evidence_source`` from the
    same manifest pointer. The primary citation always wins; the
    secondary is suppressed when it would produce a byte-equal suffix.
    """
    primary_text = _citation(primary)
    secondary_text = _citation(secondary)
    if not secondary_text or secondary_text == primary_text:
        return primary_text
    return primary_text + secondary_text


_STATUS_LABEL: dict[SectionStatus, str] = {
    "covered": "covered",
    "partial": "partial",
    "not_declared": "not declared",
    "missing": "missing",
    "informational": "informational",
}


def render_packet_markdown(
    packet: EvidencePacket,
    *,
    sanitize_output: bool = True,
) -> str:
    """Return the packet rendered as Markdown."""

    if sanitize_output:
        packet = sanitize_packet(packet)
    lines: list[str] = []
    _append_header(lines, packet)
    _append_release_decision(lines, packet.release_decision)
    _append_evidence_matrix(lines, packet.evidence_matrix)
    _append_capability_intent(lines, packet.capability_intent)
    _append_high_risk_surface(lines, packet.high_risk_surface)
    _append_tool_surface_diff(lines, packet.tool_surface_diff)
    _append_action_surface_diff(lines, packet.action_surface_diff)
    _append_approval_coverage(lines, packet.approval_coverage)
    _append_idempotency_risk(lines, packet.idempotency_risk)
    _append_scope_coverage(lines, packet.scope_coverage)
    _append_memory_isolation(lines, packet.memory_isolation)
    _append_human_in_the_loop(lines, packet.human_in_the_loop)
    _append_dynamic_scenarios(lines, packet.dynamic_scenarios)
    _append_not_proven(lines, packet.not_proven)
    return "\n".join(lines).rstrip() + "\n"


def write_packet_markdown(
    packet: EvidencePacket,
    path: Path,
    *,
    sanitize_output: bool = True,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_packet_markdown(packet, sanitize_output=sanitize_output),
        encoding="utf-8",
    )


def _append_header(lines: list[str], packet: EvidencePacket) -> None:
    project_name = packet.project.get("name") or "(unnamed project)"
    agent_name = packet.agent.get("name") or "(unnamed agent)"
    target = packet.environment.get("target") or "(unspecified)"
    lines.extend(
        [
            "# Release Evidence Packet",
            "",
            f"- Project: {_escape(project_name)}",
            f"- Agent: {_escape(agent_name)}",
            f"- Environment: {_escape(target)}",
            f"- Run id: {_escape(packet.run_id)}",
        ]
    )
    if packet.generated_at is not None:
        lines.append(f"- Generated at: {_escape(packet.generated_at)}")
    lines.extend(
        [
            f"- Packet schema: {_escape(packet.packet_schema_version)}",
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
            f"- Reason: {_escape(section.reason)}",
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
    semantic = section.evidence_coverage.semantic_coverage
    lines.extend(
        [
            "### Static semantic coverage",
            "",
            (f"- Pass-eligible actions: {semantic.pass_eligible_actions}/{semantic.total_actions}"),
            f"- Evidence gaps: {semantic.gap_count}",
            f"- Known review concerns: {semantic.review_concern_count}",
        ]
    )
    if semantic.reason_counts:
        reasons = ", ".join(
            f"{_escape(key)}={count}" for key, count in sorted(semantic.reason_counts.items())
        )
        lines.append(f"- Reasons: {reasons}")
    lines.append("")
    if section.blockers:
        lines.append("### Blockers")
        lines.append("")
        for item in section.blockers:
            # v0.19 reviewer-grade provenance: cite the tool source
            # (where the high-risk capability lives) AND, when the
            # finding fires because of a missing manifest mitigation,
            # the manifest evidence pointer. ReleaseDecisionItem.source
            # and .policy_evidence_source mirror Finding.source and
            # Finding.policy_evidence_source so the citation works for
            # packet markdown re-rendered from packet.json too.
            # ``_dual_citation`` dedupes when both pointers resolve to
            # the same ``path:line`` (e.g. agent-level findings where
            # the manifest IS the primary source).
            lines.append(
                f"- `{_escape(item.check_id)}` ({item.severity}): "
                f"{_escape(item.title)}"
                f"{_dual_citation(item.source, item.policy_evidence_source)}"
            )
        lines.append("")
    if section.review_items:
        lines.append("### Review items")
        lines.append("")
        for item in section.review_items:
            lines.append(
                f"- `{_escape(item.check_id)}` ({item.severity}): "
                f"{_escape(item.title)}"
                f"{_dual_citation(item.source, item.policy_evidence_source)}"
            )
        lines.append("")


def _append_evidence_matrix(lines: list[str], section: EvidenceMatrixSection) -> None:
    lines.append("## §1A Evidence matrix — compact review summary")
    lines.append("")
    if section.notes:
        for note in section.notes:
            lines.append(f"- {_escape(note)}")
        lines.append("")
    lines.append(
        "| Domain | Evidence present | Evidence source | Confidence | "
        "Missing controls | Blocking findings | Review items |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for row in section.rows:
        lines.append(
            f"| {_escape_table_cell(row.domain)} "
            f"| {_escape_table_cell(row.evidence_present)} "
            f"| {_escape_table_cell(_compact_text(row.evidence_source))} "
            f"| {_escape_table_cell(row.confidence)} "
            f"| {_escape_table_cell(_compact_text(row.missing_controls))} "
            f"| {_escape_table_cell(_compact_decision_items(row.blocking_findings))} "
            f"| {_escape_table_cell(_compact_decision_items(row.review_items))} |"
        )
    lines.append("")


def _compact_text(values: list[str], *, limit: int = 2) -> str:
    if not values:
        return "—"
    shown = values[:limit]
    suffix = f"; +{len(values) - limit} more" if len(values) > limit else ""
    return "; ".join(shown) + suffix


def _compact_decision_items(
    values: list[ReleaseDecisionItem],
    *,
    limit: int = 2,
) -> str:
    if not values:
        return "—"
    labels = []
    for item in values[:limit]:
        tool_suffix = ""
        if getattr(item, "baseline_status", None):
            tool_suffix = f" [{item.baseline_status}]"
        labels.append(f"{item.check_id} ({item.severity}){tool_suffix}")
    suffix = f"; +{len(values) - limit} more" if len(values) > limit else ""
    return "; ".join(labels) + suffix


def _append_capability_intent(lines: list[str], section: CapabilityIntentDiff) -> None:
    lines.append(_section_heading(2, "Capability ↔ Intent diff", section.status))
    lines.append("")
    lines.append("### Declared")
    lines.append("")
    if section.declared_purpose:
        for purpose in section.declared_purpose:
            lines.append(f"- Purpose: {_escape(purpose)}")
    else:
        lines.append("- (no declared_purpose in manifest)")
    if section.prohibited_actions:
        for prohibited in section.prohibited_actions:
            lines.append(f"- Prohibited: {_escape(prohibited)}")
    lines.append("")
    lines.append("### Observed tools")
    lines.append("")
    if section.observed_tools:
        for tool in section.observed_tools:
            lines.append(f"- {_escape(tool)}")
    else:
        lines.append("- (no tools observed)")
    lines.append("")
    if section.divergence_findings:
        lines.append("### Divergences")
        lines.append("")
        divergent_tools = sorted({tool for row in section.rows for tool in row.divergent})
        suffix = f" on `{_escape(', '.join(divergent_tools))}`" if divergent_tools else ""
        for item in section.divergence_findings:
            lines.append(
                f"- `{_escape(item.check_id)}`{suffix}: "
                f"{_escape(item.title)}"
                f"{_dual_citation(item.source, item.policy_evidence_source)}"
            )
        lines.append("")


def _append_high_risk_surface(lines: list[str], section: HighRiskSurfaceSection) -> None:
    lines.append(_section_heading(3, "High-risk tool surface", section.status))
    lines.append("")
    lines.append(f"- Total tools: {section.total_tools} · High-risk: {section.high_risk_count}")
    lines.append("")
    if section.tools:
        lines.append("| Tool | Source | Risk tags | Approval | Idempotency |")
        lines.append("|---|---|---|---|---|")
        for entry in section.tools:
            tags = _escape_table_cell(", ".join(entry.risk_tags) or "—")
            approval = "yes" if entry.has_approval_policy else "no"
            idem = "yes" if entry.has_idempotency_policy else "no"
            lines.append(
                f"| `{_escape_table_cell(entry.name)}` "
                f"| {_escape_table_cell(entry.source_type)} "
                f"| {tags} | {approval} | {idem} |"
            )
        lines.append("")
    else:
        lines.append("- No high-risk tools detected on this surface.")
        lines.append("")


def _append_tool_surface_diff(lines: list[str], section: ToolSurfaceDiffSection) -> None:
    lines.append(f"## §3A Tool-surface diff — {_STATUS_LABEL[section.status]}")
    lines.append("")
    if not section.enabled:
        note = section.notes[0] if section.notes else "No comparison source was available."
        lines.append(f"- Status: disabled — {_escape(note)}")
        lines.append(f"- Base: `{_escape(section.base_kind)}`")
        lines.append("")
        return
    summary = section.summary
    lines.append(f"- Base: `{_escape(section.base_kind)}`")
    lines.append(
        "- Tools: "
        f"+{summary.tools_added}, -{summary.tools_removed}, "
        f"{summary.tools_changed} changed"
    )
    lines.append(
        "- Evidence gaps: "
        f"{summary.new_findings} new finding(s), "
        f"{summary.resolved_findings} resolved, "
        f"{summary.accepted_debt} accepted debt"
    )
    lines.append(
        "- Risk/control drift: "
        f"{summary.new_high_risk_effects} new high-risk effect(s), "
        f"{summary.controls_removed} removed control(s), "
        f"{summary.policy_drift_items} policy drift item(s)"
    )
    if section.highlights:
        lines.append("")
        lines.append("### What changed")
        lines.append("")
        for item in section.highlights:
            lines.append(f"- {_escape(item)}")
    lines.append("")


def _append_action_surface_diff(lines: list[str], section: ActionSurfaceDiffSection) -> None:
    lines.append(f"## §3B Action-surface diff — {_STATUS_LABEL[section.status]}")
    lines.append("")
    if not section.enabled:
        note = section.notes[0] if section.notes else "No comparison source was available."
        lines.append(f"- Status: disabled — {_escape(note)}")
        lines.append(f"- Base: `{_escape(section.base_kind)}`")
        lines.append("")
        return
    summary = section.summary
    lines.append(f"- Base: `{_escape(section.base_kind)}`")
    lines.append(
        "- Actions: "
        f"+{summary.actions_added}, -{summary.actions_removed}, "
        f"{summary.actions_modified} modified"
    )
    lines.append(
        "- Escalations: "
        f"{summary.scope_expansions} scope expansion(s), "
        f"{summary.effect_escalations} effect escalation(s), "
        f"{summary.risk_tags_added} risk tag addition(s)"
    )
    lines.append(
        "- Controls: "
        f"{summary.approvals_removed} approval removal(s), "
        f"{summary.safeguards_removed} safeguard removal(s)"
    )
    lines.append(f"- Blocking action findings: {summary.blocking_findings}")
    if section.highlights:
        lines.append("")
        lines.append("### Action changes")
        lines.append("")
        for item in section.highlights:
            lines.append(f"- {_escape(item)}")
    if section.blocking_reasons:
        lines.append("")
        lines.append("### Blocking action reasons")
        lines.append("")
        for item in section.blocking_reasons:
            lines.append(f"- {_escape(item)}")
    lines.append("")


def _append_approval_coverage(lines: list[str], section: ApprovalCoverageSection) -> None:
    lines.append(_section_heading(4, "Approval policy coverage", section.status))
    lines.append("")
    if section.rows:
        lines.append("| Tool | Declared | Source | Gap finding(s) |")
        lines.append("|---|---|---|---|")
        for row in section.rows:
            declared = "yes" if row.declared else "no"
            source = _escape_table_cell(row.source or "—")
            gaps = _escape_table_cell(", ".join(row.gap_finding_ids) or "—")
            lines.append(f"| `{_escape_table_cell(row.tool)}` | {declared} | {source} | {gaps} |")
        lines.append("")
    else:
        lines.append("- No high-risk tools require approval policy review for this scan.")
        lines.append("")
    if section.gap_findings:
        lines.append("### Gap findings")
        lines.append("")
        for item in section.gap_findings:
            lines.append(f"- `{_escape(item.check_id)}` ({item.severity}): {_escape(item.title)}")
        lines.append("")


def _append_idempotency_risk(lines: list[str], section: IdempotencyRiskSection) -> None:
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
            source = _escape_table_cell(row.source or "—")
            gaps = _escape_table_cell(", ".join(row.gap_finding_ids) or "—")
            lines.append(f"| `{_escape_table_cell(row.tool)}` | {declared} | {source} | {gaps} |")
        lines.append("")
    else:
        lines.append("- No write-class tools require idempotency review for this scan.")
        lines.append("")
    if section.gap_findings:
        lines.append("### Gap findings")
        lines.append("")
        for item in section.gap_findings:
            lines.append(f"- `{_escape(item.check_id)}` ({item.severity}): {_escape(item.title)}")
        lines.append("")


def _append_scope_coverage(lines: list[str], section: ScopeCoverageSection) -> None:
    lines.append(_section_heading(6, "Scope coverage", section.status))
    lines.append("")
    if section.declared_scopes:
        lines.append("### Declared scopes")
        lines.append("")
        for scope in section.declared_scopes:
            lines.append(f"- `{_escape(scope)}`")
        lines.append("")
    else:
        lines.append("- No scopes declared in `permissions.scopes`.")
        lines.append("")
    if section.rows:
        lines.append("| Scope | Declared | Used by tools |")
        lines.append("|---|---|---|")
        for row in section.rows:
            declared = "yes" if row.declared else "no"
            used = ", ".join(f"`{_escape_table_cell(tool)}`" for tool in row.used_by_tools) or "—"
            lines.append(f"| `{_escape_table_cell(row.scope)}` | {declared} | {used} |")
        lines.append("")
    if section.unused_declared:
        lines.append("### Unused declared scopes")
        lines.append("")
        for scope in section.unused_declared:
            lines.append(f"- `{_escape(scope)}`")
        lines.append("")
    if section.missing_declared:
        lines.append("### Used by tools but not declared")
        lines.append("")
        for scope in section.missing_declared:
            lines.append(f"- `{_escape(scope)}`")
        lines.append("")
    if section.gap_findings:
        lines.append("### Gap findings")
        lines.append("")
        for item in section.gap_findings:
            lines.append(f"- `{_escape(item.check_id)}` ({item.severity}): {_escape(item.title)}")
        lines.append("")


def _append_memory_isolation(lines: list[str], section: MemoryIsolationStatus) -> None:
    lines.append(_section_heading(7, "Memory isolation", section.status))
    lines.append("")
    lines.append(f"- {_escape(section.notes)}")
    lines.append("")


def _append_human_in_the_loop(lines: list[str], section: HumanInTheLoopEvidence) -> None:
    lines.append(_section_heading(8, "Human-in-the-loop evidence", section.status))
    lines.append("")
    lines.append(f"- Configured: {'yes' if section.is_configured else 'no'}")
    lines.append(
        f"- Human review recommended: {'yes' if section.human_review_recommended else 'no'}"
    )
    lines.append(f"- Provenance mode: `{_escape(section.provenance_mode)}`")
    summary = section.capability_trace_summary
    lines.append(
        "- Capability-linked trace rows: "
        f"{summary.matched_trace_count}/{summary.trace_count} matched "
        f"({summary.source_count} source(s))"
    )
    lines.append(f"- {_escape(section.runtime_control_disclaimer)}")
    lines.append("")
    if section.approval_required_tools:
        lines.append("### Approval-required tools")
        lines.append("")
        for tool in section.approval_required_tools:
            lines.append(f"- `{_escape(tool)}`")
        lines.append("")
    if section.confirmation_required_tools:
        lines.append("### Confirmation-required tools")
        lines.append("")
        for tool in section.confirmation_required_tools:
            lines.append(f"- `{_escape(tool)}`")
        lines.append("")
    if section.trace_findings:
        lines.append("### HITL evidence gaps")
        lines.append("")
        for item in section.trace_findings:
            ref_suffix = (
                f" — trace refs: `{_escape(', '.join(item.capability_trace_refs))}`"
                if item.capability_trace_refs
                else ""
            )
            lines.append(
                f"- `{_escape(item.check_id)}` ({item.severity}): {_escape(item.title)}{ref_suffix}"
            )
        lines.append("")
    if section.capability_trace_refs:
        lines.append("### Capability trace refs")
        lines.append("")
        for ref in section.capability_trace_refs[:10]:
            lines.append(f"- `{_escape(ref)}`")
        if len(section.capability_trace_refs) > 10:
            lines.append(f"- +{len(section.capability_trace_refs) - 10} more")
        lines.append("")
    if section.source_provenance:
        lines.append("### Source provenance")
        lines.append("")
        lines.append("| Type | Status | Source | Location | Detail |")
        lines.append("|---|---|---|---|---|")
        for item in section.source_provenance:
            lines.append(
                f"| {_escape_table_cell(item.type)} "
                f"| {_escape_table_cell(item.status)} "
                f"| `{_escape_table_cell(item.ref)}` "
                f"| `{_escape_table_cell(item.location)}` "
                f"| {_escape_table_cell(item.detail)} |"
            )
        lines.append("")
    elif section.provenance_mode == "unavailable":
        lines.append(
            "- HITL source provenance is unavailable in this packet. "
            "Re-run `agents-shipgate scan` with the source workspace for "
            "full-fidelity provenance."
        )
        lines.append("")
    if not section.is_configured and not section.human_review_recommended:
        lines.append("- No human-in-the-loop evidence configured — see §10.")
        lines.append("")


def _append_dynamic_scenarios(lines: list[str], section: DynamicScenariosSection) -> None:
    lines.append(_section_heading(9, "Required dynamic scenarios", section.status))
    lines.append("")
    if section.scenarios:
        for scenario in section.scenarios:
            lines.append(f"- **{_escape(scenario.scenario)}** — {_escape(scenario.why)}")
            if scenario.finding_ids:
                ids = ", ".join(_escape(i) for i in scenario.finding_ids)
                lines.append(f"  - Related finding(s): {ids}")
        lines.append("")
    else:
        lines.append("- No additional dynamic scenarios are required from this scan.")
        lines.append("")


def _append_not_proven(lines: list[str], section: NotProvenSection) -> None:
    lines.append("## §10 What this packet did NOT prove")
    lines.append("")
    lines.append(_escape(section.headline))
    lines.append("")
    for item in section.unconditional:
        lines.append(f"- **{_escape(item.label)}.** {_escape(item.body)}")
    lines.append("")
    lines.append("### Per-run residuals")
    lines.append("")
    if section.source_warnings:
        # Grouped by mechanism for the reader; packet.json keeps the raw list
        # so the count that gates stays intact (#362).
        lines.append("- Source warnings:")
        for group in group_source_warnings(section.source_warnings):
            suffix = f" ({group.count} warnings)" if group.count > 1 else ""
            lines.append(f"  - {_escape(group.message)}{suffix}")
    else:
        lines.append("- Source warnings: none")
    if section.low_confidence_tools:
        names = ", ".join(f"`{_escape(name)}`" for name in section.low_confidence_tools)
        lines.append(f"- Low-confidence tool extractions: {names}")
    else:
        lines.append("- Low-confidence tool extractions: none")
    if section.suppressed_finding_ids:
        ids = ", ".join(_escape(i) for i in section.suppressed_finding_ids)
        lines.append(f"- Suppressed findings in effect: {ids}")
    else:
        lines.append("- Suppressed findings in effect: none")
    if section.additional_residuals:
        for note in section.additional_residuals:
            lines.append(f"- {_escape(note)}")
    lines.append("")
