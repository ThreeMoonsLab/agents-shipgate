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

__all__ = ["ACTION_EFFECT_RANK"]
