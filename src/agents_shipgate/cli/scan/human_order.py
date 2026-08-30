"""Derive the ephemeral context used to order human scan artifacts."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from agents_shipgate.core.agent_controls import git_root_for
from agents_shipgate.report.human_order import HumanArtifactContext
from agents_shipgate.schemas.verification import VerificationContext

_MANIFEST_COMMITTED_OVERRIDE: ContextVar[bool | None] = ContextVar(
    "agents_shipgate_human_manifest_committed",
    default=None,
)


def human_artifact_context(
    config_path: Path,
    verification: VerificationContext | None,
) -> HumanArtifactContext:
    """Facts already held by the scan/verify engine, never serialized.

    ``manifest_introduced`` is the verifier's stronger, comparison-relative
    proof.  The HEAD lookup covers a plain scan of a new local manifest.  A
    Git error remains unknown and therefore keeps verdict-first ordering; a
    path outside a checkout or a checkout with no HEAD cannot carry a
    committed repository trust root and is cold by construction.
    """

    committed_override = _MANIFEST_COMMITTED_OVERRIDE.get()
    return HumanArtifactContext(
        manifest_committed=(
            committed_override
            if committed_override is not None
            else _manifest_committed_at_head(config_path)
        ),
        manifest_introduced=bool(verification is not None and verification.manifest_introduced),
    )


@contextmanager
def override_human_manifest_committed(value: bool | None) -> Iterator[None]:
    """Thread/task-local provenance for a scan of an extracted commit tree."""

    token = _MANIFEST_COMMITTED_OVERRIDE.set(value)
    try:
        yield
    finally:
        _MANIFEST_COMMITTED_OVERRIDE.reset(token)


def _manifest_committed_at_head(config_path: Path) -> bool | None:
    root = git_root_for(config_path.parent)
    if root is None:
        return False
    try:
        relative = config_path.resolve().relative_to(root)
    except (OSError, ValueError):
        return False

    # Local import avoids the ``cli.verify.__init__`` -> orchestrator -> scan
    # cycle during module initialization.  More importantly, both probes go
    # through verify's single audited, bounded, no-shell Git boundary rather
    # than introducing a second process-execution surface in scan code.
    from agents_shipgate.cli.verify.git import path_committed_at_head

    return path_committed_at_head(root, relative)
