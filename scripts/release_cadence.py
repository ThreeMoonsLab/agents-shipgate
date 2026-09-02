#!/usr/bin/env python3
"""Report how long shipped work has been waiting for a tag.

Issue #491 measured the failure this exists to make visible: 81% of
``CHANGELOG.md`` had never reached a user, because releases were gated on a
single expensive artifact rather than on a schedule. A cadence nobody counts is
a cadence nobody keeps, so the interval is stated in
``docs/release-runbook.md`` and the number is printed where it will be read.

The metric is deliberately *not* a CI failure. A red check on an unrelated pull
request punishes the wrong change for a release the maintainer has not cut;
``--fail-when-overdue`` exists for an operator or a scheduled job that wants the
non-zero exit, and nothing in ``ci.yml`` passes it.

Standard library only, and no network. It reads the repository's own tags, so
it answers the same way on a laptop as it does on a runner -- and an operator
can run it before deciding whether to cut a release.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

if __package__:
    from scripts._release_support import is_release_version
else:  # ``python scripts/release_cadence.py``
    from _release_support import is_release_version

# The approved cadence, recorded in `docs/release-runbook.md` § Cadence. These
# two numbers are the policy; every surface below renders them rather than
# restating them.
INTERVAL_DAYS = 30
OVERDUE_DAYS = 45

_STATUS_NOTE = {
    "current": "within the {interval}-day interval",
    "due": "past the {interval}-day interval; cut a release or record why not",
    "overdue": "past {overdue} days; triage as a release defect (#491)",
    "unknown": "no release tag found; tags may not be fetched in this checkout",
}


@dataclass(frozen=True)
class Cadence:
    """The cadence answer, as one value every surface renders from."""

    latest_release_tag: str | None
    tagged_at: str | None
    days_since_release: int | None
    interval_days: int
    overdue_days: int
    status: str

    @property
    def note(self) -> str:
        return _STATUS_NOTE[self.status].format(
            interval=self.interval_days, overdue=self.overdue_days
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "latest_release_tag": self.latest_release_tag,
            "tagged_at": self.tagged_at,
            "days_since_release": self.days_since_release,
            "interval_days": self.interval_days,
            "overdue_days": self.overdue_days,
            "status": self.status,
            "note": self.note,
        }

    def as_line(self) -> str:
        if self.status == "unknown":
            return f"Release cadence: unknown -- {self.note}."
        return (
            f"Release cadence: {self.days_since_release} days since "
            f"{self.latest_release_tag} ({self.tagged_at}) -- {self.note}."
        )

    def as_markdown(self) -> str:
        """The step-summary block. One renderer, so no surface can disagree."""

        days = "unknown" if self.days_since_release is None else str(self.days_since_release)
        return "\n".join(
            [
                "## Release cadence",
                "",
                "| | |",
                "| --- | --- |",
                f"| Latest release tag | `{self.latest_release_tag or 'none'}` |",
                f"| Tagged | {self.tagged_at or 'n/a'} |",
                f"| Days since | **{days}** |",
                f"| Interval | {self.interval_days} days |",
                f"| Status | **{self.status}** -- {self.note} |",
                "",
            ]
        )


def is_release_tag(ref: str) -> bool:
    """True for ``v`` followed by a complete PEP 440 version.

    This is what separates a shipped artifact from the repository's other
    refs. ``wip-sectiond`` and ``m3-pre-rebase`` are not releases, and neither
    is a ``preview/*`` ref: the unqualified preview channel publishes outside
    the ``v*`` namespace precisely so that it cannot be read as one (see
    ``docs/release-evidence-policy-decision.md`` § Amendment 2), and counting a
    preview here would let the channel that exists *because* the cadence
    slipped report the cadence as kept.
    """

    return ref.startswith("v") and is_release_version(ref[1:])


def read_release_tags(repo: Path) -> list[tuple[str, int]]:
    """Every release tag in ``repo`` with its creation time, newest first."""

    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "for-each-ref", "--format=%(refname:strip=2)\t%(creatordate:unix)", "refs/tags"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    tags: list[tuple[str, int]] = []
    for line in result.stdout.splitlines():
        ref, _, when = line.partition("\t")
        if not is_release_tag(ref):
            continue
        try:
            tags.append((ref, int(when)))
        except ValueError:
            continue
    return sorted(tags, key=lambda item: item[1], reverse=True)


def assess(
    tags: list[tuple[str, int]],
    *,
    now: int,
    interval_days: int = INTERVAL_DAYS,
    overdue_days: int = OVERDUE_DAYS,
) -> Cadence:
    """Classify the newest release tag against the approved interval.

    ``tags`` is deliberately an argument rather than something this reads: the
    classification is the part worth testing, and it must not depend on the
    clock or on what happens to be tagged in the working checkout.
    """

    if not tags:
        return Cadence(None, None, None, interval_days, overdue_days, "unknown")
    ref, when = max(tags, key=lambda item: item[1])
    # Truncated, not rounded: "56 days since" must never report 57 because the
    # tag was cut in the afternoon.
    days = max(0, (now - when) // 86_400)
    if days > overdue_days:
        status = "overdue"
    elif days >= interval_days:
        status = "due"
    else:
        status = "current"
    tagged_at = datetime.fromtimestamp(when, tz=UTC).date().isoformat()
    return Cadence(ref, tagged_at, days, interval_days, overdue_days, status)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--json", action="store_true", help="emit the machine-readable form")
    parser.add_argument(
        "--github",
        action="store_true",
        help="append the summary block to $GITHUB_STEP_SUMMARY and warn when not current",
    )
    parser.add_argument(
        "--fail-when-overdue",
        action="store_true",
        help="exit non-zero past the overdue threshold (never set by ci.yml)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    cadence = assess(read_release_tags(args.repo), now=int(datetime.now(tz=UTC).timestamp()))

    if args.json:
        sys.stdout.write(json.dumps(cadence.as_dict(), indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(cadence.as_line() + "\n")

    if args.github:
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(cadence.as_markdown())
        if cadence.status != "current":
            # A warning annotation, never a failure: see the module docstring.
            sys.stdout.write(f"::warning title=Release cadence::{cadence.as_line()}\n")

    if args.fail_when_overdue and cadence.status == "overdue":
        sys.stderr.write(f"Release cadence defect: {cadence.as_line()}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
