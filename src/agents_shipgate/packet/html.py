"""Self-contained HTML renderer for the Release Evidence Packet.

The HTML is built directly from the ``EvidencePacket`` model rather
than going through Markdown — every user-controlled string passes
through ``html.escape`` at the point of insertion, so there is no
HTML-injection surface even if a tool name or finding title contains
``<script>``-like content. We deliberately do not depend on
``markdown`` or ``bleach``; building HTML by hand is small enough that
the safer path is also the simpler one.

The output is a single ``<html>`` document with an embedded ``<style>``
block; no external assets are referenced. WeasyPrint consumes the same
HTML directly to produce ``packet.pdf``.
"""

from __future__ import annotations

from html import escape
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
    ToolSurfaceDiffSection,
    VerdictLabel,
)

_VERDICT_CLASS: dict[VerdictLabel, str] = {
    "PASSED": "verdict verdict-passed",
    "REVIEW REQUIRED": "verdict verdict-review",
    "BLOCKED": "verdict verdict-blocked",
}

_STATUS_LABEL: dict[SectionStatus, str] = {
    "covered": "covered",
    "partial": "partial",
    "not_declared": "not declared",
    "missing": "missing",
    "informational": "informational",
}

_BASE_STYLE = """
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  margin: 2rem auto;
  max-width: 60rem;
  line-height: 1.5;
  color: #111;
}
h1 { font-size: 1.6rem; margin-bottom: 0.5rem; }
h2 { font-size: 1.2rem; margin-top: 2rem; border-top: 1px solid #ddd; padding-top: 1rem; }
h3 { font-size: 1rem; margin-top: 1.5rem; }
ul { padding-left: 1.5rem; }
table { border-collapse: collapse; margin: 0.5rem 0 1rem 0; width: 100%; }
th, td { text-align: left; padding: 0.4rem 0.6rem; border: 1px solid #ddd; }
th { background: #f4f4f4; }
code { background: #f4f4f4; padding: 0.05rem 0.25rem; border-radius: 3px; }
.verdict { display: inline-block; padding: 0.3rem 0.8rem; border-radius: 4px;
  font-weight: 600; letter-spacing: 0.03em; }
.verdict-passed { background: #d4f4dd; color: #14532d; }
.verdict-review { background: #fef0c7; color: #854d0e; }
.verdict-blocked { background: #fde2e2; color: #7f1d1d; }
.status-covered { color: #14532d; }
.status-partial { color: #854d0e; }
.status-not_declared { color: #555; }
.status-missing { color: #7f1d1d; }
.status-informational { color: #555; }
.meta { color: #555; font-size: 0.92rem; }
"""


def render_packet_html(packet: EvidencePacket) -> str:
    """Return the packet rendered as a self-contained HTML document."""

    parts: list[str] = []
    parts.append("<!doctype html>")
    parts.append("<html lang=\"en\"><head>")
    parts.append("<meta charset=\"utf-8\">")
    title = escape(
        f"Release Evidence Packet — {packet.project.get('name') or 'agent'}"
    )
    parts.append(f"<title>{title}</title>")
    parts.append(f"<style>{_BASE_STYLE}</style>")
    parts.append("</head><body>")
    parts.append("<h1>Release Evidence Packet</h1>")
    parts.append(_render_header(packet))
    parts.append(_render_release_decision(packet.release_decision))
    parts.append(_render_capability_intent(packet.capability_intent))
    parts.append(_render_high_risk_surface(packet.high_risk_surface))
    parts.append(_render_tool_surface_diff(packet.tool_surface_diff))
    parts.append(_render_approval_coverage(packet.approval_coverage))
    parts.append(_render_idempotency_risk(packet.idempotency_risk))
    parts.append(_render_scope_coverage(packet.scope_coverage))
    parts.append(_render_memory_isolation(packet.memory_isolation))
    parts.append(_render_human_in_the_loop(packet.human_in_the_loop))
    parts.append(_render_dynamic_scenarios(packet.dynamic_scenarios))
    parts.append(_render_not_proven(packet.not_proven))
    parts.append("</body></html>\n")
    return "".join(parts)


def write_packet_html(packet: EvidencePacket, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_packet_html(packet), encoding="utf-8")


def _render_header(packet: EvidencePacket) -> str:
    project = escape(packet.project.get("name") or "(unnamed project)")
    agent = escape(packet.agent.get("name") or "(unnamed agent)")
    target = escape(packet.environment.get("target") or "(unspecified)")
    run_id = escape(packet.run_id)
    schema = escape(packet.packet_schema_version)
    timestamp_part = ""
    if packet.generated_at is not None:
        timestamp_part = f"Generated at: {escape(packet.generated_at)} · "
    return (
        "<p class=\"meta\">"
        f"Project: <strong>{project}</strong> · "
        f"Agent: <strong>{agent}</strong> · "
        f"Environment: <strong>{target}</strong><br>"
        f"Run id: <code>{run_id}</code> · "
        f"{timestamp_part}"
        f"Packet schema: {schema}"
        "</p>"
        "<p>This packet is a reviewer-shaped synthesis of a static "
        "Agents Shipgate scan. See §10 for what the packet does "
        "<em>not</em> prove.</p>"
    )


def _heading(number: int, title: str, status: SectionStatus) -> str:
    return (
        f"<h2>§{number} {escape(title)} — "
        f"<span class=\"status-{status}\">{_STATUS_LABEL[status]}</span></h2>"
    )


def _render_release_decision(section: ReleaseDecisionSection) -> str:
    css = _VERDICT_CLASS.get(section.verdict, "verdict verdict-review")
    parts: list[str] = []
    parts.append(
        f"<h2>§1 Release decision — "
        f"<span class=\"{css}\">{escape(section.verdict)}</span></h2>"
    )
    parts.append("<ul>")
    parts.append(f"<li>Decision: <code>{escape(section.decision)}</code></li>")
    parts.append(f"<li>Reason: {escape(section.reason)}</li>")
    parts.append(f"<li>Blockers: {len(section.blockers)}</li>")
    parts.append(f"<li>Review items: {len(section.review_items)}</li>")
    parts.append("</ul>")
    parts.append("<h3>CI gate behavior (informational)</h3>")
    parts.append("<ul>")
    parts.append(
        f"<li>ci_mode: <code>{escape(section.fail_policy.ci_mode)}</code>, "
        f"would_fail_ci: <code>{str(section.fail_policy.would_fail_ci).lower()}</code>, "
        f"exit code: <code>{section.fail_policy.exit_code}</code></li>"
    )
    parts.append(
        "<li>Note: CI behavior is metadata about the run gate, not the "
        "verdict. The verdict above derives from "
        "<code>release_decision.decision</code>.</li>"
    )
    parts.append("</ul>")
    if section.blockers:
        parts.append("<h3>Blockers</h3><ul>")
        for item in section.blockers:
            parts.append(
                f"<li><code>{escape(item.check_id)}</code> "
                f"({escape(item.severity)}): {escape(item.title)}</li>"
            )
        parts.append("</ul>")
    if section.review_items:
        parts.append("<h3>Review items</h3><ul>")
        for item in section.review_items:
            parts.append(
                f"<li><code>{escape(item.check_id)}</code> "
                f"({escape(item.severity)}): {escape(item.title)}</li>"
            )
        parts.append("</ul>")
    return "".join(parts)


def _render_capability_intent(section: CapabilityIntentDiff) -> str:
    parts = [_heading(2, "Capability ↔ Intent diff", section.status)]
    parts.append("<h3>Declared</h3><ul>")
    if section.declared_purpose:
        for purpose in section.declared_purpose:
            parts.append(f"<li>Purpose: {escape(purpose)}</li>")
    else:
        parts.append("<li>(no declared_purpose in manifest)</li>")
    for prohibited in section.prohibited_actions:
        parts.append(f"<li>Prohibited: {escape(prohibited)}</li>")
    parts.append("</ul>")
    parts.append("<h3>Observed tools</h3><ul>")
    if section.observed_tools:
        for tool in section.observed_tools:
            parts.append(f"<li><code>{escape(tool)}</code></li>")
    else:
        parts.append("<li>(no tools observed)</li>")
    parts.append("</ul>")
    if section.divergence_findings:
        parts.append("<h3>Divergences</h3><ul>")
        for item in section.divergence_findings:
            parts.append(
                f"<li><code>{escape(item.check_id)}</code>: "
                f"{escape(item.title)}</li>"
            )
        parts.append("</ul>")
    return "".join(parts)


def _render_high_risk_surface(section: HighRiskSurfaceSection) -> str:
    parts = [_heading(3, "High-risk tool surface", section.status)]
    parts.append(
        f"<p class=\"meta\">Total tools: {section.total_tools} · "
        f"High-risk: {section.high_risk_count}</p>"
    )
    if section.tools:
        parts.append(
            "<table><thead><tr><th>Tool</th><th>Source</th>"
            "<th>Risk tags</th><th>Approval</th><th>Idempotency</th>"
            "</tr></thead><tbody>"
        )
        for entry in section.tools:
            tags = escape(", ".join(entry.risk_tags) or "—")
            approval = "yes" if entry.has_approval_policy else "no"
            idem = "yes" if entry.has_idempotency_policy else "no"
            parts.append(
                f"<tr><td><code>{escape(entry.name)}</code></td>"
                f"<td>{escape(entry.source_type)}</td>"
                f"<td>{tags}</td>"
                f"<td>{approval}</td>"
                f"<td>{idem}</td></tr>"
            )
        parts.append("</tbody></table>")
    else:
        parts.append(
            "<p>No high-risk tools detected on this surface.</p>"
        )
    return "".join(parts)


def _render_tool_surface_diff(section: ToolSurfaceDiffSection) -> str:
    parts = [
        "<h2>§3A Tool-surface diff — "
        f"<span class=\"status-{section.status}\">"
        f"{_STATUS_LABEL[section.status]}</span></h2>"
    ]
    if not section.enabled:
        note = section.notes[0] if section.notes else "No comparison source was available."
        parts.append(
            f"<p>Status: disabled — {escape(note)}<br>"
            f"Base: <code>{escape(section.base_kind)}</code></p>"
        )
        return "".join(parts)
    summary = section.summary
    parts.append("<ul>")
    parts.append(f"<li>Base: <code>{escape(section.base_kind)}</code></li>")
    parts.append(
        f"<li>Tools: +{summary.tools_added}, -{summary.tools_removed}, "
        f"{summary.tools_changed} changed</li>"
    )
    parts.append(
        f"<li>Evidence gaps: {summary.new_findings} new finding(s), "
        f"{summary.resolved_findings} resolved, "
        f"{summary.accepted_debt} accepted debt</li>"
    )
    parts.append(
        f"<li>Risk/control drift: {summary.new_high_risk_effects} new "
        f"high-risk effect(s), {summary.controls_removed} removed "
        f"control(s), {summary.policy_drift_items} policy drift item(s)</li>"
    )
    parts.append("</ul>")
    if section.highlights:
        parts.append("<h3>What changed</h3><ul>")
        for item in section.highlights:
            parts.append(f"<li>{escape(item)}</li>")
        parts.append("</ul>")
    return "".join(parts)


def _coverage_table(rows, columns: list[str]) -> str:
    head = "".join(f"<th>{escape(col)}</th>" for col in columns)
    out = [
        f"<table><thead><tr>{head}</tr></thead><tbody>"
    ]
    for row in rows:
        declared = "yes" if row.declared else "no"
        source = escape(row.source or "—")
        gaps = escape(", ".join(row.gap_finding_ids) or "—")
        out.append(
            f"<tr><td><code>{escape(row.tool)}</code></td>"
            f"<td>{declared}</td>"
            f"<td>{source}</td>"
            f"<td>{gaps}</td></tr>"
        )
    out.append("</tbody></table>")
    return "".join(out)


def _render_approval_coverage(section: ApprovalCoverageSection) -> str:
    parts = [_heading(4, "Approval policy coverage", section.status)]
    if section.rows:
        parts.append(
            _coverage_table(
                section.rows, ["Tool", "Declared", "Source", "Gap finding(s)"]
            )
        )
    else:
        parts.append(
            "<p>No high-risk tools require approval policy review for "
            "this scan.</p>"
        )
    if section.gap_findings:
        parts.append("<h3>Gap findings</h3><ul>")
        for item in section.gap_findings:
            parts.append(
                f"<li><code>{escape(item.check_id)}</code> "
                f"({escape(item.severity)}): {escape(item.title)}</li>"
            )
        parts.append("</ul>")
    return "".join(parts)


def _render_idempotency_risk(section: IdempotencyRiskSection) -> str:
    parts = [_heading(5, "Idempotency / retry risk", section.status)]
    retry_label = "declared" if section.retry_policy_declared else "not declared"
    parts.append(f"<p>Retry policy: <strong>{retry_label}</strong></p>")
    if section.rows:
        parts.append(
            _coverage_table(
                section.rows, ["Tool", "Declared", "Source", "Gap finding(s)"]
            )
        )
    else:
        parts.append(
            "<p>No write-class tools require idempotency review for "
            "this scan.</p>"
        )
    if section.gap_findings:
        parts.append("<h3>Gap findings</h3><ul>")
        for item in section.gap_findings:
            parts.append(
                f"<li><code>{escape(item.check_id)}</code> "
                f"({escape(item.severity)}): {escape(item.title)}</li>"
            )
        parts.append("</ul>")
    return "".join(parts)


def _render_scope_coverage(section: ScopeCoverageSection) -> str:
    parts = [_heading(6, "Scope coverage", section.status)]
    if section.declared_scopes:
        parts.append("<h3>Declared scopes</h3><ul>")
        for scope in section.declared_scopes:
            parts.append(f"<li><code>{escape(scope)}</code></li>")
        parts.append("</ul>")
    else:
        parts.append("<p>No scopes declared in <code>permissions.scopes</code>.</p>")
    if section.rows:
        parts.append(
            "<table><thead><tr>"
            "<th>Scope</th><th>Declared</th><th>Used by tools</th>"
            "</tr></thead><tbody>"
        )
        for row in section.rows:
            declared = "yes" if row.declared else "no"
            used = (
                ", ".join(f"<code>{escape(t)}</code>" for t in row.used_by_tools)
                or "—"
            )
            parts.append(
                f"<tr><td><code>{escape(row.scope)}</code></td>"
                f"<td>{declared}</td>"
                f"<td>{used}</td></tr>"
            )
        parts.append("</tbody></table>")
    if section.unused_declared:
        parts.append("<h3>Unused declared scopes</h3><ul>")
        for scope in section.unused_declared:
            parts.append(f"<li><code>{escape(scope)}</code></li>")
        parts.append("</ul>")
    if section.missing_declared:
        parts.append("<h3>Used by tools but not declared</h3><ul>")
        for scope in section.missing_declared:
            parts.append(f"<li><code>{escape(scope)}</code></li>")
        parts.append("</ul>")
    if section.gap_findings:
        parts.append("<h3>Gap findings</h3><ul>")
        for item in section.gap_findings:
            parts.append(
                f"<li><code>{escape(item.check_id)}</code> "
                f"({escape(item.severity)}): {escape(item.title)}</li>"
            )
        parts.append("</ul>")
    return "".join(parts)


def _render_memory_isolation(section: MemoryIsolationStatus) -> str:
    return (
        _heading(7, "Memory isolation", section.status)
        + f"<p>{escape(section.notes)}</p>"
    )


def _render_human_in_the_loop(section: HumanInTheLoopEvidence) -> str:
    parts = [_heading(8, "Human-in-the-loop evidence", section.status)]
    parts.append("<ul>")
    parts.append(
        f"<li>Configured: {'yes' if section.is_configured else 'no'}</li>"
    )
    parts.append(
        "<li>Human review recommended: "
        f"{'yes' if section.human_review_recommended else 'no'}</li>"
    )
    parts.append(
        f"<li>Provenance mode: "
        f"<code>{escape(section.provenance_mode)}</code></li>"
    )
    parts.append(f"<li>{escape(section.runtime_control_disclaimer)}</li>")
    parts.append("</ul>")
    if section.approval_required_tools:
        parts.append("<h3>Approval-required tools</h3><ul>")
        for tool in section.approval_required_tools:
            parts.append(f"<li><code>{escape(tool)}</code></li>")
        parts.append("</ul>")
    if section.confirmation_required_tools:
        parts.append("<h3>Confirmation-required tools</h3><ul>")
        for tool in section.confirmation_required_tools:
            parts.append(f"<li><code>{escape(tool)}</code></li>")
        parts.append("</ul>")
    if section.trace_findings:
        parts.append("<h3>HITL evidence gaps</h3><ul>")
        for item in section.trace_findings:
            parts.append(
                f"<li><code>{escape(item.check_id)}</code> "
                f"({escape(item.severity)}): {escape(item.title)}</li>"
            )
        parts.append("</ul>")
    if section.source_provenance:
        parts.append("<h3>Source provenance</h3>")
        parts.append(
            "<table><thead><tr><th>Type</th><th>Status</th>"
            "<th>Source</th><th>Location</th><th>Detail</th>"
            "</tr></thead><tbody>"
        )
        for item in section.source_provenance:
            parts.append(
                f"<tr><td>{escape(item.type)}</td>"
                f"<td>{escape(item.status)}</td>"
                f"<td><code>{escape(item.ref)}</code></td>"
                f"<td><code>{escape(item.location)}</code></td>"
                f"<td>{escape(item.detail)}</td></tr>"
            )
        parts.append("</tbody></table>")
    elif section.provenance_mode == "unavailable":
        parts.append(
            "<p>HITL source provenance is unavailable in this packet. "
            "Re-run <code>agents-shipgate scan</code> with the source "
            "workspace for full-fidelity provenance.</p>"
        )
    if not section.is_configured and not section.human_review_recommended:
        parts.append(
            "<p>No human-in-the-loop evidence configured — see §10.</p>"
        )
    return "".join(parts)


def _render_dynamic_scenarios(section: DynamicScenariosSection) -> str:
    parts = [_heading(9, "Required dynamic scenarios", section.status)]
    if section.scenarios:
        parts.append("<ul>")
        for scenario in section.scenarios:
            parts.append(
                f"<li><strong>{escape(scenario.scenario)}</strong> — "
                f"{escape(scenario.why)}"
            )
            if scenario.finding_ids:
                ids = escape(", ".join(scenario.finding_ids))
                parts.append(
                    f"<br><span class=\"meta\">Related finding(s): {ids}</span>"
                )
            parts.append("</li>")
        parts.append("</ul>")
    else:
        parts.append(
            "<p>No additional dynamic scenarios are required from this scan.</p>"
        )
    return "".join(parts)


def _render_not_proven(section: NotProvenSection) -> str:
    parts = ["<h2>§10 What this packet did NOT prove</h2>"]
    parts.append(f"<p>{escape(section.headline)}</p>")
    parts.append("<ul>")
    for item in section.unconditional:
        parts.append(
            f"<li><strong>{escape(item.label)}.</strong> {escape(item.body)}</li>"
        )
    parts.append("</ul>")
    parts.append("<h3>Per-run residuals</h3><ul>")
    if section.source_warnings:
        parts.append("<li>Source warnings:<ul>")
        for warning in section.source_warnings:
            parts.append(f"<li>{escape(warning)}</li>")
        parts.append("</ul></li>")
    else:
        parts.append("<li>Source warnings: none</li>")
    if section.low_confidence_tools:
        names = ", ".join(
            f"<code>{escape(name)}</code>" for name in section.low_confidence_tools
        )
        parts.append(f"<li>Low-confidence tool extractions: {names}</li>")
    else:
        parts.append("<li>Low-confidence tool extractions: none</li>")
    if section.suppressed_finding_ids:
        ids = ", ".join(escape(i) for i in section.suppressed_finding_ids)
        parts.append(f"<li>Suppressed findings in effect: {ids}</li>")
    else:
        parts.append("<li>Suppressed findings in effect: none</li>")
    for note in section.additional_residuals:
        parts.append(f"<li>{escape(note)}</li>")
    parts.append("</ul>")
    return "".join(parts)
