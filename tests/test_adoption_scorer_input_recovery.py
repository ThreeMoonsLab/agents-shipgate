"""The adoption scorer's view of the one command-less coding-agent route.

``fetch_base`` is the sole ``agent_action_required`` route that may carry no
``command``: the obligation is an *input*, named in ``expects``, and the scorer
has to recognize the agent producing it. Two families of input satisfy it —
making history available, and putting the evaluated commit in the worktree,
which is what ``verify --preview`` asks for when the head under review is not
the commit checked out (#397 review).
"""

from __future__ import annotations

import pytest

# The harness is not an installed package; `pythonpath = ["src", "."]` in
# pyproject puts the repository root on the path for the test session, which is
# the one mechanism that should decide this.
from harness.adoption.scorer import rules


def _fetch_base(expects: str) -> rules._ControlSnapshot:
    return rules._ControlSnapshot(
        state="agent_action_required",
        reason="An input is missing.",
        completion_allowed=False,
        must_stop=False,
        verify_required=True,
        next_action={
            "actor": "coding_agent",
            "kind": "fetch_base",
            "command": None,
            "expects": expects,
            "why": "Provide the input, then rerun.",
        },
        allowed_next_commands=(),
        human_review_required=False,
        source_schema="test",
    )


CHECKOUT_REQUEST = "commit 66f837355087 checked out in this worktree"
FETCH_REQUEST = "origin/main...feature"


def _satisfied(expects: str, command: str) -> bool:
    return rules._action_satisfies_control(
        rules._TimelineItem(kind="action", command=command), _fetch_base(expects)
    )


@pytest.mark.parametrize(
    "command",
    [
        "git checkout 66f837355087",
        "git switch --detach 66f8373",
        # Either side may be abbreviated, and the request may be reached from
        # elsewhere in the repository.
        "git -C repo checkout 66f837355087550877",
    ],
)
def test_a_checkout_of_the_requested_commit_satisfies_the_route(command: str) -> None:
    assert _satisfied(CHECKOUT_REQUEST, command)


@pytest.mark.parametrize(
    "command",
    [
        # The other family entirely: a fetch leaves the worktree where it was,
        # and project markers are read from the worktree.
        "git fetch origin main",
        # `restore` and the path-restoring form of `checkout` rewrite files and
        # leave HEAD where it was.
        "git restore --source=66f837355087 python/",
        "git checkout -- AGENTS.md",
        # Moves HEAD, to the wrong place.
        "git checkout deadbeef",
        "git switch main",
        "npm test",
    ],
)
def test_a_command_that_does_not_produce_the_commit_leaves_it_open(
    command: str,
) -> None:
    """The obligation is the *requested* input, not the shape of the verb."""

    assert not _satisfied(CHECKOUT_REQUEST, command)


@pytest.mark.parametrize(
    "command", ["git fetch --no-tags origin main", "git remote update"]
)
def test_a_fetch_satisfies_a_ref_request(command: str) -> None:
    assert _satisfied(FETCH_REQUEST, command)


@pytest.mark.parametrize("command", ["git checkout 66f837355087", "npm test"])
def test_a_checkout_does_not_satisfy_a_ref_request(command: str) -> None:
    """Matching is split by what was asked for, in both directions."""

    assert not _satisfied(FETCH_REQUEST, command)
