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
    CodingAgentEditAction,
    freeze_agent_control,
    project_legacy_agent_control,
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


def test_an_agent_owned_edit_is_a_typed_coding_agent_route():
    """A manifest the loader rejected is the agent's to fix, not a human's.

    Before the ``edit`` variant existed this had two bad projections: bury the
    instruction in some other command's ``why``, or end the turn for work the
    agent owns.
    """

    payload = _setup_envelope(
        diagnostics=diagnose_invalid_manifest(Path("shipgate.yaml"), message="bad yaml")
    )

    assert payload["control_state"] == "agent_action_required"
    assert payload["next_actor"] == "coding_agent"
    assert payload["next_action"]["kind"] == "edit"
    assert payload["next_action"]["path"] == "shipgate.yaml"
    assert payload["next_action"]["command"] is None
    assert payload["next_action"]["expects"]


def test_an_unresolved_human_owned_placeholder_routes_to_a_human():
    """#325: a declaration nobody made must never be published as agent work."""

    payload = _setup_envelope(
        operation="init",
        placeholders=[
            {"path": "agent.declared_purpose.CHANGE_ME", "current": "CHANGE_ME", "line": 13},
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
    # The list-item artifact is not shown as if it were a field name.
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
            {"path": "agent.declared_purpose.CHANGE_ME", "current": "CHANGE_ME", "line": 13}
        ],
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
        ("agent.declared_purpose.CHANGE_ME", "human"),
        ("agent.prohibited_actions.CHANGE_ME", "human"),
        ("policies.refund.approval_required", "human"),
        ("permissions.scopes.CHANGE_ME", "human"),
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
        {"path": "agent.declared_purpose.CHANGE_ME", "line": 1},
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


def test_the_frozen_codex_projection_collapses_an_edit_route():
    """``FrozenAgentControl`` knows three states and two action kinds.

    It referenced the *live* action union, so it silently widened every time a
    variant was added — the exact thing its own comment says must never happen.
    An ``edit`` route reaching it now collapses to the universal human stop
    rather than raising at the moment a caller asks what it may do.
    """

    control = derive_agent_control(
        reason="The manifest must be repaired.",
        next_action=CodingAgentEditAction(
            kind="edit",
            path="shipgate.yaml:3",
            expects="doctor runs without ConfigError",
            why="Loader rejected shipgate.yaml.",
        ),
    )
    assert control.state == "agent_action_required"

    projected = project_legacy_agent_control(control)
    assert projected["state"] == "human_review_required"
    assert projected["must_stop"] is True
    assert projected["next_action"]["kind"] == "review"
    assert freeze_agent_control(control).state == "human_review_required"


def test_the_frozen_codex_schema_does_not_know_the_edit_action():
    """Pinned against the committed document, which is the published promise."""

    schema = json.loads((REPO_ROOT / "docs/codex-boundary-result-schema.v2.json").read_text())
    assert "CodingAgentEditAction" not in schema.get("$defs", {})
    assert "CodingAgentEditAction" not in json.dumps(schema)


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


def test_a_configured_workspace_is_routed_to_the_gate_not_back_to_init(unadopted: Path):
    """``detect`` kept naming a command it knows would be refused.

    ``DetectResult.next_action`` says ``init`` whenever the workspace is
    adoptable, including when it has already been adopted — and ``init --write``
    refuses to overwrite. The control route uses the manifest's presence, a fact
    detect already computes for its own diagnostics.
    """

    runner.invoke(app, ["init", "--workspace", str(unadopted), "--write", "--json"])

    detect = _control(["detect", "--workspace", str(unadopted), "--json"])

    assert detect["decision"] == "setup_complete"
    assert detect["next_action"]["kind"] == "verify"
    assert "init" not in detect["next_action"]["command"]


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
            {"path": "agent.declared_purpose.CHANGE_ME", "current": "CHANGE_ME", "line": 13}
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
