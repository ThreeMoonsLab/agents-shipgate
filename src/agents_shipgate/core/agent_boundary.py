"""Central deterministic multi-host boundary assessment.

Catalog/actor selection is never authority: every registered repository
boundary adapter runs for every invocation.  The resulting assessment is the
single input to neutral and legacy local-control projections.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from agents_shipgate.core.boundary_diff import BoundaryInputIssue, parse_unified_diff
from agents_shipgate.core.boundary_registry import (
    BOUNDARY_ADAPTERS,
    boundary_hosts_for_path,
    is_agent_boundary_path,
)
from agents_shipgate.core.codex_boundary import (
    DEFAULT_RULES as CODEX_DEFAULT_RULES,
)
from agents_shipgate.core.codex_boundary import (
    CodexBoundaryPolicy,
    _affected_files_for,
    _agent_repair_instructions,
    _control_for_result,
    _decision_for,
    _dedupe_violations,
    _human_review_for,
    _next_action_for,
    _repair_for,
    _risk_for,
    _violation_fingerprint,
    evaluate_codex_boundary_result,
    load_codex_boundary_policy,
)
from agents_shipgate.core.host_boundary import (
    DEFAULT_POLICY_PATH as LEGACY_HOST_POLICY_PATH,
)
from agents_shipgate.core.host_boundary import (
    HostBoundaryPolicy,
    evaluate_host_boundary,
    load_host_boundary_policy,
)
from agents_shipgate.schemas.agent_boundary import (
    AGENT_BOUNDARY_RESULT_SCHEMA_VERSION,
    AgentBoundaryResultV1,
    BoundaryHostCoverage,
)
from agents_shipgate.schemas.agent_result_v1 import (
    AgentResultDiagnostic,
    AgentResultPolicy,
    AgentResultTraceEvent,
    AgentResultViolatedRule,
)
from agents_shipgate.schemas.codex_boundary_result import CodexBoundaryResultV2

UNIFIED_POLICY_PATH = Path("policies/agent-boundary.shipgate.yaml")
LEGACY_CODEX_POLICY_PATH = Path("policies/codex-boundary.shipgate.yaml")


@dataclass(frozen=True)
class AgentBoundaryAssessment:
    actor: str
    input_mode: Literal["worktree", "git_range", "provided_diff"]
    scope: Literal["repository"]
    input_coverage: Literal["complete", "partial", "unknown"]
    host_coverage: tuple[BoundaryHostCoverage, ...]
    affected_hosts: tuple[str, ...]
    violations: tuple[AgentResultViolatedRule, ...]
    diagnostics: tuple[AgentResultDiagnostic, ...]
    policies: tuple[AgentResultPolicy, ...]
    policy_set_sha256: str
    issues: tuple[str, ...]
    completion_eligible: bool
    legacy_result: CodexBoundaryResultV2


@dataclass(frozen=True)
class _PolicySet:
    codex: CodexBoundaryPolicy
    host: HostBoundaryPolicy
    diagnostics: tuple[AgentResultDiagnostic, ...]
    records: tuple[AgentResultPolicy, ...]
    digest: str
    issues: tuple[str, ...]


def evaluate_agent_boundary(
    *,
    workspace: Path,
    diff_text: str,
    actor: str = "codex",
    policy_path: Path | None = None,
    trigger: dict[str, Any] | None = None,
    release_decision: dict[str, Any] | None = None,
    capability_surfaces_changed: list[str] | None = None,
    undeclared_capability_surfaces: list[str] | None = None,
    manifest_present: bool | None = None,
    input_mode: Literal["worktree", "git_range", "provided_diff"] = "provided_diff",
    input_issues: list[BoundaryInputIssue] | None = None,
) -> AgentBoundaryAssessment:
    workspace = workspace.resolve()
    diff_files = parse_unified_diff(diff_text)
    changed_files = sorted(
        {
            path
            for item in diff_files
            for path in (item.path, item.old_path)
            if path and (path == item.path or is_agent_boundary_path(path))
        }
    )
    input_issues = [
        *(input_issues or []),
        *_structural_diff_issues(
            workspace=workspace,
            diff_files=diff_files,
            diff_text=diff_text,
        ),
    ]
    policies = _load_policy_set(workspace=workspace, explicit=policy_path)

    legacy = evaluate_codex_boundary_result(
        workspace=workspace,
        diff_text=diff_text,
        agent=actor,
        trigger=trigger,
        release_decision=release_decision,
        capability_surfaces_changed=capability_surfaces_changed,
        undeclared_capability_surfaces=undeclared_capability_surfaces,
        manifest_present=manifest_present,
        policy_override=policies.codex,
        policy_diagnostics=list(policies.diagnostics),
    )
    host_violations, host_diagnostics = evaluate_host_boundary(
        workspace=workspace,
        diff_text=diff_text,
        policy_override=policies.host,
    )
    diagnostics = _dedupe_diagnostics(
        [*legacy.diagnostics, *host_diagnostics, *policies.diagnostics]
    )
    issue_codes = list(policies.issues)
    issue_codes.extend(item.code for item in input_issues)
    for diagnostic in diagnostics:
        if diagnostic.level in {"warning", "error"} and diagnostic.code in {
            "content_source",
            "policy_conflict",
            "policy_load_failed",
            "policy_missing",
            "policy_unknown_fields",
        }:
            issue_codes.append(diagnostic.code)

    combined = _dedupe_violations([*legacy.violated_rules, *host_violations])
    combined = _with_unclassified_protected_changes(
        changed_files=changed_files,
        violations=combined,
    )
    combined = _with_experimental_adapter_changes(
        changed_files=changed_files,
        violations=combined,
    )
    combined = _sanitize_violations(combined)
    if input_issues:
        from agents_shipgate.core.host_boundary import DEFAULT_RULES as HOST_DEFAULT_RULES

        rule = HOST_DEFAULT_RULES["HOST-CONFIG-PARSE-FAILED"]
        combined = _dedupe_violations(
            [
                *combined,
                *[
                    AgentResultViolatedRule(
                        id=rule.id,
                        check_id=rule.check_id,
                        action=rule.action,  # type: ignore[arg-type]
                        risk_level=rule.risk_level,
                        title=rule.title,
                        path=issue.path,
                        evidence={
                            "kind": "boundary_input_unresolved",
                            "code": issue.code,
                        },
                        recommendation=rule.recommendation,
                    )
                    for issue in input_issues
                ],
            ]
        )
        diagnostics.extend(
            AgentResultDiagnostic(
                level="error",
                code=issue.code,
                message=issue.message,
                path=issue.path,
            )
            for issue in input_issues
        )
        diagnostics = _dedupe_diagnostics(diagnostics)
    if policies.issues and not any(
        item.evidence.get("kind") == "boundary_policy_conflict" for item in combined
    ):
        rule = CODEX_DEFAULT_RULES["CODEX-POLICY-WEAKENED"]
        combined = _dedupe_violations(
            [
                *combined,
                AgentResultViolatedRule(
                    id=rule.id,
                    check_id=rule.check_id,
                    action=rule.action,  # type: ignore[arg-type]
                    risk_level=rule.risk_level,
                    title=rule.title,
                    path=UNIFIED_POLICY_PATH.as_posix(),
                    evidence={
                        "kind": "boundary_policy_conflict",
                        "issues": sorted(policies.issues),
                    },
                    recommendation=rule.recommendation,
                ),
            ]
        )

    if issue_codes and not any(
        item.evidence.get("kind") in {
            "boundary_input_unresolved",
            "boundary_policy_conflict",
            "host_config_content_unresolved",
            "codex_config_content_unresolved",
            "json_parse_failed",
            "toml_parse_failed",
            "unknown_host_config_key",
        }
        for item in combined
    ):
        from agents_shipgate.core.host_boundary import DEFAULT_RULES as HOST_DEFAULT_RULES

        rule = HOST_DEFAULT_RULES["HOST-CONFIG-PARSE-FAILED"]
        witness = next(
            (item.path for item in diagnostics if item.level in {"warning", "error"}),
            None,
        )
        combined = _dedupe_violations(
            [
                *combined,
                AgentResultViolatedRule(
                    id=rule.id,
                    check_id=rule.check_id,
                    action=rule.action,  # type: ignore[arg-type]
                    risk_level=rule.risk_level,
                    title=rule.title,
                    path=witness,
                    evidence={
                        "kind": "boundary_coverage_incomplete",
                        "issues": sorted(dict.fromkeys(issue_codes)),
                    },
                    recommendation=rule.recommendation,
                ),
            ]
        )

    diagnostics = _sanitize_diagnostics(diagnostics)

    projected = _project_legacy(
        legacy=legacy,
        violations=combined,
        diagnostics=diagnostics,
        policy_set=policies,
        release_decision=release_decision,
    )
    affected_hosts = tuple(
        sorted({host for path in changed_files for host in boundary_hosts_for_path(path)})
    )
    coverage = _coverage_for(
        changed_files=changed_files,
        violations=combined,
        issues=issue_codes,
    )
    input_coverage: Literal["complete", "partial", "unknown"] = (
        "partial"
        if issue_codes
        or any(item.status == "partial" for item in coverage)
        else "complete"
    )
    completion_eligible = (
        input_coverage == "complete"
        and projected.control.state == "complete"
        and all(item.status not in {"partial", "experimental"} for item in coverage)
    )
    return AgentBoundaryAssessment(
        actor=actor,
        input_mode=input_mode,
        scope="repository",
        input_coverage=input_coverage,
        host_coverage=tuple(coverage),
        affected_hosts=affected_hosts,
        violations=tuple(combined),
        diagnostics=tuple(diagnostics),
        policies=policies.records,
        policy_set_sha256=policies.digest,
        issues=tuple(sorted(dict.fromkeys(issue_codes))),
        completion_eligible=completion_eligible,
        legacy_result=projected,
    )


def build_agent_boundary_result(assessment: AgentBoundaryAssessment) -> AgentBoundaryResultV1:
    legacy = assessment.legacy_result
    aggregate_policy = _aggregate_policy(assessment)
    return AgentBoundaryResultV1(
        **legacy.model_dump(mode="python", exclude={"schema_version", "policy"}),
        actor=assessment.actor,  # type: ignore[arg-type]
        input_mode=assessment.input_mode,
        scope=assessment.scope,
        input_coverage=assessment.input_coverage,
        host_coverage=list(assessment.host_coverage),
        affected_hosts=list(assessment.affected_hosts),
        policy=aggregate_policy,
        policies=list(assessment.policies),
        policy_set_sha256=assessment.policy_set_sha256,
        issues=list(assessment.issues),
        static_analysis_only=True,
        runtime_session_verified=False,
        excluded_scopes=[
            "invocation_flags",
            "transient_approvals",
            "ui_and_session_state",
            "remote_managed_settings",
            "runtime_sandbox_enforcement",
            "runtime_tool_behavior",
        ],
    )


def assessment_for_scan_context(context) -> AgentBoundaryAssessment:
    """Return the one cached boundary assessment used by verify checks."""

    if context.agent_boundary is not None:
        return context.agent_boundary
    verification = context.verification
    if verification is None:
        raise ValueError("boundary assessment requires verification context")
    diff_text = verification.diff_text or "\n".join(
        f"diff --git a/{path} b/{path}" for path in verification.changed_files
    )
    context.agent_boundary = evaluate_agent_boundary(
        workspace=Path(context.config_path).resolve().parent,
        diff_text=diff_text,
        trigger=verification.trigger_result,
        input_mode="provided_diff",
    )
    return context.agent_boundary


def _project_legacy(
    *,
    legacy: CodexBoundaryResultV2,
    violations: list[AgentResultViolatedRule],
    diagnostics: list[AgentResultDiagnostic],
    policy_set: _PolicySet,
    release_decision: dict[str, Any] | None,
) -> CodexBoundaryResultV2:
    needs_reprojection = violations != legacy.violated_rules or bool(policy_set.issues)
    if needs_reprojection:
        decision = _decision_for(violations, release_decision=release_decision)
        risk = _risk_for(violations)
        repair = _repair_for(decision, violations, legacy.agent)
        human = _human_review_for(decision, violations, repair)
        summary = _boundary_summary(decision, violations)
        first_action = _next_action_for(decision, violations, repair)
        control = _control_for_result(
            decision=decision,
            summary=summary,
            first_next_action=first_action,
            human_review=human,
            repair=repair,
            verify_required=legacy.control.verify_required,
            undeclared_gap=any(
                item.code == "undeclared_capability_surface" for item in diagnostics
            ),
            coverage_gap=any(
                item.code == "capability_change_requires_verify" for item in diagnostics
            ),
            trigger_verify_required=bool(
                legacy.trigger and legacy.trigger.get("force_run")
            ),
        )
    else:
        decision = legacy.decision
        risk = legacy.risk_level
        repair = legacy.repair
        summary = legacy.summary
        control = legacy.control
    fingerprints = [_violation_fingerprint(item) for item in violations]
    aggregate = AgentResultPolicy(
        id="agent-boundary",
        version="1",
        source=_aggregate_policy_source(policy_set.records),  # type: ignore[arg-type]
        snapshot_sha256=policy_set.digest,
        discovery=sorted(
            {
                item
                for policy in policy_set.records
                for item in policy.discovery
            }
        ),
    )
    audit_id = _agent_boundary_audit_id(
        changed_files=legacy.changed_files,
        fingerprints=fingerprints,
        policy_digest=policy_set.digest,
    )
    trace = [
        AgentResultTraceEvent(
            step="policy_discovery",
            summary=(
                f"Loaded {len(policy_set.records)} coding-agent boundary policy "
                "families."
            ),
        ),
        *[item for item in legacy.trace if item.step == "coverage"],
        AgentResultTraceEvent(
            step="decision",
            summary=f"Projected {len(violations)} violation(s) to {decision}.",
        ),
    ]
    return legacy.model_copy(
        update={
            "audit_id": audit_id,
            "decision": decision,
            "risk_level": risk,
            "summary": summary,
            "control": control,
            "repair": repair,
            "policy": aggregate,
            "violated_rules": violations,
            "affected_files": _affected_files_for(violations, legacy.changed_files),
            "required_reviewers": list(control.human_review.required_reviewers),
            "explanation": summary,
            "suggested_fixes": [item.recommendation for item in violations[:5]],
            "agent_repair_instructions": _agent_repair_instructions(decision, violations),
            "diagnostics": diagnostics,
            "trace": trace,
            "finding_fingerprints": fingerprints,
            "policy_snapshot_sha256": policy_set.digest,
        }
    )


def _agent_boundary_audit_id(
    *,
    changed_files: list[str],
    fingerprints: list[str],
    policy_digest: str,
) -> str:
    payload = {
        "schema": AGENT_BOUNDARY_RESULT_SCHEMA_VERSION,
        "changed_files": sorted(changed_files),
        "fingerprints": sorted(fingerprints),
        "policy_set_sha256": policy_digest,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return f"agent_boundary_{digest}"


def _aggregate_policy_source(records: tuple[AgentResultPolicy, ...]) -> str:
    sources = {item.source for item in records}
    return next(iter(sources)) if len(sources) == 1 else "workspace"


def _load_policy_set(*, workspace: Path, explicit: Path | None) -> _PolicySet:
    unified = workspace / UNIFIED_POLICY_PATH
    legacy_codex = workspace / LEGACY_CODEX_POLICY_PATH
    legacy_host = workspace / LEGACY_HOST_POLICY_PATH
    issues: list[str] = []
    diagnostics: list[AgentResultDiagnostic] = []

    if explicit is not None:
        selected = explicit if explicit.is_absolute() else workspace / explicit
        codex_path: Path | None = selected
        host_path = selected
        codex_source = host_source = "explicit"
        discovery = [f"explicit:{_display(selected, workspace)}"]
    elif unified.is_file():
        codex_path = unified
        host_path = unified
        codex_source = host_source = "workspace"
        discovery = [f"workspace:{UNIFIED_POLICY_PATH.as_posix()}"]
        coexist = [
            path.relative_to(workspace).as_posix()
            for path in (legacy_codex, legacy_host)
            if path.is_file()
        ]
        if coexist:
            issues.append("unified_and_legacy_policy_coexist")
            diagnostics.append(
                AgentResultDiagnostic(
                    level="error",
                    code="policy_conflict",
                    message=(
                        "Unified and legacy boundary policies coexist: "
                        + ", ".join(coexist)
                    ),
                    path=UNIFIED_POLICY_PATH.as_posix(),
                )
            )
    elif legacy_codex.is_file() or legacy_host.is_file():
        codex_path = LEGACY_CODEX_POLICY_PATH if legacy_codex.is_file() else None
        host_path = LEGACY_HOST_POLICY_PATH
        codex_source = "workspace" if legacy_codex.is_file() else "packaged_default"
        host_source = "workspace" if legacy_host.is_file() else "packaged_default"
        discovery = [
            *( [f"workspace:{LEGACY_CODEX_POLICY_PATH.as_posix()}"] if legacy_codex.is_file() else [] ),
            *( [f"workspace:{LEGACY_HOST_POLICY_PATH.as_posix()}"] if legacy_host.is_file() else [] ),
            "legacy_policy_compatibility",
        ]
    else:
        # The code defaults and the packaged unified YAML are generated from
        # the same rule union.  Loading each family through its established
        # default keeps the frozen Codex-v2 compatibility projection stable.
        codex_path = None
        host_path = LEGACY_HOST_POLICY_PATH
        codex_source = host_source = "packaged_default"
        discovery = ["packaged_default:agent-boundary"]

    codex, codex_diagnostics = load_codex_boundary_policy(
        workspace=workspace,
        policy_path=codex_path,
        allow_foreign_rules=True,
    )
    host, host_diagnostics = load_host_boundary_policy(
        workspace=workspace,
        policy_path=host_path,
    )
    diagnostics.extend(codex_diagnostics)
    diagnostics.extend(host_diagnostics)
    for item in [*codex_diagnostics, *host_diagnostics]:
        if item.level == "error":
            issues.append(item.code)
    codex = replace(
        codex,
        source=codex_source,
        path=(
            None
            if codex_source == "packaged_default"
            else _display(codex_path, workspace)
        ),
    )
    payloads = [_policy_payload(codex), _host_policy_payload(host)]
    digest = hashlib.sha256(
        json.dumps(payloads, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    records = (
        AgentResultPolicy(
            id="codex-boundary",
            version=codex.version,
            source=codex_source,  # type: ignore[arg-type]
            snapshot_sha256=hashlib.sha256(
                json.dumps(payloads[0], sort_keys=True).encode("utf-8")
            ).hexdigest(),
            path=(
                None
                if codex_source == "packaged_default"
                else _display(codex_path, workspace) if codex_path else None
            ),
            discovery=discovery,
        ),
        AgentResultPolicy(
            id="host-boundary",
            version=host.version,
            source=host_source,  # type: ignore[arg-type]
            snapshot_sha256=hashlib.sha256(
                json.dumps(payloads[1], sort_keys=True).encode("utf-8")
            ).hexdigest(),
            path=(
                None
                if host_source == "packaged_default"
                else _display(host_path, workspace)
            ),
            discovery=discovery,
        ),
    )
    return _PolicySet(
        codex=codex,
        host=host,
        diagnostics=tuple(_dedupe_diagnostics(diagnostics)),
        records=records,
        digest=digest,
        issues=tuple(sorted(dict.fromkeys(issues))),
    )


def _coverage_for(
    *,
    changed_files: list[str],
    violations: list[AgentResultViolatedRule],
    issues: list[str],
) -> list[BoundaryHostCoverage]:
    failure_paths = {
        item.path
        for item in violations
        if item.path
        and (
            "parse" in str(item.evidence.get("kind", ""))
            or "unresolved" in str(item.evidence.get("kind", ""))
            or "unknown_host_config_key" == item.evidence.get("kind")
        )
    }
    coverage: list[BoundaryHostCoverage] = []
    for adapter in BOUNDARY_ADAPTERS:
        paths = sorted(path for path in changed_files if adapter.matches(path))
        if any(path in failure_paths for path in paths) or (paths and issues):
            status = "partial"
        elif paths and adapter.experimental:
            status = "experimental"
        elif paths:
            status = "complete"
        else:
            status = "not_applicable"
        coverage.append(
            BoundaryHostCoverage(
                adapter=adapter.id,
                hosts=list(adapter.hosts),
                status=status,
                paths=paths,
                issues=sorted(dict.fromkeys(issues)) if paths and status == "partial" else [],
            )
        )
    return coverage


def _boundary_summary(decision: str, violations: list[AgentResultViolatedRule]) -> str:
    if decision == "allow":
        return "No recognized coding-agent boundary change requires action."
    if decision == "warn":
        return "Boundary evaluation completed with a required agent action."
    if decision == "require_review":
        return f"{len(violations)} coding-agent boundary change(s) require human review."
    return f"{len(violations)} coding-agent boundary change(s) block local continuation."


def _with_unclassified_protected_changes(
    *,
    changed_files: list[str],
    violations: list[AgentResultViolatedRule],
) -> list[AgentResultViolatedRule]:
    covered = {item.path for item in violations if item.path}
    additions: list[AgentResultViolatedRule] = []
    for path in changed_files:
        normalized = path.replace("\\", "/")
        if normalized in covered:
            continue
        if (
            normalized in {"AGENTS.md", "AGENTS.override.md", "CLAUDE.md"}
            or normalized.startswith(".cursor/rules/")
            or normalized.startswith(".agents/skills/")
            or normalized.startswith(".claude/skills/")
        ):
            rule = CODEX_DEFAULT_RULES[
                "CODEX-AGENTS-SHIPGATE-REQUIREMENT-REMOVED"
            ]
            additions.append(
                AgentResultViolatedRule(
                    id=rule.id,
                    check_id=rule.check_id,
                    action=rule.action,  # type: ignore[arg-type]
                    risk_level=rule.risk_level,
                    title="Coding-agent instruction trust root changed",
                    path=path,
                    evidence={"kind": "protected_instruction_change_unclassified"},
                    recommendation=(
                        "Have a human review the instruction trust-root change."
                    ),
                )
            )
        elif normalized.startswith(".github/workflows/") and normalized.count("/") == 2:
            from agents_shipgate.core.host_boundary import DEFAULT_RULES as HOST_DEFAULT_RULES

            rule = HOST_DEFAULT_RULES["HOST-WORKFLOW-PERMISSIONS-EXPANDED"]
            additions.append(
                AgentResultViolatedRule(
                    id=rule.id,
                    check_id=rule.check_id,
                    action=rule.action,  # type: ignore[arg-type]
                    risk_level=rule.risk_level,
                    title="Coding-agent workflow trust root changed",
                    path=path,
                    evidence={"kind": "protected_workflow_change_unclassified"},
                    recommendation="Have a human review the workflow behavior change.",
                )
            )
        elif is_agent_boundary_path(normalized):
            if normalized.startswith(".codex/") or normalized.startswith("policies/"):
                rule = CODEX_DEFAULT_RULES["CODEX-UNKNOWN-PERMISSION-KEY"]
                additions.append(
                    AgentResultViolatedRule(
                        id=rule.id,
                        check_id=rule.check_id,
                        action=rule.action,  # type: ignore[arg-type]
                        risk_level=rule.risk_level,
                        title="Coding-agent boundary trust root changed",
                        path=path,
                        evidence={"kind": "protected_boundary_change_unclassified"},
                        recommendation=(
                            "Have a human review the host boundary change."
                        ),
                    )
                )
            else:
                from agents_shipgate.core.host_boundary import (
                    DEFAULT_RULES as HOST_DEFAULT_RULES,
                )

                rule = HOST_DEFAULT_RULES["HOST-CONFIG-PARSE-FAILED"]
                additions.append(
                    AgentResultViolatedRule(
                        id=rule.id,
                        check_id=rule.check_id,
                        action=rule.action,  # type: ignore[arg-type]
                        risk_level=rule.risk_level,
                        title="Coding-agent boundary trust root changed",
                        path=path,
                        evidence={"kind": "protected_boundary_change_unclassified"},
                        recommendation=(
                            "Have a human review the host boundary change."
                        ),
                    )
                )
    return _dedupe_violations([*violations, *additions])


def _sanitize_violations(
    violations: list[AgentResultViolatedRule],
) -> list[AgentResultViolatedRule]:
    # Reuse the host-inventory sanitizer so check/audit cannot disagree about
    # what credential-bearing strings may enter durable JSON.
    from agents_shipgate.core.host_grants import (
        _redact_secret_values,
        _sanitize_sensitive_string,
    )

    return [
        item.model_copy(
            update={
                "title": _sanitize_sensitive_string(item.title),
                "evidence": _redact_secret_values(item.evidence),
                "recommendation": _sanitize_sensitive_string(item.recommendation),
            }
        )
        for item in violations
    ]


def _sanitize_diagnostics(
    diagnostics: list[AgentResultDiagnostic],
) -> list[AgentResultDiagnostic]:
    from agents_shipgate.core.host_grants import _sanitize_sensitive_string

    return [
        item.model_copy(
            update={"message": _sanitize_sensitive_string(item.message)}
        )
        for item in diagnostics
    ]


def _with_experimental_adapter_changes(
    *,
    changed_files: list[str],
    violations: list[AgentResultViolatedRule],
) -> list[AgentResultViolatedRule]:
    covered = {item.path for item in violations if item.path}
    additions: list[AgentResultViolatedRule] = []
    for path in changed_files:
        if path in covered:
            continue
        adapters = [item for item in BOUNDARY_ADAPTERS if item.matches(path)]
        if not any(item.experimental for item in adapters):
            continue
        from agents_shipgate.core.host_boundary import DEFAULT_RULES as HOST_DEFAULT_RULES

        rule = HOST_DEFAULT_RULES["HOST-CONFIG-PARSE-FAILED"]
        additions.append(
            AgentResultViolatedRule(
                id=rule.id,
                check_id=rule.check_id,
                action=rule.action,  # type: ignore[arg-type]
                risk_level=rule.risk_level,
                title="Experimental host boundary changed",
                path=path,
                evidence={"kind": "experimental_boundary_surface_changed"},
                recommendation=(
                    "Have a human review this experimental host boundary surface."
                ),
            )
        )
    return _dedupe_violations([*violations, *additions])


def _structural_diff_issues(
    *,
    workspace: Path,
    diff_files,
    diff_text: str,
) -> list[BoundaryInputIssue]:
    issues: list[BoundaryInputIssue] = []
    if diff_text.strip() and not diff_files:
        issues.append(
            BoundaryInputIssue(
                code="boundary_diff_unparseable",
                path="<diff>",
                message="The supplied non-empty diff contained no valid file records.",
            )
        )
    for item in diff_files:
        invalid_paths = [
            path
            for path in (item.old_path, item.new_path)
            if path and not _is_canonical_diff_path(path)
        ]
        if invalid_paths:
            issues.extend(
                BoundaryInputIssue(
                    code="boundary_diff_path_invalid",
                    path=path,
                    message="A diff path was absolute, traversing, or non-canonical.",
                )
                for path in sorted(set(invalid_paths))
            )
            continue
        old_boundary = bool(item.old_path and is_agent_boundary_path(item.old_path))
        new_boundary = bool(item.new_path and is_agent_boundary_path(item.new_path))
        if not (old_boundary or new_boundary):
            continue
        path = item.new_path or item.old_path or "<unknown>"
        if item.is_rename:
            if old_boundary and not new_boundary:
                issues.append(
                    BoundaryInputIssue(
                        code="boundary_rename_out_requires_review",
                        path=item.old_path or path,
                        message=(
                            "A tracked file was renamed out of a protected boundary "
                            "path; the removed trust root requires human review."
                        ),
                    )
                )
            if new_boundary:
                issue = _validate_renamed_boundary_head(workspace, item.new_path or path)
                if issue is not None:
                    issues.append(issue)
            continue
        if not item.hunks:
            issues.append(
                BoundaryInputIssue(
                    code="boundary_diff_content_missing",
                    path=path,
                    message=(
                        "A protected boundary diff contained no coherent hunks; "
                        "the supplied artifact cannot prove the change."
                    ),
                )
            )
    return issues


def _is_canonical_diff_path(path: str) -> bool:
    if not path or "\x00" in path or "\\" in path:
        return False
    if path.startswith(("/", "./")) or "//" in path:
        return False
    return ".." not in Path(path).parts


def _validate_renamed_boundary_head(
    workspace: Path,
    path: str,
) -> BoundaryInputIssue | None:
    candidate = workspace / path
    try:
        relative = candidate.relative_to(workspace)
    except ValueError:
        relative = None
    current = workspace
    if relative is None:
        unsafe = True
    else:
        unsafe = False
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                unsafe = True
                break
    try:
        size = candidate.stat().st_size
        candidate.resolve().relative_to(workspace)
    except (OSError, ValueError):
        unsafe = True
        size = 0
    if unsafe or not candidate.is_file():
        code = "boundary_rename_target_unresolved"
    elif size > 128 * 1024:
        code = "boundary_rename_target_oversized"
    else:
        try:
            raw = candidate.read_bytes()
            if b"\x00" in raw:
                raise UnicodeError
            raw.decode("utf-8")
            return None
        except (OSError, UnicodeError):
            code = "boundary_rename_target_unreadable"
    return BoundaryInputIssue(
        code=code,
        path=path,
        message="The renamed protected boundary file could not be evaluated safely.",
    )


def _aggregate_policy(assessment: AgentBoundaryAssessment) -> AgentResultPolicy:
    sources = {item.source for item in assessment.policies}
    source = next(iter(sources)) if len(sources) == 1 else "workspace"
    return AgentResultPolicy(
        id="agent-boundary",
        version="1",
        source=source,  # type: ignore[arg-type]
        snapshot_sha256=assessment.policy_set_sha256,
        discovery=sorted(
            {item for policy in assessment.policies for item in policy.discovery}
        ),
    )


def _policy_payload(policy: CodexBoundaryPolicy) -> dict[str, Any]:
    return {
        "id": policy.id,
        "version": policy.version,
        "rules": [vars(policy.rules[key]) for key in sorted(policy.rules)],
    }


def _host_policy_payload(policy: HostBoundaryPolicy) -> dict[str, Any]:
    return {
        "id": policy.id,
        "version": policy.version,
        "rules": [vars(policy.rules[key]) for key in sorted(policy.rules)],
    }


def _display(path: Path | None, workspace: Path) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(workspace).as_posix()
    except ValueError:
        return str(path)


def _dedupe_diagnostics(items: list[AgentResultDiagnostic]) -> list[AgentResultDiagnostic]:
    by_key = {
        json.dumps(item.model_dump(mode="json"), sort_keys=True): item for item in items
    }
    return [by_key[key] for key in sorted(by_key)]


__all__ = [
    "AGENT_BOUNDARY_RESULT_SCHEMA_VERSION",
    "AgentBoundaryAssessment",
    "assessment_for_scan_context",
    "build_agent_boundary_result",
    "evaluate_agent_boundary",
]
