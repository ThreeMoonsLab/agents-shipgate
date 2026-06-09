from __future__ import annotations

from collections.abc import Iterable

# Reward-hacking guard. Findings in these categories define the release
# gate's trust spine (trust-root protection). A manifest ``checks.ignore``
# suppression must NOT be able to hide them — otherwise a coding agent
# could edit ``shipgate.yaml`` to add ``ignore: SHIP-VERIFY-TRUST-ROOT-
# TOUCHED`` and silence the very check that flags that edit. Enforced in
# ``apply_suppressions``; mirrors how baseline-integrity findings are
# immune (they are appended after suppression runs). Severity weakening
# of these checks is blocked separately by ``CheckMetadata.floor_severity``.
# See docs/engineering/ai-coding-workflow-verifier.md §3 (Principle 3) and §5.
UNSUPPRESSIBLE_FINDING_CATEGORIES: frozenset[str] = frozenset(
    {"verify", "codex_boundary"}
)

LEGACY_CHECK_ID_ALIASES: dict[str, tuple[str, ...]] = {
    "SHIP-API-OPERATIONAL-READINESS": (
        "SHIP-API-RETRY-POLICY-MISSING",
        "SHIP-API-TIMEOUT-MISSING",
        "SHIP-API-TEST-CASES-MISSING",
        "SHIP-API-TOOL-OUTPUT-SCHEMA-MISSING",
        "SHIP-API-RETRY-WITHOUT-IDEMPOTENCY",
        "SHIP-API-TRACE-APPROVAL-MISSING",
        "SHIP-API-TRACE-CONFIRMATION-MISSING",
    ),
}


def expands_to_check_id(configured_check_id: str, emitted_check_id: str) -> bool:
    """Return whether a configured check id should match an emitted finding."""
    return (
        configured_check_id == emitted_check_id
        or emitted_check_id in LEGACY_CHECK_ID_ALIASES.get(configured_check_id, ())
    )


def known_check_ids_with_legacy(check_ids: Iterable[str]) -> set[str]:
    return {*check_ids, *LEGACY_CHECK_ID_ALIASES}
