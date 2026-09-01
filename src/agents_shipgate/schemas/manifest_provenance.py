"""Authority provenance for the manifest a verification run consumed."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate.schemas.content_identity import CONTENT_ID_PATTERN


class ManifestProvenance(BaseModel):
    """Whether a manifest can participate in release-authoritative evidence."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "oneOf": [
                {
                    "properties": {
                        "kind": {"const": "repository"},
                        "ephemeral": {"const": False},
                        "release_authoritative": {"const": True},
                        "binding_id": {"type": "null"},
                    },
                    "required": [
                        "kind",
                        "ephemeral",
                        "release_authoritative",
                        "binding_id",
                    ],
                },
                {
                    "properties": {
                        "kind": {"const": "local_review"},
                        "ephemeral": {"const": True},
                        "release_authoritative": {"const": False},
                        "binding_id": {
                            "type": "string",
                            "pattern": CONTENT_ID_PATTERN,
                        },
                    },
                    "required": [
                        "kind",
                        "ephemeral",
                        "release_authoritative",
                        "binding_id",
                    ],
                },
                {
                    "properties": {
                        "kind": {"const": "not_evaluated"},
                        "ephemeral": {"const": False},
                        "release_authoritative": {"const": False},
                        "binding_id": {"type": "null"},
                    },
                    "required": [
                        "kind",
                        "ephemeral",
                        "release_authoritative",
                        "binding_id",
                    ],
                },
            ]
        },
    )

    kind: Literal["repository", "local_review", "not_evaluated"]
    ephemeral: bool
    release_authoritative: bool
    binding_id: str | None = Field(pattern=CONTENT_ID_PATTERN)

    @classmethod
    def repository(cls) -> ManifestProvenance:
        return cls(
            kind="repository",
            ephemeral=False,
            release_authoritative=True,
            binding_id=None,
        )

    @classmethod
    def local_review(cls, binding_id: str) -> ManifestProvenance:
        return cls(
            kind="local_review",
            ephemeral=True,
            release_authoritative=False,
            binding_id=binding_id,
        )

    @classmethod
    def not_evaluated(cls) -> ManifestProvenance:
        return cls(
            kind="not_evaluated",
            ephemeral=False,
            release_authoritative=False,
            binding_id=None,
        )

    @model_validator(mode="after")
    def _kind_carries_one_authority_shape(self) -> ManifestProvenance:
        expected = {
            "repository": (False, True, False),
            "local_review": (True, False, True),
            "not_evaluated": (False, False, False),
        }[self.kind]
        observed = (
            self.ephemeral,
            self.release_authoritative,
            self.binding_id is not None,
        )
        if observed != expected:
            raise ValueError("manifest provenance contradicts its kind")
        return self


__all__ = ["ManifestProvenance"]
