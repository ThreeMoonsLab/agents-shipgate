from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

from agents_shipgate import __version__
from agents_shipgate.checks.verify import PROTECTED_FILE_EDITS
from agents_shipgate.ci.release_decision import SUGGESTED_DECLARATIONS_FILENAME
from agents_shipgate.cli._artifact_lifecycle import clear_verifier_route_artifacts
from agents_shipgate.cli._helpers import _apply_strict_plugins
from agents_shipgate.cli.scan.orchestrator import run_scan
from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.core.agent_handoff import build_agent_handoff
from agents_shipgate.core.authorization_execution import (
    authorization_execute_command,
    ensure_authorization_runtime_is_external,
)
from agents_shipgate.core.capability_lock import (
    DEFAULT_CAPABILITY_LOCK_PATH,
    diff_capability_locks,
    load_capability_lock_json,
    render_capability_lock_diff_json,
    render_capability_lock_json,
)
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError
from agents_shipgate.core.evaluation_clock import use_evaluation_date
from agents_shipgate.core.human_authorization import (
    default_human_authorization_trust_policy_path,
    evaluate_human_authorization,
)
from agents_shipgate.core.static_inputs import (
    StaticInputSnapshot,
    activate_static_input_snapshot,
    active_static_input_snapshot,
    read_static_input_bytes,
    reset_static_input_snapshot,
)
from agents_shipgate.core.trust_roots import (
    inspect_lexical_path_identity,
    is_configured_manifest,
    read_identity_bound_text,
)
from agents_shipgate.core.verification_identity import (
    build_executor,
    build_terminal_receipt,
    build_unit_result,
    build_verification_plan,
)
from agents_shipgate.packet.json_packet import load_packet_json, write_packet_json
from agents_shipgate.report.capability_lock_diff_markdown import (
    render_capability_lock_diff_markdown,
)
from agents_shipgate.report.json_report import report_json_payload
from agents_shipgate.report.pr_comment import render_pr_comment
from agents_shipgate.schemas.agent_control import (
    AgentControl,
    AgentControlAction,
    CodingAgentCommandAction,
    CodingAgentFetchBaseAction,
    HumanControlAction,
)
from agents_shipgate.schemas.capabilities import CapabilityLockDiffV1, CapabilityLockFileV1
from agents_shipgate.schemas.human_authorization import (
    AuthorizationEvaluationV1,
    HumanAuthorizationV1,
    authorization_review_items,
    build_human_authorization_request,
)
from agents_shipgate.schemas.report import ReadinessReport, ReleaseDecision
from agents_shipgate.schemas.verification import VerificationContext
from agents_shipgate.schemas.verification_identity import VerificationPlan, content_id
from agents_shipgate.schemas.verifier import (
    MergeVerdict,
    VerifierArtifact,
    VerifierBaseStatus,
    VerifierCapabilityReview,
    VerifierFixTask,
    applicability_for,
    merge_verdict_for,
)
from agents_shipgate.schemas.verify_run import (
    VerifyRunArtifactRef,
    VerifyRunOutcome,
    build_verify_run_artifact,
)
from agents_shipgate.triggers import evaluate

from .capability_review import build_capability_review
from .fix_task import FORBIDDEN_SHORTCUTS, build_fix_task, is_pure_adoption_review
from .git import (
    active_replace_refs,
    archive_tree,
    carries_manifest_like_yaml,
    commit_date,
    commit_sha,
    detect_default_base_with_notes,
    diff_context,
    ensure_git_workspace,
    git_path,
    merge_base_sha,
    read_file_at_ref,
    ref_exists,
    removes_a_yaml_file,
    repository_identity,
    resolve_source_head_identity,
    resolve_tree_path_identity,
    tree_sha,
    working_tree_context,
)

HEAD_FORMATS = ["markdown", "json", "sarif"]
# Verify owns the PR artifact contract and writes packet.json only; the
# reviewer-facing Markdown surface is pr-comment.md.
HEAD_PACKET_FORMATS = ["json"]
DEFAULT_OUT_DIR = Path("agents-shipgate-reports")
BASE_CACHE_KEEP_ENTRIES = 16
# Cache-key epoch for base-scan reuse.
#
# A cached base report is admitted on a content hash alone: that proves the
# file was not tampered with, never that its CONTENTS still mean what the
# current CLI expects of them. ``__version__`` is in the key but is not
# sufficient on its own — a source checkout, an editable install, or any two
# builds sharing one pre-release version string can change what a field means
# without moving the version, and the stale entry is then reused verbatim.
#
# Bump this whenever the MEANING of anything inside a cached base report
# changes. Pre-existing entries then land on a key nothing computes and are
# never read again; the next run does a fresh base scan. Bumping is cheap —
# the cost is one re-scan per base tree — so prefer it to reasoning about
# whether a given change is observable through the cache.
#
# 3 — ``effective_policy`` records the manifest's DECLARED ci block rather
#     than the base scan's forced ``ci_mode="advisory"`` (#298). Entries
#     written before that read back as advisory for every base, which is
#     exactly the state in which ``ci_mode_weakened`` cannot fire.
BASE_CACHE_KEY_EPOCH = 3
MAX_HUMAN_AUTHORIZATION_BYTES = 1024 * 1024
MAX_WORKTREE_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_WORKTREE_CHANGED_FILE_BYTES = 64 * 1024 * 1024


def run_verify(
    *,
    workspace: Path,
    config: Path,
    base: str | None,
    head: str,
    archive_head: bool,
    out: Path | None,
    ci_mode: str | None,
    fail_on: list[str] | None,
    baseline: Path | None,
    baseline_mode: str,
    diff_from: Path | None,
    policy_packs: list[Path] | None,
    plugins_enabled: bool | None,
    strict_plugins: bool,
    suggest_patches: bool,
    no_heuristics: bool,
    verbose: bool,
    pr_comment_style: str = "capability-review",
    auto_base: bool = False,
    authorization: Path | None = None,
) -> tuple[VerifierArtifact, ReadinessReport | None, int]:
    git_root = ensure_git_workspace(workspace.resolve())
    config_path, config_relative = _resolve_config_under_workspace(
        git_root,
        config,
        requested_workspace=workspace,
    )
    out_dir = _resolve_under_workspace(git_root, out or DEFAULT_OUT_DIR)
    baseline_path = (
        _resolve_static_input_path(
            git_root,
            baseline,
            label="--baseline",
        )
        if baseline
        else None
    )
    policy_pack_paths = (
        [
            _resolve_static_input_path(
                git_root,
                path,
                label="--policy-pack",
            )
            for path in policy_packs
        ]
        if policy_packs
        else None
    )
    static_diff_from_path = (
        _resolve_static_input_path(
            git_root,
            diff_from,
            label="--diff-from",
        )
        if diff_from is not None
        else None
    )
    _reject_output_input_overlap(
        git_root=git_root,
        out_dir=out_dir,
        inputs=[
            ("config", config_path),
            *([("baseline", baseline_path)] if baseline_path is not None else []),
            *[("policy pack", path) for path in (policy_pack_paths or [])],
            *(
                [("diff-from", static_diff_from_path)]
                if static_diff_from_path is not None
                else []
            ),
        ],
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    clear_verifier_route_artifacts(out_dir)
    verifier_path = out_dir / "verifier.json"
    verify_run_path = out_dir / "verify-run.json"
    pr_comment_path = out_dir / "pr-comment.md"

    rerun_options = _rerun_options(
        git_root=git_root,
        out_dir=out_dir,
        pr_comment_style=pr_comment_style,
        base=base,
        auto_base=auto_base,
        ci_mode=ci_mode,
        fail_on=fail_on,
        baseline_path=baseline_path,
        baseline_mode=baseline_mode,
        diff_from=diff_from,
        policy_pack_paths=policy_pack_paths,
        plugins_enabled=plugins_enabled,
        strict_plugins=strict_plugins,
        suggest_patches=suggest_patches,
        no_heuristics=no_heuristics,
        authorization=authorization,
    )

    if not config_path.is_file():
        preview_command = _preview_verify_command(
            workspace=git_root,
            config=config_relative,
            base=base,
            head=head if archive_head else None,
            out=out,
            pr_comment_style=pr_comment_style,
            preview=True,
        )
        trigger = evaluate(
            paths=[],
            diff_text="",
            manifest_present=False,
            user_requested=True,
        )
        message = (
            f"Shipgate config not found at {_display_path(config_path, git_root)}. "
            "Correct --config, or run `agents-shipgate verify --preview --json` before "
            "initializing."
        )
        verifier = _build_verifier(
            git_root=git_root,
            config_path=config_path,
            base=base,
            head=head,
            changed_files=[],
            diff_text="",
            trigger=trigger,
            base_status="not_requested",
            base_tree=None,
            base_report=None,
            base_notes=[],
            report=None,
            head_status="failed",
            head_exit_code=2,
            out_dir=out_dir,
            ci_mode=ci_mode,
            headline_override=message,
            first_next_action_override=CodingAgentCommandAction(
                kind="configure",
                command=preview_command,
                why=(
                    "Shipgate could not find the configured manifest; run verify "
                    "preview, then correct --config or initialize the intended "
                    "manifest."
                ),
            ),
            worktree=not archive_head,
            rerun_options=rerun_options,
        )
        _remove_scan_artifacts(out_dir)
        _write_artifacts(
            verifier,
            verifier_path,
            verify_run_path,
            pr_comment_path,
            report=None,
            git_root=git_root,
            config_path=config_path,
            config_logical_path=config_relative.as_posix(),
            baseline_path=baseline_path,
            policy_pack_paths=policy_pack_paths or [],
            plugins_enabled=plugins_enabled,
            no_heuristics=no_heuristics,
            fail_on=fail_on,
            pr_comment_style=pr_comment_style,
        )
        return verifier, None, 2

    changed_files: list[str] = []
    diff_text = ""
    base_status: VerifierBaseStatus = "not_requested"
    base_tree: str | None = None
    base_report: Path | None = None
    base_capability_lock: CapabilityLockFileV1 | None = None
    base_notes: list[str] = []
    diff_unavailable = False
    diff_failure_action: HumanControlAction | None = None

    head_exists = ref_exists(git_root, head)
    if not head_exists:
        trigger = evaluate(
            paths=[],
            diff_text="",
            manifest_present=True,
            user_requested=True,
        )
        message = f"Head ref does not exist locally: {head}"
        verifier = _build_verifier(
            git_root=git_root,
            config_path=config_path,
            base=base,
            head=head,
            changed_files=[],
            diff_text="",
            trigger=trigger,
            base_status="ref_missing",
            base_tree=None,
            base_report=None,
            base_notes=[message],
            report=None,
            head_status="failed",
            head_exit_code=2,
            out_dir=out_dir,
            ci_mode=ci_mode,
            headline_override=message,
            first_next_action_override=CodingAgentFetchBaseAction(
                kind="fetch_base",
                expects=head,
                why="Make the requested head ref available locally, then rerun verify.",
            ),
            worktree=not archive_head,
            rerun_options=rerun_options,
        )
        _remove_scan_artifacts(out_dir)
        _write_artifacts(
            verifier,
            verifier_path,
            verify_run_path,
            pr_comment_path,
            report=None,
            git_root=git_root,
            config_path=config_path,
            config_logical_path=config_relative.as_posix(),
            baseline_path=baseline_path,
            policy_pack_paths=policy_pack_paths or [],
            plugins_enabled=plugins_enabled,
            no_heuristics=no_heuristics,
            fail_on=fail_on,
            pr_comment_style=pr_comment_style,
        )
        return verifier, None, 2

    tree_config_relative = resolve_tree_path_identity(
        git_root,
        head,
        config_relative,
    )
    if tree_config_relative is not None:
        config_relative = tree_config_relative

    worktree_manifest_text: str | None = None
    if not archive_head:
        try:
            worktree_manifest_text = read_identity_bound_text(
                git_root,
                config_relative,
                max_bytes=MAX_WORKTREE_MANIFEST_BYTES,
            )
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise ConfigError(
                f"Configured manifest {config_relative.as_posix()} could not be "
                f"captured as one identity-bound worktree input: {exc}"
            ) from exc

    verification_date = commit_date(git_root, head)

    if base is None and auto_base:
        detection = detect_default_base_with_notes(git_root, head)
        base_notes.extend(detection.notes)
        if detection.base is not None:
            base = detection.base
            base_notes.append(
                f"Auto-detected base {detection.base!r} for diff context; "
                "pass --base to override or --no-base to disable."
            )

    base_exists = False
    if base:
        base_exists = ref_exists(git_root, base)
        if base_exists:
            try:
                changed_files, diff_text = diff_context(git_root, base, head)
            except Exception as exc:  # noqa: BLE001 - diff context degrades only.
                diff_unavailable = True
                base_status = "archive_failed"
                detail = f"Could not collect diff for {base}...{head}: {exc}"
                base_notes.append(detail)
                diff_failure_action = HumanControlAction(
                    kind="review",
                    why=(
                        f"{detail}. The refs are present; fetching cannot repair "
                        "this deterministic input failure. Inspect the reported "
                        "Git configuration/resource issue before rerunning."
                    ),
                )
        else:
            diff_unavailable = True
            base_status = "ref_missing"
            base_notes.append(
                f"Base ref {base!r} is not available locally; run with fetch-depth: 0 "
                "or fetch the base before verify."
            )

    if not archive_head:
        try:
            worktree_paths, worktree_diff = working_tree_context(
                git_root,
                exclude=out_dir,
                reject_index_hidden=True,
            )
            changed_files = _dedupe_paths([*changed_files, *worktree_paths])
            diff_text = _join_diff_text(diff_text, worktree_diff)
            changed_files = _bind_worktree_config_to_head(
                git_root=git_root,
                head=head,
                config_relative=config_relative,
                worktree_text=worktree_manifest_text,
                changed_files=changed_files,
            )
        except Exception as exc:  # noqa: BLE001 - local context degrades only.
            diff_unavailable = True
            detail = f"Could not collect working-tree diff context: {exc}"
            base_notes.append(detail)
            diff_failure_action = HumanControlAction(
                kind="review",
                why=(
                    f"{detail}. Inspect the deterministic worktree-input "
                    "failure before rerunning; fetching refs cannot repair it."
                ),
            )

    trigger = evaluate(
        paths=changed_files,
        diff_text=diff_text,
        manifest_present=config_path.exists(),
        # Running verify is itself an explicit Shipgate request; this keeps
        # trigger stop-conditions from treating the canonical PR command as
        # passive repo discovery.
        user_requested=True,
    )
    verifier = _build_verifier(
        git_root=git_root,
        config_path=config_path,
        base=base,
        head=head,
        changed_files=changed_files,
        diff_text=diff_text,
        trigger=trigger,
        base_status=base_status,
        base_tree=base_tree,
        base_report=base_report,
        base_notes=base_notes,
        report=None,
        head_status="skipped",
        head_exit_code=0,
        out_dir=out_dir,
        ci_mode=ci_mode,
        worktree=not archive_head,
        rerun_options=rerun_options,
    )

    if diff_unavailable:
        verifier = _build_verifier(
            git_root=git_root,
            config_path=config_path,
            base=base,
            head=head,
            changed_files=changed_files,
            diff_text=diff_text,
            trigger=trigger,
            base_status=base_status,
            base_tree=base_tree,
            base_report=base_report,
            base_notes=base_notes,
            report=None,
            head_status="failed",
            head_exit_code=2,
            out_dir=out_dir,
            ci_mode=ci_mode,
            first_next_action_override=diff_failure_action,
            worktree=not archive_head,
            rerun_options=rerun_options,
        )
        _write_artifacts(
            verifier,
            verifier_path,
            verify_run_path,
            pr_comment_path,
            report=None,
            git_root=git_root,
            config_path=config_path,
            config_logical_path=config_relative.as_posix(),
            baseline_path=baseline_path,
            policy_pack_paths=policy_pack_paths or [],
            plugins_enabled=plugins_enabled,
            no_heuristics=no_heuristics,
            fail_on=fail_on,
            pr_comment_style=pr_comment_style,
        )
        return verifier, None, 2

    if not trigger.get("run_shipgate"):
        if base and base_status == "not_requested":
            base_status = "skipped"
            verifier = _build_verifier(
                git_root=git_root,
                config_path=config_path,
                base=base,
                head=head,
                changed_files=changed_files,
                diff_text=diff_text,
                trigger=trigger,
                base_status=base_status,
                base_tree=base_tree,
                base_report=base_report,
                base_notes=base_notes,
                report=None,
                head_status="skipped",
                head_exit_code=0,
                out_dir=out_dir,
                ci_mode=ci_mode,
                worktree=not archive_head,
                rerun_options=rerun_options,
            )
        _write_artifacts(
            verifier,
            verifier_path,
            verify_run_path,
            pr_comment_path,
            report=None,
            git_root=git_root,
            config_path=config_path,
            config_logical_path=config_relative.as_posix(),
            baseline_path=baseline_path,
            policy_pack_paths=policy_pack_paths or [],
            plugins_enabled=plugins_enabled,
            no_heuristics=no_heuristics,
            fail_on=fail_on,
            pr_comment_style=pr_comment_style,
        )
        return verifier, None, 0

    report: ReadinessReport | None = None
    head_status = "failed"
    head_exit_code = 4
    scan_error: BaseException | None = None
    head_tmp: tempfile.TemporaryDirectory[str] | None = None
    head_config_path = config_path
    head_input_root = git_root
    head_baseline_path = baseline_path
    head_policy_pack_paths = policy_pack_paths
    head_tree: str | None = None
    head_capability_lock: CapabilityLockFileV1 | None = None
    capability_lock_diff: CapabilityLockDiffV1 | None = None

    def capture_capability_lock(lock: CapabilityLockFileV1) -> None:
        nonlocal head_capability_lock
        head_capability_lock = lock

    external_snapshot_paths = [
        path
        for path in [
            baseline_path,
            *list(policy_pack_paths or []),
            static_diff_from_path,
        ]
        if path is not None
    ]
    static_snapshot = StaticInputSnapshot(
        git_root,
        external_paths=external_snapshot_paths,
        excluded_paths=[out_dir],
    )
    if not archive_head:
        assert worktree_manifest_text is not None
        static_snapshot.preload(
            config_path,
            worktree_manifest_text.encode("utf-8"),
        )
        for relative in changed_files:
            candidate = Path(
                os.path.abspath(
                    os.path.normpath(os.fspath(git_root / relative))
                )
            )
            try:
                metadata = candidate.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise ConfigError(
                    f"Changed worktree input {relative!r} could not be inspected: {exc}"
                ) from exc
            if not stat.S_ISREG(metadata.st_mode):
                continue
            try:
                static_snapshot.read_bytes(
                    candidate,
                    max_bytes=MAX_WORKTREE_CHANGED_FILE_BYTES,
                )
            except (OSError, ValueError) as exc:
                raise ConfigError(
                    f"Changed worktree input {relative!r} could not be captured "
                    f"for verification: {exc}"
                ) from exc
    static_snapshot_token = activate_static_input_snapshot(static_snapshot)

    try:
        if diff_from is not None:
            base_status = "diff_from_provided"
            assert static_diff_from_path is not None
            base_report = static_diff_from_path
            base_notes.append(
                f"Using explicit diff reference: {_display_path(base_report, git_root)}"
            )
        elif base and base_exists:
            (
                base_status,
                base_tree,
                base_report,
                base_capability_lock,
                cache_notes,
            ) = _prepare_base_report(
                git_root=git_root,
                base=base,
                config_relative=config_relative,
                baseline_path=baseline_path,
                policy_packs=policy_pack_paths or [],
                plugins_enabled=plugins_enabled,
                no_heuristics=no_heuristics,
                verbose=verbose,
                evaluation_date=verification_date,
            )
            base_notes.extend(cache_notes)

        manifest_introduced = _manifest_introduced(
            git_root=git_root,
            config_relative=config_relative,
            base_status=base_status,
            base=base,
            head=head,
            changed_files=changed_files,
        )
    except Exception:
        reset_static_input_snapshot(static_snapshot_token)
        raise

    try:
        if archive_head:
            head_tmp = tempfile.TemporaryDirectory(prefix="agents-shipgate-verify-head-")
            head_tree_dir = Path(head_tmp.name) / "head"
            archive_tree(git_root, head, head_tree_dir)
            head_input_root = head_tree_dir
            head_tree = tree_sha(git_root, head)
            head_config_path = head_tree_dir / config_relative
            _reject_symlink_components(
                head_tree_dir,
                config_relative,
                label=f"Head manifest {config_relative.as_posix()}",
                allow_filesystem_alias=True,
            )
            if not head_config_path.is_file():
                raise ConfigError(
                    f"Head tree {head!r} does not contain {config_relative.as_posix()}."
                )
            head_policy_pack_paths = _map_policy_packs(
                git_root=git_root,
                tree_dir=head_tree_dir,
                policy_packs=policy_pack_paths or [],
            )
            head_baseline_path = _map_optional_tree_path(
                git_root=git_root,
                tree_dir=head_tree_dir,
                path=baseline_path,
            )
        with use_evaluation_date(date.fromisoformat(verification_date)):
            report, head_exit_code = run_scan(
                config_path=head_config_path,
                output_dir=out_dir,
                formats=HEAD_FORMATS,
                ci_mode=ci_mode,
                fail_on=fail_on,
                baseline_path=head_baseline_path,
                diff_from_path=base_report,
                baseline_mode=baseline_mode,
                policy_pack_paths=head_policy_pack_paths,
                plugins_enabled=plugins_enabled,
                verbose=verbose,
                suggest_patches=suggest_patches,
                packet_enabled=True,
                packet_formats=HEAD_PACKET_FORMATS,
                no_heuristics=no_heuristics,
                verification_context=VerificationContext(
                    changed_files=changed_files,
                    diff_text=diff_text,
                    diff_text_available=bool(diff_text),
                    trigger_result=trigger,
                    configured_manifest_path=config_relative.as_posix(),
                    manifest_introduced=manifest_introduced,
                ),
                capability_lock_callback=capture_capability_lock,
                manifest_text=worktree_manifest_text if not archive_head else None,
            )
        head_exit_code = _apply_strict_plugins(
            report, head_exit_code, strict_plugins=strict_plugins
        )
        if archive_head:
            _project_archived_report_paths(
                report,
                archived_config=head_config_path,
                checkout_config=config_path,
            )
        head_status = "succeeded"
        if head_capability_lock is not None:
            try:
                capability_lock_diff = _write_capability_review_artifacts(
                    git_root=git_root,
                    out_dir=out_dir,
                    base=base,
                    base_lock=base_capability_lock,
                    head_lock=head_capability_lock,
                    base_notes=base_notes,
                )
            except Exception as exc:  # noqa: BLE001 - review artifacts never gate.
                base_notes.append(f"Capability review artifacts unavailable: {exc}")
    except ConfigError as exc:
        scan_error = exc
        head_exit_code = 2
        raise
    except InputParseError as exc:
        scan_error = exc
        head_exit_code = 3
        raise
    except AgentsShipgateError as exc:
        scan_error = exc
        head_exit_code = 4
        raise
    except Exception as exc:
        scan_error = exc
        head_exit_code = 4
        raise
    finally:
        artifact_report = report if head_status == "succeeded" else None
        if artifact_report is None:
            _remove_scan_artifacts(out_dir)
        verifier = _build_verifier(
            git_root=git_root,
            config_path=config_path,
            base=base,
            head=head,
            changed_files=changed_files,
            diff_text=diff_text,
            trigger=trigger,
            base_status=base_status,
            base_tree=base_tree,
            head_tree=head_tree,
            base_report=base_report,
            base_notes=base_notes,
            report=artifact_report,
            head_status=head_status,
            head_exit_code=head_exit_code,
            out_dir=out_dir,
            ci_mode=ci_mode,
            manifest_introduced=manifest_introduced,
            worktree=not archive_head,
            rerun_options=rerun_options,
        )
        try:
            try:
                _write_artifacts(
                    verifier,
                    verifier_path,
                    verify_run_path,
                    pr_comment_path,
                    report=artifact_report,
                    git_root=git_root,
                    config_path=head_config_path,
                    config_logical_path=config_relative.as_posix(),
                    baseline_path=head_baseline_path,
                    policy_pack_paths=head_policy_pack_paths or [],
                    plugins_enabled=plugins_enabled,
                    no_heuristics=no_heuristics,
                    fail_on=fail_on,
                    pr_comment_style=pr_comment_style,
                    capability_lock_diff=capability_lock_diff,
                    input_root=head_input_root,
                    diff_text=diff_text,
                    diff_from_path=base_report,
                    authorization_path=authorization,
                    verification_options={
                        "archive_head": archive_head,
                        "baseline_mode": baseline_mode,
                        "strict_plugins": strict_plugins,
                        "suggest_patches": suggest_patches,
                        "evaluated_head_commit_sha": (os.getenv("EVALUATED_HEAD_SHA") or None),
                        "github_actions": os.getenv("GITHUB_ACTIONS") == "true",
                        "github_event_name": (
                            os.getenv("EVENT_NAME") or os.getenv("GITHUB_EVENT_NAME") or None
                        ),
                    },
                    evaluation_date=verification_date,
                )
            except Exception:
                if scan_error is None:
                    raise
                # Preserve the scan's typed error and exit code. Artifact write
                # failures are secondary once the head scan has already failed.
                pass
        finally:
            if head_tmp is not None:
                head_tmp.cleanup()
            if static_snapshot_token is not None:
                reset_static_input_snapshot(static_snapshot_token)
    return verifier, report, head_exit_code


def _prepare_base_report(
    *,
    git_root: Path,
    base: str,
    config_relative: Path,
    baseline_path: Path | None,
    policy_packs: list[Path],
    plugins_enabled: bool | None,
    no_heuristics: bool,
    verbose: bool,
    evaluation_date: str,
) -> tuple[
    VerifierBaseStatus,
    str | None,
    Path | None,
    CapabilityLockFileV1 | None,
    list[str],
]:
    notes: list[str] = []
    try:
        base_tree = tree_sha(git_root, base)
    except Exception as exc:  # noqa: BLE001 - optional base enrichment.
        return "archive_failed", None, None, None, [f"Could not resolve base tree: {exc}"]

    cache_report = _cache_report_path(
        base_tree=base_tree,
        config_relative=config_relative,
        git_root=git_root,
        baseline_path=baseline_path,
        policy_packs=policy_packs,
        plugins_enabled=plugins_enabled,
        no_heuristics=no_heuristics,
        evaluation_date=evaluation_date,
    )
    if cache_report.exists() and _cache_report_valid(cache_report):
        base_lock, lock_notes = _load_cached_capability_lock(cache_report)
        return (
            "succeeded",
            base_tree,
            cache_report,
            base_lock,
            [f"Base report resolved for tree {base_tree}.", *lock_notes],
        )
    if cache_report.exists():
        notes.append("Discarded base cache entry whose content hash did not validate.")
        cache_report.unlink(missing_ok=True)

    with tempfile.TemporaryDirectory(prefix="agents-shipgate-verify-") as tmp:
        tmp_root = Path(tmp)
        base_tree_dir = tmp_root / "base"
        base_out = tmp_root / "reports"
        try:
            archive_tree(git_root, base, base_tree_dir)
        except Exception as exc:  # noqa: BLE001 - optional base enrichment.
            return (
                "archive_failed",
                base_tree,
                None,
                None,
                [
                    f"Could not materialize base tree {base!r}: "
                    f"{_stable_archive_error(exc, archive_root=tmp_root, label='base tree')}"
                ],
            )

        base_config = base_tree_dir / config_relative
        try:
            _reject_symlink_components(
                base_tree_dir,
                config_relative,
                label=f"Base manifest {config_relative.as_posix()}",
                allow_filesystem_alias=True,
            )
        except ConfigError as exc:
            return (
                "scan_failed",
                base_tree,
                None,
                None,
                [str(exc)],
            )
        if not base_config.is_file():
            return (
                "missing_manifest",
                base_tree,
                None,
                None,
                [f"Base tree does not contain {config_relative.as_posix()}."],
            )

        base_capability_lock: CapabilityLockFileV1 | None = None

        def capture_base_capability_lock(lock: CapabilityLockFileV1) -> None:
            nonlocal base_capability_lock
            base_capability_lock = lock

        try:
            with (
                _without_github_step_summary(),
                use_evaluation_date(date.fromisoformat(evaluation_date)),
            ):
                base_report_model, base_exit = run_scan(
                    config_path=base_config,
                    output_dir=base_out,
                    formats=["json"],
                    ci_mode="advisory",
                    fail_on=None,
                    baseline_path=_map_optional_tree_path(
                        git_root=git_root,
                        tree_dir=base_tree_dir,
                        path=baseline_path,
                    ),
                    diff_from_path=None,
                    baseline_mode="new-findings",
                    policy_pack_paths=_map_policy_packs(
                        git_root=git_root,
                        tree_dir=base_tree_dir,
                        policy_packs=policy_packs,
                    ),
                    plugins_enabled=plugins_enabled,
                    verbose=verbose,
                    suggest_patches=False,
                    packet_enabled=False,
                    packet_formats=None,
                    no_heuristics=no_heuristics,
                    capability_lock_callback=capture_base_capability_lock,
                )
        except Exception as exc:  # noqa: BLE001 - optional base enrichment.
            return (
                "scan_failed",
                base_tree,
                None,
                None,
                [
                    "Base scan failed without changing the head gate: "
                    f"{_stable_archive_error(exc, archive_root=tmp_root, label='base tree')}"
                ],
            )
        if base_exit not in {0, 20}:
            return (
                "scan_failed",
                base_tree,
                None,
                None,
                [f"Base scan exited {base_exit}; diff enrichment disabled."],
            )
        source_report = base_out / "report.json"
        if not source_report.is_file():
            return (
                "scan_failed",
                base_tree,
                None,
                None,
                ["Base scan did not produce report.json; diff enrichment disabled."],
            )
        # Base diff evidence is never patch-applicable. Strip checkout/output
        # coordinates so identical commits produce identical cached inputs
        # across worktrees and clones.
        base_report_model.manifest_dir = None
        base_report_model.generated_reports = {}
        source_report.write_text(
            json.dumps(report_json_payload(base_report_model), indent=2),
            encoding="utf-8",
        )
        _copy_report_to_cache(source_report, cache_report)
        if base_capability_lock is not None:
            _write_capability_lock_to_cache(base_capability_lock, cache_report)
        else:
            notes.append("Base scan did not produce a capability lock; diff artifact disabled.")
        _prune_base_scan_cache(cache_report.parents[1], keep=BASE_CACHE_KEEP_ENTRIES)
        notes.append(f"Base report resolved for tree {base_tree}.")
    return "succeeded", base_tree, cache_report, base_capability_lock, notes


def _cache_report_path(
    *,
    base_tree: str,
    config_relative: Path,
    git_root: Path,
    baseline_path: Path | None,
    policy_packs: list[Path],
    plugins_enabled: bool | None,
    no_heuristics: bool,
    evaluation_date: str,
) -> Path:
    payload = {
        "version": BASE_CACHE_KEY_EPOCH,
        "agents_shipgate_version": __version__,
        "base_tree": base_tree,
        "config": config_relative.as_posix(),
        "baseline": {
            "path": (
                _display_path(baseline_path.resolve(), git_root)
                if baseline_path is not None
                else None
            ),
            "sha256": _static_input_sha256(baseline_path),
        },
        "policy_packs": [
            {
                "path": _display_path(path.resolve(), git_root),
                "sha256": _static_input_sha256(path),
            }
            for path in policy_packs
        ],
        "plugins_enabled": plugins_enabled,
        "no_heuristics": no_heuristics,
        "evaluation_date": evaluation_date,
    }
    key = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return git_path(git_root, f"agents-shipgate/base-scans/{key}/report.json")


def _copy_report_to_cache(source_report: Path, cache_report: Path) -> None:
    cache_report.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=cache_report.parent,
        prefix="report-",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
    try:
        shutil.copy2(source_report, temp_path)
        temp_path.replace(cache_report)
        cache_report.with_suffix(".sha256").write_text(
            f"{_sha256_file(cache_report)}\n",
            encoding="ascii",
        )
    finally:
        temp_path.unlink(missing_ok=True)


def _cache_report_valid(cache_report: Path) -> bool:
    digest_path = cache_report.with_suffix(".sha256")
    if not digest_path.is_file():
        return False
    try:
        expected = digest_path.read_text(encoding="ascii").strip()
    except OSError:
        return False
    return bool(expected and expected == _sha256_file(cache_report))


def _base_capability_lock_cache_path(cache_report: Path) -> Path:
    return cache_report.with_name("capabilities.lock.json")


def _load_cached_capability_lock(
    cache_report: Path,
) -> tuple[CapabilityLockFileV1 | None, list[str]]:
    cache_lock = _base_capability_lock_cache_path(cache_report)
    if not cache_lock.exists():
        return None, ["Cached base capability lock missing; capability diff may fall back."]
    try:
        return (
            load_capability_lock_json(
                cache_lock.read_text(encoding="utf-8"),
                source=str(cache_lock),
            ),
            [],
        )
    except (OSError, InputParseError) as exc:
        return None, [f"Cached base capability lock invalid; capability diff may fall back: {exc}"]


def _write_capability_lock_to_cache(
    lock: CapabilityLockFileV1,
    cache_report: Path,
) -> None:
    cache_lock = _base_capability_lock_cache_path(cache_report)
    cache_lock.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=cache_lock.parent,
        prefix="capabilities-",
        suffix=".tmp",
        delete=False,
        mode="w",
        encoding="utf-8",
    ) as handle:
        temp_path = Path(handle.name)
        handle.write(render_capability_lock_json(lock))
    try:
        temp_path.replace(cache_lock)
    finally:
        temp_path.unlink(missing_ok=True)


def _prune_base_scan_cache(cache_root: Path, *, keep: int) -> None:
    if keep <= 0 or not cache_root.is_dir():
        return
    entries = [path for path in cache_root.iterdir() if path.is_dir()]
    entries.sort(key=_safe_mtime, reverse=True)
    for stale in entries[keep:]:
        shutil.rmtree(stale, ignore_errors=True)


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _map_policy_packs(
    *,
    git_root: Path,
    tree_dir: Path,
    policy_packs: list[Path],
) -> list[Path] | None:
    if not policy_packs:
        return None
    mapped: list[Path] = []
    for path in policy_packs:
        candidate = path
        try:
            relative = candidate.relative_to(git_root)
        except ValueError:
            mapped.append(candidate)
        else:
            mapped.append(tree_dir / relative)
    return mapped


def _map_optional_tree_path(
    *,
    git_root: Path,
    tree_dir: Path,
    path: Path | None,
) -> Path | None:
    if path is None:
        return None
    candidate = path
    try:
        relative = candidate.relative_to(git_root)
    except ValueError:
        return candidate
    return tree_dir / relative


# File names that count as a Shipgate manifest when deciding whether a ref
# already carries a gate. ``shipgate.yaml`` is the published default; the
# configured name is added at call time so a repository that renamed its
# manifest is still recognized as adopted.
_MANIFEST_FILE_NAMES = frozenset({"shipgate.yaml"})


def _rerun_options(
    *,
    git_root: Path,
    out_dir: Path,
    pr_comment_style: str,
    base: str | None,
    auto_base: bool,
    ci_mode: str | None,
    fail_on: list[str] | None,
    baseline_path: Path | None,
    baseline_mode: str,
    diff_from: Path | None,
    policy_pack_paths: list[Path] | None,
    plugins_enabled: bool | None,
    strict_plugins: bool,
    suggest_patches: bool,
    no_heuristics: bool,
    authorization: Path | None,
) -> list[str]:
    """The rest of this run's request, as flags a rerun must repeat.

    A rerun command that drops the policy packs, baseline, or heuristic mode
    evaluates something other than the run whose findings it is meant to
    reproduce — it can come back clean on inputs the real run never used. The
    base is the subtle one: with no ``--base`` and auto-detection disabled,
    omitting ``--no-base`` lets the rerun auto-detect a branch the evaluated
    run never compared against.
    """

    options: list[str] = []
    # The workspace is unconditional: run from anywhere else and a bare command
    # either fails or evaluates a different checkout.
    options.extend(["--workspace", shlex.quote(str(git_root))])
    if out_dir.resolve() != (git_root / DEFAULT_OUT_DIR).resolve():
        # A non-default artifact directory has to be repeated, or the rerun
        # writes elsewhere and leaves the requested one stale.
        options.extend(["--out", shlex.quote(_display_path(out_dir, git_root))])
    if pr_comment_style and pr_comment_style != "capability-review":
        options.extend(["--pr-comment-style", shlex.quote(pr_comment_style)])
    if base is None and not auto_base:
        options.append("--no-base")
    if ci_mode:
        options.extend(["--ci-mode", shlex.quote(ci_mode)])
    if fail_on:
        options.extend(["--fail-on", shlex.quote(",".join(fail_on))])
    if baseline_path is not None:
        options.extend(["--baseline", shlex.quote(_display_path(baseline_path, git_root))])
    if baseline_mode and baseline_mode != "new-findings":
        options.extend(["--baseline-mode", shlex.quote(baseline_mode)])
    if diff_from is not None:
        options.extend(["--diff-from", shlex.quote(_display_path(diff_from, git_root))])
    for pack in policy_pack_paths or []:
        options.extend(["--policy-pack", shlex.quote(_display_path(pack, git_root))])
    if plugins_enabled is False:
        options.append("--no-plugins")
    if strict_plugins:
        options.append("--strict-plugins")
    if suggest_patches:
        options.append("--suggest-patches")
    if no_heuristics:
        options.append("--no-heuristics")
    if authorization is not None:
        # Authorization grants are intentionally external to the repository.
        # A path relative to the invocation directory cannot be replayed from
        # the workspace embedded above, so serialize its absolute *lexical*
        # identity now. ``abspath`` normalizes ``..`` without following a
        # symlink and silently changing the operator-supplied grant path.
        authorization_path = Path(os.path.abspath(os.fspath(authorization)))
        options.extend(
            ["--authorization", shlex.quote(str(authorization_path))]
        )
    return options


def _manifest_introduced(
    *,
    git_root: Path,
    config_relative: Path,
    base_status: VerifierBaseStatus,
    base: str | None,
    head: str,
    changed_files: list[str],
) -> bool:
    """True when the comparison base carries no Shipgate manifest at all.

    Adoption and modification are different events, and the fail-safe path
    could not tell them apart: a first adoption compares against a base with no
    policy, which is not a base whose policy was weakened. This proves the
    distinction from git rather than inferring it from the diff.

    The proof is deliberately stronger than "the configured path is absent on
    the base". A PR that *moves* the manifest — say to ``config/shipgate.yaml``
    while loosening it — also finds nothing at the configured path on the base,
    and would otherwise get to call itself a first adoption.

    Three independent checks close that, because a name check alone cannot: a
    repository may call its manifest anything, so both ``old-gate.yml`` renamed
    to ``new-gate.yml`` and a base that simply *keeps* ``old-gate.yml`` pass
    every name test. The base must carry no manifest under the configured or
    default name, no tracked file under any name that reads like a manifest at
    all, *and* the evaluated diff must not delete or rename away any YAML file.
    All three are fail-closed: a git command that cannot answer means "not an
    adoption", never "proven absent".

    Unknown bases (``ref_missing``, ``archive_failed``) are never treated as
    adoptions: absence of evidence is not evidence of absence.

    The evaluated diff must also actually contain the manifest. That is the
    literal claim being made ("this PR introduces it"), and it is what makes
    ``trust_root_touched`` structurally true for every adoption — which matters
    because ``policy_weakened`` is honestly ``false`` here, so the trust-root
    signal is the one machine consumers are left with.
    """

    if not any(
        is_configured_manifest(
            config_relative,
            str(path).replace("\\", "/"),
            workspace=git_root,
        )
        for path in changed_files
    ):
        return False
    if base_status == "missing_manifest" and base is not None:
        ref: str = base
    elif base_status in {"not_requested", "skipped"}:
        # No base was compared against, so the manifest's own history is the
        # base: present in the workspace but absent from the head commit means
        # this working tree is introducing it.
        ref = head
    else:
        return False
    # Only canonical default names are meaningful as a name-based safety
    # signal. Treating an arbitrary configured basename as manifest identity
    # would let an unrelated base file such as ``docs/release.gate`` suppress
    # a genuine adoption of ``config/release.gate``. Custom manifests are
    # detected by the suffix-agnostic structural content probe below.
    # One bounded listing performs both the canonical-name guard and the
    # suffix-agnostic structural probe. A separate ``ls-tree --name-only``
    # previously buffered an unbounded tree before this bounded probe ran.
    manifest_like = carries_manifest_like_yaml(
        git_root,
        ref,
        protected_names=_MANIFEST_FILE_NAMES,
    )
    if manifest_like is not False:
        return False
    removed = removes_a_yaml_file(
        git_root, base if base_status == "missing_manifest" else None, head
    )
    return removed is False


def _can_merge_without_human(
    *,
    merge_verdict: MergeVerdict,
    release_decision: ReleaseDecision | None,
    capability_review: VerifierCapabilityReview | None = None,
) -> bool:
    """Pure merge projection; contradictory passed substrate fails closed.

    ``manifest_introduced`` is deliberately *not* consulted. ``VerifierArtifact``
    validates this as a pure projection of ``decision``/``execution``, so a
    third input could only produce an artifact the schema rejects. The adoption
    stays fail-closed through the substrate instead: it always touches the
    manifest — ``_manifest_introduced`` requires the diff to contain it — so
    ``trust_root_touched`` is set, the note below is non-``None``, and a
    ``passed`` decision alongside it is caught as the contradiction it is.
    """

    note = _self_approval_note(capability_review)
    if release_decision is None:
        if merge_verdict == "mergeable" and note:
            raise ValueError("mergeable not-applicable projection contradicts a touched trust root")
        return merge_verdict == "mergeable"
    if release_decision.decision != "passed":
        return False
    contradictions: list[str] = []
    if merge_verdict != "mergeable":
        contradictions.append("merge verdict is not mergeable")
    if note is not None:
        contradictions.append("release trust root or policy was changed")
    if release_decision.evidence_coverage.human_review_recommended:
        contradictions.append("evidence coverage recommends human review")
    if release_decision.evidence_coverage.evidence_gaps:
        contradictions.append("semantic or binding evidence gaps remain")
    if release_decision.blockers or release_decision.review_items:
        contradictions.append("blockers or review items remain")
    if contradictions:
        raise ValueError(
            "release_decision.decision='passed' contradicts its substrate: "
            + "; ".join(contradictions)
        )
    return True


def _self_approval_note(
    capability_review: VerifierCapabilityReview | None,
    *,
    manifest_introduced: bool = False,
    pure_adoption_review: bool = False,
    configured_manifest: str | None = None,
) -> str | None:
    """The explicit self-approval prohibition when this PR edits the rules that
    evaluate it.

    A coding agent must never silently self-approve a change to its own release
    gate (reward hacking). When the head scan flags a weakened policy or a
    touched trust root, that prohibition is surfaced as the verifier headline
    and the human-review reason — not left implicit in a fix_task instruction.

    A first adoption gets its own wording. The prohibition still holds (a
    coding agent cannot adopt a release policy on the repository's behalf), but
    a PR that adds the manifest to a base that had none does not weaken an
    existing Shipgate manifest. An adoption fires on ``manifest_introduced``
    rather than on ``policy_weakened``, which is honestly false during a pure
    adoption — that keeps every caller reading this as "a trust root is in
    play" fail-closed.
    But a diff that introduces the manifest *and* weakens an existing policy
    file is not a pure adoption: ``policy_weakened`` is then true, and saying
    "there is no prior gate this change could weaken" would describe away the
    very finding that needs review. The weakening wording wins.
    """
    if capability_review is None:
        # Without a completed head scan there is no capability review whose
        # trust-root facts can support adoption guidance. Scan-failure routing
        # must remain the authoritative headline and next action.
        return None
    if (
        manifest_introduced
        and pure_adoption_review
        and not capability_review.policy_weakened
    ):
        manifest = (
            f"the configured manifest {configured_manifest!r}"
            if configured_manifest
            else "the configured Shipgate manifest"
        )
        return (
            "This PR introduces Agents Shipgate to this repository. Adopting a "
            f"release policy is a human decision — review {manifest}, then "
            "merge it through a human-reviewed PR."
        )
    if capability_review.policy_weakened:
        return (
            "This PR weakens the release policy that evaluates it; a coding "
            "agent cannot self-approve that change — a human must review it."
        )
    if capability_review.trust_root_touched:
        return (
            "This PR edits a release trust root (the manifest, CI gate, agent "
            "instructions, or trigger catalog used to evaluate it); a coding "
            "agent cannot self-approve that change — a human must review it."
        )
    return None


def _evidence_gap_identities(payload: object) -> Counter[tuple[str, str]] | None:
    """How many gaps of each (kind, subject) a report payload carries."""

    if not isinstance(payload, dict):
        return None
    decision = payload.get("release_decision")
    if not isinstance(decision, dict):
        return None
    coverage = decision.get("evidence_coverage")
    if not isinstance(coverage, dict):
        return None
    gaps = coverage.get("evidence_gaps")
    if not isinstance(gaps, list):
        return None
    return Counter(
        (str(gap.get("kind") or ""), _stable_subject(str(gap.get("subject") or "")))
        for gap in gaps
        if isinstance(gap, dict)
    )


# The base scan runs from a temporary archive, so a subject that embeds a path
# differs between base and head for reasons that have nothing to do with the
# diff. Comparing raw subjects reported an unchanged source warning as new.
_VOLATILE_PATH_RE = re.compile(r"(/[^\s'\"]*)+")


def _stable_subject(subject: str) -> str:
    """A subject with run-specific absolute paths collapsed."""

    return _VOLATILE_PATH_RE.sub(
        lambda match: f"/…/{PurePosixPath(match.group(0)).name}", subject
    )


def _gap_provenance_note(
    *,
    report: ReadinessReport | None,
    base_report: Path | None,
) -> str | None:
    """Say whether THIS diff introduced the evidence gaps, or inherited them.

    An abstention earned by a repository's pre-existing state reads, on a
    docs-only turn, as an accusation about the current change. The verdict is
    unchanged — evidence coverage is a property of the whole evaluated surface,
    and a diff that appears to touch nothing is exactly what an unseeable
    capability change looks like, so the diff can never argue the abstention
    away. What it can do is stop misattributing it.
    """

    if report is None or report.release_decision is None:
        return None
    coverage = report.release_decision.evidence_coverage
    if coverage is None or not coverage.evidence_gaps:
        return None
    head = Counter(
        (str(gap.kind), _stable_subject(str(gap.subject or "")))
        for gap in coverage.evidence_gaps
    )
    if base_report is None or not base_report.is_file():
        return None
    try:
        base = _evidence_gap_identities(
            json.loads(base_report.read_text(encoding="utf-8"))
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if base is None:
        return None
    # Multiset, not set: two gaps sharing a (kind, subject) are two gaps, and
    # collapsing them would report a genuinely new one as inherited.
    introduced = sum((head - base).values())
    total = sum(head.values())
    if introduced:
        return f"{introduced} of {total} evidence gap(s) are new in this diff."
    # Name the scaffold only when one will exist, and only for the gaps it
    # actually covers. Low-confidence and source-warning gaps carry no
    # declaration template, so promising that "a declaration closes them" would
    # be false for any mixed set.
    scaffolded = sum(
        1
        for gap in coverage.evidence_gaps
        if getattr(gap.next_action, "declaration_template", None)
    )
    if scaffolded:
        subset = "all of them" if scaffolded == total else f"{scaffolded} of them"
        remedy = (
            f" A one-time human declaration closes {subset} "
            f"({SUGGESTED_DECLARATIONS_FILENAME})."
        )
    else:
        remedy = ""
    return (
        f"This diff introduces no new evidence gap; all {total} are "
        f"pre-existing on the base.{remedy}"
    )


def _verifier_headline(
    *,
    report: ReadinessReport | None,
    merge_verdict: MergeVerdict,
    head_status: str,
    capability_review: VerifierCapabilityReview | None = None,
    manifest_introduced: bool = False,
    pure_adoption_review: bool = False,
    configured_manifest: str | None = None,
) -> str | None:
    # A failed scan has no adoption evidence to act on. Lead with the failure,
    # even if the pre-scan git proof found a newly added manifest.
    if head_status == "failed" or merge_verdict == "unknown":
        return "Shipgate could not complete the scan; human review required."
    # An adoption with another gating concern must lead with that real stop
    # condition. "Review, then merge" is only truthful when the adoption
    # finding is the sole review item.
    if manifest_introduced and not pure_adoption_review and report is not None:
        primary = (
            report.agent_summary.headline
            if report.agent_summary is not None
            else (
                report.release_decision.reason
                if report.release_decision is not None
                else "Shipgate requires human review."
            )
        )
        manifest = (
            f"the configured manifest {configured_manifest!r}"
            if configured_manifest
            else "the configured Shipgate manifest"
        )
        return (
            f"{primary} This PR also introduces {manifest}; adopting a release "
            "policy is a separate human-review decision."
        )
    # An agent editing the rules that evaluate its own change must see the
    # self-approval prohibition first, ahead of the generic scan headline.
    note = _self_approval_note(
        capability_review,
        manifest_introduced=manifest_introduced,
        pure_adoption_review=pure_adoption_review,
        configured_manifest=configured_manifest,
    )
    if note is not None:
        return note
    if report is not None and report.agent_summary is not None:
        return report.agent_summary.headline
    if head_status == "skipped":
        return "No agent-capability changes detected; Shipgate did not need to run."
    return None


def _verifier_mode(
    *,
    ci_mode: str | None,
    report: ReadinessReport | None,
    head_status: str,
    preview: bool,
) -> str:
    if preview:
        return "preview"
    if report is not None and report.release_decision is not None:
        return report.release_decision.fail_policy.ci_mode
    if head_status == "skipped":
        return "skipped"
    return ci_mode or "advisory"


def _derive_verifier_control(
    *,
    execution: str,
    merge_verdict: MergeVerdict,
    release_decision: ReleaseDecision | None,
    fix_task: VerifierFixTask | None,
    capability_review: VerifierCapabilityReview | None,
    headline: str | None,
    first_next_action_override: AgentControlAction | None,
    base_status: str,
    base_ref: str | None,
    manifest_introduced: bool = False,
    pure_adoption_review: bool = False,
    configured_manifest: str | None = None,
) -> AgentControl:
    """Project verifier facts through the shared operational control engine."""

    reason = (
        headline
        or (release_decision.reason if release_decision is not None else None)
        or "Agents Shipgate verification completed."
    )
    if execution == "skipped" and release_decision is None:
        return derive_agent_control(reason=reason)
    if release_decision is not None and release_decision.decision == "passed":
        return derive_agent_control(reason=reason)

    if first_next_action_override is not None:
        if isinstance(first_next_action_override, HumanControlAction):
            return derive_agent_control(
                reason=reason,
                next_action=first_next_action_override,
                human_review_required=True,
                human_review_why=first_next_action_override.why,
                stop_reason=first_next_action_override.why,
            )
        command = getattr(first_next_action_override, "command", None)
        return derive_agent_control(
            reason=reason,
            next_action=first_next_action_override,
            verify_required=True,
            allowed_next_commands=[command] if command else [],
        )

    if fix_task is not None and fix_task.actor == "coding_agent" and fix_task.safe_to_attempt:
        repair_commands = [
            repair.command
            for repair in fix_task.allowed_repairs
            if repair.command
        ]
        commands = list(dict.fromkeys(repair_commands))
        if (
            fix_task.verification_command
            and fix_task.verification_command not in commands
        ):
            commands.append(fix_task.verification_command)
        if not commands:
            raise ValueError("agent-safe verifier repair requires an exact repair command")
        command = commands[0]
        return derive_agent_control(
            reason=reason,
            next_action=CodingAgentCommandAction(
                kind="repair",
                command=command,
                why=fix_task.instructions[0] if fix_task.instructions else reason,
            ),
            verify_required=True,
            allowed_next_commands=commands,
        )

    if execution == "failed" and base_status in {"ref_missing", "archive_failed"}:
        expects = base_ref or "the requested base and head refs"
        return derive_agent_control(
            reason=reason,
            next_action=CodingAgentFetchBaseAction(
                kind="fetch_base",
                expects=expects,
                why="Make the requested diff refs available locally, then rerun verify.",
            ),
            verify_required=True,
        )

    # For a mixed adoption, ``reason`` already leads with the actual blocker
    # and appends the adoption review. Do not replace it with generic
    # trust-root copy and hide the condition that stopped the release.
    review_reason = reason
    if not manifest_introduced or pure_adoption_review:
        review_reason = (
            _self_approval_note(
                capability_review,
                manifest_introduced=manifest_introduced,
                pure_adoption_review=pure_adoption_review,
                configured_manifest=configured_manifest,
            )
            or reason
        )
    unsafe_block = bool(release_decision is not None and release_decision.decision == "blocked")
    return derive_agent_control(
        reason=reason,
        next_action=HumanControlAction(
            kind="stop" if unsafe_block or execution == "failed" else "review",
            why=review_reason,
        ),
        verify_required=release_decision is not None or execution == "failed",
        human_review_required=True,
        unsafe_block=unsafe_block,
        human_review_why=review_reason,
        stop_reason=review_reason,
    )


def _build_verifier(
    *,
    git_root: Path,
    config_path: Path,
    base: str | None,
    head: str,
    changed_files: list[str],
    diff_text: str,
    trigger: dict[str, Any],
    base_status: VerifierBaseStatus,
    base_tree: str | None,
    head_tree: str | None = None,
    base_report: Path | None,
    base_notes: list[str],
    report: ReadinessReport | None,
    head_status: str,
    head_exit_code: int,
    out_dir: Path,
    ci_mode: str | None = None,
    preview: bool = False,
    headline_override: str | None = None,
    first_next_action_override: AgentControlAction | None = None,
    manifest_introduced: bool = False,
    worktree: bool = False,
    rerun_options: list[str] | None = None,
) -> VerifierArtifact:
    release_decision_model = report.release_decision if report is not None else None
    release_decision = (
        release_decision_model.model_dump(mode="json")
        if release_decision_model is not None
        else None
    )
    artifacts = _artifact_paths(
        out_dir,
        git_root=git_root,
        include_scan_artifacts=report is not None,
    )
    decision = release_decision_model.decision if release_decision_model else None
    merge_verdict = merge_verdict_for(decision=decision, execution=head_status)
    applicability = applicability_for(decision=decision, execution=head_status)
    agent_summary_model = report.agent_summary if report is not None else None
    capability_review = build_capability_review(report) if report is not None else None
    pure_adoption_review = is_pure_adoption_review(
        report,
        manifest_introduced=manifest_introduced,
    )
    # ``ref_missing``/``archive_failed`` are unknown-base states: the run
    # already carries its own recovery action, and a repair task derived from a
    # comparison that never happened would be guesswork. ``missing_manifest``
    # is not in that class — the base was read successfully and simply has no
    # gate yet — so a first adoption gets a real fix_task instead of nothing to
    # act on.
    safe_recovery = first_next_action_override is not None or base_status in {
        "ref_missing",
        "archive_failed",
    }
    fix_task = (
        None
        if safe_recovery
        else build_fix_task(
            report,
            merge_verdict=merge_verdict,
            capability_review=capability_review,
            base_ref=base,
            head_ref=head,
            manifest_introduced=manifest_introduced,
            config=_display_path(config_path, git_root),
            worktree=worktree,
            rerun_options=rerun_options,
            report_path=str((out_dir / "report.json").resolve()),
            repair_subject_available=_repair_subject_available(
                report,
                git_root=git_root,
                head=head,
                worktree=worktree,
            ),
        )
    )
    can_merge = _can_merge_without_human(
        merge_verdict=merge_verdict,
        release_decision=release_decision_model,
        capability_review=capability_review,
    )
    headline = headline_override or _verifier_headline(
        report=report,
        merge_verdict=merge_verdict,
        head_status=head_status,
        capability_review=capability_review,
        manifest_introduced=manifest_introduced,
        pure_adoption_review=pure_adoption_review,
        configured_manifest=_display_path(config_path, git_root),
    )
    if headline_override is None:
        provenance = _gap_provenance_note(report=report, base_report=base_report)
        if provenance is not None:
            headline = f"{headline} {provenance}" if headline else provenance
    control = _derive_verifier_control(
        execution=head_status,
        merge_verdict=merge_verdict,
        release_decision=release_decision_model,
        fix_task=fix_task,
        capability_review=capability_review,
        headline=headline,
        first_next_action_override=first_next_action_override,
        base_status=base_status,
        base_ref=base,
        manifest_introduced=manifest_introduced,
        pure_adoption_review=pure_adoption_review,
        configured_manifest=_display_path(config_path, git_root),
    )
    return VerifierArtifact(
        workspace=str(git_root),
        config=_display_path(config_path, git_root),
        base_ref=base,
        head_ref=head,
        changed_files=changed_files,
        diff_text_available=bool(diff_text),
        trigger=trigger,
        base_status=base_status,
        base_tree_sha=base_tree,
        head_tree_sha=head_tree,
        base_report_json=(
            artifacts.get("verification_base_report_json")
            if base_report is not None
            else None
        ),
        base_notes=base_notes,
        execution=head_status,  # type: ignore[arg-type]
        head_status=head_status,  # compatibility mirror
        head_report_json=artifacts.get("report_json") if report is not None else None,
        head_exit_code=head_exit_code,
        release_decision=release_decision,
        agent_summary=(
            agent_summary_model.model_dump(mode="json") if agent_summary_model is not None else None
        ),
        reviewer_summary=(
            report.reviewer_summary.model_dump(mode="json")
            if report is not None and report.reviewer_summary is not None
            else None
        ),
        capability_review=capability_review if capability_review is not None else {},
        mode=_verifier_mode(
            ci_mode=ci_mode,
            report=report,
            head_status=head_status,
            preview=preview,
        ),
        decision=decision,
        merge_verdict=merge_verdict,
        applicability=applicability,
        can_merge_without_human=can_merge,
        control=control,
        authorization=AuthorizationEvaluationV1.not_requested(),
        headline=headline,
        fix_task=fix_task,
        forbidden_file_edits=list(PROTECTED_FILE_EDITS),
        forbidden_actions=list(FORBIDDEN_SHORTCUTS),
        artifacts=artifacts,
    )


def _artifact_paths(
    out_dir: Path,
    *,
    git_root: Path,
    include_scan_artifacts: bool,
) -> dict[str, str]:
    candidates = {
        "verifier_json": out_dir / "verifier.json",
        "verify_run_json": out_dir / "verify-run.json",
        "agent_handoff_json": out_dir / "agent-handoff.json",
        "verification_plan_json": out_dir / "verification-plan.json",
        "verification_input_diff": out_dir / "verification-input.diff",
        "verification_base_report_json": out_dir / "verification-base-report.json",
        "verification_unit_result_json": out_dir / "verification-unit-result.json",
        "verification_artifact_manifest_json": out_dir / "verification-artifacts.json",
        "verification_receipt_json": out_dir / "verification-receipt.json",
        "pr_comment": out_dir / "pr-comment.md",
    }
    if include_scan_artifacts:
        candidates = {
            "report_markdown": out_dir / "report.md",
            "report_json": out_dir / "report.json",
            "report_sarif": out_dir / "report.sarif",
            "packet_json": out_dir / "packet.json",
            "capability_lock_json": out_dir / "capabilities.lock.json",
            "base_capability_lock_json": out_dir / "base.capabilities.lock.json",
            "capability_lock_diff_json": out_dir / "capability-lock-diff.json",
            "capability_lock_diff_markdown": out_dir / "capability-lock-diff.md",
            **candidates,
        }
    return {
        key: _display_path(path.resolve(), git_root)
        for key, path in candidates.items()
        if key
        in {
            "verifier_json",
            "verify_run_json",
            "agent_handoff_json",
            "verification_plan_json",
            "verification_input_diff",
            "verification_unit_result_json",
            "verification_artifact_manifest_json",
            "verification_receipt_json",
            "pr_comment",
        }
        or path.exists()
    }


def _remove_scan_artifacts(out_dir: Path) -> None:
    for name in (
        "report.md",
        "report.json",
        "report.sarif",
        "packet.md",
        "packet.json",
        "packet.html",
        "packet.pdf",
        "capabilities.lock.json",
        "base.capabilities.lock.json",
        "capability-lock-diff.json",
        "capability-lock-diff.md",
        # Remediation instructions must not outlive the report they describe:
        # an early verifier reset would otherwise clear report.json and leave a
        # scaffold behind asking for declarations nothing is measuring.
        SUGGESTED_DECLARATIONS_FILENAME,
    ):
        path = out_dir / name
        if path.is_file() or path.is_symlink():
            with contextlib.suppress(OSError):
                path.unlink()


def _apply_authorization_overlay(
    verifier: VerifierArtifact,
    evaluation: AuthorizationEvaluationV1,
) -> None:
    """Project a trusted, exact operation onto control without changing the gate.

    The signed authorization is operational evidence only.  It can replace a
    human stop with one exact coding-agent command, but it cannot make the PR
    mergeable, alter the release decision, or authorize completion.  Build and
    validate the complete replacement state before mutating ``verifier`` so an
    incompatible evaluation fails atomically.
    """

    update: dict[str, Any] = {"authorization": evaluation.model_dump(mode="json")}
    if evaluation.status == "accepted":
        if (
            verifier.execution != "succeeded"
            or verifier.decision != "review_required"
            or verifier.merge_verdict != "human_review_required"
            or verifier.can_merge_without_human
        ):
            raise ValueError(
                "accepted human authorization can only overlay a succeeded "
                "review_required verifier result"
            )
        command = evaluation.command
        if not command:
            raise ValueError("accepted human authorization must carry an exact command")
        reason = (
            "A trusted human authorization permits exactly one bound operation; "
            "the static release decision and merge authority remain unchanged."
        )
        update.update(
            {
                "control": derive_agent_control(
                    reason=reason,
                    next_action=CodingAgentCommandAction(
                        kind="repair",
                        command=command,
                        why=reason,
                    ),
                    verify_required=True,
                    allowed_next_commands=[command],
                ).model_dump(mode="json"),
                "fix_task": None,
            }
        )

    payload = verifier.model_dump(mode="json")
    payload.update(update)
    validated = VerifierArtifact.model_validate(payload)
    verifier.authorization = validated.authorization
    if evaluation.status == "accepted":
        verifier.control = validated.control
        verifier.fix_task = validated.fix_task


def _evaluate_authorization_overlay(
    *,
    authorization_path: Path | None,
    verifier: VerifierArtifact,
    report: ReadinessReport | None,
    plan: VerificationPlan,
    workspace: Path,
    authorization_command: str,
) -> tuple[AuthorizationEvaluationV1, HumanAuthorizationV1 | None]:
    """Evaluate an external grant against the just-recomputed verifier graph."""

    if authorization_path is None:
        return AuthorizationEvaluationV1.not_requested(), None
    if verifier.execution != "succeeded" or verifier.decision != "review_required":
        return (
            AuthorizationEvaluationV1.not_applicable("release_decision_not_review_required"),
            None,
        )
    if plan.inputs.options.get("plugins_enabled") is not False:
        return (
            AuthorizationEvaluationV1.not_applicable(
                "authorization_requires_plugins_disabled"
            ),
            None,
        )
    try:
        if report is None:
            raise ValueError("authorization requires a release report")
        ensure_authorization_runtime_is_external(workspace)
        if active_replace_refs(workspace):
            raise ValueError("authorization rejects repositories with Git replace refs")
        grant = _load_external_human_authorization(
            authorization_path,
            workspace=workspace,
        )
        review_items = authorization_review_items(report.release_decision.model_dump(mode="json"))
        git = plan.subject.git
        if git.snapshot_kind != "committed_tree" or git.worktree_overlay_sha256 is not None:
            raise ValueError("authorization requires a committed Git subject")
        if not (
            git.base_commit_sha
            and git.merge_base_sha
            and git.base_tree_sha
            and git.head_tree_sha
            and git.source_head_commit_sha
        ):
            raise ValueError("authorization requires complete committed PR tree identity")
        signed_source = grant.statement.request
        if signed_source.source_engine_requirement_id != plan.engine.engine_requirement_id:
            raise ValueError("authorization source engine differs from the current engine")
        if signed_source.source_executor_id != verifier.executor_id:
            raise ValueError("authorization source executor differs from the current executor")
        # These two IDs are signer-authenticated provenance labels. Unlike the
        # engine, executor, request, subject, decision, tree, review-set, and
        # operation identities rebuilt here, the prior receipt and artifact
        # set are not transported into this second verification pass. Copying
        # them preserves the exact signed request; it is not an independent
        # provenance check by this verifier.
        expected_request = build_human_authorization_request(
            repository_id=git.repository_id,
            source_receipt_id=signed_source.source_receipt_id,
            source_artifact_set_id=signed_source.source_artifact_set_id,
            source_engine_requirement_id=signed_source.source_engine_requirement_id,
            source_executor_id=signed_source.source_executor_id,
            verification_request_id=plan.request_id,
            subject_id=plan.subject.subject_id,
            decision_id=verifier.decision_id or "",
            base_commit_sha=git.base_commit_sha,
            merge_base_sha=git.merge_base_sha,
            base_tree_sha=git.base_tree_sha,
            head_tree_sha=git.head_tree_sha,
            source_head_commit_sha=git.source_head_commit_sha,
            review_items=review_items,
            operation=grant.statement.request.operation,
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return AuthorizationEvaluationV1.rejected("authorization_context_invalid"), None

    evaluation = evaluate_human_authorization(
        grant,
        trust_policy_path=default_human_authorization_trust_policy_path(),
        workspace=workspace,
        expected_request=expected_request,
        expected_review_items=review_items,
    )
    if evaluation.status == "accepted":
        payload = evaluation.model_dump(mode="json")
        payload["command"] = authorization_command
        evaluation = AuthorizationEvaluationV1.model_validate(payload)
    return evaluation, grant


def _load_external_human_authorization(
    path: Path,
    *,
    workspace: Path,
) -> HumanAuthorizationV1:
    """Load a signed grant only from outside the evaluated workspace."""

    lexical = Path(os.path.abspath(path))
    resolved_workspace = workspace.resolve(strict=True)
    resolved = lexical.resolve(strict=True)
    if _path_is_within(lexical, Path(os.path.abspath(workspace))) or _path_is_within(
        resolved, resolved_workspace
    ):
        raise ValueError("human authorization grant must be stored outside the workspace")
    if not resolved.is_file() or resolved.stat().st_size > MAX_HUMAN_AUTHORIZATION_BYTES:
        raise ValueError("human authorization grant is unavailable or exceeds the size limit")
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("human authorization grant must contain one JSON object")
    return HumanAuthorizationV1.model_validate(payload)


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _write_artifacts(
    verifier: VerifierArtifact,
    verifier_path: Path,
    verify_run_path: Path,
    pr_comment_path: Path,
    *,
    report: ReadinessReport | None,
    git_root: Path,
    config_path: Path,
    config_logical_path: str | None = None,
    baseline_path: Path | None,
    policy_pack_paths: list[Path],
    plugins_enabled: bool | None,
    no_heuristics: bool,
    fail_on: list[str] | None,
    pr_comment_style: str,
    capability_lock_diff: CapabilityLockDiffV1 | None = None,
    input_root: Path | None = None,
    diff_text: str = "",
    diff_from_path: Path | None = None,
    authorization_path: Path | None = None,
    verification_options: dict[str, Any] | None = None,
    evaluation_date: str | None = None,
) -> None:
    verifier_path.parent.mkdir(parents=True, exist_ok=True)
    portable_diff_from_path: Path | None = None
    if diff_from_path is not None and diff_from_path.is_file():
        portable_diff_from_path = verifier_path.with_name(
            "verification-base-report.json"
        )
        source_bytes = read_static_input_bytes(
            diff_from_path,
            max_bytes=64 * 1024 * 1024,
        )
        if portable_diff_from_path != diff_from_path:
            portable_diff_from_path.write_bytes(source_bytes)
        logical_base_report = _display_path(
            portable_diff_from_path.resolve(),
            git_root,
        )
        verifier.artifacts["verification_base_report_json"] = logical_base_report
        verifier.base_report_json = logical_base_report
    verifier_path.write_text(
        json.dumps(verifier.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    pr_comment_path.write_text(
        render_pr_comment(
            verifier,
            report=report,
            style=pr_comment_style,
            capability_lock_diff=capability_lock_diff,
        ),
        encoding="utf-8",
    )
    # Keep report.json as the authoritative artifact, but validate the
    # in-memory report can still produce the canonical public payload.
    if report is not None:
        report_json_payload(report)
    # Always preserve the actionable control handoff, including typed input
    # failures that cannot produce a reproducible request identity. A complete
    # run overwrites this projection after the verify-run artifact is built.
    handoff_path = verifier_path.with_name("agent-handoff.json")
    initial_handoff = build_agent_handoff(
        verifier=verifier,
        report=report,
        verify_run=None,
    )
    handoff_path.write_text(
        json.dumps(initial_handoff.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    # A missing or invalid manifest cannot yield a reproducible request
    # identity.  Error artifacts remain useful diagnostics, but they are not a
    # trusted receipt and downstream consumers must reject their absence.
    if not config_path.is_file() or not ref_exists(git_root, verifier.head_ref):
        return
    resolved_input_root = (input_root or git_root).resolve()
    active_snapshot = active_static_input_snapshot()
    original_paths = [
        *policy_pack_paths,
        *([baseline_path] if baseline_path is not None else []),
        *([diff_from_path] if diff_from_path is not None else []),
    ]
    if active_snapshot is not None:
        try:
            # Base-report enrichment can be generated after the snapshot was
            # activated. Capture it now, before finalizing directory identity,
            # so plan construction and the receipt consume exactly these bytes.
            for path in original_paths:
                if (
                    path.is_file()
                    and active_snapshot.contains(path)
                    and not active_snapshot.has(path)
                ):
                    active_snapshot.read_bytes(path, max_bytes=64 * 1024 * 1024)
            active_snapshot.finish()
        except (OSError, ValueError) as exc:
            raise InputParseError(
                f"Verification inputs changed while they were being evaluated: {exc}"
            ) from exc
    original_static_inputs = {
        Path(os.path.abspath(os.path.normpath(os.fspath(path))))
        for path in original_paths
    }
    captured_input_paths = (
        [
            path
            for path in active_snapshot.paths()
            if path not in original_static_inputs
        ]
        if active_snapshot is not None
        else None
    )
    external_input_root = verifier_path.parent
    portable_policy_pack_paths = [
        _write_portable_static_input(
            path,
            root=external_input_root,
            category="policy-packs",
        )
        for path in policy_pack_paths
    ]
    baseline_was_captured = (
        baseline_path is not None
        and (
            active_snapshot.has(baseline_path)
            if active_snapshot is not None and active_snapshot.contains(baseline_path)
            else baseline_path.is_file()
        )
    )
    portable_baseline_path = (
        _write_portable_static_input(
            baseline_path,
            root=external_input_root,
            category="baseline",
        )
        if baseline_path is not None and baseline_was_captured
        else None
    )
    logical_config = config_logical_path or (
        config_path.resolve().relative_to(resolved_input_root).as_posix()
        if resolved_input_root in config_path.resolve().parents
        else _display_path(config_path.resolve(), git_root)
    )
    resolved_date = evaluation_date or commit_date(git_root, verifier.head_ref)
    base_commit = commit_sha(git_root, verifier.base_ref) if verifier.base_ref else None
    head_commit = commit_sha(git_root, verifier.head_ref)
    archived_head = resolved_input_root != git_root.resolve()
    resolved_options = dict(verification_options or {})
    evaluated_hint = resolved_options.pop("evaluated_head_commit_sha", None)
    github_actions = resolved_options.pop("github_actions", False)
    github_event_name = resolved_options.pop("github_event_name", None)
    source_head_commit: str | None = None
    if archived_head:
        try:
            source_identity = resolve_source_head_identity(
                git_root,
                head_ref=verifier.head_ref,
                github_actions=github_actions is True,
                event_name=(github_event_name if isinstance(github_event_name, str) else None),
                evaluated_head_sha=(evaluated_hint if isinstance(evaluated_hint, str) else None),
            )
        except ValueError as exc:
            raise InputParseError(f"Invalid committed source-head identity: {exc}") from exc
        if head_commit != source_identity.evaluated_head_commit_sha:
            raise InputParseError(
                "Resolved verification head changed while building source-head identity"
            )
        source_head_commit = source_identity.source_head_commit_sha
        resolved_options.update(
            {
                "evaluated_head_commit_sha": source_identity.evaluated_head_commit_sha,
                "source_head_commit_sha": source_identity.source_head_commit_sha,
                "source_head_relation": source_identity.relation,
            }
        )
    plan = build_verification_plan(
        git_root=git_root,
        input_root=resolved_input_root,
        config_path=config_path,
        config_logical_path=logical_config,
        base_ref=verifier.base_ref,
        head_ref=verifier.head_ref,
        archived_head=archived_head,
        repository_id=repository_identity(git_root),
        base_commit_sha=base_commit,
        base_tree_sha=(
            tree_sha(git_root, verifier.base_ref) if verifier.base_ref and base_commit else None
        ),
        source_head_commit_sha=source_head_commit,
        head_commit_sha=head_commit,
        head_tree_sha=(tree_sha(git_root, verifier.head_ref) if head_commit else None),
        merge_base_sha=(
            merge_base_sha(git_root, verifier.base_ref, verifier.head_ref)
            if verifier.base_ref and base_commit
            else None
        ),
        changed_files=verifier.changed_files,
        diff_text=diff_text,
        baseline_path=portable_baseline_path,
        diff_from_path=portable_diff_from_path,
        policy_pack_paths=portable_policy_pack_paths,
        evaluation_date=resolved_date,
        options={
            "ci_mode": verifier.mode,
            "fail_on": sorted(fail_on or []),
            "no_heuristics": no_heuristics,
            "plugins_enabled": plugins_enabled is not False,
            **resolved_options,
        },
        plugins_enabled=plugins_enabled,
        external_input_root=external_input_root,
        captured_input_paths=captured_input_paths,
    )
    plan_path = verifier_path.with_name("verification-plan.json")
    diff_input_path = verifier_path.with_name("verification-input.diff")
    diff_input_path.write_text(diff_text, encoding="utf-8")
    plan_path.write_text(
        json.dumps(plan.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    unit_result = build_unit_result(
        plan=plan,
        status=("succeeded" if verifier.execution in {"succeeded", "skipped"} else "failed"),
        normalized_ir={
            "execution": verifier.execution,
            "report_run_id": report.run_id if report is not None else None,
            "report_schema_version": (report.report_schema_version if report is not None else None),
        },
    )
    unit_path = verifier_path.with_name("verification-unit-result.json")
    unit_path.write_text(
        json.dumps(unit_result.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    executor = build_executor(plan.engine)
    decision_id = content_id(
        {
            "request_id": plan.request_id,
            "unit_result_ids": [unit_result.unit_result_id],
            "decision": verifier.decision,
            "merge_verdict": verifier.merge_verdict,
            "can_merge_without_human": verifier.can_merge_without_human,
        }
    )
    verifier.request_id = plan.request_id
    verifier.subject_id = plan.subject.subject_id
    verifier.input_set_id = plan.inputs.input_set_id
    verifier.engine_requirement_id = plan.engine.engine_requirement_id
    verifier.executor_id = executor.executor_id
    verifier.decision_id = decision_id
    evaluation, accepted_grant = _evaluate_authorization_overlay(
        authorization_path=authorization_path,
        verifier=verifier,
        report=report,
        plan=plan,
        workspace=git_root,
        authorization_command=authorization_execute_command(
            workspace=git_root,
            receipt=verifier_path.with_name("verification-receipt.json").resolve(),
            artifacts_root=verifier_path.parent.resolve(),
        ),
    )
    try:
        _apply_authorization_overlay(verifier, evaluation)
    except ValueError:
        # A contradictory accepted projection is an internal invariant failure,
        # never a reason to leak command authority or lose verifier artifacts.
        evaluation = AuthorizationEvaluationV1.rejected("authorization_overlay_invalid")
        accepted_grant = None
        _apply_authorization_overlay(verifier, evaluation)
    if evaluation.status == "accepted" and accepted_grant is not None:
        authorization_artifact = verifier_path.with_name("human-authorization.json")
        authorization_artifact.write_text(
            json.dumps(
                accepted_grant.model_dump(mode="json"),
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        verifier.artifacts["human_authorization_json"] = _display_path(
            authorization_artifact.resolve(), git_root
        )
    if report is not None:
        report.request_id = plan.request_id
        report.subject_id = plan.subject.subject_id
        report.input_set_id = plan.inputs.input_set_id
        report.engine_requirement_id = plan.engine.engine_requirement_id
        report.decision_id = decision_id
        report_path = verifier_path.with_name("report.json")
        if report_path.is_file():
            report_path.write_text(
                json.dumps(report_json_payload(report), indent=2),
                encoding="utf-8",
            )
        packet_path = verifier_path.with_name("packet.json")
        if packet_path.is_file():
            packet = load_packet_json(packet_path.read_text(encoding="utf-8"))
            packet.request_id = plan.request_id
            packet.subject_id = plan.subject.subject_id
            packet.input_set_id = plan.inputs.input_set_id
            packet.engine_requirement_id = plan.engine.engine_requirement_id
            packet.decision_id = decision_id
            write_packet_json(packet, packet_path)
    verifier_path.write_text(
        json.dumps(verifier.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    pr_comment_path.write_text(
        render_pr_comment(
            verifier,
            report=report,
            style=pr_comment_style,
            capability_lock_diff=capability_lock_diff,
        ),
        encoding="utf-8",
    )
    verify_run = _write_verify_run_artifact(
        verifier=verifier,
        path=verify_run_path,
        git_root=git_root,
        plan=plan,
        unit_result_id=unit_result.unit_result_id,
    )
    # Fail closed if this projection ever disagrees with the verifier/report
    # substrate. The handoff artifact is additive, but an inconsistent handoff
    # would be worse than no handoff for an agent-native release gate.
    handoff = build_agent_handoff(
        verifier=verifier,
        report=report,
        verify_run=verify_run,
    )
    handoff_path.write_text(
        json.dumps(handoff.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    if unit_result.status != "succeeded":
        # Failed executions retain their plan, failed unit IR, verifier,
        # verify-run, and actionable handoff, but never receive a terminal
        # success receipt.
        return
    identity_names = {
        "verification_plan_json",
        "verification_input_diff",
        "verification_unit_result_json",
        "verification_artifact_manifest_json",
        "verification_receipt_json",
    }
    artifact_paths = {
        name: _resolve_under_workspace(git_root, Path(value))
        for name, value in verifier.artifacts.items()
        if name not in identity_names
    }
    artifact_paths.update(
        {
            "verification_plan_json": plan_path,
            "verification_input_diff": diff_input_path,
            "verification_unit_result_json": unit_path,
            "agent_handoff_json": handoff_path,
            "verify_run_json": verify_run_path,
        }
    )
    manifest, receipt = build_terminal_receipt(
        plan=plan,
        unit_results=[unit_result],
        decision=verifier.decision,
        merge_verdict=verifier.merge_verdict,
        can_merge_without_human=verifier.can_merge_without_human,
        artifact_paths=artifact_paths,
        artifact_root=verifier_path.parent,
        attempt_id=None,
    )
    manifest_path = verifier_path.with_name("verification-artifacts.json")
    manifest_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    # Terminal receipt is written last. Its presence means every referenced
    # artifact existed and was hashed after final serialization.
    receipt_path = verifier_path.with_name("verification-receipt.json")
    receipt_path.write_text(
        json.dumps(receipt.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_verify_run_artifact(
    *,
    verifier: VerifierArtifact,
    path: Path,
    git_root: Path,
    plan: VerificationPlan,
    unit_result_id: str,
) -> Any:
    outcome = VerifyRunOutcome(
        exit_code=verifier.head_exit_code,
        base_status=verifier.base_status,
        execution=verifier.execution,
        applicability=verifier.applicability,
        decision=verifier.decision,
        merge_verdict=verifier.merge_verdict,
        can_merge_without_human=verifier.can_merge_without_human,
        control=verifier.control,
    )
    artifact = build_verify_run_artifact(
        plan=plan,
        executor=build_executor(plan.engine),
        unit_result_ids=[unit_result_id],
        outcome=outcome,
        artifacts=_verify_run_artifact_refs(verifier, git_root=git_root),
    )
    path.write_text(
        json.dumps(artifact.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return artifact


def _verify_run_artifact_refs(
    verifier: VerifierArtifact,
    *,
    git_root: Path,
) -> dict[str, VerifyRunArtifactRef]:
    refs: dict[str, VerifyRunArtifactRef] = {}
    terminal_identity_artifacts = {
        # agent-handoff.json embeds verify-run reproducibility, so including its
        # pre-projection hash here would create a cycle and become stale when
        # the final handoff is written immediately after verify-run.json.
        "agent_handoff_json",
        "verify_run_json",
        "verification_plan_json",
        "verification_unit_result_json",
        "verification_artifact_manifest_json",
        "verification_receipt_json",
    }
    for key, value in sorted(verifier.artifacts.items()):
        if key in terminal_identity_artifacts:
            continue
        artifact_path = _resolve_under_workspace(git_root, Path(value))
        refs[key] = VerifyRunArtifactRef(
            path=value,
            sha256=_sha256_file(artifact_path),
        )
    return refs


def _sha256_file(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _static_input_sha256(path: Path | None) -> str | None:
    """Hash the same cached bytes consumed by a verification scan."""

    if path is None or not path.is_file():
        return None
    return hashlib.sha256(
        read_static_input_bytes(path, max_bytes=64 * 1024 * 1024)
    ).hexdigest()


def _write_capability_review_artifacts(
    *,
    git_root: Path,
    out_dir: Path,
    base: str | None,
    base_lock: CapabilityLockFileV1 | None,
    head_lock: CapabilityLockFileV1,
    base_notes: list[str],
) -> CapabilityLockDiffV1 | None:
    lock_path = out_dir / "capabilities.lock.json"
    base_lock_path = out_dir / "base.capabilities.lock.json"
    lock_path.write_text(render_capability_lock_json(head_lock), encoding="utf-8")
    if not base:
        base_notes.append("Capability lock diff unavailable: no --base ref was provided.")
        return None
    used_scan_derived_base = base_lock is not None
    if base_lock is None:
        base_lock = _load_base_capability_lock(
            git_root=git_root,
            base=base,
            base_notes=base_notes,
        )
    if base_lock is None:
        return None
    base_lock_path.write_text(render_capability_lock_json(base_lock), encoding="utf-8")
    diff = diff_capability_locks(
        base_lock,
        head_lock,
        base_path=base_lock_path,
        head_path=lock_path,
    )
    (out_dir / "capability-lock-diff.json").write_text(
        render_capability_lock_diff_json(diff),
        encoding="utf-8",
    )
    (out_dir / "capability-lock-diff.md").write_text(
        render_capability_lock_diff_markdown(diff),
        encoding="utf-8",
    )
    if used_scan_derived_base:
        base_notes.append("Capability lock diff compared scan-derived base/head locks.")
    else:
        base_notes.append(
            "Capability lock diff compared the base reviewed envelope at "
            f"{DEFAULT_CAPABILITY_LOCK_PATH.as_posix()}."
        )
    return diff


def _load_base_capability_lock(
    *,
    git_root: Path,
    base: str,
    base_notes: list[str],
) -> CapabilityLockFileV1 | None:
    content = read_file_at_ref(git_root, base, DEFAULT_CAPABILITY_LOCK_PATH)
    if content is None:
        base_notes.append(
            "Capability lock diff unavailable: base tree does not contain "
            f"{DEFAULT_CAPABILITY_LOCK_PATH.as_posix()}."
        )
        return None
    try:
        return load_capability_lock_json(
            content,
            source=f"{base}:{DEFAULT_CAPABILITY_LOCK_PATH.as_posix()}",
        )
    except InputParseError as exc:
        base_notes.append(f"Capability lock diff unavailable: base lock is invalid: {exc}")
        return None


def _resolve_under_workspace(workspace: Path, path: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    return (workspace / path).resolve()


def _resolve_static_input_path(
    workspace: Path,
    path: Path,
    *,
    label: str,
) -> Path:
    """Bind a baseline/policy CLI spelling without following worktree aliases."""

    candidate = path if path.is_absolute() else workspace / path
    lexical = Path(os.path.abspath(os.path.normpath(os.fspath(candidate))))
    try:
        relative = lexical.relative_to(workspace)
    except ValueError:
        anchor = Path(lexical.anchor)
        relative = lexical.relative_to(anchor)
    else:
        anchor = workspace
    _reject_symlink_components(anchor, relative, label=label)
    try:
        metadata = lexical.lstat()
    except FileNotFoundError:
        return lexical
    except OSError as exc:
        raise ConfigError(f"{label} could not be inspected safely: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ConfigError(
            f"{label} must identify one singly-linked regular file: {path}"
        )
    return lexical


def _resolve_config_under_workspace(
    workspace: Path,
    path: Path,
    *,
    requested_workspace: Path | None = None,
) -> tuple[Path, Path]:
    """Resolve config spelling without following repository symlinks.

    The repository-relative path is part of verification identity. Calling
    ``Path.resolve`` first lets a tracked symlink replace that identity with
    whichever target happens to exist in the current worktree, so the diff can
    name the link while the scan evaluates an unmentioned target.
    """

    requested_anchor = (
        requested_workspace.resolve()
        if requested_workspace is not None
        else workspace
    )
    candidate = path if path.is_absolute() else requested_anchor / path
    if path.is_absolute() and requested_workspace is not None:
        lexical_requested = Path(
            os.path.abspath(os.path.normpath(os.fspath(requested_workspace)))
        )
        canonical_requested = requested_workspace.resolve()
        lexical_path = Path(os.path.normpath(os.fspath(path)))
        try:
            requested_tail = lexical_path.relative_to(lexical_requested)
        except ValueError:
            requested_tail = None
        if requested_tail is not None:
            # The absolute config was spelled under the caller's workspace
            # anchor. Map only that relative tail onto the anchor's canonical
            # target; this supports a symlink that jumps directly to a nested
            # repository directory without fabricating a lexical repo root.
            candidate = canonical_requested / requested_tail
        else:
            try:
                workspace_tail = canonical_requested.relative_to(workspace)
            except ValueError:
                workspace_tail = None
            if workspace_tail is not None:
                lexical_root = lexical_requested
                for _part in workspace_tail.parts:
                    lexical_root = lexical_root.parent
                try:
                    proven_root = lexical_root.resolve()
                except OSError:
                    proven_root = None
                if proven_root == workspace:
                    try:
                        config_tail = lexical_path.relative_to(lexical_root)
                    except ValueError:
                        pass
                    else:
                        candidate = workspace / config_tail
    lexical = Path(os.path.normpath(os.fspath(candidate)))
    try:
        relative = lexical.relative_to(workspace)
    except ValueError as exc:
        raise ConfigError(f"--config must be inside --workspace: {path}") from exc
    _reject_symlink_components(workspace, relative, label="--config")
    try:
        metadata = lexical.lstat()
    except FileNotFoundError:
        return lexical, relative
    except OSError as exc:
        raise ConfigError(f"--config could not be inspected safely: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ConfigError(
            "--config must identify one singly-linked regular file; "
            f"non-regular or hardlinked manifest refused: {path}"
        )
    return lexical, relative


def _reject_symlink_components(
    root: Path,
    relative: Path,
    *,
    label: str,
    allow_filesystem_alias: bool = False,
) -> None:
    """Reject a manifest reached through a symlink or filesystem alias."""

    issue = inspect_lexical_path_identity(root, relative)
    if issue is None:
        return
    requested = _display_path(issue.requested, root)
    if issue.kind == "symlink":
        raise ConfigError(f"{label} must not contain symlink components: {requested}")
    if issue.kind == "alias":
        if allow_filesystem_alias and issue.actual is not None:
            # Git can materialize the same tracked path with a precomposed
            # Unicode or index-canonical case spelling. The worktree-side
            # config identity was already validated before archiving, and
            # checks compare the two spellings by same-entry identity. An
            # unresolved alias (``actual is None``) did not prove that
            # same-entry relationship and therefore remains fail-closed.
            return
        actual = (
            _display_path(issue.actual, root)
            if issue.actual is not None
            else "a differently spelled filesystem entry"
        )
        raise ConfigError(
            f"{label} must use the exact filesystem spelling: "
            f"{requested} resolves to {actual}"
        )
    raise ConfigError(
        f"{label} could not be inspected safely: {requested}: {issue.detail}"
    )


def _dedupe_paths(paths: list[str]) -> list[str]:
    return sorted({path for path in paths if path})


def _bind_worktree_config_to_head(
    *,
    git_root: Path,
    head: str,
    config_relative: Path,
    worktree_text: str,
    changed_files: list[str],
) -> list[str]:
    """Force the manifest into context when its loaded bytes differ from HEAD.

    Git's ordinary worktree diff intentionally honors ignore rules and index
    flags such as ``assume-unchanged``/``skip-worktree``. Those are useful for
    local development but cannot hide the release policy that verify actually
    loads. Compare the manifest independently and add its exact logical path
    whenever the bytes differ or the file is absent from HEAD.
    """

    try:
        head_text = read_file_at_ref(git_root, head, config_relative)
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(
            f"Configured manifest {config_relative.as_posix()} could not be "
            f"read from {head!r}: {exc}"
        ) from exc
    if head_text == worktree_text:
        return changed_files
    return _dedupe_paths([*changed_files, config_relative.as_posix()])


def _join_diff_text(left: str, right: str) -> str:
    if left and right:
        return f"{left}\n{right}"
    return left or right


def _project_archived_report_paths(
    report: ReadinessReport,
    *,
    archived_config: Path,
    checkout_config: Path,
) -> None:
    """Project temporary archive paths onto stable checkout coordinates.

    Current machine patches are manifest-only. Any future patch against a
    different archived file must add an explicit portable mapping here rather
    than leaking a temporary path into receipt-bound evidence.
    """

    archived_target = archived_config.resolve()
    checkout_target = checkout_config.resolve()
    report.manifest_dir = str(checkout_target.parent)
    for finding in report.findings:
        for patch in finding.patches or []:
            target_file = getattr(patch, "target_file", None)
            if target_file is None:
                continue
            try:
                patch_target = Path(target_file).resolve()
            except OSError as exc:
                raise ConfigError(
                    "Archived suggested-patch target could not be mapped safely"
                ) from exc
            if patch_target != archived_target:
                raise ConfigError(
                    "Archived suggested patch targets an unsupported file: "
                    f"{target_file}"
                )
            patch.target_file = str(checkout_target)


def _repair_subject_available(
    report: ReadinessReport | None,
    *,
    git_root: Path,
    head: str,
    worktree: bool,
) -> bool:
    """Whether an agent-safe repair can be reverified without changing refs.

    A ref-bound run evaluates committed objects. ``apply-patches`` mutates the
    checkout only, so its exact ref-bound rerun would continue to scan the old
    commit and could never validate the repair. Until the control contract can
    model an intentional commit as a separate reviewed transition, only a
    working-tree run may advertise the autonomous mechanical route.
    """

    return worktree


def _stable_archive_error(
    exc: BaseException,
    *,
    archive_root: Path,
    label: str,
) -> str:
    """Remove random temporary-root spellings from public verifier evidence."""

    detail = str(exc) or type(exc).__name__
    spellings = {str(archive_root), str(archive_root.absolute())}
    try:
        spellings.add(str(archive_root.resolve()))
    except OSError:
        pass
    for spelling in sorted(spellings, key=len, reverse=True):
        detail = detail.replace(spelling, f"<{label}>")
    return detail


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _write_portable_static_input(
    path: Path,
    *,
    root: Path,
    category: str,
) -> Path:
    """Copy one captured external input into the reproducible artifact graph."""

    data = read_static_input_bytes(path, max_bytes=64 * 1024 * 1024)
    digest = hashlib.sha256(data).hexdigest()
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", path.name).strip("._")
    if not safe_name:
        safe_name = "input"
    target = root / "verification-inputs" / category / f"{digest[:16]}-{safe_name}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def _reject_output_input_overlap(
    *,
    git_root: Path,
    out_dir: Path,
    inputs: list[tuple[str, Path]],
) -> None:
    """Keep generated artifacts from replacing verifier inputs."""

    root = git_root.resolve()
    output = out_dir.resolve()
    if output == root:
        raise ConfigError("Verifier --out cannot be the workspace root.")
    for label, candidate in inputs:
        resolved = candidate.resolve()
        try:
            resolved.relative_to(output)
        except ValueError:
            continue
        raise ConfigError(
            f"Verifier --out overlaps the {label} input at "
            f"{_display_path(candidate, git_root)}."
        )


def _shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def _preview_init_command(workspace: Path) -> str:
    command_workspace = workspace if workspace.is_absolute() else Path.cwd() / workspace
    return _shell_join(
        [
            "shipgate",
            "init",
            "--workspace",
            str(command_workspace),
            "--write",
            "--json",
        ]
    )


def _preview_verify_command(
    *,
    workspace: Path,
    config: Path,
    base: str | None,
    head: str | None,
    out: Path | None,
    pr_comment_style: str = "capability-review",
    preview: bool = False,
) -> str:
    command_workspace = workspace if workspace.is_absolute() else Path.cwd() / workspace
    parts = [
        "agents-shipgate",
        "verify",
        "--workspace",
        str(command_workspace),
        "--config",
        str(config),
    ]
    if preview:
        parts.append("--preview")
    if base is not None:
        parts.extend(["--base", base])
    if head is not None:
        parts.extend(["--head", head])
    if out is not None:
        parts.extend(["--out", str(out)])
    if pr_comment_style and pr_comment_style != "capability-review":
        parts.extend(["--pr-comment-style", pr_comment_style])
    if not preview:
        parts.extend(["--ci-mode", "advisory"])
    parts.append("--json")
    return _shell_join(parts)


@contextlib.contextmanager
def _without_github_step_summary():
    prior = os.environ.pop("GITHUB_STEP_SUMMARY", None)
    try:
        yield
    finally:
        if prior is not None:
            os.environ["GITHUB_STEP_SUMMARY"] = prior


def run_preview(
    *,
    workspace: Path,
    config: Path,
    base: str | None,
    head: str | None,
    out: Path | None,
    pr_comment_style: str = "capability-review",
) -> tuple[VerifierArtifact, None, int]:
    """Lightweight relevance check for ``agents-shipgate verify --preview``."""
    requested_root = workspace.resolve()
    try:
        root = ensure_git_workspace(requested_root)
    except ConfigError:
        # Preview remains useful before a project has a Git repository. When
        # one does exist, use the same repository root and path coordinates as
        # the full verifier so its authorized command evaluates the same gate.
        root = requested_root
    config_path, config_relative = _resolve_config_under_workspace(
        root,
        config,
        requested_workspace=workspace,
    )
    out_dir = _resolve_under_workspace(root, out or DEFAULT_OUT_DIR)
    _reject_output_input_overlap(
        git_root=root,
        out_dir=out_dir,
        inputs=[("config", config_path)],
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    clear_verifier_route_artifacts(out_dir)
    verifier_path = out_dir / "verifier.json"
    verify_run_path = out_dir / "verify-run.json"
    agent_handoff_path = out_dir / "agent-handoff.json"
    pr_comment_path = out_dir / "pr-comment.md"
    manifest_present = config_path.is_file()

    changed_files: list[str] = []
    diff_text = ""
    notes: list[str] = []
    diff_unavailable = False
    diff_failure_requires_review = False
    if base or head:
        try:
            git_root = ensure_git_workspace(root)
            head_ref = head or "HEAD"
            if base:
                if ref_exists(git_root, base) and ref_exists(git_root, head_ref):
                    changed_files, diff_text = diff_context(git_root, base, head_ref)
                else:
                    diff_unavailable = True
                    notes.append(
                        "Preview diff unavailable: base/head ref is not available locally."
                    )
        except Exception as exc:  # noqa: BLE001 - preview must never crash.
            diff_unavailable = True
            diff_failure_requires_review = True
            notes.append(f"Preview diff unavailable: {exc}")

    trigger = evaluate(
        paths=changed_files,
        diff_text=diff_text,
        manifest_present=manifest_present,
        user_requested=True,
    )

    # Trigger previews may recommend detect/init as a generic recovery path.
    # Verify preview deliberately returns the exact one-shot init command that
    # installs the local contract, default agent kit, and advisory CI workflow
    # for unconfigured workspaces so cold-start agents do not need to infer the
    # next command from README prose.
    init_command = _preview_init_command(workspace)
    verify_command = _preview_verify_command(
        workspace=workspace,
        config=config,
        base=base,
        head=head,
        out=out,
        pr_comment_style=pr_comment_style,
    )

    if diff_unavailable and manifest_present and diff_failure_requires_review:
        why = (
            "Preview could not collect the requested deterministic diff even "
            "though ref availability was not the problem. Inspect the reported "
            "Git configuration/resource failure before rerunning."
        )
        next_action = HumanControlAction(kind="review", why=why)
        headline = "Shipgate preview could not safely inspect the requested PR diff."
    elif diff_unavailable and manifest_present:
        next_action: AgentControlAction = CodingAgentFetchBaseAction(
            kind="fetch_base",
            expects=base or head or "the requested base and head refs",
            why=(
                "Preview could not inspect the requested PR diff; make the base "
                "and head refs available locally, then rerun preview or verify."
            ),
        )
        headline = "Shipgate preview could not inspect the requested PR diff."
    elif manifest_present:
        next_action = CodingAgentCommandAction(
            kind="verify",
            command=verify_command,
            why="Shipgate is already set up here; run verify on the PR diff.",
        )
        headline = "Shipgate is configured; run verify on the PR to get a merge verdict."
    elif trigger.get("should_run") or trigger.get("dry_run_recommended"):
        next_action = CodingAgentCommandAction(
            kind="initialize",
            command=init_command,
            why=(
                "This unconfigured workspace looks agent-related; initialize "
                "the local Shipgate contract and advisory agent workflow."
            ),
        )
        headline = "Shipgate is relevant to this diff; initialize the local agent workflow."
    elif not (base or head):
        next_action = CodingAgentCommandAction(
            kind="initialize",
            command=init_command,
            why=(
                "No PR diff was supplied and no shipgate.yaml was found; "
                "initialize Shipgate if this workspace contains an agent."
            ),
        )
        headline = "Shipgate is not set up here yet; initialize it to gate agent-capability PRs."
    else:
        next_action = CodingAgentCommandAction(
            kind="initialize",
            command=init_command,
            why=(
                "No shipgate.yaml was found. Initialize the local Shipgate "
                "contract if this workspace contains an agent."
            ),
        )
        headline = "Shipgate is not configured in this workspace."

    control = derive_agent_control(
        reason=headline,
        next_action=next_action,
        verify_required=True,
        allowed_next_commands=(
            [next_action.command] if isinstance(next_action, CodingAgentCommandAction) else []
        ),
    )
    verifier = VerifierArtifact(
        workspace=str(root),
        config=_display_path(config_path, root),
        base_ref=base,
        head_ref=head or "HEAD",
        changed_files=changed_files,
        diff_text_available=bool(diff_text),
        trigger=trigger,
        base_status="not_requested",
        base_notes=notes,
        execution="not_run",
        head_status="not_run",
        head_exit_code=0,
        mode="preview",
        merge_verdict="unknown",
        applicability="not_evaluated",
        can_merge_without_human=False,
        control=control,
        authorization=AuthorizationEvaluationV1.not_requested(),
        headline=headline,
        forbidden_file_edits=list(PROTECTED_FILE_EDITS),
        forbidden_actions=list(FORBIDDEN_SHORTCUTS),
        artifacts={
            "verifier_json": _display_path(verifier_path.resolve(), root),
            "verify_run_json": _display_path(verify_run_path.resolve(), root),
            "agent_handoff_json": _display_path(agent_handoff_path.resolve(), root),
            "pr_comment": _display_path(pr_comment_path.resolve(), root),
        },
    )
    _write_artifacts(
        verifier,
        verifier_path,
        verify_run_path,
        pr_comment_path,
        report=None,
        git_root=root,
        config_path=config_path,
        config_logical_path=config_relative.as_posix(),
        baseline_path=None,
        policy_pack_paths=[],
        plugins_enabled=None,
        no_heuristics=False,
        fail_on=None,
        pr_comment_style=pr_comment_style,
    )
    return verifier, None, 0


__all__ = ["run_preview", "run_verify"]
