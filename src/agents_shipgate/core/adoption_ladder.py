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
  request gets an answer.
* **2 · Answer on touch** is the working state. Questions arrive with the pull
  requests that raise them, and the backlog shrinks.
* **3 · Strict** is the strongest posture the manifest can state, with a named
  reviewer on the file that states it.

Each rung names **its own** conditions and the highest match wins; they are not
a cumulative chain. That is deliberate. A repository whose surface resolves
structurally may never be asked a declaration question at all, and a cumulative
ladder would strand it at rung 1 forever — unable to reach the rung that
describes the posture it actually has.

Derived from the manifest and the checkout alone — never from a scan. The rung
is the shape of an adoption, and it has to be answerable by ``doctor``, which
runs before any report exists. **That is also the limit on what a rung may
say.** A rung describes what this repository has *declared*. It cannot describe
what a verdict will be — that depends on evidence no manifest carries — and it
cannot describe what CI or a branch will *enforce*: the generated workflow
passes ``ci_mode: advisory``, which overrides the manifest, and branch
protection lives in repository settings no file here can read. Every over-claim
caught in review was a rung reaching past that line.
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


#: The rung a repository with no manifest is on. A function rather than a
#: constant because its next step is a *command*, and a command that drops the
#: caller's workspace — or that omits ``--write``, which makes ``init`` a dry
#: run that writes nothing — is a next step that cannot advance the rung it is
#: printed on.
def audit_rung(init_command: str) -> AdoptionRung:
    """Rung 0, with the exact invocation that leaves it.

    ``init_command`` is rendered by the caller through the invocation policy
    (#322), because only the caller knows how this process was entered and
    which workspace it was pointed at.
    """

    return AdoptionRung(
        number=0,
        name="Audit",
        you_get=(
            "a read-only look with no manifest and nothing written: `shipgate "
            "detect` for the agent frameworks and tool sources present, "
            "`shipgate audit --host` for the host grants."
        ),
        exit_criterion=(
            f"run `{init_command}` to write a manifest, and the next pull "
            "request gets a verdict."
        ),
        blocking=("manifest_absent",),
    )


def adoption_rung(
    manifest: AgentsShipgateManifest,
    protection: ManifestProtection,
) -> AdoptionRung:
    """The highest rung whose own conditions this repository already meets.

    Not cumulative — see the module docstring for why a structurally-resolved
    repository must be able to reach rung 3 without ever having been asked a
    declaration question.
    """

    if manifest.ci.mode == "strict" and protection.covered:
        return AdoptionRung(
            number=3,
            name="Strict",
            # Says what is *declared*, and then says what that does not
            # establish. `ci.mode: strict` is this repository's own statement;
            # the workflow that runs Shipgate passes its own `ci_mode`, and the
            # generated one ships `advisory` — so promising "CI fails on a
            # blocking verdict" here would promise something a manifest cannot
            # deliver and this module cannot check.
            you_get=(
                "the strongest posture the manifest can state — `ci.mode: "
                "strict`, so a run in this repository's own mode fails on a "
                "blocking verdict — and a CODEOWNERS rule naming who reviews a "
                "change to the manifest that decides it."
            ),
            exit_criterion=(
                "nothing here. What this cannot see: whether your CI workflow "
                "runs Shipgate with `ci_mode: strict` (the generated workflow "
                "ships `advisory`, which overrides the manifest), and whether "
                "the branch requires the review CODEOWNERS assigns. Confirm "
                "both where they live."
            ),
        )
    if _has_reviewed_declaration(manifest):
        blocking = _blocking(manifest=manifest, protection=protection)
        return AdoptionRung(
            number=2,
            name="Answer on touch",
            you_get=(
                "a verdict that can move: every answered question takes an "
                "action out of the backlog, and `scan` reports how many are "
                "left."
            ),
            exit_criterion=_strict_exit_criterion(blocking),
            blocking=blocking,
        )
    return AdoptionRung(
        number=1,
        name="Gate the delta",
        # Deliberately silent about the verdict. A fully structural tool
        # surface can owe no declaration questions at all and scan
        # `review_required` or `passed` from here, so naming
        # `insufficient_evidence` — or promising a `suggested-declarations.yaml`
        # that would have nothing to list — states a scan result from manifest
        # shape alone.
        you_get=(
            "the gate running on every pull request. `scan` reports what, if "
            "anything, this repository still owes before its verdict can be "
            "evidence-backed."
        ),
        exit_criterion=(
            "run `scan` and answer any declaration questions it reports — they "
            "are written out as `suggested-declarations.yaml` beside the "
            "report, highest-risk first."
        ),
        blocking=("no_reviewed_declaration",),
    )


def _has_reviewed_declaration(manifest: AgentsShipgateManifest) -> bool:
    """Whether anyone has answered a **declaration question** in this manifest.

    Not "is there an action row". The questionnaire counts exactly two
    dimensions — effect and authority — so a row carrying only ``approval`` or
    only ``safeguards`` is a perfectly valid control declaration that answers
    neither, and treating it as semantic evidence advanced ``doctor`` to rung 2
    while the questionnaire stayed exactly as open as before.

    ``risk_tags`` counts because it is the second of the two routes out of a
    ``declaration_below_inferred_evidence`` row — declaring the category is an
    answer about the effect, made without raising the headline value.

    ``environment.target: template`` counts, and counts once for the whole
    repository: it is a reviewed statement that nothing here holds a
    credential, which is exactly the authority question every action would
    otherwise be asked (#410 §G).
    """

    return bool(
        any(
            action.effect is not None or action.risk_tags or action.authority is not None
            for action in manifest.action_surface.actions
        )
        or any(source.authority is not None for source in manifest.tool_sources)
        or manifest.environment.target == "template"
    )


def _blocking(
    *, manifest: AgentsShipgateManifest, protection: ManifestProtection
) -> tuple[str, ...]:
    reasons: list[str] = []
    if manifest.ci.mode != "strict":
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
    # "any … `scan` still reports", not "the remaining": nothing here has run
    # a scan, so a sentence asserting there is a backlog would be guessing at
    # one — and a repository that has already cleared it would be told to do
    # work it finished.
    return (
        "answer any declaration questions `scan` still reports, then "
        f"{' and '.join(steps)}."
    )


__all__ = ["AdoptionRung", "adoption_rung", "audit_rung"]
