"""The setup half of the shared control envelope (#323).

``detect``, ``init``, and ``doctor`` run before a release decision exists, so
their control state is derived from setup facts. That is legitimate, and it is
also the thing most likely to go wrong: if ``control_state`` can mean "the gate
says" on one command and "setup says" on another, rolling one vocabulary across
six commands has made routing *harder*, not easier.

These tests hold the boundary from both sides. A setup envelope must be
unmistakably setup-derived, in the published JSON Schema and not only in
Pydantic; it must authorize nothing; and it must never be able to say
``complete``. The last one is the load-bearing one: a successful ``init`` that
could project ``complete`` would hand a coding agent merge and
report-complete authority for having written a YAML file.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from typer.testing import CliRunner

from agents_shipgate.cli.diagnostics import (
    diagnose_detect,
    diagnose_invalid_manifest,
    diagnose_missing_manifest,
    top_next_actions,
)
from agents_shipgate.cli.discovery.placeholders import (
    HUMAN_OWNED_MANIFEST_BLOCKS,
    human_owned_placeholders,
    placeholder_owner,
)
from agents_shipgate.cli.main import app
from agents_shipgate.cli.setup_control import (
    SETUP_ACTION_KINDS,
    setup_control_envelope,
    setup_input_id,
)
from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.core.agent_control_envelope import (
    envelope_from_setup,
    render_agent_control_envelope,
)
from agents_shipgate.schemas.agent_control import (
    CodingAgentCommandAction,
)
from agents_shipgate.schemas.agent_control_envelope import (
    SETUP_DECISIONS,
    SETUP_OPERATIONS,
    validate_agent_control_envelope,
)
from agents_shipgate.schemas.detect import DetectResult, WorkspaceSignals
from agents_shipgate.schemas.diagnostics import ALL_DIAGNOSTIC_IDS

REPO_ROOT = Path(__file__).resolve().parents[1]
_PUBLISHED_SCHEMA = Draft202012Validator(
    json.loads((REPO_ROOT / "docs/agent-control-schema.v1.json").read_text())
)
SAMPLE = REPO_ROOT / "samples" / "clean_read_only_agent"
runner = CliRunner()


def _setup_envelope(**kwargs) -> dict:
    defaults = {
        "operation": "doctor",
        "input_id": "sha256:" + "0" * 64,
        "reason": "A setup step ran.",
    }
    routing = setup_control_envelope(**{**defaults, **kwargs})
    return json.loads(render_agent_control_envelope(routing.envelope))


def _reject_both_layers(payload: dict) -> None:
    """A contradictory payload must fail Pydantic *and* the published schema.

    Model validators have no JSON Schema representation, so a shape rejected
    only in Python is published as valid to every external consumer validating
    against ``docs/agent-control-schema.v1.json``.
    """

    with pytest.raises(ValidationError):
        validate_agent_control_envelope(payload)
    assert list(_PUBLISHED_SCHEMA.iter_errors(payload)), "accepted by the published JSON Schema"


# ---------------------------------------------------------------------------
# The setup / release-decision boundary
# ---------------------------------------------------------------------------


def test_a_setup_envelope_passes_both_layers():
    """The negative cases below are only meaningful if the positive one passes."""

    payload = _setup_envelope(diagnostics=diagnose_missing_manifest(Path("/ws")))

    assert payload["decision_source"] == "setup"
    assert payload["decision"] in SETUP_DECISIONS
    validate_agent_control_envelope(payload)
    assert not list(_PUBLISHED_SCHEMA.iter_errors(payload))


def test_a_setup_operation_cannot_claim_a_release_decision():
    """The confusion this rollout exists to remove, in the fail-open direction.

    A ``doctor`` envelope reporting ``decision_source: "release_decision"`` would
    present a manifest-validity answer as the gate's verdict on a change nobody
    evaluated.
    """

    _reject_both_layers(
        {**_setup_envelope(), "decision_source": "release_decision", "decision": "passed"}
    )


def test_a_release_operation_cannot_claim_a_setup_decision():
    """And the reverse: a real gate verdict must not be readable as setup noise."""

    payload = {**_setup_envelope(), "operation": "verify", "decision": "setup_complete"}
    _reject_both_layers(payload)


def test_a_setup_envelope_binds_no_artifacts_and_no_control_identity():
    """Setup publishes no pointer, so it can vouch for no file's contents."""

    base = _setup_envelope()
    _reject_both_layers({**base, "current_control_id": "sha256:" + "a" * 64})
    _reject_both_layers(
        {
            **base,
            "artifacts": {"report": {"path": "report.json", "sha256": "sha256:" + "b" * 64}},
        }
    )


def test_a_setup_decision_uses_the_closed_setup_vocabulary():
    _reject_both_layers({**_setup_envelope(), "decision": "passed"})


def test_setup_can_never_report_complete():
    """The load-bearing invariant: writing a manifest is not finishing a task.

    Enforced by ``CompleteControlEnvelope.operation``, so it holds for an
    external consumer validating the published document, not only for a caller
    that happens to go through the projection.
    """

    # The projection refuses it too, but the schema is what an external
    # consumer enforces, so both are checked.
    with pytest.raises(ValueError, match="setup read no change"):
        envelope_from_setup(
            derive_agent_control(reason="Nothing left to do."),
            operation="init",
            decision="setup_complete",
            input_id="sha256:" + "0" * 64,
        )

    for operation in SETUP_OPERATIONS:
        _reject_both_layers(
            {
                **_setup_envelope(),
                "operation": operation,
                "control_state": "complete",
                "execution": "succeeded",
                "permissions": {
                    "edit": True,
                    "commit": True,
                    "push": True,
                    "update_pr": True,
                    "merge": True,
                    "report_complete": True,
                },
                "verify_required": False,
                "next_actor": "none",
                "next_action": None,
                "human_review": {"required": False, "why": None, "required_reviewers": []},
            }
        )


def test_the_projection_refuses_a_control_that_authorizes_anything():
    """Setup read no diff, so there is no evaluated change to stand behind."""

    publishing = derive_agent_control(
        reason="A step remains.",
        next_action=CodingAgentCommandAction(
            kind="verify", command="agents-shipgate verify --json", why="Run the gate."
        ),
        publication_allowed=True,
    )
    assert publishing.permissions.publishes

    with pytest.raises(ValueError, match="setup read no change"):
        envelope_from_setup(
            publishing,
            operation="init",
            decision="setup_complete",
            input_id="sha256:" + "0" * 64,
        )


def test_every_setup_envelope_authorizes_nothing():
    for diagnostics in (
        [],
        diagnose_missing_manifest(Path("/ws")),
        diagnose_invalid_manifest(Path("shipgate.yaml"), message="bad yaml"),
    ):
        payload = _setup_envelope(
            recheck_command="agents-shipgate doctor --json",
            diagnostics=diagnostics,
            advance=top_next_actions(diagnose_missing_manifest(Path("/ws")))[0],
            advance_kind="verify",
        )
        assert not any(payload["permissions"].values()), payload


# ---------------------------------------------------------------------------
# Routing: who owns the next step
# ---------------------------------------------------------------------------


def test_every_diagnostic_states_the_kind_of_step_it_asks_for():
    """A new diagnostic cannot be added without saying what kind of step it is.

    The alternative — parsing the emitted command back into an action kind — is
    a second grammar for a fact the diagnostic's author already holds, and it
    breaks the first time a diagnostic emits `pip install`.
    """

    assert set(SETUP_ACTION_KINDS) == set(ALL_DIAGNOSTIC_IDS)


def test_an_agent_owned_edit_is_a_typed_route_the_envelope_publishes():
    """A manifest the loader rejected is the agent's to fix, not a human's.

    The envelope publishes the edit itself — ``kind: "edit"`` with ``path`` and
    ``expects``. Substituting the command that merely *checks* the edit was
    tried: an envelope-only consumer executing it re-ran ``doctor`` against an
    unchanged file and got the identical action back forever, with the
    instruction surviving only in ``why``.

    The typed action lives on the envelope rather than in ``AgentControl``,
    because six durable schemas embed that union.
    """

    routing = setup_control_envelope(
        operation="doctor",
        input_id="sha256:" + "0" * 64,
        reason="Manifest exists but failed to load",
        diagnostics=diagnose_invalid_manifest(Path("shipgate.yaml"), message="bad yaml"),
        recheck_command="agents-shipgate doctor -c shipgate.yaml --json",
    )
    payload = json.loads(render_agent_control_envelope(routing.envelope))

    assert payload["control_state"] == "agent_action_required"
    assert payload["next_actor"] == "coding_agent"
    assert payload["next_action"]["kind"] == "edit"
    assert payload["next_action"]["path"] == "shipgate.yaml"
    assert payload["next_action"]["command"] is None
    assert payload["next_action"]["expects"]
    assert not list(_PUBLISHED_SCHEMA.iter_errors(payload))
    # The ranked list names the same work.
    assert routing.actions[0].kind == "edit"
    assert routing.actions[0].path == "shipgate.yaml"


def test_only_setup_may_publish_an_edit_route():
    """An edit route on a gate operation is a payload no producer can emit."""

    routing = setup_control_envelope(
        operation="doctor",
        input_id="sha256:" + "0" * 64,
        reason="Manifest exists but failed to load",
        diagnostics=diagnose_invalid_manifest(Path("shipgate.yaml"), message="bad yaml"),
        recheck_command="agents-shipgate doctor -c shipgate.yaml --json",
    )
    payload = json.loads(render_agent_control_envelope(routing.envelope))

    _reject_both_layers(
        {
            **payload,
            "operation": "verify",
            "decision_source": "release_decision",
            "decision": "review_required",
        }
    )


def test_an_unresolved_human_owned_placeholder_routes_to_a_human():
    """#325: a declaration nobody made must never be published as agent work."""

    payload = _setup_envelope(
        operation="init",
        placeholders=[
            {"path": "agent.declared_purpose[0]", "current": "CHANGE_ME", "line": 13},
            {"path": "tool_sources.path", "current": "CHANGE_ME", "line": 20},
        ],
        manifest_display_path="shipgate.yaml",
        # An onward command is offered and must still be withheld: the point is
        # that no route past the obligation exists, not that none was available.
        advance=top_next_actions(diagnose_missing_manifest(Path("/ws")))[0],
    )

    assert payload["control_state"] == "human_review_required"
    assert payload["next_actor"] == "human"
    assert payload["next_action"]["command"] is None
    # The exact file, line, and field — the point of routing it to a person is
    # that they can act without reading the manifest to find out what is asked.
    why = payload["next_action"]["why"]
    assert "shipgate.yaml" in why
    assert "line 13" in why
    assert "agent.declared_purpose" in why
    # The value is not shown as if it were a field name.
    assert "declared_purpose.CHANGE_ME" not in why
    # The agent-owned placeholder is not offered as a way past the obligation.
    assert len(payload["next_action"]["why"]) > 0
    assert payload["control_state"] == "human_review_required"


def test_an_agent_owned_placeholder_does_not_stop_the_turn():
    """A tool-source path is ordinary repository reading."""

    advance = top_next_actions(diagnose_missing_manifest(Path("/ws")))[0]
    payload = _setup_envelope(
        operation="init",
        placeholders=[{"path": "tool_sources.path", "current": "CHANGE_ME", "line": 20}],
        advance=advance,
        advance_kind="verify",
    )

    assert payload["control_state"] == "agent_action_required"


def test_a_blocking_diagnostic_outranks_the_placeholder_obligation():
    """A manifest the loader rejects must be repaired before it can be reviewed.

    The obligation is not lost: it is derived from the manifest on every run
    rather than remembered, so the next run surfaces it.
    """

    payload = _setup_envelope(
        diagnostics=diagnose_invalid_manifest(Path("shipgate.yaml"), message="bad yaml"),
        placeholders=[
            {"path": "agent.declared_purpose[0]", "current": "CHANGE_ME", "line": 13}
        ],
        recheck_command="agents-shipgate doctor --json",
    )

    assert payload["control_state"] == "agent_action_required"
    assert payload["next_action"]["kind"] == "edit"


def test_a_workspace_with_no_agent_surface_is_reported_as_not_applicable():
    result = DetectResult(
        is_agent_project=False,
        workspace_signals=WorkspaceSignals(python_file_count=0, has_prompts_dir=True),
    )
    diagnostics = diagnose_detect(result, has_manifest=False, workspace=Path("/ws"))

    payload = _setup_envelope(operation="detect", diagnostics=diagnostics)

    assert payload["decision"] == "setup_not_applicable"
    assert payload["control_state"] == "human_review_required"
    assert payload["next_action"]["kind"] == "stop"


@pytest.mark.parametrize(
    ("path", "owner"),
    [
        ("agent.declared_purpose", "human"),
        # `collect_placeholders` names a list item by its own text, so a
        # leaf-only rule read the field an agent must never invent as the
        # agent's own to fill in.
        ("agent.declared_purpose[0]", "human"),
        ("agent.prohibited_actions[0]", "human"),
        ("policies.refund.approval_required", "human"),
        ("permissions.scopes[0]", "human"),
        ("checks.ignore.reason", "human"),
        ("tool_sources.path", "coding_agent"),
        ("project.name", "coding_agent"),
        ("agent.name", "coding_agent"),
    ],
)
def test_placeholder_ownership(path: str, owner: str):
    assert placeholder_owner(path) == owner


def test_human_owned_placeholders_filters_the_list():
    entries = [
        {"path": "agent.declared_purpose[0]", "line": 1},
        {"path": "tool_sources.path", "line": 2},
    ]
    assert [entry["line"] for entry in human_owned_placeholders(entries)] == [1]


def test_setup_input_id_tracks_the_manifest_it_answered_about(tmp_path: Path):
    """Editing the manifest changes the identity of the answer about it.

    That is the event after which a cached setup route must not be reused, so
    the two must not be able to share an id.
    """

    manifest = tmp_path / "shipgate.yaml"
    manifest.write_text("version: '0.1'\n", encoding="utf-8")
    before = setup_input_id(operation="doctor", workspace=tmp_path, manifest_path=manifest)
    manifest.write_text("version: '0.1'\nproject:\n  name: x\n", encoding="utf-8")
    after = setup_input_id(operation="doctor", workspace=tmp_path, manifest_path=manifest)

    assert before != after
    assert before.startswith("sha256:")
    # An absent manifest is not the same subject as an unreadable one.
    assert setup_input_id(operation="doctor", workspace=tmp_path) != before


# ---------------------------------------------------------------------------
# The frozen surface must not have widened
# ---------------------------------------------------------------------------


def test_no_published_schema_knows_an_edit_action():
    """The union stays out of the six durable schemas that embed it.

    A typed ``edit`` action lived here briefly and was removed: ``AgentControl``
    is carried by the verifier, the handoff, preflight, the agent result, the
    boundary result, and verify-run, so widening it widened all six under
    unchanged identifiers — and five of those artifacts record no
    ``contract_version``, so a consumer holding a stored payload cannot use the
    runtime floor to tell which shape it has.
    """

    for name in (
        "codex-boundary-result-schema.v2",
        "verifier-schema.v0.8",
        "agent-handoff-schema.v7",
        "preflight-schema.v0.4",
        "agent-result-schema.v3",
        "agent-boundary-result-schema.v2",
        "verify-run-schema.v4",
        # The envelope's own document. It is emitted on stdout and never written
        # as an artifact, so it carries the setup rollout — but not an `edit`.
        "agent-control-schema.v1",
    ):
        schema = (REPO_ROOT / f"docs/{name}.json").read_text()
        assert "CodingAgentEditAction" not in schema, name
        # `"edit"` also names a *permission*, so match the shape a Literal kind
        # is emitted as rather than the bare string.
        assert '{"const": "edit"' not in schema.replace(" ", ""), name


# ---------------------------------------------------------------------------
# End to end: the adoption walk, routed on the envelope alone
# ---------------------------------------------------------------------------


def _git(workspace: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(workspace), *args], check=True, capture_output=True)


@pytest.fixture()
def unadopted(tmp_path: Path) -> Path:
    """A repository with an agent surface and no manifest."""

    workspace = tmp_path / "repo"
    workspace.mkdir()
    shutil.copy(SAMPLE / "tools.json", workspace / "tools.json")
    (workspace / "agent.py").write_text(
        "from agents import Agent, function_tool\n"
        "\n"
        "@function_tool\n"
        "def lookup(order_id: str) -> str:\n"
        '    """Read one order."""\n'
        '    return "ok"\n'
        "\n"
        'support_agent = Agent(name="support_agent", tools=[lookup])\n',
        encoding="utf-8",
    )
    (workspace / ".gitignore").write_text("agents-shipgate-reports/\n", encoding="utf-8")
    _git(workspace, "init", "-q", "-b", "main")
    _git(workspace, "config", "user.email", "test@example.test")
    _git(workspace, "config", "user.name", "Test User")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-m", "fixture")
    return workspace


def _control(args: list[str]) -> dict:
    """Run one command and read only its control envelope."""

    result = runner.invoke(app, args)
    payload = json.loads(result.stdout)
    if isinstance(payload, list):
        payload = payload[0]
    control = payload["control"]
    assert control["schema_version"] == "shipgate.agent_control/v1"
    assert not list(_PUBLISHED_SCHEMA.iter_errors(control)), control
    return control


def test_the_adoption_walk_routes_on_the_shared_envelope_alone(unadopted: Path):
    """#323's acceptance criterion, as a walk rather than as a field list.

    Each step reads only ``control`` — never the command-specific result fields,
    never prose — and each step's answer names the next one. The walk must also
    be able to *stop*: an unresolved declaration a person owes routes to a human
    and stays there until the manifest changes, which is the difference between
    a flow that composes and a flow that always says yes.
    """

    detect = _control(["detect", "--workspace", str(unadopted), "--json"])
    assert detect["operation"] == "detect"
    assert detect["decision_source"] == "setup"
    assert detect["decision"] == "setup_incomplete"
    assert detect["control_state"] == "agent_action_required"
    assert detect["next_action"]["kind"] == "initialize"
    assert not any(detect["permissions"].values())

    init = _control(["init", "--workspace", str(unadopted), "--write", "--json"])
    assert init["operation"] == "init"
    # The manifest exists now, and the walk has stopped where it should: the
    # generated manifest still declares no purpose, and that is a person's to
    # supply. No command is offered, so there is nothing an agent could run to
    # get past it.
    assert (unadopted / "shipgate.yaml").is_file()
    assert init["control_state"] == "human_review_required"
    assert init["next_actor"] == "human"
    assert init["next_action"]["command"] is None
    assert "declared_purpose" in init["next_action"]["why"]

    doctor_blocked = _control(
        ["doctor", "--config", str(unadopted / "shipgate.yaml"), "--json"]
    )
    # Same obligation, same answer — derived from the manifest each run rather
    # than remembered, so a second opinion cannot appear from a different command.
    assert doctor_blocked["control_state"] == "human_review_required"

    manifest = unadopted / "shipgate.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "- CHANGE_ME", "- Look up order status for support requests"
        ),
        encoding="utf-8",
    )

    doctor = _control(["doctor", "--config", str(manifest), "--json"])
    assert doctor["decision"] == "setup_complete"
    assert doctor["control_state"] == "agent_action_required"
    # Deterministically the gate, not a user-facing scan step (#325/#327).
    assert doctor["next_action"]["kind"] == "verify"
    assert doctor["verify_required"] is True
    assert not any(doctor["permissions"].values())

    # The step the walk was pointed at, run through the same vocabulary. This is
    # where a release decision first exists, and where the source changes.
    verify = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(unadopted),
            "--config",
            str(manifest),
            "--format",
            "control",
        ],
    )
    gate = json.loads(verify.stdout)
    assert gate["schema_version"] == detect["schema_version"]
    assert gate["operation"] == "verify"
    assert gate["decision_source"] == "release_decision"
    assert gate["decision"] not in SETUP_DECISIONS


def test_a_configured_workspace_is_handed_to_doctor_not_declared_complete(unadopted: Path):
    """``detect`` kept naming a command it knows would be refused — and then a
    completion it has no way to establish.

    ``DetectResult.next_action`` says ``init`` whenever the workspace is
    adoptable, including when it has already been adopted, and ``init --write``
    refuses to overwrite. Routing to the gate instead fixed that and introduced a
    worse problem: ``detect`` never opens the manifest, so declaring
    ``setup_complete`` from the presence of a file contradicted ``init`` and
    ``doctor``, which return ``human_review_required`` for the same manifest
    while a declaration is unresolved — a route around the human stop.

    The honest handoff is to the command that does read it.
    """

    runner.invoke(app, ["init", "--workspace", str(unadopted), "--write", "--json"])

    detect = _control(["detect", "--workspace", str(unadopted), "--json"])

    assert detect["decision"] == "setup_incomplete"
    assert detect["next_action"]["kind"] == "configure"
    assert "doctor" in detect["next_action"]["command"]

    # And the command it names reaches the same obligation the other two report.
    doctor = _control(["doctor", "--config", str(unadopted / "shipgate.yaml"), "--json"])
    assert doctor["control_state"] == "human_review_required"


def test_a_dry_run_routes_to_the_write_it_did_not_do(unadopted: Path):
    """Nothing was written, so the outstanding step is writing it."""

    control = _control(["init", "--workspace", str(unadopted), "--json"])

    assert not (unadopted / "shipgate.yaml").exists()
    assert control["decision"] == "setup_incomplete"
    assert control["next_action"]["kind"] == "initialize"
    assert "--write" in control["next_action"]["command"]


def test_an_instruction_refresh_over_an_existing_manifest_is_not_an_obligation(
    unadopted: Path,
):
    """`init --write --agent-instructions=...` is the advertised refresh command.

    It reports success and deliberately leaves the manifest alone, so routing the
    caller to edit a manifest nothing is wrong with would invent an obligation
    out of a run that had none. `init --write` on its own still exits 2 there,
    and still says so.
    """

    runner.invoke(app, ["init", "--workspace", str(unadopted), "--write", "--json"])
    manifest = unadopted / "shipgate.yaml"

    # While the written manifest still owes a human declaration, the refresh is
    # *not* a way past it. This is the route the boundary had to be closed on:
    # the same unedited manifest previously reported `setup_complete -> verify`
    # because the placeholders of the template — which was never written — were
    # inspected instead of the file on disk.
    blocked = _control(
        [
            "init", "--workspace", str(unadopted), "--write",
            "--agent-instructions=agents-md", "--json",
        ]
    )
    assert blocked["control_state"] == "human_review_required"
    assert "CHANGE_ME" in manifest.read_text(encoding="utf-8")

    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "- CHANGE_ME", "- Look up order status for support requests"
        ),
        encoding="utf-8",
    )

    refresh = _control(
        [
            "init", "--workspace", str(unadopted), "--write",
            "--agent-instructions=agents-md", "--json",
        ]
    )
    assert refresh["decision"] == "setup_complete"
    assert refresh["next_action"]["kind"] == "verify"

    plain = _control(["init", "--workspace", str(unadopted), "--write", "--json"])
    assert plain["decision"] == "setup_incomplete"
    assert plain["next_action"]["kind"] == "edit"
    assert plain["exit_code"] == 2


def test_the_control_route_agrees_with_the_ranked_actions_beside_it(unadopted: Path):
    """One condition, two fields, one answer.

    ``control.next_action`` is built from the same ranked diagnostic that
    produces ``next_actions[0]``, so the compact envelope and the ranked list
    cannot describe different work.
    """

    result = runner.invoke(app, ["detect", "--workspace", str(unadopted), "--json"])
    payload = json.loads(result.stdout)

    assert payload["control"]["next_action"]["command"] == payload["next_action"]

    # And where a diagnostic decides the route, the compact and ranked forms
    # come from that same diagnostic rather than from two authors agreeing.
    empty = unadopted / "empty"
    empty.mkdir()
    bare = json.loads(
        runner.invoke(app, ["detect", "--workspace", str(empty), "--json"]).stdout
    )
    assert bare["diagnostics"]
    assert bare["control"]["next_action"]["why"] == bare["next_actions"][0]["why"]


# ---------------------------------------------------------------------------
# Review regressions (PR #372). Each of these reproduced against the first
# revision of this branch before it was fixed.
# ---------------------------------------------------------------------------


def test_a_setup_envelope_cannot_carry_publication_authority():
    """Fixing provenance left `permissions` unconstrained.

    A genuine setup envelope with its vector replaced by the publish-only one was
    accepted by both layers, so a schema-driven consumer could read
    edit/commit/push authority out of a command that never opened a diff.
    """

    _reject_both_layers(
        {
            **_setup_envelope(),
            "permissions": {
                "edit": True,
                "commit": True,
                "push": True,
                "update_pr": True,
                "merge": False,
                "report_complete": False,
            },
        }
    )


def test_setup_cannot_reach_review_publishable():
    """The state whose entire meaning is "the evidence may be published"."""

    _reject_both_layers(
        {
            **_setup_envelope(),
            "control_state": "review_publishable",
            "permissions": {
                "edit": True,
                "commit": True,
                "push": True,
                "update_pr": True,
                "merge": False,
                "report_complete": False,
            },
            "next_actor": "human",
            "next_action": {
                "actor": "human",
                "kind": "review",
                "command": None,
                "expects": None,
                "why": "x",
            },
            "human_review": {"required": True, "why": "x", "required_reviewers": []},
        }
    )


def test_a_setup_verdict_is_unreadable_under_any_other_source():
    """The inverse pairing the operation-keyed rules did not cover.

    ``operation: "verify"`` with ``decision_source: "release_decision"`` and
    ``decision: "setup_complete"`` passed both layers: the rules narrowed
    ``decision`` only when the *operation* was setup.
    """

    _reject_both_layers(
        {
            **_setup_envelope(),
            "operation": "verify",
            "decision_source": "release_decision",
            "decision": "setup_complete",
        }
    )


@pytest.mark.parametrize("input_id", ["   ", None])
def test_a_setup_control_must_name_its_subject(input_id):
    """Authority that cannot name the input it assessed is uncheckable."""

    _reject_both_layers({**_setup_envelope(), "input_id": input_id})


@pytest.mark.parametrize(
    "path",
    [
        # Every `do_not_auto_assert` surface with a manifest spelling.
        "agent_bindings.root.object",
        "agent_bindings.declarations.agent",
        "tool_identity.bindings.primary.tool",
        "action_surface.actions.authority",
        "action_surface.actions.effect",
        "action_surface.policies.require",
        "checks.ignore.reason",
        "risk_overrides.selectors.tags",
        "baseline.integrity_mode",
        "human_ack.CHANGE_ME",
        "organization.policy_pin",
    ],
)
def test_binding_and_authority_declarations_are_human_owned(path: str):
    """`agent_bindings.root` was classified as coding-agent work.

    The contract publishes `agent_binding`, `action_effect`, `action_authority`,
    `approval`, and `confirmation` in `do_not_auto_assert` — reviewed
    closed-world claims about deployed wiring. Routing an unresolved one as an
    `edit` action instructs the governed agent to invent it.
    """

    assert placeholder_owner(path) == "human"


def test_every_do_not_auto_assert_surface_with_a_manifest_spelling_is_covered():
    """Pins the mapping so a new contract entry cannot quietly go unmapped."""

    from agents_shipgate.schemas.contract import DO_NOT_AUTO_ASSERT

    mapped = set(HUMAN_OWNED_MANIFEST_BLOCKS.values())
    # The entries with no manifest surface at all: they are runtime or
    # verification-time concepts, not fields anyone writes into `shipgate.yaml`.
    without_manifest_surface = {
        "idempotency",
        "broad-scope",
        "prohibited-action",
        "runtime-trace",
        "human-authorization",
        "waiver",
        "approval",
        "confirmation",
        "policy-weakening",
        "action_authority",
        "action_effect",
        "agent_binding",
        "human-ack",
        "suppression",
        "baseline",
    }
    assert set(DO_NOT_AUTO_ASSERT) <= without_manifest_surface | mapped
    # And the ones that *do* have a manifest spelling are actually mapped.
    assert {"agent_binding", "action_effect", "action_authority"} <= mapped


def test_a_human_route_publishes_exactly_one_action():
    """An alternative is a way around the obligation.

    Keeping the ranked alternatives beside a human route re-offered the very
    placeholder being routed to a person as an agent-executable edit, one
    position down the same list.
    """

    routing = setup_control_envelope(
        operation="init",
        input_id="sha256:" + "0" * 64,
        reason="Wrote shipgate.yaml.",
        placeholders=[
            {"path": "agent.declared_purpose[0]", "current": "CHANGE_ME", "line": 13}
        ],
        manifest_display_path="shipgate.yaml",
        advance=top_next_actions(diagnose_missing_manifest(Path("/ws")))[0],
    )

    assert len(routing.actions) == 1
    assert routing.actions[0].kind == "review"
    assert routing.actions[0].command is None
    assert routing.legacy_next_action.startswith("Review:")


@pytest.mark.parametrize("count", [1, 3, 12, 40])
@pytest.mark.parametrize("field", ["agent.declared_purpose", "z" * 300])
def test_human_review_locations_survive_the_prose_cap(count: int, field: str):
    """The exact locations must not be silently cut.

    They live in `why`, which the projection truncates at
    `MAX_ENVELOPE_PROSE_BYTES`; twelve unresolved declarations, or one very long
    field path, dropped the later locations and could lose the sentence saying
    what they were. What is elided now is elided visibly, with a count and a
    pointer at `placeholders[]`, which carries every location in full.
    """

    from agents_shipgate.cli.setup_control import _placeholder_review_why
    from agents_shipgate.schemas.agent_control_envelope import truncate_prose

    why = _placeholder_review_why(
        [{"path": field, "current": "CHANGE_ME", "line": 10 + i} for i in range(count)],
        "shipgate.yaml",
    )

    assert truncate_prose(why) == why, "the review prose was cut by the envelope cap"
    assert "line 10" in why
    if count > 4:
        assert "more in placeholders[]" in why


def test_the_setup_identity_moves_when_the_route_moves(tmp_path: Path):
    """An identity that does not change when the answer changes invites reuse.

    Hashing only the operation, the workspace, and the manifest bytes left
    `detect` reporting `setup_not_applicable` and `setup_incomplete` under the
    *same* `input_id`, and `doctor` moving from a verify handoff to an edit under
    another.
    """

    empty = setup_input_id(operation="detect", workspace=tmp_path, routing_facts={"agent": False})
    found = setup_input_id(operation="detect", workspace=tmp_path, routing_facts={"agent": True})
    assert empty != found

    manifest = tmp_path / "shipgate.yaml"
    manifest.write_text("version: '0.1'\n", encoding="utf-8")
    resolved = setup_input_id(
        operation="doctor",
        workspace=tmp_path,
        manifest_path=manifest,
        manifest_bytes=manifest.read_bytes(),
        routing_facts={"unresolved_sources": []},
    )
    missing_source = setup_input_id(
        operation="doctor",
        workspace=tmp_path,
        manifest_path=manifest,
        manifest_bytes=manifest.read_bytes(),
        routing_facts={"unresolved_sources": [{"id": "a"}]},
    )
    assert resolved != missing_source


def test_the_setup_identity_uses_the_bytes_the_caller_inspected(tmp_path: Path):
    """One snapshot, not two reads an edit can land between."""

    manifest = tmp_path / "shipgate.yaml"
    manifest.write_text("version: '0.1'\n", encoding="utf-8")
    inspected = manifest.read_bytes()
    identity = setup_input_id(
        operation="doctor", workspace=tmp_path, manifest_path=manifest, manifest_bytes=inspected
    )

    manifest.write_text("version: '0.1'\nproject:\n  name: later\n", encoding="utf-8")

    assert (
        setup_input_id(
            operation="doctor",
            workspace=tmp_path,
            manifest_path=manifest,
            manifest_bytes=inspected,
        )
        == identity
    )
    # Reopening the file instead would have produced an id for a manifest state
    # no answer was ever computed from.
    assert (
        setup_input_id(operation="doctor", workspace=tmp_path, manifest_path=manifest) != identity
    )


def test_the_doctor_handoff_runs_from_outside_the_workspace(unadopted: Path, tmp_path: Path):
    """A command that silently depends on the caller's cwd is not runnable.

    `verify --config <abs path>` resolved the repository from wherever the caller
    happened to be standing, so following doctor's own rank-1 action from any
    other directory exited 2 with "Workspace is not inside a git checkout".
    """

    runner.invoke(app, ["init", "--workspace", str(unadopted), "--write", "--json"])
    manifest = unadopted / "shipgate.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("- CHANGE_ME", "- Answer support questions"),
        encoding="utf-8",
    )

    control = _control(["doctor", "--config", str(manifest), "--json"])
    command = control["next_action"]["command"]

    assert "--workspace" in command
    assert str(unadopted) in command
    # And running it from an unrelated directory reaches the right repository.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    args = shlex.split(command)
    assert args[args.index("--workspace") + 1] == str(unadopted)
    result = subprocess.run(
        [sys.executable, "-m", "agents_shipgate", *args[args.index("verify") :]],
        cwd=elsewhere,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
    )
    assert "not inside a git checkout" not in result.stderr, result.stderr


# ---------------------------------------------------------------------------
# Second review round (PR #372). Each reproduced against 822c4f9e.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # Block style, the spelling `init` emits.
        'version: "0.1"\nagent:\n  name: bot\n  declared_purpose:\n    - CHANGE_ME\n',
        # Flow style, which the loader accepts and the schema validates. The
        # line scanner tracked indentation, so it reported this at path `agent`
        # — not a human-owned field — and doctor published an executable edit
        # for a declaration only a person may make.
        'version: "0.1"\nagent: {name: bot, declared_purpose: [CHANGE_ME]}\n',
        # Mixed, and nested one level deeper.
        'version: "0.1"\nagent:\n  {name: bot, declared_purpose: [CHANGE_ME]}\n',
    ],
)
def test_ownership_does_not_depend_on_yaml_spelling(text: str):
    from agents_shipgate.cli.discovery.placeholders import collect_placeholders

    found = collect_placeholders(text)

    assert [entry["path"] for entry in found] == ["agent.declared_purpose[0]"]
    assert human_owned_placeholders(found), "an equivalent spelling changed the owner"


def test_placeholder_locations_come_from_the_parsed_document():
    """Line numbers must point at the placeholder, whatever the layout."""

    from agents_shipgate.cli.discovery.placeholders import collect_placeholders

    text = (
        'version: "0.1"\n'
        "\n"
        "# a comment\n"
        "agent_bindings:\n"
        "  root: {object: CHANGE_ME}\n"
        "tool_sources:\n"
        "  - id: a\n"
        "    path: CHANGE_ME\n"
    )

    found = {entry["path"]: entry["line"] for entry in collect_placeholders(text)}

    assert found == {"agent_bindings.root.object": 5, "tool_sources[0].path": 8}


@pytest.mark.parametrize(
    "path",
    [
        # `runtime-trace` is in `do_not_auto_assert`, and these are its manifest
        # spellings. Recorded runtime behaviour is the one thing a static tool
        # cannot check and an agent must never supply.
        "validation.evidence.approval_traces[0].path",
        "validation.evidence.agent_traces[0].path",
        "openai_api.trace_samples[0].path",
        "google_adk.trace_samples[0].path",
    ],
)
def test_runtime_evidence_declarations_are_human_owned(path: str):
    assert placeholder_owner(path) == "human"


def test_detect_never_declares_setup_complete_from_a_file_existing(tmp_path: Path):
    """`detect` does not open the manifest, so it cannot know."""

    from agents_shipgate.cli.detect import _detect_advance

    result = DetectResult(is_agent_project=True)
    advance, _kind, decision = _detect_advance(
        result, has_manifest=True, workspace=tmp_path
    )

    assert decision != "setup_complete"
    assert advance is not None
    assert "doctor" in (advance.command or "")


def test_a_refused_instruction_target_still_reports_what_failed(unadopted: Path):
    """A non-zero exit has to say what failed, on both streams.

    The refused target is an obligation *this run produced*, so it outranks the
    standing placeholder review — which is not skipped, only deferred: it is
    derived from the manifest on every run, so the next one surfaces it.
    """

    runner.invoke(app, ["init", "--workspace", str(unadopted), "--write", "--json"])
    cursor = unadopted / ".cursor" / "rules" / "agents-shipgate.mdc"
    cursor.parent.mkdir(parents=True, exist_ok=True)
    cursor.write_text("hand-written, no managed block\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "init", "--workspace", str(unadopted), "--write",
            "--agent-instructions=cursor", "--json",
        ],
    )
    payload = json.loads(result.stdout)

    assert result.exit_code != 0
    assert payload["control"]["next_action"]["kind"] == "edit"
    assert "agents-shipgate.mdc" in payload["control"]["next_action"]["path"]
    # And the structured location rides in the ranked list.
    assert payload["next_actions"][0]["kind"] == "edit"
    assert "agents-shipgate.mdc" in payload["next_actions"][0]["path"]
    # And the deferred human obligation is still the answer on the next run.
    cursor.unlink()
    again = _control(
        [
            "init", "--workspace", str(unadopted), "--write",
            "--agent-instructions=cursor", "--json",
        ]
    )
    assert again["control_state"] == "human_review_required"


def test_the_placeholders_the_action_points_at_are_published(unadopted: Path):
    """"and N more in placeholders[]" must name an array that is actually there."""

    runner.invoke(app, ["init", "--workspace", str(unadopted), "--write", "--json"])
    manifest = unadopted / "shipgate.yaml"

    doctor = json.loads(
        runner.invoke(app, ["doctor", "--config", str(manifest), "--json"]).stdout
    )[0]
    assert doctor["placeholders"], "doctor referenced placeholders[] without publishing it"
    assert any(
        entry["path"].startswith("agent.declared_purpose") for entry in doctor["placeholders"]
    )

    # And init publishes the manifest it routed on, not a template it did not write.
    refresh = json.loads(
        runner.invoke(
            app,
            [
                "init", "--workspace", str(unadopted), "--write",
                "--agent-instructions=agents-md", "--json",
            ],
        ).stdout
    )
    on_disk = {(entry["path"], entry["line"]) for entry in refresh["placeholders"]}
    from agents_shipgate.cli.discovery.placeholders import collect_placeholders

    assert on_disk == {
        (entry["path"], entry["line"])
        for entry in collect_placeholders(manifest.read_text(encoding="utf-8"))
    }


def test_the_published_decision_vocabularies_match_their_engines():
    """The duplicated tuples cannot drift from the enums they mirror."""

    from agents_shipgate.schemas.agent_control_envelope import (
        AGENT_BOUNDARY_DECISION_VALUES,
        RELEASE_DECISION_VALUES,
    )
    from agents_shipgate.schemas.agent_result_v1 import AgentResultDecision
    from agents_shipgate.schemas.contract import RELEASE_DECISIONS

    assert set(RELEASE_DECISION_VALUES) == set(RELEASE_DECISIONS)
    assert set(AGENT_BOUNDARY_DECISION_VALUES) == set(AgentResultDecision.__args__)


@pytest.mark.parametrize(
    ("mutation", "why"),
    [
        ({"decision": "allow"}, "a boundary verdict under the release engine"),
        ({"decision": "anything at all"}, "an arbitrary string"),
        (
            {"decision_source": "agent_boundary", "decision": "allow"},
            "the wrong engine for this operation",
        ),
    ],
)
def test_decision_source_constrains_the_vocabulary(mutation: dict, why: str):
    """Naming the engine is only useful if it also says what that engine can say."""

    base = json.loads(
        render_agent_control_envelope(
            envelope_from_setup(
                derive_agent_control(
                    reason="A step remains.",
                    next_action=CodingAgentCommandAction(
                        kind="verify",
                        command="agents-shipgate verify --json",
                        why="Run the gate.",
                    ),
                ),
                operation="doctor",
                decision="setup_complete",
                input_id="sha256:" + "0" * 64,
            )
        )
    )
    release = {
        **base,
        "operation": "verify",
        "decision_source": "release_decision",
        "decision": "passed",
    }
    # The unmutated release shape is accepted, so the rejections below are the rule.
    validate_agent_control_envelope(release)
    _reject_both_layers({**release, **mutation})


# ---------------------------------------------------------------------------
# Interaction with #370 (manifest scope), merged from main.
# ---------------------------------------------------------------------------


@pytest.fixture()
def ambiguous_monorepo(tmp_path: Path) -> Path:
    """Two self-contained agent projects, so no single manifest describes the root."""

    workspace = tmp_path / "mono"
    for name in ("a", "b"):
        project = workspace / "apps" / name
        project.mkdir(parents=True)
        (project / "pyproject.toml").write_text(f'[project]\nname = "{name}"\n', encoding="utf-8")
        (project / "agent.py").write_text(
            "from google.adk.agents import LlmAgent\n"
            f"def act_{name}(x: str) -> str:\n    return x\n"
            f'root_agent = LlmAgent(name="agent_{name}", tools=[act_{name}])\n',
            encoding="utf-8",
        )
    return workspace


def test_detect_does_not_emit_a_command_for_a_scope_init_will_refuse(
    ambiguous_monorepo: Path,
):
    """`DetectResult.next_action` is prose when the scope is unresolved (#370).

    Naming one candidate would make the arbitrary pick `init --write` exists to
    refuse. Typing that prose as a `command` action would publish an unrunnable
    string; typing it as an agent route would ask the agent to make the choice.
    It is a human route.
    """

    control = _control(["detect", "--workspace", str(ambiguous_monorepo), "--json"])

    assert control["control_state"] == "human_review_required"
    assert control["decision"] == "setup_incomplete"
    assert control["next_action"]["command"] is None


def test_a_refused_scope_publishes_one_rank_one_across_both_streams(
    ambiguous_monorepo: Path,
):
    """The scope refusal routes *through* the envelope, not beside it.

    `init` publishes the refusal on stderr and its payload on stdout. Composing
    an independent ranked list for each is the split the unified routing exists
    to remove — and it would have come back through the merge, because both
    changes landed on this branch of `init` independently.

    The per-candidate commands survive as alternatives, unlike the placeholder
    review's: they are not ways *around* the decision, they are how the chosen
    project gets initialized once a person has made it.
    """

    with mock.patch.dict(os.environ, {"AGENTS_SHIPGATE_AGENT_MODE": "1"}):
        result = runner.invoke(
            app, ["init", "--workspace", str(ambiguous_monorepo), "--write", "--json"]
        )

    assert result.exit_code != 0
    payload = json.loads(result.stdout)
    error = json.loads(result.stderr.strip().splitlines()[-1])

    assert payload["control"]["control_state"] == "human_review_required"
    assert payload["next_actions"][0]["kind"] == "review"
    assert payload["next_actions"][0]["command"] is None
    # One rank-1 answer, whichever stream a caller reads.
    assert error["next_action"] == payload["next_action"]
    assert error["next_actions"] == payload["next_actions"]
    assert error["control"] == payload["control"]
    # ...and #370's per-candidate routes are not dropped.
    assert [action["kind"] for action in payload["next_actions"][1:]] == ["command", "command"]
    assert error["agent_scope"] == "ambiguous"


# ---------------------------------------------------------------------------
# Third and fourth review rounds (PR #372).
# ---------------------------------------------------------------------------


def test_an_existing_manifest_must_load_before_setup_is_called_complete(tmp_path: Path):
    """A file that exists is not a configured manifest.

    Scanning it for placeholders found none in an *empty* `shipgate.yaml`, so the
    instruction-refresh path reported `setup_complete` and handed back a verify
    command that exits 2 on the same file. Manifest validity is a setup fact.
    """

    (tmp_path / "shipgate.yaml").write_text("", encoding="utf-8")

    control = _control(
        [
            "init", "--workspace", str(tmp_path), "--write",
            "--agent-instructions=agents-md", "--json",
        ]
    )

    assert control["decision"] == "setup_incomplete"
    assert control["control_state"] == "agent_action_required"
    assert "does not load" in control["next_action"]["why"]


def test_doctor_error_paths_carry_the_shared_envelope(tmp_path: Path):
    """`doctor --json` promises a control field; these paths did not carry one.

    An invalid manifest printed nothing on stdout and a legacy error line on
    stderr, so the all-six-commands claim did not hold for the most common
    doctor failure there is.
    """

    manifest = tmp_path / "shipgate.yaml"
    manifest.write_text("", encoding="utf-8")

    with mock.patch.dict(os.environ, {"AGENTS_SHIPGATE_AGENT_MODE": "1"}):
        result = runner.invoke(app, ["doctor", "--config", str(manifest), "--json"])

    assert result.exit_code == 2
    error = json.loads(
        [line for line in result.stderr.splitlines() if line.strip().startswith("{")][-1]
    )
    control = error["control"]
    assert control["decision_source"] == "setup"
    assert control["decision"] == "setup_incomplete"
    assert control["execution"] == "failed"
    assert not any(control["permissions"].values())
    assert not list(_PUBLISHED_SCHEMA.iter_errors(control))


def test_an_unresolved_trace_declaration_keeps_its_location_on_the_error_path(
    tmp_path: Path,
):
    """`runtime-trace` evidence fails to open *because* nobody supplied it.

    The manifest is valid, so the failure surfaces while opening a file literally
    named `CHANGE_ME` — before ownership was ever evaluated. The caller got
    generic "inspect the file" guidance instead of the field, the line, and the
    fact that a person owes the value.
    """

    import yaml

    for name in ("shipgate.yaml", "tools.json"):
        (tmp_path / name).write_text((SAMPLE / name).read_text(encoding="utf-8"), encoding="utf-8")
    manifest = tmp_path / "shipgate.yaml"
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    data["validation"] = {
        "mode": "human_in_the_loop",
        "evidence": {"approval_traces": [{"path": "CHANGE_ME"}]},
    }
    manifest.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with mock.patch.dict(os.environ, {"AGENTS_SHIPGATE_AGENT_MODE": "1"}):
        result = runner.invoke(app, ["doctor", "--config", str(manifest), "--json"])

    assert result.exit_code == 3
    error = json.loads(
        [line for line in result.stderr.splitlines() if line.strip().startswith("{")][-1]
    )
    control = error["control"]
    assert control["control_state"] == "human_review_required"
    assert control["next_actor"] == "human"
    why = control["next_action"]["why"]
    assert "validation.evidence.approval_traces[0].path" in why
    assert "line " in why


def test_an_adopted_root_settles_an_ambiguous_scope(ambiguous_monorepo: Path):
    """`--allow-unresolved-scope` is a decision, and it has to survive.

    A person who accepted the root as the boundary wrote a manifest there. Asking
    them to choose a subproject on the next `detect` made that decision
    unrepeatable — the flow could never hand the accepted manifest to doctor.
    """

    runner.invoke(
        app,
        [
            "init", "--workspace", str(ambiguous_monorepo),
            "--allow-unresolved-scope", "--write", "--json",
        ],
    )
    assert (ambiguous_monorepo / "shipgate.yaml").is_file()

    control = _control(["detect", "--workspace", str(ambiguous_monorepo), "--json"])

    assert control["control_state"] == "agent_action_required"
    assert "doctor" in control["next_action"]["command"]


def test_the_dry_run_advance_repeats_the_setup_it_was_asked_for(
    ambiguous_monorepo: Path,
):
    """A recovery that drops flags completes with less than the caller wanted.

    The bare `init --write` it emitted also *fails*: without
    `--allow-unresolved-scope` it exits 2 in the very monorepo that needed it.
    """

    control = _control(
        [
            "init", "--workspace", str(ambiguous_monorepo), "--ci",
            "--allow-unresolved-scope", "--agent-instructions=agents-md", "--json",
        ]
    )
    command = control["next_action"]["command"]

    assert "--write" in command
    assert "--ci" in command
    assert "--agent-instructions=agents-md" in command
    assert "--allow-unresolved-scope" in command


def test_the_setup_identity_moves_when_the_scope_facts_move(ambiguous_monorepo: Path):
    """The #370 facts select the route, so they belong in the identity."""

    def refusal_identity() -> str:
        return _control(
            ["init", "--workspace", str(ambiguous_monorepo), "--write", "--json"]
        )["input_id"]

    before = refusal_identity()
    third = ambiguous_monorepo / "apps" / "c"
    third.mkdir(parents=True)
    (third / "pyproject.toml").write_text('[project]\nname = "c"\n', encoding="utf-8")
    (third / "agent.py").write_text(
        "from google.adk.agents import LlmAgent\n"
        "def act_c(x: str) -> str:\n    return x\n"
        'root_agent = LlmAgent(name="agent_c", tools=[act_c])\n',
        encoding="utf-8",
    )

    assert refusal_identity() != before


# ---------------------------------------------------------------------------
# Fifth review round (PR #372).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "decision"),
    [
        ("release_decision", "review_required"),
        ("release_decision", "insufficient_evidence"),
        ("release_decision", "blocked"),
        ("agent_boundary", "require_review"),
        ("agent_boundary", "block"),
    ],
)
def test_a_completion_cannot_rest_on_a_negative_verdict(source: str, decision: str):
    """Constraining the vocabulary alone left the verdict free of the authority.

    A `complete` envelope with `permissions.merge: true` accepted
    `decision: "blocked"` — a schema-valid negative gate result granting terminal
    authority, which is the confusion this envelope exists to make
    unrepresentable. `complete` is the one state where the two must agree.
    """

    check = source == "agent_boundary"
    payload = {
        "schema_version": "shipgate.agent_control/v1",
        "contract_version": "24",
        "operation": "check" if check else "verify",
        "source": "run",
        "execution": "succeeded",
        "exit_code": 0,
        "input_id": "sha256:" + "1" * 64,
        "decision": decision,
        "decision_source": source,
        "control_state": "complete",
        "permissions": dict.fromkeys(
            ("edit", "commit", "push", "update_pr", "merge", "report_complete"), True
        ),
        "verify_required": False,
        "next_actor": "none",
        "next_action": None,
        "human_review": {"required": False, "why": None, "required_reviewers": []},
        "pending_review": [],
        "reason": "ok",
        "current_control_id": None if check else "sha256:" + "2" * 64,
        "artifacts": {} if check else {"verifier": {"path": "x", "sha256": "sha256:" + "3" * 64}},
    }

    _reject_both_layers(payload)
    # ...and the verdicts that *do* permit completion still pass.
    validate_agent_control_envelope(
        {**payload, "decision": "allow" if check else "passed"}
    )


def test_a_manifest_that_is_not_utf8_is_refused_rather_than_rewritten(tmp_path: Path):
    """`errors="replace"` does not read a manifest, it writes a different one.

    One `0xff` in `project.name` became U+FFFD, so `doctor` loaded a *valid*
    manifest, reported `setup_complete`, and recommended verify — while `scan` on
    the same file exited 4 with `UnicodeDecodeError`. Setup and the gate have to
    validate the same input language.
    """

    manifest = tmp_path / "shipgate.yaml"
    manifest.write_bytes(b'version: "0.1"\nproject:\n  name: bad\xffname\n')

    with mock.patch.dict(os.environ, {"AGENTS_SHIPGATE_AGENT_MODE": "1"}):
        result = runner.invoke(app, ["doctor", "--config", str(manifest), "--json"])

    assert result.exit_code == 2
    error = json.loads(
        [line for line in result.stderr.splitlines() if line.strip().startswith("{")][-1]
    )
    control = error["control"]
    assert control["decision"] == "setup_incomplete"
    assert "not valid UTF-8" in control["reason"]


def test_the_dry_run_follow_up_is_equivalent_to_the_invocation(tmp_path: Path):
    """A follow-up that drops mode flags completes something else.

    `init --minimal --json` emitted a bare `init --workspace … --write`, which
    writes the *auto-detected* template rather than the minimal one that was
    previewed, and returns human prose instead of the JSON control loop.
    """

    kit = tmp_path / "kit.yaml"
    kit.write_text("schema_version: 1\n", encoding="utf-8")

    control = _control(
        [
            "init", "--workspace", str(tmp_path), "--minimal", "--ci",
            "--agent-instructions=agents-md", "--json",
        ]
    )
    command = control["next_action"]["command"]

    for flag in ("--write", "--minimal", "--ci", "--agent-instructions=agents-md", "--json"):
        assert flag in command, flag


def test_the_setup_identity_moves_with_the_requested_setup(tmp_path: Path):
    """The invocation selects the route, so it belongs in the identity.

    On one unchanged empty workspace a plain dry run, `--ci`, and
    `--agent-instructions=agents-md` returned the *same* `input_id` while their
    `next_action.command` values differed, so a cache keyed by the documented
    identity could reuse a different requested setup.

    Computed in one process rather than parametrised: the property is that the
    identities *differ from each other*, which no single parametrised case can
    see.
    """

    seen: dict[tuple[str, ...], tuple[str, str]] = {}
    for flags in ([], ["--ci"], ["--agent-instructions=agents-md"], ["--minimal"]):
        workspace = tmp_path / ("plain" if not flags else flags[0].strip("-").split("=")[0])
        workspace.mkdir()
        control = _control(["init", "--workspace", str(workspace), *flags, "--json"])
        seen[tuple(flags)] = (control["input_id"], control["next_action"]["command"])

    identities = [identity for identity, _ in seen.values()]
    assert len(set(identities)) == len(identities), seen
    # The commands differ too — the identity is tracking a real difference.
    assert len({command for _, command in seen.values()}) == len(seen)
