from __future__ import annotations

from agents_shipgate.schemas.surfaces import ActionEffect

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

#: The built-in controls each effect obliges, as the dot paths
#: ``_current_action_policy_findings`` requires plus the ``confirmation``
#: policy it looks up separately. Effects absent from this table oblige no
#: built-in control.
#:
#: Effects are **not** totally ordered by obligation. ``financial_write``
#: outranks ``external_communication`` on risk while requiring neither
#: confirmation nor — for the communicating half — anything about who is
#: written to, so a declaration cannot discharge one category by naming a
#: higher-risk different one. The control evaluator has always read effects as
#: a *set* for this reason (``_control_effects``); this table is the same fact
#: in a form the declaration comparator can read.
#:
#: ``test_the_builtin_obligation_table_matches_the_controls_that_fire`` walks
#: every entry through a real scan, so the table cannot drift from the branches
#: it mirrors.
BUILTIN_EFFECT_OBLIGATIONS: dict[ActionEffect, frozenset[str]] = {
    "financial_write": frozenset(
        {"approval.required", "safeguards.audit_log", "safeguards.idempotency"}
    ),
    "external_communication": frozenset({"safeguards.audit_log", "confirmation.required"}),
    "destructive": frozenset(
        {"approval.required", "safeguards.rollback", "confirmation.required"}
    ),
    "production_operation": frozenset({"approval.required"}),
    "code_execution": frozenset({"approval.required"}),
}


def builtin_obligations(effect: ActionEffect) -> frozenset[str]:
    """The built-in controls ``effect`` obliges — empty for effects with none."""

    return BUILTIN_EFFECT_OBLIGATIONS.get(effect, frozenset())


__all__ = [
    "ACTION_EFFECT_RANK",
    "BUILTIN_EFFECT_OBLIGATIONS",
    "builtin_obligations",
]
