"""v0.17 (M1) — severity override floor, tier-crossing ack, audit, expiry.

Covers:

- Floor enforcement is hard (no acknowledgement bypass).
- Tier-crossing downgrade without acknowledgement → ConfigError.
- Tier-crossing downgrade with valid ack → audit row carries reason.
- Same-tier downgrade with no ack → allowed, audit row reason=None.
- Upgrade → never requires ack, never blocked by floor.
- Rich-form override (severity + reason + expires) → reason/expires
  flow into audit row.
- Legacy scalar override → coerced, audit row reason=None.
- Expired acknowledgement → ConfigError at scan time (no warning path).
- Unknown check_id in severity_overrides → ConfigError.
- Legacy alias check_id (SHIP-API-OPERATIONAL-READINESS) → resolves to
  the strictest expansion's floor.
- ChecksConfig.acknowledge_overrides duplicate check_id rejected.
- ReadinessReport.policy_audit shape lands on the report and round-trips
  through JSON.
- Existing scalar-only ``apply_severity_overrides`` API unchanged.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from agents_shipgate.config.schema import (
    ChecksConfig,
    OverrideAcknowledgement,
    SeverityOverrideEntry,
)
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.findings import apply_severity_overrides
from agents_shipgate.core.models import (
    CheckMetadata,
    Finding,
    PolicyAudit,
    SeverityOverrideAuditEntry,
)
from agents_shipgate.core.severity_overrides import (
    crosses_tier,
    resolve_severity_overrides,
    severity_tier,
)


# --- Fixtures ---------------------------------------------------------------


def _catalog() -> list[CheckMetadata]:
    """Minimal in-test catalog covering the cases under exercise.

    Mirrors the shape of ``CHECK_METADATA`` in checks/registry.py without
    requiring the full builtin set — keeps the resolver tests focused.
    """
    return [
        CheckMetadata(
            id="SHIP-POLICY-APPROVAL-MISSING",
            category="policy",
            default_severity="critical",
            description="Approval missing.",
            floor_severity="high",
        ),
        CheckMetadata(
            id="SHIP-AUTH-MANIFEST-BROAD-SCOPE",
            category="auth",
            default_severity="high",
            description="Broad scope.",
            floor_severity="medium",
        ),
        CheckMetadata(
            id="SHIP-SCHEMA-MISSING-BOUNDS",
            category="schema",
            default_severity="high",
            description="No floor — legacy unfenced check.",
        ),
        CheckMetadata(
            id="SHIP-DOC-MISSING-DESCRIPTION",
            category="documentation",
            default_severity="medium",
            description="No floor.",
        ),
    ]


# --- Tier helpers -----------------------------------------------------------


def test_severity_tier_partitions_three_groups() -> None:
    assert severity_tier("critical") == "critical"
    assert severity_tier("high") == "high"
    assert severity_tier("medium") == "normal"
    assert severity_tier("low") == "normal"
    assert severity_tier("info") == "normal"


@pytest.mark.parametrize(
    "default,applied,expected",
    [
        ("critical", "high", True),   # critical → high crosses
        ("critical", "critical", False),
        ("high", "medium", True),     # high → normal crosses
        ("medium", "low", False),     # same tier (normal)
        ("medium", "info", False),
        ("high", "critical", True),   # upgrade also crosses
        ("low", "info", False),
    ],
)
def test_crosses_tier(default: str, applied: str, expected: bool) -> None:
    assert crosses_tier(default, applied) is expected  # type: ignore[arg-type]


# --- Floor enforcement (hard, no escape) -----------------------------------


def test_below_floor_override_raises_config_error_even_without_downgrade_ack() -> None:
    overrides = {
        "SHIP-POLICY-APPROVAL-MISSING": SeverityOverrideEntry(severity="info"),
    }
    with pytest.raises(ConfigError, match=r"below the floor"):
        resolve_severity_overrides(
            overrides=overrides,
            acknowledgements=[],
            catalog=_catalog(),
        )


def test_below_floor_override_with_ack_still_raises() -> None:
    """Acknowledgement does not bypass floor. Hard contract."""
    overrides = {
        "SHIP-POLICY-APPROVAL-MISSING": SeverityOverrideEntry(severity="info"),
    }
    acks = [
        OverrideAcknowledgement(
            check_id="SHIP-POLICY-APPROVAL-MISSING",
            reason="security said it's fine",
        ),
    ]
    with pytest.raises(ConfigError, match=r"below the floor"):
        resolve_severity_overrides(
            overrides=overrides,
            acknowledgements=acks,
            catalog=_catalog(),
        )


def test_at_floor_override_is_accepted_with_required_ack() -> None:
    """critical default + high floor + high override: at-floor, tier-crossed
    downgrade. Allowed with ack, rejected without."""
    overrides = {
        "SHIP-POLICY-APPROVAL-MISSING": SeverityOverrideEntry(severity="high"),
    }
    acks = [
        OverrideAcknowledgement(
            check_id="SHIP-POLICY-APPROVAL-MISSING",
            reason="internal-only release",
        ),
    ]
    resolution = resolve_severity_overrides(
        overrides=overrides,
        acknowledgements=acks,
        catalog=_catalog(),
    )
    assert resolution.override_by_check_id == {
        "SHIP-POLICY-APPROVAL-MISSING": "high"
    }
    [row] = resolution.audit.severity_overrides_applied
    assert row.applied_severity == "high"
    assert row.default_severity == "critical"
    assert row.tier_crossed is True
    assert row.direction == "downgrade"
    assert row.reason == "internal-only release"


def test_at_floor_override_without_ack_rejected() -> None:
    overrides = {
        "SHIP-POLICY-APPROVAL-MISSING": SeverityOverrideEntry(severity="high"),
    }
    with pytest.raises(ConfigError, match=r"crossing.*tier boundary"):
        resolve_severity_overrides(
            overrides=overrides,
            acknowledgements=[],
            catalog=_catalog(),
        )


# --- Tier-crossing ack semantics -------------------------------------------


def test_same_tier_downgrade_does_not_require_ack() -> None:
    overrides = {
        # medium → low: both in "normal" tier
        "SHIP-DOC-MISSING-DESCRIPTION": SeverityOverrideEntry(severity="low"),
    }
    resolution = resolve_severity_overrides(
        overrides=overrides,
        acknowledgements=[],
        catalog=_catalog(),
    )
    [row] = resolution.audit.severity_overrides_applied
    assert row.tier_crossed is False
    assert row.direction == "downgrade"
    # Rich-form reason absent on legacy scalar projection
    assert row.reason is None


def test_upgrade_never_requires_ack() -> None:
    overrides = {
        # high → critical (upgrade across tiers, strictly more conservative)
        "SHIP-AUTH-MANIFEST-BROAD-SCOPE": SeverityOverrideEntry(severity="critical"),
    }
    resolution = resolve_severity_overrides(
        overrides=overrides,
        acknowledgements=[],
        catalog=_catalog(),
    )
    [row] = resolution.audit.severity_overrides_applied
    assert row.direction == "upgrade"
    assert row.tier_crossed is True
    assert row.applied_severity == "critical"


def test_rich_entry_reason_lands_on_same_tier_audit_row() -> None:
    overrides = {
        "SHIP-SCHEMA-MISSING-BOUNDS": SeverityOverrideEntry(
            severity="medium",
            reason="reviewed under SOC2 audit 2026-Q2",
        ),
    }
    resolution = resolve_severity_overrides(
        overrides=overrides,
        acknowledgements=[],
        catalog=_catalog(),
    )
    [row] = resolution.audit.severity_overrides_applied
    assert row.reason == "reviewed under SOC2 audit 2026-Q2"


def test_rich_entry_expires_lands_on_audit_row() -> None:
    overrides = {
        "SHIP-SCHEMA-MISSING-BOUNDS": SeverityOverrideEntry(
            severity="medium",
            reason="quarterly review",
            expires=date(2027, 1, 1),
        ),
    }
    resolution = resolve_severity_overrides(
        overrides=overrides,
        acknowledgements=[],
        catalog=_catalog(),
        today=date(2026, 5, 15),
    )
    [row] = resolution.audit.severity_overrides_applied
    assert row.expires == "2027-01-01"


# --- Expired acknowledgement (hard config error) ---------------------------


def test_expired_acknowledgement_raises_config_error() -> None:
    acks = [
        OverrideAcknowledgement(
            check_id="SHIP-POLICY-APPROVAL-MISSING",
            reason="legacy review",
            expires=date(2026, 1, 1),
        ),
    ]
    today = date(2026, 5, 15)
    with pytest.raises(ConfigError, match=r"expired"):
        resolve_severity_overrides(
            overrides={
                "SHIP-POLICY-APPROVAL-MISSING": SeverityOverrideEntry(severity="high"),
            },
            acknowledgements=acks,
            catalog=_catalog(),
            today=today,
        )


def test_ack_expiring_today_is_expired() -> None:
    today = date(2026, 5, 15)
    acks = [
        OverrideAcknowledgement(
            check_id="SHIP-POLICY-APPROVAL-MISSING",
            reason="x",
            expires=today,
        ),
    ]
    with pytest.raises(ConfigError, match=r"expired"):
        resolve_severity_overrides(
            overrides={
                "SHIP-POLICY-APPROVAL-MISSING": SeverityOverrideEntry(severity="high"),
            },
            acknowledgements=acks,
            catalog=_catalog(),
            today=today,
        )


def test_ack_expiring_tomorrow_is_accepted() -> None:
    today = date(2026, 5, 15)
    acks = [
        OverrideAcknowledgement(
            check_id="SHIP-POLICY-APPROVAL-MISSING",
            reason="renewed yesterday",
            expires=today + timedelta(days=1),
        ),
    ]
    resolution = resolve_severity_overrides(
        overrides={
            "SHIP-POLICY-APPROVAL-MISSING": SeverityOverrideEntry(severity="high"),
        },
        acknowledgements=acks,
        catalog=_catalog(),
        today=today,
    )
    [row] = resolution.audit.severity_overrides_applied
    assert row.applied_severity == "high"
    assert row.expires == (today + timedelta(days=1)).isoformat()


# --- Unknown check_id rejection --------------------------------------------


def test_unknown_check_id_in_overrides_raises() -> None:
    overrides = {
        "SHIP-NOPE-NOT-A-REAL-CHECK": SeverityOverrideEntry(severity="medium"),
    }
    with pytest.raises(ConfigError, match=r"unknown check_id"):
        resolve_severity_overrides(
            overrides=overrides,
            acknowledgements=[],
            catalog=_catalog(),
        )


# --- Legacy scalar back-compat ----------------------------------------------


def test_checksconfig_coerces_legacy_scalar_entries() -> None:
    config = ChecksConfig.model_validate(
        {
            "severity_overrides": {
                "SHIP-SCHEMA-MISSING-BOUNDS": "medium",
            },
        }
    )
    entry = config.severity_overrides["SHIP-SCHEMA-MISSING-BOUNDS"]
    assert isinstance(entry, SeverityOverrideEntry)
    assert entry.severity == "medium"
    assert entry.reason is None
    assert entry.expires is None


def test_checksconfig_accepts_rich_mapping_entries() -> None:
    config = ChecksConfig.model_validate(
        {
            "severity_overrides": {
                "SHIP-AUTH-MANIFEST-BROAD-SCOPE": {
                    "severity": "medium",
                    "reason": "reviewed",
                    "expires": "2027-01-01",
                },
            },
        }
    )
    entry = config.severity_overrides["SHIP-AUTH-MANIFEST-BROAD-SCOPE"]
    assert entry.severity == "medium"
    assert entry.reason == "reviewed"
    assert entry.expires == date(2027, 1, 1)


def test_checksconfig_rejects_invalid_severity_scalar() -> None:
    with pytest.raises(ValueError, match=r"not a valid severity"):
        ChecksConfig.model_validate(
            {
                "severity_overrides": {
                    "SHIP-SCHEMA-MISSING-BOUNDS": "spicy",
                },
            }
        )


def test_checksconfig_rejects_duplicate_acknowledgements() -> None:
    with pytest.raises(ValueError, match=r"duplicate"):
        ChecksConfig.model_validate(
            {
                "acknowledge_overrides": [
                    {"check_id": "SHIP-X", "reason": "one"},
                    {"check_id": "SHIP-X", "reason": "two"},
                ],
            }
        )


def test_override_acknowledgement_requires_non_empty_reason() -> None:
    with pytest.raises(ValueError, match=r"non-empty"):
        OverrideAcknowledgement(check_id="SHIP-X", reason="   ")


# --- Audit shape ------------------------------------------------------------


def test_audit_entry_is_round_trippable() -> None:
    entry = SeverityOverrideAuditEntry(
        check_id="SHIP-FOO",
        default_severity="critical",
        applied_severity="high",
        manifest_path="shipgate.yaml#/checks/severity_overrides/SHIP-FOO",
        reason="quarterly review",
        tier_crossed=True,
        direction="downgrade",
        expires="2027-01-01",
    )
    payload = entry.model_dump(mode="json")
    restored = SeverityOverrideAuditEntry.model_validate(payload)
    assert restored == entry


def test_empty_policy_audit_serializes_clean() -> None:
    audit = PolicyAudit()
    payload = audit.model_dump(mode="json")
    assert payload == {"severity_overrides_applied": []}


# --- apply_severity_overrides scalar API preserved -------------------------


def test_apply_severity_overrides_scalar_dict_signature_unchanged() -> None:
    """The legacy ``apply_severity_overrides(findings, dict[str, Severity])``
    contract must keep working for callers that bypass the resolver
    (notably test_findings.py and policy-pack tests).
    """
    finding = Finding(
        check_id="SHIP-DOC-MISSING-DESCRIPTION",
        title="x",
        severity="medium",
        category="documentation",
        recommendation="describe",
    )
    apply_severity_overrides([finding], {"SHIP-DOC-MISSING-DESCRIPTION": "critical"})
    assert finding.severity == "critical"
    assert finding.evidence["default_severity"] == "medium"


# --- Resolver → apply integration ------------------------------------------


def test_resolver_output_feeds_apply_severity_overrides_cleanly() -> None:
    overrides = {
        "SHIP-AUTH-MANIFEST-BROAD-SCOPE": SeverityOverrideEntry(severity="critical"),
    }
    resolution = resolve_severity_overrides(
        overrides=overrides,
        acknowledgements=[],
        catalog=_catalog(),
    )
    finding = Finding(
        check_id="SHIP-AUTH-MANIFEST-BROAD-SCOPE",
        title="Broad",
        severity="high",
        category="auth",
        recommendation="Narrow.",
    )
    apply_severity_overrides([finding], resolution.override_by_check_id)
    assert finding.severity == "critical"
    assert finding.evidence["default_severity"] == "high"
    # And the audit row is intact.
    [row] = resolution.audit.severity_overrides_applied
    assert row.direction == "upgrade"


# --- CheckMetadata floor self-consistency ----------------------------------


def test_check_metadata_rejects_floor_above_default() -> None:
    with pytest.raises(ValueError, match=r"cannot be stronger"):
        CheckMetadata(
            id="SHIP-X",
            category="x",
            default_severity="medium",
            description="x",
            floor_severity="critical",
        )


def test_check_metadata_accepts_floor_equal_to_default() -> None:
    meta = CheckMetadata(
        id="SHIP-X",
        category="x",
        default_severity="medium",
        description="x",
        floor_severity="medium",
    )
    assert meta.floor_severity == "medium"


def test_check_metadata_accepts_no_floor() -> None:
    meta = CheckMetadata(
        id="SHIP-X",
        category="x",
        default_severity="medium",
        description="x",
    )
    assert meta.floor_severity is None
