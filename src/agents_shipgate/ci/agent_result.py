from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from agents_shipgate import __version__
from agents_shipgate.schemas.agent_result_v1 import (
    AgentResultAffectedFile,
    AgentResultDecision,
    AgentResultDiagnostic,
    AgentResultNextAction,
    AgentResultRiskLevel,
    AgentResultTraceEvent,
    AgentResultV1,
    AgentResultViolatedRule,
)
from agents_shipgate.schemas.report import Finding, ReadinessReport, ReleaseDecisionItem
from agents_shipgate.schemas.verifier import VerifierArtifact

AGENT_RESULT_SCHEMA_VERSION = "agent_result_v1"
AgentResult = AgentResultV1
_REVIEW_TOKEN_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def build_agent_result(
    *,
    verifier: VerifierArtifact,
    report: ReadinessReport | None,
) -> AgentResult:
    """Project verifier/report artifacts into the compact agent result.

    This is a projection only. The release gate remains
    ``report.release_decision.decision`` when a report exists.
    """

    release_decision = report.release_decision if report is not None else None
    decision = _project_decision(verifier=verifier, report=report)
    items = _decision_items(release_decision, decision)
    advisory_findings = _advisory_findings(report, release_decision, decision)
    violated_rules = _violated_rules(items, advisory_findings, decision)
    policy_hash = _policy_snapshot_sha256(report)
    risk_level = _risk_level(decision, items, release_decision, verifier)
    affected_files = _affected_files(items, verifier)
    required_reviewers = _required_reviewers(
        decision=decision,
        items=items,
        release_decision_value=(
            release_decision.decision if release_decision is not None else None
        ),
        verifier=verifier,
    )
    trace = _trace(
        verifier=verifier,
        report=report,
        decision=decision,
        risk_level=risk_level,
        policy_hash=policy_hash,
        violated_rules=violated_rules,
    )
    audit_id = _audit_id(
        verifier=verifier,
        report=report,
        decision=decision,
        policy_hash=policy_hash,
        violated_rules=violated_rules,
    )
    return AgentResult(
        decision=decision,
        risk_level=risk_level,
        audit_id=audit_id,
        policy_version=_policy_version(policy_hash),
        summary=_explanation(verifier, report, decision),
        changed_files=list(verifier.changed_files),
        first_next_action=_first_next_action(verifier, decision),
        violated_rules=violated_rules,
        diagnostics=_diagnostics(verifier),
        release_decision=(
            release_decision.model_dump(mode="json")
            if release_decision is not None
            else verifier.release_decision
        ),
        trigger=verifier.trigger,
        finding_fingerprints=_finding_fingerprints(items, advisory_findings),
        affected_files=affected_files,
        required_reviewers=required_reviewers,
        suggested_fixes=_suggested_fixes(verifier, decision),
        agent_repair_instructions=_agent_repair_instructions(verifier, decision),
        policy_snapshot_sha256=policy_hash,
        trace=trace,
        source_artifacts=dict(sorted(verifier.artifacts.items())),
        exit_code_hint=_exit_code_hint(decision),
    )


def write_agent_result(result: AgentResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            result.model_dump(mode="json", exclude_none=True),
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _project_decision(
    *,
    verifier: VerifierArtifact,
    report: ReadinessReport | None,
) -> AgentResultDecision:
    release_decision = report.release_decision if report is not None else None
    if release_decision is None:
        return "allow" if verifier.head_status == "skipped" else "require_review"
    if release_decision.decision == "blocked":
        return "block"
    if release_decision.decision in {"review_required", "insufficient_evidence"}:
        return "require_review"
    if release_decision.decision == "passed":
        return "warn" if _has_review_tier_advisory(report, release_decision) else "allow"
    return "require_review"


def _has_review_tier_advisory(
    report: ReadinessReport | None,
    release_decision: Any,
) -> bool:
    return bool(_advisory_findings(report, release_decision, "warn"))


def _decision_items(
    release_decision: Any,
    decision: AgentResultDecision,
) -> list[ReleaseDecisionItem]:
    if release_decision is None:
        return []
    if decision == "block":
        return list(release_decision.blockers)
    if decision == "require_review":
        return list(release_decision.review_items)
    return []


def _violated_rules(
    items: list[ReleaseDecisionItem],
    advisory_findings: list[Finding],
    decision: AgentResultDecision,
) -> list[AgentResultViolatedRule]:
    rules = [
        AgentResultViolatedRule(
            id=_rule_id_from_item(item),
            check_id=item.check_id,
            action=decision,
            risk_level=_risk_from_severity(item.severity),
            title=item.title,
            path=_path_from_item(item),
            evidence={},
            recommendation="Review the release-decision item and address the underlying finding.",
        )
        for item in items
    ]
    for finding in advisory_findings:
        rules.append(
            AgentResultViolatedRule(
                id=_rule_id_from_finding(finding),
                check_id=finding.check_id,
                action="warn",
                risk_level=_risk_from_severity(finding.severity),
                title=finding.title or "Review-tier advisory finding",
                path=_path_from_source(finding.source),
                evidence=dict(finding.evidence or {}),
                recommendation=finding.recommendation
                or "Review the advisory finding before relying on this pass.",
            )
        )
    return sorted(rules, key=lambda item: (item.id, item.title, item.check_id))


def _advisory_findings(
    report: ReadinessReport | None,
    release_decision: Any,
    decision: AgentResultDecision,
) -> list[Finding]:
    if decision != "warn" or report is None or release_decision is None:
        return []
    findings_by_fingerprint = {
        finding.fingerprint: finding
        for finding in report.findings
        if finding.fingerprint and not finding.suppressed
    }
    findings_by_check: dict[str, list[Finding]] = {}
    for finding in report.findings:
        if finding.suppressed:
            continue
        findings_by_check.setdefault(finding.check_id, []).append(finding)

    out: list[Finding] = []
    for rule in release_decision.contribution_rules:
        if rule.category != "excluded" or rule.rule != "sub_threshold":
            continue
        finding = (
            findings_by_fingerprint.get(rule.fingerprint)
            if rule.fingerprint
            else None
        )
        if finding is None:
            candidates = findings_by_check.get(rule.check_id) or []
            finding = candidates[0] if len(candidates) == 1 else None
        if finding is None or finding.severity != "medium":
            continue
        out.append(finding)
    return out


def _policy_snapshot_sha256(report: ReadinessReport | None) -> str | None:
    if report is None:
        return None
    effective_policy = getattr(report, "effective_policy", None)
    if effective_policy is None:
        return None
    return hashlib.sha256(
        json.dumps(
            effective_policy.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _risk_level(
    decision: AgentResultDecision,
    items: list[ReleaseDecisionItem],
    release_decision: Any,
    verifier: VerifierArtifact,
) -> AgentResultRiskLevel:
    if decision == "block":
        return "critical" if any(item.severity == "critical" for item in items) else "high"
    if decision == "require_review":
        if release_decision is not None and release_decision.decision == "insufficient_evidence":
            return "high"
        if verifier.merge_verdict == "unknown" or verifier.head_status == "failed":
            return "high"
        severities = {item.severity for item in items}
        if "critical" in severities:
            return "critical"
        if "high" in severities:
            return "high"
        return "medium"
    if decision == "warn":
        return "low"
    return "none"


def _affected_files(
    items: list[ReleaseDecisionItem],
    verifier: VerifierArtifact,
) -> list[AgentResultAffectedFile]:
    files: dict[tuple[str, int | None, int | None, str | None], AgentResultAffectedFile] = {}
    for item in items:
        for source in (item.source, item.policy_evidence_source):
            if source is None:
                continue
            path = _path_from_source(source)
            if not path:
                continue
            row = AgentResultAffectedFile(
                path=path,
                start_line=source.start_line,
                end_line=source.end_line,
                pointer=source.pointer,
                source_type=source.type,
            )
            files[(row.path, row.start_line, row.end_line, row.pointer)] = row
    if not files:
        for path in verifier.changed_files:
            files[(path, None, None, None)] = AgentResultAffectedFile(path=path)
    return [
        files[key]
        for key in sorted(
            files,
            key=lambda item: (item[0], item[1] or 0, item[2] or 0, item[3] or ""),
        )
    ][:20]


def _path_from_location(value: str | None) -> str | None:
    if not value:
        return None
    path, _, maybe_line = value.rpartition(":")
    return path if maybe_line.isdigit() else value


def _path_from_source(source: Any) -> str | None:
    if source is None:
        return None
    return source.path or _path_from_location(source.location or source.ref)


def _path_from_item(item: ReleaseDecisionItem) -> str | None:
    return _path_from_source(item.source) or _path_from_source(item.policy_evidence_source)


def _rule_id_from_item(item: ReleaseDecisionItem) -> str:
    return _rule_id_from_evidence(item.check_id, {})


def _rule_id_from_finding(finding: Finding) -> str:
    return _rule_id_from_evidence(finding.check_id, finding.evidence or {})


def _rule_id_from_evidence(check_id: str, evidence: dict[str, Any]) -> str:
    for key in ("policy_rule_id", "rule_id", "policy_id"):
        value = evidence.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return check_id


def _risk_from_severity(severity: str) -> AgentResultRiskLevel:
    if severity in {"critical", "high", "medium", "low"}:
        return severity  # type: ignore[return-value]
    return "none"


def _finding_fingerprints(
    items: list[ReleaseDecisionItem],
    advisory_findings: list[Finding],
) -> list[str]:
    values = [
        *(item.fingerprint for item in items if item.fingerprint),
        *(finding.fingerprint for finding in advisory_findings if finding.fingerprint),
    ]
    return sorted(dict.fromkeys(values))


def _first_next_action(
    verifier: VerifierArtifact,
    decision: AgentResultDecision,
) -> AgentResultNextAction:
    if decision == "allow":
        return AgentResultNextAction(
            actor="coding_agent",
            kind="continue",
            command=None,
            why="No Agents Shipgate release action is required.",
        )
    if decision == "warn":
        return AgentResultNextAction(
            actor="coding_agent",
            kind="warn",
            command=None,
            why="Review low-risk advisory findings, then continue if acceptable.",
        )
    if decision == "block":
        return AgentResultNextAction(
            actor="human",
            kind="stop",
            command=None,
            why=_next_action_why(verifier)
            or "Blocking Agents Shipgate finding requires human review.",
        )
    return AgentResultNextAction(
        actor="human",
        kind="review",
        command=None,
        why=_next_action_why(verifier) or "Human review is required before merge.",
    )


def _next_action_why(verifier: VerifierArtifact) -> str | None:
    action = verifier.first_next_action
    if action is not None:
        return action.why
    if verifier.fix_task and verifier.fix_task.instructions:
        return verifier.fix_task.instructions[0]
    return None


def _diagnostics(verifier: VerifierArtifact) -> list[AgentResultDiagnostic]:
    diagnostics: list[AgentResultDiagnostic] = []
    if verifier.head_status == "failed":
        diagnostics.append(
            AgentResultDiagnostic(
                level="error",
                code="verify_head_failed",
                message=f"Head scan failed with exit code {verifier.head_exit_code}.",
            )
        )
    if verifier.base_status in {"ref_missing", "archive_failed"}:
        diagnostics.append(
            AgentResultDiagnostic(
                level="warning",
                code=f"verify_base_{verifier.base_status}",
                message="Base comparison was unavailable during verify.",
            )
        )
    for index, note in enumerate(verifier.base_notes[:3], start=1):
        diagnostics.append(
            AgentResultDiagnostic(
                level="warning",
                code=f"verify_base_note_{index}",
                message=note,
            )
        )
    return diagnostics


def _policy_version(policy_hash: str | None) -> str:
    return policy_hash or f"agents-shipgate:{__version__}"


def _required_reviewers(
    *,
    decision: AgentResultDecision,
    items: list[ReleaseDecisionItem],
    release_decision_value: str | None,
    verifier: VerifierArtifact,
) -> list[str]:
    reviewers: set[str] = set()
    if decision == "block":
        reviewers.add("security")
    if release_decision_value == "insufficient_evidence":
        reviewers.add("agent-platform")
    review = verifier.capability_review
    if review.policy_weakened:
        reviewers.add("security")
    if review.trust_root_touched:
        reviewers.add("agent-platform")
    for item in items:
        text = f"{item.check_id} {item.title}".lower()
        tokens = set(_REVIEW_TOKEN_RE.findall(text))
        if _matches_review_marker(
            text,
            tokens,
            exact=(
                "auth",
                "approval",
                "credential",
                "credentials",
                "secret",
                "secrets",
                "security",
                "ci",
                "policy-weakened",
                "destructive",
            ),
            phrases=(
                "ci gate",
                "ci-gate",
                "continuous integration",
                "external write",
            ),
        ):
            reviewers.add("security")
        if _matches_review_marker(
            text,
            tokens,
            exact=(
                "capability",
                "mcp",
                "scope",
                "manifest",
                "trust-root",
                "inventory",
                "plugin",
                "tool",
                "dependency",
            )
        ):
            reviewers.add("agent-platform")
    if decision == "require_review" and not reviewers:
        reviewers.add("release-owner")
    return sorted(reviewers)


def _matches_review_marker(
    text: str,
    tokens: set[str],
    *,
    exact: tuple[str, ...],
    phrases: tuple[str, ...] = (),
) -> bool:
    return bool(tokens.intersection(exact)) or any(phrase in text for phrase in phrases)


def _trace(
    *,
    verifier: VerifierArtifact,
    report: ReadinessReport | None,
    decision: AgentResultDecision,
    risk_level: AgentResultRiskLevel,
    policy_hash: str | None,
    violated_rules: list[AgentResultViolatedRule],
) -> list[AgentResultTraceEvent]:
    release_value = (
        report.release_decision.decision
        if report is not None and report.release_decision is not None
        else None
    )
    events = [
        AgentResultTraceEvent(
            step="release_decision",
            summary=f"Projected release decision {release_value or verifier.head_status!r} to {decision}.",
        ),
        AgentResultTraceEvent(
            step="risk_level",
            summary=f"Projected risk level {risk_level} from decision items and verifier status.",
        ),
        AgentResultTraceEvent(
            step="violated_rules",
            summary=f"Included {len(violated_rules)} rule(s) in the compact result.",
        ),
    ]
    if policy_hash:
        events.append(
            AgentResultTraceEvent(
                step="policy_snapshot",
                summary=f"Hashed report.effective_policy as {policy_hash}.",
            )
        )
    if verifier.base_status != "not_requested":
        events.append(
            AgentResultTraceEvent(
                step="base_diff",
                summary=f"Verifier base status: {verifier.base_status}.",
            )
        )
    return events


def _audit_id(
    *,
    verifier: VerifierArtifact,
    report: ReadinessReport | None,
    decision: AgentResultDecision,
    policy_hash: str | None,
    violated_rules: list[AgentResultViolatedRule],
) -> str:
    payload = {
        "schema_version": AGENT_RESULT_SCHEMA_VERSION,
        "run_id": report.run_id if report is not None else None,
        "base_tree_sha": verifier.base_tree_sha,
        "head_tree_sha": verifier.head_tree_sha,
        "decision": decision,
        "policy_snapshot_sha256": policy_hash,
        "violated_rule_ids": sorted(rule.id for rule in violated_rules),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"sg_audit_{digest}"


def _explanation(
    verifier: VerifierArtifact,
    report: ReadinessReport | None,
    decision: AgentResultDecision,
) -> str:
    if verifier.headline:
        return verifier.headline
    if report is not None and report.release_decision is not None:
        return report.release_decision.reason
    if decision == "allow":
        return "No Shipgate scan was required for this diff."
    return "Shipgate could not produce a trusted release decision; human review is required."


def _suggested_fixes(
    verifier: VerifierArtifact,
    decision: AgentResultDecision,
) -> list[str]:
    fix_task = verifier.fix_task
    if fix_task and fix_task.instructions:
        return list(fix_task.instructions[:6])
    if decision == "block":
        return ["Fix the blocking finding(s), then rerun Agents Shipgate verify."]
    if decision == "require_review":
        return ["Request human review from the listed reviewer role(s)."]
    return []


def _agent_repair_instructions(
    verifier: VerifierArtifact,
    decision: AgentResultDecision,
) -> list[str]:
    instructions: list[str] = []
    fix_task = verifier.fix_task
    if fix_task is not None:
        instructions.extend(fix_task.instructions[:6])
        instructions.extend(fix_task.forbidden_shortcuts[:6])
        if fix_task.verification_command:
            instructions.append(f"Then rerun: {fix_task.verification_command}")
        if fix_task.actor == "human":
            instructions.append(
                "Stop and request human review; do not self-resolve this authority gap."
            )
    elif decision in {"block", "require_review"}:
        instructions.append("Stop and request human review unless the fix task is agent-safe.")
    controller = verifier.agent_controller
    if controller is not None:
        instructions.extend(controller.forbidden_actions[:4])
    return _dedupe_preserve_order(instructions)


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _exit_code_hint(decision: AgentResultDecision) -> int:
    return 20 if decision == "block" else 0


__all__ = [
    "AGENT_RESULT_SCHEMA_VERSION",
    "AgentResult",
    "AgentResultAffectedFile",
    "AgentResultDecision",
    "AgentResultRiskLevel",
    "AgentResultTraceEvent",
    "AgentResultViolatedRule",
    "build_agent_result",
    "write_agent_result",
]
