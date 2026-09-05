"""The newest Agents Shipgate release a stranger can actually fetch.

``__version__`` is what this working tree *is*. It is not what an adopter's
CI can resolve. The tree runs ahead of the newest tag for the whole interval
between releases — 56 days when #506 was filed — and anything written into
someone else's repository during that window has to name a ref that exists on
the day it is written, or the adopter's very first Shipgate run is a red check
about *our* repository, raised at action-resolution time before a single step
executes.

So every version an adopter's machine has to resolve — the ``uses:`` pin in the
workflow ``init --ci`` writes, the runner pins in the bundled adoption prompts,
the ``shipgate_version`` input in the bundled CI recipe — comes from
``LATEST_PUBLISHED_VERSION`` here, which is the same rule ``llms.txt``,
``.well-known``, the docs and the Action examples already follow. One rule, one
constant.

Both constants are bumped together, after the tag is pushed and never before —
``docs/release-runbook.md`` § After the tag. Bumping them is not what fixes an
unresolvable pin; nothing here may be set to a version that is not published:

``LATEST_PUBLISHED_VERSION``
    The newest ``v*`` release tag. ``tests/test_adopter_pins_resolve.py`` binds
    it to this repository's tag history and to
    ``.well-known/agents-shipgate.json``'s ``release_status.latest_release``;
    ``ci.yml``'s ``release-tag-consistency`` job re-checks that same claim
    against origin on every push to ``main``.

``LATEST_PUBLISHED_CONTRACT_VERSION``
    The ``CONTRACT_VERSION`` that release emits — read out of the tag itself by
    the same test. It is not derivable from the version string, and it is what
    makes the honesty rule below decidable instead of a guess.

The honesty rule: pinning the newest published release keeps the pin
resolvable, but it does not make it *sufficient*. An adoption prompt states a
contract floor it needs, and the newest published build may predate that floor
— it does today: ``v0.15.0`` emits contract ``10`` against a floor of ``21``.
The answer is to say so, in the prompt, in the same breath as the pin. The
answer is never to pin a version that does not exist: an agent told to fetch it
gets an error from the index rather than a build, and learns nothing about why.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The newest published release tag, without its ``v`` prefix.
LATEST_PUBLISHED_VERSION = "0.15.0"

#: The ``CONTRACT_VERSION`` that release emits. ``v0.15.0`` predates
#: ``MINIMUM_CONTROL_CONTRACT_VERSION`` entirely, so its ``contract --json``
#: carries no floor field at all.
LATEST_PUBLISHED_CONTRACT_VERSION = "10"


def latest_published_action_ref() -> str:
    """The ``ThreeMoonsLab/agents-shipgate@<ref>`` ref to write into a workflow."""

    return f"v{LATEST_PUBLISHED_VERSION}"


def published_release_meets_contract_floor(floor: str) -> bool:
    """Whether the newest published release satisfies ``floor``.

    Contract versions are monotonically increasing integers rendered as
    strings. A non-integer on either side is treated as unsatisfied rather than
    raised: this decides whether a prompt makes a claim about a published
    build, and the safe answer to "I cannot tell" is to state the gap.
    """

    try:
        return int(LATEST_PUBLISHED_CONTRACT_VERSION) >= int(floor)
    except ValueError:
        return False


@dataclass(frozen=True)
class ContractFloorProse:
    """The two renderings of one fact, produced together so they cannot differ.

    ``notice`` is the standalone sentence an adoption prompt puts beside its
    install step; ``source`` is the short parenthetical a prompt uses where it
    names which build reports the floor. Two call sites, one computed state.
    """

    notice: str
    source: str
    satisfied: bool


def contract_floor_prose(floor: str) -> ContractFloorProse:
    """Render what is true about ``floor`` and the newest published release."""

    version = LATEST_PUBLISHED_VERSION
    if published_release_meets_contract_floor(floor):
        return ContractFloorProse(
            notice=(
                f"The newest published release, `agents-shipgate` `{version}`, reports it, "
                "and every pin below names that release."
            ),
            source=f"`{version}` or newer",
            satisfied=True,
        )
    published = LATEST_PUBLISHED_CONTRACT_VERSION
    return ContractFloorProse(
        notice=(
            "**No published release reports that contract yet — say so before you start.** "
            f"The newest published build is `agents-shipgate` `{version}`, which reports "
            f"contract `{published}`, and every pin below names it because a version the "
            "index does not carry cannot be fetched at all: the install fails before any "
            f"step runs. Run the steps `{version}` supports, and at the first step that "
            f"needs contract `{floor}`, stop and tell the user no released build provides "
            "it yet rather than reporting that step as done."
        ),
        source=(
            f"no published build reports that floor yet — the newest, `{version}`, "
            f"reports contract `{published}`"
        ),
        satisfied=False,
    )


__all__ = [
    "LATEST_PUBLISHED_CONTRACT_VERSION",
    "LATEST_PUBLISHED_VERSION",
    "ContractFloorProse",
    "contract_floor_prose",
    "latest_published_action_ref",
    "published_release_meets_contract_floor",
]
