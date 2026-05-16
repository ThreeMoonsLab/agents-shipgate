"""v0.17 (M1) severity-override validation + audit.

Splits responsibility cleanly from ``core/findings.py``:

- ``findings.apply_severity_overrides`` stays the public mutation point
  on the finding list (kept by ``cli/scan.py`` and existing tests).
- This module owns the *policy validation* — floor enforcement, tier
  detection, acknowledgement matching, expiry checks — and produces an
  immutable ``SeverityOverrideResolution`` that the apply step consumes
  without re-deriving anything.

Why a separate module: the validation surface is large enough to deserve
isolated tests, and the M2/M5 trust-hardening items will reuse the same
``PolicyAudit`` envelope, so concentrating the audit-row construction
here keeps that surface single-source-of-truth.

Threat model context (see STABILITY.md "Severity-override trust"):

- **Floor** is a hard contract. No acknowledgement bypasses it.
- **Tier crossing** is friction-only: a downgrade that crosses a tier
  boundary (critical ↔ high, high ↔ medium/low/info) requires the
  reviewer to add an ``acknowledge_overrides`` entry with a reason. The
  reason becomes the audit row's ``reason``.
- **Expiry** is a hard time gate: an expired acknowledgement raises
  ``ConfigError`` (exit 2) before the scan completes; there is no
  warning path. This makes "I'll add an expiry and forget" impossible.

The function set here is pure: no I/O, no environment, no time
indirection beyond the explicit ``today`` parameter (defaulted to
``date.today()`` for production callers, injectable for tests).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

from agents_shipgate.core.check_ids import expands_to_check_id
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.models import (
    CheckMetadata,
    PolicyAudit,
    Severity,
    SeverityOverrideAuditEntry,
)

if TYPE_CHECKING:
    from agents_shipgate.config.schema import (
        OverrideAcknowledgement,
        SeverityOverrideEntry,
    )

# Weakest → strongest. Lower number == stronger severity, matching
# ``findings.SEVERITY_ORDER``. Duplicated here to avoid a circular import
# (findings.py imports from this module's siblings).
_SEVERITY_RANK: dict[Severity, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}


def _severity_rank(value: Severity) -> int:
    return _SEVERITY_RANK[value]


def _is_weaker(applied: Severity, baseline: Severity) -> bool:
    """``applied`` is strictly weaker (higher rank number) than ``baseline``."""
    return _severity_rank(applied) > _severity_rank(baseline)


# Three tiers. Tier crossing is what triggers the acknowledgement
# requirement. Same-tier downgrades (e.g. medium → low) do not require
# ack — the user is fine-tuning within a band that reviewers consider
# equivalent for release purposes.
_SEVERITY_TIER: dict[Severity, str] = {
    "critical": "critical",
    "high": "high",
    "medium": "normal",
    "low": "normal",
    "info": "normal",
}


def severity_tier(value: Severity) -> str:
    return _SEVERITY_TIER[value]


def crosses_tier(default: Severity, applied: Severity) -> bool:
    return _SEVERITY_TIER[default] != _SEVERITY_TIER[applied]


# --- Resolution result -----------------------------------------------------


@dataclass(frozen=True)
class SeverityOverrideResolution:
    """Immutable result of ``resolve_severity_overrides``.

    ``override_by_check_id`` is the flat scalar form the existing
    finding-list mutation in ``findings.apply_severity_overrides``
    consumes. ``audit`` is the policy-audit envelope hung off
    ``ReadinessReport.policy_audit``.

    Keeping these as one object enforces a single resolution pass; if
    findings.py recomputed from the rich entries, the audit and the
    applied overrides could disagree.
    """

    override_by_check_id: dict[str, Severity] = field(default_factory=dict)
    audit: PolicyAudit = field(default_factory=PolicyAudit)


# --- Validation entrypoints ------------------------------------------------


def resolve_severity_overrides(
    *,
    overrides: dict[str, SeverityOverrideEntry],
    acknowledgements: list[OverrideAcknowledgement],
    catalog: list[CheckMetadata],
    manifest_path_prefix: str = "shipgate.yaml#/checks/severity_overrides",
    today: date | None = None,
) -> SeverityOverrideResolution:
    """Resolve manifest severity overrides into apply-able form + audit.

    Raises ``ConfigError`` (exit 2) for any of:

    - Override targeting an unknown check ID (no built-in, no legacy
      alias, not produced by a loaded plugin). The unknown-check_id
      surface is otherwise already covered by
      ``SHIP-MANIFEST-STALE-SUPPRESSION`` for the ``ignore`` path; the
      override path keeps its own pre-check because applying an unknown
      override silently is exactly the trust hole M1 closes.
    - Override resolving below ``CheckMetadata.floor_severity``. Hard
      contract; no acknowledgement bypasses it.
    - Tier-crossing downgrade without a matching
      ``acknowledge_overrides`` entry.
    - Acknowledgement whose ``expires`` is on or before ``today``.

    Upgrades (override stronger than default) never require
    acknowledgement and never fail — they are strictly conservative.
    """
    today = today or date.today()
    catalog_by_id = _catalog_index(catalog)
    known_ids = _known_check_ids(catalog)
    ack_by_id = _ack_by_check_id(acknowledgements)

    # 1. Expired acknowledgements are a config error regardless of
    #    whether the matching override is tier-crossing — the user
    #    asserted a review date, that date passed, the gate refuses.
    _enforce_ack_expiry(acknowledgements, today=today)

    audit = PolicyAudit()
    applied: dict[str, Severity] = {}

    for check_id, entry in overrides.items():
        # Resolve target check metadata. The override can be configured
        # against either a current check ID or a legacy alias (e.g.
        # SHIP-API-OPERATIONAL-READINESS that fanned out in v0.4).
        target_metadata = _resolve_metadata(
            check_id, catalog_by_id=catalog_by_id, known_ids=known_ids
        )
        if target_metadata is None:
            raise ConfigError(
                f"checks.severity_overrides[{check_id!r}] targets an "
                f"unknown check_id. Use `agents-shipgate list-checks --json` "
                f"to list valid IDs."
            )

        applied_severity = entry.severity
        default_severity = target_metadata.default_severity

        # 2. Floor enforcement. Hard. No ack bypass.
        floor = target_metadata.floor_severity
        if floor is not None and _is_weaker(applied_severity, floor):
            raise ConfigError(
                f"checks.severity_overrides[{check_id!r}] resolves to "
                f"{applied_severity!r}, which is below the floor "
                f"({floor!r}) declared for this check. Acknowledgement "
                f"does not bypass the floor; choose a severity ≥ {floor!r} "
                f"or remove the override."
            )

        # 3. Tier-crossing downgrade requires explicit acknowledgement.
        is_downgrade = _is_weaker(applied_severity, default_severity)
        tier_crossed = crosses_tier(default_severity, applied_severity)
        if is_downgrade and tier_crossed:
            ack = ack_by_id.get(check_id)
            if ack is None:
                # Surface the alias path too — a user who configured
                # SHIP-API-OPERATIONAL-READINESS gets the *alias* name in
                # the diagnostic, not the expanded one.
                raise ConfigError(
                    f"checks.severity_overrides[{check_id!r}] downgrades "
                    f"{default_severity!r} → {applied_severity!r}, "
                    f"crossing the {severity_tier(default_severity)} → "
                    f"{severity_tier(applied_severity)} tier boundary. "
                    f"Add an acknowledge_overrides entry with a reason."
                )
            reason: str | None = ack.reason
            expires_iso = ack.expires.isoformat() if ack.expires else None
        else:
            # Same-tier downgrade or upgrade: optional rich-form reason
            # propagates if supplied; no ack lookup.
            reason = entry.reason
            expires_iso = entry.expires.isoformat() if entry.expires else None

        # 4. Build the audit row. Every override goes into the audit,
        #    not just downgrades — reviewers want a full picture.
        direction: str
        if _is_weaker(applied_severity, default_severity):
            direction = "downgrade"
        elif _is_weaker(default_severity, applied_severity):
            direction = "upgrade"
        else:
            direction = "same"

        audit.severity_overrides_applied.append(
            SeverityOverrideAuditEntry(
                check_id=check_id,
                default_severity=default_severity,
                applied_severity=applied_severity,
                manifest_path=f"{manifest_path_prefix}/{check_id}",
                reason=reason,
                tier_crossed=tier_crossed,
                direction=direction,  # type: ignore[arg-type]
                expires=expires_iso,
            )
        )
        applied[check_id] = applied_severity

    # 5. Acknowledgement-without-override is a soft inconsistency. We
    #    do NOT raise: removing an override but keeping its ack is a
    #    natural transient state during PR review. ``SHIP-MANIFEST-*``
    #    family will pick it up as stale config in M2's audit follow-up.

    return SeverityOverrideResolution(
        override_by_check_id=applied,
        audit=audit,
    )


def _enforce_ack_expiry(
    acknowledgements: list[OverrideAcknowledgement],
    *,
    today: date,
) -> None:
    expired = [ack for ack in acknowledgements if ack.expires and ack.expires <= today]
    if not expired:
        return
    bullets = "\n".join(
        f"  - {ack.check_id}: expired on {ack.expires.isoformat()}"
        for ack in expired
    )
    plural = "s" if len(expired) > 1 else ""
    raise ConfigError(
        f"checks.acknowledge_overrides has {len(expired)} expired "
        f"entr{('ies' if len(expired) > 1 else 'y')} (today={today.isoformat()}):\n"
        f"{bullets}\n"
        f"Renew the review and update the expires date{plural}, or remove "
        f"the acknowledgement{plural} (which will re-require the override "
        f"to be raised back into-tier)."
    )


def _ack_by_check_id(
    acknowledgements: list[OverrideAcknowledgement],
) -> dict[str, OverrideAcknowledgement]:
    # Uniqueness already enforced by ChecksConfig._ack_check_ids_unique.
    return {ack.check_id: ack for ack in acknowledgements}


def _catalog_index(catalog: list[CheckMetadata]) -> dict[str, CheckMetadata]:
    return {entry.id: entry for entry in catalog}


def _known_check_ids(catalog: list[CheckMetadata]) -> set[str]:
    return {entry.id for entry in catalog}


def _resolve_metadata(
    check_id: str,
    *,
    catalog_by_id: dict[str, CheckMetadata],
    known_ids: set[str],
) -> CheckMetadata | None:
    direct = catalog_by_id.get(check_id)
    if direct is not None:
        return direct
    # Legacy alias support: an override against e.g. the v0.3
    # SHIP-API-OPERATIONAL-READINESS bundle expands to several v0.4
    # atomic checks. We can validate by checking whether the configured
    # ID expands to ANY known check; the floor used for diagnostic
    # purposes is then the **strictest** floor among the expansions
    # (safest semantics — caller intent is to apply uniformly).
    candidates = [
        catalog_by_id[known]
        for known in known_ids
        if expands_to_check_id(check_id, known) and known in catalog_by_id
    ]
    if not candidates:
        return None
    # Return a synthetic metadata that conservatively represents the
    # alias: keep the alias ID, take the floor as the strictest
    # (lowest-rank, i.e. closest to critical) floor among expansions,
    # and the default as the strictest default. This means floor
    # enforcement against a legacy alias is at least as strict as the
    # individual expansions would be — safe-closed.
    strictest_default = min(
        candidates, key=lambda meta: _severity_rank(meta.default_severity)
    ).default_severity
    floors = [meta.floor_severity for meta in candidates if meta.floor_severity]
    strictest_floor: Severity | None = None
    if floors:
        strictest_floor = min(floors, key=_severity_rank)
    # Note: we do NOT mutate the catalog entries — return a synthetic
    # ``CheckMetadata`` carrying just the fields the resolver reads. Use
    # the first candidate's category/description to satisfy required
    # fields without inventing new content.
    template = candidates[0]
    return CheckMetadata(
        id=check_id,
        category=template.category,
        default_severity=strictest_default,
        description=template.description,
        floor_severity=strictest_floor,
    )
