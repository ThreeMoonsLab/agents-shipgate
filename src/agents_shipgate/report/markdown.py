from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from agents_shipgate.core.findings import SEVERITY_ORDER
from agents_shipgate.core.models import DeclaredIntention, Finding, ReadinessReport

DISCLAIMER = (
    "Agents Shipgate is an advisory release-readiness scanner. It does not certify "
    "agent safety or compliance. Findings are based on static configuration, declared "
    "policies, tool schemas, and optional SDK metadata. Runtime behavior, actual tool "
    "routing, and output interpretation are not verified."
)
MARKDOWN_ESCAPE_CHARS = (
    "\\",
    "`",
    "*",
    "_",
    "{",
    "}",
    "[",
    "]",
    "(",
    ")",
    "#",
    "+",
    "!",
    "|",
    "<",
    ">",
)
CAPABILITY_DIFF_MARKDOWN_LIMITS = {
    "intentions": 3,
    "capabilities": 5,
    "misalignments": 5,
    "scenarios": 5,
}


def write_markdown_report(report: ReadinessReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown_report(report), encoding="utf-8")


def render_markdown_report(report: ReadinessReport) -> str:
    lines: list[str] = []
    summary = report.summary
    lines.extend(
        [
            "# Agents Shipgate Report",
            "",
            f"Project: {_safe_markdown_text(report.project.get('name'))}",
            f"Agent: {_safe_markdown_text(report.agent.get('name'))}",
            f"Target: {_safe_markdown_text(report.environment.get('target'))}",
            "",
        ]
    )
    if "packet_md" in report.generated_reports:
        lines.extend(
            [
                "See `packet.md` for the reviewer-shaped Release Evidence Packet.",
                "",
            ]
        )
    _append_release_decision(lines, report)
    lines.extend(
        [
            "## Summary",
            "",
            f"- Critical: {summary.critical_count}",
            f"- High: {summary.high_count}",
            f"- Medium: {summary.medium_count}",
            f"- Low: {summary.low_count}",
            f"- Suppressed: {summary.suppressed_count}",
            (
                f"- Status: {_human_status(summary.status)} "
                "(legacy; see Release Decision above)"
            ),
            "",
        ]
    )
    _append_top_findings(lines, report.findings)
    _append_capability_intent_diff(lines, report)
    _append_baseline(lines, report)
    _append_recommended_actions(lines, report.recommended_actions)
    _append_source_warnings(lines, report)
    _append_loaded_policy_packs(lines, report)
    _append_loaded_plugins(lines, report)
    _append_tool_surface(lines, report)
    _append_tool_surface_diff(lines, report)
    _append_api_surface(lines, report)
    _append_frameworks(lines, report)
    _append_findings_by_category(lines, report.findings)
    _append_inventory(lines, report)
    lines.extend(["", "## Disclaimer", "", DISCLAIMER, ""])
    return "\n".join(lines)


def _append_release_decision(lines: list[str], report: ReadinessReport) -> None:
    decision = report.release_decision
    lines.extend(["## Release Decision", ""])
    if decision is None:
        lines.extend(["No release decision recorded.", ""])
        return
    lines.append(f"Decision: {decision.decision}")
    lines.append(f"Reason: {_safe_markdown_text(decision.reason)}")
    lines.append("")
    _append_decision_items(lines, "Blockers", decision.blockers)
    _append_decision_items(lines, "Review items", decision.review_items)
    ev = decision.evidence_coverage
    ev_extras: list[str] = []
    if ev.low_confidence_tool_count:
        ev_extras.append(f"{ev.low_confidence_tool_count} low-confidence tool(s)")
    if ev.source_warning_count:
        ev_extras.append(f"{ev.source_warning_count} source warning(s)")
    if ev.human_review_recommended:
        ev_extras.append("human review recommended")
    suffix = f" ({'; '.join(ev_extras)})" if ev_extras else ""
    lines.append(f"Evidence coverage: {ev.level}{suffix}")
    lines.append("")
    bd = decision.baseline_delta
    if bd.enabled:
        path = _safe_markdown_text(bd.path) if bd.path else "(unknown path)"
        lines.append(
            f"Baseline delta: enabled ({path}) — "
            f"{bd.matched_count} matched, {bd.new_count} new, "
            f"{bd.resolved_count} resolved"
        )
    else:
        lines.append("Baseline delta: not enabled")
    lines.append("")
    fp = decision.fail_policy
    fail_on_text = ", ".join(fp.fail_on) if fp.fail_on else "none"
    lines.append(
        f"Fail policy: ci_mode={fp.ci_mode}, fail_on=[{fail_on_text}], "
        f"new_findings_only={str(fp.new_findings_only).lower()}, "
        f"would_fail_ci={str(fp.would_fail_ci).lower()} "
        f"(exit {fp.exit_code})"
    )
    lines.append("")


def _append_decision_items(
    lines: list[str], label: str, items: list[object]
) -> None:
    if not items:
        lines.append(f"{label} (0): none")
        lines.append("")
        return
    lines.append(f"{label} ({len(items)}):")
    for item in items:
        # Items are ReleaseDecisionItem; reference attrs directly.
        baseline_suffix = (
            f" [{item.baseline_status}]" if item.baseline_status else ""
        )
        lines.append(
            f"- {item.severity.upper()} {_safe_markdown_text(item.check_id)}"
            f"{baseline_suffix} — {_safe_markdown_text(item.title)}"
        )
    lines.append("")


def _append_top_findings(lines: list[str], findings: list[Finding]) -> None:
    active = sorted(
        [
            finding
            for finding in findings
            if not finding.suppressed and finding.severity in {"critical", "high"}
        ],
        key=lambda finding: (SEVERITY_ORDER[finding.severity], finding.check_id),
    )
    lines.extend(["## Top Findings", ""])
    if not active:
        lines.extend(["No critical or high findings.", ""])
        return
    for index, finding in enumerate(active[:5], start=1):
        lines.append(f"{index}. {_safe_markdown_text(finding.title)}")
        lines.append(f"   Evidence: {_compact_evidence(finding.evidence)}")
        lines.append(f"   Recommendation: {_safe_markdown_text(finding.recommendation)}")
        lines.append("")


def _append_capability_intent_diff(
    lines: list[str],
    report: ReadinessReport,
) -> None:
    lines.extend(["## Capability <-> Intent Diff", ""])
    if not report.misalignments:
        lines.extend(
            [
                "No capability/intent misalignments detected from static evidence.",
                "",
            ]
        )
        return

    lines.extend(["Agent intent:", ""])
    if report.declared_intentions:
        visible_intentions, hidden_intention_count = _capability_diff_intentions(
            report.declared_intentions
        )
        for intention in visible_intentions:
            tags = ", ".join(intention.intent_tags) if intention.intent_tags else "none"
            lines.append(
                f"- {_safe_markdown_text(intention.kind)}: "
                f"{_safe_markdown_text(_truncate_text(intention.text))} "
                f"(tags: {_safe_markdown_text(tags)})"
            )
        if hidden_intention_count:
            lines.append(f"- {hidden_intention_count} more in report.json")
    else:
        lines.append("- No declared intentions captured.")
    lines.append("")

    lines.extend(["Actual capabilities:", ""])
    if report.capability_facts:
        for fact in report.capability_facts[: CAPABILITY_DIFF_MARKDOWN_LIMITS["capabilities"]]:
            tags = ", ".join(fact.risk_tags) if fact.risk_tags else "none"
            lines.append(
                f"- {_safe_markdown_text(fact.tool_name)}: "
                f"capability={_safe_markdown_text(fact.capability)}, "
                f"risk={_safe_markdown_text(tags)}, "
                f"control={_safe_markdown_text(fact.control_status)}"
            )
        _append_more_line(
            lines,
            len(report.capability_facts),
            CAPABILITY_DIFF_MARKDOWN_LIMITS["capabilities"],
        )
    else:
        lines.append("- No high-risk or gap-referenced capabilities selected.")
    lines.append("")

    lines.extend(["Policy/control gaps:", ""])
    for misalignment in report.misalignments[: CAPABILITY_DIFF_MARKDOWN_LIMITS["misalignments"]]:
        tool = f" [{misalignment.tool_name}]" if misalignment.tool_name else ""
        lines.append(
            f"- {misalignment.severity.upper()} {_safe_markdown_text(misalignment.kind)}"
            f"{_safe_markdown_text(tool)}: {_safe_markdown_text(misalignment.gap)}"
        )
        lines.append(
            "  Requires: "
            f"{_safe_markdown_text(misalignment.policy_requirement)}"
        )
        lines.append(
            "  Release implication: "
            f"{_safe_markdown_text(misalignment.release_implication)}"
        )
    _append_more_line(
        lines,
        len(report.misalignments),
        CAPABILITY_DIFF_MARKDOWN_LIMITS["misalignments"],
    )
    lines.append("")

    lines.extend(["Release implication:", ""])
    consequence = report.release_consequence
    if consequence is None:
        lines.append("- No release consequence recorded.")
    else:
        lines.append(f"- Decision: {_safe_markdown_text(consequence.decision)}")
        lines.append(f"- {_safe_markdown_text(consequence.summary)}")
    lines.append("")

    lines.extend(["Next validation:", ""])
    if report.suggested_scenarios:
        for scenario in report.suggested_scenarios[: CAPABILITY_DIFF_MARKDOWN_LIMITS["scenarios"]]:
            lines.append(
                f"- {_safe_markdown_text(scenario.title)}: "
                f"{_safe_markdown_text(scenario.expected_control)}"
            )
        _append_more_line(
            lines,
            len(report.suggested_scenarios),
            CAPABILITY_DIFF_MARKDOWN_LIMITS["scenarios"],
        )
    else:
        lines.append("- No additional validation scenarios suggested.")
    lines.append("")


def _append_more_line(lines: list[str], total: int, limit: int) -> None:
    if total > limit:
        lines.append(f"- {total - limit} more in report.json")


def _capability_diff_intentions(
    intentions: list[DeclaredIntention],
) -> tuple[list[DeclaredIntention], int]:
    prohibited = [item for item in intentions if item.kind == "prohibited_action"]
    instruction_preview = [item for item in intentions if item.kind == "instruction_preview"]
    declared = [item for item in intentions if item.kind == "declared_purpose"]
    declared_limit = CAPABILITY_DIFF_MARKDOWN_LIMITS["intentions"]
    visible = prohibited + declared[:declared_limit] + instruction_preview
    hidden = max(0, len(declared) - declared_limit)
    return visible, hidden


def _append_recommended_actions(lines: list[str], actions: list[str]) -> None:
    lines.extend(["## Recommended Next Actions", ""])
    if not actions:
        lines.extend(["No action required from static findings.", ""])
        return
    for action in actions:
        lines.append(f"- {_safe_markdown_text(action)}")
    lines.append("")


def _append_baseline(lines: list[str], report: ReadinessReport) -> None:
    if not report.baseline:
        return
    baseline = report.baseline
    lines.extend(
        [
            "## Baseline",
            "",
            f"- Path: {_safe_markdown_text(baseline.path)}",
            f"- Matched findings: {baseline.matched_count}",
            f"- New findings: {baseline.new_count}",
            f"- Resolved findings: {baseline.resolved_count}",
            "",
        ]
    )


def _append_source_warnings(lines: list[str], report: ReadinessReport) -> None:
    if not report.source_warnings:
        return
    lines.extend(["## Source Warnings", ""])
    for warning in report.source_warnings:
        lines.append(f"- {_safe_markdown_text(warning)}")
    lines.append("")


def _append_loaded_plugins(lines: list[str], report: ReadinessReport) -> None:
    if not report.loaded_plugins:
        return
    lines.extend(["## Loaded Plugins", ""])
    for plugin in report.loaded_plugins:
        distribution = plugin.get("distribution") or "unknown distribution"
        version = plugin.get("version")
        check_id = plugin.get("check_id") or "unknown check"
        suffix = f" {version}" if version else ""
        lines.append(
            f"- {_safe_markdown_text(distribution)}{_safe_markdown_text(suffix)}: "
            f"{_safe_markdown_text(check_id)}"
        )
    lines.append("")


def _append_loaded_policy_packs(lines: list[str], report: ReadinessReport) -> None:
    if not report.loaded_policy_packs:
        return
    lines.extend(["## Loaded Policy Packs", ""])
    for pack in report.loaded_policy_packs:
        version = f" {pack.version}" if pack.version else ""
        lines.append(
            f"- {_safe_markdown_text(pack.name)}{_safe_markdown_text(version)} "
            f"({_safe_markdown_text(pack.id)}): {pack.rule_count} rules"
        )
    lines.append("")


def _append_tool_surface(lines: list[str], report: ReadinessReport) -> None:
    surface = report.tool_surface
    lines.extend(
        [
            "## Tool Surface Summary",
            "",
            f"- Total tools: {surface.total_tools}",
            f"- High-risk tools: {surface.high_risk_tools}",
            f"- Wildcard tools: {surface.wildcard_tools}",
            f"- Missing descriptions: {surface.missing_descriptions}",
            f"- Sources: {', '.join(f'{key}={value}' for key, value in surface.sources.items()) or 'none'}",
            "",
        ]
    )


def _append_tool_surface_diff(lines: list[str], report: ReadinessReport) -> None:
    diff = report.tool_surface_diff
    lines.extend(["## Tool Surface Diff", ""])
    if not diff.enabled:
        note = diff.notes[0] if diff.notes else "No comparison source was available."
        lines.extend(
            [
                f"- Status: disabled - {_safe_markdown_text(note)}",
                f"- Base: {_safe_markdown_text(diff.base.kind)}",
            ]
        )
        if (
            diff.summary.new_findings
            or diff.summary.resolved_findings
            or diff.summary.accepted_debt
        ):
            lines.append(
                "- Finding deltas: "
                f"{diff.summary.new_findings} new, "
                f"{diff.summary.resolved_findings} resolved, "
                f"{diff.summary.accepted_debt} accepted debt"
            )
        lines.append("")
        return

    summary = diff.summary
    lines.extend(
        [
            f"- Base: {_safe_markdown_text(diff.base.kind)}"
            + (f" ({_safe_markdown_text(diff.base.path)})" if diff.base.path else ""),
            (
                "- Tools: "
                f"+{summary.tools_added}, -{summary.tools_removed}, "
                f"{summary.tools_changed} changed"
            ),
            (
                "- Scopes: "
                f"+{summary.new_scopes}, -{summary.removed_scopes}; "
                f"{summary.new_high_risk_effects} new high-risk effect(s)"
            ),
            (
                "- Controls: "
                f"+{summary.controls_added}, -{summary.controls_removed}; "
                f"{summary.policy_drift_items} policy drift item(s)"
            ),
            (
                "- Findings: "
                f"{summary.new_findings} new, "
                f"{summary.resolved_findings} resolved, "
                f"{summary.unchanged_findings} unchanged, "
                f"{summary.accepted_debt} accepted debt"
            ),
            "",
        ]
    )
    _append_diff_values(
        lines,
        "Added tools",
        [item.name for item in diff.tools if item.kind == "added"],
    )
    _append_diff_values(
        lines,
        "Changed tools",
        [item.name for item in diff.tools if item.kind == "changed"],
    )
    _append_diff_values(
        lines,
        "New high-risk effects",
        [
            f"{item.tool}: {item.tag}"
            for item in diff.high_risk_effects
            if item.kind == "added"
        ],
    )
    _append_diff_values(
        lines,
        "Removed controls",
        [
            f"{item.tool}: {item.control}"
            for item in diff.controls
            if item.kind == "removed"
        ],
    )
    _append_diff_values(
        lines,
        "Policy drift",
        [f"{item.kind} {item.policy_kind} {item.key}" for item in diff.policy_drift],
    )
    if diff.notes:
        _append_diff_values(lines, "Notes", diff.notes[:3])


def _append_diff_values(lines: list[str], label: str, values: list[str]) -> None:
    if not values:
        return
    lines.extend([f"{label}:", ""])
    for value in values[:5]:
        lines.append(f"- {_safe_markdown_text(value)}")
    if len(values) > 5:
        lines.append(f"- {len(values) - 5} more in report.json")
    lines.append("")


def _append_api_surface(lines: list[str], report: ReadinessReport) -> None:
    if not report.api_surface:
        return
    surface = report.api_surface
    lines.extend(
        [
            "## OpenAI API Surface Summary",
            "",
            f"- Prompt files: {surface.get('prompt_file_count', 0)}",
            f"- Tool files: {surface.get('tool_file_count', 0)}",
            f"- Response formats: {surface.get('response_format_count', 0)}",
            f"- Model config present: {surface.get('model_config_present', False)}",
            f"- Test cases: {surface.get('test_case_count', 0)}",
            f"- Trace samples: {surface.get('trace_sample_count', 0)}",
            f"- Policy rule files: {surface.get('policy_rule_count', 0)}",
            "",
        ]
    )


def _append_frameworks(lines: list[str], report: ReadinessReport) -> None:
    if not report.frameworks:
        return
    specs = [
        (
            "google_adk",
            "Google ADK",
            "## Google ADK Surface Summary",
            [
                ("Python entrypoints", "python_entrypoint_count"),
                ("Agent config files", "agent_config_count"),
                ("Agents", "agent_count"),
                ("Function tools", "function_tool_count"),
                ("Long-running tools", "long_running_tool_count"),
                ("Toolsets", "toolset_count"),
                ("Dynamic or unresolved toolsets", "dynamic_toolset_count"),
                ("Callbacks", "callback_count"),
                ("Plugins", "plugin_count"),
                ("Eval files", "eval_file_count"),
            ],
        ),
        (
            "langchain",
            "LangChain",
            "## LangChain Surface Summary",
            [
                ("Python entrypoints", "python_entrypoint_count"),
                ("Function tools", "function_tool_count"),
                ("Structured tools", "structured_tool_count"),
                ("Tool nodes", "tool_node_count"),
                ("Agent tool bindings", "agent_tool_binding_count"),
                ("Dynamic or unresolved tool surfaces", "dynamic_tool_surface_count"),
                ("Tool inventory files", "tool_inventory_file_count"),
            ],
        ),
        (
            "crewai",
            "CrewAI",
            "## CrewAI Surface Summary",
            [
                ("Python entrypoints", "python_entrypoint_count"),
                ("Agents", "agent_count"),
                ("Crews", "crew_count"),
                ("Function tools", "function_tool_count"),
                ("Class tools", "class_tool_count"),
                ("Prebuilt tools", "prebuilt_tool_count"),
                ("Dynamic or unresolved tool surfaces", "dynamic_tool_surface_count"),
                ("Tool inventory files", "tool_inventory_file_count"),
            ],
        ),
    ]
    for framework_key, label, title, fields in specs:
        surface = report.frameworks.get(framework_key)
        if not isinstance(surface, dict):
            continue
        lines.extend([title, ""])
        for field_label, field_name in fields:
            lines.append(f"- {field_label}: {surface.get(field_name, 0)}")
        lines.append("")
        warnings = surface.get("warnings")
        if isinstance(warnings, list) and warnings:
            lines.extend([f"{label} warnings:", ""])
            for warning in warnings:
                lines.append(f"- {_safe_markdown_text(warning)}")
            lines.append("")


def _append_findings_by_category(lines: list[str], findings: list[Finding]) -> None:
    lines.extend(["## Findings By Category", ""])
    if not findings:
        lines.extend(["No findings.", ""])
        return
    grouped: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        grouped[finding.category].append(finding)
    for category in sorted(grouped):
        lines.append(f"### {category.replace('_', ' ').title()}")
        lines.append("")
        for finding in sorted(
            grouped[category],
            key=lambda item: (SEVERITY_ORDER[item.severity], item.check_id, item.tool_name or ""),
        ):
            suppressed = " (suppressed)" if finding.suppressed else ""
            baseline = f" ({finding.baseline_status})" if finding.baseline_status else ""
            target = f" [{_safe_markdown_text(finding.tool_name)}]" if finding.tool_name else ""
            lines.append(
                f"- {finding.severity.upper()}: {finding.check_id}{target}{suppressed}{baseline} - "
                f"{_safe_markdown_text(finding.title)}"
            )
            if finding.suppressed and finding.suppression_reason:
                lines.append(f"  Suppression: {_safe_markdown_text(finding.suppression_reason)}")
        lines.append("")


def _append_inventory(lines: list[str], report: ReadinessReport) -> None:
    lines.extend(["## Appendix: Normalized Tool Inventory", ""])
    if not report.tool_inventory:
        lines.extend(["No tools were enumerated.", ""])
        return
    lines.append("| Tool | Source | Risk Tags | Risk Confidence | Auth Scopes | Owner |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for tool in report.tool_inventory:
        name = _table_cell(tool.get("name") or "-")
        source_type = _table_cell(tool.get("source_type") or "-")
        risk_tags = _table_cell(", ".join(tool.get("risk_tags") or []) or "-")
        risk_confidence = _table_cell(_risk_confidence_summary(tool.get("risk_tag_confidence")) or "-")
        scopes = _table_cell(", ".join(tool.get("auth_scopes") or []) or "-")
        owner = _table_cell(tool.get("owner") or "-")
        lines.append(
            f"| {name} | {source_type} | {risk_tags} | {risk_confidence} | {scopes} | {owner} |"
        )
    lines.append("")


def _human_status(status: str) -> str:
    return status.replace("_", " ").capitalize()


def _compact_evidence(evidence: dict[str, object]) -> str:
    parts = []
    for key, value in evidence.items():
        parts.append(_safe_markdown_text(f"{key}={value}"))
    return "; ".join(parts) or "static metadata"


def _table_cell(value: object) -> str:
    return _safe_markdown_text(value)


def _risk_confidence_summary(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    return ", ".join(f"{tag}={confidence}" for tag, confidence in value.items())


def _safe_markdown_text(value: object) -> str:
    text = "" if value is None else str(value)
    for char in MARKDOWN_ESCAPE_CHARS:
        text = text.replace(char, f"\\{char}")
    text = re.sub(r"(?m)^(\s*)-", r"\1\\-", text)
    text = re.sub(r"(?m)^(\s*\d+)\.", r"\1\\.", text)
    return text


def _truncate_text(value: str, limit: int = 220) -> str:
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3]}..."
