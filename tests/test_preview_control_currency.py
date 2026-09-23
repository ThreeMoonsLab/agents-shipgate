"""`verify --preview` publishes the route it prints, configured or not (#807).

In a repository with a committed `shipgate.yaml`, `verify --preview --json`
answered `agent_action_required` with the exact `verify` command, while `verify
--preview --format control` answered `human_review_required` ("The recorded
source and dependency inputs are no longer current: input directory capture is
unavailable; re-run verification") and every `agent control` refresh exited 4
with `workspace_unverifiable`. Re-running only reproduced it, for the default,
an in-repository and a sibling `--out` alike.

The cause was the plan: a manifest lets the preview record the verification
plan a `verify` would run, and its pointer bound that plan as its currency
evidence. A preview runs no adapter, so the plan's inputs were never captured
and it carries no directory census, which every reader refuses. The preview
pointer now binds the route it wrote and the working tree it read, exactly as
a manifest-free preview's does, and the refusal for a bound plan without a
census is unchanged.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "samples" / "clean_read_only_agent"
REPORTS = "agents-shipgate-reports"
# Locked text environment: no colour, no CI annotations, no terminal wrapping.
ENV = {"NO_COLOR": "1", "GITHUB_ACTIONS": None, "FORCE_COLOR": None, "COLUMNS": "200"}
AGENT_ENV = {**ENV, "AGENTS_SHIPGATE_AGENT_MODE": "1"}
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# The three output-directory classes the shared containment rule (#575, #804)
# distinguishes: gitignored inside the repository, inside it and holding only
# Shipgate artifacts, and outside it.
OUTS = ("default", "in_repository", "sibling")

runner = CliRunner()


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repository(tmp_path: Path, *, manifest: bool) -> Path:
    """The clean read-only sample as a one-commit repository, with or without its manifest."""

    repo = tmp_path / "repo"
    repo.mkdir()
    names = ("shipgate.yaml", "tools.json") if manifest else ("tools.json",)
    for name in names:
        shutil.copy(SAMPLE / name, repo / name)
    (repo / ".gitignore").write_text(f"{REPORTS}/\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo


def _out(repo: Path, tmp_path: Path, where: str) -> tuple[list[str], list[str], Path]:
    """`verify` flags, `agent control` flags, and the directory both use.

    The default reports directory is refreshed with no `--reports-dir` at all,
    the way a default `verify --workspace` pairs with `agent control`.
    """

    if where == "default":
        return [], [], repo / REPORTS
    if where == "in_repository":
        # Not gitignored: it holds nothing but the Shipgate artifacts written here.
        return ["--out", "reports"], ["--reports-dir", "reports"], repo / "reports"
    sibling = tmp_path / "sibling-reports"
    return ["--out", str(sibling)], ["--reports-dir", str(sibling)], sibling


def _preview(out_flags: list[str], *fmt: str):
    return runner.invoke(
        app,
        [
            "verify", "--workspace", ".", "--config", "shipgate.yaml", "--preview",
            *out_flags, *fmt,
        ],
        env=ENV,
    )


def _refresh(reports_flags: list[str], env=ENV):
    return runner.invoke(app, ["agent", "control", "--workspace", ".", *reports_flags], env=env)


def _refusal(result) -> str:
    lines = [
        line
        for line in _plain(result.output).splitlines()
        if line.startswith("Current control is unavailable")
    ]
    assert lines, _plain(result.output)
    return lines[-1]


def _error_line(output: str) -> dict:
    lines = [line for line in _plain(output).splitlines() if line.startswith('{"error"')]
    assert lines, f"no agent-mode error line in:\n{output}"
    return json.loads(lines[-1])


@pytest.mark.parametrize("where", OUTS)
@pytest.mark.parametrize("manifest", [True, False], ids=["configured", "manifest_free"])
def test_preview_control_is_the_json_route_and_stays_current_until_the_tree_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest: bool, where: str
):
    repo = _repository(tmp_path, manifest=manifest)
    monkeypatch.chdir(repo)
    out_flags, reports_flags, reports = _out(repo, tmp_path, where)

    as_json = _preview(out_flags, "--json")
    assert as_json.exit_code == 0, _plain(as_json.output)
    control = json.loads(as_json.stdout)["control"]
    assert control["state"] == "agent_action_required"
    route = control["next_action"]

    # The control format reports the same route, not a human stop.
    as_control = _preview(out_flags, "--format", "control")
    assert as_control.exit_code == 0, _plain(as_control.output)
    envelope = json.loads(as_control.stdout)
    assert envelope["control_state"] == "agent_action_required", envelope
    assert envelope["operation"] == "preview"
    assert envelope["next_action"]["kind"] == route["kind"]
    assert envelope["next_action"]["command"] == route["command"]
    assert "input directory capture" not in json.dumps(envelope)
    argv = shlex.split(route["command"])
    if manifest:
        assert route["kind"] == "verify"
        assert "verify" in argv and "--preview" not in argv
        assert envelope["reason"] == (
            "Shipgate is configured; run verify on the PR to get a merge verdict."
        )
    else:
        assert route["kind"] == "initialize"
        assert "init" in argv

    # A manifest still lets the preview record the plan a `verify` would run,
    # but that is not evidence this run evaluated, so the pointer never binds it.
    pointer = json.loads((reports / "current-control.json").read_text(encoding="utf-8"))
    assert pointer["operation"] == "preview"
    assert "verification_plan" not in pointer["artifacts"]
    assert "verification_plan" not in envelope["artifacts"]
    assert (reports / "verification-plan.json").is_file() is manifest
    assert pointer["workspace_identity"]["snapshot_kind"] == "worktree_overlay"

    # Current while the worktree is unchanged: the same generation, the same route.
    refreshed = _refresh(reports_flags)
    assert refreshed.exit_code == 0, _plain(refreshed.output)
    current = json.loads(refreshed.stdout)
    assert current["control_state"] == "agent_action_required"
    assert current["current_control_id"] == envelope["current_control_id"]
    assert current["current_control_id"] == pointer["current_control_id"]
    assert current["next_action"]["command"] == route["command"]

    # A tracked edit moves the tree the answer was read from ...
    tools = repo / "tools.json"
    original = tools.read_text(encoding="utf-8")
    tools.write_text(original + "\n", encoding="utf-8")
    tracked = _refresh(reports_flags)
    assert tracked.exit_code == 4, _plain(tracked.output)
    assert "(workspace_changed)" in _refusal(tracked)
    assert "(tools.json)" in _refusal(tracked)

    # ... undoing it restores exactly the tree that was read ...
    tools.write_text(original, encoding="utf-8")
    restored = _refresh(reports_flags)
    assert restored.exit_code == 0, _plain(restored.output)
    assert json.loads(restored.stdout)["current_control_id"] == pointer["current_control_id"]

    # ... an untracked file is a change it never read either ...
    (repo / "agent.py").write_text("print('hello')\n", encoding="utf-8")
    untracked = _refresh(reports_flags)
    assert untracked.exit_code == 4, _plain(untracked.output)
    assert "(workspace_changed)" in _refusal(untracked)
    assert "(agent.py)" in _refusal(untracked)
    (repo / "agent.py").unlink()
    assert _refresh(reports_flags).exit_code == 0

    # ... and so is a removal.
    tools.unlink()
    removed = _refresh(reports_flags)
    assert removed.exit_code == 4, _plain(removed.output)
    assert "(workspace_changed)" in _refusal(removed)
    assert "(tools.json)" in _refusal(removed)


def test_a_preview_of_uncommitted_work_is_current_over_that_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The tree read is the dirty one, so the same dirty tree is still current."""

    repo = _repository(tmp_path, manifest=True)
    monkeypatch.chdir(repo)
    (repo / "agent.py").write_text("print('hello')\n", encoding="utf-8")

    envelope = json.loads(_preview([], "--format", "control").stdout)
    assert envelope["control_state"] == "agent_action_required", envelope

    refreshed = _refresh([])
    assert refreshed.exit_code == 0, _plain(refreshed.output)
    assert json.loads(refreshed.stdout)["current_control_id"] == envelope["current_control_id"]

    (repo / "agent.py").write_text("print('changed')\n", encoding="utf-8")
    edited = _refresh([])
    assert edited.exit_code == 4, _plain(edited.output)
    assert "(workspace_changed)" in _refusal(edited)


@pytest.mark.parametrize("manifest", [True, False], ids=["configured", "manifest_free"])
def test_a_preview_under_refused_git_config_is_refused_with_its_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest: bool
):
    """Unbinding the plan must not unbind the working tree (#813 stays closed).

    Under Git configuration the worktree readers refuse, the preview cannot
    read the overlay it would bind. Its pointer used to declare no snapshot
    kind then, so the refresh compared HEAD alone and returned a manifest-free
    preview as current over any later edit. Both previews now declare the
    worktree snapshot and are refused with the cause, as every other pointer
    in such a repository is.
    """

    repo = _repository(tmp_path, manifest=manifest)
    _git(repo, "config", "diff.lockb.textconv", "bun")
    monkeypatch.chdir(repo)
    as_json = _preview([], "--json")
    assert json.loads(as_json.stdout)["control"]["state"] == "agent_action_required"
    as_control = _preview([], "--format", "control")
    assert as_control.exit_code == 0, _plain(as_control.output)
    # The run's own answer goes through the same read, so it is withheld too.
    envelope = json.loads(as_control.stdout)
    assert envelope["control_state"] == "human_review_required", envelope
    assert "diff.lockb.textconv" in envelope["reason"]
    pointer = json.loads((repo / REPORTS / "current-control.json").read_text(encoding="utf-8"))
    assert pointer["workspace_identity"]["snapshot_kind"] == "worktree_overlay"
    assert "verification_plan" not in pointer["artifacts"]

    for edit in (None, "agent.py"):
        if edit is not None:
            (repo / edit).write_text("print('hello')\n", encoding="utf-8")
        refused = _refresh([], env=AGENT_ENV)
        assert refused.exit_code == 4, _plain(refused.output)
        refusal = _refusal(refused)
        assert "(workspace_unverifiable)" in refusal
        assert refusal.split("): ", 1)[1].startswith("Deterministic diff collection"), refusal
        assert "diff.lockb.textconv" in refusal
        assert _error_line(refused.output)["next_actions"][0]["kind"] == "review"


@pytest.mark.parametrize(
    "stop",
    [
        ["--config", "missing.yaml"],
        ["--config", "shipgate.yaml", "--head", "no-such-ref"],
    ],
    ids=["missing_config", "missing_head"],
)
def test_a_verify_that_stopped_before_its_plan_is_refused_under_refused_git_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stop: list[str]
):
    """The same plan-less identity, reached by a `verify` that built no plan.

    Such a run publishes the fallback identity a preview now always uses. Under
    Git configuration the worktree readers refuse it declared no snapshot kind
    either, so its route stayed current over a later untracked file; with the
    configuration gone it is still read against the tree as before.
    """

    repo = _repository(tmp_path, manifest=True)
    monkeypatch.chdir(repo)
    args = ["verify", "--workspace", ".", *stop, "--format", "control"]

    clean = runner.invoke(app, args, env=ENV)
    assert clean.exit_code == 2, _plain(clean.output)
    assert json.loads(clean.stdout)["control_state"] == "agent_action_required"
    pointer = json.loads((repo / REPORTS / "current-control.json").read_text(encoding="utf-8"))
    assert "verification_plan" not in pointer["artifacts"]
    assert pointer["workspace_identity"]["snapshot_kind"] == "worktree_overlay"
    assert _refresh([]).exit_code == 0

    _git(repo, "config", "diff.lockb.textconv", "bun")
    refused_run = runner.invoke(app, args, env=ENV)
    assert refused_run.exit_code == 2, _plain(refused_run.output)
    envelope = json.loads(refused_run.stdout)
    assert envelope["control_state"] == "human_review_required", envelope
    assert "diff.lockb.textconv" in envelope["reason"]
    pointer = json.loads((repo / REPORTS / "current-control.json").read_text(encoding="utf-8"))
    assert pointer["workspace_identity"]["snapshot_kind"] == "worktree_overlay"

    (repo / "agent.py").write_text("print('hello')\n", encoding="utf-8")
    refused = _refresh([], env=AGENT_ENV)
    assert refused.exit_code == 4, _plain(refused.output)
    assert "(workspace_unverifiable)" in _refusal(refused)
    assert "diff.lockb.textconv" in _refusal(refused)
    assert _error_line(refused.output)["next_actions"][0]["kind"] == "review"


def test_a_changed_path_shaped_like_a_credential_is_named_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The drift refusal names the tree's paths, and never a token-shaped one raw."""

    token = "ghp_" + "Q" * 36
    repo = _repository(tmp_path, manifest=True)
    monkeypatch.chdir(repo)
    assert _preview([], "--format", "control").exit_code == 0
    (repo / f"{token}.txt").write_text("x\n", encoding="utf-8")

    refused = _refresh([], env=AGENT_ENV)

    assert refused.exit_code == 4, _plain(refused.output)
    assert "(workspace_changed)" in _refusal(refused)
    assert "[REDACTED:" in _refusal(refused)
    assert token not in refused.output


def test_a_bound_plan_without_its_census_still_refuses_with_a_verify_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Never fail open on a missing input-directory list.

    Earlier builds published a configured preview's pointer over its plan, which
    carries no census. Such a pointer, left in a reports directory, must keep
    refusing after upgrade, and the refusal is a technical one: the next action
    re-runs verification, it does not ask a person to approve missing evidence.
    """

    from agents_shipgate.core.current_control import (
        VERIFIER_ROUTE_CONTROL_ARTIFACT_KEYS,
        publish_current_control,
        workspace_identity_from_plan,
    )
    from agents_shipgate.schemas.current_control import CurrentControlPointer
    from agents_shipgate.schemas.verification_identity import VerificationPlan

    repo = _repository(tmp_path, manifest=True)
    monkeypatch.chdir(repo)
    reports = repo / REPORTS
    assert _preview([], "--format", "control").exit_code == 0
    plan = VerificationPlan.model_validate_json(
        (reports / "verification-plan.json").read_text(encoding="utf-8")
    )
    assert "input_directories" not in plan.inputs.options
    published = CurrentControlPointer.model_validate_json(
        (reports / "current-control.json").read_text(encoding="utf-8")
    )
    # The shape a 1.1.0 configured preview published.
    publish_current_control(
        reports,
        operation="preview",
        control=published.control,
        request_id=published.request_id,
        decision_id=published.decision_id,
        workspace_identity=workspace_identity_from_plan(plan),
        artifact_keys=VERIFIER_ROUTE_CONTROL_ARTIFACT_KEYS,
    )
    legacy = json.loads((reports / "current-control.json").read_text(encoding="utf-8"))
    assert "verification_plan" in legacy["artifacts"]

    refused = _refresh([], env=AGENT_ENV)
    assert refused.exit_code == 4, _plain(refused.output)
    refusal = _refusal(refused)
    assert "(workspace_unverifiable)" in refusal
    assert "input directory capture is unavailable" in refusal
    action = _error_line(refused.output)["next_actions"][0]
    assert action["kind"] == "command"
    assert "verify" in shlex.split(action["command"])

    # Re-running the preview replaces it with a pointer that reads current.
    assert _preview([], "--format", "control").exit_code == 0
    assert _refresh([]).exit_code == 0


def test_the_preview_plan_is_still_refused_for_worker_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Unbinding the plan is not a census for it: nothing replays a preview."""

    repo = _repository(tmp_path, manifest=True)
    monkeypatch.chdir(repo)
    reports = repo / REPORTS
    assert _preview([], "--format", "control").exit_code == 0

    result = runner.invoke(
        app,
        [
            "verification", "worker", "--plan", str(reports / "verification-plan.json"),
            "--workspace", str(repo), "--diff", str(reports / "verification-input.diff"),
            "--out", str(reports / "replayed-unit.json"),
        ],
        env=ENV,
    )

    assert result.exit_code != 0
    assert "capture is unavailable" in str(result.exception)
    assert not (reports / "replayed-unit.json").exists()
