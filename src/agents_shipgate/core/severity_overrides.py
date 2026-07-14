"""v0.17 (M1) severity-override validation + audit.

Splits responsibility cleanly from ``core/findings/``:

- ``findings.apply_severity_overrides`` stays the public mutation point
  on the finding list (kept by ``cli/scan/decision.py`` and existing tests).
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
from agents_shipgate.core.evaluation_clock import evaluation_date
from agents_shipgate.schemas.checks import CheckMetadata
from agents_shipgate.schemas.common import Severity
from agents_shipgate.schemas.report import (
    PolicyAudit,
    SeverityOverrideAuditEntry,
)

if TYPE_CHECKING:
    from agents_shipgate.schemas.manifest import (
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
    extra_known_check_defaults: dict[str, Severity] | None = None,
    manifest_path_prefix: str = "shipgate.yaml#/checks/severity_overrides",
    today: date | None = None,
) -> SeverityOverrideResolution:
    """Resolve manifest severity overrides into apply-able form + audit.

    Raises ``ConfigError`` (exit 2) for any of:

    - Override targeting an unknown check ID (no built-in, no legacy
      alias, not produced by a loaded plugin or policy pack). The
      unknown-check_id surface is otherwise already covered by
      ``SHIP-MANIFEST-STALE-SUPPRESSION`` for the ``ignore`` path; the
      override path keeps its own pre-check because applying an unknown
      override silently is exactly the trust hole M1 closes.
    - Override resolving below ``CheckMetadata.floor_severity``. Hard
      contract; no acknowledgement bypasses it.
    - Tier-crossing downgrade without a matching
      ``acknowledge_overrides`` entry.
    - Rich-form override entry whose ``expires`` is on or before
      ``today``. Same hard contract as expired acknowledgements — the
      user asserted a review date and the date passed.
    - Acknowledgement whose ``expires`` is on or before ``today``.

    Upgrades (override stronger than default) never require
    acknowledgement and never fail — they are strictly conservative.

    ``extra_known_check_defaults`` carries the *effective* default
    severity per check ID, used in two cases:

    1. **Outside-catalog check IDs.** Policy-pack rules whose IDs flow
       through ``run_checks(extra_known_check_ids=...)`` are valid
       override targets but live outside the built-in catalog. The
       resolver synthesizes a metadata entry with the supplied default
       and no floor (floors are a built-in trust contract).
    2. **Catalog checks with manifest-declared dynamic severity.**
       ``SHIP-ACTION-POLICY-VIOLATION`` findings emit at the matching
       action policy's declared severity (see
       ``action_surface.policies[].severity``), not the static catalog
       default. Without this signal, a `severity: critical` action
       policy with override `high` would compare against the catalog
       default `high` and silently bypass the critical→high tier-crossing
       gate. When the supplied default is *stronger* than the catalog
       default, the resolver uses the supplied value for tier-crossing
       and audit purposes. Floor enforcement still uses the catalog
       floor (the static gate floor for the check class).
    """
    today = today or evaluation_date()
    catalog_by_id = _catalog_index(catalog)
    known_ids = _known_check_ids(catalog)
    ack_by_id = _ack_by_check_id(acknowledgements)
    extra_defaults = extra_known_check_defaults or {}

    # 1. Expired acknowledgements are a config error regardless of
    #    whether the matching override is tier-crossing — the user
    #    asserted a review date, that date passed, the gate refuses.
    _enforce_ack_expiry(acknowledgements, today=today)

    # 1b. Expired rich-form override entries are the same hard contract.
    #     STABILITY.md promises ``expires`` is a hard expiry; treating
    #     it as advisory only on ack-bound entries would be a footgun.
    _enforce_override_expiry(overrides, today=today)

    audit = PolicyAudit()
    applied: dict[str, Severity] = {}

    for check_id, entry in overrides.items():
        # Resolve target check metadata. Three lookup paths:
        # 1. Direct catalog hit (built-in or plugin check).
        # 2. Legacy alias expansion (e.g. SHIP-API-OPERATIONAL-READINESS).
        # 3. Policy-pack rule ID — passed in via
        #    ``extra_known_check_defaults`` because policy-pack rules
        #    don't carry a CheckMetadata entry in the catalog. These
        #    have no floor (floors are a built-in trust contract).
        target_metadata = _resolve_metadata(
            check_id, catalog_by_id=catalog_by_id, known_ids=known_ids
        )
        if target_metadata is None and check_id in extra_defaults:
            target_metadata = CheckMetadata(
                id=check_id,
                category="policy_pack",
                default_severity=extra_defaults[check_id],
                description="Policy-pack rule (no built-in floor).",
            )
        if target_metadata is None:
            raise ConfigError(
                f"checks.severity_overrides[{check_id!r}] targets an "
                f"unknown check_id. Use `agents-shipgate list-checks --json` "
                f"to list valid IDs."
            )

        # v0.18 (PR #1): internal-consistency guard. A catalog check
        # carrying ``dynamic_default=True`` MUST have a corresponding
        # entry in ``extra_known_check_defaults``. The aggregator in
        # ``core/dynamic_defaults.py:dynamic_check_defaults`` seeds
        # every such catalog check unconditionally (its step 1), so
        # this guard fires ONLY when the catalog and aggregator are
        # out of sync — never on user input. RuntimeError (not
        # ConfigError) because the contract test
        # ``test_dynamic_default_aggregator_completeness`` catches it
        # pre-merge; ``assert`` is unsafe because it's stripped under
        # ``python -O``.
        if target_metadata.dynamic_default and check_id not in extra_defaults:
            raise RuntimeError(
                f"internal: dynamic-default check {check_id!r} missing "
                f"from extra_known_check_defaults; the aggregator in "
                f"core/dynamic_defaults.py:dynamic_check_defaults must "
                f"seed every catalog check with dynamic_default=True."
            )

        applied_severity = entry.severity
        # Effective default for tier-crossing: max(catalog default,
        # manifest-declared dynamic default). Closes the
        # SHIP-ACTION-POLICY-VIOLATION bypass — an action policy
        # declared at ``severity: critical`` makes the effective
        # default critical, even though the catalog static default
        # for that check is high. See ``cli/scan/decision.py`` for the
        # call-site that aggregates action-policy declarations.
        catalog_default_severity = target_metadata.default_severity
        dynamic_default = extra_defaults.get(check_id)
        if dynamic_default is not None and _is_weaker(catalog_default_severity, dynamic_default):
            default_severity = dynamic_default
        else:
            default_severity = catalog_default_severity

        # 2. Floor enforcement. Hard. No ack bypass. Policy-pack rules
        #    fall through with floor=None (extra_defaults path above).
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
            # Ack reason wins when both are set — ack is the explicit
            # tier-crossing audit signal. Rich-form ``reason`` on the
            # entry still appears in audit only via the entry path.
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
        f"  - {ack.check_id}: expired on {ack.expires.isoformat()}" for ack in expired
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


def _enforce_override_expiry(
    overrides: dict[str, SeverityOverrideEntry],
    *,
    today: date,
) -> None:
    """v0.17 (M1): rich-form override entries with ``expires`` are a hard
    time gate, parallel to ``acknowledge_overrides``.

    Without this check, an expired rich-form override would silently
    keep applying — STABILITY.md and the schema docstring promise
    otherwise. Same hard contract as ack expiry: exit 2 on/past the
    expires date, no advisory bypass.
    """
    expired = [
        (check_id, entry)
        for check_id, entry in overrides.items()
        if entry.expires is not None and entry.expires <= today
    ]
    if not expired:
        return
    bullets = "\n".join(
        f"  - {check_id}: expired on {entry.expires.isoformat()}"  # type: ignore[union-attr]
        for check_id, entry in expired
    )
    plural = "s" if len(expired) > 1 else ""
    raise ConfigError(
        f"checks.severity_overrides has {len(expired)} expired "
        f"entr{('ies' if len(expired) > 1 else 'y')} (today={today.isoformat()}):\n"
        f"{bullets}\n"
        f"Renew the review and update the expires date{plural}, or remove "
        f"the rich-form override entries (which lets the check fire at "
        f"its declared default severity)."
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
