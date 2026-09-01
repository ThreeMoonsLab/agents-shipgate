"""Acyclic action-effect semantics shared by schemas and evaluators."""

from __future__ import annotations

from collections.abc import Iterable
from typing import cast

from agents_shipgate.schemas.surfaces import ActionEffect

EFFECT_RISK_RANK: dict[ActionEffect, int] = {
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

ACTION_EFFECT_RANK: dict[ActionEffect, int] = {
    "read": 0,
    "privileged_data_access": 1,
    "write": 2,
    "external_communication": 3,
    "financial_write": 4,
    "production_operation": 4,
    "identity_access": 4,
    "code_execution": 4,
    "destructive": 5,
}

BUILTIN_EFFECT_OBLIGATIONS: dict[ActionEffect, frozenset[str]] = {
    "financial_write": frozenset(
        {"approval.required", "safeguards.audit_log", "safeguards.idempotency"}
    ),
    "external_communication": frozenset(
        {"safeguards.audit_log", "confirmation.required"}
    ),
    "destructive": frozenset(
        {"approval.required", "safeguards.rollback", "confirmation.required"}
    ),
    "production_operation": frozenset({"approval.required"}),
    "code_execution": frozenset({"approval.required"}),
}

RISK_TAG_EFFECTS: dict[str, ActionEffect] = {
    "read_only": "read",
    "write": "write",
    "writes_data": "write",
    "filesystem_write": "write",
    "destructive": "destructive",
    "irreversible": "destructive",
    "external_write": "external_communication",
    "external_communication": "external_communication",
    "customer_communication": "external_communication",
    "external_side_effect": "external_communication",
    "financial_action": "financial_write",
    "financial_write": "financial_write",
    "infrastructure_change": "production_operation",
    "production_operation": "production_operation",
    "production_ops": "production_operation",
    "sensitive_data_access": "privileged_data_access",
    "privileged_data_access": "privileged_data_access",
    "privileged_data": "privileged_data_access",
    "secret_access": "privileged_data_access",
    "code_execution": "code_execution",
    "identity_access": "identity_access",
    "unknown_side_effect": "write",
}


def declaration_covers(declared: str, inferred: str) -> bool:
    """Whether a declaration covers an observation under risk and controls."""

    if declared == inferred:
        return True
    if declared not in EFFECT_RISK_RANK or inferred not in EFFECT_RISK_RANK:
        return False
    declared_effect = cast(ActionEffect, declared)
    inferred_effect = cast(ActionEffect, inferred)
    if EFFECT_RISK_RANK[declared_effect] < EFFECT_RISK_RANK[inferred_effect]:
        return False
    if ACTION_EFFECT_RANK[declared_effect] < ACTION_EFFECT_RANK[inferred_effect]:
        return False
    return BUILTIN_EFFECT_OBLIGATIONS.get(
        inferred_effect, frozenset()
    ).issubset(BUILTIN_EFFECT_OBLIGATIONS.get(declared_effect, frozenset()))


def declaration_effects(
    declared_effect: str | None,
    risk_tags: Iterable[str],
) -> tuple[ActionEffect, ...]:
    """Return the exact effect-bearing proposal carried by one declaration."""

    effects: set[ActionEffect] = set()
    if declared_effect in EFFECT_RISK_RANK:
        effects.add(cast(ActionEffect, declared_effect))
    for raw_tag in risk_tags:
        effect = RISK_TAG_EFFECTS.get(str(raw_tag))
        if effect is not None and effect != "read":
            effects.add(effect)
    return tuple(sorted(effects, key=lambda effect: (EFFECT_RISK_RANK[effect], effect)))


__all__ = [
    "ACTION_EFFECT_RANK",
    "BUILTIN_EFFECT_OBLIGATIONS",
    "EFFECT_RISK_RANK",
    "RISK_TAG_EFFECTS",
    "declaration_covers",
    "declaration_effects",
]
