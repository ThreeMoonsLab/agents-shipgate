from __future__ import annotations

BROAD_FREE_TEXT_PARAMETER_NAMES = {
    "action",
    "body",
    "command",
    "content",
    "instructions",
    "message",
    "prompt",
    "update",
    "updates",
}

RISKY_NUMERIC_PARAMETER_NAMES = {
    "amount",
    "amt",
    "cap",
    "count",
    "limit",
    "max",
    "maximum",
    "qty",
    "quantity",
    "refund_amount",
    "size",
    "total",
}


def is_broad_scope(scope: str) -> bool:
    normalized = scope.strip().lower()
    return (
        normalized in {"*", "admin"}
        or normalized.endswith(":*")
        or normalized.endswith("/*")
        or "admin" in normalized
        or "write-all" in normalized
        or "write_all" in normalized
    )
