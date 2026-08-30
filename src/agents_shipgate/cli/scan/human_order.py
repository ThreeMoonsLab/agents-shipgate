"""Derive the ephemeral context used to order human scan artifacts."""

from __future__ import annotations

import os
import subprocess
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
    env = dict(os.environ)
    env.update({"GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0"})
    try:
        head = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(root),
                "rev-parse",
                "--verify",
                "--quiet",
                "HEAD^{commit}",
            ],
            capture_output=True,
            check=False,
            env=env,
            timeout=60,
        )
        if head.returncode == 1:
            return False
        if head.returncode != 0:
            return None
        result = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(root),
                "ls-tree",
                "-z",
                "--full-tree",
                "HEAD",
                "--",
                relative.as_posix(),
            ],
            capture_output=True,
            check=False,
            env=env,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    records = [record for record in result.stdout.split(b"\0") if record]
    return any(record.partition(b"\t")[0].split()[1:2] == [b"blob"] for record in records)
