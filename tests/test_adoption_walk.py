"""The #327 adoption walk, driven end to end by the shared control envelope.

This is #323's sixth acceptance criterion and #327's definition of done: one
fixture takes an unadopted repository through preview → init → the gate using
*only* ``shipgate.agent_control/v1`` to decide what to do next. Nothing here
reads a command-specific result field to route, constructs a command by hand,
or looks at the workspace to work out what stage it is in.

That constraint is the point. Every defect #327 collects was found by walking
the flow by hand, and none by a test, because each command was tested against
its own output rather than against the next command's input. A walk driven by
the envelope alone fails the moment two steps stop composing — which is how the
one below found that ``init --write`` over an existing, loadable manifest
published ``edit shipgate.yaml`` with an ``expects`` the file already satisfied.
An envelope-only caller opened it, found nothing to change, re-ran, and got the
same action back forever; re-running the command that stopped is the only
resume such a caller has after a human resolves a declaration, so the walk could
not leave stage 2.

**Where the envelope comes from.** Setup commands run before any control
identity exists, so they carry it on their own ``--json`` payload. A command
that publishes a control *pointer* — ``verify`` — has its envelope read back
through the promoted read, ``agents-shipgate agent control``, which runs the
currency protocol against the live workspace. That is the published contract
(``AGENTS.md``), not a convenience: ``verify --json`` prints ``verifier.json``,
whose ``control`` field is the durable ``AgentControl`` union that six
published schemas embed and that this envelope projects.

**Who resolves a human step.** The envelope-only rule binds the *coding agent*.
A ``human_review_required`` route deliberately carries no command, so the
scripted human below resolves it out of band and the walk resumes by re-running
the command that stopped. That resume is not a shortcut the fixture invented —
it is the only one available to a caller that routes on the envelope.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from agents_shipgate.schemas.agent_control_envelope import (
    AGENT_CONTROL_ENVELOPE_SCHEMA_VERSION,
    PROSE_TRUNCATION_MARKER,
    SETUP_DECISIONS,
    SETUP_OPERATIONS,
    validate_agent_control_envelope,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# The document external consumers validate against. Every step of the walk is
# checked against it as well as against the model, because a payload accepted
# only in Python is published as valid to everyone reading the schema.
_PUBLISHED_SCHEMA = Draft202012Validator(
    json.loads((REPO_ROOT / "docs/agent-control-schema.v1.json").read_text(encoding="utf-8"))
)

# The walk is bounded so a routing cycle fails as a cycle rather than as a
# timeout. The shipped walk is five steps — preview, init, the init resumed
# after the human declaration, doctor, verify — and this leaves room for a
# sixth to be added without the bound becoming the thing that fails.
MAX_WALK_STEPS = 8

PLACEHOLDER = "CHANGE_ME"


ADK_AGENT_SOURCE = '''"""A small ADK agent, in the shape an unadopted repository publishes one."""

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool


def read_ticket(ticket_id: str) -> str:
    """Read a support ticket by id."""

    return ticket_id


def close_ticket(ticket_id: str) -> str:
    """Close a support ticket."""

    return ticket_id


root_agent = LlmAgent(
    name="support_agent",
    model="gemini-2.0-flash",
    tools=[FunctionTool(read_ticket), FunctionTool(close_ticket)],
)
'''


def _git(workspace: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Walk",
            "GIT_AUTHOR_EMAIL": "walk@example.invalid",
            "GIT_COMMITTER_NAME": "Walk",
            "GIT_COMMITTER_EMAIL": "walk@example.invalid",
        },
    )


@pytest.fixture
def unadopted_repository(tmp_path: Path) -> Path:
    """A committed agent repository with no manifest and no reports directory."""

    workspace = tmp_path / "demo"
    (workspace / "agents").mkdir(parents=True)
    (workspace / "agents" / "app.py").write_text(ADK_AGENT_SOURCE, encoding="utf-8")
    _git(workspace, "init", "-q", ".")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-qm", "seed")
    return workspace


@dataclass
class _Step:
    """One executed command and the envelope it published."""

    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str
    envelope: dict
    # Where the envelope was found, so the walk can assert the streams are not
    # two shapes: "stdout" | "stderr" | "pointer".
    stream: str
    payload: dict | None = None
    error_lines: list[dict] = field(default_factory=list)


class _Walk:
    """Runs commands as real subprocesses and reads back one envelope each."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.steps: list[_Step] = []

    # -- process ----------------------------------------------------------
    def _run(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv,
            cwd=self.workspace,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PYTHONPATH": str(REPO_ROOT / "src"),
                "AGENTS_SHIPGATE_AGENT_MODE": "1",
                # The walk must not be routed by whichever harness, entry
                # point, or adapter set happens to be configured where the
                # suite runs. `AGENTS_SHIPGATE_CLI` is the load-bearing one:
                # it names the entry point every emitted command is spelled
                # for, and the walk *executes* those commands — an exported
                # value would send the walk into a different install.
                "AGENTS_SHIPGATE_CLI": "",
                "AGENTS_SHIPGATE_ENABLE_PLUGINS": "",
                "CLAUDECODE": "",
                "CURSOR_TRACE_ID": "",
            },
        )

    def cli(self, *args: str) -> list[str]:
        return [sys.executable, "-m", "agents_shipgate", *args]

    # -- envelope ---------------------------------------------------------
    @staticmethod
    def _error_lines(stderr: str) -> list[dict]:
        lines = []
        for line in stderr.splitlines():
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict) and "error" in parsed:
                lines.append(parsed)
        return lines

    def _pointer_envelope(self) -> dict:
        """The promoted read for a run that published a control identity."""

        result = self._run(self.cli("agent", "control", "--workspace", "."))
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def execute(self, argv: list[str], *, from_pointer: bool = False) -> _Step:
        result = self._run(argv)
        errors = self._error_lines(result.stderr)
        payload: dict | None = None
        try:
            decoded = json.loads(result.stdout)
        except ValueError:
            decoded = None
        if isinstance(decoded, list) and decoded:
            # `doctor` answers per manifest. One manifest here, so one answer.
            decoded = decoded[0]
        if isinstance(decoded, dict):
            payload = decoded

        envelope, stream = self._envelope_for(
            result, payload=payload, errors=errors, from_pointer=from_pointer
        )
        assert envelope is not None, (
            "no control envelope on any documented entry point for "
            f"{shlex.join(argv)}\nstdout={result.stdout[:400]}\n"
            f"stderr={result.stderr[:800]}"
        )
        step = _Step(
            argv=argv,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            envelope=envelope,
            stream=stream,
            payload=payload,
            error_lines=errors,
        )
        self.steps.append(step)
        return step

    def _envelope_for(
        self,
        result: subprocess.CompletedProcess[str],
        *,
        payload: dict | None,
        errors: list[dict],
        from_pointer: bool,
    ) -> tuple[dict | None, str]:
        """The four documented places one step's envelope can be, and no others.

        Spelled out rather than searched for, because "find the envelope
        somewhere" is exactly the per-command shape-learning the rollout exists
        to remove. A step whose envelope is in none of these is a step an
        envelope-only caller cannot route on, whatever else it printed.
        """

        if from_pointer:
            # A run that published a control identity. The envelope is the
            # promoted read, which revalidates the pointer against the live
            # workspace before answering.
            return self._pointer_envelope(), "pointer"
        if payload is not None:
            if payload.get("schema_version") == AGENT_CONTROL_ENVELOPE_SCHEMA_VERSION:
                # `--format control` / `--format agent-control-json`: the
                # document *is* the envelope.
                return payload, "stdout"
            if isinstance(payload.get("control"), dict):
                # A setup command's own `--json` payload.
                return payload["control"], "stdout"
        carrying = [line for line in errors if isinstance(line.get("control"), dict)]
        if carrying:
            # A run that could not finish: the agent-mode error line.
            return carrying[-1]["control"], "stderr"
        return None, "none"


def _assert_shared_envelope(step: _Step) -> None:
    """Every step speaks the one vocabulary, whichever command produced it."""

    envelope = step.envelope
    where = shlex.join(step.argv)
    assert envelope["schema_version"] == AGENT_CONTROL_ENVELOPE_SCHEMA_VERSION, where
    # Both layers, on a real emission rather than a constructed one: the
    # discriminated union is what makes a contradictory state unrepresentable,
    # and only the published document proves that to a consumer outside Python.
    validate_agent_control_envelope(envelope)
    assert not list(_PUBLISHED_SCHEMA.iter_errors(envelope)), where
    # The five fields a caller routes on, present on every step regardless of
    # which command answered — criterion 1.
    for required in ("operation", "control_state", "permissions", "decision_source", "decision"):
        assert required in envelope, f"{required} missing from {where}"

    # Criterion 3: a setup-derived state and a gate-derived one name their
    # source, and the vocabularies do not overlap.
    if envelope["operation"] in SETUP_OPERATIONS:
        assert envelope["decision_source"] == "setup", where
        assert envelope["decision"] in SETUP_DECISIONS, where
        # Setup authorizes nothing: it read no diff.
        assert set(envelope["permissions"].values()) == {False}, where
        assert envelope["control_state"] != "complete", where
        assert envelope["current_control_id"] is None, where
        assert envelope["artifacts"] == {}, where
    else:
        assert envelope["decision_source"] != "setup", where
        assert envelope["decision"] not in SETUP_DECISIONS, where

    # Criterion 2: one typed rank-1 action on every non-complete state.
    if envelope["control_state"] == "complete":
        assert envelope["next_action"] is None, where
    else:
        action = envelope["next_action"]
        assert isinstance(action, dict), where
        assert action["actor"] in {"coding_agent", "human"}, where
        assert action["kind"], where
        assert action["why"], where
        if action["actor"] == "human":
            # A human route never publishes a command an agent could run.
            assert action["command"] is None, where

    # Criterion 5: the command-specific fields survive and cannot disagree with
    # the envelope, so a pre-envelope consumer and an envelope-only one are
    # never sent to different work.
    if step.payload is not None and "next_actions" in step.payload:
        ranked = step.payload["next_actions"]
        assert ranked, where
        action = envelope["next_action"]
        assert action is not None, where
        if action["actor"] == "human":
            assert ranked[0]["kind"] in {"review", "stop"}, where
            assert ranked[0].get("command") is None, where
            # Same route, and the envelope's copy may be the capped one: the
            # ranked list is uncapped, `why` on the envelope is truncated at
            # `MAX_ENVELOPE_PROSE_BYTES` with a visible marker. What must hold
            # is that one is the other, whole or abridged — not that a long
            # sentence is silently a different sentence.
            envelope_why, ranked_why = action["why"], ranked[0]["why"]
            if envelope_why != ranked_why:
                assert envelope_why.endswith(PROSE_TRUNCATION_MARKER), where
                head = envelope_why[: -len(PROSE_TRUNCATION_MARKER)].rstrip()
                assert ranked_why.startswith(head), where
        elif action["kind"] == "edit":
            assert ranked[0]["kind"] == "edit", where
            assert ranked[0]["path"] == action["path"], where
        else:
            assert ranked[0]["command"] == action["command"], where


def _route_key(step: _Step) -> tuple:
    """What this step asked for, for the cycle detector.

    ``input_id`` content-addresses the subject a setup answer was computed
    against, so an unchanged subject asking for the same action twice is a
    route that cannot advance — the exact shape of a non-advancing step.
    """

    action = step.envelope["next_action"] or {}
    return (
        step.envelope["operation"],
        step.envelope.get("input_id"),
        action.get("kind"),
        action.get("command"),
        action.get("path"),
    )


def _resolve_human_declaration(workspace: Path) -> bool:
    """The scripted human, supplying the declarations only a person may make.

    Values only. ``CHANGE_ME`` also appears in the template's own comments —
    "Replace CHANGE_ME with a one-line description" — and rewriting the
    instructions instead of the field would let this report success for a
    manifest that still owes a declaration.
    """

    manifest = next(
        path
        for path in (
            workspace / ".agents-shipgate-local-review.yaml",
            workspace / "shipgate.yaml",
        )
        if path.is_file()
    )
    before = manifest.read_text(encoding="utf-8")
    answer = "Read and close customer support tickets"
    lines = []
    for line in before.splitlines(keepends=True):
        value = line.split("#", 1)[0].strip()
        if value == f"- {PLACEHOLDER}":
            lines.append(line.replace(f"- {PLACEHOLDER}", f"- {answer}", 1))
        elif value.endswith(f": {PLACEHOLDER}"):
            lines.append(line.replace(f": {PLACEHOLDER}", f": {answer}", 1))
        else:
            lines.append(line)
    after = "".join(lines)
    if after == before:
        return False
    manifest.write_text(after, encoding="utf-8")
    return True


def _fake_step(*, operation: str, input_id: str | None, action: dict | None) -> _Step:
    return _Step(
        argv=["shipgate", operation],
        exit_code=0,
        stdout="",
        stderr="",
        envelope={"operation": operation, "input_id": input_id, "next_action": action},
        stream="stdout",
    )


def test_a_route_is_the_same_route_only_when_both_subject_and_step_are():
    """The cycle detector, exercised directly rather than only in the walk.

    A guard that never fires on the happy path is still a guard, but one whose
    key does not actually discriminate would never fire at all. Both halves
    matter: the same action for a *moved* subject is progress, and a different
    action for the same subject is progress too.
    """

    edit = {"kind": "edit", "command": None, "path": "/ws/shipgate.yaml"}
    repeated = _fake_step(operation="init", input_id="sha256:aa", action=edit)
    identical = _fake_step(operation="init", input_id="sha256:aa", action=dict(edit))
    moved_subject = _fake_step(operation="init", input_id="sha256:bb", action=dict(edit))
    moved_step = _fake_step(
        operation="init",
        input_id="sha256:aa",
        action={"kind": "command", "command": "shipgate doctor", "path": None},
    )

    assert _route_key(repeated) == _route_key(identical)
    assert _route_key(repeated) != _route_key(moved_subject)
    assert _route_key(repeated) != _route_key(moved_step)
    # A terminal state carries no action and must not collide with a step that
    # merely published no command.
    assert _route_key(_fake_step(operation="verify", input_id=None, action=None)) == (
        "verify",
        None,
        None,
        None,
        None,
    )


def test_the_adoption_walk_routes_end_to_end_on_the_envelope_alone(
    unadopted_repository: Path,
) -> None:
    """Preview → init → the gate, with the envelope as the only routing input."""

    workspace = unadopted_repository
    walk = _Walk(workspace)

    # Stage 1. The one command the walk chooses for itself, because a walk has
    # to start somewhere; `--format control` is the published entry point for a
    # caller that routes on the envelope.
    step = walk.execute(
        walk.cli("verify", "--preview", "--workspace", str(workspace), "--format", "control")
    )

    seen: set[tuple] = set()
    human_stops = 0
    reached_gate = False

    for _ in range(MAX_WALK_STEPS):
        _assert_shared_envelope(step)

        if step.envelope["decision_source"] == "release_decision":
            reached_gate = True
            break

        key = _route_key(step)
        assert key not in seen, (
            "the walk was handed the same action for the same subject twice, so "
            f"following it cannot advance: {key}"
        )
        seen.add(key)

        action = step.envelope["next_action"]
        assert action is not None, "a non-complete setup state published no route"

        if action["actor"] == "human":
            human_stops += 1
            assert step.envelope["control_state"] == "human_review_required"
            assert step.envelope["human_review"]["required"] is True
            assert _resolve_human_declaration(workspace), (
                f"the walk stopped for a human with nothing for a human to resolve: {action['why']}"
            )
            # The only resume an envelope-only caller has: re-run the command
            # that stopped.
            step = walk.execute(step.argv)
            continue

        # An `edit` route is legitimate on setup — a manifest the loader
        # rejects is coding-agent work — but it is not reachable on *this*
        # walk: the only file the agent could be sent to is a manifest Shipgate
        # itself just wrote, whose remaining obligation is the human-owned
        # declaration above. One appearing here means a step published work
        # that nothing in the walk asked for, which is how the non-advancing
        # `init --write` route was found.
        assert action["kind"] != "edit", (
            f"the walk was handed a coding-agent file edit with no obligation behind it: {action}"
        )
        command = action["command"]
        assert command, "an agent_action_required route published no command"
        argv = shlex.split(command)
        # A `verify` step publishes a control pointer rather than an envelope,
        # so its answer is read back through the promoted read. The rule comes
        # from the envelope's own typed `kind` — parsing the command string to
        # work out which program it names would be a second grammar for
        # something the route already states.
        step = walk.execute(argv, from_pointer=action["kind"] == "verify")
    else:  # pragma: no cover - the walk is expected to terminate
        pytest.fail(
            "the walk did not reach a release decision in "
            f"{MAX_WALK_STEPS} steps: {[shlex.join(s.argv) for s in walk.steps]}"
        )

    assert reached_gate

    # It went through every stage, in order, and through a human declaration on
    # the way: a flow that never stops is not the goal.
    operations = [s.envelope["operation"] for s in walk.steps]
    assert operations[0] == "preview"
    assert "init" in operations
    assert "doctor" in operations
    assert operations[-1] == "verify"
    assert human_stops == 1, operations

    # Criterion 7: the gate state is a projection of `release_decision`, not a
    # second verdict computed by a renderer.
    gate = walk.steps[-1].envelope
    report = json.loads(
        (workspace / "agents-shipgate-reports" / "report.json").read_text(encoding="utf-8")
    )
    assert gate["decision"] == report["release_decision"]["decision"]
    assert gate["decision_source"] == "release_decision"
    # And the gate — unlike every setup step before it — is the only thing that
    # can authorize anything.
    assert gate["control_state"] in {"complete", "review_publishable", "human_review_required"}


def test_a_setup_failure_routes_on_the_same_envelope_as_a_setup_answer(
    unadopted_repository: Path,
) -> None:
    """Criterion 4: the two documented streams are not two shapes.

    A setup command answers on stdout and, when it cannot finish, on an
    agent-mode error line. Both carried ``next_action``/``next_actions``; only
    some carried ``control``, so whether an envelope-only caller could route at
    all depended on which setup command had failed and on which of its failures.
    """

    walk = _Walk(unadopted_repository)

    # A refusal with no stdout payload at all.
    failure = walk.execute(
        walk.cli(
            "init",
            "--workspace",
            str(unadopted_repository),
            "--write",
            "--control-pack",
            "no-such-pack",
            "--json",
        )
    )
    assert failure.exit_code == 2
    assert failure.stream == "stderr"
    _assert_shared_envelope(failure)
    assert failure.envelope["execution"] == "failed"
    assert failure.envelope["exit_code"] == 2
    assert failure.envelope["decision"] == "setup_incomplete"

    # Following it reaches a run that answers, and the answer is the same shape
    # on the other stream.
    recovery = failure.envelope["next_action"]
    assert recovery["actor"] == "coding_agent"
    answered = walk.execute(shlex.split(recovery["command"]))
    assert answered.stream == "stdout"
    _assert_shared_envelope(answered)

    # And where a command emits both, the two carry the same envelope rather
    # than a payload state beside a different error-line route.
    both = walk.execute(
        walk.cli("init", "--workspace", str(unadopted_repository), "--write", "--json")
    )
    carrying = [line for line in both.error_lines if isinstance(line.get("control"), dict)]
    assert carrying, both.stderr
    assert carrying[-1]["control"] == both.payload["control"]


def _adopted_repository(walk: _Walk) -> Path:
    """The same repository, taken to a state every command can answer about."""

    workspace = walk.workspace
    walk.execute(walk.cli("init", "--workspace", str(workspace), "--write", "--json"))
    assert _resolve_human_declaration(workspace), "init wrote no human-owned declaration"
    return workspace


# Criterion 1, as a table rather than as prose: each command, and the one
# documented entry point at which it publishes the envelope. `scan` is the
# entry that is *not* its own output — a scan pointer binds no reconfirmable
# snapshot, so the envelope for a scan generation is the promoted read, and it
# reports a withheld verdict rather than a decision.
_ENVELOPE_ENTRY_POINTS = (
    ("detect", ("detect", "--workspace", ".", "--json"), False),
    ("init", ("init", "--workspace", ".", "--json"), False),
    ("doctor", ("doctor", "--config", "shipgate.yaml", "--json"), False),
    ("check", ("check", "--workspace", ".", "--format", "agent-control-json"), False),
    ("verify", ("verify", "--workspace", ".", "--format", "control"), False),
    (
        "scan",
        ("scan", "-c", "shipgate.yaml", "--format", "json", "--ci-mode", "advisory"),
        True,
    ),
)


@pytest.mark.parametrize(
    ("operation", "argv", "from_pointer"),
    _ENVELOPE_ENTRY_POINTS,
    ids=[entry[0] for entry in _ENVELOPE_ENTRY_POINTS],
)
def test_every_command_publishes_the_shared_envelope(
    unadopted_repository: Path,
    operation: str,
    argv: tuple[str, ...],
    from_pointer: bool,
) -> None:
    """All six commands answer "what may I do next" in one vocabulary.

    The rollout's first criterion, checked at the entry point each command
    documents rather than assumed from the three that carry it on `--json`.
    """

    walk = _Walk(unadopted_repository)
    _adopted_repository(walk)

    step = walk.execute(walk.cli(*argv), from_pointer=from_pointer)

    assert step.envelope["operation"] == operation
    _assert_shared_envelope(step)
    # And the permission vector is the same six-way object everywhere, so a
    # caller can ask "may I merge" without knowing which command answered.
    assert set(step.envelope["permissions"]) == {
        "edit",
        "commit",
        "push",
        "update_pr",
        "merge",
        "report_complete",
    }


# ---------------------------------------------------------------------------
# Following the emitted routes, not reading them
# ---------------------------------------------------------------------------

# What `--minimal` writes and detection does not. The two templates differ in
# more than this, but a scaffold tool source is the difference that decides
# whether the request survived: `--minimal` writes placeholders, detection
# writes the surface it read.
SCAFFOLD_SOURCE = "type: CHANGE_ME"
DETECTED_SOURCE = "type: google_adk"


def test_a_failure_recovery_delivers_what_the_invocation_asked_for(
    unadopted_repository: Path,
) -> None:
    """Run the recovery, then look at what it produced.

    Asserting the *shape* of a recovery command says nothing about whether
    following it completes the run it is correcting. This one did not: every
    recovery was built from the flag list a rerun in a **different** workspace
    may repeat, which omits `--minimal` (and `--allow-unresolved-scope`, an
    explicit kit, and a raised parse cap). So `init --write --minimal
    --control-pack <bad>` emitted a recovery without `--minimal`, and following
    it wrote a detected manifest where a legacy template was asked for — while
    reporting success.
    """

    workspace = unadopted_repository
    walk = _Walk(workspace)

    failure = walk.execute(
        walk.cli(
            "init",
            "--workspace",
            str(workspace),
            "--write",
            "--minimal",
            "--control-pack",
            "no-such-pack",
            "--json",
        )
    )
    assert failure.exit_code == 2
    _assert_shared_envelope(failure)

    recovery = failure.envelope["next_action"]
    assert recovery["actor"] == "coding_agent"
    answered = walk.execute(shlex.split(recovery["command"]))
    _assert_shared_envelope(answered)

    manifest = (workspace / "shipgate.yaml").read_text(encoding="utf-8")
    assert SCAFFOLD_SOURCE in manifest, manifest
    assert DETECTED_SOURCE not in manifest, manifest


def test_the_recovery_carries_the_default_when_that_is_what_was_asked_for(
    unadopted_repository: Path,
) -> None:
    """The other half of the pair, so the assertion above is not vacuous.

    A recovery that dropped every option would also pass a test that only
    checked the option it happens to carry. Without `--minimal` the same
    recovery must write the detected surface.
    """

    workspace = unadopted_repository
    walk = _Walk(workspace)

    failure = walk.execute(
        walk.cli(
            "init",
            "--workspace",
            str(workspace),
            "--write",
            "--control-pack",
            "no-such-pack",
            "--json",
        )
    )
    walk.execute(shlex.split(failure.envelope["next_action"]["command"]))

    manifest = (workspace / "shipgate.yaml").read_text(encoding="utf-8")
    assert DETECTED_SOURCE in manifest, manifest
    assert SCAFFOLD_SOURCE not in manifest, manifest


def test_an_unapplied_configuration_request_routes_somewhere_that_changes_it(
    unadopted_repository: Path,
) -> None:
    """The route for a request a refused write did not apply, followed.

    `init --write` over an existing manifest hands the caller onward. When this
    invocation asked for a control pack the manifest does not select, both
    onward routes advance under the pack that is *there* — so the request is
    lost and nothing says so. The route has to name the field and the value,
    and making that edit has to be what moves the answer.
    """

    workspace = unadopted_repository
    walk = _Walk(workspace)
    _adopted_repository(walk)

    asked = walk.execute(
        walk.cli(
            "init",
            "--workspace",
            str(workspace),
            "--write",
            "--control-pack",
            "financial-strict",
            "--json",
        )
    )
    _assert_shared_envelope(asked)
    action = asked.envelope["next_action"]
    assert action["actor"] == "coding_agent"
    assert action["kind"] == "edit"
    assert action["path"] == str(workspace / "shipgate.yaml")
    assert "policies.control_pack" in action["expects"]
    assert "financial-strict" in action["expects"]

    # Make exactly the edit the route names, then ask again. The obligation has
    # to be gone — a route whose postcondition holds and which still reports
    # itself is the defect this PR exists to remove.
    manifest = Path(action["path"])
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "control_pack: default", "control_pack: financial-strict"
        ),
        encoding="utf-8",
    )
    again = walk.execute(asked.argv)
    _assert_shared_envelope(again)
    assert again.envelope["next_action"] != action
    assert again.envelope["next_action"]["kind"] != "edit"


# ---------------------------------------------------------------------------
# Scoped routing: the emitted command has to do what its `expects` promises
# ---------------------------------------------------------------------------

_TWO_PROJECT_AGENT = '''from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool


def act(value: str) -> str:
    """Do one thing."""

    return value


root_agent = LlmAgent(name="bot_{name}", model="gemini-2.0-flash", tools=[FunctionTool(act)])
'''

_ADOPTED_CANDIDATE_MANIFEST = """version: "0.1"
project: {name: a}
agent: {name: bot_a, declared_purpose: [act]}
environment: {target: local}
tool_sources: [{id: src, type: mcp, path: tools.json}]
policies: {control_pack: default}
"""


@pytest.fixture
def ambiguous_workspace(tmp_path: Path) -> Path:
    """Two self-contained agent projects, so `init --write` refuses to choose."""

    workspace = tmp_path / "mono"
    for name in ("a", "b"):
        project = workspace / name
        project.mkdir(parents=True)
        (project / "agent.py").write_text(_TWO_PROJECT_AGENT.format(name=name), encoding="utf-8")
        (project / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "0.1"\n', encoding="utf-8"
        )
    _git(workspace, "init", "-q", ".")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-qm", "seed")
    return workspace


def _rank_one(step: _Step) -> dict:
    return step.envelope["next_action"]


def test_the_capped_retry_repeats_the_invocation_it_is_retrying(
    ambiguous_workspace: Path,
) -> None:
    """The one scope route that reruns *this* workspace, followed to the end.

    It replaces the parse cap and nothing else, so it owes every other option
    this invocation carried. It was built from the flag list a rerun in a
    *different* workspace may repeat, so `--allow-unresolved-scope` was
    dropped — and following the retry exactly returned the same
    `refused_unresolved_scope` it was issued to resolve, writing no manifest.
    """

    walk = _Walk(ambiguous_workspace)
    refusal = walk.execute(
        walk.cli(
            "init",
            "--workspace",
            str(ambiguous_workspace),
            "--write",
            "--allow-unresolved-scope",
            "--max-python-files",
            "1",
            "--json",
        )
    )
    _assert_shared_envelope(refusal)
    retry = _rank_one(refusal)
    assert retry["actor"] == "coding_agent"
    argv = shlex.split(retry["command"])
    assert "--allow-unresolved-scope" in argv
    # Only the cap is replaced, and it is replaced once.
    assert argv.count("--max-python-files") == 1
    assert argv[argv.index("--max-python-files") + 1] != "1"

    walk.execute(argv)
    assert (ambiguous_workspace / "shipgate.yaml").is_file(), (
        "following the retry exactly did not produce the manifest it promised"
    )


def test_a_candidate_command_carries_the_bound_the_root_run_was_given(
    ambiguous_workspace: Path,
) -> None:
    """A cap bounds how much is read, not which workspace, so it transfers.

    Candidate commands were built from the smallest flag list, which omits it —
    so a candidate inherited the root's truncation, refused again, and wrote no
    manifest while its `expects` promised one.
    """

    walk = _Walk(ambiguous_workspace)
    refusal = walk.execute(
        walk.cli(
            "init",
            "--workspace",
            str(ambiguous_workspace),
            "--write",
            "--max-python-files",
            "4321",
            "--json",
        )
    )
    candidate_commands = [
        shlex.split(action["command"])
        for action in refusal.payload["next_actions"]
        if action.get("command") and "init" in shlex.split(action["command"])
    ]
    assert candidate_commands, refusal.payload["next_actions"]
    for argv in candidate_commands:
        assert argv[argv.index("--max-python-files") + 1] == "4321", argv


def test_an_adopted_candidate_reconciles_the_pack_before_handing_off(
    ambiguous_workspace: Path,
) -> None:
    """The same loss as the workspace's own route, one directory down.

    A candidate that already carries a manifest routes to `doctor`. `doctor`
    reads a manifest that loads fine and advances to the gate under the pack
    that is *there* — so a root run asking for a different pack lost the
    request with nothing saying so.
    """

    project = ambiguous_workspace / "a"
    (project / "tools.json").write_text(
        json.dumps({"tools": [{"name": "act", "description": "An action."}]}),
        encoding="utf-8",
    )
    (project / "shipgate.yaml").write_text(_ADOPTED_CANDIDATE_MANIFEST, encoding="utf-8")

    walk = _Walk(ambiguous_workspace)
    refusal = walk.execute(
        walk.cli(
            "init",
            "--workspace",
            str(ambiguous_workspace),
            "--write",
            "--control-pack",
            "financial-strict",
            "--json",
        )
    )
    routes = refusal.payload["next_actions"]
    for_candidate = [
        action for action in routes if action.get("path") == str(project / "shipgate.yaml")
    ]
    assert for_candidate, routes
    action = for_candidate[0]
    assert action["kind"] == "edit"
    assert "policies.control_pack" in action["expects"]
    assert "financial-strict" in action["expects"]
    # And no route for that candidate hands it to `doctor` while the request
    # is outstanding.
    doctor_routes = [
        action
        for action in routes
        if action.get("command") and str(project / "shipgate.yaml") in action["command"]
    ]
    assert not doctor_routes, doctor_routes


def test_an_explicit_default_pack_is_a_request_and_a_weakening(
    unadopted_repository: Path,
) -> None:
    """The request a default-comparison cannot see, and the direction rule.

    `--control-pack default` over a `financial-strict` manifest asks for the
    one transition that can only require less. Inferring "was this asked for?"
    by comparing against the default made it invisible; and routing it as a
    coding-agent edit would have let a governed agent loosen its own gate by
    choosing its own argv.
    """

    workspace = unadopted_repository
    walk = _Walk(workspace)
    _adopted_repository(walk)
    manifest = workspace / "shipgate.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "control_pack: default", "control_pack: financial-strict"
        ),
        encoding="utf-8",
    )

    asked = walk.execute(
        walk.cli(
            "init",
            "--workspace",
            str(workspace),
            "--write",
            "--control-pack",
            "default",
            "--json",
        )
    )
    _assert_shared_envelope(asked)
    action = _rank_one(asked)
    assert asked.envelope["control_state"] == "human_review_required"
    assert action["actor"] == "human"
    assert action["command"] is None
    assert "financial-strict" in action["why"]
    assert set(asked.envelope["permissions"].values()) == {False}

    # …while omitting the option entirely is not a request at all.
    quiet = walk.execute(walk.cli("init", "--workspace", str(workspace), "--write", "--json"))
    _assert_shared_envelope(quiet)
    assert quiet.envelope["control_state"] == "agent_action_required"
    assert _rank_one(quiet)["kind"] == "configure"
