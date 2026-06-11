from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_shipgate import __version__
from agents_shipgate.schemas.report import ReadinessReport, ReleaseDecisionItem
from agents_shipgate.schemas.verifier import VerifierArtifact

AGENT_RESULT_SCHEMA_VERSION = "shipgate.agent_result/v1"

AgentResultDecision = Literal["allow", "warn", "require_review", "block"]
AgentResultRiskLevel = Literal["low", "medium", "high", "critical"]


class AgentResultTool(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "agents-shipgate"
    version: str = __version__


class AgentResultSubject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace: str
    agent: str | None = None
    diff: str | None = None
    base: str | None = None
    head: str | None = None


class AgentResultRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    severity: str
    decision: AgentResultDecision


class AgentResultFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    start_line: int | None = None
    end_line: int | None = None
    pointer: str | None = None
    source_type: str | None = None


class AgentResultTraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: str
    summary: str


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.agent_result/v1"] = AGENT_RESULT_SCHEMA_VERSION
    tool: AgentResultTool = Field(default_factory=AgentResultTool)
    subject: AgentResultSubject
    decision: AgentResultDecision
    risk_level: AgentResultRiskLevel
    merge_verdict: str = "unknown"
    can_merge_without_human: bool = False
    violated_rules: list[AgentResultRule] = Field(default_factory=list)
    affected_files: list[AgentResultFile] = Field(default_factory=list)
    required_reviewers: list[str] = Field(default_factory=list)
    explanation: str
    suggested_fixes: list[str] = Field(default_factory=list)
    agent_repair_instructions: list[str] = Field(default_factory=list)
    audit_id: str
    policy_snapshot_sha256: str | None = None
    trace: list[AgentResultTraceEvent] = Field(default_factory=list)
    source_artifacts: dict[str, str] = Field(default_factory=dict)
    exit_code_hint: int = 0


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
    violated_rules = _violated_rules(items, decision, release_decision)
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
        subject=_subject(verifier, report),
        decision=decision,
        risk_level=risk_level,
        merge_verdict=verifier.merge_verdict,
        can_merge_without_human=verifier.can_merge_without_human,
        violated_rules=violated_rules,
        affected_files=affected_files,
        required_reviewers=required_reviewers,
        explanation=_explanation(verifier, report, decision),
        suggested_fixes=_suggested_fixes(verifier, decision),
        agent_repair_instructions=_agent_repair_instructions(verifier, decision),
        audit_id=audit_id,
        policy_snapshot_sha256=policy_hash,
        trace=trace,
        source_artifacts=dict(sorted(verifier.artifacts.items())),
        exit_code_hint=_exit_code_hint(decision),
    )


def write_agent_result(result: AgentResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True),
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
        return "warn" if _has_non_gating_advisory(release_decision) else "allow"
    return "require_review"


def _has_non_gating_advisory(release_decision: Any) -> bool:
    for rule in release_decision.contribution_rules:
        if rule.category == "excluded" and rule.rule == "sub_threshold":
            return True
    return False


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
    decision: AgentResultDecision,
    release_decision: Any,
) -> list[AgentResultRule]:
    rules = [
        AgentResultRule(
            id=item.check_id,
            title=item.title,
            severity=item.severity,
            decision=decision,
        )
        for item in items
    ]
    if decision == "warn" and release_decision is not None:
        seen = {rule.id for rule in rules}
        for rule in release_decision.contribution_rules:
            if rule.category == "excluded" and rule.rule == "sub_threshold":
                if rule.check_id in seen:
                    continue
                seen.add(rule.check_id)
                rules.append(
                    AgentResultRule(
                        id=rule.check_id,
                        title="Non-gating advisory finding",
                        severity="low",
                        decision="warn",
                    )
                )
    return sorted(rules, key=lambda item: (item.id, item.title, item.severity))


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
        return "medium"
    return "low"


def _affected_files(
    items: list[ReleaseDecisionItem],
    verifier: VerifierArtifact,
) -> list[AgentResultFile]:
    files: dict[tuple[str, int | None, int | None, str | None], AgentResultFile] = {}
    for item in items:
        for source in (item.source, item.policy_evidence_source):
            if source is None:
                continue
            path = source.path or _path_from_location(source.location or source.ref)
            if not path:
                continue
            row = AgentResultFile(
                path=path,
                start_line=source.start_line,
                end_line=source.end_line,
                pointer=source.pointer,
                source_type=source.type,
            )
            files[(row.path, row.start_line, row.end_line, row.pointer)] = row
    if not files:
        for path in verifier.changed_files:
            files[(path, None, None, None)] = AgentResultFile(path=path)
    return [
        files[key]
        for key in sorted(files, key=lambda item: (item[0], item[1] or 0, item[3] or ""))
    ][:20]


def _path_from_location(value: str | None) -> str | None:
    if not value:
        return None
    path, _, maybe_line = value.rpartition(":")
    return path if maybe_line.isdigit() else value


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
        token = f"{item.check_id} {item.title}".lower()
        if any(
            marker in token
            for marker in (
                "auth",
                "approval",
                "credential",
                "secret",
                "security",
                "ci",
                "policy-weakened",
                "destructive",
                "external write",
            )
        ):
            reviewers.add("security")
        if any(
            marker in token
            for marker in (
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


def _trace(
    *,
    verifier: VerifierArtifact,
    report: ReadinessReport | None,
    decision: AgentResultDecision,
    risk_level: AgentResultRiskLevel,
    policy_hash: str | None,
    violated_rules: list[AgentResultRule],
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
    violated_rules: list[AgentResultRule],
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


def _subject(verifier: VerifierArtifact, report: ReadinessReport | None) -> AgentResultSubject:
    if verifier.base_ref:
        diff = f"{verifier.base_ref}...{verifier.head_ref}"
    else:
        diff = None
    agent_name = None
    if report is not None:
        raw = report.agent.get("name")
        agent_name = str(raw) if raw else None
    return AgentResultSubject(
        workspace=verifier.workspace,
        agent=agent_name,
        diff=diff,
        base=verifier.base_ref,
        head=verifier.head_ref,
    )


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
        if fix_task.verification_command:
            instructions.append(f"Then rerun: {fix_task.verification_command}")
        if fix_task.actor == "human":
            instructions.append("Stop and request human review; do not self-resolve this authority gap.")
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
    "AgentResultDecision",
    "AgentResultFile",
    "AgentResultRiskLevel",
    "AgentResultRule",
    "AgentResultSubject",
    "AgentResultTraceEvent",
    "AgentResultTool",
    "build_agent_result",
    "write_agent_result",
]
