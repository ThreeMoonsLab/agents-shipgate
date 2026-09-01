"""P0 exact-outcome safety canaries for evidence-backed ``passed``.

These cases are deliberately data-driven and named.  The catalog-count test
is part of the qualification contract: deleting or silently de-parameterizing
a canary is itself a test failure.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pytest

from agents_shipgate.cli.scan import run_scan
from agents_shipgate.core.baseline import write_baseline
from agents_shipgate.core.domain import (
    AuthAlternative,
    AuthInfo,
    AuthSchemeRequirement,
    Tool,
    ToolRiskHint,
)
from agents_shipgate.core.semantic_assessment import assess_tool_semantics
from agents_shipgate.schemas.manifest import ActionDeclarationConfig

CanaryCategory = Literal[
    "evidence_completeness",
    "contradiction_evasion",
    "dilution_cross_source",
    "effect_control_transition",
    "trust_root_bypass",
    "robustness_metamorphic",
]


@dataclass(frozen=True)
class SemanticCanary:
    category: CanaryCategory
    name: str
    tool: dict[str, Any] = field(default_factory=dict)
    declaration: dict[str, Any] | None = None
    effect_status: str = "unknown"
    effect: str = "write"
    authority_status: str = "unknown"
    authority_mode: str = "unknown"
    pass_eligible: bool = False
    effect_issues: frozenset[str] = frozenset()
    authority_issues: frozenset[str] = frozenset()


def _auth_none() -> AuthInfo:
    return AuthInfo(source="test", mode="none", explicit=True)


def _auth_scoped(*scopes: str) -> AuthInfo:
    return AuthInfo(
        type="oauth2",
        scopes=list(scopes),
        source="test",
        mode="scoped",
        explicit=True,
    )


EVIDENCE_COMPLETENESS_CASES = [
    SemanticCanary(
        "evidence_completeness",
        "neutral_mcp_requires_effect_and_authority",
        effect_status="protocol_default",
        authority_status="unknown",
        effect_issues=frozenset({"missing_effect_evidence"}),
        authority_issues=frozenset({"missing_authority_evidence"}),
    ),
    SemanticCanary(
        "evidence_completeness",
        "mcp_explicit_read_and_none_is_eligible",
        tool={"annotations": {"readOnlyHint": True}, "auth": _auth_none()},
        effect_status="structural",
        effect="read",
        authority_status="structural",
        authority_mode="none",
        pass_eligible=True,
    ),
    SemanticCanary(
        "evidence_completeness",
        "mcp_explicit_destructive_and_none_is_semantically_eligible",
        tool={"annotations": {"destructiveHint": True}, "auth": _auth_none()},
        effect_status="structural",
        effect="destructive",
        authority_status="structural",
        authority_mode="none",
        pass_eligible=True,
    ),
    SemanticCanary(
        "evidence_completeness",
        "openapi_get_is_structural_read",
        tool={
            "source_type": "openapi",
            "annotations": {"httpMethod": "GET"},
            "auth": _auth_none(),
        },
        effect_status="structural",
        effect="read",
        authority_status="structural",
        authority_mode="none",
        pass_eligible=True,
    ),
    SemanticCanary(
        "evidence_completeness",
        "openapi_head_is_structural_read",
        tool={
            "source_type": "openapi",
            "annotations": {"httpMethod": "HEAD"},
            "auth": _auth_none(),
        },
        effect_status="structural",
        effect="read",
        authority_status="structural",
        authority_mode="none",
        pass_eligible=True,
    ),
    SemanticCanary(
        "evidence_completeness",
        "openapi_options_is_structural_read",
        tool={
            "source_type": "openapi",
            "annotations": {"httpMethod": "OPTIONS"},
            "auth": _auth_none(),
        },
        effect_status="structural",
        effect="read",
        authority_status="structural",
        authority_mode="none",
        pass_eligible=True,
    ),
    SemanticCanary(
        "evidence_completeness",
        "openapi_post_is_structural_write",
        tool={
            "source_type": "openapi",
            "annotations": {"httpMethod": "POST"},
            "auth": _auth_none(),
        },
        effect_status="structural",
        effect="write",
        authority_status="structural",
        authority_mode="none",
        pass_eligible=True,
    ),
    SemanticCanary(
        "evidence_completeness",
        "openapi_put_with_scoped_oauth_is_eligible",
        tool={
            "source_type": "openapi",
            "annotations": {"httpMethod": "PUT"},
            "auth": _auth_scoped("orders:write"),
        },
        effect_status="structural",
        effect="write",
        authority_status="structural",
        authority_mode="scoped",
        pass_eligible=True,
    ),
    SemanticCanary(
        "evidence_completeness",
        "openapi_patch_with_unscoped_key_requires_review",
        tool={
            "source_type": "openapi",
            "annotations": {"httpMethod": "PATCH"},
            "auth": AuthInfo(
                type="apiKey",
                source="openapi",
                mode="unscoped",
                explicit=True,
            ),
        },
        effect_status="structural",
        effect="write",
        authority_status="structural",
        authority_mode="unscoped",
    ),
    SemanticCanary(
        "evidence_completeness",
        "openapi_delete_is_structural_destructive",
        tool={
            "source_type": "openapi",
            "annotations": {"httpMethod": "DELETE"},
            "auth": _auth_none(),
        },
        effect_status="structural",
        effect="destructive",
        authority_status="structural",
        authority_mode="none",
        pass_eligible=True,
    ),
    SemanticCanary(
        "evidence_completeness",
        "ast_function_without_inventory_is_incomplete",
        tool={"source_type": "sdk_function", "auth": _auth_none()},
        effect_status="unknown",
        authority_status="structural",
        authority_mode="none",
        effect_issues=frozenset({"missing_effect_evidence", "incomplete_surface"}),
    ),
    SemanticCanary(
        "evidence_completeness",
        "ast_declaration_cannot_replace_inventory",
        tool={"source_type": "sdk_function"},
        declaration={
            "tool": "process_order",
            "effect": "write",
            "authority": {"mode": "none"},
        },
        effect_status="declared",
        authority_status="declared",
        authority_mode="none",
        effect_issues=frozenset({"incomplete_surface"}),
    ),
    SemanticCanary(
        "evidence_completeness",
        "reviewed_framework_inventory_can_be_eligible",
        tool={"source_type": "langchain_inventory"},
        declaration={
            "tool": "process_order",
            "effect": "write",
            "authority": {"mode": "none"},
        },
        effect_status="declared",
        authority_status="declared",
        authority_mode="none",
        pass_eligible=True,
    ),
    SemanticCanary(
        "evidence_completeness",
        "low_confidence_extraction_requires_attestation",
        tool={
            "source_type": "openapi",
            "annotations": {"httpMethod": "GET"},
            "auth": _auth_none(),
            "extraction_confidence": "low",
            "extraction": {"method": "test", "confidence": "low"},
        },
        effect_status="structural",
        effect="read",
        authority_status="structural",
        authority_mode="none",
        effect_issues=frozenset({"unattested_surface"}),
    ),
    SemanticCanary(
        "evidence_completeness",
        "malformed_mcp_boolean_is_not_evidence",
        tool={"annotations": {"readOnlyHint": "true"}, "auth": _auth_none()},
        effect_status="protocol_default",
        authority_status="structural",
        authority_mode="none",
        effect_issues=frozenset(
            {"invalid_semantic_annotation", "missing_effect_evidence"}
        ),
    ),
    SemanticCanary(
        "evidence_completeness",
        "read_scope_does_not_prove_read_only_effect",
        tool={"auth": _auth_scoped("orders:read")},
        effect_status="protocol_default",
        authority_status="structural",
        authority_mode="scoped",
        effect_issues=frozenset({"missing_effect_evidence"}),
    ),
]


CONTRADICTION_EVASION_CASES = [
    SemanticCanary(
        "contradiction_evasion",
        "readonly_and_destructive_hints_conflict",
        tool={
            "annotations": {"readOnlyHint": True, "destructiveHint": True},
            "auth": _auth_none(),
        },
        effect_status="conflicting",
        effect="destructive",
        authority_status="structural",
        authority_mode="none",
        effect_issues=frozenset({"conflicting_effect_evidence"}),
    ),
    SemanticCanary(
        "contradiction_evasion",
        "readonly_hint_cannot_hide_write_scope",
        tool={
            "annotations": {"readOnlyHint": True},
            "auth": _auth_scoped("orders:write"),
        },
        effect_status="conflicting",
        effect="write",
        authority_status="structural",
        authority_mode="scoped",
        effect_issues=frozenset({"conflicting_effect_evidence"}),
    ),
    SemanticCanary(
        "contradiction_evasion",
        "read_declaration_cannot_downgrade_post",
        tool={
            "source_type": "openapi",
            "annotations": {"httpMethod": "POST"},
            "auth": _auth_none(),
        },
        declaration={
            "tool": "process_order",
            "effect": "read",
            "authority": {"mode": "none"},
        },
        effect_status="conflicting",
        effect="write",
        authority_status="declared",
        authority_mode="none",
        effect_issues=frozenset({"conflicting_effect_evidence"}),
    ),
    SemanticCanary(
        "contradiction_evasion",
        "read_declaration_cannot_downgrade_delete",
        tool={
            "source_type": "openapi",
            "annotations": {"httpMethod": "DELETE"},
            "auth": _auth_none(),
        },
        declaration={
            "tool": "process_order",
            "effect": "read",
            "authority": {"mode": "none"},
        },
        effect_status="conflicting",
        effect="destructive",
        authority_status="declared",
        authority_mode="none",
        effect_issues=frozenset({"conflicting_effect_evidence"}),
    ),
    SemanticCanary(
        "contradiction_evasion",
        "write_declaration_cannot_downgrade_delete",
        tool={
            "source_type": "openapi",
            "annotations": {"httpMethod": "DELETE"},
            "auth": _auth_none(),
        },
        declaration={
            "tool": "process_order",
            "effect": "write",
            "authority": {"mode": "none"},
        },
        effect_status="conflicting",
        effect="destructive",
        authority_status="declared",
        authority_mode="none",
        effect_issues=frozenset({"conflicting_effect_evidence"}),
    ),
    # #424 retired this slot's previous occupant,
    # `manual_financial_tag_cannot_downgrade_to_read`. A declared
    # `risk_tags: [financial_action]` beside `effect: read` is the manifest
    # refining its own row, not contradicting it — and reading it as a
    # contradiction is what left the `declare_risk_tags` repair unable to close
    # the row that published it. The property that canary guarded is unchanged
    # and asserted, in its post-#424 form, by
    # `test_a_reviewed_tag_transitions_the_action_instead_of_blocking_it`.
    #
    # The slot now holds the boundary that fix draws. A declared *scope* is a
    # different kind of statement: #417 made a declared `billing.delete` grant
    # bound the action's effect, so it must keep contradicting a weaker
    # declaration even though the same human wrote both lines. The tag rides
    # along to prove it cannot launder the grant.
    SemanticCanary(
        "contradiction_evasion",
        "declared_delete_scope_cannot_downgrade_to_read",
        declaration={
            "tool": "process_order",
            "effect": "read",
            "risk_tags": ["financial_action"],
            "scopes": ["billing.delete"],
            "authority": {"mode": "scoped", "auth_type": "oauth2"},
        },
        effect_status="conflicting",
        effect="destructive",
        authority_status="declared",
        authority_mode="scoped",
        effect_issues=frozenset({"conflicting_effect_evidence"}),
    ),
    SemanticCanary(
        "contradiction_evasion",
        "string_false_destructive_hint_is_invalid",
        tool={"annotations": {"destructiveHint": "false"}, "auth": _auth_none()},
        effect_status="protocol_default",
        authority_status="structural",
        authority_mode="none",
        effect_issues=frozenset(
            {"invalid_semantic_annotation", "missing_effect_evidence"}
        ),
    ),
    SemanticCanary(
        "contradiction_evasion",
        "read_and_write_permission_classes_conflict",
        tool={
            "annotations": {"permission_classes": ["read", "write"]},
            "auth": _auth_none(),
        },
        effect_status="conflicting",
        effect="write",
        authority_status="structural",
        authority_mode="none",
        effect_issues=frozenset({"conflicting_effect_evidence"}),
    ),
    SemanticCanary(
        "contradiction_evasion",
        "read_and_destructive_permission_classes_conflict",
        tool={
            "annotations": {"permission_classes": ["read", "destructive"]},
            "auth": _auth_none(),
        },
        effect_status="conflicting",
        effect="destructive",
        authority_status="structural",
        authority_mode="none",
        effect_issues=frozenset({"conflicting_effect_evidence"}),
    ),
    SemanticCanary(
        "contradiction_evasion",
        "scoped_source_cannot_be_declared_none",
        tool={"auth": _auth_scoped("orders:write")},
        declaration={
            "tool": "process_order",
            "effect": "write",
            "authority": {"mode": "none"},
        },
        effect_status="declared",
        authority_status="conflicting",
        authority_mode="unknown",
        authority_issues=frozenset({"conflicting_authority_evidence"}),
    ),
    SemanticCanary(
        "contradiction_evasion",
        "anonymous_source_cannot_be_declared_scoped",
        tool={"auth": _auth_none()},
        declaration={
            "tool": "process_order",
            "effect": "write",
            "scopes": ["orders:write"],
            "authority": {"mode": "scoped", "auth_type": "oauth2"},
        },
        effect_status="declared",
        authority_status="conflicting",
        authority_mode="unknown",
        authority_issues=frozenset({"conflicting_authority_evidence"}),
    ),
    SemanticCanary(
        "contradiction_evasion",
        "ambiguous_openapi_alternatives_stay_partial",
        tool={
            "source_type": "openapi",
            "annotations": {"httpMethod": "GET"},
            "auth": AuthInfo(
                source="openapi",
                explicit=True,
                alternatives=[
                    AuthAlternative(anonymous=True),
                    AuthAlternative(
                        schemes=[
                            AuthSchemeRequirement(name="api_key", type="apiKey")
                        ]
                    ),
                ],
            ),
        },
        declaration={
            "tool": "process_order",
            "effect": "read",
            "authority": {"mode": "none"},
        },
        effect_status="declared",
        effect="read",
        authority_status="partial",
        authority_mode="unknown",
        authority_issues=frozenset({"partial_authority_evidence"}),
    ),
]


def _semantic_tool(**updates: Any) -> Tool:
    values: dict[str, Any] = {
        "id": "tool:process_order",
        "name": "process_order",
        "description": "Perform an explicitly bounded operation for a canary.",
        "source_type": "mcp",
        "source_id": "canary",
        "source_pointer": "/tools/0",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": False,
        },
        "extraction_confidence": "high",
        "extraction": {"method": "canary", "confidence": "high"},
    }
    values.update(updates)
    return Tool.model_validate(values)


@pytest.mark.parametrize(
    "case",
    EVIDENCE_COMPLETENESS_CASES,
    ids=lambda case: case.name,
)
def test_p0_evidence_completeness_canary(case: SemanticCanary) -> None:
    declaration = (
        ActionDeclarationConfig.model_validate(case.declaration)
        if case.declaration is not None
        else None
    )
    assessment = assess_tool_semantics(_semantic_tool(**case.tool), declaration)

    assert assessment.effect.status == case.effect_status
    assert assessment.conservative_effect == case.effect
    assert assessment.authority.status == case.authority_status
    assert assessment.authority.mode == case.authority_mode
    assert assessment.pass_eligible is case.pass_eligible
    assert {issue.kind for issue in assessment.effect.issues} == set(case.effect_issues)
    assert {issue.kind for issue in assessment.authority.issues} == set(
        case.authority_issues
    )


@pytest.mark.parametrize(
    "case",
    CONTRADICTION_EVASION_CASES,
    ids=lambda case: case.name,
)
def test_p0_contradiction_evasion_canary(case: SemanticCanary) -> None:
    declaration = (
        ActionDeclarationConfig.model_validate(case.declaration)
        if case.declaration is not None
        else None
    )
    assessment = assess_tool_semantics(_semantic_tool(**case.tool), declaration)

    assert assessment.effect.status == case.effect_status
    assert assessment.conservative_effect == case.effect
    assert assessment.authority.status == case.authority_status
    assert assessment.authority.mode == case.authority_mode
    assert assessment.pass_eligible is False
    assert {issue.kind for issue in assessment.effect.issues} == set(case.effect_issues)
    assert {issue.kind for issue in assessment.authority.issues} == set(
        case.authority_issues
    )


def _raw_tool(
    name: str,
    *,
    annotations: dict[str, Any] | None = None,
    auth: Any = None,
    include_auth: bool = False,
    description: str | None = None,
) -> dict[str, Any]:
    tool: dict[str, Any] = {
        "name": name,
        "owner": "platform-security",
        "description": description
        if description is not None
        else f"Perform the bounded {name} operation using declared inputs.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": False,
        },
    }
    if annotations is not None:
        tool["annotations"] = annotations
    if include_auth:
        tool["auth"] = auth
    return tool


def _safe_action(name: str) -> dict[str, Any]:
    return {"tool": name, "effect": "read", "authority": {"mode": "none"}}


def _write_scan_project(
    root: Path,
    *,
    tools: list[dict[str, Any]],
    actions: list[dict[str, Any]] | None = None,
    confirmations: list[str] | None = None,
    approvals: list[str] | None = None,
    idempotency: list[str] | None = None,
    checks: dict[str, Any] | None = None,
    human_ack: list[dict[str, Any]] | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "tools.json").write_text(
        json.dumps({"tools": tools}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    manifest: dict[str, Any] = {
        "version": "0.1",
        "project": {"name": "p0-safety-canary"},
        "agent": {
            "name": "p0-safety-canary-agent",
            "declared_purpose": ["exercise exact evidence-backed pass outcomes"],
        },
        "environment": {"target": "production_like"},
        "tool_sources": [{"id": "tools", "type": "mcp", "path": "tools.json"}],
        "agent_bindings": {
            "declarations": [
                {
                    "agent": "root",
                    "complete": True,
                    "tools": [
                        {"tool": str(tool["name"]), "source_id": "tools"}
                        for tool in tools
                    ],
                    "handoffs": [],
                    "reason": "reviewed exact P0 semantic canary surface",
                }
            ]
        },
        "ci": {"mode": "advisory"},
        "output": {"directory": "reports", "formats": ["json"]},
    }
    if actions is not None:
        manifest["action_surface"] = {"actions": actions}
    policy: dict[str, Any] = {}
    if confirmations:
        policy["require_confirmation_for_tools"] = confirmations
    if approvals:
        policy["require_approval_for_tools"] = approvals
    if idempotency:
        policy["require_idempotency_for_tools"] = idempotency
    if policy:
        manifest["policies"] = policy
    if checks:
        manifest["checks"] = checks
    if human_ack:
        manifest["human_ack"] = human_ack
    config = root / "shipgate.yaml"
    config.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return config


@dataclass(frozen=True)
class DilutionCanary:
    category: CanaryCategory
    name: str
    safe_count: int
    unsafe_tool: dict[str, Any]
    unsafe_action: dict[str, Any] | None
    gap_kinds: frozenset[str]
    decision: str = "insufficient_evidence"


DILUTION_CROSS_SOURCE_CASES = [
    DilutionCanary(
        "dilution_cross_source",
        "one_safe_cannot_dilute_neutral_mcp",
        1,
        _raw_tool("unresolved"),
        None,
        frozenset({"missing_effect_evidence", "missing_authority_evidence"}),
    ),
    DilutionCanary(
        "dilution_cross_source",
        "two_safe_cannot_dilute_neutral_mcp",
        2,
        _raw_tool("unresolved"),
        None,
        frozenset({"missing_effect_evidence", "missing_authority_evidence"}),
    ),
    DilutionCanary(
        "dilution_cross_source",
        "ten_safe_cannot_dilute_neutral_mcp",
        10,
        _raw_tool("unresolved"),
        None,
        frozenset({"missing_effect_evidence", "missing_authority_evidence"}),
    ),
    DilutionCanary(
        "dilution_cross_source",
        "hundred_safe_cannot_dilute_neutral_mcp",
        100,
        _raw_tool("unresolved"),
        None,
        frozenset({"missing_effect_evidence", "missing_authority_evidence"}),
    ),
    DilutionCanary(
        "dilution_cross_source",
        "declared_effect_cannot_dilute_missing_authority",
        4,
        _raw_tool("unresolved"),
        {"tool": "unresolved", "effect": "read"},
        frozenset({"missing_authority_evidence"}),
    ),
    DilutionCanary(
        "dilution_cross_source",
        "known_authority_cannot_dilute_missing_effect",
        4,
        _raw_tool("unresolved", auth={"mode": "none"}, include_auth=True),
        None,
        frozenset({"missing_effect_evidence"}),
    ),
    DilutionCanary(
        "dilution_cross_source",
        "safe_tools_cannot_dilute_effect_conflict",
        4,
        _raw_tool(
            "unresolved",
            annotations={"readOnlyHint": True, "destructiveHint": True},
            auth={"mode": "none"},
            include_auth=True,
        ),
        None,
        frozenset({"conflicting_effect_evidence"}),
        "blocked",
    ),
    DilutionCanary(
        "dilution_cross_source",
        "safe_tools_cannot_dilute_invalid_authority",
        4,
        _raw_tool(
            "unresolved",
            annotations={"readOnlyHint": True},
            auth={"mode": "scoped", "type": "oauth2", "scopes": []},
            include_auth=True,
        ),
        None,
        frozenset({"invalid_semantic_annotation", "partial_authority_evidence"}),
    ),
]


@pytest.mark.parametrize(
    "case",
    DILUTION_CROSS_SOURCE_CASES,
    ids=lambda case: case.name,
)
def test_p0_dilution_cross_source_canary(
    tmp_path: Path,
    case: DilutionCanary,
) -> None:
    safe_tools = [_raw_tool(f"safe_{index}") for index in range(case.safe_count)]
    actions = [_safe_action(str(tool["name"])) for tool in safe_tools]
    if case.unsafe_action is not None:
        actions.append(case.unsafe_action)
    config = _write_scan_project(
        tmp_path,
        tools=[*safe_tools, case.unsafe_tool],
        actions=actions,
    )

    report, exit_code = run_scan(
        config_path=config,
        output_dir=tmp_path / "out",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    assert report.release_decision is not None
    assert report.release_decision.decision == case.decision
    assert exit_code == 0
    coverage = report.release_decision.evidence_coverage.semantic_coverage
    assert coverage.total_actions == case.safe_count + 1
    assert coverage.pass_eligible_actions == case.safe_count
    assert {gap.kind for gap in report.release_decision.evidence_coverage.evidence_gaps} == set(
        case.gap_kinds
    )


@dataclass(frozen=True)
class ControlCanary:
    category: CanaryCategory
    name: str
    effect: str
    action_controls: dict[str, Any]
    confirmations: bool
    policy_approval: bool
    policy_idempotency: bool
    decision: str
    required_check: str | None


EFFECT_CONTROL_TRANSITION_CASES = [
    ControlCanary(
        "effect_control_transition",
        "read_needs_no_high_impact_control",
        "read",
        {},
        False,
        False,
        False,
        "passed",
        None,
    ),
    ControlCanary(
        "effect_control_transition",
        "ordinary_write_needs_no_specialized_control",
        "write",
        {},
        False,
        False,
        False,
        "passed",
        None,
    ),
    ControlCanary(
        "effect_control_transition",
        "financial_write_without_controls_blocks",
        "financial_write",
        {},
        False,
        False,
        False,
        "blocked",
        "SHIP-ACTION-FINANCIAL-WRITE-CONTROL-MISSING",
    ),
    ControlCanary(
        "effect_control_transition",
        "financial_write_with_union_controls_passes",
        "financial_write",
        {
            "approval": {"required": True},
            "safeguards": {"audit_log": True, "idempotency": True},
        },
        False,
        True,
        True,
        "passed",
        None,
    ),
    ControlCanary(
        "effect_control_transition",
        "destructive_without_controls_blocks",
        "destructive",
        {},
        False,
        False,
        False,
        "blocked",
        "SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING",
    ),
    ControlCanary(
        "effect_control_transition",
        "destructive_with_approval_confirmation_rollback_passes",
        "destructive",
        {"approval": {"required": True}, "safeguards": {"rollback": True}},
        True,
        True,
        False,
        "passed",
        None,
    ),
    ControlCanary(
        "effect_control_transition",
        "external_communication_without_controls_blocks",
        "external_communication",
        {},
        False,
        False,
        False,
        "blocked",
        "SHIP-ACTION-EXTERNAL-COMMUNICATION-AUDIT-MISSING",
    ),
    ControlCanary(
        "effect_control_transition",
        "external_communication_with_confirmation_audit_passes",
        "external_communication",
        {"safeguards": {"audit_log": True}},
        True,
        False,
        False,
        "passed",
        None,
    ),
    ControlCanary(
        "effect_control_transition",
        "production_operation_without_approval_blocks",
        "production_operation",
        {},
        False,
        False,
        False,
        "blocked",
        "SHIP-ACTION-POLICY-VIOLATION",
    ),
    ControlCanary(
        "effect_control_transition",
        "production_operation_with_approval_passes",
        "production_operation",
        {"approval": {"required": True}},
        False,
        True,
        False,
        "passed",
        None,
    ),
    ControlCanary(
        "effect_control_transition",
        "code_execution_without_approval_blocks",
        "code_execution",
        {},
        False,
        False,
        False,
        "blocked",
        "SHIP-ACTION-POLICY-VIOLATION",
    ),
    ControlCanary(
        "effect_control_transition",
        "code_execution_with_approval_passes",
        "code_execution",
        {"approval": {"required": True}},
        False,
        True,
        False,
        "passed",
        None,
    ),
]


@pytest.mark.parametrize(
    "case",
    EFFECT_CONTROL_TRANSITION_CASES,
    ids=lambda case: case.name,
)
def test_p0_effect_control_transition_canary(
    tmp_path: Path,
    case: ControlCanary,
) -> None:
    name = "controlled_action"
    action = {
        "tool": name,
        "effect": case.effect,
        "authority": {"mode": "none"},
        **case.action_controls,
    }
    config = _write_scan_project(
        tmp_path,
        tools=[_raw_tool(name)],
        actions=[action],
        confirmations=[name] if case.confirmations else None,
        approvals=[name] if case.policy_approval else None,
        idempotency=[name] if case.policy_idempotency else None,
    )

    report, exit_code = run_scan(
        config_path=config,
        output_dir=tmp_path / "out",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=False,
    )

    assert report.release_decision is not None
    assert report.release_decision.decision == case.decision
    assert exit_code == 0
    action_fact = report.action_surface_facts.actions[0]
    assert action_fact.effect == case.effect
    assert action_fact.semantic_assessment is not None
    assert action_fact.semantic_assessment.pass_eligible is True
    active_check_ids = {
        finding.check_id for finding in report.findings if not finding.suppressed
    }
    if case.required_check is None:
        assert not any(check_id.startswith("SHIP-ACTION-") for check_id in active_check_ids)
    else:
        assert case.required_check in active_check_ids


@dataclass(frozen=True)
class TrustRootCanary:
    category: CanaryCategory
    name: str
    scenario: str
    decision: str


TRUST_ROOT_BYPASS_CASES = [
    TrustRootCanary(
        "trust_root_bypass",
        "suppression_cannot_hide_semantic_gap",
        "suppress_gap",
        "insufficient_evidence",
    ),
    TrustRootCanary(
        "trust_root_bypass",
        "severity_override_cannot_hide_semantic_gap",
        "override_gap",
        "insufficient_evidence",
    ),
    TrustRootCanary(
        "trust_root_bypass",
        "human_ack_cannot_close_semantic_gap",
        "human_ack_gap",
        "insufficient_evidence",
    ),
    TrustRootCanary(
        "trust_root_bypass",
        "no_heuristics_cannot_promote_inferred_effect",
        "no_heuristics_gap",
        "insufficient_evidence",
    ),
    TrustRootCanary(
        "trust_root_bypass",
        "suppression_cannot_waive_mandatory_control",
        "suppress_control",
        "blocked",
    ),
    TrustRootCanary(
        "trust_root_bypass",
        "severity_downgrade_cannot_waive_mandatory_control",
        "override_control",
        "blocked",
    ),
    TrustRootCanary(
        "trust_root_bypass",
        "baseline_cannot_turn_control_debt_into_pass",
        "baseline_control",
        "review_required",
    ),
    TrustRootCanary(
        "trust_root_bypass",
        "suppressed_auth_finding_cannot_hide_unscoped_authority",
        "suppress_unscoped",
        "review_required",
    ),
]


@pytest.mark.parametrize(
    "case",
    TRUST_ROOT_BYPASS_CASES,
    ids=lambda case: case.name,
)
def test_p0_trust_root_bypass_canary(
    tmp_path: Path,
    case: TrustRootCanary,
) -> None:
    name = "governed_action"
    tools = [_raw_tool(name)]
    actions: list[dict[str, Any]] | None = None
    confirmations: list[str] | None = None
    approvals: list[str] | None = None
    checks: dict[str, Any] | None = None
    human_ack: list[dict[str, Any]] | None = None
    no_heuristics = False

    if case.scenario == "suppress_gap":
        tools = [_raw_tool(name, description="")]
        checks = {
            "ignore": [
                {
                    "check_id": "SHIP-DOC-MISSING-DESCRIPTION",
                    "tool": name,
                    "reason": "canary attempts to suppress all visible noise",
                }
            ]
        }
    elif case.scenario == "override_gap":
        tools = [_raw_tool(name, description="")]
        checks = {"severity_overrides": {"SHIP-DOC-MISSING-DESCRIPTION": "low"}}
    elif case.scenario == "human_ack_gap":
        human_ack = [
            {
                "affected_surface": "policy",
                "owner": "canary-reviewer",
                "reason": "acknowledgement is not semantic evidence",
                "expires": "2027-12-31",
            }
        ]
    elif case.scenario == "no_heuristics_gap":
        tools = [
            _raw_tool(
                "erase_everything_preview",
                auth={"mode": "none"},
                include_auth=True,
            )
        ]
        no_heuristics = True
    elif case.scenario in {"suppress_control", "override_control", "baseline_control"}:
        actions = [
            {
                "tool": name,
                "effect": "destructive",
                "authority": {"mode": "none"},
            }
        ]
        if case.scenario == "suppress_control":
            checks = {
                "ignore": [
                    {
                        "check_id": "SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING",
                        "tool": name,
                        "reason": "canary attempts to waive mandatory control",
                    }
                ]
            }
        elif case.scenario == "override_control":
            checks = {
                "severity_overrides": {
                    "SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING": "high"
                },
                "acknowledge_overrides": [
                    {
                        "check_id": "SHIP-ACTION-DESTRUCTIVE-ROLLBACK-MISSING",
                        "owner": "canary-reviewer",
                        "reason": "attempted severity downgrade must not waive control",
                        "expires": "2027-12-31",
                    }
                ],
            }
    elif case.scenario == "suppress_unscoped":
        actions = [
            {
                "tool": name,
                "effect": "read",
                "authority": {
                    "mode": "unscoped",
                    "auth_type": "apiKey",
                    "reason": "provider offers no enumerable scopes",
                },
            }
        ]
        checks = {
            "ignore": [
                {
                    "check_id": "SHIP-AUTH-MISSING-SCOPE",
                    "tool": name,
                    "reason": "canary attempts to hide known broad authority",
                }
            ]
        }

    config = _write_scan_project(
        tmp_path,
        tools=tools,
        actions=actions,
        confirmations=confirmations,
        approvals=approvals,
        checks=checks,
        human_ack=human_ack,
    )
    baseline_path: Path | None = None
    if case.scenario == "baseline_control":
        first, _ = run_scan(
            config_path=config,
            output_dir=tmp_path / "first",
            formats=["json"],
            ci_mode="advisory",
            packet_enabled=False,
        )
        baseline_path = tmp_path / "baseline.json"
        write_baseline(first, baseline_path)

    report, exit_code = run_scan(
        config_path=config,
        output_dir=tmp_path / "out",
        formats=["json"],
        ci_mode="advisory",
        baseline_path=baseline_path,
        no_heuristics=no_heuristics,
        packet_enabled=False,
    )

    assert report.release_decision is not None
    assert report.release_decision.decision == case.decision
    assert exit_code == 0
    assert report.release_decision.decision != "passed"


@dataclass(frozen=True)
class RobustnessCanary:
    category: CanaryCategory
    name: str
    scenario: str


ROBUSTNESS_METAMORPHIC_CASES = [
    RobustnessCanary(
        "robustness_metamorphic",
        "removing_effect_evidence_never_promotes",
        "remove_effect",
    ),
    RobustnessCanary(
        "robustness_metamorphic",
        "removing_authority_evidence_never_promotes",
        "remove_authority",
    ),
    RobustnessCanary(
        "robustness_metamorphic",
        "adding_destructive_claim_never_lowers_effect",
        "add_destructive",
    ),
    RobustnessCanary(
        "robustness_metamorphic",
        "broadening_authority_never_promotes",
        "broaden_authority",
    ),
    RobustnessCanary(
        "robustness_metamorphic",
        "risk_hint_reordering_is_deterministic",
        "reorder_hints",
    ),
    RobustnessCanary(
        "robustness_metamorphic",
        "scope_reordering_is_deterministic",
        "reorder_scopes",
    ),
    RobustnessCanary(
        "robustness_metamorphic",
        "repeated_assessment_is_byte_identical",
        "repeat",
    ),
    RobustnessCanary(
        "robustness_metamorphic",
        "non_english_euphemism_gets_no_safety_credit",
        "non_english",
    ),
]


_EFFECT_ORDER = {
    "read": 0,
    "write": 1,
    "privileged_data_access": 2,
    "identity_access": 3,
    "code_execution": 4,
    "production_operation": 5,
    "external_communication": 6,
    "financial_write": 7,
    "destructive": 8,
}


@pytest.mark.parametrize(
    "case",
    ROBUSTNESS_METAMORPHIC_CASES,
    ids=lambda case: case.name,
)
def test_p0_robustness_metamorphic_canary(case: RobustnessCanary) -> None:
    if case.scenario == "remove_effect":
        before = assess_tool_semantics(
            _semantic_tool(annotations={"readOnlyHint": True}, auth=_auth_none())
        )
        after = assess_tool_semantics(_semantic_tool(auth=_auth_none()))
        assert before.pass_eligible is True
        assert after.pass_eligible is False
        assert after.effect.status == "protocol_default"
    elif case.scenario == "remove_authority":
        before = assess_tool_semantics(
            _semantic_tool(annotations={"readOnlyHint": True}, auth=_auth_none())
        )
        after = assess_tool_semantics(
            _semantic_tool(annotations={"readOnlyHint": True})
        )
        assert before.pass_eligible is True
        assert after.pass_eligible is False
        assert after.authority.status == "unknown"
    elif case.scenario == "add_destructive":
        before = assess_tool_semantics(
            _semantic_tool(annotations={"readOnlyHint": True}, auth=_auth_none())
        )
        after = assess_tool_semantics(
            _semantic_tool(
                annotations={"readOnlyHint": True, "destructiveHint": True},
                auth=_auth_none(),
            )
        )
        assert _EFFECT_ORDER[after.conservative_effect] >= _EFFECT_ORDER[
            before.conservative_effect
        ]
        assert after.effect.status == "conflicting"
        assert after.pass_eligible is False
    elif case.scenario == "broaden_authority":
        before = assess_tool_semantics(
            _semantic_tool(annotations={"readOnlyHint": True}, auth=_auth_none())
        )
        after = assess_tool_semantics(
            _semantic_tool(
                annotations={"readOnlyHint": True},
                auth=AuthInfo(
                    type="apiKey",
                    source="test",
                    mode="unscoped",
                    explicit=True,
                ),
            )
        )
        assert before.pass_eligible is True
        assert after.authority.mode == "unscoped"
        assert after.pass_eligible is False
    elif case.scenario == "reorder_hints":
        hints = [
            ToolRiskHint(tag="write", source="sdk_keyword", confidence="medium"),
            ToolRiskHint(
                tag="financial_action", source="manual", confidence="high"
            ),
        ]
        left = assess_tool_semantics(
            _semantic_tool(
                source_type="openapi",
                annotations={"httpMethod": "POST"},
                auth=_auth_none(),
                risk_hints=hints,
            )
        )
        right = assess_tool_semantics(
            _semantic_tool(
                source_type="openapi",
                annotations={"httpMethod": "POST"},
                auth=_auth_none(),
                risk_hints=list(reversed(hints)),
            )
        )
        assert left.model_dump_json() == right.model_dump_json()
        assert left.conservative_effect == "financial_write"
    elif case.scenario == "reorder_scopes":
        left = assess_tool_semantics(
            _semantic_tool(
                annotations={"permission_class": "write"},
                auth=_auth_scoped("orders:read", "orders:write"),
            )
        )
        right = assess_tool_semantics(
            _semantic_tool(
                annotations={"permission_class": "write"},
                auth=_auth_scoped("orders:write", "orders:read"),
            )
        )
        assert left.model_dump_json() == right.model_dump_json()
        assert left.authority.scopes == ["orders:read", "orders:write"]
    elif case.scenario == "repeat":
        tool = _semantic_tool(
            source_type="openapi",
            annotations={"httpMethod": "PUT"},
            auth=_auth_scoped("orders:write"),
        )
        first = assess_tool_semantics(tool).model_dump_json()
        second = assess_tool_semantics(tool).model_dump_json()
        assert first == second
        assert json.loads(first)["pass_eligible"] is True
    else:
        assessment = assess_tool_semantics(
            _semantic_tool(
                name="datos_seguro_solo_consulta_预览",
                description="Consulta segura en modo de vista previa.",
            )
        )
        assert assessment.effect.status == "protocol_default"
        assert assessment.conservative_effect == "write"
        assert assessment.pass_eligible is False


def test_a_reviewed_tag_transitions_the_action_instead_of_blocking_it() -> None:
    """The retired canary's property, in the form #424 left it.

    `effect: read` + `risk_tags: [financial_action]` used to be a blocking
    `conflicting_effect_evidence`. It is now pass-eligible — and the reason
    that is not an evasion is asserted here rather than assumed: the action
    resolves to `financial_write`, which is the effect every downstream
    consumer reads, and the financial-write claim is policy-eligible, which is
    what makes `_control_effects` apply that category's controls.
    """

    assessment = assess_tool_semantics(
        _semantic_tool(),
        ActionDeclarationConfig.model_validate(
            {
                "tool": "process_order",
                "effect": "read",
                "risk_tags": ["financial_action"],
                "authority": {"mode": "none"},
            }
        ),
    )

    assert assessment.conservative_effect == "financial_write"
    assert "financial_write" in {
        claim.value for claim in assessment.effect.claims if claim.policy_eligible
    }
    assert assessment.pass_eligible is True
    assert not assessment.effect.issues


def test_p0_safety_canary_catalog_has_exact_planned_shape() -> None:
    all_cases = [
        *EVIDENCE_COMPLETENESS_CASES,
        *CONTRADICTION_EVASION_CASES,
        *DILUTION_CROSS_SOURCE_CASES,
        *EFFECT_CONTROL_TRANSITION_CASES,
        *TRUST_ROOT_BYPASS_CASES,
        *ROBUSTNESS_METAMORPHIC_CASES,
    ]
    expected = {
        "evidence_completeness": 16,
        "contradiction_evasion": 12,
        "dilution_cross_source": 8,
        "effect_control_transition": 12,
        "trust_root_bypass": 8,
        "robustness_metamorphic": 8,
    }

    assert len(all_cases) == 64
    assert Counter(case.category for case in all_cases) == Counter(expected)
    names = [case.name for case in all_cases]
    assert len(names) == len(set(names))
