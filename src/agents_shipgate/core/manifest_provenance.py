"""Manifest provenance facts shared by setup and verification.

The local-review filename is deliberately reserved.  A manifest at this path
is an ephemeral assessment input even if somebody force-adds it to Git: moving
the file to the durable ``shipgate.yaml`` path is an explicit adoption step,
and verification of the reserved path must never make release-authoritative
claims.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

LOCAL_REVIEW_MANIFEST_NAME = ".agents-shipgate-local-review.yaml"

ManifestProvenance = Literal["repository", "local_review", "uncommitted", "unknown"]


def manifest_provenance(path: Path, *, committed_at_head: bool | None = True) -> ManifestProvenance:
    """Classify a manifest without trusting declarations inside that manifest.

    The reserved local-review name always wins, including when force-added to
    Git. Other paths are repository roots only when Git proves that the path
    exists at HEAD; an untracked custom manifest must not become authoritative
    merely because its filename is different.
    """

    if path.name == LOCAL_REVIEW_MANIFEST_NAME:
        return "local_review"
    if committed_at_head is True:
        return "repository"
    if committed_at_head is False:
        return "uncommitted"
    return "unknown"


def is_local_review_manifest(path: Path) -> bool:
    return manifest_provenance(path) == "local_review"


LOCAL_REVIEW_PROVISIONAL_NOTE = (
    "Manifest provenance: local_review (ephemeral and uncommitted by design). "
    "Conclusions are provisional static-assessment output; a committed, "
    "human-reviewed repository trust root is required for release authority."
)


def provisional_manifest_note(provenance: ManifestProvenance) -> str | None:
    if provenance == "repository":
        return None
    if provenance == "local_review":
        return LOCAL_REVIEW_PROVISIONAL_NOTE
    return (
        f"Manifest provenance: {provenance}. Conclusions are provisional "
        "static-assessment output; Git must prove a committed, human-reviewed "
        "repository trust root before this run can carry release authority."
    )


__all__ = [
    "LOCAL_REVIEW_MANIFEST_NAME",
    "LOCAL_REVIEW_PROVISIONAL_NOTE",
    "ManifestProvenance",
    "is_local_review_manifest",
    "manifest_provenance",
    "provisional_manifest_note",
]
