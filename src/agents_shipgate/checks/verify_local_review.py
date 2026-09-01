"""Fail-safe release routing for an ephemeral local-review manifest."""

from __future__ import annotations

from agents_shipgate.core.context import ScanContext
from agents_shipgate.schemas.common import SourceReference, parse_confidence, parse_severity
from agents_shipgate.schemas.report import Finding

CHECK_ID = "SHIP-VERIFY-LOCAL-REVIEW-PROVISIONAL"


def run(context: ScanContext) -> list[Finding]:
    verification = context.verification
    if verification is None or verification.manifest_provenance == "repository":
        return []
    manifest = verification.configured_manifest_path or str(context.config_path)
    provenance = verification.manifest_provenance
    title = (
        "Local-review manifest is provisional, not a release trust root"
        if provenance == "local_review"
        else "Configured manifest is not proven to be a committed release trust root"
    )
    return [
        Finding(
            check_id=CHECK_ID,
            title=title,
            severity=parse_severity("medium"),
            category="verify",
            agent_id=context.agent.id,
            evidence={
                "manifest": manifest,
                "manifest_provenance": provenance,
                "ephemeral": True,
                "release_authoritative": False,
            },
            confidence=parse_confidence("high"),
            provenance_kind="static_declaration",
            source=SourceReference(type="manifest", path=manifest),
            recommendation=(
                "Use this run only as provisional static assessment. For "
                "release authority, adopt shipgate.yaml in the repository "
                "through init --write and have a human review the committed "
                "trust root."
            ),
        )
    ]


__all__ = ["CHECK_ID", "run"]
