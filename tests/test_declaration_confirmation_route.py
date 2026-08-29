"""#410 §D — ``next_action.kind: confirm_declarations``.

The route is what turns a questionnaire into a loop an agent can finish. It
fires only where a declaration is what the verdict is short of, it authorizes
only publishing (never merge), and when it is done the remaining questions are
named rather than summarised as "human review required".

Everything here is asserted on the published envelope, because that is the
surface an agent actually reads.
"""

from __future__ import annotations

import hashlib
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


# --- the route is reachable, and says so when it is not ---------------------


def _adopting_repo(path: Path, *, extra_bytes: int = 0) -> Path:
    """A first adoption: the manifest is written but not yet committed."""

    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.test")
    _git(path, "config", "user.name", "Test")
    (path / "agent.py").write_text(_AGENT_SOURCE, encoding="utf-8")
    (path / ".gitignore").write_text("sg-out/\n.agents-shipgate/\n", encoding="utf-8")
    if extra_bytes:
        (path / "uv.lock").write_bytes(b"x" * extra_bytes)
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "base")
    (path / "shipgate.yaml").write_text(_MANIFEST, encoding="utf-8")
    return path


def test_the_route_is_offered_on_a_first_adoption(tmp_path: Path) -> None:
    """The run with every question open is the one that most needs it."""

    repo = _adopting_repo(tmp_path / "repo")
    envelope = _verify(repo, "--no-base")

    assert envelope["next_action"]["kind"] == "confirm_declarations"
    review = _artifact(repo, "verifier.json")["capability_review"]
    assert review["trust_root_touched"] is True
    assert review["policy_weakened"] is False


def test_one_large_file_does_not_withdraw_the_route(tmp_path: Path) -> None:
    """#429, manifestation 1, at its actual cause.

    Proving that no file in the tree parses as a manifest under any name means
    reading the tree, and the read stops at the first blob past its
    per-candidate bound. One lockfile is enough — ``google/adk-samples``, the
    walk target, carries 35 — so ``policy_weakened`` stays fail-closed true and
    the route was refused on it, in essentially every real repository.

    The flag is *right* to stay raised, and it still does: what changed is that
    the route no longer depends on it where this diff introduces the very gate
    it is judged by. That is the assertion pair here — the probe still cannot
    answer, and the route is offered anyway.
    """

    from agents_shipgate.cli.verify.git import _MAX_MANIFEST_BYTES

    repo = _adopting_repo(tmp_path / "repo", extra_bytes=_MAX_MANIFEST_BYTES + 1)
    envelope = _verify(repo, "--no-base")

    assert envelope["next_action"]["kind"] == "confirm_declarations"
    review = _artifact(repo, "verifier.json")["capability_review"]
    assert review["policy_weakened"] is True
    assert review["policy_weakening_proven"] is False


def test_a_withheld_route_produces_the_cause_it_acted_on(tmp_path: Path) -> None:
    """Its inputs are published either way, so silence is the wrong answer.

    ``report.json`` carries every open question with ``authorable_by``
    resolved whether or not the route is. An agent that can see a row is its
    own to write, and a control that offers nothing and explains nothing, is
    being invited to edit the trust root without the route (#429).

    Asserted on the cause the route itself produced rather than on this run's
    headline. The clause travels as headline context under a shared 400-byte
    budget — this fixture spends 322 of it on the verdict, the target and the
    gap provenance — so where it *lands* is a fixture property.
    ``test_a_weakening_edit_is_still_refused_the_route`` covers a run with room
    for it.
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

    report = _artifact(repo, "report.json")
    questions = report["release_decision"]["evidence_coverage"]["semantic_coverage"][
        "declaration_questions"
    ]["open_questions"]
    drafts = [row for row in questions if row["authorable_by"] == "coding_agent"]
    assert drafts, "the fixture must still publish a question an agent could draft"

    assert envelope["next_action"]["kind"] != "confirm_declarations"

    from agents_shipgate.cli.verify.fix_task import declaration_route
    from agents_shipgate.schemas.verifier import VerifierArtifact

    verifier = VerifierArtifact.model_validate(_artifact(repo, "verifier.json"))
    _route, withheld = declaration_route(
        _read_report_model(repo),
        capability_review=verifier.capability_review,
        merge_verdict=verifier.merge_verdict,
        report_path=str(repo / "sg-out" / "report.json"),
        # The one precondition a ref-bound run fails, and the reason this
        # fixture is ref-bound at all.
        repair_subject_available=False,
    )
    assert withheld == (
        f"{len(drafts)} declaration(s) this scan could draft are withheld: this run "
        "reads committed refs, not a worktree to write into."
    )


def test_nothing_is_named_when_nothing_was_withheld(tmp_path: Path) -> None:
    """The dual. A run with no question an agent may draft withheld no route.

    Without this, "withheld" would print on every human-routed run in the
    product, and an agent would read a refusal into a route that never existed.
    """

    published = _verify(_repo(tmp_path / "offered"))
    assert published["next_action"]["kind"] == "confirm_declarations"
    assert "are withheld" not in published["reason"]

    humans_only = _verify(_repo(tmp_path / "humans", agent=_NEUTRAL_SOURCE))
    assert humans_only["next_action"]["kind"] == "review"
    assert "are withheld" not in humans_only["reason"]


def test_a_run_that_never_reached_the_route_names_no_cause(tmp_path: Path) -> None:
    """A verdict of ``mergeable`` refused nothing, and must say nothing.

    ``build_fix_task`` returns no task at all when the PR is mergeable, and on
    the base-recovery paths that stop before any decision. Asking the route for
    a cause there gets the first condition it happens to check — "this run has
    a finding to answer" — printed on a run with no finding at all.
    """

    from agents_shipgate.cli.verify.orchestrator import _withheld_declaration_note
    from agents_shipgate.schemas.verifier import (
        VerifierCapabilityReview,
        VerifierFixTask,
    )

    repo = _repo(tmp_path / "repo")
    _verify(repo)
    report = _read_report_model(repo)
    arguments = {
        "capability_review": VerifierCapabilityReview(),
        "merge_verdict": "mergeable",
        "report_path": str(repo / "sg-out" / "report.json"),
        "repair_subject_available": True,
        "configured_gate_introduced": False,
    }

    human_task = VerifierFixTask(actor="human", safe_to_attempt=False)
    assert _withheld_declaration_note(report, fix_task=human_task, **arguments)
    assert _withheld_declaration_note(report, fix_task=None, **arguments) == []


def test_a_retained_gate_under_another_name_still_refuses_the_adoption_claim(
    tmp_path: Path,
) -> None:
    """The reviewer's shape, and exactly how far the route's exemption goes.

    A base that keeps an operational manifest under an arbitrary name, padded
    past the probe's read bound, must not be talked into "nothing existed to
    weaken": the probe answers "cannot prove", ``policy_weakened`` stays
    fail-closed true and the wording stays conservative — the same as for the
    identical gate below the bound, so file size does no semantic work.

    The route is still offered, and that is the deliberate line. The gate this
    run is judged by is ``shipgate.yaml``, which this diff introduces; whatever
    ``old-gate.json`` is, it is not a prior version of *that* gate, so nothing
    here could have loosened it. The agent may draft blanks into a manifest a
    person still merges; it may not merge, and the verdict is unchanged.
    """

    from agents_shipgate.cli.verify.git import _MAX_MANIFEST_BYTES

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test")
    (repo / "agent.py").write_text(_AGENT_SOURCE, encoding="utf-8")
    (repo / ".gitignore").write_text("sg-out/\n.agents-shipgate/\n", encoding="utf-8")
    (repo / "old-gate.json").write_text(
        _MANIFEST + ("# pad\n" * (_MAX_MANIFEST_BYTES // 3)), encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base keeps an oversize custom-named gate")
    assert (repo / "old-gate.json").stat().st_size > _MAX_MANIFEST_BYTES
    (repo / "shipgate.yaml").write_text(_MANIFEST, encoding="utf-8")

    envelope = _verify(repo, "--no-base")
    review = _artifact(repo, "verifier.json")["capability_review"]

    assert review["policy_weakened"] is True
    assert review["policy_weakening_proven"] is False
    assert "no base policy was available to prove" in _artifact(
        repo, "verifier.json"
    )["headline"]
    assert envelope["next_action"]["kind"] == "confirm_declarations"


_ORG_PACK = """id: org-rules
name: Org rules
version: "1.0"
rules:
  - id: ORG-NO-EMAIL
    severity: critical
    title: Outward email is not permitted
    when:
      effect: external_communication
"""

_EMPTIED_PACK = """id: org-rules
name: Org rules
version: "1.0"
rules: []
"""


def test_an_introduction_that_also_empties_a_referenced_pack_is_refused(
    tmp_path: Path,
) -> None:
    """The route's exemption must not survive a diff that moved another gate.

    A base holding a critical ``org-rules.yml``, a diff that adds
    ``shipgate.yaml`` *referencing* it and empties it in the same breath: the
    fixed policy-surface globs cannot see a pack at an arbitrary
    ``checks.policy_packs[].path``, so "only the configured manifest changed"
    was false while reading true (#429 review). The packs are now taken from
    the run's own record of what it loaded.
    """

    from agents_shipgate.cli.verify.git import _MAX_MANIFEST_BYTES

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test")
    (repo / "agent.py").write_text(_AGENT_SOURCE, encoding="utf-8")
    (repo / ".gitignore").write_text("sg-out/\n.agents-shipgate/\n", encoding="utf-8")
    # Forces the whole-tree probe to fail closed, which is what makes the
    # route depend on the introduction fact at all.
    (repo / "uv.lock").write_bytes(b"x" * (_MAX_MANIFEST_BYTES + 1))
    (repo / "org-rules.yml").write_text(_ORG_PACK, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base carries a critical org pack")

    (repo / "shipgate.yaml").write_text(
        _MANIFEST + "checks:\n  policy_packs:\n    - path: org-rules.yml\n",
        encoding="utf-8",
    )
    (repo / "org-rules.yml").write_text(_EMPTIED_PACK, encoding="utf-8")

    envelope = _verify(repo, "--no-base")
    report = _artifact(repo, "report.json")

    assert [pack["path"] for pack in report["loaded_policy_packs"]] == ["org-rules.yml"]
    assert _artifact(repo, "verifier.json")["capability_review"]["policy_weakened"] is (
        True
    )
    assert envelope["next_action"]["kind"] != "confirm_declarations"
    assert envelope["next_actor"] == "human"


def test_a_staged_rename_onto_the_configured_path_is_refused(tmp_path: Path) -> None:
    """Move-and-loosen, hidden in the index under ``--base``.

    ``base...head`` cannot see a staged rename, and the run evaluates the
    worktree, so the committed range was the wrong comparison to ask. The
    removal check now asks the comparison the run actually evaluated, and asks
    it suffix-agnostically — a gate may be ``old-gate.json`` (#429 review).
    """

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test")
    (repo / "agent.py").write_text(_AGENT_SOURCE, encoding="utf-8")
    (repo / ".gitignore").write_text("sg-out/\n.agents-shipgate/\n", encoding="utf-8")
    (repo / "old-gate.yml").write_text(_MANIFEST + _STRICTER, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base carries an existing gate")

    _git(repo, "mv", "old-gate.yml", "shipgate.yaml")
    (repo / "shipgate.yaml").write_text(_MANIFEST + _LOOSENED, encoding="utf-8")
    _git(repo, "add", "-A")

    envelope = _verify(repo, "--base", "main")

    assert envelope["next_action"]["kind"] != "confirm_declarations"
    assert envelope["next_actor"] == "human"


def test_a_json_named_gate_rename_is_refused(tmp_path: Path) -> None:
    """The dual: the removal check may not be a suffix list.

    ``removes_a_yaml_file`` recognizes only ``.yaml``/``.yml``, and a
    configured manifest may be JSON or have no suffix at all.
    """

    from agents_shipgate.cli.verify.orchestrator import _configured_gate_introduced

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test")
    (repo / "old-gate.json").write_text(_MANIFEST, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base carries a json-named gate")
    _git(repo, "mv", "old-gate.json", "new-gate.json")
    _git(repo, "commit", "-qm", "move it")

    assert (
        _configured_gate_introduced(
            git_root=repo,
            config_relative=Path("new-gate.json"),
            base_status="missing_manifest",
            base="HEAD~1",
            head="HEAD",
            worktree_ref=None,
            changed_files=["new-gate.json", "old-gate.json"],
        )
        is False
    )


def test_a_blocked_report_names_the_blocker_not_the_verdict(tmp_path: Path) -> None:
    """Order among the causes, which decides which one is ever emitted.

    A blocked report satisfies the verdict mismatch too, so asking that first
    made "a blocker is open" a cause the code could not reach — a documented
    example no run produces.
    """

    from agents_shipgate.cli.verify.fix_task import declaration_route
    from agents_shipgate.schemas.verifier import VerifierCapabilityReview

    repo = _repo(tmp_path / "repo")
    _verify(repo)
    report = _read_report_model(repo)
    decision = report.release_decision
    decision.decision = "blocked"
    decision.blockers = [*decision.review_items[:1]] or decision.blockers

    _route, withheld = declaration_route(
        report,
        capability_review=VerifierCapabilityReview(),
        merge_verdict="blocked",
        report_path=str(repo / "sg-out" / "report.json"),
        repair_subject_available=True,
    )
    assert withheld is not None
    assert "a blocker is open" in withheld


_STRICTER = """checks:
  severity_overrides:
    SHIP-DOC-MISSING-DESCRIPTION: high
"""

_LOOSENED = """checks:
  severity_overrides:
    SHIP-DOC-MISSING-DESCRIPTION: low
"""


def test_a_weakening_edit_is_still_refused_the_route(tmp_path: Path) -> None:
    """The regression #429 asks for: relaxing *this* is not on the table.

    A diff that loosens the gate keeps the route refused and the decision a
    person's. The state stays ``review_publishable`` rather than the total
    stop, which is #335's design and unrelated to the route: the change was
    read, so pushing it to a PR is how a human comes to see it, and ``merge``
    is denied throughout.
    """

    repo = _repo(tmp_path / "repo", manifest=_MANIFEST + _STRICTER)
    _git(repo, "checkout", "-qb", "feature")
    (repo / "shipgate.yaml").write_text(_MANIFEST + _LOOSENED, encoding="utf-8")

    envelope = _verify(repo, "--base", "main")

    review = _artifact(repo, "verifier.json")["capability_review"]
    assert review["policy_weakened"] is True
    assert review["policy_weakening_proven"] is True
    assert envelope["next_actor"] == "human"
    assert envelope["next_action"]["kind"] != "confirm_declarations"
    assert envelope["permissions"]["merge"] is False
    assert envelope["permissions"]["report_complete"] is False
    assert _artifact(repo, "verifier.json")["fix_task"]["declaration_confirmation"] is (
        None
    )
    assert "weakens the release policy that evaluates it" in envelope["reason"]

    # Both context sentences are on this run, and the order between them is
    # deliberate: ``_fit_sentences`` keeps a prefix of whole sentences, so
    # second is the one a long enough headline deletes. Gap provenance is a
    # fact about the release decision and renders into the PR comment a person
    # reads (#433); the withholding is an explanation for an agent that has the
    # human route either way, so it is the right one to lose under pressure.
    reason = envelope["reason"]
    assert "are withheld" in reason
    assert "pre-existing on the base" in reason
    assert reason.index("pre-existing on the base") < reason.index("are withheld")


def test_an_unprovable_direction_does_not_claim_a_weakening(tmp_path: Path) -> None:
    """The refusal is the same; only the sentence may differ.

    ``policy_weakened`` stays raised when nothing could be compared, and this
    route is refused on that flag. Saying "this weakens the gate" about a
    comparison that never ran states a fact the run does not have.
    """

    from agents_shipgate.cli.verify.fix_task import declaration_route
    from agents_shipgate.schemas.verifier import VerifierCapabilityReview

    repo = _repo(tmp_path / "repo")
    _verify(repo)
    report = _read_report_model(repo)

    _route, unproven = declaration_route(
        report,
        capability_review=VerifierCapabilityReview(policy_weakened=True),
        merge_verdict="insufficient_evidence",
        report_path=str(repo / "sg-out" / "report.json"),
        repair_subject_available=True,
    )
    _route, proven = declaration_route(
        report,
        capability_review=VerifierCapabilityReview(
            policy_weakened=True,
            policy_weakening_proven=True,
        ),
        merge_verdict="insufficient_evidence",
        report_path=str(repo / "sg-out" / "report.json"),
        repair_subject_available=True,
    )

    assert unproven is not None and "no base policy proved" in unproven
    assert proven is not None and "weakens the release policy" in proven


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


def test_the_route_says_to_rerun_before_it_says_to_publish(tmp_path: Path) -> None:
    """Order, and it is the counter-intuitive one.

    The command edits ``shipgate.yaml``, so the moment it succeeds this control
    is stale — ``agent control`` refuses with ``workspace_changed`` and the v20
    refresh rule requires that read before any commit, push, or PR update. The
    permissions printed beside the route were computed against a manifest that
    no longer exists, so a route that told the agent to spend them was naming
    an illegal step (#429 review).
    """

    repo = _repo(tmp_path / "repo")
    envelope = _verify(repo)
    action = envelope["next_action"]

    assert action["kind"] == "confirm_declarations"
    why = action["why"]
    assert why.index("re-run verification") < why.index("committing")
    # And the typed post-condition promises the write and the supersession,
    # never a commit this control cannot authorize once the command has run.
    assert "control superseded" in action["expects"]
    assert "committed to this branch" not in action["expects"]

    # The same sentence, from the one place it is written — and equality is
    # also what keeps it inside ``MAX_ENVELOPE_PROSE_BYTES``: a longer version
    # arrives here truncated, with the ordering clause cut off the end.
    fix_task = _artifact(repo, "verifier.json")["fix_task"]
    assert fix_task["instructions"][0] == why


def test_the_control_this_route_publishes_is_superseded_by_its_own_command(
    tmp_path: Path,
) -> None:
    """The fact the ordering rests on, asserted rather than asserted about.

    Reproduced end to end: the route is published with commit/push true, its
    exact command succeeds, and the mandatory control refresh then refuses.
    Any instruction that spends those permissions after the command has run is
    naming a step the protocol rejects.
    """

    from agents_shipgate.cli.current_workspace import live_workspace
    from agents_shipgate.core.current_control import (
        CurrentControlUnavailable,
        read_current_control,
    )
    from agents_shipgate.schemas.current_control import VERIFIER_ARTIFACT_KEY

    repo = _repo(tmp_path / "repo")
    envelope = _verify(repo)
    assert envelope["next_action"]["kind"] == "confirm_declarations"
    assert envelope["permissions"]["commit"] is True

    reports = repo / "sg-out"

    def _read():
        # Exactly how `agents-shipgate agent control` reads it, capture and
        # all: the capture is what a currency refusal can hand back.
        return read_current_control(
            reports,
            live=lambda: live_workspace(repo, reports),
            capture=(VERIFIER_ARTIFACT_KEY,),
        )

    assert _read().pointer.control.state == "agent_action_required"

    result = runner.invoke(
        app,
        [
            "apply-patches",
            "--from",
            str(reports / "report.json"),
            "--kinds",
            "declare_action",
            "--confidence",
            "high",
            "--apply",
        ],
    )
    assert result.exit_code == 0, result.output

    with pytest.raises(CurrentControlUnavailable) as refusal:
        _read()
    assert refusal.value.reason == "workspace_changed"

    # And the refusal carries the *validated* set, so the recovery it names is
    # the producing run's own exact local rerun rather than a default PR verify
    # that would scan committed HEAD and miss the edit that superseded it
    # (#429 review).
    from agents_shipgate.cli.agent_interface import _superseded_recovery_command

    recovery = _superseded_recovery_command(
        refusal.value, workspace=repo, reports_dir=reports
    )
    assert recovery == _artifact(repo, "verifier.json")["fix_task"][
        "verification_command"
    ]
    # The three things the hard-coded PR verify got wrong for this route.
    assert "--base origin/main" not in recovery
    assert "--head HEAD" not in recovery
    assert "--out sg-out" in recovery
    assert "--config shipgate.yaml" in recovery


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


# --- the continuation ------------------------------------------------------


def _apply(repo: Path) -> None:
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


def test_the_drafted_proposal_reaches_review(tmp_path: Path) -> None:
    """The loop's last step, and the one that was missing.

    Applying the route's own command makes the declared risk judgeable, so the
    fresh decision is ``blocked`` — and a blocked decision authorizes nothing,
    which left the proposal Shipgate drafted unable to reach the person the
    route exists to hand it to (#429 review). The receipt closes it: the run
    is publish-only, so the change reaches a PR, and ``merge`` is exactly as
    denied as it was.
    """

    repo = _repo(tmp_path / "repo")
    assert _verify(repo)["next_action"]["kind"] == "confirm_declarations"
    _apply(repo)

    after = _verify(repo)
    assert after["decision"] == "blocked"
    assert after["control_state"] == "review_publishable"
    assert after["permissions"] == {
        "edit": True,
        "commit": True,
        "push": True,
        "update_pr": True,
        "merge": False,
        "report_complete": False,
    }
    assert after["human_review"]["required"] is True

    receipt = json.loads(
        (repo / "sg-out" / "declaration-continuation.json").read_text(encoding="utf-8")
    )
    assert receipt["schema_version"] == "shipgate.declaration_continuation/v1"
    assert receipt["manifest_path"] == "shipgate.yaml"
    assert [row["declaration"]["effect"] for row in receipt["applied"]] == [
        "external_communication"
    ]


def test_without_the_receipt_a_blocked_run_authorizes_nothing(tmp_path: Path) -> None:
    """The carve-out is the receipt's, not the verdict's.

    Same manifest, same blocked decision, no receipt: the total stop that every
    other blocked run gets. Nothing about ``blocked`` changed in general.
    """

    repo = _repo(tmp_path / "repo")
    _verify(repo)
    _apply(repo)
    (repo / "sg-out" / "declaration-continuation.json").unlink()

    after = _verify(repo)
    assert after["decision"] == "blocked"
    assert after["control_state"] == "human_review_required"
    assert after["permissions"]["commit"] is False


def test_a_receipt_stops_describing_a_manifest_that_moved_again(
    tmp_path: Path,
) -> None:
    """Both digests are load-bearing, and the *after* one is what expires.

    A receipt that kept authorizing publication after a second, unrelated edit
    would be authorizing that edit too — which is the whole point of pinning
    the bytes on both sides rather than recording that an apply happened.
    """

    repo = _repo(tmp_path / "repo")
    _verify(repo)
    _apply(repo)
    assert _verify(repo)["control_state"] == "review_publishable"

    manifest = repo / "shipgate.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n# later\n", encoding="utf-8")
    assert _verify(repo)["control_state"] == "human_review_required"


def test_a_forged_receipt_cannot_publish_a_loosened_gate(tmp_path: Path) -> None:
    """The receipt is provenance, not a signature, and this is the bound.

    Anyone who can write the manifest can write a receipt whose digests match
    it. What they cannot do is make the delta parse as declarations: the two
    manifests are compared, and anything but added ``action_surface.actions``
    rows refuses. So a forged receipt buys putting a proposal in front of a
    person — which is what it is for — and never a loosened gate.
    """

    repo = _repo(tmp_path / "repo", manifest=_MANIFEST + _STRICTER)
    _verify(repo)
    _apply(repo)

    manifest = repo / "shipgate.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "SHIP-DOC-MISSING-DESCRIPTION: high", "SHIP-DOC-MISSING-DESCRIPTION: low"
        ),
        encoding="utf-8",
    )
    receipt_path = repo / "sg-out" / "declaration-continuation.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["manifest_sha256_after"] = hashlib.sha256(
        manifest.read_bytes()
    ).hexdigest()
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    after = _verify(repo)
    assert after["control_state"] == "human_review_required"
    assert after["permissions"]["commit"] is False


def test_a_ref_bound_run_never_honours_a_continuation(tmp_path: Path) -> None:
    """A committed-ref run has nothing for a receipt to be about.

    It evaluates objects, not a working tree, so the uncommitted manifest the
    receipt pins is not what it read.
    """

    from agents_shipgate.cli.verify.orchestrator import _declaration_continuation_holds

    repo = _repo(tmp_path / "repo")
    _verify(repo)
    _apply(repo)
    assert (
        _declaration_continuation_holds(
            git_root=repo,
            config_path=repo / "shipgate.yaml",
            config_relative=Path("shipgate.yaml"),
            out_dir=repo / "sg-out",
            comparison_ref=None,
            gate_introduced=False,
        )
        is False
    )


def test_a_published_continuation_validates_against_its_own_schemas(
    tmp_path: Path,
) -> None:
    """First-party output must satisfy the contract it advertises.

    The Pydantic validators learned the exception; the JSON-Schema ``allOf``
    conditions had not, so a real continuation emitted three artifacts that
    each failed their own newly published schema — the one thing a version
    bump is supposed to make impossible (#429 review).
    """

    from jsonschema import Draft202012Validator

    repo = _repo(tmp_path / "repo")
    _verify(repo)
    _apply(repo)
    after = _verify(repo)
    assert after["control_state"] == "review_publishable"

    root = Path(__file__).resolve().parent.parent
    for artifact, schema in (
        ("verifier.json", "verifier-schema.v0.15.json"),
        ("agent-handoff.json", "agent-handoff-schema.v8.json"),
        ("verify-run.json", "verify-run-schema.v5.json"),
    ):
        payload = json.loads((repo / "sg-out" / artifact).read_text(encoding="utf-8"))
        validator = Draft202012Validator(
            json.loads((root / "docs" / schema).read_text(encoding="utf-8"))
        )
        assert not list(validator.iter_errors(payload)), (
            artifact,
            [error.message for error in validator.iter_errors(payload)][:2],
        )


def test_a_blocked_run_without_a_continuation_still_fails_the_schema(
    tmp_path: Path,
) -> None:
    """The condition is narrowed, not removed.

    A payload that publishes on a blocked decision *without* the flag — every
    pre-v0.15 artifact included, since they omit the field entirely — must
    still be refused by the published schema.
    """

    from jsonschema import Draft202012Validator

    repo = _repo(tmp_path / "repo")
    _verify(repo)
    _apply(repo)
    _verify(repo)

    root = Path(__file__).resolve().parent.parent
    payload = json.loads((repo / "sg-out" / "verifier.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(
        json.loads((root / "docs" / "verifier-schema.v0.15.json").read_text("utf-8"))
    )
    payload.pop("declaration_continuation")
    assert list(validator.iter_errors(payload))


def test_a_first_adoption_can_still_publish_its_proposal(tmp_path: Path) -> None:
    """There is no earlier version of a manifest this diff introduces.

    Reading a before-digest out of the comparison ref therefore found nothing
    and refused, which left the advertised apply/rerun path immediately blocked
    on the very run the route was built for. What carries "nothing could have
    been loosened" here is the introduction proof — there was no gate
    (#429 review).
    """

    repo = _adopting_repo(tmp_path / "repo")
    assert _verify(repo, "--no-base")["next_action"]["kind"] == "confirm_declarations"
    _apply(repo)

    # The receipt records the pre-apply bytes it saw; what the reader does
    # *not* do is treat them as an anchor, because nothing committed carries
    # them on an adoption.
    assert (repo / "sg-out" / "declaration-continuation.json").is_file()

    after = _verify(repo, "--no-base")
    assert after["decision"] == "blocked"
    assert after["control_state"] == "review_publishable"
    assert after["permissions"]["commit"] is True


def test_an_absent_before_state_needs_the_introduction_proof(tmp_path: Path) -> None:
    """The dual, and the reason the absent case is not a hole.

    "No earlier bytes" is exactly what an *unrelated* uncommitted manifest also
    looks like. Without a proven introduction beside it, the receipt is
    describing a change nothing established, and the run stops.
    """

    from agents_shipgate.cli.verify.orchestrator import _declaration_continuation_holds

    repo = _adopting_repo(tmp_path / "repo")
    _verify(repo, "--no-base")
    _apply(repo)
    arguments = {
        "git_root": repo,
        "config_path": repo / "shipgate.yaml",
        "config_relative": Path("shipgate.yaml"),
        "out_dir": repo / "sg-out",
        "comparison_ref": "HEAD",
    }
    assert _declaration_continuation_holds(**arguments, gate_introduced=True) is True
    assert _declaration_continuation_holds(**arguments, gate_introduced=False) is False


def test_the_continuation_survives_a_scoped_manifest(tmp_path: Path) -> None:
    """Two coordinate systems, and the receipt speaks the applier's.

    ``manifest_path`` is recorded relative to ``report.manifest_dir``, so a
    scoped ``services/closer/shipgate.yaml`` is written as ``shipgate.yaml``.
    Compared as a repository path it matched nothing, and the exact apply/rerun
    route stripped every publication permission on the one repository shape
    monorepo adoption produces (#429 review).
    """

    repo = tmp_path / "repo"
    (repo / "services" / "closer").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test")
    (repo / "services" / "closer" / "agent.py").write_text(_AGENT_SOURCE, encoding="utf-8")
    (repo / "services" / "closer" / "shipgate.yaml").write_text(_MANIFEST, encoding="utf-8")
    (repo / ".gitignore").write_text("sg-out/\n.agents-shipgate/\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")

    def _run(*extra: str) -> dict:
        result = runner.invoke(
            app,
            [
                "verify",
                "--workspace",
                str(repo),
                "--config",
                "services/closer/shipgate.yaml",
                "--ci-mode",
                "advisory",
                "--format",
                "control",
                "--out",
                "sg-out",
                "--no-base",
                *extra,
            ],
        )
        assert result.exit_code in (0, 1), result.output
        return json.loads(result.output[result.output.index("{") :])

    assert _run()["next_action"]["kind"] == "confirm_declarations"
    applied = runner.invoke(
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
    assert applied.exit_code == 0, applied.output
    receipt = json.loads(
        (repo / "sg-out" / "declaration-continuation.json").read_text(encoding="utf-8")
    )
    assert receipt["manifest_path"] == "shipgate.yaml"

    after = _run()
    assert after["decision"] == "blocked"
    assert after["control_state"] == "review_publishable"
    assert after["permissions"]["commit"] is True
    assert after["permissions"]["merge"] is False


def test_a_relative_recorded_path_is_never_resolved_against_the_process(
    tmp_path: Path,
) -> None:
    """The bug under the scoped failure, isolated.

    ``Path("shipgate.yaml").resolve()`` answers against the *process* directory.
    Run from the repository root that relativized cleanly, so a scoped
    receipt's path silently became a root-level one — the check then passed on
    a path nobody had written and failed on the real one.
    """

    import os

    from agents_shipgate.cli.verify.orchestrator import _repository_relative

    repo = tmp_path / "repo"
    (repo / "services" / "closer").mkdir(parents=True)
    previous = os.getcwd()
    os.chdir(repo)
    try:
        assert (
            _repository_relative(
                "shipgate.yaml", repo, anchor=repo / "services" / "closer"
            )
            == "services/closer/shipgate.yaml"
        )
    finally:
        os.chdir(previous)


def test_the_continuation_accepts_a_filled_row(tmp_path: Path) -> None:
    """The applier's *other* authorized shape.

    ``_declare_action`` fills the fields an existing row leaves silent, which
    changes a row in place and leaves the list the same length. Requiring the
    list to grow refused exactly the patch the route emits for an action the
    manifest already lists — and a parsed manifest spells "silent" as a present
    key with a ``None`` value, so a raw dict comparison read the fill as a
    changed answer (#429 review).
    """

    listed = _MANIFEST + (
        "action_surface:\n"
        "  actions:\n"
        "    - tool: send_email\n"
        "      source_id: adk_agent\n"
    )
    repo = _repo(tmp_path / "repo", manifest=listed)
    assert _verify(repo)["next_action"]["kind"] == "confirm_declarations"
    _apply(repo)

    manifest = (repo / "shipgate.yaml").read_text(encoding="utf-8")
    assert "effect: external_communication" in manifest

    after = _verify(repo)
    assert after["decision"] == "blocked"
    assert after["control_state"] == "review_publishable"
    assert after["permissions"]["commit"] is True


def test_a_changed_answer_is_not_a_filled_blank(tmp_path: Path) -> None:
    """The dual: only *silent* fields may be answered.

    A row that already carried an effect and now carries a different one is a
    reviewed answer being replaced, which no receipt may publish.
    """

    from agents_shipgate.cli.verify.orchestrator import _only_adds_action_declarations

    def _manifest(effect: str | None) -> bytes:
        row = "    - tool: send_email\n      source_id: adk_agent\n"
        if effect:
            row += f"      effect: {effect}\n"
        return (_MANIFEST + "action_surface:\n  actions:\n" + row).encode()

    silent = _manifest(None)
    answered = _manifest("external_communication")
    replaced = _manifest("read")

    assert _only_adds_action_declarations(silent, answered) is True
    assert _only_adds_action_declarations(answered, replaced) is False
    assert _only_adds_action_declarations(answered, answered) is False


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
