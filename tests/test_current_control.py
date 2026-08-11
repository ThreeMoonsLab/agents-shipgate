"""The current-control pointer and the refresh protocol built on it.

The bug this covers is a control-plane one, not a verdict one: a coding agent
kept enforcing a `human_review_required` it had cached in conversation state
after a human committed the reviewed change and a newer complete run existed.
The same gap runs backward — a cached `complete` acted on after the workspace
moved. Both directions are exercised here against the real engine.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli._artifact_lifecycle import ArtifactLifecycleError
from agents_shipgate.cli.main import app
from agents_shipgate.cli.scan import writing as scan_writing
from agents_shipgate.cli.scan.orchestrator import run_scan
from agents_shipgate.cli.verification import assemble, prepare, worker
from agents_shipgate.cli.verify import orchestrator as verify_orchestrator
from agents_shipgate.cli.verify.git import (
    commit_sha,
    merge_base_sha,
    repository_identity,
    tree_sha,
    working_tree_context,
)
from agents_shipgate.cli.verify.orchestrator import run_preview, run_verify
from agents_shipgate.core import current_control as current_control_module
from agents_shipgate.core.current_control import (
    CurrentControlUnavailable,
    LiveWorkspace,
    begin_current_control,
    current_control_path,
    publish_current_control,
    read_current_control,
)
from agents_shipgate.schemas.current_control import (
    CURRENT_CONTROL_ARTIFACT_NAME,
    AgentActionRequiredCurrentControl,
    CompleteCurrentControl,
    CurrentControlArtifactRef,
    CurrentControlPointer,
    CurrentControlWorkspaceIdentity,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "samples" / "clean_read_only_agent"
runner = CliRunner()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A committed workspace whose clean verify reaches ``complete``."""

    workspace = tmp_path / "repo"
    workspace.mkdir()
    for name in ("shipgate.yaml", "tools.json"):
        shutil.copy(SAMPLE / name, workspace / name)
    # Every adopted workspace gitignores the reports directory (`init` writes
    # this block). Without it, generated artifacts become worktree verification
    # inputs, which is a separate pre-existing hazard this fixture should not
    # simulate.
    (workspace / ".gitignore").write_text("agents-shipgate-reports/\n", encoding="utf-8")
    _git(workspace, "init", "-q", "-b", "main")
    _git(workspace, "config", "user.email", "test@example.test")
    _git(workspace, "config", "user.name", "Test User")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-m", "fixture")
    return workspace


def _verify(repo: Path, **overrides: object) -> tuple[object, object, int]:
    options: dict[str, object] = {
        "workspace": repo,
        "config": Path("shipgate.yaml"),
        "base": None,
        "head": "HEAD",
        "archive_head": True,
        "out": repo / "agents-shipgate-reports",
        "ci_mode": "advisory",
        "fail_on": None,
        "baseline": None,
        "baseline_mode": "new-findings",
        "diff_from": None,
        "policy_packs": None,
        "plugins_enabled": False,
        "strict_plugins": False,
        "suggest_patches": False,
        "no_heuristics": False,
        "verbose": False,
    }
    options.update(overrides)
    return run_verify(**options)  # type: ignore[arg-type]


def _live(repo: Path) -> LiveWorkspace:
    """The live workspace, resolved the way `agents-shipgate agent control` does."""

    changed, _ = working_tree_context(repo, exclude=repo / "agents-shipgate-reports")

    def resolve_commit(ref: str) -> str | None:
        try:
            return commit_sha(repo, ref)
        except Exception:  # noqa: BLE001 - an unresolvable ref is drift.
            return None

    def resolve_merge_base(base: str, head: str) -> str | None:
        try:
            return merge_base_sha(repo, base, head)
        except Exception:  # noqa: BLE001 - an unresolvable range is drift.
            return None

    return LiveWorkspace(
        root=repo,
        repository=repository_identity(repo),
        head_commit_sha=commit_sha(repo, "HEAD"),
        head_tree_sha=tree_sha(repo, "HEAD"),
        changed_paths=tuple(changed),
        resolve_commit=resolve_commit,
        resolve_merge_base=resolve_merge_base,
    )


def _pointer(repo: Path) -> dict[str, object]:
    path = repo / "agents-shipgate-reports" / CURRENT_CONTROL_ARTIFACT_NAME
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The two reported failure directions
# ---------------------------------------------------------------------------


def test_cached_stop_is_superseded_after_the_human_commits(repo: Path) -> None:
    """Forward regression: the exact issue-#339 sequence.

    Worktree verify stops on a trust-root edit; a human commits the reviewed
    change; the committed-ref run completes. An agent that refreshes must see
    the new identity, not the `must_stop` it is holding.
    """

    manifest = repo / "shipgate.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n# reviewed\n", encoding="utf-8")

    stopped, _, _ = _verify(repo, archive_head=False)
    # A trust-root edit owes a human the merge decision, not the whole turn.
    # The staleness this test is about is the pointer's, not the state's.
    assert stopped.control.state == "review_publishable"
    cached = _pointer(repo)
    assert cached["control"]["state"] == "review_publishable"
    assert cached["control"]["permissions"]["merge"] is False
    assert cached["control"]["permissions"]["update_pr"] is True

    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "human commits the reviewed change")

    completed, _, _ = _verify(repo)
    assert completed.control.state == "complete"

    refreshed = read_current_control(
        repo / "agents-shipgate-reports", live=_live(repo)
    ).pointer
    assert refreshed.current_control_id != cached["current_control_id"]
    assert refreshed.control.state == "complete"
    assert refreshed.control.completion_allowed is True
    assert refreshed.control.must_stop is False
    # Completion authority is only representable alongside the receipt that
    # backs it, so "complete" can never be a leftover from another run.
    assert "verification_receipt" in refreshed.artifacts
    assert refreshed.request_id is not None and refreshed.decision_id is not None


def test_cached_completion_cannot_survive_a_committed_change(repo: Path) -> None:
    """Reverse regression: a remembered `complete` is not authority later.

    Byte consistency is not generation consistency. Every bound artifact still
    hashes correctly after an unrelated commit, so the read has to compare the
    pointer's workspace identity against the live repository or it hands back
    completion authority for a workspace that has moved on.
    """

    completed, _, _ = _verify(repo)
    assert completed.control.state == "complete"
    reports = repo / "agents-shipgate-reports"
    authorized = read_current_control(reports, live=_live(repo)).pointer
    assert authorized.control.completion_allowed is True

    _git(repo, "commit", "--allow-empty", "-m", "an unrelated commit")

    with pytest.raises(CurrentControlUnavailable) as raised:
        read_current_control(reports, live=_live(repo))
    assert raised.value.reason == "workspace_changed"

    _verify(repo)
    current = read_current_control(reports, live=_live(repo)).pointer
    assert current.current_control_id != authorized.current_control_id
    assert current.control.completion_allowed is True


def test_cached_completion_cannot_survive_an_uncommitted_edit(repo: Path) -> None:
    """A worktree decision is about working-tree content, which HEAD hides.

    Editing a file leaves HEAD and its tree byte-identical, so the overlay the
    decision committed to has to be recomputed -- both its content and the set
    of paths it covered, since a file changed *after* the decision appears in
    neither.
    """

    completed, _, _ = _verify(repo, archive_head=False)
    assert completed.control.state == "complete"
    reports = repo / "agents-shipgate-reports"
    assert read_current_control(reports, live=_live(repo)).pointer.control.completion_allowed

    head_before = commit_sha(repo, "HEAD")
    tools = repo / "tools.json"
    tools.write_text(tools.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert commit_sha(repo, "HEAD") == head_before

    with pytest.raises(CurrentControlUnavailable) as raised:
        read_current_control(reports, live=_live(repo))
    assert raised.value.reason == "workspace_changed"


def test_a_committed_tree_stop_does_not_survive_a_worktree_edit(repo: Path) -> None:
    """Stale denial: a pre-change stop must not be enforced after the change.

    The archived run cannot clear itself -- re-running the same `--head`
    verification reproduces the same decision -- so the refusal has to route the
    caller to a worktree verification instead of leaving the old stop standing.
    """

    _git(repo, "checkout", "-b", "feature")
    manifest = repo / "shipgate.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n# reviewed\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "a trust-root edit a human must review")
    stopped, _, _ = _verify(repo, base="main")
    # A trust-root edit is a human decision the agent may still publish for;
    # what must not survive the worktree edit is the pointer, not the state.
    assert stopped.control.state == "review_publishable"
    reports = repo / "agents-shipgate-reports"
    pointer = read_current_control(reports, live=_live(repo)).pointer
    assert pointer.workspace_identity.snapshot_kind == "committed_tree"
    assert pointer.control.completion_allowed is False
    assert pointer.control.permissions.merge is False

    (repo / "scratch.py").write_text("# a human edited the worktree\n", encoding="utf-8")

    with pytest.raises(CurrentControlUnavailable) as raised:
        read_current_control(reports, live=_live(repo))
    assert raised.value.reason == "workspace_changed"
    assert "omit --head" in str(raised.value)


@pytest.mark.parametrize("mutation", ["chmod", "symlink"])
def test_metadata_only_changes_invalidate_a_worktree_decision(
    repo: Path, mutation: str
) -> None:
    """Content is not the whole capability.

    Flipping a tool file's executable bit changes no bytes, and swapping it for
    a symlink to an identical in-repo file changes no bytes either. Both are
    changes the decision must not survive, and both leave HEAD, the tree, and a
    content-only overlay row identical.
    """

    tools = repo / "tools.json"
    tools.write_text(tools.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    _verify(repo, archive_head=False)
    reports = repo / "agents-shipgate-reports"
    plan = json.loads((reports / "verification-plan.json").read_text(encoding="utf-8"))
    assert "tools.json" in plan["inputs"]["changed_paths"]
    assert read_current_control(reports, live=_live(repo)).pointer.lifecycle_state == "terminal"

    if mutation == "chmod":
        tools.chmod(0o755)
    else:
        twin = repo / "twin.json"
        twin.write_bytes(tools.read_bytes())
        tools.unlink()
        tools.symlink_to("twin.json")

    with pytest.raises(CurrentControlUnavailable) as raised:
        read_current_control(reports, live=_live(repo))
    assert raised.value.reason == "workspace_changed"


def test_advancing_the_base_invalidates_a_decision_about_the_range(repo: Path) -> None:
    """A decision about `base...HEAD` is a decision about that range.

    Advancing the base can empty the range without touching HEAD or the working
    tree, which leaves every HEAD-based check satisfied while the evidence the
    decision rested on is gone.
    """

    _git(repo, "checkout", "-b", "feature")
    tools = repo / "tools.json"
    tools.write_text(tools.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feature work")

    _verify(repo, base="main")
    reports = repo / "agents-shipgate-reports"
    pointer = read_current_control(reports, live=_live(repo)).pointer
    assert pointer.workspace_identity.base_ref == "main"
    assert pointer.workspace_identity.base_commit_sha
    head_before = commit_sha(repo, "HEAD")

    # Advance the base only. HEAD and the working tree are untouched.
    _git(repo, "branch", "-f", "main", "HEAD")
    assert commit_sha(repo, "HEAD") == head_before

    with pytest.raises(CurrentControlUnavailable) as raised:
        read_current_control(reports, live=_live(repo))
    assert raised.value.reason == "workspace_changed"
    assert "base this decision was made against moved" in str(raised.value)


def test_completion_is_refused_when_the_workspace_cannot_be_checked(repo: Path) -> None:
    """`live=None` means "not compared", which is never a pass for completion."""

    completed, _, _ = _verify(repo)
    assert completed.control.state == "complete"

    with pytest.raises(CurrentControlUnavailable) as raised:
        read_current_control(repo / "agents-shipgate-reports")
    assert raised.value.reason == "workspace_unverified"


def test_a_local_run_with_a_base_stays_current_on_a_clean_tree(repo: Path) -> None:
    """Stale denial: the canonical local flow must not refuse itself.

    `verify` without `--head` carries the union of `base...HEAD` and the
    worktree in `plan.inputs.changed_paths`, so that set is not the uncommitted
    set. Comparing them for equality refused a clean workspace the instant the
    run that produced it finished -- the exact failure direction this pointer
    exists to prevent.
    """

    _git(repo, "checkout", "-b", "feature")
    tools = repo / "tools.json"
    tools.write_text(tools.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "a committed change on the branch")

    _verify(repo, base="main", archive_head=False)
    reports = repo / "agents-shipgate-reports"
    plan = json.loads((reports / "verification-plan.json").read_text(encoding="utf-8"))
    # The committed change is in the plan's set; the working tree is clean. A
    # base that did not resolve would leave changed_paths empty and quietly stop
    # this test exercising the union set it exists to cover.
    assert plan["subject"]["git"]["snapshot_kind"] == "worktree_overlay"
    assert plan["inputs"]["changed_paths"] == ["tools.json"]
    live = _live(repo)
    assert live.changed_paths == ()

    assert read_current_control(reports, live=live).pointer.lifecycle_state == "terminal"


def test_committed_tree_completion_cannot_survive_a_new_uncommitted_file(
    repo: Path,
) -> None:
    """A committed-tree decision stops at HEAD; later working changes are new.

    An untracked tool file added beside a clean `complete` is exactly the
    capability change an archived-commit run could not have covered.
    """

    completed, _, _ = _verify(repo)
    assert completed.control.state == "complete"
    reports = repo / "agents-shipgate-reports"
    pointer = read_current_control(reports, live=_live(repo)).pointer
    assert pointer.workspace_identity.snapshot_kind == "committed_tree"
    assert pointer.control.completion_allowed is True

    (repo / "extra-tools.json").write_text('{"tools": []}\n', encoding="utf-8")

    with pytest.raises(CurrentControlUnavailable) as raised:
        read_current_control(reports, live=_live(repo))
    assert raised.value.reason == "workspace_changed"


def test_an_interrupted_run_leaves_no_decision_current(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash must not leave the previous terminal decision advertised."""

    _verify(repo)
    assert _pointer(repo)["control"]["completion_allowed"] is True

    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("interrupted mid-run")

    monkeypatch.setattr(verify_orchestrator, "_write_artifacts", explode)
    with pytest.raises(RuntimeError):
        _verify(repo)

    stranded = read_current_control(repo / "agents-shipgate-reports").pointer
    assert stranded.lifecycle_state == "in_progress"
    assert stranded.control.state == "unavailable"
    assert stranded.control.must_stop is True
    assert stranded.control.completion_allowed is False
    assert stranded.artifacts == {}


def test_a_failed_lifecycle_cleanup_leaves_the_pointer_non_terminal(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup failure is fail-closed: invalidate happens before it."""

    _verify(repo)

    def refuse(out_dir: Path) -> None:
        raise ArtifactLifecycleError(out_dir / "verifier.json", OSError("locked"))

    monkeypatch.setattr(verify_orchestrator, "clear_verifier_route_artifacts", refuse)
    with pytest.raises(ArtifactLifecycleError):
        _verify(repo)

    stranded = read_current_control(repo / "agents-shipgate-reports").pointer
    assert stranded.lifecycle_state == "in_progress"
    assert stranded.control.must_stop is True


# ---------------------------------------------------------------------------
# Command scoping
# ---------------------------------------------------------------------------


def test_a_standalone_scan_never_retains_merge_authorization(repo: Path) -> None:
    completed, _, _ = _verify(repo)
    assert completed.control.state == "complete"

    run_scan(
        config_path=repo / "shipgate.yaml",
        output_dir=repo / "agents-shipgate-reports",
        ci_mode="advisory",
        plugins_enabled=False,
    )

    current = read_current_control(repo / "agents-shipgate-reports").pointer
    assert current.operation == "scan"
    assert current.control.state == "agent_action_required"
    assert current.control.completion_allowed is False
    assert "verification_receipt" not in current.artifacts


def test_a_scan_binds_only_the_formats_it_wrote(repo: Path) -> None:
    """`scan --format markdown` must not claim an earlier run's JSON report.

    A scan only replaces the formats it emits, so a verifier's `report.json`
    survives a markdown-only scan. Binding it would present the previous run's
    decision ids as part of the current set.
    """

    _verify(repo)
    reports = repo / "agents-shipgate-reports"
    stale_report = json.loads((reports / "report.json").read_text(encoding="utf-8"))
    assert stale_report["request_id"]

    run_scan(
        config_path=repo / "shipgate.yaml",
        output_dir=reports,
        formats=["markdown"],
        ci_mode="advisory",
        plugins_enabled=False,
        packet_enabled=False,
    )

    current = read_current_control(reports, live=_live(repo)).pointer
    assert current.operation == "scan"
    assert "report" not in current.artifacts
    assert "packet" not in current.artifacts
    assert current.artifacts["report_markdown"].path == "report.md"
    # The stale JSON report is still on disk; it is simply no longer current.
    assert (reports / "report.json").is_file()
    assert current.request_id is None


def test_assembly_binds_the_receipt_it_emitted(repo: Path) -> None:
    """`assemble --out` accepts any name, so the canonical path may be stale.

    The pointer has to bind the receipt this run actually closed. Binding the
    canonical path instead would either miss it entirely -- leaving a valid
    custom-output run unable to publish its own completion -- or bind an older
    run's receipt beside a newer decision.
    """

    _verify(repo)
    reports = repo / "agents-shipgate-reports"
    stale_receipt = (reports / "verification-receipt.json").read_text(encoding="utf-8")
    stale_request = json.loads(stale_receipt)["request_id"]

    # A second run against a different tree produces a different request.
    (repo / "notes.txt").write_text("notes\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "unrelated file")
    _verify(repo)
    fresh_request = json.loads(
        (reports / "verification-receipt.json").read_text(encoding="utf-8")
    )["request_id"]
    assert fresh_request != stale_request

    unit = reports / "distributed-unit.json"
    worker(
        plan_path=reports / "verification-plan.json",
        workspace=repo,
        diff_path=reports / "verification-input.diff",
        out=unit,
    )
    # The canonical path now holds the previous run's receipt.
    (reports / "verification-receipt.json").write_text(stale_receipt, encoding="utf-8")
    assemble(
        plan_path=reports / "verification-plan.json",
        unit_paths=[unit],
        verifier_path=reports / "verifier.json",
        artifacts_root=reports,
        out=reports / "custom-receipt.json",
    )

    published = _pointer(repo)
    assert published["request_id"] == fresh_request
    assert published["artifacts"]["verification_receipt"]["path"] == "custom-receipt.json"
    assert published["control"]["state"] == "complete"
    assert published["control"]["completion_allowed"] is True
    assert json.loads((reports / "verification-receipt.json").read_text())["request_id"] == (
        stale_request
    )


def test_completion_is_refused_when_the_bound_receipt_closes_another_request(
    repo: Path,
) -> None:
    """The cross-check stands on its own, for producers that get it wrong."""

    _verify(repo)
    reports = repo / "agents-shipgate-reports"
    receipt = json.loads((reports / "verification-receipt.json").read_text(encoding="utf-8"))

    published = publish_current_control(
        reports,
        operation="verify",
        control=CompleteCurrentControl(state="complete", reason="Release ready."),
        request_id="sha256:" + "1" * 64,
        decision_id=receipt["decision_id"],
    )

    assert published.control.state == "human_review_required"
    assert "different request" in published.control.reason


def test_a_preview_neither_completes_nor_binds_an_older_report(repo: Path) -> None:
    _verify(repo)
    reports = repo / "agents-shipgate-reports"
    stale_report = (reports / "report.json").read_bytes()

    run_preview(
        workspace=repo,
        config=Path("shipgate.yaml"),
        base=None,
        head=None,
        out=reports,
    )

    current = read_current_control(reports).pointer
    assert current.operation == "preview"
    assert current.control.completion_allowed is False
    # The previous run's report survives on disk; the preview pointer must not
    # present it as part of the artifact set that is current.
    assert (reports / "report.json").read_bytes() == stale_report
    assert "report" not in current.artifacts
    assert "packet" not in current.artifacts


def test_verify_to_verify_supersedes_the_identity_it_replaced(repo: Path) -> None:
    _verify(repo)
    first = _pointer(repo)
    ids: list[str] = []

    real_begin = current_control_module.begin_current_control

    def record(out_dir: Path, **kwargs: object):
        pointer = real_begin(out_dir, **kwargs)  # type: ignore[arg-type]
        ids.append(pointer.current_control_id)
        return pointer

    verify_orchestrator.begin_current_control = record  # type: ignore[assignment]
    try:
        _verify(repo)
    finally:
        verify_orchestrator.begin_current_control = real_begin  # type: ignore[assignment]

    second = _pointer(repo)
    # The in-progress marker supersedes the previous terminal pointer, and the
    # new terminal pointer supersedes the marker: an unbroken chain with no
    # instant where the old decision was current for the new run.
    assert ids and ids[0] != first["current_control_id"]
    assert second["supersedes"] == ids[0]


def test_verify_reruns_are_byte_identical_for_an_unchanged_workspace(repo: Path) -> None:
    """Identity is a statement about state, not about how many runs happened."""

    _verify(repo)
    first = _pointer(repo)["current_control_id"]
    _verify(repo)
    assert _pointer(repo)["current_control_id"] == first


def test_baseline_save_does_not_disturb_current_pr_control(repo: Path) -> None:
    _verify(repo)
    before = _pointer(repo)

    result = runner.invoke(
        app,
        [
            "baseline",
            "save",
            "--config",
            str(repo / "shipgate.yaml"),
            "--out",
            str(repo / ".agents-shipgate" / "baseline.json"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert _pointer(repo) == before


def test_preparing_a_portable_plan_invalidates_before_it_reads_inputs(repo: Path) -> None:
    """`prepare` opens a new lifecycle, so the previous decision stops being current.

    It invalidates before reading a single input rather than just before
    writing the plan, so the plan and any later replay of it observe one stable
    directory state.
    """

    _verify(repo)
    reports = repo / "agents-shipgate-reports"
    assert _pointer(repo)["control"]["completion_allowed"] is True

    prepare(
        workspace=repo,
        config=Path("shipgate.yaml"),
        base=None,
        head=None,
        baseline=None,
        diff_from=None,
        policy_packs=None,
        ci_mode="advisory",
        no_plugins=True,
        no_heuristics=False,
        evaluation_date=None,
        out=reports / "verification-plan.json",
    )

    opened = read_current_control(reports).pointer
    assert opened.lifecycle_state == "in_progress"
    assert opened.control.must_stop is True
    # The prepared plan replays cleanly: nothing the invalidation touched moved
    # after the plan hashed its inputs.
    worker(
        plan_path=reports / "verification-plan.json",
        workspace=repo,
        diff_path=reports / "verification-input.diff",
        out=reports / "replayed-unit.json",
    )
    assert (reports / "replayed-unit.json").is_file()


def test_the_assembler_publishes_the_terminal_pointer(repo: Path) -> None:
    """The portable assembler closes the lifecycle the same way verify does."""

    _verify(repo)
    reports = repo / "agents-shipgate-reports"
    unit = reports / "distributed-unit.json"
    worker(
        plan_path=reports / "verification-plan.json",
        workspace=repo,
        diff_path=reports / "verification-input.diff",
        out=unit,
    )
    begin_current_control(reports, operation="verify", reason="Assembly pending.")

    assemble(
        plan_path=reports / "verification-plan.json",
        unit_paths=[unit],
        verifier_path=reports / "verifier.json",
        artifacts_root=reports,
        out=reports / "verification-receipt.json",
    )

    closed = read_current_control(reports, live=_live(repo)).pointer
    assert closed.operation == "verify"
    assert closed.lifecycle_state == "terminal"
    assert closed.control.state == "complete"
    assert "verification_receipt" in closed.artifacts
    assert closed.request_id is not None and closed.decision_id is not None


# ---------------------------------------------------------------------------
# Reader protocol
# ---------------------------------------------------------------------------


def test_reader_rejects_a_mixed_artifact_set(repo: Path) -> None:
    _verify(repo)
    reports = repo / "agents-shipgate-reports"
    verifier = reports / "verifier.json"
    verifier.write_text(verifier.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(CurrentControlUnavailable) as raised:
        read_current_control(reports)
    assert raised.value.reason == "artifact_mismatch"


def test_reader_rejects_a_generation_change_underneath_it(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that republishes mid-read must fail the read, not blend it."""

    _verify(repo)
    reports = repo / "agents-shipgate-reports"
    real_validate = current_control_module._validate_bound_artifacts

    def republish_then_validate(
        out_dir: Path, pointer: CurrentControlPointer, **kwargs: object
    ) -> dict[str, bytes]:
        captured = real_validate(out_dir, pointer, **kwargs)
        begin_current_control(
            out_dir,
            operation="verify",
            reason="A competing run started while the pointer was being read.",
        )
        return captured

    monkeypatch.setattr(
        current_control_module, "_validate_bound_artifacts", republish_then_validate
    )
    with pytest.raises(CurrentControlUnavailable) as raised:
        read_current_control(reports, live=_live(repo), attempts=1)
    assert raised.value.reason == "generation_changed"


def test_reader_retries_past_a_republish_but_not_past_real_tampering(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent run should not permanently deny a reader.

    An artifact mismatch under a pointer that also moved is a race worth
    retrying; the same mismatch under a pointer that did not move is a real
    inconsistency and must surface.
    """

    _verify(repo)
    reports = repo / "agents-shipgate-reports"
    real_validate = current_control_module._validate_bound_artifacts
    calls: list[int] = []

    def fail_once_then_republish(
        out_dir: Path, pointer: CurrentControlPointer, **kwargs: object
    ) -> dict[str, bytes]:
        calls.append(1)
        if len(calls) == 1:
            begin_current_control(
                out_dir, operation="verify", reason="A competing run started."
            )
            publish_current_control(
                out_dir,
                operation="scan",
                control=AgentActionRequiredCurrentControl(
                    state="agent_action_required", reason="The competing run finished."
                ),
            )
            raise CurrentControlUnavailable("artifact_mismatch", "raced", path=out_dir)
        return real_validate(out_dir, pointer, **kwargs)

    monkeypatch.setattr(
        current_control_module, "_validate_bound_artifacts", fail_once_then_republish
    )
    assert read_current_control(reports).pointer.operation == "scan"
    assert len(calls) == 2


def test_reader_refuses_a_missing_pointer_rather_than_falling_back(tmp_path: Path) -> None:
    empty = tmp_path / "reports"
    empty.mkdir()
    with pytest.raises(CurrentControlUnavailable) as raised:
        read_current_control(empty)
    assert raised.value.reason == "missing"


def test_reader_refuses_a_symlinked_pointer(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text("{}", encoding="utf-8")
    os.symlink(elsewhere, current_control_path(reports))

    with pytest.raises(CurrentControlUnavailable) as raised:
        read_current_control(reports)
    assert raised.value.reason == "unsafe_pointer"


def test_reader_refuses_a_symlinked_artifact(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "verifier.json").write_text("{}", encoding="utf-8")
    publish_current_control(
        reports,
        operation="scan",
        control=AgentActionRequiredCurrentControl(
            state="agent_action_required", reason="Scan only."
        ),
    )
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (reports / "verifier.json").unlink()
    os.symlink(outside, reports / "verifier.json")

    with pytest.raises(CurrentControlUnavailable) as raised:
        read_current_control(reports)
    assert raised.value.reason == "artifact_unreadable"


def test_publish_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    begin_current_control(reports, operation="scan", reason="Starting.")
    assert [path.name for path in reports.iterdir()] == [CURRENT_CONTROL_ARTIFACT_NAME]


# ---------------------------------------------------------------------------
# Structural invariants
# ---------------------------------------------------------------------------


def _artifact_ref(name: str = "verifier.json") -> CurrentControlArtifactRef:
    return CurrentControlArtifactRef(path=name, sha256="sha256:" + "a" * 64, size_bytes=1)


def _pointer_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "current_control_id": "sha256:" + "0" * 64,
        "operation": "verify",
        "lifecycle_state": "terminal",
        "workspace_identity": CurrentControlWorkspaceIdentity(),
        "control": CompleteCurrentControl(state="complete", reason="Release ready."),
        "artifacts": {"verification_receipt": _artifact_ref("verification-receipt.json")},
        "request_id": "sha256:" + "1" * 64,
        "decision_id": "sha256:" + "2" * 64,
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("operation", ["scan", "preview"])
def test_only_verify_can_publish_completion_authority(operation: str) -> None:
    with pytest.raises(ValueError, match="only verify"):
        CurrentControlPointer(**_pointer_payload(operation=operation))


def test_completion_authority_requires_a_bound_receipt() -> None:
    with pytest.raises(ValueError, match="terminal receipt"):
        CurrentControlPointer(**_pointer_payload(artifacts={"verifier": _artifact_ref()}))


def test_completion_authority_requires_a_bound_request_and_decision() -> None:
    with pytest.raises(ValueError, match="bound request and decision"):
        CurrentControlPointer(**_pointer_payload(request_id=None))


def test_an_in_progress_pointer_cannot_carry_a_settled_decision() -> None:
    with pytest.raises(ValueError, match="deny every cached decision"):
        CurrentControlPointer(**_pointer_payload(lifecycle_state="in_progress"))


def test_a_terminal_pointer_must_bind_something() -> None:
    with pytest.raises(ValueError, match="at least one artifact"):
        CurrentControlPointer(
            **_pointer_payload(
                artifacts={},
                control=AgentActionRequiredCurrentControl(
                    state="agent_action_required", reason="Run verify."
                ),
                request_id=None,
                decision_id=None,
            )
        )


@pytest.mark.parametrize("path", ["../escape.json", "/absolute.json", "a/../b.json"])
def test_bound_artifact_paths_stay_portable_and_contained(path: str) -> None:
    with pytest.raises(ValueError, match="portable relative paths"):
        CurrentControlArtifactRef(path=path, sha256="sha256:" + "a" * 64, size_bytes=1)


def test_a_pointer_cannot_be_hand_edited_without_breaking_its_identity() -> None:
    """Every field the pointer asserts is inside `current_control_id`.

    ``_pointer_payload`` is otherwise valid, so the only thing left to reject
    is its placeholder identity.
    """

    with pytest.raises(ValueError, match="current_control_id must hash"):
        CurrentControlPointer(**_pointer_payload())


# ---------------------------------------------------------------------------
# The reader command
# ---------------------------------------------------------------------------


def test_agent_control_command_prints_the_validated_pointer(repo: Path) -> None:
    """The pointer itself remains reachable, under ``--format pointer``.

    Contract v22 made the compact control envelope this command's default
    output; the underlying artifact is what this test is about, so it asks for
    it explicitly.
    """

    _verify(repo)
    result = runner.invoke(
        app,
        [
            "agent",
            "control",
            "--workspace",
            str(repo),
            "--reports-dir",
            str(repo / "agents-shipgate-reports"),
            "--format",
            "pointer",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == "shipgate.current_control/v1"
    assert payload["control"]["state"] == "complete"


def test_agent_control_ignores_its_own_output_directory(tmp_path: Path) -> None:
    """The run's own artifacts are not part of the change it evaluated.

    A workspace that does not gitignore the reports directory would otherwise
    report every generated file as an uncommitted change the decision never saw,
    and every refresh would refuse.
    """

    workspace = tmp_path / "repo"
    workspace.mkdir()
    for name in ("shipgate.yaml", "tools.json"):
        shutil.copy(SAMPLE / name, workspace / name)
    _git(workspace, "init", "-q", "-b", "main")
    _git(workspace, "config", "user.email", "test@example.test")
    _git(workspace, "config", "user.name", "Test User")
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-m", "fixture without a reports gitignore")

    _verify(workspace, archive_head=False)
    assert not (workspace / ".gitignore").exists()

    result = runner.invoke(
        app,
        [
            "agent",
            "control",
            "--workspace",
            str(workspace),
            "--reports-dir",
            str(workspace / "agents-shipgate-reports"),
        ],
    )

    assert result.exit_code == 0, result.output


def test_agent_control_command_fails_closed_when_nothing_is_current(tmp_path: Path) -> None:
    empty = tmp_path / "reports"
    empty.mkdir()
    result = runner.invoke(app, ["agent", "control", "--reports-dir", str(empty)])

    assert result.exit_code == 3
    assert "unavailable" in result.output


def test_agent_control_command_fails_closed_on_workspace_drift(repo: Path) -> None:
    """Drift is a currency failure, not a parse failure, so it exits 4."""

    _verify(repo)
    _git(repo, "commit", "--allow-empty", "-m", "an unrelated commit")

    result = runner.invoke(
        app,
        [
            "agent",
            "control",
            "--workspace",
            str(repo),
            "--reports-dir",
            str(repo / "agents-shipgate-reports"),
        ],
    )

    assert result.exit_code == 4
    assert "workspace_changed" in result.output


def test_scan_publishes_its_own_pointer_only_when_it_owns_the_lifecycle(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify's internal head scan must not claim the PR's control identity."""

    operations: list[str] = []
    real_publish = scan_writing.publish_current_control

    def record(out_dir: Path, **kwargs: object):
        operations.append(str(kwargs["operation"]))
        return real_publish(out_dir, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(scan_writing, "publish_current_control", record)
    _verify(repo)
    assert operations == []

    run_scan(
        config_path=repo / "shipgate.yaml",
        output_dir=repo / "agents-shipgate-reports",
        ci_mode="advisory",
        plugins_enabled=False,
    )
    assert operations == ["scan"]
