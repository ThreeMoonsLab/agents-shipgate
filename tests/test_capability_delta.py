from __future__ import annotations

from agents_shipgate.core.capabilities import capability_fact_from_action_fact
from agents_shipgate.core.capability_delta import (
    CapabilityFactContext,
    classify_capability_delta,
    diff_capability_fact_sets,
)
from agents_shipgate.schemas.surfaces import (
    ActionApprovalFact,
    ActionEvidenceFact,
    ActionFact,
    ActionSafeguardsFact,
    ActionSurfaceHashes,
)


def _action(
    *,
    action_id: str = "agent:openapi:support:cases.search",
    tool_name: str = "cases.search",
    operation: str = "cases.search",
    effect: str = "read",
    risk_tags: list[str] | None = None,
    scopes: list[str] | None = None,
    approval: bool | None = None,
    safeguards: ActionSafeguardsFact | None = None,
    input_fields: list[str] | None = None,
    required_input_fields: list[str] | None = None,
    schema_hash: str = "schema-a",
) -> ActionFact:
    policy_hash = f"policy:{approval}:{safeguards}"
    risk_hash = f"risk:{effect}:{risk_tags}:{scopes}"
    return ActionFact(
        action_id=action_id,
        agent_id="agent:one",
        tool_id=f"tool:{tool_name}",
        tool_name=tool_name,
        provider="support",
        source_type="openapi",
        source_id="support_api",
        operation=operation,
        effect=effect,  # type: ignore[arg-type]
        risk_tags=sorted(risk_tags or []),
        required_scopes=sorted(scopes or []),
        approval_policy=ActionApprovalFact(required=approval),
        safeguards=safeguards or ActionSafeguardsFact(),
        evidence=ActionEvidenceFact(),
        input_fields=sorted(input_fields or []),
        required_input_fields=sorted(required_input_fields or []),
        input_schema_hash=schema_hash,
        hashes=ActionSurfaceHashes(
            identity_hash=action_id,
            schema_hash=schema_hash,
            policy_hash=policy_hash,
            risk_hash=risk_hash,
        ),
    )


def _ctx(action: ActionFact) -> CapabilityFactContext:
    return CapabilityFactContext(
        fact=capability_fact_from_action_fact(action),
        action_id=action.action_id,
        input_fields=tuple(action.input_fields),
        required_input_fields=tuple(action.required_input_fields),
    )


def _changed(base: ActionFact, head: ActionFact):
    diff = diff_capability_fact_sets([_ctx(base)], [_ctx(head)])
    return diff.changed or diff.reidentified


def test_semantic_delta_ordering_is_deterministic() -> None:
    base = [
        _ctx(_action(tool_name="z.search", operation="z.search", schema_hash="z1")),
        _ctx(_action(tool_name="a.search", operation="a.search", schema_hash="a1")),
    ]
    head = [
        _ctx(
            _action(
                tool_name="z.search",
                operation="z.search",
                schema_hash="z2",
                input_fields=["q"],
            )
        ),
        _ctx(
            _action(
                tool_name="a.search",
                operation="a.search",
                schema_hash="a2",
                input_fields=["q"],
            )
        ),
    ]

    diff = diff_capability_fact_sets(base, list(reversed(head)))

    assert [row.after.identity.tool_name for row in diff.changed] == [
        "a.search",
        "z.search",
    ]


def test_scope_broaden_narrow_and_mixed_are_classified() -> None:
    broadened = _changed(
        _action(scopes=["cases:read"]),
        _action(scopes=["cases:read", "cases:write"]),
    )[0]
    narrowed = _changed(
        _action(scopes=["cases:read", "cases:write"]),
        _action(scopes=["cases:read"]),
    )[0]
    mixed = _changed(
        _action(scopes=["cases:read"]),
        _action(scopes=["cases:write"]),
    )[0]

    assert broadened.semantic_direction == "broadened"
    assert narrowed.semantic_direction == "narrowed"
    assert mixed.semantic_direction == "mixed"
    assert any(change.field == "identity.scope" for change in broadened.semantic_changes)


def test_effect_escalation_and_reduction_are_classified() -> None:
    escalated = _changed(_action(effect="read"), _action(effect="write"))[0]
    reduced = _changed(_action(effect="write"), _action(effect="read"))[0]

    assert escalated.semantic_direction == "broadened"
    assert reduced.semantic_direction == "narrowed"
    assert any(change.kind == "effect_changed" for change in escalated.semantic_changes)


def test_control_removed_and_added_are_classified() -> None:
    removed = _changed(_action(approval=True), _action(approval=False))[0]
    added = _changed(_action(approval=False), _action(approval=True))[0]

    assert removed.semantic_direction == "broadened"
    assert added.semantic_direction == "narrowed"
    assert any(
        change.field == "controls.approval_required"
        for change in removed.semantic_changes
    )


def test_action_fact_idempotency_false_is_not_positive_evidence() -> None:
    fact = capability_fact_from_action_fact(
        _action(safeguards=ActionSafeguardsFact(idempotency=False))
    )

    assert fact.controls.safeguard_idempotency is False
    assert fact.effect.idempotency_known is None


def test_schema_broadening_and_narrowing_are_classified_from_context() -> None:
    broadened = _changed(
        _action(input_fields=["query"], schema_hash="a"),
        _action(input_fields=["query", "limit"], schema_hash="b"),
    )[0]
    narrowed = _changed(
        _action(required_input_fields=["query"], schema_hash="a"),
        _action(required_input_fields=["query", "account_id"], schema_hash="b"),
    )[0]

    assert broadened.semantic_direction == "broadened"
    assert narrowed.semantic_direction == "narrowed"
    assert any(
        change.kind == "schema_input_added"
        for change in broadened.semantic_changes
    )
    assert any(
        change.kind == "schema_required_input_added"
        for change in narrowed.semantic_changes
    )


def test_risk_added_and_removed_are_classified() -> None:
    added = _changed(_action(), _action(risk_tags=["network_access"]))[0]
    removed = _changed(_action(risk_tags=["network_access"]), _action())[0]

    assert added.semantic_direction == "broadened"
    assert removed.semantic_direction == "narrowed"
    assert any(change.field == "risk_tags" for change in added.semantic_changes)


def test_authority_metadata_drift_is_unknown() -> None:
    before = capability_fact_from_action_fact(_action())
    after = before.model_copy(
        update={
            "authority": before.authority.model_copy(update={"auth_type": "api_key"}),
            "hashes": before.hashes.model_copy(update={"authority_hash": "changed"}),
        }
    )

    direction, changes = classify_capability_delta(
        before,
        after,
        changed_hashes=("authority_hash",),
    )

    assert direction == "unknown"
    assert any(change.field == "authority.auth_type" for change in changes)


def test_evidence_only_change_is_separate_from_semantic_changed() -> None:
    before = capability_fact_from_action_fact(_action())
    after = before.model_copy(
        update={
            "evidence": before.evidence.model_copy(
                update={"source_path": "tools/changed.yaml"}
            ),
            "hashes": before.hashes.model_copy(update={"evidence_hash": "changed"}),
        }
    )

    diff = diff_capability_fact_sets([before], [after])

    assert diff.changed == []
    assert len(diff.evidence_changed) == 1
    assert diff.evidence_changed[0].semantic_direction == "evidence_only"
