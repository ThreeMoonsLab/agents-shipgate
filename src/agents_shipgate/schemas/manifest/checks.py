from __future__ import annotations

from datetime import date
from typing import Any, get_args

from pydantic import BaseModel, Field, field_validator, model_validator

from agents_shipgate.schemas.common import Severity
from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG
from agents_shipgate.schemas.manifest.policy_packs import (
    PolicyPackConfig,
    _parse_policy_pack_entries,
)
from agents_shipgate.schemas.manifest.severity_overrides import (
    OverrideAcknowledgement,
    SeverityOverrideEntry,
)


class SuppressionConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    check_id: str
    tool: str | None = None
    tool_id: str | None = None
    provider: str | None = None
    source_type: str | None = None
    source_id: str | None = None
    owner: str | None = None
    reason: str
    expires: date | None = None

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("suppression reason is required")
        return value.strip()

    @field_validator("owner")
    @classmethod
    def _owner_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class ChecksConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    ignore: list[SuppressionConfig] = Field(default_factory=list)
    policy_packs: list[PolicyPackConfig] = Field(default_factory=list)
    # v0.17 (M1): rich shape accepts either ``Severity`` scalar (legacy)
    # or ``SeverityOverrideEntry`` (preferred). The validator coerces
    # scalars so every entry is a ``SeverityOverrideEntry`` after load —
    # downstream code never sees the raw scalar form.
    severity_overrides: dict[str, SeverityOverrideEntry] = Field(
        default_factory=dict
    )
    # v0.17 (M1): explicit per-check acknowledgement of tier-crossing
    # severity downgrades. Empty by default. The loader cross-checks
    # this list against the resolved overrides and raises ``ConfigError``
    # (exit 2) for missing acks or expired entries.
    acknowledge_overrides: list[OverrideAcknowledgement] = Field(
        default_factory=list
    )

    @field_validator("policy_packs", mode="before")
    @classmethod
    def parse_policy_packs(cls, value: Any) -> list[PolicyPackConfig]:
        return _parse_policy_pack_entries(value)

    @field_validator("severity_overrides", mode="before")
    @classmethod
    def _coerce_severity_overrides(
        cls, value: Any
    ) -> dict[str, SeverityOverrideEntry]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise TypeError("checks.severity_overrides must be a mapping")
        valid_severities = set(get_args(Severity))
        coerced: dict[str, SeverityOverrideEntry] = {}
        for check_id, raw in value.items():
            if isinstance(raw, SeverityOverrideEntry):
                coerced[check_id] = raw
                continue
            if isinstance(raw, str):
                if raw not in valid_severities:
                    raise ValueError(
                        f"severity_overrides[{check_id!r}]: "
                        f"{raw!r} is not a valid severity "
                        f"(expected one of {sorted(valid_severities)})"
                    )
                coerced[check_id] = SeverityOverrideEntry(
                    severity=raw  # type: ignore[arg-type]
                )
                continue
            if isinstance(raw, dict):
                coerced[check_id] = SeverityOverrideEntry.model_validate(raw)
                continue
            raise TypeError(
                f"severity_overrides[{check_id!r}] must be a severity "
                f"string or a mapping; got {type(raw).__name__}"
            )
        return coerced

    @model_validator(mode="after")
    def _ack_check_ids_unique(self) -> ChecksConfig:
        # Catch duplicate acknowledgements early so audit accounting is
        # unambiguous. A check_id appearing twice would create surprising
        # "latest entry wins" behavior.
        seen: set[str] = set()
        for ack in self.acknowledge_overrides:
            if ack.check_id in seen:
                raise ValueError(
                    f"acknowledge_overrides contains duplicate entry for "
                    f"check_id={ack.check_id!r}"
                )
            seen.add(ack.check_id)
        return self
