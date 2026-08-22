from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import tempfile
import unicodedata
from collections import Counter
from collections.abc import Callable
from contextvars import Token
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

from agents_shipgate import __version__
from agents_shipgate.checks.verify import PROTECTED_FILE_EDITS
from agents_shipgate.ci.release_decision import SUGGESTED_DECLARATIONS_FILENAME
from agents_shipgate.cli._artifact_lifecycle import clear_verifier_route_artifacts
from agents_shipgate.cli._helpers import _apply_strict_plugins
from agents_shipgate.cli.discovery.scope import (
    ChangeScope,
    ScopeResolution,
    manifest_opt_in,
    resolve_change_scope,
)
from agents_shipgate.cli.discovery.signals import weak_marker_evidence_dirs
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
from agents_shipgate.core.current_control import (
    VERIFIER_ROUTE_CONTROL_ARTIFACT_KEYS,
    begin_current_control,
    owns_current_control,
    project_agent_control,
    publish_current_control,
    workspace_identity_from_plan,
)
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError
from agents_shipgate.core.evaluation_clock import use_evaluation_date
from agents_shipgate.core.findings.constants import SEVERITY_ORDER
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
    worktree_overlay,
)
from agents_shipgate.invocation import retarget_command
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
from agents_shipgate.schemas.agent_control_envelope import MAX_ENVELOPE_PROSE_BYTES
from agents_shipgate.schemas.capabilities import CapabilityLockDiffV1, CapabilityLockFileV1
from agents_shipgate.schemas.current_control import (
    CurrentControlOperation,
    CurrentControlWorkspaceIdentity,
)
from agents_shipgate.schemas.human_authorization import (
    AuthorizationEvaluationV1,
    HumanAuthorizationV1,
    authorization_review_items,
    build_human_authorization_request,
)
from agents_shipgate.schemas.report import (
    ReadinessReport,
    ReleaseDecision,
    ReleaseDecisionItem,
)
from agents_shipgate.schemas.verification import VerificationContext
from agents_shipgate.schemas.verification_identity import VerificationPlan, content_id
from agents_shipgate.schemas.verifier import (
    MergeVerdict,
    VerifierArtifact,
    VerifierBaseStatus,
    VerifierCapabilityReview,
    VerifierDiffStatus,
    VerifierFixTask,
    applicability_for,
    merge_verdict_for,
)
from agents_shipgate.schemas.verify_run import (
    VerifyRunArtifactRef,
    VerifyRunOutcome,
    build_verify_run_artifact,
)
from agents_shipgate.triggers import (
    ACTION_FORCE_RUN as TRIGGER_ACTION_FORCE_RUN,
)
from agents_shipgate.triggers import (
    INPUT_COMPLETE,
    INPUT_PARTIAL,
    INPUT_UNAVAILABLE,
    evaluate,
)

from .capability_review import build_capability_review
from .fix_task import FORBIDDEN_SHORTCUTS, build_fix_task, is_pure_adoption_review
from .git import (
    DiffContext,
    DiffInputError,
    active_replace_refs,
    archive_tree,
    carries_manifest_like_yaml,
    collect_diff_context,
    commit_date,
    commit_sha,
    detect_default_base_with_notes,
    ensure_git_workspace,
    git_path,
    merge_base_sha,
    read_file_at_ref,
    ref_exists,
    removes_a_yaml_file,
    repository_identity,
    require_merge_base_sha,
    resolve_source_head_identity,
    resolve_tree_path_identity,
    tree_sha,
    working_tree_context,
    working_tree_paths,
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


@owns_current_control("verify")
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
    out_dir = _resolve_out_dir(
        git_root=git_root, requested_workspace=workspace.resolve(), out=out
    )
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
    # Invalidate before anything else moves. A prior terminal pointer must not
    # stay current for one instant of a run that is about to replace the
    # artifacts it references, and a crash from here on must leave a directory
    # that denies cached control rather than one that still authorizes it.
    begin_current_control(
        out_dir,
        operation="verify",
        reason=(
            "A verification run is in progress; no decision in this directory "
            "is current until it publishes one."
        ),
        repository=_safe_repository_identity(git_root),
    )
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
            # verify stopped before reading any diff, so the evaluator has no
            # change set to reason about and must not report "no rules matched".
            input_status=INPUT_UNAVAILABLE,
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
            diff_status=VerifierDiffStatus(
                completeness="unavailable",
                reason="not_attempted",
                detail="verify stopped at the missing manifest.",
                remediation=(
                    "Point --config at the manifest, or initialize Shipgate, "
                    "then rerun."
                ),
            ),
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
    worktree_overlay_paths: list[str] = []
    diff_text = ""
    base_status: VerifierBaseStatus = "not_requested"
    base_tree: str | None = None
    base_report: Path | None = None
    base_capability_lock: CapabilityLockFileV1 | None = None
    base_notes: list[str] = []
    diff_unavailable = False
    # Every collector that fell short, paired with what its repair would need.
    # The action and the headline are derived once from the worst of them, so
    # a published ``diff_status`` can never disagree with the repair it
    # authorizes.
    diff_failures: list[tuple[DiffContext, str]] = []

    head_exists = ref_exists(git_root, head)
    if not head_exists:
        trigger = evaluate(
            paths=[],
            diff_text="",
            manifest_present=True,
            user_requested=True,
            input_status=INPUT_UNAVAILABLE,
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
            diff_status=VerifierDiffStatus(
                completeness="unavailable",
                reason="refs_missing",
                detail=message,
                remediation=(
                    "Fetch the head ref locally, then rerun verify."
                ),
                fetch_repairable=True,
            ),
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
    effective_worktree_ref = head
    committed_diff_complete = False
    if base:
        base_exists = ref_exists(git_root, base)
        if base_exists:
            if archive_head:
                collected = _collect_diff(git_root, base, head)
                changed_files = list(collected.changed_files)
                diff_text = collected.diff_text
                if collected.completeness != "complete":
                    # The refs resolved, so the shortfall is about history depth,
                    # object availability, or Git itself — each of which has a
                    # different repair. Report which one instead of a single
                    # "could not be read".
                    diff_unavailable = True
                    base_status = "archive_failed"
                    diff_failures.append((collected, f"{base}...{head}"))
                    base_notes.append(
                        f"Could not collect the {base}...{head} diff in full. "
                        f"{collected.note}"
                    )
            else:
                try:
                    effective_worktree_ref = require_merge_base_sha(
                        git_root,
                        base,
                        head,
                    )
                    committed_diff_complete = True
                except DiffInputError as exc:
                    diff_unavailable = True
                    base_status = "archive_failed"
                    diff_failures.append((exc.context, f"{base}...{head}"))
                    base_notes.append(
                        "Could not resolve the merge base needed for one "
                        f"effective-head diff. {exc.context.note}"
                    )
        else:
            diff_unavailable = True
            base_status = "ref_missing"
            diff_failures.append(
                (
                    DiffContext(
                        completeness="unavailable",
                        reason="refs_missing",
                        detail=f"Base ref {base!r} is not available locally.",
                    ),
                    base,
                )
            )
            base_notes.append(
                f"Base ref {base!r} is not available locally; run with fetch-depth: 0 "
                "or fetch the base before verify."
            )

    if not archive_head:
        try:
            worktree_paths, worktree_diff = working_tree_context(
                git_root,
                comparison_ref=effective_worktree_ref,
                exclude=out_dir,
                reject_index_hidden=True,
            )
            if committed_diff_complete:
                # This is the single merge-base-to-effective-worktree diff.
                # Do not append the committed range: overlapping paths must be
                # represented exactly once at their effective content.
                changed_files = worktree_paths
                diff_text = worktree_diff
            else:
                # The committed side was unavailable or partial. Preserve all
                # evidence collected before the mandatory fail-closed exit.
                changed_files = _dedupe_paths([*changed_files, *worktree_paths])
                diff_text = _join_diff_text(diff_text, worktree_diff)
            head_commit = commit_sha(git_root, head)
            worktree_overlay_paths = (
                worktree_paths
                if head_commit == commit_sha(git_root, effective_worktree_ref)
                else working_tree_paths(
                    git_root,
                    comparison_ref=head,
                    exclude=out_dir,
                    reject_index_hidden=True,
                )
            )
            if committed_diff_complete:
                cancelled_committed_paths = sorted(
                    set(worktree_overlay_paths) - set(worktree_paths)
                )
                if cancelled_committed_paths:
                    count = len(cancelled_committed_paths)
                    noun = "change" if count == 1 else "changes"
                    verb = "is" if count == 1 else "are"
                    base_notes.append(
                        f"{count} committed {noun} {verb} canceled by uncommitted "
                        "worktree edits; the committed branch has not been verified."
                    )
            changed_files = _bind_worktree_config_to_head(
                git_root=git_root,
                head=head,
                config_relative=config_relative,
                worktree_text=worktree_manifest_text,
                changed_files=changed_files,
            )
            worktree_overlay_paths = _bind_worktree_config_to_head(
                git_root=git_root,
                head=head,
                config_relative=config_relative,
                worktree_text=worktree_manifest_text,
                changed_files=worktree_overlay_paths,
            )
        except Exception as exc:  # noqa: BLE001 - local context degrades only.
            diff_unavailable = True
            worktree_failure = _as_diff_context(exc)
            # Whatever the failed collector did read still counts. Reporting
            # "changed paths were collected" in the notes while handing the
            # trigger an empty list would lose the path-rule match the paths
            # exist to produce.
            changed_files = _dedupe_paths(
                [*changed_files, *worktree_failure.changed_files]
            )
            diff_text = _join_diff_text(diff_text, worktree_failure.diff_text)
            diff_failures.append((worktree_failure, head))
            base_notes.append(
                f"Could not collect working-tree diff context. "
                f"{worktree_failure.note}"
            )

    # A worktree shortfall is never softened by a committed-ref diff that did
    # read cleanly: the two are unioned into one change set, so the union is
    # only as complete as its weakest half — and the repair Shipgate authorizes
    # has to be the repair for *that* half. Deriving it incrementally published
    # a fetch_base action beside a diff_status no fetch could repair.
    diff_input, diff_failure_expects = _worst_diff_failure(diff_failures)
    diff_failure_action = (
        _diff_failure_action(diff_input, expects=diff_failure_expects)
        if diff_input is not None
        else None
    )

    trigger = evaluate(
        paths=changed_files,
        diff_text=diff_text,
        manifest_present=config_path.exists(),
        # Running verify is itself an explicit Shipgate request; this keeps
        # trigger stop-conditions from treating the canonical PR command as
        # passive repo discovery.
        user_requested=True,
        input_status=_trigger_input_status(diff_input),
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
        diff_status=_diff_status_artifact(diff_input),
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
            diff_status=_diff_status_artifact(diff_input),
            base_report=base_report,
            base_notes=base_notes,
            report=None,
            head_status="failed",
            head_exit_code=2,
            out_dir=out_dir,
            ci_mode=ci_mode,
            first_next_action_override=diff_failure_action,
            headline_override=_diff_failure_headline(diff_input),
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
                diff_status=_diff_status_artifact(diff_input),
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
    head_snapshot: StaticInputSnapshot | None = None
    head_snapshot_token: Token[StaticInputSnapshot | None] | None = None
    head_manifest_text: str | None = None
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
        # The overlay set is HEAD-relative and the change set is merge-base-
        # relative, so a canceled path appears only in the former. Bind the
        # union: an overlay path the snapshot contains but never read is
        # reported as absent, which would attest a present file as deleted.
        _bind_changed_files(
            static_snapshot,
            root=git_root,
            relative_paths=_dedupe_paths([*changed_files, *worktree_overlay_paths]),
        )
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
            # Resolve once the tree exists. The snapshot matches paths lexically,
            # and on macOS the temporary directory is reached through /var while
            # every adapter resolves its base directory to /private/var — two
            # spellings make `contains()` false for paths plainly inside it.
            head_tree_dir = head_tree_dir.resolve()
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
            # The outer snapshot is bound to the worktree and cannot observe a
            # read under the archived tree, so a committed-tree scan would
            # otherwise record no adapter reads at all. Capture against the tree
            # actually being evaluated, so an input discovered while parsing an
            # entrypoint — an ADK McpToolset inventory, an OpenAPI spec named
            # only from Python — reaches `input_set_id` here exactly as it
            # already does for a worktree run.
            head_snapshot = StaticInputSnapshot(
                head_tree_dir,
                excluded_paths=[out_dir],
            )
            # Read the manifest once, here, and hand those exact bytes to the
            # scan. `load_manifest_with_positions` otherwise parses it twice —
            # first through a direct `Path.read_text`, only then through the
            # snapshot for positions — so a rewrite between the two would let
            # the scan follow one manifest while the plan hashes the other.
            # The worktree path has always passed its captured text for this
            # reason; the archived path passed None.
            try:
                head_manifest_text = head_snapshot.read_bytes(
                    head_config_path,
                    max_bytes=MAX_WORKTREE_CHANGED_FILE_BYTES,
                ).decode("utf-8")
            except (OSError, ValueError, UnicodeDecodeError) as exc:
                raise ConfigError(
                    f"Head manifest {config_relative.as_posix()} could not be "
                    f"captured for verification: {exc}"
                ) from exc
            # Externally supplied inputs keep their worktree location even for a
            # committed-tree run — `_map_optional_tree_path` only rewrites paths
            # under the repository — so the scan below reads them from outside
            # the tree this snapshot watches. Bind their bytes to the worktree
            # snapshot now, before the scan can observe them, so exactly one
            # snapshot owns each external input for the whole run: two watchers
            # would both have to re-validate the same directory, and the second
            # would fail on any change the first legitimately allowed.
            for path in external_snapshot_paths:
                if (
                    path.is_file()
                    and static_snapshot.contains(path)
                    and not static_snapshot.has(path)
                ):
                    static_snapshot.read_bytes(path, max_bytes=64 * 1024 * 1024)
            # Bind the changed files too. Plan construction runs under this
            # snapshot, and ``_blobs`` drops a path it contains but never read —
            # so a changed file the adapters do not open (a README, an unrelated
            # module) would silently vanish from ``changed_files``. Mirrors the
            # worktree preload above.
            _bind_changed_files(
                head_snapshot,
                root=head_tree_dir,
                relative_paths=changed_files,
            )
        with use_evaluation_date(date.fromisoformat(verification_date)):
            head_snapshot_token = (
                activate_static_input_snapshot(head_snapshot)
                if head_snapshot is not None
                else None
            )
            try:
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
                        # A base ref was resolved and produced no report: the
                        # ref was missing, the archive or the base scan failed,
                        # or the base carries no manifest. ``diff_from_path`` is
                        # simply ``None`` in all of those, which the head scan
                        # cannot tell apart from "nobody asked to compare" —
                        # and concluding an unbound tool is pre-existing from a
                        # comparison that never ran is exactly the weakening
                        # §2.3 forbids.
                        base_comparison_unavailable=bool(base) and base_report is None,
                    ),
                    capability_lock_callback=capture_capability_lock,
                    manifest_text=(
                        head_manifest_text if archive_head else worktree_manifest_text
                    ),
                )
            finally:
                # Deactivated for the rest of the run so the worktree snapshot
                # is restored for the externally supplied inputs it owns. It is
                # re-activated for plan construction, which must hash the bytes
                # captured here rather than reopen the files.
                if head_snapshot_token is not None:
                    reset_static_input_snapshot(head_snapshot_token)
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
            diff_status=_diff_status_artifact(diff_input),
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
                    input_snapshot=head_snapshot,
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
                    worktree_overlay_paths=worktree_overlay_paths,
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


def _bind_changed_files(
    snapshot: StaticInputSnapshot,
    *,
    root: Path,
    relative_paths: list[str],
) -> None:
    """Bind every changed file's bytes to ``snapshot`` before it is sealed.

    Plan construction runs under the snapshot, and ``_blobs`` skips a path the
    snapshot contains but never read. Without this a changed file no adapter
    opens would drop out of ``changed_files`` entirely.
    """

    for relative in relative_paths:
        candidate = Path(os.path.abspath(os.path.normpath(os.fspath(root / relative))))
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
            snapshot.read_bytes(candidate, max_bytes=MAX_WORKTREE_CHANGED_FILE_BYTES)
        except (OSError, ValueError) as exc:
            raise ConfigError(
                f"Changed worktree input {relative!r} could not be captured "
                f"for verification: {exc}"
            ) from exc


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

    Routing and claim are separate questions. ``policy_weakened`` is the
    fail-closed routing flag and stays raised when no base policy existed to
    compare against; ``policy_weakening_proven`` is the narrower fact that a
    comparison actually ran. The route is the same for both, so nothing about
    gating depends on which sentence comes back — but only the proven case may
    say the policy was weakened.
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
        # ``policy_weakened`` is the fail-closed routing flag: it stays raised
        # when the direction could not be established at all, so that breaking
        # the base scan is not a way to clear the alarm. The *claim* must not
        # inherit that conservatism. Saying "this PR weakens the release
        # policy" about a change nothing compared states a fact the run does
        # not have, and leaves the reader unable to tell it from a real
        # weakening. The route below is identical either way.
        if capability_review.policy_weakening_proven:
            return (
                "This PR weakens the release policy that evaluates it; a coding "
                "agent cannot self-approve that change — a human must review it."
            )
        return (
            "This PR changes the release policy that evaluates it and no base "
            "policy was available to prove the change does not weaken the gate; "
            "a coding agent cannot self-approve that change — a human must "
            "review it."
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


# Severities that outrank a governance notice in the headline. The trust-root
# and no-base-policy notices are medium; a blocker at these tiers describes a
# risk in the change itself, which is the thing a reviewer is deciding how much
# attention to spend on.
_HEADLINE_LEADING_SEVERITIES = frozenset({"critical", "high"})


def _worst_blocker(release_decision: ReleaseDecision | None) -> ReleaseDecisionItem | None:
    """The blocker a reviewer should read first, chosen deterministically.

    Same ordering as ``agent_summary``'s top-finding picker: severity first,
    then check id, then title, so two runs of the same tree name the same row.
    """

    if release_decision is None or not release_decision.blockers:
        return None
    return min(
        release_decision.blockers,
        key=lambda item: (
            SEVERITY_ORDER.get(item.severity, 99),
            item.check_id,
            item.title,
        ),
    )


# Unicode general categories that must never survive into headline prose.
#
# ``Cc`` is the C0/C1 control range — a newline ends the one-sentence contract.
# ``Cf`` is the format range, and it is the one that matters most here: U+202E
# RIGHT-TO-LEFT OVERRIDE and U+2066 LEFT-TO-RIGHT ISOLATE reorder *rendered*
# text without changing a byte of it, so a tool name carrying one can visually
# move the reserved governance suffix out of the position the composition
# guarantees it. ``Cs`` (lone surrogates) cannot be UTF-8 encoded at all and
# would raise inside the byte budgeting. ``Co``/``Cn`` are private-use and
# unassigned: they render as whatever the reader's font decides, which is not
# a property a release artifact should depend on. ``Zl``/``Zp`` are the
# line/paragraph separators that ``str.split`` already folds, listed so the
# rule is stated once and completely.
_UNSAFE_UNICODE_CATEGORIES = frozenset(
    {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}
)

# How much of the worst blocker's title the headline will carry. The title is
# an untrusted value — it embeds a tool name read out of an OpenAPI spec, an
# MCP export, or a Python source file — so it is bounded on its own account,
# independently of the composition budget below, to keep an oversized one from
# expanding every downstream renderer that quotes the headline.
_HEADLINE_TITLE_MAX_CHARS = 120
# The configured manifest path is interpolated into the adoption suffix. It is
# operator-supplied rather than scanned, but the suffix is the part the
# composition promises to keep whole, so its one dynamic input is bounded to
# keep that promise satisfiable.
_HEADLINE_MANIFEST_MAX_CHARS = 80
_ELLIPSIS = "…"


def _one_clause(value: str) -> str:
    """Collapse an untrusted value into a single clause of a single sentence.

    Unsafe codepoints are dropped rather than escaped: the headline is prose,
    not a transcript, and a ``\\x0a`` escape in the middle of a sentence is no
    more readable than the newline it replaces. Runs of whitespace collapse so
    the result cannot be padded into a different shape, and the result is
    guaranteed UTF-8 encodable so the byte budgeting below cannot raise on it.
    """

    stripped = "".join(
        " " if unicodedata.category(char) in _UNSAFE_UNICODE_CATEGORIES else char
        for char in value
    )
    return " ".join(stripped.split())


def _manifest_label(configured_manifest: str | None) -> str:
    """The configured manifest path, safe to interpolate into a reserved suffix.

    The suffix is the part the composition promises to deliver whole, so its
    only dynamic input has to be bounded — otherwise a deeply nested path makes
    the promise unsatisfiable and the requirement is truncated after all.
    """

    return _bounded(_one_clause(configured_manifest or ""), _HEADLINE_MANIFEST_MAX_CHARS)


def _bounded(value: str, max_chars: int) -> str:
    """Cap a value at ``max_chars`` codepoints, marking any abridgement."""

    if max_chars <= 0:
        return ""
    if len(value) <= max_chars:
        return value
    if max_chars <= len(_ELLIPSIS):
        return _ELLIPSIS[:max_chars]
    return value[: max_chars - len(_ELLIPSIS)].rstrip() + _ELLIPSIS


def _bounded_bytes(value: str, max_bytes: int) -> str:
    """Cap a value in UTF-8 bytes, always cutting on a codepoint boundary."""

    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    marker = _ELLIPSIS.encode("utf-8")
    if max_bytes <= len(marker):
        return ""
    kept = encoded[: max_bytes - len(marker)].decode("utf-8", errors="ignore")
    return kept.rstrip() + _ELLIPSIS


def _compose_with_reserved_suffix(lead: str, suffix: str) -> str:
    """Put ``lead`` first while guaranteeing ``suffix`` survives the budget.

    The compact control envelope caps free prose at
    ``MAX_ENVELOPE_PROSE_BYTES`` and truncates the *tail*, which is exactly
    where the human-review requirement sits once a blocker leads. Composing
    without a reservation therefore turns a long blocker title into a way to
    delete "a human must review it" from the projection a routing consumer
    reads. The lead is shortened instead — and if the requirement alone fills
    the budget, it is published on its own, which is what the headline said
    before blockers ever led it.
    """

    if not suffix:
        return lead
    if not lead:
        return suffix
    room = MAX_ENVELOPE_PROSE_BYTES - len(suffix.encode("utf-8")) - 1
    if room <= 0:
        return suffix
    lead = _bounded_bytes(lead, room)
    if not lead:
        return suffix
    return f"{lead} {suffix}"


def _report_primary_headline(
    report: ReadinessReport,
    *,
    title_byte_budget: int | None = None,
) -> str:
    """The scan's own one-line verdict, with the blocking cause named.

    ``agent_summary.headline`` counts the blockers but does not say what they
    are, and a count is not a cause: "4 active finding(s) block release" reads
    the same whether the agent is missing a docstring or can move funds with no
    enforced control. The blocker title already carries the tool and the
    capability, so naming the worst one costs a clause and is the difference
    between a headline a triager can act on and one they have to open the
    report to understand.

    The title is normalized and bounded before it is quoted: it is scanned
    input, and an unbounded multiline value is how one finding's name becomes
    several lines of the artifact that reports it.

    ``title_byte_budget`` lets the caller shrink *this* clause when the whole
    headline is over budget. It is the right thing to give up first: it is the
    only untrusted material in the line, and every other part — the verdict,
    the evidence-gap context, the human-review requirement — is a sentence
    Shipgate wrote about its own run.
    """

    if report.agent_summary is not None:
        summary = _one_clause(report.agent_summary.headline)
    elif report.release_decision is not None:
        summary = _one_clause(report.release_decision.reason)
    else:
        return "Shipgate requires human review."
    worst = _worst_blocker(report.release_decision)
    if worst is None:
        return summary
    title = _bounded(_one_clause(worst.title).rstrip("."), _HEADLINE_TITLE_MAX_CHARS)
    if title_byte_budget is not None:
        title = _bounded_bytes(title, title_byte_budget)
    if not title:
        return summary
    return f"{summary} Most severe: {title}."


def _blockers_outrank_governance(release_decision: ReleaseDecision | None) -> bool:
    """Whether a release blocker should lead the headline over the notice.

    The self-approval prohibition is a real requirement, but it is not the
    most severe thing a reviewer needs to know when the same PR also blocks
    release on critical or high findings. Ranking the medium governance notice
    above them understated severity at exactly the moment attention is being
    allocated — and it did so most reliably for new adopters, whose first
    verdict always touches the trust root.

    Ordering only: the notice is appended, never dropped, so the human-review
    requirement survives in the same string, and no gating, control state, or
    permission is derived from this ranking.
    """

    if release_decision is None:
        return False
    return any(
        blocker.severity in _HEADLINE_LEADING_SEVERITIES
        for blocker in release_decision.blockers
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
    context_note: str | None = None,
) -> str | None:
    """The whole headline, composed once.

    ``context_note`` carries every later addition — today the evidence-gap
    provenance note — *into* this function rather than being appended to its
    result. Appending after composition silently spent the budget that
    ``_compose_with_reserved_suffix`` had reserved for the human-review
    requirement, so a long blocker title plus one gap note was enough to push
    the requirement past the compact control projection's limit and delete it
    from ``reason`` and ``human_review.why``. Context belongs in the lead; the
    requirement stays last and whole.
    """

    lead_context = _one_clause(context_note) if context_note else ""

    def _lead(primary: str) -> str:
        return f"{primary} {lead_context}" if lead_context else primary

    def _report_lead(source: ReadinessReport, suffix: str) -> str:
        """Build the lead in priority order, so the least load-bearing part yields.

        The verdict and the named blocking cause are why the headline was
        reordered in the first place; the evidence-gap provenance note is
        context about where the gaps came from. So the note is what shrinks
        when the budget is tight, and it is dropped rather than reduced to a
        stub. The reserved suffix is never touched by any of this.
        """

        primary = _report_primary_headline(source)
        if not lead_context:
            return primary
        room = MAX_ENVELOPE_PROSE_BYTES - len(suffix.encode("utf-8")) - 1
        budget = room - len(primary.encode("utf-8")) - 1
        # Below this there is no room for a sentence, only for an ellipsis
        # pretending to be one.
        context = _bounded_bytes(lead_context, budget) if budget >= 24 else ""
        return f"{primary} {context}" if context else primary

    # A failed scan has no adoption evidence to act on. Lead with the failure,
    # even if the pre-scan git proof found a newly added manifest.
    if head_status == "failed" or merge_verdict == "unknown":
        return _lead("Shipgate could not complete the scan; human review required.")
    # An adoption with another gating concern must lead with that real stop
    # condition. "Review, then merge" is only truthful when the adoption
    # finding is the sole review item.
    if manifest_introduced and not pure_adoption_review and report is not None:
        manifest = (
            f"the configured manifest {_manifest_label(configured_manifest)!r}"
            if configured_manifest
            else "the configured Shipgate manifest"
        )
        suffix = (
            f"This PR also introduces {manifest}; adopting a release "
            "policy is a separate human-review decision."
        )
        return _compose_with_reserved_suffix(_report_lead(report, suffix), suffix)
    # An agent editing the rules that evaluate its own change must see the
    # self-approval prohibition first, ahead of the generic scan headline.
    note = _self_approval_note(
        capability_review,
        manifest_introduced=manifest_introduced,
        pure_adoption_review=pure_adoption_review,
        configured_manifest=configured_manifest,
    )
    if note is not None:
        # Same ranking as the adoption branch above, for the repository that
        # has already adopted: the blocker leads and the prohibition follows.
        if report is not None and _blockers_outrank_governance(
            report.release_decision
        ):
            return _compose_with_reserved_suffix(_report_lead(report, note), note)
        return _compose_with_reserved_suffix(lead_context, note)
    if report is not None and report.agent_summary is not None:
        return _lead(_one_clause(report.agent_summary.headline))
    if head_status == "skipped":
        return _lead(
            "No agent-capability changes detected; Shipgate did not need to run."
        )
    return lead_context or None


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
    diff_status: VerifierDiffStatus,
    manifest_introduced: bool = False,
    pure_adoption_review: bool = False,
    configured_manifest: str | None = None,
) -> AgentControl:
    """Project verifier facts through the shared operational control engine."""

    # One fact, computed once, for every route below: did this run actually
    # read the change and reach a determination it can stand behind? Publishing
    # asserts something about an evaluated change, so no route may claim it
    # without all four parts — not the repair route, and not the human one.
    subject_evaluated = bool(
        execution == "succeeded"
        and release_decision is not None
        and release_decision.decision != "blocked"
        and diff_status.completeness == "complete"
    )

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
            publication_allowed=subject_evaluated,
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
            publication_allowed=subject_evaluated,
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

    # ``reason`` is the headline, which already leads with the actual blocker
    # and appends the self-approval prohibition whenever one outranks the
    # other — on a mixed adoption, and on any run whose blockers outrank the
    # governance notice. Replacing it with the bare trust-root copy there would
    # hide the condition that stopped the release from the one string the
    # human-review route carries.
    headline_carries_the_note = (
        manifest_introduced and not pure_adoption_review
    ) or _blockers_outrank_governance(release_decision)
    review_reason = reason
    if not headline_carries_the_note:
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
    if subject_evaluated:
        # The exact command that regenerates this evidence against the
        # committed refs, so the agent can commit, push, and republish without
        # inventing a rerun. ``fix_task`` is present on every non-mergeable
        # verdict; an absent one simply offers no command.
        rerun = fix_task.verification_command if fix_task is not None else None
        return derive_agent_control(
            reason=reason,
            next_action=HumanControlAction(kind="review", why=review_reason),
            verify_required=True,
            human_review_required=True,
            publication_allowed=True,
            allowed_next_commands=[rerun] if rerun else [],
            human_review_why=review_reason,
        )
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


_TRIGGER_INPUT_STATUS: dict[str, str] = {
    "complete": INPUT_COMPLETE,
    "partial": INPUT_PARTIAL,
    "unavailable": INPUT_UNAVAILABLE,
}


def _collect_diff(git_root: Path, base: str, head: str) -> DiffContext:
    """Collect a committed-ref diff, turning any surprise into a typed state."""

    try:
        return collect_diff_context(git_root, base, head)
    except Exception as exc:  # noqa: BLE001 - diff context degrades only.
        return _as_diff_context(exc)


def _as_diff_context(exc: Exception) -> DiffContext:
    """Represent an unexpected collection failure in the same typed vocabulary."""

    if isinstance(exc, DiffInputError):
        return exc.context
    return DiffContext(
        completeness="unavailable",
        reason="git_failed",
        detail=str(exc) or exc.__class__.__name__,
    )


_DIFF_COMPLETENESS_ORDER = {"complete": 0, "partial": 1, "unavailable": 2}


def _worst_diff_failure(
    failures: list[tuple[DiffContext, str]],
) -> tuple[DiffContext | None, str]:
    """Pick the one failure the artifact must report, with its repair target.

    Halves of a single change set are unioned, so the union is only as complete
    as its weakest half. Among equally incomplete halves the one a fetch cannot
    repair wins: authorizing another fetch against a deterministic failure is
    the loop this ordering exists to prevent.
    """

    if not failures:
        return None, ""
    return max(
        failures,
        key=lambda pair: (
            _DIFF_COMPLETENESS_ORDER[pair[0].completeness],
            0 if pair[0].fetch_repairable else 1,
        ),
    )


def _matched_diff_evidence(trigger: dict[str, Any]) -> bool:
    """Whether any matched rule was decided by the change set itself.

    ``force_run`` fires from the presence of a manifest, which is repository
    state rather than diff evidence. Separating the two keeps a headline from
    claiming a diff showed something when no diff was read.
    """

    return any(
        match.get("action") != TRIGGER_ACTION_FORCE_RUN
        for match in trigger.get("matched_rules", [])
        if isinstance(match, dict)
    )


def _diff_failure_headline(context: DiffContext | None) -> str | None:
    """Summarize a diff-input failure in the terms its control route uses.

    The generic failed-scan headline says "human review required" for every
    unknown verdict, which contradicts an artifact whose control state is
    ``agent_action_required`` with a ``fetch_base`` next action. The headline
    and the route are both derived from the same classified failure here.
    """

    if context is None:
        return None
    if context.fetch_repairable:
        return (
            f"Shipgate could not read the PR diff ({context.reason}); the "
            "history it needs is not available locally yet, so no verdict was "
            "reached. Make it available, then rerun verify."
        )
    return (
        f"Shipgate could not read the PR diff ({context.reason}); fetching "
        "cannot repair this, so no verdict was reached and a human must "
        "resolve the input."
    )


def _diff_status_artifact(context: DiffContext | None) -> VerifierDiffStatus:
    """Project one diff-acquisition attempt onto the verifier artifact."""

    context = context or DiffContext()
    return VerifierDiffStatus(
        completeness=context.completeness,
        reason=context.reason,
        detail=context.detail or None,
        remediation=context.remediation or None,
        fetch_repairable=context.fetch_repairable,
    )


def _trigger_input_status(context: DiffContext | None) -> str:
    if context is None:
        return INPUT_COMPLETE
    return _TRIGGER_INPUT_STATUS[context.completeness]


def _diff_failure_action(
    context: DiffContext, *, expects: str
) -> AgentControlAction:
    """Route a diff-input failure to the action that can actually repair it.

    A missing merge base or an unfetched partial-clone object is repaired by
    making history available locally — that is agent work, not review work.
    Everything else is a deterministic failure that fetching cannot touch, so
    it goes to a human with the Git diagnostic attached.
    """

    if context.fetch_repairable:
        return CodingAgentFetchBaseAction(
            kind="fetch_base",
            expects=expects,
            why=context.note,
        )
    return HumanControlAction(kind="review", why=context.note)


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
    diff_status: VerifierDiffStatus | None = None,
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
    resolved_diff_status = diff_status or VerifierDiffStatus()
    # The provenance note is passed *into* the composition, not appended to its
    # result: the reserved budget that keeps the human-review requirement whole
    # is only a guarantee if every later addition goes through it.
    headline = headline_override or _verifier_headline(
        report=report,
        merge_verdict=merge_verdict,
        head_status=head_status,
        capability_review=capability_review,
        manifest_introduced=manifest_introduced,
        pure_adoption_review=pure_adoption_review,
        configured_manifest=_display_path(config_path, git_root),
        context_note=_gap_provenance_note(report=report, base_report=base_report),
    )
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
        diff_status=resolved_diff_status,
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
        diff_status=resolved_diff_status,
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
                    # Guarded above: this overlay only ever applies to a
                    # succeeded review_required result, so the subject was read.
                    publication_allowed=True,
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
    operation: CurrentControlOperation = "verify",
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
    input_snapshot: StaticInputSnapshot | None = None,
    diff_text: str = "",
    diff_from_path: Path | None = None,
    authorization_path: Path | None = None,
    verification_options: dict[str, Any] | None = None,
    worktree_overlay_paths: list[str] | None = None,
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
        _publish_run_control(
            verifier=verifier,
            out_dir=verifier_path.parent,
            git_root=git_root,
            operation=operation,
        )
        return
    resolved_input_root = (input_root or git_root).resolve()
    active_snapshot = active_static_input_snapshot()
    original_paths = [
        *policy_pack_paths,
        *([baseline_path] if baseline_path is not None else []),
        *([diff_from_path] if diff_from_path is not None else []),
    ]
    def _finalize(snapshot: StaticInputSnapshot | None) -> None:
        """Seal one snapshot, rejecting any input that moved under it."""

        if snapshot is None:
            return
        try:
            # Base-report enrichment can be generated after the snapshot was
            # activated. Capture it now, before finalizing directory identity,
            # so plan construction and the receipt consume exactly these bytes.
            for path in original_paths:
                if path.is_file() and snapshot.contains(path) and not snapshot.has(path):
                    snapshot.read_bytes(path, max_bytes=64 * 1024 * 1024)
            snapshot.finish()
        except (OSError, ValueError) as exc:
            raise InputParseError(
                f"Verification inputs changed while they were being evaluated: {exc}"
            ) from exc

    _finalize(active_snapshot)
    original_static_inputs = {
        Path(os.path.abspath(os.path.normpath(os.fspath(path))))
        for path in original_paths
    }
    # Identity must rest on what the adapters actually read, which is the only
    # account that reaches an input discovered while parsing an entrypoint
    # rather than named in the manifest. Two snapshots observe two roots: the
    # outer one is bound to the worktree, and a committed-tree run is observed
    # by ``input_snapshot``, bound to the archived tree it scanned. Pick the one
    # that watched the evaluated root; fall back to the manifest's declared
    # inputs only when neither did (issue #299).
    archived_head = resolved_input_root != git_root.resolve()
    evaluated_snapshot = input_snapshot if archived_head else active_snapshot
    if evaluated_snapshot is not active_snapshot:
        _finalize(evaluated_snapshot)
    captured_input_paths = (
        [
            path
            for path in evaluated_snapshot.paths()
            # Blob paths are logical and relative to the evaluated input root,
            # so a read outside it cannot become one. In practice this only
            # drops the externally supplied inputs already bound by name.
            if path not in original_static_inputs
            and (resolved_input_root in path.parents)
        ]
        if evaluated_snapshot is not None
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
    # Hash the bytes the adapters read, not whatever is on disk now. Without
    # this the plan's path list and its blob hashes come from two different
    # instants, so a file rewritten after the scan is attested at its new
    # content while ``tool_sources`` still lists what the old content pointed
    # at — a receipt for bytes the report never evaluated.
    plan_snapshot_token = (
        activate_static_input_snapshot(evaluated_snapshot)
        if evaluated_snapshot is not None and evaluated_snapshot is not active_snapshot
        else None
    )
    try:
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
                tree_sha(git_root, verifier.base_ref)
                if verifier.base_ref and base_commit
                else None
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
            worktree_overlay_paths=worktree_overlay_paths,
            external_input_root=external_input_root,
            captured_input_paths=captured_input_paths,
        )
    finally:
        if plan_snapshot_token is not None:
            reset_static_input_snapshot(plan_snapshot_token)
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
        _publish_run_control(
            verifier=verifier,
            out_dir=verifier_path.parent,
            git_root=git_root,
            operation=operation,
        )
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
    # Terminal receipt is written last of the evidence artifacts. Its presence
    # means every referenced artifact existed and was hashed after final
    # serialization.
    receipt_path = verifier_path.with_name("verification-receipt.json")
    receipt_path.write_text(
        json.dumps(receipt.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _publish_run_control(
        verifier=verifier,
        out_dir=verifier_path.parent,
        git_root=git_root,
        operation=operation,
    )


def _publish_run_control(
    *,
    verifier: VerifierArtifact,
    out_dir: Path,
    git_root: Path,
    operation: CurrentControlOperation,
) -> None:
    """Publish the control pointer as the last visible file of a run.

    Called at every exit of :func:`_write_artifacts`, including the ones that
    never reach a terminal receipt: an in-progress marker that is never
    replaced would keep an otherwise usable diagnostic run looking like a crash.
    Anything that raises before this point deliberately leaves the in-progress
    marker current, which denies every cached decision.
    """

    publish_current_control(
        out_dir,
        operation=operation,
        control=project_agent_control(
            verifier.control,
            operation=operation,
            receipt_bound=(out_dir / "verification-receipt.json").is_file(),
        ),
        request_id=verifier.request_id,
        decision_id=verifier.decision_id,
        workspace_identity=_current_control_workspace_identity(
            out_dir=out_dir,
            git_root=git_root,
            verifier=verifier,
        ),
        # A preview never runs a scan, so report.json and packet.json in this
        # directory belong to some earlier run.  Binding them would present two
        # generations as one current artifact set.
        artifact_keys=(
            VERIFIER_ROUTE_CONTROL_ARTIFACT_KEYS if operation == "preview" else None
        ),
    )


def _current_control_workspace_identity(
    *,
    out_dir: Path,
    git_root: Path,
    verifier: VerifierArtifact,
) -> CurrentControlWorkspaceIdentity:
    """Bind what this run was evaluated against.

    The verification plan is the authoritative source when the run produced
    one, because that is the same subject the receipt closes over.  Runs that
    stopped before plan construction fall back to the verifier's coarser view.

    That fallback binds *this worktree's* HEAD, which is deliberately not
    ``head_ref``: a preview reads project markers from the working tree, so the
    tree it actually read is the one a later reader has to still be sitting in.
    Binding nothing left the pointer permanently current, and a preview that
    asked for a checkout kept answering ``agent_action_required`` with the same
    ``current_control_id`` after the caller performed it — the refresh entry
    point reported the request as still outstanding, which is how a
    refresh-driven controller repeats an action forever (#397 review).

    HEAD is not enough on its own, because the evidence a preview routes on is
    frequently *uncommitted*: a route selected from an untracked ``agent.py``
    survived that file being deleted, since neither HEAD nor its tree moved. So
    the overlay of the paths that differ from HEAD is bound too, and the
    pointer declares itself a worktree snapshot. The path set is derived from
    the live worktree on both sides rather than recorded — the reader excludes
    the same reports directory this run wrote into, because that is the
    directory it was pointed at to find the pointer at all.
    """

    plan_path = out_dir / "verification-plan.json"
    if plan_path.is_file() and not plan_path.is_symlink():
        try:
            plan = VerificationPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            plan = None
        if plan is not None:
            return workspace_identity_from_plan(plan)
    bound, overlay = _safe_worktree_overlay(git_root, exclude=out_dir)
    return CurrentControlWorkspaceIdentity(
        repository=_safe_repository_identity(git_root),
        head_ref=verifier.head_ref,
        head_commit_sha=_safe_worktree_sha(git_root, commit_sha),
        # An evaluated tree, when the run recorded one, is what this answer
        # describes; otherwise the worktree's own HEAD tree is.
        head_tree_sha=verifier.head_tree_sha or _safe_worktree_sha(git_root, tree_sha),
        snapshot_kind="worktree_overlay" if bound else None,
        worktree_overlay_sha256=overlay,
    )


def _safe_worktree_overlay(git_root: Path, *, exclude: Path) -> tuple[bool, str | None]:
    """The overlay of everything that differs from HEAD right now.

    Returns ``(bound, digest)``. The pair is needed because ``None`` is a
    *value* here and not only a failure: a clean worktree has no overlay rows,
    and the reader spells that as ``None`` too, so the two must agree. Reporting
    the clean case as unbound instead would leave the pointer with nothing to
    validate, and a later edit invisible — which is the state this is fixing.

    ``bound=False`` is the genuinely unknown case, and it declares no snapshot
    kind at all rather than manufacturing currency from an unreadable tree.
    """

    try:
        changed, _ = working_tree_context(git_root, exclude=exclude)
        rows = worktree_overlay(git_root, list(changed))
    except Exception:  # noqa: BLE001 - pointer identity is best-effort here.
        return (False, None)
    # The reader's own rule, so an empty overlay compares equal on both sides.
    return (True, content_id(rows) if rows else None)


def _safe_worktree_sha(
    git_root: Path, resolve: Callable[[Path, str], str | None]
) -> str | None:
    """This worktree's HEAD identity, or ``None`` outside a readable repository."""

    try:
        return resolve(git_root, "HEAD")
    except Exception:  # noqa: BLE001 - pointer identity is best-effort here.
        return None


def _safe_repository_identity(workspace: Path) -> str | None:
    """Resolve the credential-free repository locator, or ``None`` outside Git."""

    try:
        return repository_identity(workspace)
    except Exception:  # noqa: BLE001 - identity is advisory on this surface.
        return None


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


def _resolve_out_dir(
    *, git_root: Path, requested_workspace: Path, out: Path | None
) -> Path:
    """Where this run writes its artifacts.

    The *default* follows the workspace the caller named, not the repository
    root. With one manifest per project — the layout this release makes
    routable — root-shared reports mean project A's run replaces project B's
    control pointer, and `init`'s managed ignore lands in the project while
    the reports it is meant to cover land somewhere else (#363 review).

    An explicit ``--out`` keeps resolving against the repository root, so
    every existing invocation that names a directory still writes exactly
    where it wrote before.
    """

    if out is not None:
        return _resolve_under_workspace(git_root, out)
    return _resolve_under_workspace(requested_workspace, DEFAULT_OUT_DIR)


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


def _preview_init_command(workspace: Path, *, scope: ChangeScope | None = None) -> str:
    command_workspace = _preview_command_workspace(workspace, scope=scope)
    return retarget_command(
        _shell_join(
            [
                "shipgate",
                "init",
                "--workspace",
                str(command_workspace),
                "--write",
                "--json",
            ]
        )
    )


def _scoped_manifest_path(directory: Path, name: str) -> Path | None:
    """The manifest in ``directory``, when one exists that verify would accept.

    ``Path.is_file()`` follows symlinks, and the verifier refuses a manifest
    path with symlink components. Routing to a symlinked manifest would
    promise a ``verify`` command that exits 2 (#363 review).
    """

    candidate = directory / name
    try:
        if candidate.is_symlink() or not candidate.is_file():
            return None
    except OSError:  # pragma: no cover - unreadable directory
        return None
    return candidate


@dataclasses.dataclass(frozen=True)
class _HeadPosition:
    """Where the evaluated head sits relative to this worktree, and what it is.

    ``commit`` is the immutable id the requested ref resolved to, and it is the
    whole reason this is a pair rather than a boolean. ``--head`` accepts
    revision expressions: ``HEAD~1`` names one commit before the recovery's
    checkout and a different one after it, so a route spelled with the
    *expression* walks history backwards one commit per iteration instead of
    terminating — and a ``HEAD``-relative ``--base`` silently re-ranges the
    same way (#397 review). Only a commit id says the same thing on both sides
    of the checkout it asks for.

    ``None`` when the ref could not be resolved at all. Preview never reaches
    the scope routing in that state — an unreadable ref is a diff-input
    failure, which outranks it — so the routes below fall back to the ref's own
    spelling rather than inventing an id.
    """

    matches_worktree: bool
    commit: str | None = None


def _head_position(root: Path, head: str | None) -> _HeadPosition:
    """Resolve the evaluated head against the commit this worktree holds.

    Manifest-scope evidence is read from the working tree, so a preview of
    some *other* ref would authorize a directory the diff never described.
    An absent ``--head`` is the working tree by definition.
    """

    if head is None:
        return _HeadPosition(matches_worktree=True)
    try:
        head_sha = commit_sha(root, head)
        worktree_sha = commit_sha(root, "HEAD")
    except Exception:  # noqa: BLE001 - preview must never crash.
        return _HeadPosition(matches_worktree=False)
    return _HeadPosition(
        matches_worktree=head_sha is not None and head_sha == worktree_sha,
        commit=head_sha,
    )


def _pinned_ref(root: Path, ref: str | None) -> str | None:
    """``ref`` as a short commit id, for a command that must survive a checkout."""

    if ref is None:
        return None
    try:
        resolved = commit_sha(root, ref)
    except Exception:  # noqa: BLE001 - preview must never crash.
        return None
    return resolved[:12] if resolved else None


@dataclasses.dataclass(frozen=True)
class _PreviewScope:
    """Which project the diff belongs to, and why it could not be established.

    ``cause`` is what selects the recovery. It is not derivable from
    ``resolution`` alone: ``not_evaluated`` covers a capped probe, evidence the
    diff deleted, and a head that is not this worktree, and the honest next
    step differs for each (#399 review).

    ``head`` and the two pinned commit ids are carried beside the cause that
    names them rather than read again at the route: the ``head_mismatch``
    recovery *is* "check this commit out, then re-run against it", so the refs
    and the cause have to be produced together or the two can drift (#397).
    The ids are what make that recovery terminate — see :class:`_HeadPosition`.
    """

    resolution: ScopeResolution
    cause: str | None = None
    python_file_total: int = 0
    head: str | None = None
    head_commit: str | None = None
    base_commit: str | None = None


def _preview_scope(
    *,
    root: Path,
    changed_files: list[str],
    limit: Path | None,
    head: str | None,
    base: str | None = None,
) -> _PreviewScope:
    """Which project the changed paths belong to, for the routing below.

    Project markers are read from the working tree, because that is what
    ``init`` will run against. When the evaluated head is some other commit
    the two disagree — the same refs would recommend different directories
    depending on what happens to be checked out — so no scope is claimed.

    ``evidence_dirs`` is what lets the resolver see a project whose whole
    boundary is a ``requirements.txt`` beside an ``agent.py``. Without it the
    walk climbs past such a project to the repository root, nothing resolves,
    and routing emits the root ``init`` that ``init`` refuses
    deterministically (#394).

    A directory the evidence probe could not settle is not a directory
    without a project. Spending it as one would put the workspace root back
    in the same routing slot the missing evidence took it out of, so an
    unsettled probe is reported as ``not_evaluated`` and routes to discovery
    (#399 review).
    """

    position = _head_position(root, head)
    if not position.matches_worktree:
        pinned = position.commit[:12] if position.commit else None
        return _PreviewScope(
            resolution=ScopeResolution(
                status="not_evaluated",
                detail=(
                    f"the evaluated head {head!r}"
                    + (f" ({pinned})" if pinned else "")
                    + " is not the commit this worktree has checked out, and "
                    "project markers are read from the worktree that init "
                    "would run against"
                ),
            ),
            cause="head_mismatch",
            head=head,
            head_commit=pinned,
            base_commit=_pinned_ref(root, base),
        )
    evidence = weak_marker_evidence_dirs(root, changed_files)
    if evidence.undetermined:
        causes = evidence.causes
        # Deleted evidence outranks a capped probe: raising a bound cannot
        # find a file the change removed, so a retry would settle the wrong
        # question confidently.
        cause = (
            "deleted_evidence"
            if "deleted_evidence" in causes
            else ("unreadable_inventory" if "unreadable_inventory" in causes else "parse_budget")
        )
        return _PreviewScope(
            resolution=ScopeResolution(
                status="not_evaluated", detail=evidence.detail
            ),
            cause=cause,
            python_file_total=evidence.python_file_total,
        )
    return _PreviewScope(
        resolution=resolve_change_scope(
            root=root,
            changed_files=changed_files,
            limit=limit,
            evidence_dirs=evidence.directories,
        )
    )


def _unresolved_scope_route(
    *, scope: _PreviewScope, workspace: Path
) -> tuple[AgentControlAction, str]:
    """The recovery for a change whose project could not be established.

    The default is discovery, and for most unresolved states that is the
    right and sufficient answer. It is not always: routing every cause to
    one generic ``detect`` published a recovery that reproduces the failure
    it is recovering from (#399 review). A ``detect`` at the same cap hits
    the same cap, and a ``detect`` of the head tree cannot see the evidence
    this change deleted — it reports the surviving project as the workspace's
    single scope and its ``init`` writes a manifest for an agent the pull
    request never touched.

    So the cause chooses the route. A cap is a mechanical, read-only retry
    and gets a concrete command with a bound that reaches every file.
    Deleted evidence is a question no read-only command can answer, so it
    gets a human route and no command at all — publishing one here would be
    publishing a step that cannot take.

    A head that is not this worktree is neither. Nothing about the change is
    in doubt there: the missing input is a *working tree* holding the commit
    under review, and producing it is one mechanical step the caller owns.
    Shipgate never writes to a caller's worktree, so it cannot spell that
    step as a command it runs — which is exactly what ``fetch_base`` is for.
    Naming the input and letting the caller produce it keeps the loop moving;
    routing it to a human published ``must_stop`` and ``command: null`` for a
    state the coding agent clears itself, and the remedy — derivable from the
    ref preview was handed — went unsaid (#397).
    """

    resolution = scope.resolution
    if scope.cause == "head_mismatch":
        # ``head_mismatch`` is only produced for a non-None ``--head``: an
        # absent one is the working tree by definition. The commit id is
        # preferred over the caller's spelling everywhere below, because the
        # step being asked for *moves HEAD*: `--head HEAD~1` names one commit
        # before the checkout and its parent after, so the expression form
        # walks history backwards forever instead of resolving (#397 review).
        target = scope.head_commit or scope.head or "the evaluated head"
        if scope.head_commit is None:
            # Nothing resolved, so there is no id to pin to and no honest way
            # to promise the rerun means the same thing afterwards. Say what is
            # missing and stop there rather than printing an instruction that
            # contradicts the sentence next to it.
            rerun = None
        else:
            rerun = f"--head {target}"
            if scope.base_commit is not None:
                # A `HEAD`-relative base re-ranges across the same checkout,
                # which is quieter than the loop and worse: the rerun succeeds
                # while evaluating a diff nobody asked for.
                rerun = f"--base {scope.base_commit} {rerun}"
        return (
            CodingAgentFetchBaseAction(
                kind="fetch_base",
                # The whole recovery, in the one field the envelope never
                # truncates: `truncate_prose` caps `why` at 400 bytes and
                # leaves `expects` alone, so the immutable ids survive a ref
                # name long enough to push everything else out. A consumer
                # that re-derived the rerun from the caller's own
                # `HEAD`-relative request would rebuild the backward walk
                # this route exists to end (#397 review).
                expects=(
                    f"commit {target} checked out in this worktree"
                    + (f", then this preview re-run with {rerun}" if rerun else "")
                ),
                # The instruction leads for the same reason: what gets cut is
                # the diagnosis, never the step.
                why=(
                    f"Check {target} out in this worktree, then re-run this "
                    + (f"preview with {rerun}. " if rerun else "preview. ")
                    + "The project this change belongs to could not be "
                    f"established ({resolution.detail}); discovery of the "
                    "worktree as it stands answers about a different tree, and "
                    "a revision expression re-resolves against the new HEAD."
                ),
            ),
            f"The evaluated head {target} is not checked out here, so no "
            "project could be named; check it out and re-run this preview "
            "against that commit.",
        )
    if scope.cause in ("deleted_evidence", "unreadable_inventory"):
        return (
            HumanControlAction(
                kind="review",
                # The reason `detect` is not offered here is *not* that it
                # would read some other tree — both causes are produced only
                # after the head was confirmed to be this worktree. It is that
                # the evidence is missing from the tree everything can read, so
                # discovery reports the surviving project as the single scope
                # and its `init` adopts an agent this change never touched.
                why=(
                    "The project this change belongs to could not be "
                    f"established ({resolution.detail}), and no read-only "
                    "command settles it: the evidence is missing from the tree "
                    "this run reads, so discovery would report whatever "
                    "survived as the workspace's single scope. Decide from the "
                    "change itself which project it belongs to and initialize "
                    "that directory by name; initializing the workspace root "
                    "would adopt a scope nobody chose."
                ),
            ),
            "Shipgate could not establish which project this change belongs "
            "to, and no command settles it; a human decides here.",
        )
    if scope.cause == "parse_budget":
        return (
            CodingAgentCommandAction(
                kind="discover",
                command=_preview_detect_command(
                    workspace, max_python_files=scope.python_file_total
                ),
                why=(
                    "The project this change belongs to could not be "
                    f"established ({resolution.detail}), so initializing the "
                    "workspace root would adopt a scope nobody chose. This "
                    "command raises the cap to cover every Python file in the "
                    "workspace, so it settles what the capped pass could not; "
                    "then initialize the project this change belongs to."
                ),
            ),
            "Shipgate stopped at its parse budget before it could tell which "
            "project this change belongs to; re-run discovery uncapped.",
        )
    return (
        CodingAgentCommandAction(
            kind="discover",
            command=_preview_detect_command(workspace),
            why=(
                "The project this change belongs to could not be established "
                f"({resolution.detail}), so initializing the workspace root "
                "would adopt a scope nobody chose. Read the project list, then "
                "initialize the project this change belongs to."
            ),
        ),
        "Shipgate could not establish which project this change belongs to; "
        "discover the projects before setting one up.",
    )


def _preview_detect_command(workspace: Path, *, max_python_files: int = 0) -> str:
    command_workspace = _preview_command_workspace(workspace, scope=None)
    parts = ["shipgate", "detect", "--workspace", str(command_workspace)]
    if max_python_files > 0:
        parts.extend(["--max-python-files", str(max_python_files)])
    parts.append("--json")
    return retarget_command(_shell_join(parts))


def _preview_command_workspace(workspace: Path, *, scope: ChangeScope | None) -> Path:
    """Absolute workspace to spell into an emitted command.

    Without a narrowed scope this keeps the caller's own spelling of
    ``--workspace``, so the routed command stays the one they would have
    written themselves.
    """

    if scope is not None:
        return scope.directory
    return workspace if workspace.is_absolute() else Path.cwd() / workspace


def _preview_verify_command(
    *,
    workspace: Path,
    config: Path,
    base: str | None,
    head: str | None,
    out: Path | None,
    pr_comment_style: str = "capability-review",
    preview: bool = False,
    scope: ChangeScope | None = None,
) -> str:
    command_workspace = _preview_command_workspace(workspace, scope=scope)
    # A scoped run reads the manifest that was found *in* that directory,
    # so the config spelling narrows with the workspace.
    command_config = Path(config.name) if scope is not None else config
    parts = [
        "agents-shipgate",
        "verify",
        "--workspace",
        str(command_workspace),
        "--config",
        str(command_config),
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
    return retarget_command(_shell_join(parts))


@contextlib.contextmanager
def _without_github_step_summary():
    prior = os.environ.pop("GITHUB_STEP_SUMMARY", None)
    try:
        yield
    finally:
        if prior is not None:
            os.environ["GITHUB_STEP_SUMMARY"] = prior


@owns_current_control("preview")
def run_preview(
    *,
    workspace: Path,
    config: Path,
    base: str | None,
    head: str | None,
    out: Path | None,
    pr_comment_style: str = "capability-review",
    auto_base: bool = False,
) -> tuple[VerifierArtifact, None, int]:
    """Lightweight relevance check for ``agents-shipgate verify --preview``.

    ``auto_base`` mirrors the verifier: with no explicit ``--base`` and
    detection enabled, the default branch is discovered the same way. The
    promoted adoption command is the bare ``verify --preview --json``, so
    without it the preview that every first adoption runs would evaluate an
    empty change set — no relevance evidence, and no way to tell which
    project a monorepo pull request is about (#363).
    """
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
    out_dir = _resolve_out_dir(
        git_root=root, requested_workspace=requested_root, out=out
    )
    _reject_output_input_overlap(
        git_root=root,
        out_dir=out_dir,
        inputs=[("config", config_path)],
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    # Preview is a non-terminal operation, so it starts by denying whatever a
    # previous run left behind rather than leaving it current beside a preview.
    begin_current_control(
        out_dir,
        operation="preview",
        reason=(
            "A verification preview is in progress; no decision in this "
            "directory is current until it publishes one."
        ),
        repository=_safe_repository_identity(root),
    )
    clear_verifier_route_artifacts(out_dir)
    verifier_path = out_dir / "verifier.json"
    verify_run_path = out_dir / "verify-run.json"
    agent_handoff_path = out_dir / "agent-handoff.json"
    pr_comment_path = out_dir / "pr-comment.md"
    manifest_present = config_path.is_file()

    changed_files: list[str] = []
    diff_text = ""
    notes: list[str] = []
    diff_input: DiffContext | None = None
    if base is None and auto_base:
        try:
            detection = detect_default_base_with_notes(root, head or "HEAD")
        except Exception:  # noqa: BLE001 - preview must never crash.
            detection = None
        if detection is not None:
            notes.extend(detection.notes)
            if detection.base is not None:
                base = detection.base
                notes.append(
                    f"Auto-detected base {detection.base!r} for diff context; "
                    "pass --base to override or --no-base to disable."
                )
    if base:
        try:
            git_root = ensure_git_workspace(root)
            collected = _collect_diff(git_root, base, head or "HEAD")
        except Exception as exc:  # noqa: BLE001 - preview must never crash.
            collected = _as_diff_context(exc)
        changed_files = list(collected.changed_files)
        diff_text = collected.diff_text
        if collected.completeness != "complete":
            diff_input = collected
            notes.append(f"Preview diff unavailable: {collected.note}")

    # The action preview emits runs against the *worktree*, so preview has to
    # see the same effective change set the full verifier would: committed
    # range unioned with uncommitted and untracked work. Without this an
    # uncommitted-only capability change reads as an empty diff, and the empty
    # diff reads as "nothing here narrows the scope" (#363 review). Skipped
    # when an explicit --head names another tree, which is the one case where
    # the worktree is not what is being evaluated — verify archives the head
    # there for the same reason.
    if head is None:
        try:
            # The merge base, not the base ref: comparing against the tip of
            # the base branch would report its own commits as this change.
            comparison_ref = (
                require_merge_base_sha(root, base, "HEAD") if base else "HEAD"
            )
            worktree_paths, worktree_diff = working_tree_context(
                root,
                comparison_ref=comparison_ref,
                exclude=out_dir,
            )
        except Exception:  # noqa: BLE001 - preview must never crash.
            worktree_paths, worktree_diff = [], ""
            notes.append(
                "Preview could not read the working tree; the scope below "
                "rests on committed evidence alone."
            )
        if worktree_paths or worktree_diff:
            if base and diff_input is None:
                # One comparison, exactly as the verifier does it: overlapping
                # paths must appear once at their effective content, not twice.
                changed_files = list(worktree_paths)
                diff_text = worktree_diff
            else:
                changed_files = _dedupe_paths([*changed_files, *worktree_paths])
                diff_text = _join_diff_text(diff_text, worktree_diff)

    # The changed paths already say which project this pull request is
    # about. Routing setup to the workspace root instead would hand a
    # monorepo one manifest for every agent in it, so the adoption command
    # below is scoped to the project the diff actually touches (#363).
    #
    # Project markers are read from the working tree, because that is what
    # `init` will run against. When the evaluated head is some other commit,
    # the two disagree — the same refs would recommend different directories
    # depending on what happens to be checked out — so no scope is claimed.
    preview_scope = _preview_scope(
        root=root,
        changed_files=changed_files,
        limit=requested_root,
        head=head,
        base=base,
    )
    resolution = preview_scope.resolution
    scope = resolution.scope
    scoped_config = (
        _scoped_manifest_path(scope.directory, config_path.name)
        if scope is not None
        else None
    )
    scoped_manifest_present = scoped_config is not None
    # Which of the contested projects already carry their own manifest. A
    # change spanning two configured projects has two governance boundaries
    # and no single command that honors both; a root manifest is not a
    # substitute for either (#363 review).
    contested_configured = tuple(
        relative
        for relative in resolution.contested
        if _scoped_manifest_path(
            root if relative == "." else root / relative, config_path.name
        )
        is not None
    )

    trigger = evaluate(
        paths=changed_files,
        diff_text=diff_text,
        # A manifest one directory down is an opt-in too. Reading only the
        # workspace root reported an adopted monorepo project as unadopted,
        # so a docs-only change to it skipped the gate its own manifest asks
        # for (#363). One resolver answers this for preview and for the
        # `trigger` command, so the two cannot disagree about what "the repo
        # already opted in" means.
        manifest_present=manifest_present
        or manifest_opt_in(root, changed_paths=changed_files, name=config_path.name),
        user_requested=True,
        input_status=_trigger_input_status(diff_input),
    )

    # Trigger previews may recommend detect/init as a generic recovery path.
    # Verify preview deliberately returns the exact one-shot init command that
    # installs the local contract, default agent kit, and advisory CI workflow
    # for unconfigured workspaces so cold-start agents do not need to infer the
    # next command from README prose.
    init_command = _preview_init_command(workspace, scope=scope)
    verify_command = _preview_verify_command(
        workspace=workspace,
        config=config,
        base=base,
        head=head,
        out=out,
        pr_comment_style=pr_comment_style,
    )
    scoped_verify_command = _preview_verify_command(
        workspace=workspace,
        config=config,
        base=base,
        head=head,
        out=out,
        pr_comment_style=pr_comment_style,
        scope=scope,
    )
    scope_note = (
        (
            f"Every changed path that carries a capability surface belongs to "
            f"{scope.relative}, which is its own project root ({scope.marker}); "
            "a manifest at the workspace root would instead cover every "
            "unrelated project in this repository."
        )
        if scope is not None
        else ""
    )

    # A diff Shipgate could not read outranks every adoption route below it,
    # whether or not this workspace has a manifest. Falling through to "Shipgate
    # is not configured here" would answer a question nobody asked and bury the
    # fact that the PR was never inspected — and an unadopted repo reached over a
    # shallow or blobless clone is exactly where this failure lands.
    if diff_input is not None:
        next_action: AgentControlAction = _diff_failure_action(
            diff_input,
            expects=(
                f"{base}...{head or 'HEAD'}"
                if base
                else (head or "the requested base and head refs")
            ),
        )
        read = (
            "could only partly read"
            if diff_input.completeness == "partial"
            else "could not read"
        )
        # Partial evidence can still carry a sound run verdict — a matched path
        # rule needs no diff body — and the evaluator publishes it. Saying "no
        # relevance verdict was reached" alongside `should_run: true` would make
        # the headline contradict the artifact it summarizes. But the run may
        # rest on evidence that has nothing to do with the diff: an adopted
        # repository force-runs on the manifest alone, with no paths read at
        # all, so naming the paths there would attribute the verdict to
        # evidence that does not exist.
        if not trigger.get("run_shipgate"):
            outcome = "no relevance verdict was reached"
        elif _matched_diff_evidence(trigger):
            outcome = (
                "the change it did read already shows an agent-capability "
                "surface, so relevance is established; recover the full diff "
                "before trusting any merge verdict"
            )
        else:
            outcome = (
                "this workspace is already configured for Shipgate, so "
                "verification must run regardless; recover the full diff "
                "before trusting any merge verdict"
            )
        headline = (
            f"Shipgate preview {read} the requested PR diff "
            f"({diff_input.reason}); {outcome}."
        )
    elif scope is not None and scoped_manifest_present:
        # The changed project carries its own manifest, so that is the gate
        # for this diff — ahead of any root manifest, which governs a
        # different boundary and would answer for code this PR never touched.
        # It also breaks a loop when the root has none: init refuses to
        # overwrite the manifest that already exists one directory down.
        next_action = CodingAgentCommandAction(
            kind="verify",
            command=scoped_verify_command,
            why=(
                f"Every changed path that carries a capability surface belongs "
                f"to {scope.relative}, which has its own shipgate.yaml; run "
                "verify there on the PR diff. That manifest, not the "
                "workspace root, is the governance boundary for this change."
            ),
        )
        headline = (
            f"Shipgate is configured for the changed project ({scope.relative}); "
            "run verify there to get a merge verdict."
        )
    elif contested_configured:
        # The change spans projects that are already configured. Each carries
        # its own gate and no single command honors both, so a root manifest
        # must not stand in for either: it would answer for a boundary this
        # change never crossed (#363 review).
        listed = ", ".join(resolution.contested)
        # One command cannot honor two gates, and the control contract will
        # not let a routing surface authorize two. Naming them in prose and
        # stopping is the honest form of "each of these is its own boundary".
        commands = "; ".join(
            _preview_verify_command(
                workspace=workspace,
                config=config,
                base=base,
                head=head,
                out=out,
                pr_comment_style=pr_comment_style,
                scope=ChangeScope(
                    directory=root / relative,
                    relative=relative,
                    marker=config_path.name,
                ),
            )
            for relative in contested_configured
        )
        next_action = HumanControlAction(
            kind="review",
            why=(
                f"This change spans {len(resolution.contested)} self-contained "
                f"projects ({listed}), and {len(contested_configured)} of them "
                "carry their own shipgate.yaml. Each is a separate governance "
                "boundary and no single run covers both, so a human decides "
                "here: run every one of these and read the results together — "
                f"{commands} — rather than letting one manifest answer for all."
            ),
        )
        headline = (
            f"This change spans {len(resolution.contested)} projects, "
            f"{len(contested_configured)} of them separately configured; "
            "each is its own gate."
        )
    elif manifest_present:
        next_action = CodingAgentCommandAction(
            kind="verify",
            command=verify_command,
            why="Shipgate is already set up here; run verify on the PR diff.",
        )
        headline = "Shipgate is configured; run verify on the PR to get a merge verdict."
    elif resolution.contested:
        # Several projects own part of this change. `init` at the root would
        # refuse deterministically, so recommending it would be recommending
        # a failure; ask discovery to name the projects instead.
        contested = ", ".join(resolution.contested)
        next_action = CodingAgentCommandAction(
            kind="discover",
            command=_preview_detect_command(workspace),
            why=(
                f"This change spans {len(resolution.contested)} self-contained "
                f"projects ({contested}), so no single manifest describes it "
                "and initializing the workspace root would refuse. Read the "
                "project list, then initialize the one this change belongs to."
            ),
        )
        headline = (
            f"This change spans {len(resolution.contested)} projects; "
            "choose the one to set up before verifying."
        )
    elif resolution.unresolved:
        # "Shipgate could not tell which project this is" is not permission to
        # write a manifest for whichever agent happens to be in the current
        # checkout (#363 review). Which recovery says so depends on why it
        # could not tell, so the route is chosen from the cause.
        next_action, headline = _unresolved_scope_route(
            scope=preview_scope, workspace=workspace
        )
    elif trigger.get("should_run") or trigger.get("dry_run_recommended"):
        next_action = CodingAgentCommandAction(
            kind="initialize",
            command=init_command,
            why=(
                "This unconfigured workspace looks agent-related; initialize "
                "the local Shipgate contract and advisory agent workflow."
                + (f" {scope_note}" if scope_note else "")
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
                + (f" {scope_note}" if scope_note else "")
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
        diff_status=_diff_status_artifact(diff_input),
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
        # A preview is never a merge decision.  Scoping the pointer to the
        # preview operation makes "complete" unrepresentable for this run, so
        # an agent cannot read a preview as authorization to finish.
        operation="preview",
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
