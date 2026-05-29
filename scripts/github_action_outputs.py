from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

TRUST_ROOT_CHECK_ID = "SHIP-VERIFY-TRUST-ROOT-TOUCHED"
POLICY_WEAKENING_CHECK_ID = "SHIP-VERIFY-POLICY-WEAKENED"


def clean(value: object) -> str:
    # `value or ""` would emit 0 as "", which breaks workflow checks
    # like `if: outputs.blocker_count == '0'`. Treat None as empty;
    # stringify everything else, including 0 and False.
    text = "" if value is None else str(value)
    return text.replace("\r", "").replace("\n", "")


def bool_clean(value: object) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return clean(value)


def trigger_action(trigger: dict[str, Any]) -> str:
    if trigger.get("stop_conditions_fired"):
        return "skip_shipgate"
    if trigger.get("force_run"):
        return "force_run"
    if trigger.get("should_run") or trigger.get("run_shipgate"):
        return "run_shipgate"
    if trigger.get("dry_run_recommended"):
        return "dry_run"
    if trigger.get("skip_reason") in {"skip_rule", "stop_conditions"}:
        return "skip_shipgate"
    if trigger:
        return "none"
    return ""


def extract_outputs(output_dir: Path) -> dict[str, object]:
    report_json = output_dir / "report.json"
    report_markdown = output_dir / "report.md"
    report_sarif = output_dir / "report.sarif"
    verifier_json = output_dir / "verifier.json"
    pr_comment_markdown = output_dir / "pr-comment.md"

    payload = _load_json(report_json)
    verifier_payload = _load_json(verifier_json)
    summary = payload.get("summary") or {}
    baseline = payload.get("baseline") or {}
    adk_surface = (payload.get("frameworks") or {}).get("google_adk") or {}
    release_decision = payload.get("release_decision") or {}
    fail_policy = release_decision.get("fail_policy") or {}
    tool_surface_diff = payload.get("tool_surface_diff") or {}
    diff_summary = tool_surface_diff.get("summary") or {}
    action_surface_diff = payload.get("action_surface_diff") or {}
    action_diff_summary = action_surface_diff.get("summary") or {}
    trigger = verifier_payload.get("trigger") or {}
    matched_rules = trigger.get("matched_rules") or []
    verifier_summary = payload.get("verifier_summary") or {}

    capability_added, capability_modified, capability_removed = _capability_counts(
        payload,
        verifier_payload,
        action_diff_summary,
        diff_summary,
    )
    trust_root_touched, policy_weakened = _verifier_flags(
        payload,
        verifier_payload,
    )
    verifier_verdict = _verifier_verdict(release_decision, verifier_summary, verifier_payload)

    return {
        "status": summary.get("status", ""),
        "critical_count": summary.get("critical_count", 0),
        "high_count": summary.get("high_count", 0),
        "medium_count": summary.get("medium_count", 0),
        "baseline_new_count": baseline.get("new_count", 0),
        "baseline_matched_count": baseline.get("matched_count", 0),
        "baseline_resolved_count": baseline.get("resolved_count", 0),
        "adk_agent_count": adk_surface.get("agent_count", 0),
        "adk_dynamic_toolset_count": adk_surface.get("dynamic_toolset_count", 0),
        "report_json": report_json,
        "report_markdown": report_markdown,
        "report_sarif": report_sarif,
        "verifier_json": verifier_json,
        "pr_comment_markdown": pr_comment_markdown,
        "decision": release_decision.get("decision", ""),
        "blocker_count": len(release_decision.get("blockers") or []),
        "review_item_count": len(release_decision.get("review_items") or []),
        # v0.8+ output. Pinned older package versions emit no release_decision,
        # so this stays false until the workflow installs v0.8+.
        "ci_would_fail": str(bool(fail_policy.get("would_fail_ci"))).lower(),
        "diff_enabled": str(bool(tool_surface_diff.get("enabled"))).lower(),
        "action_diff_enabled": str(bool(action_surface_diff.get("enabled"))).lower(),
        "actions_added": action_diff_summary.get("actions_added", 0),
        "actions_modified": action_diff_summary.get("actions_modified", 0),
        "actions_removed": action_diff_summary.get("actions_removed", 0),
        "should_run": bool_clean(trigger.get("should_run", trigger.get("run_shipgate", ""))),
        "trigger_action": trigger_action(trigger),
        "trigger_rule_ids": ",".join(
            clean(rule.get("id"))
            for rule in matched_rules
            if isinstance(rule, dict) and rule.get("id")
        ),
        "verifier_verdict": verifier_verdict,
        "trust_root_touched": str(trust_root_touched).lower(),
        "policy_weakened": str(policy_weakened).lower(),
        "capability_changes_added": capability_added,
        "capability_changes_modified": capability_modified,
        "capability_changes_removed": capability_removed,
    }


def append_step_summary(output_dir: Path, values: dict[str, object]) -> None:
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not step_summary:
        return

    payload = _load_json(output_dir / "report.json")
    release_decision = payload.get("release_decision") or {}
    summary = payload.get("summary") or {}
    fail_policy = release_decision.get("fail_policy") or {}
    action_surface_diff = payload.get("action_surface_diff") or {}
    action_diff_summary = action_surface_diff.get("summary") or {}
    tool_surface_diff = payload.get("tool_surface_diff") or {}
    diff_summary = tool_surface_diff.get("summary") or {}

    blocker_count = len(release_decision.get("blockers") or [])
    review_item_count = len(release_decision.get("review_items") or [])
    would_fail_ci = str(bool(fail_policy.get("would_fail_ci"))).lower()
    with open(step_summary, "a", encoding="utf-8") as summary_file:
        summary_file.write("## Agents Shipgate\n\n")
        if release_decision:
            summary_file.write(
                f"- Decision: `{clean(release_decision.get('decision'))}`\n"
            )
            agent_summary = payload.get("agent_summary") or {}
            if agent_summary.get("headline"):
                summary_file.write(
                    f"- Summary: {clean(agent_summary.get('headline'))}\n"
                )
            summary_file.write(f"- Blockers: {blocker_count}\n")
            summary_file.write(f"- Review items: {review_item_count}\n")
            evidence = release_decision.get("evidence_coverage") or {}
            ev_parts = []
            if evidence.get("low_confidence_tool_count"):
                ev_parts.append(
                    f"{clean(evidence.get('low_confidence_tool_count'))} "
                    "low-confidence tool(s)"
                )
            if evidence.get("source_warning_count"):
                ev_parts.append(
                    f"{clean(evidence.get('source_warning_count'))} source warning(s)"
                )
            if evidence.get("human_review_recommended"):
                ev_parts.append("human review recommended")
            ev_suffix = f" ({'; '.join(ev_parts)})" if ev_parts else ""
            summary_file.write(
                f"- Evidence coverage: {clean(evidence.get('level', 'unknown'))}"
                f"{ev_suffix}\n"
            )
            summary_file.write(
                "- Fail policy: "
                f"would_fail_ci=`{would_fail_ci}` "
                f"(exit {clean(fail_policy.get('exit_code'))})\n"
            )
        else:
            summary_file.write(f"- Status: `{clean(summary.get('status'))}`\n")
            summary_file.write(
                f"- Critical: {clean(summary.get('critical_count', 0))} - "
                f"High: {clean(summary.get('high_count', 0))} - "
                f"Medium: {clean(summary.get('medium_count', 0))}\n"
            )
        if action_surface_diff.get("enabled"):
            summary_file.write(
                "- Action Surface Diff: "
                f"+{clean(action_diff_summary.get('actions_added', 0))} actions, "
                f"-{clean(action_diff_summary.get('actions_removed', 0))} actions, "
                f"{clean(action_diff_summary.get('actions_modified', 0))} modified, "
                f"{clean(action_diff_summary.get('blocking_findings', 0))} "
                "blocking finding(s)\n"
            )
        elif action_surface_diff.get("notes"):
            summary_file.write(
                "- Action-surface diff: "
                f"{clean(action_surface_diff.get('notes')[0])}\n"
            )
        if tool_surface_diff.get("enabled"):
            summary_file.write(
                "- What changed: "
                f"+{clean(diff_summary.get('tools_added', 0))} tools, "
                f"-{clean(diff_summary.get('tools_removed', 0))} tools, "
                f"{clean(diff_summary.get('tools_changed', 0))} changed, "
                f"{clean(diff_summary.get('new_high_risk_effects', 0))} "
                "new high-risk effect(s), "
                f"{clean(diff_summary.get('new_findings', 0))} new finding(s)\n"
            )
        elif tool_surface_diff.get("notes"):
            summary_file.write(
                f"- Tool-surface diff: {clean(tool_surface_diff.get('notes')[0])}\n"
            )
        summary_file.write(f"- Report JSON: `{clean(values.get('report_json'))}`\n")


def write_github_outputs(values: dict[str, object]) -> None:
    output_path = os.environ["GITHUB_OUTPUT"]
    with open(output_path, "a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={clean(value)}\n")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def _capability_counts(
    payload: dict[str, Any],
    verifier_payload: dict[str, Any],
    action_diff_summary: dict[str, Any],
    diff_summary: dict[str, Any],
) -> tuple[int, int, int]:
    capability_change = payload.get("capability_change")
    if isinstance(capability_change, dict):
        return (
            len(capability_change.get("added") or []),
            len(capability_change.get("broadened") or [])
            + len(capability_change.get("narrowed") or []),
            len(capability_change.get("removed") or []),
        )

    capability_review = verifier_payload.get("capability_review")
    if isinstance(capability_review, dict) and capability_review:
        return (
            int(capability_review.get("added") or 0),
            int(capability_review.get("modified") or 0),
            int(capability_review.get("removed") or 0),
        )

    return (
        int(action_diff_summary.get("actions_added") or 0)
        + int(diff_summary.get("tools_added") or 0)
        + int(diff_summary.get("new_scopes") or 0)
        + int(diff_summary.get("controls_added") or 0),
        int(action_diff_summary.get("actions_modified") or 0)
        + int(diff_summary.get("tools_changed") or 0)
        + int(diff_summary.get("metadata_changes") or 0)
        + int(diff_summary.get("policy_drift_items") or 0),
        int(action_diff_summary.get("actions_removed") or 0)
        + int(diff_summary.get("tools_removed") or 0)
        + int(diff_summary.get("removed_scopes") or 0)
        + int(diff_summary.get("controls_removed") or 0),
    )


def _verifier_flags(
    payload: dict[str, Any],
    verifier_payload: dict[str, Any],
) -> tuple[bool, bool]:
    verifier_summary = payload.get("verifier_summary")
    if isinstance(verifier_summary, dict) and verifier_summary:
        return (
            bool(verifier_summary.get("protected_surface_touched")),
            bool(verifier_summary.get("policy_weakened")),
        )

    capability_review = verifier_payload.get("capability_review")
    if isinstance(capability_review, dict) and capability_review:
        return (
            bool(capability_review.get("trust_root_touched")),
            bool(capability_review.get("policy_weakened")),
        )

    active_findings = [
        finding
        for finding in (payload.get("findings") or [])
        if isinstance(finding, dict) and not finding.get("suppressed")
    ]
    return (
        bool(payload.get("protected_surface_changes"))
        or any(finding.get("check_id") == TRUST_ROOT_CHECK_ID for finding in active_findings),
        any(
            finding.get("check_id") == POLICY_WEAKENING_CHECK_ID
            for finding in active_findings
        ),
    )


def _verifier_verdict(
    release_decision: dict[str, Any],
    verifier_summary: dict[str, Any],
    verifier_payload: dict[str, Any],
) -> str:
    verdict = release_decision.get("decision") or verifier_summary.get("verdict")
    if verdict:
        return str(verdict)
    head_status = verifier_payload.get("head_status")
    if head_status == "skipped":
        return "skipped"
    if head_status == "failed":
        return "failed"
    return ""


def main() -> None:
    output_dir = Path(os.environ.get("OUTPUT_DIR") or "agents-shipgate-reports")
    values = extract_outputs(output_dir)
    write_github_outputs(values)
    append_step_summary(output_dir, values)


if __name__ == "__main__":
    main()
