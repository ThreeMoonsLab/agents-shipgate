from __future__ import annotations

from pathlib import PurePosixPath

from pydantic import BaseModel, Field, field_validator, model_validator

from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG


class OrganizationTeamConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    reviewers: list[str] = Field(default_factory=list)

    @field_validator("reviewers")
    @classmethod
    def _reviewers_non_empty(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for reviewer in value:
            normalized = reviewer.strip()
            if not normalized:
                raise ValueError("organization team reviewers must be non-empty strings")
            out.append(normalized)
        return out


class OrganizationExceptionPolicyConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    require_owner: bool = False
    require_reason: bool = False
    require_expiry: bool = False
    max_age_days: int | None = None

    @field_validator("max_age_days")
    @classmethod
    def _positive_max_age(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("organization.exception_policy.max_age_days must be positive")
        return value


class OrganizationAuditConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    registry: str | None = None

    @field_validator("registry")
    @classmethod
    def _registry_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("organization.audit.registry must be non-empty")
        # Keep this purely structural: no filesystem access and no containment
        # checks here. Commands resolve paths relative to their workspace.
        PurePosixPath(normalized.replace("\\", "/"))
        return normalized


class OrganizationConfig(BaseModel):
    """Optional org metadata for local, deterministic governance workflows."""

    model_config = STRICT_MODEL_CONFIG

    id: str
    repo: str | None = None
    service: str | None = None
    tier: str | None = None
    teams: dict[str, OrganizationTeamConfig] = Field(default_factory=dict)
    exception_policy: OrganizationExceptionPolicyConfig = Field(
        default_factory=OrganizationExceptionPolicyConfig
    )
    audit: OrganizationAuditConfig = Field(default_factory=OrganizationAuditConfig)

    @field_validator("id")
    @classmethod
    def _id_non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("organization.id must be non-empty")
        return normalized

    @field_validator("repo", "service", "tier")
    @classmethod
    def _optional_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("organization string fields must be non-empty when set")
        return normalized

    @model_validator(mode="after")
    def _team_names_non_empty(self) -> OrganizationConfig:
        normalized: dict[str, OrganizationTeamConfig] = {}
        for name, team in self.teams.items():
            clean = name.strip()
            if not clean:
                raise ValueError("organization.teams keys must be non-empty")
            if clean in normalized:
                raise ValueError(f"duplicate organization team name after trimming: {clean!r}")
            normalized[clean] = team
        self.teams = normalized
        return self


__all__ = [
    "OrganizationAuditConfig",
    "OrganizationConfig",
    "OrganizationExceptionPolicyConfig",
    "OrganizationTeamConfig",
]
