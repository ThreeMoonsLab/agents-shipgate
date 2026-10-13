"""#1023: verify fails closed without a base or a manifest, and anchors gate paths.

Five ways `verify` reported a diff it never computed, or misrouted a removed
gate:

* With no detectable base (a default branch other than main/master, no
  ``origin/HEAD``), the run read no committed change, published a complete
  diff and granted merge. It now stops with exit 2, names ``base_unresolved``
  and offers ``--base`` and ``--no-base``. A repository with no remote uses its
  local ``main`` the way `check` does (#649).
* A change that deletes ``shipgate.yaml`` was routed to ``configure`` and then
  ``init --write``. When the base had the manifest it is now a human stop.
* ``SHIP-VERIFY-CI-GATE-REMOVED`` joined repository-relative paths onto the
  manifest's directory, so a nested manifest turned a comment in the root
  workflow into a removed gate.
* A depth-1 checkout could not archive ``--head`` (the commit pack walked into
  the missing parent) and agent mode said to edit ``shipgate.yaml``.
* `check` counted verify's own untracked reports as changed files.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from agents_shipgate.cli.main import app

runner = CliRunner()

#: Git that ignores the caller's configuration, hooks and exported ``GIT_*``.
_GIT_ENV = {
    **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}

MANIFEST = """\
version: "0.1"
project:
  name: test
agent:
  name: test-agent
  declared_purpose:
    - test
environment:
  target: local
tool_sources:
  - id: tools
    type: mcp
    path: tools.json
"""

WORKFLOW = """\
name: agents-shipgate
on: [pull_request]
jobs:
  shipgate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""

GATE = ".github/workflows/agents-shipgate.yml"
CHECK_ID = "SHIP-VERIFY-CI-GATE-REMOVED"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c", "user.name=Test",
            "-c", "user.email=test@example.invalid",
            "-c", "commit.gpgsign=false",
            "-c", f"core.hooksPath={os.devnull}",
            "-c", "protocol.file.allow=always",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    ).stdout.strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _project(repo: Path, *, branch: str, manifest_dir: str = "") -> None:
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", branch)
    root = repo / manifest_dir if manifest_dir else repo
    _write(root / "shipgate.yaml", MANIFEST)
    _write(root / "tools.json", '{"tools": []}\n')
    _write(repo / GATE, WORKFLOW)
    _write(repo / "README.md", "base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


def _ci_clone(origin: Path, destination: Path, sha: str) -> Path:
    """A CI-style checkout: every branch fetched, no ``origin/HEAD``."""

    destination.mkdir()
    _git(destination, "init", "-q", "-b", "ci")
    _git(destination, "remote", "add", "origin", f"file://{origin}")
    _git(destination, "fetch", "-q", "origin", "+refs/heads/*:refs/remotes/origin/*")
    _git(destination, "checkout", "-q", "--detach", sha)
    return destination


def _develop_origin(tmp_path: Path) -> tuple[Path, str, str]:
    """An origin whose default branch is ``develop``, with two PR branches."""

    origin = tmp_path / "origin"
    _project(origin, branch="develop")
    _git(origin, "checkout", "-q", "-b", "pr-delete-workflow")
    (origin / GATE).unlink()
    workflow_pr = _commit(origin, "delete the gate workflow")
    _git(origin, "checkout", "-q", "develop")
    _git(origin, "checkout", "-q", "-b", "pr-delete-manifest")
    (origin / "shipgate.yaml").unlink()
    manifest_pr = _commit(origin, "delete the manifest")
    _git(origin, "checkout", "-q", "develop")
    return origin, workflow_pr, manifest_pr


def _verify(workspace: Path, *args: str, out: Path, config: str = "shipgate.yaml"):
    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace", str(workspace),
            "--config", config,
            "--out", str(out),
            "--format", "json",
            *args,
        ],
    )
    return result, json.loads(result.stdout)


def _finding_ids(out: Path) -> list[str]:
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    return [finding["check_id"] for finding in report["findings"]]


def _action_text(payload: dict) -> str:
    action = payload["control"]["next_action"]
    return " ".join(str(action.get(key) or "") for key in ("kind", "command", "expects", "why"))


# --- no detectable base ------------------------------------------------------


def test_no_detectable_base_fails_closed_naming_base_and_no_base(tmp_path: Path) -> None:
    origin, workflow_pr, _ = _develop_origin(tmp_path)
    ci = _ci_clone(origin, tmp_path / "ci", workflow_pr)

    result, payload = _verify(
        ci, "--ci-mode", "strict", "--head", workflow_pr, out=tmp_path / "out"
    )

    assert result.exit_code == 2, result.output
    assert payload["execution"] == "failed"
    assert payload["merge_verdict"] == "unknown"
    assert payload["can_merge_without_human"] is False
    assert payload["control"]["state"] != "complete"
    assert payload["control"]["permissions"]["merge"] is False
    assert payload["base_ref"] is None
    assert payload["base_status"] == "ref_missing"
    status = payload["diff_status"]
    assert status["completeness"] == "unavailable"
    assert "base_unresolved" in status["detail"]
    assert "--base" in status["remediation"] and "--no-base" in status["remediation"]
    assert "base_unresolved" in payload["headline"]
    action = _action_text(payload)
    assert payload["control"]["next_action"]["kind"] == "fetch_base"
    assert "--base" in action and "--no-base" in action
    assert not (tmp_path / "out" / "report.json").exists()


def test_the_same_change_with_its_base_named_is_blocked(tmp_path: Path) -> None:
    """The contrast that makes the stop above necessary: the change is a gate removal."""

    origin, workflow_pr, _ = _develop_origin(tmp_path)
    ci = _ci_clone(origin, tmp_path / "ci", workflow_pr)

    result, payload = _verify(
        ci,
        "--ci-mode", "strict",
        "--base", "origin/develop",
        "--head", workflow_pr,
        out=tmp_path / "out",
    )

    assert result.exit_code == 20, result.output
    assert payload["merge_verdict"] == "blocked"
    assert CHECK_ID in _finding_ids(tmp_path / "out")


def test_no_base_flag_still_opts_out_explicitly(tmp_path: Path) -> None:
    origin, workflow_pr, _ = _develop_origin(tmp_path)
    ci = _ci_clone(origin, tmp_path / "ci", workflow_pr)

    result, payload = _verify(
        ci, "--no-base", "--head", workflow_pr, out=tmp_path / "out"
    )

    assert result.exit_code == 0, result.output
    assert payload["base_status"] == "not_requested"
    assert payload["diff_status"]["completeness"] == "complete"


def test_a_remote_less_repository_uses_its_local_main(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _project(repo, branch="main")
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / GATE).unlink()
    _commit(repo, "delete the gate workflow")

    result, payload = _verify(repo, "--ci-mode", "strict", out=tmp_path / "out")

    assert payload["base_ref"] == "main"
    assert GATE in payload["changed_files"]
    assert payload["control"]["state"] != "complete"
    assert payload["control"]["permissions"]["merge"] is False
    assert result.exit_code == 20, result.output
    assert payload["merge_verdict"] == "blocked"
    assert CHECK_ID in _finding_ids(tmp_path / "out")


def test_on_the_default_branch_tip_there_is_nothing_committed_to_compare(
    tmp_path: Path,
) -> None:
    """A default branch at ``HEAD`` resolves the comparison; it is not missing."""

    repo = tmp_path / "repo"
    _project(repo, branch="main")

    result, payload = _verify(repo, out=tmp_path / "out")

    assert result.exit_code == 0, result.output
    assert payload["diff_status"]["completeness"] == "complete"
    assert payload["base_status"] == "not_requested"


# --- a deleted manifest -------------------------------------------------------


def _assert_manifest_removal_stop(result, payload: dict) -> None:
    assert result.exit_code != 0, result.output
    action = payload["control"]["next_action"]
    assert action["actor"] == "human"
    assert action["kind"] == "stop"
    assert payload["control"]["state"] == "human_review_required"
    assert payload["control"]["permissions"]["merge"] is False
    assert payload["can_merge_without_human"] is False
    assert "removes" in payload["headline"] and "shipgate.yaml" in payload["headline"]
    rendered = json.dumps(payload["control"])
    for misroute in ("configure", "initialize", "init --write", "--preview"):
        assert misroute not in rendered


def test_deleting_the_manifest_with_a_resolvable_base_is_a_human_stop(
    tmp_path: Path,
) -> None:
    origin, _, manifest_pr = _develop_origin(tmp_path)
    ci = _ci_clone(origin, tmp_path / "ci", manifest_pr)

    for mode in ("advisory", "strict"):
        out = tmp_path / f"out-{mode}"
        result, payload = _verify(
            ci,
            "--ci-mode", mode,
            "--base", "origin/develop",
            "--head", manifest_pr,
            out=out,
        )
        _assert_manifest_removal_stop(result, payload)
        assert payload["base_ref"] == "origin/develop"


def test_deleting_the_manifest_without_a_resolvable_base_fails_closed(
    tmp_path: Path,
) -> None:
    origin, _, manifest_pr = _develop_origin(tmp_path)
    ci = _ci_clone(origin, tmp_path / "ci", manifest_pr)

    result, payload = _verify(ci, "--head", manifest_pr, out=tmp_path / "out")

    assert result.exit_code == 2, result.output
    assert payload["control"]["next_action"]["kind"] == "fetch_base"
    assert "base_unresolved" in payload["diff_status"]["detail"]
    assert payload["control"]["permissions"]["merge"] is False
    assert "configure" not in json.dumps(payload["control"])


def test_deleting_the_manifest_with_a_named_base_that_is_not_here_fails_closed(
    tmp_path: Path,
) -> None:
    """``diff_base: target`` without ``fetch-depth: 0`` names a base the
    checkout does not hold. It cannot show whether the manifest was removed,
    so it is an unknown base in both modes, never the setup or host route."""

    origin, _, manifest_pr = _develop_origin(tmp_path)
    ci = _ci_clone(origin, tmp_path / "ci", manifest_pr)

    for mode in ("advisory", "strict"):
        result, payload = _verify(
            ci,
            "--ci-mode", mode,
            "--base", "origin/not-fetched",
            "--head", manifest_pr,
            out=tmp_path / f"out-{mode}",
        )

        assert result.exit_code == 2, result.output
        assert payload["base_status"] == "ref_missing"
        assert payload["diff_status"]["reason"] == "refs_missing"
        assert payload["control"]["next_action"]["kind"] == "fetch_base"
        assert payload["control"]["permissions"]["merge"] is False
        assert payload.get("host_comparison") is None
        rendered = json.dumps(payload["control"])
        for misroute in ("configure", "initialize", "init --write"):
            assert misroute not in rendered


def _depth_one(origin: Path, destination: Path, sha: str) -> Path:
    """The default ``actions/checkout`` layout: one commit, fetched by SHA."""

    _git(origin, "config", "uploadpack.allowAnySHA1InWant", "true")
    destination.mkdir()
    _git(destination, "init", "-q", "-b", "ci")
    _git(destination, "remote", "add", "origin", f"file://{origin}")
    _git(destination, "fetch", "-q", "--depth=1", "origin", sha)
    _git(destination, "checkout", "-q", "--detach", sha)
    assert _git(destination, "rev-parse", "--is-shallow-repository") == "true"
    return destination


def _assert_unknown_base_stop(result, payload: dict) -> None:
    assert result.exit_code == 2, result.output
    assert payload["base_status"] == "ref_missing"
    assert payload["diff_status"]["reason"] == "refs_missing"
    assert payload["control"]["next_action"]["kind"] == "fetch_base"
    assert payload["control"]["permissions"]["merge"] is False
    assert payload.get("host_comparison") is None
    rendered = json.dumps(payload["control"])
    for misroute in ("configure", "initialize", "init --write"):
        assert misroute not in rendered


def test_a_depth_one_checkout_cannot_show_the_manifest_never_existed(
    tmp_path: Path,
) -> None:
    """``diff_base: target`` on the default depth-1 checkout (#1023 review
    cycle 2). History there ends at the PR commit, so ``rev-list`` finds no
    commit that ever held the manifest; that silence is not proof."""

    origin, _, manifest_pr = _develop_origin(tmp_path)
    shallow = _depth_one(origin, tmp_path / "shallow", manifest_pr)

    for mode in ("advisory", "strict"):
        result, payload = _verify(
            shallow,
            "--ci-mode", mode,
            "--base", "origin/develop",
            "--head", manifest_pr,
            out=tmp_path / f"out-{mode}",
        )
        _assert_unknown_base_stop(result, payload)


def test_a_manifest_free_depth_one_checkout_with_an_absent_base_stops(
    tmp_path: Path,
) -> None:
    """The same layout without any manifest: advisory used to exit 0 on the
    host route, and strict used to route to ``configure``. Neither can be told
    from a deleted gate here, so both stop for the base."""

    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _write(origin / ".claude" / "settings.json", '{"permissions": {"deny": ["Bash(rm *)"]}}\n')
    _commit(origin, "base")
    _git(origin, "checkout", "-q", "-b", "change")
    _write(origin / ".claude" / "settings.json", '{"permissions": {"allow": ["Bash(*)"]}}\n')
    change = _commit(origin, "widen")
    shallow = _depth_one(origin, tmp_path / "shallow", change)

    for mode in ("advisory", "strict"):
        result, payload = _verify(
            shallow,
            "--ci-mode", mode,
            "--base", "origin/main",
            "--head", change,
            out=tmp_path / f"out-{mode}",
        )
        _assert_unknown_base_stop(result, payload)


def test_a_graft_file_cannot_show_the_manifest_never_existed(tmp_path: Path) -> None:
    """``info/grafts`` cuts history without making the clone shallow."""

    origin, _, manifest_pr = _develop_origin(tmp_path)
    ci = _ci_clone(origin, tmp_path / "ci", manifest_pr)
    _write(ci / ".git" / "info" / "grafts", f"{manifest_pr}\n")
    assert _git(ci, "rev-parse", "--is-shallow-repository") == "false"

    for mode in ("advisory", "strict"):
        result, payload = _verify(
            ci,
            "--ci-mode", mode,
            "--base", "origin/not-fetched",
            "--head", manifest_pr,
            out=tmp_path / f"out-{mode}",
        )
        _assert_unknown_base_stop(result, payload)


def test_a_named_base_that_is_not_here_keeps_setup_where_history_shows_no_manifest(
    tmp_path: Path,
) -> None:
    """Complete history that never held the manifest is first adoption."""

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo / "README.md", "hello\n")
    _commit(repo, "initial")

    result, payload = _verify(
        repo, "--ci-mode", "strict", "--base", "origin/main", out=tmp_path / "out"
    )

    assert result.exit_code == 2, result.output
    assert payload["control"]["next_action"]["kind"] == "configure"


def test_a_shallow_base_that_holds_the_manifest_asks_for_history(tmp_path: Path) -> None:
    """The base holds the manifest, and only the merge base says whether this
    change removed it; a depth-1 checkout cannot read that, so it says so."""

    origin, _, manifest_pr = _develop_origin(tmp_path)
    _git(origin, "config", "uploadpack.allowAnySHA1InWant", "true")
    shallow = tmp_path / "shallow"
    shallow.mkdir()
    _git(shallow, "init", "-q", "-b", "ci")
    _git(shallow, "remote", "add", "origin", f"file://{origin}")
    _git(shallow, "fetch", "-q", "--depth=1", "origin", manifest_pr)
    _git(shallow, "fetch", "-q", "--depth=1", "origin", "+develop:refs/remotes/origin/develop")
    _git(shallow, "checkout", "-q", "--detach", manifest_pr)

    result, payload = _verify(
        shallow, "--base", "origin/develop", "--head", manifest_pr, out=tmp_path / "out"
    )

    assert result.exit_code == 2, result.output
    assert payload["diff_status"]["reason"] == "merge_base_missing"
    assert payload["control"]["next_action"]["kind"] == "fetch_base"
    assert payload["control"]["permissions"]["merge"] is False
    assert "configure" not in json.dumps(payload["control"])


def test_an_uncommitted_manifest_deletion_is_a_human_stop(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _project(repo, branch="main")
    (repo / "shipgate.yaml").unlink()

    result, payload = _verify(repo, out=tmp_path / "out")

    _assert_manifest_removal_stop(result, payload)

    envelope = runner.invoke(
        app,
        [
            "verify",
            "--workspace", str(repo),
            "--out", str(tmp_path / "out-control"),
            "--format", "control",
        ],
    )
    control = json.loads(envelope.stdout)
    assert envelope.exit_code == 2, envelope.output
    assert control["control_state"] == "human_review_required"
    assert control["next_action"]["kind"] == "stop"
    assert control["permissions"]["merge"] is False


def test_a_repository_that_never_had_the_manifest_keeps_the_setup_route(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo / "README.md", "hello\n")
    _commit(repo, "initial")

    result, payload = _verify(repo, "--ci-mode", "strict", out=tmp_path / "out")

    assert result.exit_code == 2, result.output
    assert payload["control"]["next_action"]["kind"] == "configure"


# --- the gate check anchors at the repository root ---------------------------


def _monorepo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _project(repo, branch="main", manifest_dir="apps/agent")
    _git(repo, "checkout", "-q", "-b", "feature")
    return repo


def test_a_nested_manifest_does_not_read_a_root_workflow_edit_as_removal(
    tmp_path: Path,
) -> None:
    repo = _monorepo(tmp_path)
    with (repo / GATE).open("a", encoding="utf-8") as handle:
        handle.write("# a comment\n")
    _commit(repo, "comment the workflow")

    out = tmp_path / "out"
    result, payload = _verify(
        repo, "--base", "main", out=out, config="apps/agent/shipgate.yaml"
    )

    assert GATE in payload["changed_files"]
    assert payload["execution"] == "succeeded", result.output
    assert CHECK_ID not in _finding_ids(out)


def test_a_nested_manifest_still_sees_the_root_workflow_deleted(tmp_path: Path) -> None:
    repo = _monorepo(tmp_path)
    (repo / GATE).unlink()
    _commit(repo, "delete the gate workflow")

    out = tmp_path / "out"
    result, payload = _verify(
        repo,
        "--ci-mode", "strict",
        "--base", "main",
        out=out,
        config="apps/agent/shipgate.yaml",
    )

    assert result.exit_code == 20, result.output
    assert CHECK_ID in _finding_ids(out)


def test_a_nested_copy_of_the_workflow_does_not_hide_a_root_deletion(
    tmp_path: Path,
) -> None:
    repo = _monorepo(tmp_path)
    _write(repo / "apps" / "agent" / GATE, WORKFLOW)
    _commit(repo, "add a nested copy")
    _git(repo, "branch", "-f", "main", "HEAD")
    (repo / GATE).unlink()
    _commit(repo, "delete the root gate workflow")

    out = tmp_path / "out"
    result, _payload = _verify(
        repo,
        "--ci-mode", "strict",
        "--base", "main",
        out=out,
        config="apps/agent/shipgate.yaml",
    )

    assert result.exit_code == 20, result.output
    assert CHECK_ID in _finding_ids(out)


# --- a depth-1 checkout ------------------------------------------------------


def test_a_depth_one_checkout_archives_its_head(tmp_path: Path) -> None:
    origin, workflow_pr, _ = _develop_origin(tmp_path)
    _git(origin, "config", "uploadpack.allowAnySHA1InWant", "true")
    shallow = tmp_path / "shallow"
    shallow.mkdir()
    _git(shallow, "init", "-q", "-b", "ci")
    _git(shallow, "remote", "add", "origin", f"file://{origin}")
    _git(shallow, "fetch", "-q", "--depth=1", "origin", workflow_pr)
    _git(shallow, "checkout", "-q", "--detach", workflow_pr)
    assert _git(shallow, "rev-parse", "--is-shallow-repository") == "true"

    result, payload = _verify(
        shallow, "--no-base", "--head", workflow_pr, out=tmp_path / "out"
    )

    assert result.exit_code == 0, result.output
    assert payload["execution"] == "succeeded"

    result, payload = _verify(shallow, "--head", workflow_pr, out=tmp_path / "out2")

    assert result.exit_code == 2, result.output
    assert payload["control"]["next_action"]["kind"] == "fetch_base"
    remediation = payload["diff_status"]["remediation"]
    assert "fetch-depth: 0" in remediation
    assert "shipgate.yaml" not in _action_text(payload)


# --- check leaves verify's reports out of its change set ---------------------


def test_check_after_verify_does_not_count_the_reports(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _project(repo, branch="main")

    result, _payload = _verify(repo, "--no-base", out=repo / "agents-shipgate-reports")
    assert result.exit_code == 0, result.output
    assert (repo / "agents-shipgate-reports" / "verifier.json").is_file()
    _write(repo / "notes.md", "an edit the developer made\n")

    checked = runner.invoke(
        app,
        [
            "check",
            "--agent", "claude-code",
            "--workspace", str(repo),
            "--config", "shipgate.yaml",
            "--base", "HEAD",
            "--format", "agent-boundary-json",
        ],
    )

    payload = json.loads(checked.stdout)
    assert "notes.md" in payload["changed_files"]
    assert not [
        path for path in payload["changed_files"] if path.startswith("agents-shipgate-reports/")
    ]
