"""Verification context — the optional diff-aware input for verify checks.

``scan`` reads ``shipgate.yaml`` plus declared local sources; it does not
know which paths a PR changed. Checks that reason about "this PR touched
a trust root" need that diff context separately. ``VerificationContext``
carries it.

Rules (see docs/engineering/ai-coding-workflow-verifier.md §2.5, §4.1):

- It is input metadata, NOT a release verdict.
- It may cause checks to emit ordinary ``Finding``s.
- It must never bypass ``release_decision``.
- Absence (``ScanContext.verification is None``) means plain ``scan``
  behavior — verify checks emit nothing.

M1 populates ``changed_files`` only; ``verify`` (P1) will add base/head
orchestration fields additively.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class VerificationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changed_files: list[str] = Field(default_factory=list)
    diff_text: str | None = None
    diff_text_available: bool = False
    trigger_result: dict[str, Any] = Field(default_factory=dict)
    # Stable repository-relative identity of the manifest the verifier loaded.
    # A committed-head scan reads the physical file from a temporary archive,
    # so ``ScanContext.config_path`` alone cannot be compared with repository-
    # relative changed paths without leaking or depending on that temp path.
    configured_manifest_path: str | None = None
    # True only when the comparison base carries no Shipgate manifest at all —
    # this diff *introduces* the gate rather than modifying one. Checks that
    # fail safe on a missing base use it to say so honestly; it never relaxes a
    # verdict. The orchestrator proves it from git (see
    # ``cli/verify/orchestrator._manifest_introduced``), so a renamed manifest
    # cannot pass itself off as a first adoption.
    manifest_introduced: bool = False
