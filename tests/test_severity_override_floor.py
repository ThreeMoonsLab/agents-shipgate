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
        # Built-in for the action-surface policy bypass test below. The
        # static catalog default is "high", but findings emit at the
        # user-declared ``action_surface.policies[].severity``, which
        # can be critical.
        CheckMetadata(
            id="SHIP-ACTION-POLICY-VIOLATION",
            category="action_surface",
            default_severity="high",
            description="Action-surface policy violation.",
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
    # medium → low: both in "normal" tier, no ack required. The
    # rich-form ``reason`` flows into the audit row directly. (Earlier
    # versions of this test mistakenly used a high → medium override,
    # which is tier-crossing — the resolver correctly rejects that
    # without an ack. See PR 80 review fixup.)
    overrides = {
        "SHIP-DOC-MISSING-DESCRIPTION": SeverityOverrideEntry(
            severity="low",
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
    assert row.tier_crossed is False
    assert row.direction == "downgrade"


def test_rich_entry_expires_lands_on_audit_row() -> None:
    # Same-tier downgrade so we exercise the rich-form audit-row
    # passthrough cleanly. medium → low, no ack required.
    overrides = {
        "SHIP-DOC-MISSING-DESCRIPTION": SeverityOverrideEntry(
            severity="low",
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


# --- Rich-form override expiry (parallel to ack expiry, hard contract) -----


def test_expired_rich_form_override_raises_config_error() -> None:
    """STABILITY.md and the schema docstring both promise rich-form
    ``expires`` is a hard expiry — same contract as ack expiry. An
    expired rich-form override must raise, not silently apply.
    """
    today = date(2026, 5, 15)
    overrides = {
        "SHIP-DOC-MISSING-DESCRIPTION": SeverityOverrideEntry(
            severity="low",
            reason="quarterly review (now stale)",
            expires=date(2026, 1, 1),
        ),
    }
    with pytest.raises(ConfigError, match=r"expired"):
        resolve_severity_overrides(
            overrides=overrides,
            acknowledgements=[],
            catalog=_catalog(),
            today=today,
        )


def test_rich_form_override_expiring_today_is_expired() -> None:
    today = date(2026, 5, 15)
    overrides = {
        "SHIP-DOC-MISSING-DESCRIPTION": SeverityOverrideEntry(
            severity="low",
            expires=today,
        ),
    }
    with pytest.raises(ConfigError, match=r"expired"):
        resolve_severity_overrides(
            overrides=overrides,
            acknowledgements=[],
            catalog=_catalog(),
            today=today,
        )


def test_rich_form_override_expiring_tomorrow_applies_cleanly() -> None:
    today = date(2026, 5, 15)
    overrides = {
        "SHIP-DOC-MISSING-DESCRIPTION": SeverityOverrideEntry(
            severity="low",
            expires=today + timedelta(days=1),
        ),
    }
    resolution = resolve_severity_overrides(
        overrides=overrides,
        acknowledgements=[],
        catalog=_catalog(),
        today=today,
    )
    assert resolution.override_by_check_id == {
        "SHIP-DOC-MISSING-DESCRIPTION": "low"
    }


# --- Policy-pack rule IDs (no built-in floor) ------------------------------


def test_policy_pack_rule_override_resolves_when_id_passed_as_known() -> None:
    """Policy-pack rule IDs are valid override targets. The resolver
    accepts them via ``extra_known_check_defaults`` and applies no
    floor — floors are a built-in trust contract by design.

    Exercises the same-tier path here so the test only covers the
    "ID known to the resolver" promise; tier-crossing semantics for
    policy-pack rules are covered by the two tests below.
    """
    overrides = {
        "ORG-CUSTOM-CHECK": SeverityOverrideEntry(severity="low"),
    }
    resolution = resolve_severity_overrides(
        overrides=overrides,
        acknowledgements=[],
        catalog=_catalog(),
        extra_known_check_defaults={"ORG-CUSTOM-CHECK": "medium"},
    )
    [row] = resolution.audit.severity_overrides_applied
    assert row.default_severity == "medium"
    assert row.applied_severity == "low"
    assert row.tier_crossed is False
    assert row.direction == "downgrade"


def test_policy_pack_rule_override_tier_crossing_requires_ack() -> None:
    """Policy-pack rule IDs respect tier-crossing semantics: a
    downgrade that crosses a tier needs an acknowledgement, same as
    built-ins. This is what the test above relied on — split out
    explicitly so a regression here is loud.
    """
    overrides = {
        "ORG-HIGH-RISK-OWNER-MISSING": SeverityOverrideEntry(severity="medium"),
    }
    with pytest.raises(ConfigError, match=r"crossing.*tier boundary"):
        resolve_severity_overrides(
            overrides=overrides,
            acknowledgements=[],
            catalog=_catalog(),
            extra_known_check_defaults={"ORG-HIGH-RISK-OWNER-MISSING": "high"},
        )


def test_policy_pack_rule_override_same_tier_needs_no_ack() -> None:
    """medium → low policy-pack downgrade is same-tier (both normal).
    Goes through cleanly with no ack."""
    overrides = {
        "ORG-CUSTOM-CHECK": SeverityOverrideEntry(severity="low"),
    }
    resolution = resolve_severity_overrides(
        overrides=overrides,
        acknowledgements=[],
        catalog=_catalog(),
        extra_known_check_defaults={"ORG-CUSTOM-CHECK": "medium"},
    )
    assert resolution.override_by_check_id == {"ORG-CUSTOM-CHECK": "low"}


# --- Action-surface policies declare per-finding severity ------------------


def test_action_policy_critical_overrides_to_high_is_tier_crossing() -> None:
    """Regression for PR 80 review P1.2. An action policy declared
    ``severity: critical`` makes the effective default for
    ``SHIP-ACTION-POLICY-VIOLATION`` critical, even though the catalog
    static default is high. Override → high is therefore tier-crossing
    (critical tier → high tier) and requires an acknowledgement.

    Without ``extra_known_check_defaults`` carrying the manifest-declared
    severity, the resolver would compare high → high and silently
    accept, downgrading a severity-driven blocker to review_required.
    """
    overrides = {
        "SHIP-ACTION-POLICY-VIOLATION": SeverityOverrideEntry(severity="high"),
    }
    with pytest.raises(ConfigError, match=r"crossing.*tier boundary"):
        resolve_severity_overrides(
            overrides=overrides,
            acknowledgements=[],
            catalog=_catalog(),
            extra_known_check_defaults={
                "SHIP-ACTION-POLICY-VIOLATION": "critical",
            },
        )


def test_action_policy_critical_overrides_to_high_with_ack_passes() -> None:
    """Same as above, with the acknowledgement present. The audit row
    reports critical → high (the *effective* default, not the catalog
    static one) so reviewers can see the actual downgrade."""
    overrides = {
        "SHIP-ACTION-POLICY-VIOLATION": SeverityOverrideEntry(severity="high"),
    }
    acks = [
        OverrideAcknowledgement(
            check_id="SHIP-ACTION-POLICY-VIOLATION",
            reason="release-board approved this policy at high",
        ),
    ]
    resolution = resolve_severity_overrides(
        overrides=overrides,
        acknowledgements=acks,
        catalog=_catalog(),
        extra_known_check_defaults={
            "SHIP-ACTION-POLICY-VIOLATION": "critical",
        },
    )
    [row] = resolution.audit.severity_overrides_applied
    assert row.default_severity == "critical"
    assert row.applied_severity == "high"
    assert row.tier_crossed is True
    assert row.direction == "downgrade"
    assert row.reason == "release-board approved this policy at high"


def test_action_policy_dynamic_default_only_used_when_stronger() -> None:
    """If the manifest declares an action policy at ``severity: medium``
    (weaker than the catalog static default of high), the resolver
    keeps the catalog default for tier-crossing semantics. The dynamic
    default only escalates, never de-escalates."""
    overrides = {
        "SHIP-ACTION-POLICY-VIOLATION": SeverityOverrideEntry(severity="medium"),
    }
    # high (catalog) → medium IS tier-crossing — needs ack.
    with pytest.raises(ConfigError, match=r"crossing.*tier boundary"):
        resolve_severity_overrides(
            overrides=overrides,
            acknowledgements=[],
            catalog=_catalog(),
            # Manifest weaker than catalog: catalog wins.
            extra_known_check_defaults={
                "SHIP-ACTION-POLICY-VIOLATION": "medium",
            },
        )


def test_policy_pack_rule_override_with_ack_passes() -> None:
    """Same as the prior tier-crossing test, but the ack is present.
    The override applies and the audit row picks up the ack reason."""
    overrides = {
        "ORG-HIGH-RISK-OWNER-MISSING": SeverityOverrideEntry(severity="medium"),
    }
    acks = [
        OverrideAcknowledgement(
            check_id="ORG-HIGH-RISK-OWNER-MISSING",
            reason="internal-only release",
        ),
    ]
    resolution = resolve_severity_overrides(
        overrides=overrides,
        acknowledgements=acks,
        catalog=_catalog(),
        extra_known_check_defaults={"ORG-HIGH-RISK-OWNER-MISSING": "high"},
    )
    [row] = resolution.audit.severity_overrides_applied
    assert row.applied_severity == "medium"
    assert row.reason == "internal-only release"


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
    with pytest.raises(ValueError, match=r"must not exceed"):
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


# --- v0.18 (PR #1): dynamic_default contract --------------------------------


def test_dynamic_default_requires_floor_validator() -> None:
    """``CheckMetadata.dynamic_default=True`` without ``floor_severity``
    must fail at construction time. A swing check without a floor has
    no safety net against silent downgrade bypass.
    """
    with pytest.raises(ValueError, match=r"floor_severity"):
        CheckMetadata(
            id="SHIP-X-DYNAMIC",
            category="x",
            default_severity="high",
            description="dynamic",
            dynamic_default=True,
            floor_severity=None,
        )


def test_dynamic_default_with_floor_is_accepted() -> None:
    """The validator only fires when ``floor_severity`` is None."""
    meta = CheckMetadata(
        id="SHIP-X-DYNAMIC",
        category="x",
        default_severity="high",
        description="dynamic",
        dynamic_default=True,
        floor_severity="medium",
    )
    assert meta.dynamic_default is True
    assert meta.floor_severity == "medium"


def _base_manifest_dict() -> dict:
    return {
        "version": "0.1",
        "project": {"name": "p"},
        "agent": {"name": "a", "declared_purpose": ["t"]},
        "environment": {"target": "local"},
        "tool_sources": [{"id": "mcp", "type": "mcp", "path": "tools.json"}],
    }


# v0.18 (PR #1) — per-check overlay-verification registry. Each entry maps a
# ``dynamic_default=True`` catalog check ID to:
#   (manifest_overlay, expected_dynamic_severity)
# where ``manifest_overlay`` is a partial-manifest dict that, when merged
# into a minimal manifest, populates the section the aggregator MUST read
# to produce a manifest-effective default *stronger* than the catalog
# static. The expected severity must beat the catalog default for the
# check so the overlay is observable in the aggregator output.
#
# When a new built-in dynamic-default check is added:
#   1. CHECK_METADATA marks dynamic_default=True (forces floor_severity).
#   2. cli/scan.py:_dynamic_check_defaults gets an overlay branch.
#   3. _DYNAMIC_DEFAULT_OVERLAY_CASES gets an entry below.
#
# The completeness test below fails if (1) is done without (3), and the
# per-case test fails if (2) is missing — together they pin the contract
# the seed loop alone cannot enforce.
_DYNAMIC_DEFAULT_OVERLAY_CASES: dict[str, tuple[dict, str]] = {
    "SHIP-ACTION-POLICY-VIOLATION": (
        {
            "action_surface": {
                "policies": [
                    {
                        "id": "p1",
                        "severity": "critical",
                        "match": {"effects": ["write"]},
                        "require": {"approval.required": True},
                    }
                ]
            }
        },
        "critical",
    ),
}


def test_dynamic_default_overlay_registry_is_complete() -> None:
    """v0.18 (PR #1) contract: every catalog check carrying
    ``dynamic_default=True`` MUST have an entry in
    ``_DYNAMIC_DEFAULT_OVERLAY_CASES`` above. The entry documents
    which manifest section drives the dynamic default and pins the
    overlay-verification test below.

    A missing entry means a contributor added a new dynamic-default
    check without thinking through the manifest path that
    strengthens its default — exactly the wiring gap M1 closed for
    SHIP-ACTION-POLICY-VIOLATION. Add an entry, then ensure the
    aggregator overlay test passes.
    """
    from agents_shipgate.checks.registry import CHECK_METADATA

    dynamic_check_ids = {
        entry.id for entry in CHECK_METADATA if entry.dynamic_default
    }
    assert dynamic_check_ids, (
        "expected at least one dynamic_default=True check in catalog "
        "(SHIP-ACTION-POLICY-VIOLATION at minimum)"
    )
    missing = dynamic_check_ids - _DYNAMIC_DEFAULT_OVERLAY_CASES.keys()
    assert not missing, (
        f"dynamic_default=True check IDs without entries in "
        f"_DYNAMIC_DEFAULT_OVERLAY_CASES: {sorted(missing)}. "
        "Add an overlay case showing which manifest section drives the "
        "dynamic default for each new check, then ensure "
        "cli/scan.py:_dynamic_check_defaults has the matching overlay "
        "branch (the per-case test will fail until it does)."
    )


@pytest.mark.parametrize(
    "check_id",
    sorted(_DYNAMIC_DEFAULT_OVERLAY_CASES),
    ids=lambda cid: cid,
)
def test_dynamic_default_aggregator_overlay_fires(check_id: str) -> None:
    """v0.18 (PR #1) contract: for each dynamic-default check, the
    aggregator MUST overlay the manifest-effective default onto the
    seeded catalog default. Verified by populating the relevant manifest
    section with a known stronger severity and asserting the aggregator
    returns that stronger value (not the catalog static).

    This catches the regression class the seed loop alone cannot: a
    new ``dynamic_default=True`` catalog entry without a corresponding
    overlay branch in ``cli/scan.py:_dynamic_check_defaults`` would
    return only the catalog static and the resolver would never see
    the stronger manifest-effective default, silently widening the
    tier-crossing bypass.
    """
    from agents_shipgate.checks.registry import CHECK_METADATA
    from agents_shipgate.cli.scan import _dynamic_check_defaults
    from agents_shipgate.config.schema import AgentsShipgateManifest
    from agents_shipgate.inputs.policy_packs import LoadedPolicyPacks

    overlay, expected_dynamic = _DYNAMIC_DEFAULT_OVERLAY_CASES[check_id]
    manifest_dict = {**_base_manifest_dict(), **overlay}
    manifest = AgentsShipgateManifest.model_validate(manifest_dict)
    catalog = list(CHECK_METADATA)
    catalog_default = next(e.default_severity for e in catalog if e.id == check_id)
    assert catalog_default != expected_dynamic, (
        f"test rigged wrong: expected_dynamic={expected_dynamic!r} matches "
        f"the catalog static default for {check_id}; the overlay must be "
        "STRONGER than the catalog so the test can observe it firing."
    )

    aggregated = _dynamic_check_defaults(
        manifest,
        LoadedPolicyPacks(loaded=[], rules=[], warnings=[]),
        catalog=catalog,
    )

    assert aggregated.get(check_id) == expected_dynamic, (
        f"For {check_id}, expected dynamic default {expected_dynamic!r} after "
        f"populating the manifest section, but _dynamic_check_defaults "
        f"returned {aggregated.get(check_id)!r}. The aggregator likely "
        f"lacks an overlay branch — the seed loop alone returns the catalog "
        f"static ({catalog_default!r}). Add a branch in "
        f"cli/scan.py:_dynamic_check_defaults that reads the relevant "
        f"manifest section for {check_id}."
    )


def test_action_policy_violation_has_floor_and_dynamic_default() -> None:
    """Direct catalog assertion: pins the design choice so it cannot
    be silently weakened in a future PR. SHIP-ACTION-POLICY-VIOLATION
    is the canonical v0.17 dynamic-severity check; downgrading
    ``floor_severity`` or unsetting ``dynamic_default`` here re-opens
    the M1 bypass class.
    """
    from agents_shipgate.checks.registry import CHECK_METADATA

    entry = next(
        e for e in CHECK_METADATA if e.id == "SHIP-ACTION-POLICY-VIOLATION"
    )
    assert entry.dynamic_default is True
    assert entry.floor_severity == "medium"


def test_dynamic_default_missing_extra_raises_runtime_error() -> None:
    """The resolver's internal-consistency guard fires when a catalog
    check carrying ``dynamic_default=True`` does NOT appear in the
    ``extra_known_check_defaults`` dict the resolver receives.

    On the normal scan path this is unreachable (the aggregator seeds
    every such check). The guard exists for the case where the
    aggregator regresses — the test exercises it directly by
    constructing a synthetic catalog with a dynamic-default check and
    passing an empty ``extra_known_check_defaults``.
    """
    catalog = [
        CheckMetadata(
            id="SHIP-X-DYNAMIC",
            category="x",
            default_severity="high",
            description="dynamic test",
            dynamic_default=True,
            floor_severity="medium",
        )
    ]
    overrides = {
        "SHIP-X-DYNAMIC": SeverityOverrideEntry(severity="high"),
    }
    with pytest.raises(RuntimeError, match=r"missing from extra_known_check_defaults"):
        resolve_severity_overrides(
            overrides=overrides,
            acknowledgements=[],
            catalog=catalog,
            extra_known_check_defaults={},
        )


def test_aggregator_handles_override_without_manifest_section() -> None:
    """v0.18 (PR #1) regression for the P1 false-positive scenario.

    A user manifest can override ``SHIP-ACTION-POLICY-VIOLATION``
    (a ``dynamic_default=True`` catalog check) without declaring
    ``action_surface.policies[]``. The resolver MUST NOT raise
    ``RuntimeError`` in this case — that's the bug the seed step of
    ``_dynamic_check_defaults`` exists to prevent. The override should
    apply cleanly against the catalog static default, and the audit row
    should report the catalog default (no dynamic strengthening).

    Note: ``high → medium`` is tier-crossing (high → normal), so the
    manifest declares an ``acknowledge_overrides`` entry. The point of
    this test is that the v0.18 ``RuntimeError`` guard does NOT fire on
    legitimate user input that lacks ``action_surface.policies[]`` — not
    that all overrides apply without ack.
    """
    from agents_shipgate.checks.registry import CHECK_METADATA
    from agents_shipgate.cli.scan import _dynamic_check_defaults
    from agents_shipgate.config.schema import AgentsShipgateManifest
    from agents_shipgate.inputs.policy_packs import LoadedPolicyPacks

    # Manifest overrides SHIP-ACTION-POLICY-VIOLATION (default high) to
    # medium with a tier-crossing acknowledgement. No
    # action_surface.policies declared — this is the P1 scenario.
    manifest = AgentsShipgateManifest.model_validate(
        {
            "version": "0.1",
            "project": {"name": "p"},
            "agent": {"name": "a", "declared_purpose": ["t"]},
            "environment": {"target": "local"},
            "tool_sources": [
                {"id": "mcp", "type": "mcp", "path": "tools.json"}
            ],
            "checks": {
                "severity_overrides": {
                    "SHIP-ACTION-POLICY-VIOLATION": {"severity": "medium"},
                },
                "acknowledge_overrides": [
                    {
                        "check_id": "SHIP-ACTION-POLICY-VIOLATION",
                        "reason": "P1 regression fixture — verifies the seed loop",
                    },
                ],
            },
        }
    )

    catalog = list(CHECK_METADATA)
    effective = _dynamic_check_defaults(
        manifest,
        LoadedPolicyPacks(loaded=[], rules=[], warnings=[]),
        catalog=catalog,
    )

    # Seed step in _dynamic_check_defaults included the entry; no
    # overlay because no policies declared. Effective default ==
    # catalog static (no dynamic strengthening).
    assert (
        effective["SHIP-ACTION-POLICY-VIOLATION"] == "high"
    ), "seed loop must place catalog default when no overlay applies"

    # Resolver must NOT raise RuntimeError — this is the P1 regression:
    # pre-fix, the guard fired here because the action-only overlay
    # branch was the sole source of the entry, so the dict was sparse
    # for any manifest that didn't declare action_surface.policies.
    resolution = resolve_severity_overrides(
        overrides=manifest.severity_override_entries(),
        acknowledgements=manifest.acknowledge_overrides(),
        catalog=catalog,
        extra_known_check_defaults=effective,
    )
    audit_row = next(
        row
        for row in resolution.audit.severity_overrides_applied
        if row.check_id == "SHIP-ACTION-POLICY-VIOLATION"
    )
    # Catalog static default (not the manifest-declared dynamic value
    # — no action_surface.policies present means no dynamic
    # strengthening).
    assert audit_row.default_severity == "high"
    assert audit_row.applied_severity == "medium"
    assert audit_row.direction == "downgrade"
    assert audit_row.tier_crossed is True
