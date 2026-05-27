from __future__ import annotations

from datetime import date

from pydantic import BaseModel, field_validator

from agents_shipgate.schemas.common import Severity
from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG


class SeverityOverrideEntry(BaseModel):
    """Rich form of a ``checks.severity_overrides`` entry (v0.17 / M1).

    The legacy scalar form (``SHIP-XYZ: medium``) continues to work via the
    ``ChecksConfig.severity_overrides`` validator below; the rich form is
    additive and lets reviewers attach a reason and an expiry to any
    individual override.

    Example::

        checks:
          severity_overrides:
            # Legacy scalar — still supported
            SHIP-SCHEMA-MISSING-BOUNDS: medium
            # Rich form — preferred for tier-crossing overrides because
            # ``reason`` is the only thing that lands in the audit row.
            SHIP-AUTH-MANIFEST-BROAD-SCOPE:
              severity: medium
              reason: "internal-network agent; scope reviewed JIRA-1234"
              expires: 2026-09-01
    """

    model_config = STRICT_MODEL_CONFIG

    severity: Severity
    reason: str | None = None
    # Optional ISO-8601 date. When present, the manifest loader rejects
    # the manifest after the date with a structured ``override_ack_expired``
    # config error. Allows time-bounded acceptance of weakened severity.
    expires: date | None = None

    @field_validator("reason")
    @classmethod
    def _strip_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class OverrideAcknowledgement(BaseModel):
    """One entry in ``checks.acknowledge_overrides`` (v0.17 / M1).

    Required for any ``severity_overrides`` entry whose application
    crosses a severity tier boundary (critical / high / medium-low) as a
    downgrade. Tier-crossing upgrades never require ack (strictly more
    conservative). Below-floor downgrades are rejected outright; ack does
    not bypass the floor.
    """

    model_config = STRICT_MODEL_CONFIG

    check_id: str
    reason: str
    expires: date | None = None

    @field_validator("reason")
    @classmethod
    def _require_non_empty_reason(cls, value: str) -> str:
        stripped = (value or "").strip()
        if not stripped:
            raise ValueError("acknowledge_overrides reason must be non-empty")
        return stripped
