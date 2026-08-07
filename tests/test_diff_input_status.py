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
from pydantic import ValidationError
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


MINIMAL_MANIFEST = """\
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


def _shallow_clone(tmp_path: Path) -> Path:
    """A shallow clone whose truncated history hides a merge base that exists.

    The `actions/checkout` default shape: head fetched at depth 1, base
    fetched at depth 1, no reachable common ancestor between them.
    """

    origin = _repo(tmp_path / "origin")
    (origin / "README.md").write_text("one\n", encoding="utf-8")
    _commit(origin, "c1")
    (origin / "README.md").write_text("two\n", encoding="utf-8")
    _commit(origin, "c2")
    _git(origin, "branch", "base-ref")
    agent = origin / "src" / "agent.py"
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_text(ADK_AGENT_SOURCE, encoding="utf-8")
    _commit(origin, "add adk agent")

    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", "--no-local", f"file://{origin}", str(clone)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(clone, "config", "user.email", "test@example.test")
    _git(clone, "config", "user.name", "Test")
    _git(clone, "fetch", "-q", "--depth", "1", "origin", "base-ref:base-ref")
    assert _git(clone, "rev-parse", "--is-shallow-repository") == "true"
    return clone


def _blobless_clone(
    tmp_path: Path,
    *,
    capability_path: str = "src/agent.py",
    capability_text: str = ADK_AGENT_SOURCE,
) -> Path:
    """A partial clone missing the blobs the base side of the diff needs."""

    origin = _repo(tmp_path / "origin")
    (origin / "README.md").write_text("base\n", encoding="utf-8")
    _commit(origin, "base")
    _git(origin, "branch", "base-ref")

    agent = origin / capability_path
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_text(capability_text, encoding="utf-8")
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


def test_shallow_history_reports_a_repairable_missing_merge_base(
    tmp_path: Path,
) -> None:
    clone = _shallow_clone(tmp_path)

    context = collect_diff_context(clone, "base-ref", "HEAD")

    assert context.completeness == "unavailable"
    assert context.reason == "merge_base_missing"
    assert "no merge base" in context.detail
    # Deepening history really does restore the merge base here, so this is
    # agent work rather than review work.
    assert context.fetch_repairable is True
    assert "deepen" in context.remediation.casefold()


def test_deepening_a_shallow_clone_actually_repairs_the_diff(
    tmp_path: Path,
) -> None:
    """The remediation must be the one that works, not the one that reads well."""

    clone = _shallow_clone(tmp_path)
    assert collect_diff_context(clone, "base-ref", "HEAD").reason == "merge_base_missing"

    _git(clone, "fetch", "-q", "--deepen=10", "origin", "main", "base-ref")

    repaired = collect_diff_context(clone, "base-ref", "HEAD")
    assert repaired.completeness == "complete"
    assert "src/agent.py" in repaired.changed_files


def test_unrelated_histories_are_never_routed_to_another_fetch(
    tmp_path: Path,
) -> None:
    """Git reports both causes as "no merge base"; only one is fetch-repairable.

    Two orphan roots in a complete checkout share no ancestor at all, so
    `--deepen`/`--unshallow` can never produce one. Routing this to `fetch_base`
    would loop an agent forever.
    """

    repo = _unrelated_histories(tmp_path)
    assert _git(repo, "rev-parse", "--is-shallow-repository") == "false"

    context = collect_diff_context(repo, "detached-base", "HEAD")

    assert context.completeness == "unavailable"
    assert context.reason == "unrelated_histories"
    assert "no merge base" in context.detail
    assert context.fetch_repairable is False
    assert "no fetch can create one" in context.remediation


def test_unrelated_histories_route_a_verify_run_to_a_human(tmp_path: Path) -> None:
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
    assert payload["diff_status"]["reason"] == "unrelated_histories"
    assert payload["diff_status"]["fetch_repairable"] is False
    assert payload["control"]["next_action"]["kind"] != "fetch_base"
    assert payload["can_merge_without_human"] is False


def test_preview_withholds_the_verdict_when_no_merge_base_exists(
    tmp_path: Path,
) -> None:
    clone = _shallow_clone(tmp_path)

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
    assert payload["diff_status"]["completeness"] == "unavailable"
    assert payload["diff_status"]["reason"] == "merge_base_missing"
    assert payload["trigger"]["evaluation_status"] == "not_evaluated"
    assert payload["trigger"]["should_run"] is None
    assert payload["trigger"]["skip_reason"] is None
    assert "no_match" not in json.dumps(payload["trigger"])
    assert payload["merge_verdict"] == "unknown"
    assert payload["can_merge_without_human"] is False


def test_verify_fails_closed_and_names_the_missing_merge_base(tmp_path: Path) -> None:
    clone = _shallow_clone(tmp_path)
    (clone / "shipgate.yaml").write_text('version: "0.1"\n', encoding="utf-8")
    _commit(clone, "adopt shipgate")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(clone),
            "--base",
            "base-ref",
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


# --- the artifact may not contradict itself --------------------------------


def test_partial_evidence_that_proves_relevance_keeps_its_run_verdict(
    tmp_path: Path,
) -> None:
    """A path rule needs no diff body, so partial input can still decide "run".

    The evaluator publishes that verdict deliberately. The headline and
    `control.reason` summarize the same artifact and must not answer
    "no relevance verdict was reached" over the top of `should_run: true`.
    """

    clone = _blobless_clone(
        tmp_path,
        capability_path="tools/new_mcp.json",
        capability_text='{"mcpServers": {}}\n',
    )

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
    trigger = payload["trigger"]

    assert payload["diff_status"]["completeness"] == "partial"
    assert "tools/new_mcp.json" in payload["changed_files"]
    assert trigger["evaluation_status"] == "evaluated"
    assert trigger["should_run"] is True
    assert "TRIGGER-MCP-EXPORT-CHANGED" in {
        match["id"] for match in trigger["matched_rules"]
    }

    for surface in (payload["headline"], payload["control"]["reason"]):
        assert "no relevance verdict" not in surface
        assert "relevance is established" in surface
    # The diff still has to be recovered before any merge verdict is trusted.
    assert payload["merge_verdict"] == "unknown"
    assert payload["can_merge_without_human"] is False


def test_verify_merges_partial_worktree_paths_into_the_change_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed worktree read still contributes the paths it did collect.

    Reporting "changed paths were collected" in `base_notes` while handing the
    trigger an empty list loses exactly the path-rule match those paths exist
    to produce.
    """

    repo = _repo(tmp_path / "repo")
    (repo / "shipgate.yaml").write_text(MINIMAL_MANIFEST, encoding="utf-8")
    (repo / "tools.json").write_text('{"tools": []}\n', encoding="utf-8")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit(repo, "base")
    _git(repo, "branch", "base-ref")

    # Uncommitted: one capability path plus enough text to blow the body bound.
    (repo / "tools").mkdir()
    (repo / "tools" / "new_mcp.json").write_text('{"mcpServers": {}}\n', encoding="utf-8")
    (repo / "README.md").write_text("x" * 200_000 + "\n", encoding="utf-8")

    monkeypatch.setattr(verify_git, "_DIFF_BODY_LIMIT", 4096)

    # No --head: that is what makes verify read the working tree rather than an
    # archived committed tree, which is the collector under test.
    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--base",
            "base-ref",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)

    assert payload["diff_status"]["completeness"] == "partial"
    assert payload["diff_status"]["reason"] == "body_limit_exceeded"
    assert any("paths were collected" in note for note in payload["base_notes"])
    # The claim in base_notes and the published change set must agree.
    assert "tools/new_mcp.json" in payload["changed_files"]
    assert "TRIGGER-MCP-EXPORT-CHANGED" in {
        match["id"] for match in payload["trigger"]["matched_rules"]
    }
    assert payload["merge_verdict"] == "unknown"
    assert payload["can_merge_without_human"] is False


# --- the status, the repair, and the headline must agree -------------------


def test_a_current_artifact_cannot_omit_its_input_health(tmp_path: Path) -> None:
    """`diff_status` is the input-health contract; dropping it must not validate.

    A v0.7 payload with no `diff_status` would be indistinguishable from one
    that read its diff cleanly — the exact claim the field exists to prevent.
    """

    from agents_shipgate.schemas.verifier import VerifierArtifact, VerifierDiffStatus

    repo = _repo(tmp_path / "repo")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit(repo, "base")
    result = runner.invoke(
        app, ["verify", "--workspace", str(repo), "--preview", "--json"]
    )
    assert result.exit_code == 0, result.output
    emitted = json.loads(result.output)
    assert emitted["diff_status"]["completeness"] == "complete"

    without = {k: v for k, v in emitted.items() if k != "diff_status"}
    with pytest.raises(ValidationError):
        VerifierArtifact.model_validate(without)

    # A pre-v0.7 artifact legitimately has none, and normalizes to "unknown" —
    # which is not "complete", so it still withholds trust in a negative result.
    legacy = dict(without)
    legacy["verifier_schema_version"] = "0.6"
    normalized = VerifierArtifact.model_validate(legacy)
    assert normalized.verifier_schema_version == "0.7"
    assert normalized.diff_status == VerifierDiffStatus.unknown()
    assert normalized.diff_status.completeness == "unknown"
    assert normalized.diff_status.reason is None


def test_fetch_repairable_cannot_be_claimed_for_a_deterministic_failure() -> None:
    from agents_shipgate.schemas.verifier import VerifierDiffStatus

    with pytest.raises(ValueError, match="fetching cannot repair"):
        VerifierDiffStatus(
            completeness="unavailable",
            reason="unrelated_histories",
            fetch_repairable=True,
        )


def test_the_worst_failure_decides_both_status_and_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fetchable committed failure must not authorize a fetch for a worse one.

    Committed diff fails fetch-repairably (`refs_missing`); the worktree then
    fails deterministically. Deriving the action incrementally published
    `fetch_base` beside a `diff_status` no fetch could repair — a loop.
    """

    repo = _repo(tmp_path / "repo")
    (repo / "shipgate.yaml").write_text(MINIMAL_MANIFEST, encoding="utf-8")
    (repo / "tools.json").write_text('{"tools": []}\n', encoding="utf-8")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit(repo, "base")
    (repo / "README.md").write_text("uncommitted\n", encoding="utf-8")

    def _explode(*args: object, **kwargs: object) -> tuple[list[str], str]:
        raise RuntimeError("simulated deterministic worktree failure")

    monkeypatch.setattr(
        "agents_shipgate.cli.verify.orchestrator.working_tree_context", _explode
    )

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--base",
            "does-not-exist",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    status = payload["diff_status"]
    action = payload["control"]["next_action"]

    assert status["reason"] == "git_failed"
    assert status["fetch_repairable"] is False
    # The published status and the authorized repair may not disagree.
    assert action["kind"] != "fetch_base"
    assert payload["can_merge_without_human"] is False


def test_the_failure_headline_matches_the_control_route(tmp_path: Path) -> None:
    """A `fetch_base` route may not be summarized as "human review required"."""

    repo = _repo(tmp_path / "repo")
    (repo / "shipgate.yaml").write_text(MINIMAL_MANIFEST, encoding="utf-8")
    (repo / "tools.json").write_text('{"tools": []}\n', encoding="utf-8")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit(repo, "base")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    control = payload["control"]

    assert payload["diff_status"]["reason"] == "refs_missing"
    assert control["state"] == "agent_action_required"
    assert control["next_action"]["kind"] == "fetch_base"
    assert control["human_review"]["required"] is False
    for surface in (payload["headline"], control["reason"]):
        assert "human review required" not in surface.casefold()
        assert "refs_missing" in surface


def test_a_force_run_verdict_is_not_attributed_to_unread_paths(
    tmp_path: Path,
) -> None:
    """An adopted repo force-runs on the manifest, with no paths read at all."""

    repo = _repo(tmp_path / "repo")
    (repo / "shipgate.yaml").write_text(MINIMAL_MANIFEST, encoding="utf-8")
    (repo / "tools.json").write_text('{"tools": []}\n', encoding="utf-8")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit(repo, "base")

    result = runner.invoke(
        app,
        [
            "verify",
            "--workspace",
            str(repo),
            "--preview",
            "--base",
            "origin/main",
            "--head",
            "HEAD",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    trigger = payload["trigger"]

    assert payload["changed_files"] == []
    assert trigger["force_run"] is True
    assert trigger["should_run"] is True
    assert {match["id"] for match in trigger["matched_rules"]} == {
        "TRIGGER-EXISTING-MANIFEST-PRESENT"
    }
    for surface in (payload["headline"], payload["control"]["reason"]):
        assert "already shows an agent-capability surface" not in surface
        assert "already configured for Shipgate" in surface
