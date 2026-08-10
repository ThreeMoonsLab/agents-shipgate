"""The compact ``shipgate.agent_control/v1`` control envelope.

The failure this covers is not a wrong verdict — it is a right verdict a reader
could not act on. In the #338 walkthrough a run reported ``execution:
"succeeded"`` and exit code 0 while its operational state was
``human_review_required``, and both facts sat thousands of tokens apart in the
same document. The tests below hold three things:

* the envelope is a *projection* — it copies the authoritative control object
  and cannot widen it;
* the three confusable facts (tool ran / gate decided / agent may act) are
  separated structurally, so no emitter can publish a payload that conflates
  them; and
* one read answers the whole routing question, within a published budget.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.core.agent_control_envelope import (
    AgentControlRouteUnavailable,
    control_headline_lines,
    envelope_from_pointer,
    envelope_from_verifier,
    project_agent_control_envelope,
    render_agent_control_envelope,
)
from agents_shipgate.schemas.agent_control import (
    CodingAgentCommandAction,
)
from agents_shipgate.schemas.agent_control_envelope import (
    MAX_AGENT_CONTROL_ENVELOPE_BYTES,
    MAX_ENVELOPE_PROSE_CHARS,
    PROSE_TRUNCATION_MARKER,
    AgentControlArtifactRef,
    AgentControlEnvelope,
)
from agents_shipgate.schemas.current_control import (
    CurrentControlArtifactRef,
    CurrentControlPointer,
    CurrentControlWorkspaceIdentity,
    HumanReviewRequiredCurrentControl,
    current_control_identity_payload,
)
from agents_shipgate.schemas.verification_identity import content_id
from agents_shipgate.schemas.verifier import VerifierArtifact

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "samples" / "clean_read_only_agent"
runner = CliRunner()


# ---------------------------------------------------------------------------
# Control objects the projection is fed, one per state.
# ---------------------------------------------------------------------------


def _complete():
    return derive_agent_control(reason="Release ready.")


def _agent_action(command: str = "agents-shipgate verify --json"):
    return derive_agent_control(
        reason="Verification is required before completion.",
        next_action=CodingAgentCommandAction(
            kind="verify", command=command, why="Run the PR gate."
        ),
        verify_required=True,
        allowed_next_commands=[command],
        publication_allowed=True,
    )


def _review_publishable():
    return derive_agent_control(
        reason="A human must approve the merge.",
        human_review_required=True,
        publication_allowed=True,
        human_review_why="A capability was added that a person must accept.",
    )


def _human_stop():
    return derive_agent_control(
        reason="Shipgate could not read the diff.",
        unsafe_block=True,
        human_review_why="The diff could not be read, so no verdict was reached.",
    )


def _envelope(control, **overrides) -> AgentControlEnvelope:
    kwargs = {
        "control": control,
        "operation": "verify",
        "source": "run",
        "execution": "succeeded",
        "exit_code": 0,
        "decision": "passed",
        "decision_source": "release_decision",
    }
    kwargs.update(overrides)
    return project_agent_control_envelope(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The three separations, held structurally.
# ---------------------------------------------------------------------------


def test_succeeded_execution_does_not_imply_release_readiness():
    """The headline confusion of #338, stated as an accepted payload.

    A run that executed perfectly and exited 0 can still authorize nothing
    terminal. Nothing in the envelope may object to that combination — it is
    the normal case — while every field a reader would route on says so.
    """

    envelope = _envelope(
        _review_publishable(),
        execution="succeeded",
        exit_code=0,
        decision="review_required",
    )

    assert envelope.execution == "succeeded"
    assert envelope.exit_code == 0
    assert envelope.control_state == "review_publishable"
    assert envelope.permissions.merge is False
    assert envelope.permissions.report_complete is False
    # ...and the actions that produce a reviewable pull request stay open.
    assert envelope.permissions.commit is True
    assert envelope.permissions.push is True
    assert envelope.permissions.update_pr is True
    assert envelope.next_actor == "human"


def test_a_failed_execution_can_never_authorize_completion():
    """The one direction of the implication that *is* enforced."""

    with pytest.raises(ValidationError, match="failed execution cannot authorize completion"):
        AgentControlEnvelope.model_validate(
            {
                **json.loads(render_agent_control_envelope(_envelope(_complete()))),
                "execution": "failed",
            }
        )


def test_exit_code_zero_never_stands_in_for_merge_authority():
    """Advisory CI exits 0 on a blocked decision; the envelope must still deny."""

    envelope = _envelope(
        _human_stop(),
        execution="succeeded",
        exit_code=0,
        decision="blocked",
    )

    assert envelope.exit_code == 0
    assert envelope.permissions.merge is False
    assert envelope.permissions.authorizes_anything is False
    assert envelope.control_state == "human_review_required"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "permissions",
            {
                "edit": True,
                "commit": True,
                "push": True,
                "update_pr": True,
                "merge": True,
                "report_complete": True,
            },
            "exactly when",
        ),
        ("next_actor", "coding_agent", "next_actor must name"),
        ("decision_source", "none", "both be present or both absent"),
    ],
)
def test_a_contradictory_field_is_rejected(field, value, message):
    """Hand-assembled payloads cannot publish a shape the union would refuse."""

    payload = json.loads(render_agent_control_envelope(_envelope(_review_publishable())))
    payload[field] = value
    with pytest.raises(ValidationError, match=message):
        AgentControlEnvelope.model_validate(payload)


def test_a_stopping_state_authorizes_nothing():
    envelope = _envelope(_human_stop(), decision="blocked")

    assert envelope.control_state == "human_review_required"
    assert envelope.permissions.authorizes_anything is False
    assert envelope.human_review.required is True


def test_a_non_complete_envelope_always_names_who_acts_next():
    """The dead end #338 is about: no route, no actor, no way forward."""

    payload = json.loads(render_agent_control_envelope(_envelope(_agent_action())))
    payload["next_action"] = None
    payload["next_actor"] = "none"
    with pytest.raises(ValidationError, match="must name the actor who acts next"):
        AgentControlEnvelope.model_validate(payload)


# ---------------------------------------------------------------------------
# Projection fidelity.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "build",
    [_complete, _agent_action, _review_publishable, _human_stop],
    ids=["complete", "agent_action_required", "review_publishable", "human_review_required"],
)
def test_the_permission_vector_is_copied_not_recomputed(build):
    """Every state round-trips its authority exactly.

    A projection that re-derived the vector would be a second place authority is
    decided, which is the roadmap non-goal this envelope must not cross.
    """

    control = build()
    envelope = _envelope(control, decision=None, decision_source="none")

    assert envelope.control_state == control.state
    assert envelope.permissions.model_dump() == control.permissions.model_dump()


def test_prose_is_capped_but_the_exact_command_never_is():
    """The budget may cost explanation. It may never cost reproducibility."""

    command = "agents-shipgate verify --workspace " + "a" * 300 + " --json"
    control = derive_agent_control(
        reason="R" * (MAX_ENVELOPE_PROSE_CHARS + 200),
        next_action=CodingAgentCommandAction(
            kind="verify", command=command, why="W" * (MAX_ENVELOPE_PROSE_CHARS + 200)
        ),
        verify_required=True,
        allowed_next_commands=[command],
    )

    envelope = _envelope(control, decision=None, decision_source="none")

    assert len(envelope.reason) == MAX_ENVELOPE_PROSE_CHARS
    assert envelope.reason.endswith(PROSE_TRUNCATION_MARKER)
    assert len(envelope.next_action.why) == MAX_ENVELOPE_PROSE_CHARS
    assert envelope.next_action.command == command


def test_an_agent_owned_route_always_carries_something_executable():
    """A route the agent owns must be actionable without further reading.

    ``CodingAgentCommandAction`` carries an exact command;
    ``CodingAgentFetchBaseAction`` carries the exact ref to make available,
    because Shipgate never fetches refs itself. Either is executable. Neither
    may be empty.
    """

    envelope = _envelope(_agent_action(), decision=None, decision_source="none")

    assert envelope.next_actor == "coding_agent"
    assert envelope.next_action.command or envelope.next_action.expects


def test_the_envelope_carries_the_published_fields_and_nothing_else():
    """It is a control interface, not a second copy of the report.

    Pinned as an exact set: a future field added here is a contract change, and
    a field that leaks findings, tool inventories, or evidence would quietly
    turn the cheap read back into the expensive one.
    """

    payload = json.loads(render_agent_control_envelope(_envelope(_complete())))

    assert set(payload) == {
        "schema_version",
        "contract_version",
        "operation",
        "source",
        "execution",
        "exit_code",
        "decision",
        "decision_source",
        "control_state",
        "permissions",
        "verify_required",
        "next_actor",
        "next_action",
        "human_review",
        "reason",
        "current_control_id",
        "artifacts",
    }


def test_required_reviewers_survive_the_budget():
    """A name that gates the merge is never dropped to hit a size target."""

    reviewers = [f"security-reviewer-{index}" for index in range(20)]
    control = derive_agent_control(
        reason="A human must approve the merge.",
        human_review_required=True,
        publication_allowed=True,
        human_review_why="Q" * (MAX_ENVELOPE_PROSE_CHARS + 50),
        required_reviewers=reviewers,
    )

    envelope = _envelope(control, decision="review_required")

    assert sorted(envelope.human_review.required_reviewers) == sorted(reviewers)
    assert len(envelope.human_review.why) == MAX_ENVELOPE_PROSE_CHARS


def test_a_representative_envelope_fits_the_published_budget():
    """The budget is a promise to the caller, so it is measured, not asserted."""

    artifacts = {
        key: AgentControlArtifactRef(
            path=f"agents-shipgate-reports/{key}.json", sha256=f"sha256:{'a' * 64}"
        )
        for key in (
            "verification_receipt",
            "verification_artifact_manifest",
            "verification_plan",
            "agent_handoff",
            "verifier",
            "verify_run",
            "human_authorization",
            "report",
            "report_markdown",
            "report_sarif",
            "packet",
            "pr_comment",
        )
    }
    command = "agents-shipgate verify --workspace . --config shipgate.yaml --json"
    control = derive_agent_control(
        reason="R" * MAX_ENVELOPE_PROSE_CHARS,
        next_action=CodingAgentCommandAction(
            kind="verify", command=command, why="W" * MAX_ENVELOPE_PROSE_CHARS
        ),
        verify_required=True,
        allowed_next_commands=[command],
    )

    rendered = render_agent_control_envelope(
        _envelope(
            control,
            decision=None,
            decision_source="none",
            artifacts=artifacts,
            current_control_id=f"sha256:{'b' * 64}",
        )
    )

    assert len(rendered.encode("utf-8")) <= MAX_AGENT_CONTROL_ENVELOPE_BYTES


# ---------------------------------------------------------------------------
# Reconciliation with the current-control pointer.
# ---------------------------------------------------------------------------


def _pointer(reason: str) -> CurrentControlPointer:
    draft = CurrentControlPointer.model_construct(
        current_control_id="sha256:" + "0" * 64,
        operation="verify",
        lifecycle_state="terminal",
        request_id=None,
        decision_id=None,
        workspace_identity=CurrentControlWorkspaceIdentity(repository="example/repo"),
        control=HumanReviewRequiredCurrentControl(state="human_review_required", reason=reason),
        artifacts={
            "verifier": CurrentControlArtifactRef(
                path="verifier.json", sha256=f"sha256:{'c' * 64}", size_bytes=1
            )
        },
        supersedes=None,
    )
    identity = content_id(current_control_identity_payload(draft))
    return CurrentControlPointer.model_validate(
        {**draft.model_dump(mode="json"), "current_control_id": identity}
    )


def _completed_verifier(repo: Path) -> VerifierArtifact:
    """A real ``complete`` verifier artifact.

    Constructed by running the engine rather than by hand: ``VerifierArtifact``
    binds several container invariants across ``control``, ``release_decision``,
    and ``can_merge_without_human``, and a hand-built stub that satisfies them
    would be asserting my reading of those invariants rather than the
    reconciliation this test is about.
    """

    assert (
        runner.invoke(
            app, ["verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--json"]
        ).exit_code
        == 0
    )
    verifier = VerifierArtifact.model_validate_json(
        (repo / "agents-shipgate-reports" / "verifier.json").read_bytes()
    )
    assert verifier.control.state == "complete"
    return verifier


def test_the_pointer_overrules_a_run_that_claimed_more_than_it_bound(repo: Path):
    """A completion the pointer refused must not be recovered from the run.

    ``project_agent_control`` downgrades a ``complete`` whose run bound no
    terminal receipt. Reading the run's own optimistic control block instead
    would undo exactly the refusal that keeps stale authorization impossible.
    """

    reason = "A completion-authorizing control was projected without a receipt."
    envelope = envelope_from_verifier(
        _completed_verifier(repo),
        operation="verify",
        source="refresh",
        exit_code=0,
        pointer=_pointer(reason),
    )

    assert envelope.control_state == "human_review_required"
    assert envelope.permissions.authorizes_anything is False
    assert envelope.next_actor == "human"
    assert reason in envelope.reason


def test_a_refresh_without_a_bound_verifier_refuses_rather_than_inventing_a_route():
    with pytest.raises(AgentControlRouteUnavailable):
        envelope_from_pointer(_pointer("no route here"), verifier=None, exit_code=None)


def test_artifact_paths_are_openable_from_where_the_envelope_was_printed(repo: Path):
    """The pointer records paths relative to itself; stdout has no directory."""

    envelope = envelope_from_verifier(
        _completed_verifier(repo),
        operation="verify",
        source="run",
        exit_code=0,
        pointer=_pointer("stopped"),
        artifact_root="agents-shipgate-reports",
    )

    assert envelope.artifacts["verifier"].path == "agents-shipgate-reports/verifier.json"


# ---------------------------------------------------------------------------
# The human lead-in.
# ---------------------------------------------------------------------------


def test_human_text_leads_with_the_operational_state_and_next_actor():
    lines = control_headline_lines(_envelope(_review_publishable(), decision="review_required"))

    assert lines[0] == "Control: review_publishable — next actor: human"
    assert lines[1].startswith("You may: ")
    assert "commit" in lines[1] and "push" in lines[1] and "update_pr" in lines[1]
    assert "merge" in lines[2] and lines[2].startswith("You may not: ")


def test_human_text_prints_the_exact_command_when_the_agent_owns_the_route():
    lines = control_headline_lines(
        _envelope(_agent_action(), decision=None, decision_source="none")
    )

    assert lines[0] == "Control: agent_action_required — next actor: coding_agent"
    assert "Next command: agents-shipgate verify --json" in lines


# ---------------------------------------------------------------------------
# End to end, through the real CLI.
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    for name in ("shipgate.yaml", "tools.json"):
        shutil.copy(SAMPLE / name, workspace / name)
    (workspace / ".gitignore").write_text("agents-shipgate-reports/\n", encoding="utf-8")
    _git(workspace, "init", "-q", "-b", "main")
    _git(workspace, "config", "user.email", "test@example.test")
    _git(workspace, "config", "user.name", "Test User")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-m", "fixture")
    return workspace


def test_verify_format_control_answers_in_one_object(repo: Path):
    result = runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--format", "control"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "shipgate.agent_control/v1"
    assert payload["operation"] == "verify"
    assert payload["source"] == "run"
    assert payload["decision_source"] == "release_decision"
    assert payload["current_control_id"].startswith("sha256:")
    # The evidence is reachable from the same object, by path and hash.
    assert payload["artifacts"]["verifier"]["path"].endswith("verifier.json")
    assert (repo / payload["artifacts"]["verifier"]["path"]).is_file()
    assert len(result.stdout.encode("utf-8")) <= MAX_AGENT_CONTROL_ENVELOPE_BYTES


def test_verify_format_control_is_smaller_than_the_artifact_it_projects(repo: Path):
    """The metric this issue moves: control-payload tokens per verify."""

    compact = runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--format", "control"],
    )
    full = runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--format", "json"],
    )

    assert compact.exit_code == 0 and full.exit_code == 0
    assert len(compact.stdout) < len(full.stdout) / 2


def test_verify_text_leads_with_control_before_the_verdict(repo: Path):
    result = runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--format", "text"],
    )

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0].startswith("Control: ")
    assert "next actor: " in lines[0]
    # The verdict line is retained verbatim for existing readers, below.
    assert any(line.startswith("Agents Shipgate verify: ") for line in lines)


def test_text_mode_survives_a_control_projection_failure(repo: Path, monkeypatch):
    """A control-plane bug must not cost a human their verdict or exit code.

    The fallback denies authority rather than assuming it, and `--format
    control` still raises — a caller who asked for the control answer must not
    receive a partial one.
    """

    from agents_shipgate.cli.verify import command as verify_command

    def explode(*args, **kwargs):
        raise ValueError("two enforcement layers disagree")

    monkeypatch.setattr(verify_command, "_verify_envelope", explode)

    result = runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--format", "text"],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0].startswith("Control: could not be projected")
    assert "authorizing nothing" in result.stdout
    assert any(line.startswith("Agents Shipgate verify: ") for line in result.stdout.splitlines())


def test_verify_json_still_emits_the_full_verifier_artifact(repo: Path):
    """`--format control` adds a shape; it does not take one away."""

    result = runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["verifier_schema_version"]
    assert "control" in payload and "release_decision" in payload


def test_check_projects_the_boundary_decision_not_a_release_one(repo: Path):
    result = runner.invoke(
        app,
        [
            "check",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--format",
            "agent-control-json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["operation"] == "check"
    assert payload["decision_source"] == "agent_boundary"
    assert payload["artifacts"] == {}
    assert payload["current_control_id"] is None


def test_check_reports_an_unreadable_diff_the_way_verify_does(repo: Path):
    """One condition, one description across two commands.

    A check that cannot read its diff still emits a considered `block`. Calling
    that `execution: "succeeded"` because the process finished would reintroduce
    the confusion in miniature, and would disagree with `verify`, which reports
    the same condition as a failed execution.
    """

    result = runner.invoke(
        app,
        [
            "check",
            "--workspace", str(repo),
            "--config", "shipgate.yaml",
            "--diff", str(repo / "absent.diff"),
            "--format", "agent-control-json",
        ],
    )

    payload = json.loads(result.stdout)
    assert payload["execution"] == "failed"
    assert payload["decision"] == "block"
    assert payload["control_state"] == "human_review_required"
    assert payload["exit_code"] == 0
    assert payload["permissions"]["merge"] is False


def test_agent_control_returns_the_envelope_by_default(repo: Path):
    assert (
        runner.invoke(
            app, ["verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--json"]
        ).exit_code
        == 0
    )

    envelope = runner.invoke(
        app,
        [
            "agent",
            "control",
            "--workspace",
            str(repo),
            "--reports-dir",
            str(repo / "agents-shipgate-reports"),
        ],
    )
    pointer = runner.invoke(
        app,
        [
            "agent",
            "control",
            "--workspace",
            str(repo),
            "--reports-dir",
            str(repo / "agents-shipgate-reports"),
            "--format",
            "pointer",
        ],
    )

    assert envelope.exit_code == 0, envelope.output
    assert pointer.exit_code == 0, pointer.output
    compact = json.loads(envelope.stdout)
    raw = json.loads(pointer.stdout)
    assert compact["schema_version"] == "shipgate.agent_control/v1"
    assert compact["source"] == "refresh"
    assert raw["schema_version"] == "shipgate.current_control/v1"
    # Same generation, two shapes: the envelope adds the route the pointer omits.
    assert compact["current_control_id"] == raw["current_control_id"]
    assert "next_action" in compact and "next_action" not in raw["control"]


def test_agent_control_refuses_a_routeless_pointer_with_an_exact_command(repo: Path):
    """A `scan` pointer is current, and still cannot say what to do next.

    `scan` reaches no release decision and binds no verifier, so there is no
    published route to return. The refusal is the product answer — with the
    exact command that produces one — rather than a fabricated step or a
    silently empty route.
    """

    reports = repo / "agents-shipgate-reports"
    assert runner.invoke(
        app,
        [
            "scan",
            "--workspace", str(repo),
            "--config", str(repo / "shipgate.yaml"),
            "--out", str(reports),
        ],
    ).exit_code == 0

    result = runner.invoke(
        app,
        ["agent", "control", "--workspace", str(repo), "--reports-dir", str(reports)],
    )

    assert result.exit_code == 4
    assert "no published route could be recovered" in result.output
    # The pointer itself is still readable; only the route is missing.
    pointer = runner.invoke(
        app,
        [
            "agent", "control",
            "--workspace", str(repo),
            "--reports-dir", str(reports),
            "--format", "pointer",
        ],
    )
    assert pointer.exit_code == 0, pointer.output
    assert json.loads(pointer.stdout)["operation"] == "scan"


def test_agent_control_rejects_an_unknown_format(repo: Path):
    result = runner.invoke(
        app,
        [
            "agent",
            "control",
            "--workspace",
            str(repo),
            "--reports-dir",
            str(repo / "agents-shipgate-reports"),
            "--format",
            "verifier",
        ],
    )

    assert result.exit_code == 2
