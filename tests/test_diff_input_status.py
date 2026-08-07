"""An unreadable diff must never be reported as "nothing here is agent-related".

Regression coverage for the class of bug where the diff-acquisition layer
collapsed every failure into one message, the caller then evaluated the trigger
catalog against empty inputs, and the artifact published
``skip_reason: "no_match"`` — "nothing in this PR signals a tool-surface
change" — about a PR the verifier had never read.

The three input shapes exercised here are the ones that occur in practice:

1. no reachable merge base (a shallow clone, or unrelated histories);
2. a partial clone whose blobs were never fetched, with lazy fetching disabled
   as verification's static/no-implicit-network boundary requires;
3. an agent-related diff whose body exceeds the static diff-body bound.

In every one of them the changed-path evidence and the diff body have
different availability, so the tests assert on both.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.cli.verify import git as verify_git
from agents_shipgate.cli.verify.git import (
    DiffInputError,
    collect_diff_context,
    diff_revspec_context,
)
from agents_shipgate.triggers import evaluate

runner = CliRunner()

ADK_AGENT_SOURCE = """\
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool


def issue_refund(order_id: str, amount: float) -> dict:
    return {"order_id": order_id, "amount": amount}


refund_tool = FunctionTool(issue_refund)
root_agent = LlmAgent(name="support", tools=[refund_tool])
"""


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.test")
    _git(path, "config", "user.name", "Test")
    return path


def _commit(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", message)


def _unrelated_histories(tmp_path: Path) -> Path:
    """A repo whose two branches share no commit — no merge base exists."""

    repo = _repo(tmp_path / "repo")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit(repo, "base")

    _git(repo, "checkout", "-q", "--orphan", "detached-base")
    _git(repo, "rm", "-rq", "--cached", ".")
    (repo / "README.md").unlink()
    (repo / "OTHER.md").write_text("unrelated root\n", encoding="utf-8")
    _commit(repo, "unrelated root")
    _git(repo, "checkout", "-q", "main")

    agent = repo / "src" / "agent.py"
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_text(ADK_AGENT_SOURCE, encoding="utf-8")
    _commit(repo, "add adk agent")
    return repo


def _blobless_clone(tmp_path: Path) -> Path:
    """A partial clone missing the blobs the base side of the diff needs."""

    origin = _repo(tmp_path / "origin")
    (origin / "README.md").write_text("base\n", encoding="utf-8")
    _commit(origin, "base")
    _git(origin, "branch", "base-ref")

    agent = origin / "src" / "agent.py"
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_text(ADK_AGENT_SOURCE, encoding="utf-8")
    # An existing file must also change, so the base side owns a blob the
    # clone never fetches. A pure addition would leave nothing missing.
    (origin / "README.md").write_text("base, revised\n", encoding="utf-8")
    _commit(origin, "add adk agent")

    _git(origin, "config", "uploadpack.allowfilter", "true")
    _git(origin, "config", "uploadpack.allowanysha1inwant", "true")

    clone = tmp_path / "clone"
    try:
        subprocess.run(
            [
                "git",
                "clone",
                "-q",
                "--filter=blob:none",
                "--no-local",
                f"file://{origin}",
                str(clone),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:  # pragma: no cover - host policy
        pytest.skip(f"local Git refused the partial clone: {exc.stderr}")
    applied = subprocess.run(
        ["git", "-C", str(clone), "config", "--get", "remote.origin.partialclonefilter"],
        check=False,
        capture_output=True,
        text=True,
    )
    if applied.stdout.strip() != "blob:none":  # pragma: no cover - host policy
        pytest.skip("local Git did not apply the blob:none partial-clone filter")
    _git(clone, "config", "user.email", "test@example.test")
    _git(clone, "config", "user.name", "Test")
    _git(clone, "fetch", "-q", "origin", "base-ref:base-ref")
    return clone


# --- 1. no merge base ------------------------------------------------------


def test_missing_merge_base_is_not_a_generic_bounds_failure(tmp_path: Path) -> None:
    repo = _unrelated_histories(tmp_path)

    context = collect_diff_context(repo, "detached-base", "HEAD")

    assert context.completeness == "unavailable"
    assert context.reason == "merge_base_missing"
    assert "no merge base" in context.detail
    # Deepening history is the repair, so this is agent work, not review work.
    assert context.fetch_repairable is True
    assert "deepen" in context.remediation.casefold()


def test_preview_withholds_the_verdict_when_no_merge_base_exists(
    tmp_path: Path,
) -> None:
    repo = _unrelated_histories(tmp_path)

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--preview",
            "--base",
            "detached-base",
            "--head",
            "HEAD",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["diff_status"]["completeness"] == "unavailable"
    assert payload["diff_status"]["reason"] == "merge_base_missing"
    assert payload["trigger"]["evaluation_status"] == "not_evaluated"
    assert payload["trigger"]["should_run"] is None
    assert payload["trigger"]["skip_reason"] is None
    assert "no_match" not in json.dumps(payload["trigger"])
    assert payload["merge_verdict"] == "unknown"
    assert payload["can_merge_without_human"] is False


def test_verify_fails_closed_and_names_the_missing_merge_base(tmp_path: Path) -> None:
    repo = _unrelated_histories(tmp_path)
    (repo / "shipgate.yaml").write_text('version: "0.1"\n', encoding="utf-8")
    _commit(repo, "adopt shipgate")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--base",
            "detached-base",
            "--head",
            "HEAD",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["diff_status"]["reason"] == "merge_base_missing"
    assert payload["base_status"] == "archive_failed"
    assert payload["merge_verdict"] == "unknown"
    assert payload["can_merge_without_human"] is False
    assert any("merge_base_missing" in note for note in payload["base_notes"])
    # The refs are present; only history depth is, so fetching is the repair.
    assert payload["control"]["next_action"]["kind"] == "fetch_base"


# --- 2. partial clone, objects never fetched -------------------------------


def test_partial_clone_keeps_changed_paths_when_blobs_are_missing(
    tmp_path: Path,
) -> None:
    clone = _blobless_clone(tmp_path)

    context = collect_diff_context(clone, "base-ref", "HEAD")

    # `--name-status` answers fully in a blobless clone even though the
    # textual diff cannot be produced, and those paths are precisely what
    # says the PR touches an agent surface.
    assert context.completeness == "partial"
    assert context.reason == "objects_missing"
    assert "src/agent.py" in context.changed_files
    assert context.fetch_repairable is True
    assert "GIT_NO_LAZY_FETCH" in context.remediation


def test_preview_routes_a_blobless_clone_to_hydrating_objects(tmp_path: Path) -> None:
    clone = _blobless_clone(tmp_path)

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(clone),
            "--preview",
            "--base",
            "base-ref",
            "--head",
            "HEAD",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["diff_status"]["completeness"] == "partial"
    assert payload["diff_status"]["reason"] == "objects_missing"
    assert "src/agent.py" in payload["changed_files"]
    assert payload["trigger"]["evaluation_status"] == "not_evaluated"
    assert payload["trigger"]["skip_reason"] is None
    assert payload["control"]["next_action"]["kind"] == "fetch_base"
    assert payload["can_merge_without_human"] is False


def test_unconfigured_workspace_still_reports_the_diff_failure(tmp_path: Path) -> None:
    """The cold-start case this class of failure actually comes from.

    Shallow and blobless clones of un-adopted repositories are the normal
    shape of first contact. Routing them to "Shipgate is not configured in
    this workspace" answers a question nobody asked and hides the fact that
    the PR was never inspected.
    """

    clone = _blobless_clone(tmp_path)
    assert not (clone / "shipgate.yaml").exists()

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(clone),
            "--preview",
            "--base",
            "base-ref",
            "--head",
            "HEAD",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["control"]["next_action"]["kind"] != "initialize"
    assert "not configured" not in payload["headline"]
    assert "objects_missing" in payload["headline"]
    assert payload["diff_status"]["reason"] == "objects_missing"


# --- 3. an agent diff whose body is unreadable -----------------------------


def test_body_limit_keeps_paths_and_never_reports_no_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A binary-heavy PR must not lose its `agent.py` evidence.

    The Google ADK shape: one small `agent.py` that adds an `LlmAgent` and
    `FunctionTool` bindings, shipped alongside demo assets large enough to
    push the aggregate diff past the static body bound.
    """

    repo = _repo(tmp_path / "repo")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit(repo, "base")
    _git(repo, "branch", "base-ref")

    agent = repo / "src" / "agent.py"
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_text(ADK_AGENT_SOURCE, encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "demo.txt").write_text("x" * 200_000 + "\n", encoding="utf-8")
    _commit(repo, "add adk agent plus demo assets")

    monkeypatch.setattr(verify_git, "_DIFF_BODY_LIMIT", 4096)

    context = collect_diff_context(repo, "base-ref", "HEAD")

    assert context.completeness == "partial"
    assert context.reason == "body_limit_exceeded"
    assert "src/agent.py" in context.changed_files
    assert context.fetch_repairable is False

    # The path evidence alone does not fire the ADK rule (it keys on the
    # `FunctionTool(` token in the body), so the honest answer is "not
    # evaluated" — never "nothing in this PR signals a tool-surface change".
    trigger = evaluate(
        paths=list(context.changed_files),
        diff_text=context.diff_text,
        manifest_present=False,
        user_requested=True,
        input_status="partial",
    )
    assert trigger["evaluation_status"] == "not_evaluated"
    assert trigger["should_run"] is None
    assert trigger["skip_reason"] is None
    assert trigger["next_action"]["kind"] == "input_required"


def test_a_readable_agent_diff_still_runs(tmp_path: Path) -> None:
    """The control: with the body readable, the same PR routes to a run."""

    repo = _repo(tmp_path / "repo")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit(repo, "base")
    _git(repo, "branch", "base-ref")

    agent = repo / "src" / "agent.py"
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_text(ADK_AGENT_SOURCE, encoding="utf-8")
    _commit(repo, "add adk agent")

    context = collect_diff_context(repo, "base-ref", "HEAD")

    assert context.completeness == "complete"
    assert context.reason is None
    trigger = evaluate(
        paths=list(context.changed_files),
        diff_text=context.diff_text,
        manifest_present=False,
        user_requested=True,
    )
    assert trigger["evaluation_status"] == "evaluated"
    assert trigger["should_run"] is True
    assert "TRIGGER-FUNCTION-TOOL-DECORATOR" in {
        match["id"] for match in trigger["matched_rules"]
    }


# --- the strict wrapper keeps its contract ---------------------------------


def test_strict_wrapper_refuses_to_hand_back_partial_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Callers that cannot represent partial input must not receive it silently."""

    repo = _repo(tmp_path / "repo")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit(repo, "base")
    _git(repo, "branch", "base-ref")
    (repo / "README.md").write_text("x" * 200_000 + "\n", encoding="utf-8")
    _commit(repo, "large edit")

    monkeypatch.setattr(verify_git, "_DIFF_BODY_LIMIT", 4096)

    with pytest.raises(DiffInputError) as excinfo:
        diff_revspec_context(repo, "base-ref...HEAD")

    assert excinfo.value.context.reason == "body_limit_exceeded"
    assert excinfo.value.context.changed_files == ("README.md",)


def test_diagnostics_do_not_leak_the_local_checkout_path(tmp_path: Path) -> None:
    repo = _unrelated_histories(tmp_path)

    context = collect_diff_context(repo, "detached-base", "HEAD")

    assert str(repo) not in context.detail
    assert str(repo) not in context.note
