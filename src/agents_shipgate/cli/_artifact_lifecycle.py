from __future__ import annotations

from pathlib import Path

from agents_shipgate.core.errors import AgentsShipgateError

# These files carry an actionable verifier route or the content-addressed
# identity that supports it.  They must move as one lifecycle set: retaining
# even one beside a report from a later standalone scan can make the older
# verification run look current.
VERIFIER_ROUTE_ARTIFACT_NAMES = (
    "verifier.json",
    "agent-handoff.json",
    "pr-comment.md",
    "verify-run.json",
    "verification-plan.json",
    "verification-input.diff",
    "verification-base-report.json",
    "verification-unit-result.json",
    "verification-artifacts.json",
    "verification-receipt.json",
    "human-authorization.json",
    # Identity-bearing: it names the ``input_set_id`` of the run that
    # produced it, so one left beside a later run's receipt would offer a
    # chain into a verification that is no longer current.
    "capability-delta-attestation.json",
)


class ArtifactLifecycleError(AgentsShipgateError):
    """A stale verifier artifact could not be removed safely."""

    def __init__(self, path: Path, cause: OSError) -> None:
        self.path = path
        super().__init__(f"Could not remove stale verifier artifact {path}: {cause}")


def clear_verifier_route_artifacts(out_dir: Path) -> None:
    """Remove stale verifier route/identity artifacts from ``out_dir``.

    Fail closed when a present artifact cannot be removed.  Writing a new
    report while an older handoff or receipt remains would present
    contradictory runs as one current artifact set.
    """

    for name in VERIFIER_ROUTE_ARTIFACT_NAMES:
        path = out_dir / name
        if not (path.is_file() or path.is_symlink()):
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            # Another lifecycle cleanup won the race; the invariant already
            # holds, so the caller can continue.
            continue
        except OSError as exc:
            raise ArtifactLifecycleError(path, exc) from exc
