from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

VerifierBaseStatus = Literal[
    "not_requested",
    "skipped",
    "diff_from_provided",
    "ref_missing",
    "archive_failed",
    "missing_manifest",
    "scan_failed",
    "cache_hit",
    "succeeded",
]
VerifierHeadStatus = Literal["skipped", "succeeded", "failed"]


class VerifierArtifact(BaseModel):
    """Machine-readable artifact emitted by ``agents-shipgate verify``.

    This is an orchestration record only. The release gate remains
    ``report.json.release_decision.decision`` from the head scan.
    """

    model_config = ConfigDict(extra="forbid")

    verifier_schema_version: Literal["0.1"] = "0.1"
    workspace: str
    config: str
    base_ref: str | None = None
    head_ref: str = "HEAD"
    changed_files: list[str] = Field(default_factory=list)
    diff_text_available: bool = False
    trigger: dict[str, Any] = Field(default_factory=dict)
    base_status: VerifierBaseStatus = "not_requested"
    base_tree_sha: str | None = None
    base_report_json: str | None = None
    base_notes: list[str] = Field(default_factory=list)
    head_status: VerifierHeadStatus = "skipped"
    head_report_json: str | None = None
    head_exit_code: int = 0
    release_decision: dict[str, Any] | None = None
    agent_summary: dict[str, Any] | None = None
    reviewer_summary: dict[str, Any] | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)


__all__ = [
    "VerifierArtifact",
    "VerifierBaseStatus",
    "VerifierHeadStatus",
]
