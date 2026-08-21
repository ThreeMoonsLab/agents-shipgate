"""The adoption scorer's view of the one command-less coding-agent route.

``fetch_base`` is the sole ``agent_action_required`` route that may carry no
``command``: the obligation is an *input*, named in ``expects``, and the scorer
has to recognize the agent producing it. Two families of input satisfy it —
making history available, and putting the evaluated commit in the worktree,
which is what ``verify --preview`` asks for when the head under review is not
the commit checked out (#397 review).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # the harness is not an installed package
    sys.path.insert(0, str(REPO_ROOT))

from harness.adoption.scorer import rules  # noqa: E402


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


@pytest.mark.parametrize(
    "command",
    [
        "git fetch --no-tags origin main",
        "git remote update",
        "git checkout pr-1745",
        "git switch --detach 66f837355087",
    ],
)
def test_either_family_of_input_recovery_satisfies_a_commandless_route(
    command: str,
) -> None:
    control = _fetch_base("commit 66f837355087 checked out in this worktree")
    item = rules._TimelineItem(kind="action", command=command)

    assert rules._action_satisfies_control(item, control)


def test_an_unrelated_command_still_leaves_the_obligation_open() -> None:
    """The route stays fail-closed: only input recovery clears it."""

    control = _fetch_base("commit 66f837355087 checked out in this worktree")
    item = rules._TimelineItem(kind="action", command="npm test")

    assert not rules._action_satisfies_control(item, control)
