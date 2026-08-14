from __future__ import annotations

from collections.abc import Iterable

# Reward-hacking guard. Findings in these categories define the release
# gate's trust spine (trust-root protection). A manifest ``checks.ignore``
# suppression must NOT be able to hide them — otherwise a coding agent
# could edit ``shipgate.yaml`` to add ``ignore: SHIP-VERIFY-TRUST-ROOT-
# TOUCHED`` and silence the very check that flags that edit. The same
# holds for the codex and host boundary categories: a PR that expands a
# Claude Code allowlist or a workflow permission must not be able to
# suppress the boundary check flagging it. Enforced in
# ``apply_suppressions``; mirrors how baseline-integrity findings are
# immune (they are appended after suppression runs). Severity weakening
# of these checks is blocked separately by ``CheckMetadata.floor_severity``.
# See docs/engineering/ai-coding-workflow-verifier.md §3 (Principle 3) and §5.
UNSUPPRESSIBLE_FINDING_CATEGORIES: frozenset[str] = frozenset(
    {"verify", "codex_boundary", "host_boundary"}
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


# A check whose firing conditions *split* keeps its pre-split id as an umbrella
# for manifest configuration. This is deliberately NOT
# ``LEGACY_CHECK_ID_ALIASES``: the key here is not deprecated — it still emits
# for its own half — so a baseline entry naming it must not be reported as a
# stale alias.
#
# Without this map a split silently changes a configured release decision. A
# repository that had already raised the no-base policy fail-safe with
# ``checks.severity_overrides: {SHIP-VERIFY-POLICY-WEAKENED: critical}`` would,
# after the rename alone, get a `medium` finding and a `review_required` verdict
# where it previously got `critical` and `blocked` — a gate that quietly
# loosened because an id moved. Configuration written against the umbrella must
# keep reaching both halves; an override written against a half still wins,
# because ``_severity_override_for_check`` prefers the exact id.
#
# Floor enforcement is unaffected: ``_resolve_metadata`` resolves the umbrella
# id directly in the catalog, so an override against it is still validated
# against the umbrella's own (stricter) floor.
SPLIT_CHECK_ID_ALIASES: dict[str, tuple[str, ...]] = {
    "SHIP-VERIFY-POLICY-WEAKENED": ("SHIP-VERIFY-POLICY-BASE-ABSENT",),
}


def expands_to_check_id(configured_check_id: str, emitted_check_id: str) -> bool:
    """Return whether a configured check id should match an emitted finding."""
    return (
        configured_check_id == emitted_check_id
        or emitted_check_id in LEGACY_CHECK_ID_ALIASES.get(configured_check_id, ())
        or emitted_check_id in SPLIT_CHECK_ID_ALIASES.get(configured_check_id, ())
    )


def known_check_ids_with_legacy(check_ids: Iterable[str]) -> set[str]:
    return {*check_ids, *LEGACY_CHECK_ID_ALIASES}
