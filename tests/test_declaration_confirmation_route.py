"""#410 §D — ``next_action.kind: confirm_declarations``.

The route is what turns a questionnaire into a loop an agent can finish. It
fires only where a declaration is what the verdict is short of, it authorizes
only publishing (never merge), and when it is done the remaining questions are
named rather than summarised as "human review required".

Everything here is asserted on the published envelope, because that is the
surface an agent actually reads.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from agents_shipgate.cli.main import app

runner = CliRunner()

_AGENT_SOURCE = '''
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool


def send_email(to: str, body: str) -> dict:
    """Send an email."""
    return {"status": "sent"}


root_agent = LlmAgent(
    name="closer_agent",
    instruction="Route approvals.",
    tools=[FunctionTool(func=send_email)],
)
'''

_MANIFEST = """version: "0.1"
project:
  name: declaration-route
agent:
  name: closer-agent
  declared_purpose:
    - route approval mail
environment:
  target: local
tool_sources:
  - id: adk_agent
    type: google_adk
    path: agent.py
"""

# A tool nothing reads a risk into: no effect can be proposed for it, so both
# of its questions are a human's. The fixture the "no route" cases need — not
# a declared ``send_email``, which trips a built-in control policy and reaches
# ``blocked`` instead of the ``insufficient_evidence`` those cases are about.
_NEUTRAL_SOURCE = '''
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool


def lookup_case(case_id: str) -> dict:
    """Look up a case."""
    return {"case": case_id}


root_agent = LlmAgent(
    name="closer_agent",
    instruction="Route approvals.",
    tools=[FunctionTool(func=lookup_case)],
)
'''

_SOURCE_AUTHORITY = """    authority:
      mode: none
"""

_DECLARED_EFFECT = """action_surface:
  actions:
    - tool: lookup_case
      source_id: adk_agent
      effect: read
"""


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repo(path: Path, *, manifest: str = _MANIFEST, agent: str = _AGENT_SOURCE) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.test")
    _git(path, "config", "user.name", "Test")
    (path / "agent.py").write_text(agent, encoding="utf-8")
    (path / "shipgate.yaml").write_text(manifest, encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "base")
    return path


def _verify(repo: Path, *extra: str) -> dict:
    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--config",
            "shipgate.yaml",
            "--ci-mode",
            "advisory",
            "--format",
            "control",
            "--out",
            "sg-out",
            *extra,
        ],
    )
    assert result.exit_code in (0, 1), result.output
    start = result.output.index("{")
    return json.loads(result.output[start:])


def _artifact(repo: Path, name: str) -> dict:
    return json.loads((repo / "sg-out" / name).read_text(encoding="utf-8"))


# --- the route fires --------------------------------------------------------


def test_the_route_hands_the_agent_the_answers_and_names_the_rest(tmp_path: Path) -> None:
    envelope = _verify(_repo(tmp_path / "repo"))

    assert envelope["decision"] == "insufficient_evidence"
    assert envelope["control_state"] == "agent_action_required"
    action = envelope["next_action"]
    assert action["kind"] == "confirm_declarations"
    assert action["actor"] == "coding_agent"
    assert "apply-patches" in action["command"]
    assert "--kinds declare_action" in action["command"]

    assert action["agent_authorable"] == 1
    assert action["human_authorable"] == 1
    tagged = {(q["dimension"], q["authorable_by"]) for q in action["questions"]}
    assert tagged == {("effect", "coding_agent"), ("authority", "human")}
    human = [q for q in action["questions"] if q["authorable_by"] == "human"][0]
    assert human["answer_path"].startswith("shipgate.yaml#tool_sources")


def test_the_route_authorizes_publishing_and_never_merging(tmp_path: Path) -> None:
    envelope = _verify(_repo(tmp_path / "repo"))

    assert envelope["permissions"] == {
        "edit": True,
        "commit": True,
        "push": True,
        "update_pr": True,
        "merge": False,
        "report_complete": False,
    }
    assert envelope["verify_required"] is True
    assert envelope["human_review"]["required"] is False


def test_the_published_command_is_the_one_the_fix_task_authorizes(tmp_path: Path) -> None:
    """The join, asserted rather than assumed.

    The envelope publishes the richer form of a step the control holds as a
    ``repair`` command. If those two ever named different commands, the typed
    route would describe work the control is not asking for.
    """

    repo = _repo(tmp_path / "repo")
    envelope = _verify(repo)
    verifier = _artifact(repo, "verifier.json")

    confirmation = verifier["fix_task"]["declaration_confirmation"]
    assert confirmation is not None
    assert envelope["next_action"]["command"] == confirmation["command"]
    assert verifier["control"]["next_action"]["kind"] == "repair"
    assert verifier["control"]["next_action"]["command"] == confirmation["command"]
    assert confirmation["command"] in verifier["control"]["allowed_next_commands"]


def test_the_task_forbids_answering_what_the_scan_left_blank(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    _verify(repo)
    fix_task = _artifact(repo, "verifier.json")["fix_task"]

    assert fix_task["actor"] == "coding_agent"
    assert fix_task["safe_to_attempt"] is True
    forbidden = {row["id"] for row in fix_task["forbidden_repairs"]}
    assert "answer_unevidenced_declaration" in forbidden
    assert "invent_authority_evidence" in forbidden


# --- the route does not fire ------------------------------------------------


def test_no_route_when_every_open_question_is_a_humans(tmp_path: Path) -> None:
    """Nothing was read about this action, so nothing may be drafted for it."""

    repo = _repo(tmp_path / "repo", agent=_NEUTRAL_SOURCE)
    envelope = _verify(repo)

    assert envelope["decision"] == "insufficient_evidence"
    assert envelope["next_action"]["actor"] == "human"
    assert envelope["next_action"]["kind"] == "review"
    assert _artifact(repo, "verifier.json")["fix_task"]["declaration_confirmation"] is None


def test_no_route_when_nothing_is_owed(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path / "repo",
        manifest=_MANIFEST + _SOURCE_AUTHORITY + _DECLARED_EFFECT,
        agent=_NEUTRAL_SOURCE,
    )
    envelope = _verify(repo)

    assert envelope["next_action"] is None or envelope["next_action"]["kind"] != (
        "confirm_declarations"
    )
    fix_task = _artifact(repo, "verifier.json").get("fix_task")
    assert fix_task is None or fix_task.get("declaration_confirmation") is None


_BLOCKING_SOURCE = '''
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool


def request_refund_approval(order_id: str, amount: float) -> dict:
    """Request a refund approval."""
    return {"status": "pending"}


def send_email(to: str, body: str) -> dict:
    """Send an email."""
    return {"status": "sent"}


root_agent = LlmAgent(
    name="closer_agent",
    instruction="Route approvals.",
    tools=[FunctionTool(func=request_refund_approval), FunctionTool(func=send_email)],
)
'''

# One tool declared into a control gap the manifest does not close, and one
# left undeclared. The verdict is ``blocked`` while an agent-authorable
# question is still open — the shape that separates "too little is known" from
# "something is wrong", and the only fixture where the verdict gate is what
# refuses the route.
_ONE_DECLARED = """action_surface:
  actions:
    - tool: send_email
      source_id: adk_agent
      effect: external_communication
"""


def test_no_route_when_a_finding_is_what_the_verdict_is_short_of(
    tmp_path: Path,
) -> None:
    """A blocker is a human's call, whatever else the questionnaire still owes."""

    repo = _repo(
        tmp_path / "repo",
        manifest=_MANIFEST + _SOURCE_AUTHORITY + _ONE_DECLARED,
        agent=_BLOCKING_SOURCE,
    )
    envelope = _verify(repo)

    report = _artifact(repo, "report.json")
    questions = report["release_decision"]["evidence_coverage"]["semantic_coverage"][
        "declaration_questions"
    ]["open_questions"]
    assert any(row["authorable_by"] == "coding_agent" for row in questions), (
        "the fixture must still owe a question an agent could draft"
    )

    assert envelope["decision"] == "blocked"
    assert envelope["next_actor"] == "human"
    assert envelope["next_action"]["kind"] != "confirm_declarations"
    assert _artifact(repo, "verifier.json")["fix_task"]["declaration_confirmation"] is None


# A high finding with nothing blocking. It isolates the verdict clause from
# the blocker clause beside it: on the fixture above the blockers are what
# refuse the route, so removing the verdict check would change nothing there.
_ELEVATED = """checks:
  severity_overrides:
    SHIP-DOC-MISSING-DESCRIPTION: high
"""


def test_no_route_when_a_human_is_being_asked_about_a_finding(
    tmp_path: Path,
) -> None:
    """``review_required`` is a question about a finding, not about a blank."""

    repo = _repo(
        tmp_path / "repo",
        manifest=_MANIFEST + _SOURCE_AUTHORITY + _ELEVATED,
    )
    envelope = _verify(repo)

    report = _artifact(repo, "report.json")
    decision = report["release_decision"]
    assert decision["decision"] == "review_required"
    assert decision["blockers"] == []
    questions = decision["evidence_coverage"]["semantic_coverage"][
        "declaration_questions"
    ]["open_questions"]
    assert any(row["authorable_by"] == "coding_agent" for row in questions), (
        "the fixture must still owe a question an agent could draft"
    )

    assert envelope["next_action"]["kind"] != "confirm_declarations"
    assert _artifact(repo, "verifier.json")["fix_task"]["declaration_confirmation"] is None


def test_no_route_without_the_step_the_command_would_take(tmp_path: Path) -> None:
    """A route may not name a step the report it points at does not carry.

    Reached by taking the patches back off a real report, because nothing a
    scan emits can produce that shape today — the row model refuses an
    agent-authorable tag on a template no patch can be built from. The guard is
    for the report a *caller* supplies, and for the day those two drift.
    """

    from agents_shipgate.cli.verify.fix_task import build_fix_task
    from agents_shipgate.schemas.verifier import VerifierCapabilityReview

    repo = _repo(tmp_path / "repo")
    _verify(repo)
    report = _read_report_model(repo)

    def _task(report_model):
        return build_fix_task(
            report_model,
            merge_verdict="insufficient_evidence",
            capability_review=VerifierCapabilityReview(),
            base_ref=None,
            head_ref="HEAD",
            worktree=True,
            report_path=str(repo / "sg-out" / "report.json"),
            repair_subject_available=True,
        )

    assert _task(report).declaration_confirmation is not None

    for gap in report.release_decision.evidence_coverage.evidence_gaps:
        gap.next_action.patch = None
    stripped = _task(report)
    assert stripped.declaration_confirmation is None
    assert stripped.actor == "human"


def test_no_route_without_a_question_the_agent_may_answer(tmp_path: Path) -> None:
    """The dual of the patch guard, and not a restatement of it.

    One asks whether the command has anything to apply; the other asks whether
    the questionnaire agrees that an agent may. Both are true together today,
    and a route that fired on either alone would be describing the other's
    answer.
    """

    from agents_shipgate.cli.verify.fix_task import build_fix_task
    from agents_shipgate.schemas.verifier import VerifierCapabilityReview

    repo = _repo(tmp_path / "repo")
    _verify(repo)
    report = _read_report_model(repo)

    questions = report.release_decision.evidence_coverage.semantic_coverage.declaration_questions
    for row in questions.open_questions:
        row.authorable_by = "human"

    task = build_fix_task(
        report,
        merge_verdict="insufficient_evidence",
        capability_review=VerifierCapabilityReview(),
        base_ref=None,
        head_ref="HEAD",
        worktree=True,
        report_path=str(repo / "sg-out" / "report.json"),
        repair_subject_available=True,
    )
    assert task.declaration_confirmation is None
    assert task.actor == "human"


def test_a_human_task_cannot_carry_a_declaration_confirmation() -> None:
    from agents_shipgate.schemas.verifier import (
        VerifierDeclarationConfirmation,
        VerifierDeclarationQuestion,
        VerifierFixTask,
    )

    confirmation = VerifierDeclarationConfirmation(
        command="agents-shipgate apply-patches --from r.json --kinds declare_action --apply",
        questions=[
            VerifierDeclarationQuestion(
                subject="send_email [adk_agent]",
                dimension="effect",
                answer_path="shipgate.yaml#action_surface.actions[tool='send_email']",
                authorable_by="coding_agent",
            )
        ],
    )
    with pytest.raises(ValidationError):
        VerifierFixTask(
            actor="human",
            safe_to_attempt=False,
            declaration_confirmation=confirmation,
        )


def test_the_envelope_falls_back_when_the_control_routes_elsewhere(
    tmp_path: Path,
) -> None:
    """``declaration_confirmation`` says a route was built, not that it was taken.

    The pointer reconciliation can drop a run's route, and a later branch of
    the control derivation can win. Publishing the richer form off the fix task
    alone would describe a step the control is not asking for, so the join is
    the exact command — and on a mismatch the envelope keeps the plain
    ``repair`` action rather than inventing agreement.
    """

    from agents_shipgate.core.agent_control_envelope import envelope_from_verifier
    from agents_shipgate.schemas.verifier import VerifierArtifact

    repo = _repo(tmp_path / "repo")
    _verify(repo)
    payload = _artifact(repo, "verifier.json")

    matched = envelope_from_verifier(
        VerifierArtifact.model_validate(payload),
        operation="verify",
        source="run",
        exit_code=0,
    )
    assert matched.next_action is not None
    assert matched.next_action.kind == "confirm_declarations"

    payload["fix_task"]["declaration_confirmation"]["command"] += " --dry-run"
    mismatched = envelope_from_verifier(
        VerifierArtifact.model_validate(payload),
        operation="verify",
        source="run",
        exit_code=0,
    )
    assert mismatched.next_action is not None
    assert mismatched.next_action.kind == "repair"


def _read_report_model(repo: Path):
    from agents_shipgate.schemas.report import ReadinessReport

    report = ReadinessReport.model_validate(_artifact(repo, "report.json"))
    assert report.release_decision is not None
    return report


def test_no_route_on_a_ref_bound_run(tmp_path: Path) -> None:
    """A ref-bound rerun would re-scan the commit the edit is not in yet.

    Same precondition the mechanical repair route has always carried
    (``_repair_subject_available``): ``apply-patches`` mutates the checkout, so
    only a working-tree run may advertise it.
    """

    repo = _repo(tmp_path / "repo")
    _git(repo, "checkout", "-qb", "feature")
    (repo / "agent.py").write_text(_AGENT_SOURCE + "\n# tweak\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--base",
            "main",
            "--head",
            "feature",
            "--config",
            "shipgate.yaml",
            "--ci-mode",
            "advisory",
            "--format",
            "control",
            "--out",
            "sg-out",
        ],
    )
    envelope = json.loads(result.output[result.output.index("{") :])

    assert envelope["decision"] == "insufficient_evidence"
    assert envelope["next_action"]["actor"] == "human"


# --- the loop closes --------------------------------------------------------


def test_the_loop_ends_with_the_agent_out_of_moves_and_a_named_residue(
    tmp_path: Path,
) -> None:
    """One turn of the loop, and what is left of it.

    Answering the drafted question moves the verdict off "I do not know enough"
    — here all the way to ``blocked``, because declaring the outward
    communication is what makes its missing audit control judgeable, which is
    the point of asking at all. What matters for the route is that the agent is
    no longer holding it, the drafted question is scored as answered, and the
    one still open is the one it was told it could not write.
    """

    repo = _repo(tmp_path / "repo")
    envelope = _verify(repo)
    assert envelope["next_action"]["kind"] == "confirm_declarations"
    owed = [q for q in envelope["next_action"]["questions"] if q["authorable_by"] == "human"]
    assert [q["dimension"] for q in owed] == ["authority"]

    result = runner.invoke(
        app,
        [
            "apply-patches",
            "--from",
            str(repo / "sg-out" / "report.json"),
            "--kinds",
            "declare_action",
            "--confidence",
            "high",
            "--apply",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "effect: external_communication" in (repo / "shipgate.yaml").read_text(
        encoding="utf-8"
    )

    after = _verify(repo)
    assert after["decision"] != "insufficient_evidence"
    assert after["next_actor"] == "human"
    assert after["next_action"]["kind"] != "confirm_declarations"
    assert after["permissions"]["merge"] is False

    counter = _artifact(repo, "report.json")["release_decision"]["evidence_coverage"][
        "semantic_coverage"
    ]["declaration_questions"]
    assert (counter["total"], counter["answered"]) == (2, 1)
    assert [row["answer_path"] for row in counter["open_questions"]] == [
        q["answer_path"] for q in owed
    ]


def test_a_ref_bound_run_never_publishes_an_archive_path(tmp_path: Path) -> None:
    """A patch target is receipt-bound evidence; a temp directory is not.

    A ref-bound run scans an *archived* checkout under a randomly named
    temporary directory. A declaration patch names its target relative to
    ``manifest_dir``, so it reads the same on any machine and in any run —
    and the artifacts that embed the row (the packet, the SARIF file, a cached
    base scan) carry no path that will have ceased to exist by the time
    somebody opens them.
    """

    repo = _repo(tmp_path / "repo")
    _git(repo, "checkout", "-qb", "feature")
    (repo / "agent.py").write_text(_AGENT_SOURCE + "\n# tweak\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--base",
            "main",
            "--head",
            "feature",
            "--config",
            "shipgate.yaml",
            "--ci-mode",
            "advisory",
            "--format",
            "json",
            "--out",
            "sg-out",
        ],
    )
    assert result.exit_code in (0, 1), result.output

    report = _artifact(repo, "report.json")
    assert report["manifest_dir"] == str(repo.resolve())
    patched = [
        gap["next_action"]["patch"]
        for gap in report["release_decision"]["evidence_coverage"]["evidence_gaps"]
        if gap["next_action"].get("patch")
    ]
    assert patched, "the fixture should still publish a drafted declaration"
    for patch in patched:
        assert patch["target_path"] == "shipgate.yaml"

    # The same row travels into the artifacts that leave this machine. None of
    # them may carry an absolute path, and the temp root is the one that would
    # have been wrong even on this machine, ten seconds later.
    for name in ("packet.json", "report.sarif", "verification-base-report.json"):
        text = (repo / "sg-out" / name).read_text(encoding="utf-8")
        assert "agents-shipgate-verify-" not in text, name


# --- the envelope's own limits ----------------------------------------------


def test_the_question_list_is_a_prefix_and_says_so() -> None:
    from agents_shipgate.schemas.agent_control_envelope import (
        MAX_ENVELOPE_QUESTIONS,
        ConfirmDeclarationsAction,
        EnvelopeDeclarationQuestion,
    )

    rows = [
        EnvelopeDeclarationQuestion(
            subject=f"tool_{index}",
            dimension="effect",
            answer_path=f"shipgate.yaml#action_surface.actions[tool='tool_{index}']",
            authorable_by="coding_agent",
        )
        for index in range(MAX_ENVELOPE_QUESTIONS)
    ]
    action = ConfirmDeclarationsAction(
        kind="confirm_declarations",
        command="agents-shipgate apply-patches --from r.json --kinds declare_action --apply",
        expects="e",
        why="w",
        questions=rows,
        agent_authorable=40,
        human_authorable=7,
    )
    assert len(action.questions) < action.agent_authorable + action.human_authorable

    with pytest.raises(ValidationError):
        ConfirmDeclarationsAction(
            kind="confirm_declarations",
            command="c",
            expects="e",
            why="w",
            questions=rows,
            agent_authorable=1,
            human_authorable=0,
        )

    with pytest.raises(ValidationError):
        ConfirmDeclarationsAction(
            kind="confirm_declarations",
            command="c",
            expects="e",
            why="w",
            questions=[
                *rows,
                EnvelopeDeclarationQuestion(
                    subject="one too many",
                    dimension="effect",
                    answer_path="p",
                    authorable_by="coding_agent",
                ),
            ],
            agent_authorable=40,
            human_authorable=7,
        )


def test_a_declaration_route_cannot_be_published_by_setup() -> None:
    """It is projected from a release decision, and only ``verify`` reaches one."""

    from agents_shipgate.core.agent_control import derive_agent_control
    from agents_shipgate.core.agent_control_envelope import (
        project_agent_control_envelope,
    )
    from agents_shipgate.schemas.agent_control import CodingAgentCommandAction
    from agents_shipgate.schemas.agent_control_envelope import (
        ConfirmDeclarationsAction,
        EnvelopeDeclarationQuestion,
    )

    route = ConfirmDeclarationsAction(
        kind="confirm_declarations",
        command="agents-shipgate apply-patches --from r.json --kinds declare_action --apply",
        expects="e",
        why="w",
        questions=[
            EnvelopeDeclarationQuestion(
                subject="send_email",
                dimension="effect",
                answer_path="shipgate.yaml#action_surface.actions[tool='send_email']",
                authorable_by="coding_agent",
            )
        ],
        agent_authorable=1,
        human_authorable=0,
    )
    control = derive_agent_control(
        reason="r",
        next_action=CodingAgentCommandAction(kind="repair", command="c", why="w"),
        verify_required=True,
    )
    # ``input_id`` is supplied, and the message is matched, because neither is
    # optional for this to be a test of *this* rule: without the id an
    # unrelated setup-provenance validator raises first, and a bare
    # ``pytest.raises(ValueError)`` then passes whether or not the route is
    # guarded at all. Found by perturbing both layers and watching the test
    # stay green.
    with pytest.raises(ValueError, match="verify"):
        project_agent_control_envelope(
            control=control,
            operation="init",
            source="run",
            execution="succeeded",
            exit_code=0,
            decision="setup_incomplete",
            decision_source="setup",
            input_id="sha256:" + "0" * 64,
            declaration_route=route,
        )


def test_a_repair_route_still_cannot_invent_a_command(tmp_path: Path) -> None:
    """The widened fix-task invariant is wider, not absent."""

    from agents_shipgate.schemas.verifier import VerifierArtifact

    repo = _repo(tmp_path / "repo")
    _verify(repo)
    payload = _artifact(repo, "verifier.json")

    VerifierArtifact.model_validate(payload)

    payload["control"]["next_action"]["command"] = "agents-shipgate verify --somewhere-else"
    payload["control"]["allowed_next_commands"] = [
        "agents-shipgate verify --somewhere-else"
    ]
    with pytest.raises(ValidationError):
        VerifierArtifact.model_validate(payload)
