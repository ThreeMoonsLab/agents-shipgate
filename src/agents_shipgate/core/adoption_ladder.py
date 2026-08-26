"""Which rung of the adoption ladder this repository is standing on (#410 §G).

Adoption stalls because the distance between "I installed it" and "it gates my
pull requests" is unlabelled. Every intermediate state reads like a failure:
a manifest with no declarations reports ``insufficient_evidence``, which is
accurate and sounds like something is broken. The ladder gives each state a
name, says what it is worth on its own, and names the one thing that moves it
up — so an adopter always knows both where they are and what "further" means.

Four rungs, and the property that keeps them honest is that **each one is
useful without the next**:

* **0 · Audit** needs no manifest at all. What is in a repository is worth
  knowing before deciding whether to gate it, and both repositories the
  adoption walks used were samples someone was evaluating rather than shipping.
* **1 · Gate the delta** is the first verdict: a manifest exists, so a pull
  request gets an answer about what it changed.
* **2 · Answer on touch** is the working state. Questions arrive with the pull
  requests that raise them, the backlog shrinks, and the counter says by how
  much.
* **3 · Strict** is the enforced gate: CI fails on a bad verdict, and the
  manifest that decides it cannot change without a named human's review.

Derived from the manifest and the checkout alone — never from a scan. The rung
is the shape of an adoption, and it has to be answerable by ``doctor``, which
runs before any report exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agents_shipgate.core.manifest_protection import ManifestProtection
from agents_shipgate.schemas.manifest import AgentsShipgateManifest


@dataclass(frozen=True)
class AdoptionRung:
    """One rung, what it is worth, and the exact thing that leaves it."""

    number: int
    name: str
    #: What this rung gives an adopter who goes no further.
    you_get: str
    #: What to do to reach the next rung. Empty at the top.
    exit_criterion: str
    #: The specific unmet conditions behind ``exit_criterion``, so a consumer
    #: can act on them one at a time instead of parsing a sentence.
    blocking: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> str:
        """One line: where you are, and what "further" means."""

        line = f"rung {self.number} · {self.name} — {self.you_get}"
        return f"{line} Next: {self.exit_criterion}" if self.exit_criterion else line


#: The rung a repository with no manifest is on. Named here rather than at the
#: one call site that can observe it, so the ladder is one list in one place.
AUDIT_RUNG = AdoptionRung(
    number=0,
    name="Audit",
    you_get=(
        "a read-only look with no manifest and nothing written: `shipgate "
        "detect` for the agent frameworks and tool sources present, `shipgate "
        "audit --host` for the host grants."
    ),
    exit_criterion=(
        "run `agents-shipgate init` to write a manifest, and the next pull "
        "request gets a verdict on what it changed."
    ),
    blocking=("manifest_absent",),
)


def adoption_rung(
    manifest: AgentsShipgateManifest,
    protection: ManifestProtection,
) -> AdoptionRung:
    """The highest rung whose conditions this repository already meets.

    Conditions are cumulative and each is a fact about the manifest or the
    checkout: a rung is never awarded for something a scan would have to prove,
    because the rung has to be answerable before the first scan runs.
    """

    strict = manifest.ci.mode == "strict"
    answered = _has_reviewed_declaration(manifest)

    if answered and strict and protection.covered:
        return AdoptionRung(
            number=3,
            name="Strict",
            you_get=(
                "an enforced gate: CI fails on a blocking verdict, and the "
                "manifest that decides it takes a named owner's review to "
                "change."
            ),
            exit_criterion="",
        )
    if answered:
        blocking = _blocking(strict=strict, protection=protection)
        return AdoptionRung(
            number=2,
            name="Answer on touch",
            you_get=(
                "per-pull-request verdicts and a shrinking question backlog; "
                "`scan` reports how many declaration questions remain."
            ),
            exit_criterion=_strict_exit_criterion(blocking),
            blocking=blocking,
        )
    return AdoptionRung(
        number=1,
        name="Gate the delta",
        you_get=(
            "a verdict on every pull request, covering what that pull request "
            "changed. Nothing already in the repository has to be declared "
            "first."
        ),
        exit_criterion=(
            "answer a declaration question — `suggested-declarations.yaml`, "
            "written beside the report, lists them in the order that moves the "
            "verdict fastest."
        ),
        blocking=("no_reviewed_declaration",),
    )


def _has_reviewed_declaration(manifest: AgentsShipgateManifest) -> bool:
    """Whether anyone has answered a declaration question in this manifest.

    ``environment.target: template`` counts, and counts as one answer for the
    whole repository: it is a reviewed statement that nothing here holds a
    credential, which is exactly the authority question every action would
    otherwise be asked (#410 §G).
    """

    return bool(
        manifest.action_surface.actions
        or any(source.authority is not None for source in manifest.tool_sources)
        or manifest.environment.target == "template"
    )


def _blocking(*, strict: bool, protection: ManifestProtection) -> tuple[str, ...]:
    reasons: list[str] = []
    if not strict:
        reasons.append("ci_mode_not_strict")
    if not protection.covered:
        reasons.append("manifest_unprotected")
    return tuple(reasons)


def _strict_exit_criterion(blocking: tuple[str, ...]) -> str:
    """Name only the conditions that are actually unmet.

    A fixed sentence listing both would tell an adopter who already set
    `ci.mode: strict` to set it again, which is how a next step stops being
    read at all.
    """

    steps: list[str] = []
    if "ci_mode_not_strict" in blocking:
        steps.append("set ci.mode: strict")
    if "manifest_unprotected" in blocking:
        steps.append(
            "add a CODEOWNERS rule covering the manifest so changing the gate "
            "takes a named owner's review"
        )
    if not steps:  # pragma: no cover - rung 3 is returned before this is reached
        return ""
    return (
        f"clear the remaining declaration questions, then {' and '.join(steps)}."
    )


__all__ = ["AUDIT_RUNG", "AdoptionRung", "adoption_rung"]
