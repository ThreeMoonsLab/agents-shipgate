from __future__ import annotations

from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from agents_shipgate.core.agent_control import (
    AgentControlConsistencyError,
    derive_agent_control,
    normalize_legacy_agent_control,
)
from agents_shipgate.schemas.agent_control import (
    AGENT_CONTROL_ADAPTER,
    AgentActionRequiredControl,
    CodingAgentCommandAction,
    CodingAgentFetchBaseAction,
    CompleteAgentControl,
    HumanControlAction,
    HumanReviewRequiredControl,
    ReviewPublishableControl,
    project_legacy_agent_control,
    validate_agent_control,
)

VERIFY = "agents-shipgate verify --workspace . --config shipgate.yaml --format json"


def _complete() -> dict[str, object]:
    return derive_agent_control(reason="No control obligation remains.").model_dump(mode="json")


def _agent_action() -> dict[str, object]:
    return derive_agent_control(
        reason="Verification remains required.",
        next_action=CodingAgentCommandAction(
            kind="verify",
            command=VERIFY,
            why="Verify the current diff.",
        ),
        verify_required=True,
        publication_allowed=True,
    ).model_dump(mode="json")


def _unevaluated_agent_action() -> dict[str, object]:
    """An agent route whose caller did not assert an evaluated subject."""

    return derive_agent_control(
        reason="Shipgate stopped before reading a diff.",
        next_action=CodingAgentCommandAction(
            kind="configure",
            command=VERIFY,
            why="Point --config at the intended manifest, then rerun.",
        ),
        verify_required=True,
    ).model_dump(mode="json")


def _human() -> dict[str, object]:
    return derive_agent_control(
        reason="A protected trust root changed.",
        next_action=HumanControlAction(kind="stop", why="A protected trust root changed."),
        human_review_required=True,
        unsafe_block=True,
        required_reviewers=["security"],
    ).model_dump(mode="json")


def _review_publishable() -> dict[str, object]:
    return derive_agent_control(
        reason="A reviewer must approve this capability change before merge.",
        human_review_required=True,
        publication_allowed=True,
        allowed_next_commands=[VERIFY],
        required_reviewers=["security"],
    ).model_dump(mode="json")


@pytest.mark.parametrize(
    ("payload", "expected_type"),
    [
        (_complete(), CompleteAgentControl),
        (_agent_action(), AgentActionRequiredControl),
        (_review_publishable(), ReviewPublishableControl),
        (_human(), HumanReviewRequiredControl),
    ],
)
def test_agent_control_union_round_trips_exact_variants(
    payload: dict[str, object], expected_type: type[object]
) -> None:
    result = AGENT_CONTROL_ADAPTER.validate_python(payload)
    assert isinstance(result, expected_type)
    assert result.model_dump(mode="json") == payload


def test_generated_schema_is_a_state_discriminated_one_of() -> None:
    schema = AGENT_CONTROL_ADAPTER.json_schema()
    assert len(schema["oneOf"]) == 4
    assert schema["discriminator"] == {
        "mapping": {
            "agent_action_required": "#/$defs/AgentActionRequiredControl",
            "complete": "#/$defs/CompleteAgentControl",
            "human_review_required": "#/$defs/HumanReviewRequiredControl",
            "review_publishable": "#/$defs/ReviewPublishableControl",
        },
        "propertyName": "state",
    }
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize(
    "field",
    [
        "completion_allowed",
        "must_stop",
        "verify_required",
        "next_action",
        "allowed_next_commands",
        "human_review",
        "stop_reason",
    ],
)
def test_generated_schema_requires_every_published_control_field(field: str) -> None:
    payload = _complete()
    payload.pop(field)
    errors = Draft202012Validator(AGENT_CONTROL_ADAPTER.json_schema()).iter_errors(payload)
    assert list(errors)


def test_permissions_is_required_only_where_it_cannot_be_legacy() -> None:
    """Backward-valid for pre-v20 payloads; fail-closed on the new state.

    A contract-19 artifact has no ``permissions`` at all and must keep
    validating against the same schema identifiers it was emitted under, so the
    three states an old emitter could produce leave it optional. The
    publishable review did not exist then, so an omitted vector there is a
    malformed *current* payload silently granting publication.
    """

    validator = Draft202012Validator(AGENT_CONTROL_ADAPTER.json_schema())
    for legacy_reachable in (_complete(), _agent_action(), _human()):
        legacy_reachable.pop("permissions")
        assert not list(validator.iter_errors(legacy_reachable))
        # Pydantic reconstructs the vector rather than defaulting it to publish.
        assert validate_agent_control(legacy_reachable).permissions.merge is (
            legacy_reachable["state"] == "complete"
        )

    publishable = _review_publishable()
    publishable.pop("permissions")
    assert list(validator.iter_errors(publishable))
    with pytest.raises(ValidationError):
        AGENT_CONTROL_ADAPTER.validate_python(publishable)


def test_generated_schema_requires_explicit_action_actor() -> None:
    payload = _agent_action()
    payload["next_action"].pop("actor")
    errors = Draft202012Validator(AGENT_CONTROL_ADAPTER.json_schema()).iter_errors(payload)
    assert list(errors)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(must_stop=True),
        lambda value: value.update(completion_allowed=False),
        lambda value: value.update(verify_required=True),
        lambda value: value.update(
            next_action={
                "actor": "coding_agent",
                "kind": "verify",
                "command": VERIFY,
                "expects": None,
                "why": "Verify.",
            }
        ),
        lambda value: value.update(stop_reason="stop"),
    ],
)
def test_complete_contradictions_fail_pydantic_and_json_schema(mutate: object) -> None:
    payload = _complete()
    mutate(payload)  # type: ignore[operator]
    with pytest.raises(ValidationError):
        AGENT_CONTROL_ADAPTER.validate_python(payload)
    assert list(Draft202012Validator(AGENT_CONTROL_ADAPTER.json_schema()).iter_errors(payload))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(completion_allowed=True),
        lambda value: value.update(must_stop=True),
        lambda value: value.update(
            next_action={
                "actor": "human",
                "kind": "review",
                "command": None,
                "expects": None,
                "why": "Review.",
            }
        ),
        lambda value: value.update(
            human_review={
                "required": True,
                "why": "Review.",
                "required_reviewers": [],
            }
        ),
        lambda value: value.update(stop_reason="stop"),
    ],
)
def test_agent_action_contradictions_fail_pydantic_and_json_schema(
    mutate: object,
) -> None:
    payload = _agent_action()
    mutate(payload)  # type: ignore[operator]
    with pytest.raises(ValidationError):
        AGENT_CONTROL_ADAPTER.validate_python(payload)
    assert list(Draft202012Validator(AGENT_CONTROL_ADAPTER.json_schema()).iter_errors(payload))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(completion_allowed=True),
        lambda value: value.update(must_stop=False),
        lambda value: value.update(next_action=None),
        lambda value: value.update(
            human_review={
                "required": False,
                "why": None,
                "required_reviewers": [],
            }
        ),
        lambda value: value.update(stop_reason=None),
        lambda value: value.update(allowed_next_commands=[VERIFY]),
    ],
)
def test_human_state_contradictions_fail_pydantic_and_json_schema(
    mutate: object,
) -> None:
    payload = _human()
    mutate(payload)  # type: ignore[operator]
    with pytest.raises(ValidationError):
        AGENT_CONTROL_ADAPTER.validate_python(payload)
    assert list(Draft202012Validator(AGENT_CONTROL_ADAPTER.json_schema()).iter_errors(payload))


def test_agent_commands_are_exact_and_fetch_base_requires_expects() -> None:
    with pytest.raises(ValidationError):
        CodingAgentCommandAction(kind="install", command="  ", why="Install.")
    with pytest.raises(ValidationError):
        CodingAgentFetchBaseAction(expects="  ", why="Provide the base ref.")

    request = CodingAgentFetchBaseAction(
        kind="fetch_base",
        expects="origin/main",
        why="Make the verifier base available.",
    )
    result = derive_agent_control(reason="Base ref is missing.", next_action=request)
    assert result.state == "agent_action_required"
    assert result.next_action.expects == "origin/main"
    assert result.next_action.command is None


def test_agent_command_must_be_exposed_as_an_allowed_command() -> None:
    with pytest.raises(ValidationError, match="allowed_next_commands"):
        AgentActionRequiredControl(
            state="agent_action_required",
            reason="Verify.",
            verify_required=True,
            next_action=CodingAgentCommandAction(kind="verify", command=VERIFY, why="Verify."),
            allowed_next_commands=[],
        )


def test_derive_rejects_pending_verify_without_an_exact_route() -> None:
    with pytest.raises(AgentControlConsistencyError, match="exact coding-agent"):
        derive_agent_control(reason="Verification remains.", verify_required=True)


def test_derive_rejects_simultaneous_human_and_agent_routes() -> None:
    with pytest.raises(AgentControlConsistencyError, match="human-only"):
        derive_agent_control(
            reason="Conflicting routes.",
            human_review_required=True,
            next_action=CodingAgentCommandAction(kind="repair", command=VERIFY, why="Repair."),
        )


def test_derive_sets_repair_to_non_stopping_non_complete_state() -> None:
    control = derive_agent_control(
        reason="A mechanical repair is available.",
        next_action=CodingAgentCommandAction(
            kind="repair", command=VERIFY, why="Repair and verify."
        ),
    )
    assert control.state == "agent_action_required"
    assert control.completion_allowed is False
    assert control.must_stop is False
    assert control.verify_required is False
    assert control.human_review.required is False


def test_repair_preserves_an_independent_verify_obligation() -> None:
    control = derive_agent_control(
        reason="Repair, then run full verification.",
        next_action=CodingAgentCommandAction(
            kind="repair", command=VERIFY, why="Repair and verify."
        ),
        verify_required=True,
    )
    assert control.verify_required is True


def test_legacy_verify_obligation_overrides_old_completion_boolean() -> None:
    control = normalize_legacy_agent_control(
        {
            "completion_allowed": True,
            "must_stop": False,
            "verify_required": True,
            "first_next_action": {
                "actor": "coding_agent",
                "kind": "warn",
                "why": "Verify before finishing.",
            },
        },
        verification_command=VERIFY,
    )
    assert control.state == "agent_action_required"
    assert control.next_action.kind == "verify"
    assert control.completion_allowed is False


def test_legacy_install_normalizes_to_agent_action_even_if_old_stop_was_true() -> None:
    control = normalize_legacy_agent_control(
        {
            "completion_allowed": False,
            "must_stop": True,
            "first_next_action": {
                "actor": "coding_agent",
                "kind": "install",
                "command": "pipx install agents-shipgate",
                "why": "Install the verifier.",
            },
        }
    )
    assert control.state == "agent_action_required"
    assert control.must_stop is False
    assert control.next_action.kind == "install"


def test_legacy_human_route_wins_over_agent_safe_repair() -> None:
    control = normalize_legacy_agent_control(
        {
            "completion_allowed": False,
            "must_stop": False,
            "verify_required": True,
            "first_next_action": {
                "actor": "coding_agent",
                "kind": "repair",
                "command": VERIFY,
                "why": "Repair.",
            },
            "human_review": {"required": True, "why": "Human authority."},
        }
    )
    assert control.state == "human_review_required"
    assert control.must_stop is True
    assert control.next_action.actor == "human"


def test_unresolvable_legacy_action_fails_closed() -> None:
    control = normalize_legacy_agent_control(
        {
            "completion_allowed": True,
            "must_stop": False,
            "verify_required": True,
            "first_next_action": {
                "actor": "coding_agent",
                "kind": "warn",
                "why": "Verification is still required.",
            },
        }
    )
    assert control.state == "human_review_required"
    assert control.completion_allowed is False
    assert control.must_stop is True


def test_control_serialization_is_deterministic() -> None:
    first = derive_agent_control(
        reason="Verification remains required.",
        next_action=CodingAgentCommandAction(
            kind="verify",
            command=VERIFY,
            why="Verify the current diff.",
        ),
        verify_required=True,
        allowed_next_commands=["shipgate doctor --json", "shipgate detect --json"],
    )
    second = derive_agent_control(
        reason="Verification remains required.",
        next_action=CodingAgentCommandAction(
            kind="verify",
            command=VERIFY,
            why="Verify the current diff.",
        ),
        verify_required=True,
        allowed_next_commands=["shipgate detect --json", "shipgate doctor --json"],
    )
    assert AGENT_CONTROL_ADAPTER.dump_json(first) == AGENT_CONTROL_ADAPTER.dump_json(second)

    reordered = deepcopy(first.model_dump(mode="json"))
    reordered["allowed_next_commands"] = list(reversed(reordered["allowed_next_commands"]))
    assert AGENT_CONTROL_ADAPTER.dump_json(first) == AGENT_CONTROL_ADAPTER.dump_json(
        AGENT_CONTROL_ADAPTER.validate_python(reordered)
    )


def test_human_reviewer_order_is_deterministic() -> None:
    first = derive_agent_control(
        reason="Human review is required.",
        human_review_required=True,
        required_reviewers=["security", "platform"],
    )
    second = derive_agent_control(
        reason="Human review is required.",
        human_review_required=True,
        required_reviewers=["platform", "security"],
    )
    assert AGENT_CONTROL_ADAPTER.dump_json(first) == AGENT_CONTROL_ADAPTER.dump_json(second)


# --- Contract v20: publish authority is separate from merge authority ------


def test_permissions_are_fixed_by_the_state_not_set_independently() -> None:
    """Each state publishes exactly one permission set; drift is unemittable."""

    assert _complete()["permissions"] == {
        "edit": True,
        "commit": True,
        "push": True,
        "update_pr": True,
        "merge": True,
        "report_complete": True,
    }
    progress = {
        "edit": True,
        "commit": True,
        "push": True,
        "update_pr": True,
        "merge": False,
        "report_complete": False,
    }
    assert _agent_action()["permissions"] == progress
    assert _review_publishable()["permissions"] == progress
    none_of_it = dict.fromkeys(progress, False)
    assert _human()["permissions"] == none_of_it
    # An agent route whose subject was never evaluated authorizes only its own
    # next action, so it carries the same vector without stopping the turn.
    assert _unevaluated_agent_action()["permissions"] == none_of_it


@pytest.mark.parametrize(
    "mutate",
    [
        # merge and report_complete are the terminal pair: they may not diverge
        # from completion_allowed in either direction.
        lambda value: value["permissions"].update(merge=True),
        lambda value: value["permissions"].update(report_complete=True),
        # must_stop must keep meaning "nothing is authorized".
        lambda value: value.update(must_stop=True),
        lambda value: value["permissions"].update(commit=False, push=False, update_pr=False, edit=False),
        lambda value: value.update(stop_reason="stop"),
        lambda value: value.update(completion_allowed=True),
        lambda value: value.update(
            next_action={"actor": "human", "kind": "stop", "command": None, "expects": None, "why": "Stop."}
        ),
        lambda value: value.update(
            human_review={"required": False, "why": None, "required_reviewers": []}
        ),
    ],
)
def test_review_publishable_contradictions_fail_pydantic(mutate: object) -> None:
    payload = _review_publishable()
    mutate(payload)  # type: ignore[operator]
    with pytest.raises(ValidationError):
        AGENT_CONTROL_ADAPTER.validate_python(payload)


def test_the_four_transitions_are_distinct_control_states() -> None:
    """Repair, review publication, approval, and merge are separate steps."""

    repair = derive_agent_control(
        reason="A mechanical repair remains.",
        next_action=CodingAgentCommandAction(
            kind="repair", command=VERIFY, why="Apply the repair, then rerun."
        ),
        verify_required=True,
    )
    publish = validate_agent_control(_review_publishable())
    approved_rerun = derive_agent_control(
        reason="A trusted human authorization permits one exact bound operation.",
        next_action=CodingAgentCommandAction(
            kind="repair", command=VERIFY, why="Run the authorized operation."
        ),
        verify_required=True,
    )
    merged = validate_agent_control(_complete())

    assert [item.state for item in (repair, publish, approved_rerun, merged)] == [
        "agent_action_required",
        "review_publishable",
        "agent_action_required",
        "complete",
    ]
    # Only the terminal state authorizes merge, and only the publishable review
    # routes to a human while keeping the pull request updatable.
    assert [item.permissions.merge for item in (repair, publish, approved_rerun, merged)] == [
        False,
        False,
        False,
        True,
    ]
    assert publish.next_action.actor == "human"
    assert publish.permissions.update_pr is True
    assert repair.next_action.actor == "coding_agent"


def test_publication_cannot_be_asserted_alongside_an_unsafe_block() -> None:
    with pytest.raises(AgentControlConsistencyError):
        derive_agent_control(
            reason="Blocked.",
            human_review_required=True,
            unsafe_block=True,
            publication_allowed=True,
        )
    with pytest.raises(AgentControlConsistencyError):
        derive_agent_control(
            reason="Stop.",
            next_action=HumanControlAction(kind="stop", why="Stop."),
            human_review_required=True,
            publication_allowed=True,
        )
    with pytest.raises(AgentControlConsistencyError):
        derive_agent_control(
            reason="Review.",
            human_review_required=True,
            publication_allowed=True,
            stop_reason="This route does not stop.",
        )


def test_a_stopping_result_cannot_expose_allowed_next_commands() -> None:
    with pytest.raises(AgentControlConsistencyError):
        derive_agent_control(
            reason="Blocked.",
            human_review_required=True,
            unsafe_block=True,
            allowed_next_commands=[VERIFY],
        )


def test_legacy_payloads_never_normalize_to_the_publishable_route() -> None:
    """A pre-v20 emitter could not assert the publication fact."""

    legacy = normalize_legacy_agent_control(
        {
            "completion_allowed": False,
            "must_stop": True,
            "summary": "A human must review this change.",
            "human_review": {"required": True, "why": "Review the capability change."},
            "first_next_action": {"actor": "human", "kind": "review", "why": "Review."},
        }
    )
    assert legacy.state == "human_review_required"
    assert legacy.must_stop is True
    assert legacy.permissions.model_dump() == {
        "edit": False,
        "commit": False,
        "push": False,
        "update_pr": False,
        "merge": False,
        "report_complete": False,
    }

    contradictory = normalize_legacy_agent_control({"completion_allowed": True, "must_stop": True})
    assert contradictory.state == "human_review_required"


def test_frozen_projection_drops_permissions_and_collapses_the_new_state() -> None:
    """Pre-v20 consumers keep the conservative reading they were promised."""

    projected = project_legacy_agent_control(validate_agent_control(_review_publishable()))
    assert "permissions" not in projected
    assert projected["state"] == "human_review_required"
    assert projected["must_stop"] is True
    assert projected["allowed_next_commands"] == []
    assert projected["stop_reason"]

    unchanged = project_legacy_agent_control(validate_agent_control(_agent_action()))
    assert "permissions" not in unchanged
    assert unchanged["state"] == "agent_action_required"
    assert unchanged["allowed_next_commands"] == [VERIFY]


def test_published_vocabulary_matches_the_model() -> None:
    """`contract --json` advertises the fields the union actually emits."""

    from agents_shipgate.schemas.contract import (
        AGENT_CONTROL_FIELDS,
        AGENT_CONTROL_PERMISSIONS,
        AGENT_CONTROL_STATES,
    )

    control = validate_agent_control(_review_publishable())
    assert list(control.model_dump(mode="json")) == list(AGENT_CONTROL_FIELDS)
    assert list(control.permissions.model_dump(mode="json")) == list(AGENT_CONTROL_PERMISSIONS)
    assert set(AGENT_CONTROL_STATES) == set(
        AGENT_CONTROL_ADAPTER.json_schema()["discriminator"]["mapping"]
    )


# --- Review findings: publication needs an evaluated, bound subject ---------


def test_publication_is_an_asserted_fact_not_an_inferred_action_kind() -> None:
    """A kind allowlist is a heuristic, and a wrong one grants publication.

    `verify` stopping at a missing manifest emits a `configure` route with
    `execution=failed` and no diff read at all — a shape any plausible
    allowlist would have called evaluated. Callers assert the fact instead, and
    the default is that they have not.
    """

    for kind in ("configure", "verify", "repair", "discover", "rerun", "install"):
        unasserted = derive_agent_control(
            reason="Shipgate has not read a diff.",
            next_action=CodingAgentCommandAction(kind=kind, command=VERIFY, why="w"),
        )
        assert unasserted.permissions.publishes is False, kind
        asserted = derive_agent_control(
            reason="The change was evaluated.",
            next_action=CodingAgentCommandAction(kind=kind, command=VERIFY, why="w"),
            publication_allowed=True,
        )
        assert asserted.permissions.publishes is True, kind
        assert asserted.permissions.merge is False, kind

    fetch = derive_agent_control(
        reason="The requested base ref is unavailable.",
        next_action=CodingAgentFetchBaseAction(
            kind="fetch_base", expects="origin/main", why="Make the base available."
        ),
        verify_required=True,
    )
    assert fetch.permissions.publishes is False
    assert fetch.must_stop is False


def test_must_stop_implies_nothing_is_authorized() -> None:
    """One-directional on purpose: `must_stop=false` is not a publish promise."""

    for payload in (_complete(), _agent_action(), _review_publishable(), _human()):
        control = validate_agent_control(payload)
        if control.must_stop:
            assert control.permissions.authorizes_anything is False
        assert control.permissions.merge is control.completion_allowed
        assert control.permissions.report_complete is control.completion_allowed
    # The state this whole change exists for still authorizes progress.
    assert validate_agent_control(_review_publishable()).permissions.publishes is True
