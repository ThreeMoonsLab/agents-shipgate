"""A currency refusal says why the workspace could not be read (#813).

`live_workspace` swallowed every failure to read the repository. The refusals
built on it named no cause, and `agent control` routed each one back to the
`verify` command that had just failed the same way: in a repository with Git
LFS, git-crypt or any other configuration the static worktree readers refuse,
an agent following `next_actions` looped forever. Separately, a resolver that
could not observe the workspace at all left every pointer short of `complete`
unchecked, `review_publishable` with commit, push and update_pr included.

The rules pinned here, against real Git repositories:

* Every refusal an unreadable workspace produces leads with its cause, which
  names the configuration key or path and carries no remediation, secret or
  file content, for the worktree, preview and committed-tree pointers alike.
* Repository configuration routes to a human `review` step that says no rerun,
  `--head` included, changes the answer, names read-only `diff`, and never
  advises removing the configuration. A read bound routes to committing or
  shrinking the change before re-running. Anything else keeps the rerun.
* A resolver that could not observe the workspace refuses every pointer state
  that binds a Git identity. A caller that asks for no currency (`live=None`)
  and a `--preview` run outside Git keep their answers.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "samples" / "clean_read_only_agent"
REPORTS = "agents-shipgate-reports"
ENV = {"NO_COLOR": "1", "GITHUB_ACTIONS": None, "FORCE_COLOR": None, "COLUMNS": "200"}
AGENT_ENV = {**ENV, "AGENTS_SHIPGATE_AGENT_MODE": "1"}
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Built at runtime, never spelled as one literal a secret scanner would flag.
_TOKEN = "ghp_" + "Q" * 36

runner = CliRunner()


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _sample(path: Path, *, lfs_file: str | None = None) -> Path:
    """The clean read-only sample as a one-commit repository.

    ``lfs_file`` also commits a ``.gitattributes`` routing ``*.bin`` through
    the LFS filter and a tracked file matching it — the configuration an LFS
    repository carries whatever its local Git config says.
    """

    path.mkdir(parents=True)
    for name in ("shipgate.yaml", "tools.json"):
        shutil.copy(SAMPLE / name, path / name)
    (path / ".gitignore").write_text(f"{REPORTS}/\n", encoding="utf-8")
    if lfs_file is not None:
        (path / ".gitattributes").write_text(
            "*.bin filter=lfs diff=lfs merge=lfs -text\n", encoding="utf-8"
        )
        (path / lfs_file).parent.mkdir(parents=True, exist_ok=True)
        (path / lfs_file).write_bytes(b"version https://git-lfs.github.com/spec/v1\n")
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.test")
    _git(path, "config", "user.name", "Test User")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "fixture")
    return path


#: Each refused configuration, how to put it in place, and what its cause names.
CAUSES = {
    "filter_config": (
        lambda repo: _git(repo, "config", "filter.lfs.clean", "git-lfs clean -- %f"),
        "filter.lfs.clean",
    ),
    "filter_attribute": (None, "assets/logo.bin"),
    "diff_config": (
        lambda repo: _git(repo, "config", "diff.lockb.textconv", "bun"),
        "diff.lockb.textconv",
    ),
}

#: The three currency paths a published pointer is checked through.
ROUTES = {
    # A worktree run binds its plan: the plan-bound overlay check.
    "worktree": ("--no-base",),
    # A preview inside Git binds the same plan shape, as a preview operation.
    "preview": ("--preview",),
    # An archived head: the clean-worktree check.
    "committed": ("--no-base", "--head", "HEAD"),
}


def _configured(tmp_path: Path, cause: str) -> tuple[Path, str]:
    setup, named = CAUSES[cause]
    repo = _sample(
        tmp_path / "repo",
        lfs_file="assets/logo.bin" if cause == "filter_attribute" else None,
    )
    if setup is not None:
        setup(repo)
    return repo, named


def _verify(repo: Path, *extra: str, fmt: str = "control", env=ENV):
    return runner.invoke(
        app,
        ["verify", "--workspace", str(repo), "--config", "shipgate.yaml", *extra,
         "--format", fmt],
        env=env,
    )


def _control(repo: Path, env=AGENT_ENV):
    return runner.invoke(app, ["agent", "control", "--workspace", str(repo)], env=env)


def _refusal(result) -> str:
    lines = [
        line
        for line in _plain(result.output).splitlines()
        if line.startswith("Current control is unavailable")
    ]
    assert lines, _plain(result.output)
    return lines[-1]


def _error_line(result) -> dict:
    lines = [
        line for line in _plain(result.output).splitlines() if line.startswith('{"error"')
    ]
    assert lines, _plain(result.output)
    return json.loads(lines[-1])


def _pointer(repo: Path) -> dict:
    return json.loads((repo / REPORTS / "current-control.json").read_text(encoding="utf-8"))


# -- the cause, first, through every currency path ---------------------------


@pytest.mark.parametrize("route", sorted(ROUTES))
@pytest.mark.parametrize("cause", sorted(CAUSES))
def test_a_refused_repository_configuration_is_named_and_routed_to_a_human(
    tmp_path: Path, cause: str, route: str
):
    """The loop the issue reported, for each configuration and each pointer kind.

    The configuration is in place before the run, as it is in a real LFS or
    git-crypt repository. Every route still publishes a pointer — the worktree
    run as `human_review_required`, the preview as `agent_action_required`,
    the archived head as `complete` — and every one of them is refused, with
    the key or path first and a recovery that is not the same rerun.
    """

    repo, named = _configured(tmp_path, cause)
    published = _verify(repo, *ROUTES[route])
    assert published.exit_code in {0, 2}, _plain(published.output)
    pointer = _pointer(repo)
    assert pointer["lifecycle_state"] == "terminal", pointer
    assert pointer["workspace_identity"]["snapshot_kind"] is not None, pointer

    result = _control(repo)
    assert result.exit_code == 4, _plain(result.output)
    refusal = _refusal(result)
    assert "(workspace_unverifiable)" in refusal, refusal
    # The cause leads: nothing sits between the reason code and the key.
    message = refusal.split("): ", 1)[1]
    assert named in message.split(". ")[0], message
    assert message.index(named) < message.index("could not be determined"), message
    # The misleading remediation is gone from the reader's answer.
    assert "verify refs" not in message, message

    payload = _error_line(result)
    assert payload["message"] == message
    [action] = payload["next_actions"]
    assert action["kind"] == "review", action
    assert action.get("command") is None, action
    why = action["why"]
    assert why.startswith(message.split(" The current set")[0]), why
    assert named in why
    assert "with or without --head" in why
    assert " diff --workspace " in why
    # Removing LFS or git-crypt configuration breaks those checkouts.
    for advice in ("unset", "git config", "remove the", "delete"):
        assert advice not in why.lower(), why
    assert payload["next_action"] == why


def test_the_cause_also_reaches_verify_s_own_control_answer(tmp_path: Path):
    """`verify --format control` and `--format text` read the pointer through the same protocol.

    An archived `--head` run in an LFS repository passes, and its own control
    answer is withheld; the reason it prints used to be the causeless
    sentence, so even the human output never said "filter".
    """

    repo, named = _configured(tmp_path, "filter_attribute")

    control = _verify(repo, "--no-base", "--head", "HEAD")
    assert control.exit_code == 0, _plain(control.output)
    envelope = json.loads(control.stdout)
    assert envelope["control_state"] == "human_review_required", envelope
    assert envelope["next_action"]["why"].startswith(
        "Static worktree collection refuses Git filter attributes"
    ), envelope
    assert named in envelope["next_action"]["why"]

    text = _verify(repo, "--no-base", "--head", "HEAD", fmt="text")
    assert text.exit_code == 0, _plain(text.output)
    next_lines = [
        line for line in _plain(text.output).splitlines() if line.startswith("Next:")
    ]
    assert next_lines and named in next_lines[0], _plain(text.output)


def test_a_complete_pointer_gains_the_same_cause_when_the_configuration_arrives_later(
    tmp_path: Path,
):
    """`git lfs install --local` after a passing run, the issue's own reproduction."""

    repo = _sample(tmp_path / "repo")
    assert _verify(repo, "--no-base").exit_code == 0
    assert _pointer(repo)["control"]["state"] == "complete"
    assert _control(repo).exit_code == 0

    _git(repo, "config", "filter.lfs.clean", "git-lfs clean -- %f")
    result = _control(repo)
    assert result.exit_code == 4, _plain(result.output)
    assert (
        "(workspace_unverifiable): Static worktree collection refuses executable "
        "Git filters (filter.lfs.clean)."
    ) in _refusal(result)
    assert _error_line(result)["next_actions"][0]["kind"] == "review"


def test_git_info_attributes_is_the_same_class_of_cause(tmp_path: Path):
    from agents_shipgate.cli.current_workspace import live_workspace
    from agents_shipgate.core.current_control import LiveWorkspace

    repo = _sample(tmp_path / "repo")
    info = repo / ".git" / "info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "attributes").write_text("*.json -diff\n", encoding="utf-8")

    live = live_workspace(repo, repo / REPORTS)
    assert isinstance(live, LiveWorkspace)
    assert live.changed_paths is None
    assert live.changed_paths_cause is not None
    assert live.changed_paths_cause.kind == "repository_configuration"
    assert ".git/info/attributes" in live.changed_paths_cause.text


# -- the cause carries nothing it should not ---------------------------------


def test_a_token_shaped_path_in_the_cause_is_redacted_everywhere_it_is_printed(
    tmp_path: Path,
):
    """A tracked file name can be shaped like a credential (the class of #802)."""

    repo = _sample(tmp_path / "repo", lfs_file=f"assets/{_TOKEN}.bin")

    # The writer's own refusal names the same path, in the artifacts it publishes.
    worktree = _verify(repo, "--no-base")
    assert worktree.exit_code == 2, _plain(worktree.output)
    for name in ("verifier.json", "pr-comment.md"):
        published = (repo / REPORTS / name).read_text(encoding="utf-8")
        assert _TOKEN not in published, name
    detail = json.loads((repo / REPORTS / "verifier.json").read_text(encoding="utf-8"))[
        "diff_status"
    ]["detail"]
    assert "(assets/[REDACTED:github_token].bin)" in detail, detail

    assert _verify(repo, "--no-base", "--head", "HEAD").exit_code == 0
    result = _control(repo)
    assert result.exit_code == 4, _plain(result.output)
    assert _TOKEN not in result.output
    refusal = _refusal(result)
    assert "assets/[REDACTED:github_token].bin" in refusal, refusal
    assert "[REDACTED:github_token]" in _error_line(result)["next_actions"][0]["why"]

    control = _verify(repo, "--no-base", "--head", "HEAD")
    envelope = json.loads(control.stdout)
    assert _TOKEN not in envelope["next_action"]["why"]
    assert "[REDACTED:github_token]" in envelope["next_action"]["why"]


def test_the_cause_is_classified_never_copied_from_foreign_exception_text():
    from agents_shipgate.cli.current_workspace import workspace_read_cause
    from agents_shipgate.cli.verify.git import (
        DiffContext,
        DiffInputError,
        UnboundGitConfigurationError,
    )
    from agents_shipgate.core.current_control import MAX_LIVE_WORKSPACE_CAUSE_BYTES
    from agents_shipgate.core.errors import ConfigError

    configured = workspace_read_cause(
        UnboundGitConfigurationError(
            "Static worktree collection refuses executable Git filters (filter.lfs.clean).",
            remediation="Commit the intended changes and verify refs.",
        )
    )
    assert configured.kind == "repository_configuration"
    assert configured.text == (
        "Static worktree collection refuses executable Git filters (filter.lfs.clean)."
    )

    for reason in ("body_limit_exceeded", "metadata_limit_exceeded", "git_timeout"):
        bounded = workspace_read_cause(
            DiffInputError(DiffContext(completeness="partial", reason=reason))
        )
        assert bounded.kind == "resource_limit", reason
        assert f"({reason})" in bounded.text
        # The diff reader's own remediation is for a diff of refs, not this read.
        assert "Split" not in bounded.text and "split" not in bounded.text

    missing = workspace_read_cause(
        DiffInputError(
            DiffContext(
                completeness="unavailable",
                reason="refs_missing",
                detail="Git ref 'HEAD' is not available locally.",
            )
        )
    )
    assert missing.kind == "other"
    assert "Fetch" not in missing.text

    # A Shipgate sentence is kept; text Shipgate did not write never is.
    assert workspace_read_cause(ConfigError("Workspace is not inside a git checkout: x")) \
        .text == "Workspace is not inside a git checkout: x."
    foreign = workspace_read_cause(RuntimeError(f"line 1 of settings: token={_TOKEN}"))
    assert foreign.kind == "other"
    assert foreign.text == "Git could not be read in this workspace (RuntimeError)."

    long = workspace_read_cause(ConfigError("refuses " + "a/" * 400 + _TOKEN))
    assert len(long.text.encode("utf-8")) <= MAX_LIVE_WORKSPACE_CAUSE_BYTES
    assert _TOKEN[:12] not in long.text


def test_a_token_shaped_reports_directory_example_is_redacted_and_keeps_its_class(
    tmp_path: Path,
):
    """#804's refusal is a cause too, with its own class and its own recovery."""

    from agents_shipgate.core.current_control import (
        CurrentControlUnavailable,
        LiveWorkspace,
        publish_current_control,
        read_current_control,
    )
    from agents_shipgate.schemas.current_control import (
        AgentActionRequiredCurrentControl,
        CurrentControlWorkspaceIdentity,
    )

    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "verifier.json").write_text("{}\n", encoding="utf-8")
    publish_current_control(
        reports,
        operation="preview",
        control=AgentActionRequiredCurrentControl(
            state="agent_action_required", reason="A preview reached no decision."
        ),
        workspace_identity=CurrentControlWorkspaceIdentity(),
        artifact_keys={"verifier"},
    )
    refused = LiveWorkspace(
        root=tmp_path,
        reports_dir_refusal=f"holds tracked repository files (docs/{_TOKEN}.md)",
    )
    with pytest.raises(CurrentControlUnavailable) as raised:
        read_current_control(reports, live=refused)
    assert raised.value.reason == "workspace_unverifiable"
    assert raised.value.cause is not None
    assert raised.value.cause.kind == "reports_directory"
    assert _TOKEN not in str(raised.value)
    assert _TOKEN not in (raised.value.reports_dir_refusal or "")
    assert str(raised.value).startswith(f"The reports directory {reports} holds tracked")


# -- routes by cause class ----------------------------------------------------


def test_a_read_bound_routes_to_committing_or_shrinking_before_the_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from agents_shipgate.cli.verify import git as verify_git

    repo = _sample(tmp_path / "repo")
    assert _verify(repo, "--no-base").exit_code == 0

    def oversized(*_args, **_kwargs):
        raise verify_git.DiffInputError(
            verify_git.DiffContext(completeness="partial", reason="body_limit_exceeded")
        )

    monkeypatch.setattr(verify_git, "working_tree_context", oversized)
    result = _control(repo)
    assert result.exit_code == 4, _plain(result.output)
    assert "(workspace_unverifiable): Changed paths were collected but the diff body " \
        "could not be read (body_limit_exceeded)." in _refusal(result)
    [action] = _error_line(result)["next_actions"]
    assert action["kind"] == "command", action
    assert " verify " in f" {action['command']} "
    assert "Commit or shrink the uncommitted change" in action["why"]


def test_any_other_cause_keeps_the_rerun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from agents_shipgate.cli.verify import git as verify_git
    from agents_shipgate.core.errors import ConfigError

    repo = _sample(tmp_path / "repo")
    assert _verify(repo, "--no-base").exit_code == 0

    def failing(*_args, **_kwargs):
        raise ConfigError("Git returned malformed NUL-delimited diff metadata.")

    monkeypatch.setattr(verify_git, "working_tree_context", failing)
    result = _control(repo)
    assert result.exit_code == 4, _plain(result.output)
    assert "(workspace_unverifiable): Git returned malformed NUL-delimited diff metadata." \
        in _refusal(result)
    [action] = _error_line(result)["next_actions"]
    assert action["kind"] == "command", action
    assert action["why"].startswith("Re-run ")


# -- an unobservable workspace refuses every state ---------------------------


def _publish_state(
    reports: Path, repo: Path, state: str, snapshot_kind: str
) -> None:
    """Hand-publish a plan-less pointer in ``state``, bound to ``repo``'s HEAD."""

    from agents_shipgate.cli.verify.git import commit_sha, repository_identity, tree_sha
    from agents_shipgate.core.current_control import publish_current_control
    from agents_shipgate.schemas.agent_control import PublishOnlyPermissions
    from agents_shipgate.schemas.current_control import (
        AgentActionRequiredCurrentControl,
        CurrentControlWorkspaceIdentity,
        HumanReviewRequiredCurrentControl,
        ReviewPublishableCurrentControl,
    )

    controls = {
        "agent_action_required": AgentActionRequiredCurrentControl(
            state="agent_action_required", reason="One agent step remains."
        ),
        "human_review_required": HumanReviewRequiredCurrentControl(
            state="human_review_required", reason="A human reviews this."
        ),
        "review_publishable": ReviewPublishableCurrentControl(
            state="review_publishable",
            reason="A human gates the merge.",
            permissions=PublishOnlyPermissions(),
        ),
    }
    reports.mkdir(parents=True)
    (reports / "verifier.json").write_text("{}\n", encoding="utf-8")
    publish_current_control(
        reports,
        operation="verify",
        control=controls[state],
        workspace_identity=CurrentControlWorkspaceIdentity(
            repository=repository_identity(repo),
            head_ref="HEAD",
            head_commit_sha=commit_sha(repo, "HEAD"),
            head_tree_sha=tree_sha(repo, "HEAD"),
            snapshot_kind=snapshot_kind,
        ),
        artifact_keys={"verifier"},
    )


@pytest.mark.parametrize("snapshot_kind", ["worktree_overlay", "committed_tree"])
def test_every_pointer_state_refuses_when_the_workspace_cannot_be_read(
    tmp_path: Path, snapshot_kind: str
):
    """Both failures, through the real resolver, for every state.

    The worktree view failing (a refused configuration) and the workspace
    failing as a whole (no `.git`) are two different code paths. The second
    used to return every pointer short of `complete` unchecked.
    """

    from agents_shipgate.cli.current_workspace import live_workspace
    from agents_shipgate.core.current_control import (
        CurrentControlUnavailable,
        read_current_control,
    )

    repo = _sample(tmp_path / "repo")
    head = ("--head", "HEAD") if snapshot_kind == "committed_tree" else ()
    assert _verify(repo, "--no-base", *head).exit_code == 0
    directories = {"complete": repo / REPORTS}
    assert _pointer(repo)["control"]["state"] == "complete"
    for state in ("agent_action_required", "human_review_required", "review_publishable"):
        directories[state] = tmp_path / state
        _publish_state(directories[state], repo, state, snapshot_kind)

    def outcomes(expected_cause: str) -> dict[str, str]:
        seen: dict[str, str] = {}
        for state, reports in directories.items():
            try:
                read_current_control(reports, live=lambda r=reports: live_workspace(repo, r))
            except CurrentControlUnavailable as exc:
                assert str(exc).startswith(expected_cause), (state, str(exc))
                assert exc.cause is not None and exc.cause.text == expected_cause
                seen[state] = exc.reason
            else:
                seen[state] = "answered"
        return seen

    # Readable, the hand-published pointers are current: what refuses below
    # is the failure to look, not the fixture.
    for state, reports in directories.items():
        assert read_current_control(
            reports, live=lambda r=reports: live_workspace(repo, r)
        ).pointer.control.state == state

    _git(repo, "config", "filter.lfs.clean", "git-lfs clean -- %f")
    configured = "Static worktree collection refuses executable Git filters (filter.lfs.clean)."
    assert outcomes(configured) == dict.fromkeys(directories, "workspace_unverifiable")
    _git(repo, "config", "--unset", "filter.lfs.clean")

    hidden = tmp_path / "hidden.git"
    (repo / ".git").rename(hidden)
    try:
        unobservable = f"Workspace is not inside a git checkout: {repo.resolve()}."
        assert outcomes(unobservable) == {
            "complete": "workspace_unverified",
            "agent_action_required": "workspace_unverifiable",
            "human_review_required": "workspace_unverifiable",
            "review_publishable": "workspace_unverifiable",
        }
    finally:
        hidden.rename(repo / ".git")


def test_a_review_publishable_pointer_no_longer_answers_when_git_is_gone(tmp_path: Path):
    """The fail-open, end to end: commit, push and update_pr over an unread tree."""

    repo = _sample(tmp_path / "repo")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(git status)"]}}) + "\n", encoding="utf-8"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "claude settings")
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(git status)", "Bash(curl:*)"]}}) + "\n",
        encoding="utf-8",
    )
    decided = _verify(repo, "--base", "main")
    assert decided.exit_code == 0, _plain(decided.output)
    assert json.loads(decided.stdout)["control_state"] == "review_publishable"
    assert _control(repo).exit_code == 0

    (repo / "notes.txt").write_text("an edit the decision never saw\n", encoding="utf-8")
    hidden = tmp_path / "hidden.git"
    (repo / ".git").rename(hidden)
    try:
        result = _control(repo)
    finally:
        hidden.rename(repo / ".git")
    assert result.exit_code == 4, _plain(result.output)
    assert '"control_state"' not in result.stdout
    refusal = _refusal(result)
    assert "(workspace_unverifiable): Workspace is not inside a git checkout" in refusal


def test_a_caller_that_asks_for_no_currency_keeps_its_answer(tmp_path: Path):
    """`live=None` is not a resolver that failed: it withholds completion only."""

    from agents_shipgate.core.current_control import read_current_control

    repo = _sample(tmp_path / "repo")
    reports = tmp_path / "review_publishable"
    _publish_state(reports, repo, "review_publishable", "worktree_overlay")
    assert read_current_control(reports).pointer.control.state == "review_publishable"


def test_a_preview_outside_git_still_reads_as_current(tmp_path: Path):
    """The supported non-Git route: nothing Git-bound to compare, so nothing to refuse."""

    workspace = tmp_path / "plain"
    workspace.mkdir()
    for name in ("shipgate.yaml", "tools.json"):
        shutil.copy(SAMPLE / name, workspace / name)

    preview = _verify(workspace, "--preview")
    assert preview.exit_code == 0, _plain(preview.output)
    pointer = _pointer(workspace)
    assert pointer["workspace_identity"]["snapshot_kind"] is None, pointer
    assert pointer["workspace_identity"]["head_commit_sha"] is None, pointer

    result = _control(workspace, env=ENV)
    assert result.exit_code == 0, _plain(result.output)
    assert json.loads(result.stdout)["control_state"] == pointer["control"]["state"]
