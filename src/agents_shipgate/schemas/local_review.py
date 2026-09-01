"""Typed provenance and binding metadata for provisional local reviews.

The binding is deliberately stored outside the evaluated worktree.  A
workspace file cannot opt itself into local-review semantics merely by naming
itself like the generated manifest, and a local-review manifest can never
claim release authority merely by being schema-valid.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agents_shipgate.schemas.content_identity import (
    CONTENT_ID_PATTERN,
    content_id,
    validate_portable_path,
)
from agents_shipgate.schemas.manifest_provenance import ManifestProvenance

LOCAL_REVIEW_BINDING_SCHEMA_VERSION = "shipgate.local_review_binding/v1"


class LocalReviewBinding(BaseModel):
    """Content-addressed binding from one worktree to its ephemeral manifest."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["shipgate.local_review_binding/v1"] = (
        LOCAL_REVIEW_BINDING_SCHEMA_VERSION
    )
    binding_id: str = Field(pattern=CONTENT_ID_PATTERN)
    workspace: str
    repository_root: str
    manifest_path: str
    reports_path: str
    manifest_sha256: str = Field(pattern=CONTENT_ID_PATTERN)
    exclude_file: str
    exclude_file_preexisting: bool
    exclude_separator: Literal["", "\n", "\n\n"]
    exclude_patterns: list[str] = Field(min_length=2, max_length=2)
    reports_directory_device: int = Field(ge=0)
    reports_directory_inode: int = Field(ge=1)
    reports_owner_id: str = Field(pattern=CONTENT_ID_PATTERN)

    _manifest_path_is_portable = field_validator("manifest_path")(validate_portable_path)
    _reports_path_is_portable = field_validator("reports_path")(validate_portable_path)

    @field_validator("exclude_patterns")
    @classmethod
    def _exclude_patterns_are_exact(cls, value: list[str]) -> list[str]:
        if value != sorted(set(value)):
            raise ValueError("local-review exclude patterns must be sorted and unique")
        if any(not pattern.startswith("/") or ".." in pattern.split("/") for pattern in value):
            raise ValueError("local-review exclude patterns must be anchored repository paths")
        return value

    @model_validator(mode="after")
    def _binding_id_is_content_addressed(self) -> LocalReviewBinding:
        payload = self.model_dump(
            mode="json",
            exclude={"schema_version", "binding_id"},
            exclude_none=False,
        )
        if self.binding_id != content_id(payload):
            raise ValueError("binding_id must hash the complete local-review binding")
        return self

    @property
    def provenance(self) -> ManifestProvenance:
        return ManifestProvenance.local_review(self.binding_id)


__all__ = [
    "LOCAL_REVIEW_BINDING_SCHEMA_VERSION",
    "LocalReviewBinding",
    "ManifestProvenance",
]
