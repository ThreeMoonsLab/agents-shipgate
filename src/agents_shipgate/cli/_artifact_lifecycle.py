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

# Every name a Shipgate run writes directly into a reports directory, whichever
# command wrote it: the pointer, the verifier route above, the scan and packet
# renderers and the skeletons they suggest beside a report, the capability-lock
# artifacts, the files the GitHub Action writes into its ``output_dir``, and
# the artifacts the published contract (``schemas.contract.ARTIFACTS``) places
# beside them. An output directory whose uncommitted content is only these can
# be left out of the change set (#804), except where a path also names a trust
# root: a name alone cannot tell a generated ``packet.md`` from a slash command
# at ``.claude/commands/packet.md``, so the classifier never grants a trust-root
# path this allowance. Spelled here, not derived, so this leaf loads no schema;
# ``tests/test_output_directory_content.py`` holds it to every registry it
# restates, and to the names real runs actually leave behind.
REPORTS_DIRECTORY_ARTIFACT_NAMES: frozenset[str] = frozenset(
    {
        *VERIFIER_ROUTE_ARTIFACT_NAMES,
        "current-control.json",
        "report.md",
        "report.json",
        "report.sarif",
        "packet.md",
        "packet.json",
        "packet.html",
        "packet.pdf",
        "capabilities.lock.json",
        "base.capabilities.lock.json",
        "capability-lock-diff.json",
        "capability-lock-diff.md",
        "human-review-request.json",
        "human-authorization-request.json",
        "suggested-declarations.yaml",
        # `scan`/`verify` beside a report whose sources static extraction could
        # not enumerate (`ci.release_decision.SUGGESTED_INVENTORY_FILENAME`).
        "suggested-inventory.json",
        # `scenario suggest`'s default, beside the report it reads.
        "suggested-scenarios.yaml",
        # The GitHub Action's annotation and check-run payloads.
        "check-annotations.json",
        "check-run-payload.json",
        "declaration-continuation.json",
        "attestation.json",
        "host-grants.json",
        "org-status.json",
        "org-evidence-bundle.json",
        # `skill lint`, `skill security` and `skill review` write here too when
        # `--out` is omitted: `skill.runner.REPORT_BASENAME` in each format
        # `--format` accepts (markdown, json, sarif).
        *(
            f"{basename}.{suffix}"
            for basename in ("skill-lint", "skill-security", "skill-review")
            for suffix in ("md", "json", "sarif")
        ),
    }
)
# Directories a run creates beneath a reports directory; everything inside one
# is that run's own copy of a captured input.
REPORTS_DIRECTORY_ARTIFACT_SUBDIRECTORIES: frozenset[str] = frozenset(
    {"verification-inputs"}
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
