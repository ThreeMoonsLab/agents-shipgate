"""Central deterministic multi-host boundary assessment.

Catalog/actor selection is never authority: every registered repository
boundary adapter runs for every invocation.  The resulting assessment is the
single input to neutral and legacy local-control projections.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from agents_shipgate.core.boundary_diff import (
    BoundaryInputIssue,
    DiffFile,
    git_diff_path_token,
    parse_unified_diff,
)
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
    _pending_review_for,
    _repair_for,
    _required_reviewers_for,
    _risk_for,
    _violation_fingerprint,
    detect_command_for,
    evaluate_codex_boundary_result,
    load_codex_boundary_policy,
    preview_command_for,
    verify_command_for,
    violations_within_agent_actionable_band,
)
from agents_shipgate.core.host_boundary import (
    DEFAULT_POLICY_PATH as LEGACY_HOST_POLICY_PATH,
)
from agents_shipgate.core.host_boundary import (
    HostBoundaryPolicy,
    evaluate_host_boundary,
    load_host_boundary_policy,
)
from agents_shipgate.core.host_grants import (
    HostBoundarySnapshot,
    build_host_boundary_snapshot,
)
from agents_shipgate.core.trust_roots import (
    is_configured_manifest,
    is_portable_repo_path,
    read_absolute_identity_bound_text,
    trust_root_class_for,
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

# The actor every pre-detection audit id implicitly described.
_LEGACY_AUDIT_ACTOR = "codex"

UNIFIED_POLICY_PATH = Path("policies/agent-boundary.shipgate.yaml")
LEGACY_CODEX_POLICY_PATH = Path("policies/codex-boundary.shipgate.yaml")


@dataclass(frozen=True)
class AgentBoundaryAssessment:
    actor: str
    verify_command: str
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
    host_snapshot: HostBoundarySnapshot
    legacy_result: CodexBoundaryResultV2


@dataclass(frozen=True)
class _PolicySet:
    codex: CodexBoundaryPolicy
    host: HostBoundaryPolicy
    diagnostics: tuple[AgentResultDiagnostic, ...]
    records: tuple[AgentResultPolicy, ...]
    digest: str
    issues: tuple[str, ...]


@dataclass(frozen=True)
class _GenericBoundaryRule:
    id: str
    check_id: str
    title: str
    action: str
    risk_level: str
    recommendation: str


_GENERIC_RULES = {
    "PROTECTED-SURFACE-UNCLASSIFIED": _GenericBoundaryRule(
        id="BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED",
        check_id="SHIP-AGENT-BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED",
        title="Protected coding-agent surface lacks a safe static classification",
        action="require_review",
        risk_level="medium",
        recommendation="Have a human review the protected boundary change.",
    ),
    "EXPERIMENTAL-SURFACE-CHANGED": _GenericBoundaryRule(
        id="BOUNDARY-EXPERIMENTAL-SURFACE-CHANGED",
        check_id="SHIP-AGENT-BOUNDARY-EXPERIMENTAL-SURFACE-CHANGED",
        title="Experimental coding-agent boundary surface changed",
        action="require_review",
        risk_level="high",
        recommendation="Have a human review the experimental boundary surface.",
    ),
    "STATIC-REQUIREMENTS-CHANGED": _GenericBoundaryRule(
        id="BOUNDARY-STATIC-REQUIREMENTS-CHANGED",
        check_id="SHIP-AGENT-BOUNDARY-STATIC-REQUIREMENTS-CHANGED",
        title="Static host requirements changed",
        action="require_review",
        risk_level="high",
        recommendation="Have a human review the static host requirements change.",
    ),
    "INPUT-INCOMPLETE": _GenericBoundaryRule(
        id="BOUNDARY-INPUT-INCOMPLETE",
        check_id="SHIP-AGENT-BOUNDARY-INPUT-INCOMPLETE",
        title="Boundary input is incomplete",
        action="require_review",
        risk_level="medium",
        recommendation="Provide a complete, coherent boundary diff and rerun the check.",
    ),
}


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
    host_snapshot: HostBoundarySnapshot | None = None,
    changed_files_override: list[str] | None = None,
    config_path: Path | None = None,
    requested_workspace: Path | None = None,
    base: str | None = None,
    head: str | None = None,
    verification_replayable: bool = False,
    base_manifest_absent: bool | None = None,
) -> AgentBoundaryAssessment:
    # The command this assessment authorizes must evaluate the target that was
    # actually checked, not the default manifest in the current directory.
    verify_command = verify_command_for(
        requested_workspace,
        config_path,
        base=base,
        head=head,
    )
    detect_command = detect_command_for(requested_workspace)
    preview_command = preview_command_for(
        requested_workspace,
        config_path,
        base=base,
        head=head,
    )
    workspace = workspace.resolve()
    host_snapshot = host_snapshot or build_host_boundary_snapshot(
        workspace,
        scope="repository",
    )
    diff_files = parse_unified_diff(diff_text)
    changed_files = sorted(
        {
            *(changed_files_override or []),
            *(
                path
                for item in diff_files
                for path in (item.new_path, item.old_path)
                if path
            ),
        }
    )
    input_issues = [
        *(input_issues or []),
        *[
            BoundaryInputIssue(
                code=f"host_inventory_{item.get('kind', 'unresolved')}",
                path=str(item.get("source") or ""),
                message=str(
                    item.get("message")
                    or "A repository host-boundary source could not be inventoried."
                ),
            )
            for item in host_snapshot.inventory.get("issues", [])
            if item.get("blocking")
        ],
        *_structural_diff_issues(
            workspace=workspace,
            diff_files=diff_files,
            diff_text=diff_text,
        ),
    ]
    policies = _load_policy_set(workspace=workspace, explicit=policy_path)
    resolved_text_cache = {}

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
        diff_files_override=diff_files,
        changed_files_override=changed_files,
        resolved_text_cache=resolved_text_cache,
        static_read_cache=host_snapshot.cache,
        verify_command=verify_command,
        detect_command=detect_command,
        preview_command=preview_command,
        verification_replayable=verification_replayable,
        discovery_replayable=input_mode != "git_range",
        manifest_label=_manifest_label(config_path, workspace),
    )
    host_violations, host_diagnostics = evaluate_host_boundary(
        workspace=workspace,
        diff_text=diff_text,
        policy_override=policies.host,
        diff_files_override=diff_files,
        resolved_text_cache=resolved_text_cache,
        static_read_cache=host_snapshot.cache,
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
        adoption_paths=_manifest_adoption_paths(
            diff_files,
            config_path,
            workspace,
            base_manifest_absent=base_manifest_absent,
        ),
        config_path=config_path,
        policy_path=policy_path,
        workspace=workspace,
        evaluated_paths={
            item.path
            for item in diagnostics
            if (
                (item.code == "content_source" and item.path == ".codex/config.toml")
                or item.code == "proposal_safe_manifest_addition"
                or item.code == "version_synced_instruction_document"
            )
        },
    )
    combined = _with_experimental_adapter_changes(
        changed_files=changed_files,
        violations=combined,
    )
    combined = _sanitize_violations(combined)
    if input_issues:
        rule = _GENERIC_RULES["INPUT-INCOMPLETE"]
        combined = _dedupe_violations(
            [
                *combined,
                *[
                    AgentResultViolatedRule(
                        id=rule.id,
                        check_id=rule.check_id,
                        action=rule.action,  # type: ignore[arg-type]
                        risk_level=rule.risk_level,  # type: ignore[arg-type]
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
        rule = _GENERIC_RULES["INPUT-INCOMPLETE"]
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
                    risk_level=rule.risk_level,  # type: ignore[arg-type]
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
        verify_command=verify_command,
        legacy=legacy,
        violations=combined,
        diagnostics=diagnostics,
        policy_set=policies,
        release_decision=release_decision,
        detect_command=detect_command,
        input_mode=input_mode,
        verification_replayable=verification_replayable,
        discovery_replayable=input_mode != "git_range",
        diff_text=diff_text,
    )
    invocation_shared_paths = {
        path
        for path in changed_files
        if (
            _is_invocation_path(
                policy_path,
                path,
                workspace=workspace,
            )
            or _is_invocation_path(
                config_path,
                path,
                workspace=workspace,
            )
        )
    }
    affected_hosts = tuple(
        sorted(
            {
                *(
                    host
                    for path in changed_files
                    for host in boundary_hosts_for_path(path)
                ),
                *(
                    {"codex", "claude-code", "cursor"}
                    if invocation_shared_paths
                    else set()
                ),
            }
        )
    )
    coverage = _coverage_for(
        changed_files=changed_files,
        violations=combined,
        issues=issue_codes,
        invocation_shared_paths=invocation_shared_paths,
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
        verify_command=verify_command,
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
        host_snapshot=host_snapshot,
        legacy_result=projected,
    )


def build_agent_boundary_result(assessment: AgentBoundaryAssessment) -> AgentBoundaryResultV1:
    legacy = assessment.legacy_result
    aggregate_policy = _aggregate_policy(assessment)
    pending_review = (
        _pending_review_for(
            assessment.violations,
            _required_reviewers_for(legacy.decision, list(assessment.violations)),
        )
        if legacy.control.state == "agent_action_required"
        and legacy.decision == "require_review"
        else []
    )
    return AgentBoundaryResultV1(
        **legacy.model_dump(mode="python", exclude={"schema_version", "policy"}),
        pending_review=pending_review,
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
        violations=list(assessment.violations),
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
        "diff --git "
        f"{git_diff_path_token('a/', path)} {git_diff_path_token('b/', path)}"
        for path in verification.changed_files
    )
    configured_manifest = (
        Path(verification.configured_manifest_path)
        if verification.configured_manifest_path
        else Path(context.config_path)
    )
    context.agent_boundary = evaluate_agent_boundary(
        workspace=_scan_workspace(
            config_path=Path(context.config_path),
            configured_manifest=configured_manifest,
        ),
        diff_text=diff_text,
        trigger=verification.trigger_result,
        input_mode="provided_diff",
        changed_files_override=list(verification.changed_files),
        # Without this a custom-named manifest produced the protected-surface
        # boundary finding under local `check` but not under full `verify`, so
        # the two public surfaces published different evidence for one diff.
        config_path=configured_manifest,
        verification_replayable=True,
        # Verify already proved this fact from the comparison base. Reusing it
        # keeps the cached boundary projection from re-inferring adoption from
        # diff shape alone.
        base_manifest_absent=verification.manifest_introduced,
    )
    return context.agent_boundary


def _scan_workspace(*, config_path: Path, configured_manifest: Path) -> Path:
    """Recover the scan's repository root from its stable manifest identity.

    Verify scans a committed head from a temporary archive. ``config_path`` is
    therefore physical (``<tmp>/head/services/support/new-gate.yml``), while
    changed paths and ``configured_manifest`` are repository-relative
    (``services/support/new-gate.yml``). Removing those stable path components
    recovers the archive root, so boundary content resolution and configured-
    manifest comparison use the same coordinate system.

    Direct scan callers predating the additive identity field retain the
    historical manifest-parent fallback.
    """

    resolved_config = config_path.resolve()
    if configured_manifest.is_absolute():
        return resolved_config.parent
    components = configured_manifest.parts
    if not components or any(part == ".." for part in components):
        return resolved_config.parent
    root = resolved_config
    for _part in components:
        root = root.parent
    try:
        if (root / configured_manifest).resolve() == resolved_config:
            return root
    except OSError:
        pass
    return resolved_config.parent


def _project_legacy(
    *,
    legacy: CodexBoundaryResultV2,
    violations: list[AgentResultViolatedRule],
    diagnostics: list[AgentResultDiagnostic],
    policy_set: _PolicySet,
    release_decision: dict[str, Any] | None,
    verify_command: str | None = None,
    detect_command: str | None = None,
    input_mode: Literal["worktree", "git_range", "provided_diff"] = "provided_diff",
    verification_replayable: bool = True,
    discovery_replayable: bool = True,
    diff_text: str = "",
) -> CodexBoundaryResultV2:
    needs_reprojection = violations != legacy.violated_rules or bool(policy_set.issues)
    if needs_reprojection:
        decision = _decision_for(violations, release_decision=release_decision)
        risk = _risk_for(violations)
        repair = _repair_for(decision, violations, legacy.agent)
        human = _human_review_for(decision, violations, repair)
        summary = _boundary_summary(decision, violations)
        undeclared_gap = any(
            item.code == "undeclared_capability_surface"
            for item in diagnostics
        )
        first_action = (
            legacy.control.next_action
            if undeclared_gap
            and getattr(legacy.control.next_action, "command", None)
            else _next_action_for(decision, violations, repair)
        )
        control = _control_for_result(
            verify_command=verify_command,
            decision=decision,
            summary=summary,
            first_next_action=first_action,
            human_review=human,
            repair=repair,
            verify_required=legacy.control.verify_required,
            undeclared_gap=undeclared_gap,
            coverage_gap=any(
                item.code == "capability_change_requires_verify" for item in diagnostics
            ),
            trigger_verify_required=bool(
                legacy.trigger and legacy.trigger.get("force_run")
            ),
            violations=violations,
            detect_command=detect_command,
            verification_replayable=verification_replayable,
            discovery_replayable=discovery_replayable,
        )
    else:
        decision = legacy.decision
        risk = legacy.risk_level
        repair = legacy.repair
        summary = _boundary_summary(decision, violations)
        control = legacy.control.model_copy(
            update={
                "reason": (
                    legacy.control.reason
                    if (
                        (not verification_replayable or not discovery_replayable)
                        and legacy.control.verify_required
                    )
                    else summary
                )
            }
        )
    if (
        (not verification_replayable or not discovery_replayable)
        and control.state == "human_review_required"
    ):
        summary = control.reason
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
        actor=legacy.agent,
        changed_files=legacy.changed_files,
        fingerprints=fingerprints,
        policy_digest=policy_set.digest,
        input_mode=input_mode,
        verification_replayable=verification_replayable,
        trigger=legacy.trigger,
        control=control.model_dump(mode="json", exclude_none=True),
        diff_text=diff_text,
        legacy_audit_id=legacy.audit_id,
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
    actor: str,
    changed_files: list[str],
    fingerprints: list[str],
    policy_digest: str,
    input_mode: Literal["worktree", "git_range", "provided_diff"],
    verification_replayable: bool,
    trigger: dict[str, Any] | None = None,
    control: dict[str, Any] | None = None,
    diff_text: str = "",
    legacy_audit_id: str | None = None,
) -> str:
    """Identity of one audited evaluation.

    The actor belongs in it: the result records which agent was evaluated, so
    two runs differing only by actor are two audit rows, not one — without it,
    detecting the actor changed the label while leaving every Claude Code and
    Cursor run indistinguishable from a codex run in the audit trail.

    Actor is folded in only for a non-default actor. The input binding is
    omitted only for the legacy verify projection (a replayable provided diff)
    so established ids for that exact substrate stay stable. Worktree, ref
    range, and detached provided-diff evaluations can produce different
    controls from identical text and therefore must be distinct audit rows.
    """

    legacy_subject = input_mode == "provided_diff" and verification_replayable
    payload = {
        "schema": AGENT_BOUNDARY_RESULT_SCHEMA_VERSION,
        # Added only for a non-default actor, so every id issued before actor
        # detection existed keeps its value. Rotating established codex ids
        # would break anyone who stored one, and the identity contract is not
        # versioned separately from the schema.
        **({"actor": actor} if actor != _LEGACY_AUDIT_ACTOR else {}),
        **(
            {
                "input_mode": input_mode,
                "verification_replayable": verification_replayable,
            }
            if not legacy_subject
            else {}
        ),
        **(
            {
                "trigger": {
                    key: trigger.get(key)
                    for key in (
                        "should_run",
                        "run_shipgate",
                        "force_run",
                        "dry_run_recommended",
                        "skip_reason",
                    )
                    if trigger is not None and key in trigger
                },
                "control": {
                    "state": (control or {}).get("state"),
                    "verify_required": (control or {}).get("verify_required"),
                    "next_action_kind": (
                        ((control or {}).get("next_action") or {}).get("kind")
                        if isinstance((control or {}).get("next_action"), dict)
                        else None
                    ),
                },
                "subject_sha256": hashlib.sha256(diff_text.encode()).hexdigest(),
                "legacy_audit_id": legacy_audit_id,
            }
            if not legacy_subject
            else {}
        ),
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

    shared_policy_text: str | None = None
    if codex_path is not None:
        codex_candidate = (
            codex_path if codex_path.is_absolute() else workspace / codex_path
        )
        host_candidate = host_path if host_path.is_absolute() else workspace / host_path
        if codex_candidate == host_candidate and codex_candidate.is_file():
            try:
                shared_policy_text = read_absolute_identity_bound_text(
                    codex_candidate,
                    max_bytes=1024 * 1024,
                )
            except (OSError, UnicodeDecodeError, ValueError):
                # Family loaders retain their established diagnostics and
                # default-policy fallback when the shared capture fails.
                shared_policy_text = None
    codex, codex_diagnostics = load_codex_boundary_policy(
        workspace=workspace,
        policy_path=codex_path,
        allow_foreign_rules=True,
        policy_text=shared_policy_text,
    )
    host, host_diagnostics = load_host_boundary_policy(
        workspace=workspace,
        policy_path=host_path,
        policy_text=shared_policy_text,
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
    payloads = [_policy_payload(codex), _policy_payload(host)]
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
    invocation_shared_paths: set[str] | None = None,
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
        if adapter.id == "shared" and invocation_shared_paths:
            paths = sorted({*paths, *invocation_shared_paths})
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
        # Match the control state the same facts produce: a graded set does not
        # stop the turn, so the summary must not read as a local stop.
        if violations_within_agent_actionable_band(violations):
            return (
                f"{len(violations)} coding-agent boundary change(s) need PR-time "
                "review; verify, then report them."
            )
        return f"{len(violations)} coding-agent boundary change(s) require human review."
    return f"{len(violations)} coding-agent boundary change(s) block local continuation."


def _manifest_adoption_paths(
    diff_files: list[DiffFile],
    config_path: Path | None = None,
    workspace: Path | None = None,
    *,
    base_manifest_absent: bool | None = None,
) -> frozenset[str]:
    """Paths where this diff *introduces* a Shipgate manifest.

    "Adopting the gate" and "changing the gate" deserve different words, and a
    pure addition is the only shape that is unambiguously the first. The
    qualification is deliberately narrow: exactly one manifest record in the
    whole diff, and that record a plain addition. A diff that also modifies,
    deletes, or renames a manifest is touching an existing gate — PR #282's
    lesson is that a block-level "this part is safe" signal must never soften a
    path-wide fail-closed guard, and the composite shapes are exactly where
    that goes wrong.

    Only the wording moves: the rule id, action, and risk level are unchanged,
    so the local decision and the control state are identical either way.
    """

    # A plain added file proves only that this path is new. It does not prove
    # that the repository had no operational manifest under another name.
    # Callers with a git/worktree subject supply the separately established
    # base fact; raw diff callers receive neutral protected-surface wording.
    if base_manifest_absent is not True:
        return frozenset()

    records = [
        item
        for item in diff_files
        for candidate in (item.new_path, item.old_path)
        if candidate
        and (
            trust_root_class_for(candidate.replace("\\", "/")) == "manifest"
            or is_configured_manifest(config_path, candidate, workspace=workspace)
        )
    ]
    if len(records) != 1:
        return frozenset()
    only = records[0]
    if not only.is_new or only.is_deleted or only.is_rename or only.old_path:
        return frozenset()
    return frozenset({only.path.replace("\\", "/")})


def _with_unclassified_protected_changes(
    *,
    changed_files: list[str],
    violations: list[AgentResultViolatedRule],
    evaluated_paths: set[str],
    adoption_paths: frozenset[str] = frozenset(),
    config_path: Path | None = None,
    policy_path: Path | None = None,
    workspace: Path | None = None,
) -> list[AgentResultViolatedRule]:
    covered = {item.path for item in violations if item.path}
    additions: list[AgentResultViolatedRule] = []
    for path in changed_files:
        normalized = path.replace("\\", "/")
        if (
            normalized in covered
            or normalized in evaluated_paths
            or not (
                is_agent_boundary_path(normalized)
                or trust_root_class_for(normalized) is not None
                # The manifest this invocation loaded is a protected surface
                # whatever it is called: a repository run with
                # ``--config new-gate.yml`` otherwise got ``allow`` and no
                # violations for a diff that rewrote its own gate.
                or is_configured_manifest(
                    config_path, normalized, workspace=workspace
                )
                # An explicit ``--policy`` is the live boundary gate for this
                # invocation even when it has a custom name outside
                # ``policies/*.shipgate.yaml``.
                or _is_invocation_path(
                    policy_path,
                    normalized,
                    workspace=workspace,
                )
            )
        ):
            continue
        kind = (
            "STATIC-REQUIREMENTS-CHANGED"
            if normalized == ".codex/requirements.toml"
            else "PROTECTED-SURFACE-UNCLASSIFIED"
        )
        rule = _GENERIC_RULES[kind]
        adopting = normalized in adoption_paths
        # Recorded only for a manifest the path table cannot see, so existing
        # rows keep their fingerprints. The band predicate reads it to keep a
        # gate-governing surface out of the graded route.
        configured_manifest = trust_root_class_for(
            normalized
        ) is None and is_configured_manifest(
            config_path, normalized, workspace=workspace
        )
        configured_policy = (
            trust_root_class_for(normalized) is None
            and _is_invocation_path(
                policy_path,
                normalized,
                workspace=workspace,
            )
        )
        additions.append(
            AgentResultViolatedRule(
                id=rule.id,
                check_id=rule.check_id,
                action=rule.action,  # type: ignore[arg-type]
                risk_level=rule.risk_level,  # type: ignore[arg-type]
                title=(
                    "Adopting Agents Shipgate: this change introduces the manifest"
                    if adopting
                    else rule.title
                ),
                path=path,
                evidence={
                    "kind": (
                        "manifest_introduced"
                        if adopting
                        else "static_requirements_changed"
                        if kind == "STATIC-REQUIREMENTS-CHANGED"
                        else "protected_surface_unclassified"
                    ),
                    **(
                        {"trust_root_class": "manifest"}
                        if configured_manifest
                        else {"trust_root_class": "policy"}
                        if configured_policy
                        else {}
                    ),
                },
                recommendation=(
                    f"Review {normalized} and merge the adoption "
                    "through a human-reviewed PR; a coding agent cannot adopt a "
                    "release policy on the repository's behalf."
                    if adopting
                    else rule.recommendation
                ),
            )
        )
    return _dedupe_violations([*violations, *additions])


def _is_invocation_path(
    selected_path: Path | None,
    changed_path: str,
    *,
    workspace: Path,
) -> bool:
    """Match one CLI-selected repo file to its exact changed-path identity."""

    return is_configured_manifest(
        selected_path,
        changed_path,
        workspace=workspace,
    )


def _manifest_label(config_path: Path | None, workspace: Path) -> str:
    if config_path is None:
        return "shipgate.yaml"
    if not config_path.is_absolute():
        return config_path.as_posix()
    try:
        return config_path.relative_to(workspace).as_posix()
    except ValueError:
        return config_path.as_posix()


def _sanitize_violations(
    violations: list[AgentResultViolatedRule],
) -> list[AgentResultViolatedRule]:
    # Reuse the host-inventory sanitizer so check/audit cannot disagree about
    # what credential-bearing strings may enter durable JSON.
    from agents_shipgate.core.host_grants import _redact_secret_values

    return [
        item.model_copy(
            update={
                "title": _sanitize_boundary_string(item.title),
                "evidence": _sanitize_boundary_value(
                    _redact_secret_values(item.evidence)
                ),
                "recommendation": _sanitize_boundary_string(item.recommendation),
            }
        )
        for item in violations
    ]


def _sanitize_diagnostics(
    diagnostics: list[AgentResultDiagnostic],
) -> list[AgentResultDiagnostic]:
    return [
        item.model_copy(
            update={"message": _sanitize_boundary_string(item.message)}
        )
        for item in diagnostics
    ]


_BOUNDARY_URL_RE = re.compile(r"(?:https?|wss?)://[^\s'\"<>]+")


def _sanitize_boundary_string(value: str) -> str:
    from agents_shipgate.core.host_grants import _sanitize_sensitive_string

    sanitized = _sanitize_sensitive_string(value)

    def replace_url(match: re.Match[str]) -> str:
        try:
            parsed = urlsplit(match.group(0))
            hostname = parsed.hostname or ""
            netloc = f"{hostname}:{parsed.port}" if parsed.port is not None else hostname
            path = "/<redacted-path>" if parsed.path not in {"", "/"} else parsed.path
            return f"{parsed.scheme}://{netloc}{path}"
        except ValueError:
            return "<redacted-url>"

    return _BOUNDARY_URL_RE.sub(replace_url, sanitized)


def _sanitize_boundary_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize_boundary_value(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_sanitize_boundary_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_boundary_value(item) for item in value]
    if isinstance(value, str):
        return _sanitize_boundary_string(value)
    return value


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
        rule = _GENERIC_RULES["EXPERIMENTAL-SURFACE-CHANGED"]
        additions.append(
            AgentResultViolatedRule(
                id=rule.id,
                check_id=rule.check_id,
                action=rule.action,  # type: ignore[arg-type]
                risk_level=rule.risk_level,  # type: ignore[arg-type]
                title=rule.title,
                path=path,
                evidence={"kind": "experimental_boundary_surface_changed"},
                recommendation=rule.recommendation,
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
    seen_paths: set[str] = set()
    if diff_text.strip() and not diff_files:
        issues.append(
            BoundaryInputIssue(
                code="boundary_diff_unparseable",
                path="<diff>",
                message="The supplied non-empty diff contained no valid file records.",
            )
        )
    for item in diff_files:
        record_paths = {
            path for path in (item.old_path, item.new_path) if path is not None
        }
        duplicate_paths = sorted(record_paths.intersection(seen_paths))
        seen_paths.update(record_paths)
        if duplicate_paths:
            issues.extend(
                BoundaryInputIssue(
                    code="boundary_diff_shape_invalid",
                    path=path,
                    message=(
                        "The supplied diff contains more than one file record "
                        "for the same path; one coherent record per path is required."
                    ),
                )
                for path in duplicate_paths
            )
            continue
        shape_errors = [
            *(
                ["new-file record retains an old path"]
                if item.is_new and item.old_path is not None
                else []
            ),
            *(
                ["deleted-file record retains a new path"]
                if item.is_deleted and item.new_path is not None
                else []
            ),
            *(
                ["record is both new and deleted"]
                if item.is_new and item.is_deleted
                else []
            ),
            *(
                ["new/deleted record is also marked as a rename"]
                if item.is_rename and (item.is_new or item.is_deleted)
                else []
            ),
        ]
        if shape_errors:
            issues.append(
                BoundaryInputIssue(
                    code="boundary_diff_shape_invalid",
                    path=item.path or "<diff>",
                    message=(
                        "A diff record has contradictory file-mode and path "
                        f"headers: {', '.join(shape_errors)}."
                    ),
                )
            )
            continue
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
    return is_portable_repo_path(path)


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


def _policy_payload(
    policy: CodexBoundaryPolicy | HostBoundaryPolicy,
) -> dict[str, Any]:
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
