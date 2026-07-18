from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shlex
import shutil
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from agents_shipgate import __version__
from agents_shipgate.checks.verify import PROTECTED_FILE_EDITS
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
from .fix_task import FORBIDDEN_SHORTCUTS, build_fix_task
from .git import (
    active_replace_refs,
    archive_tree,
    commit_date,
    commit_sha,
    detect_default_base_with_notes,
    diff_context,
    ensure_git_workspace,
    git_path,
    merge_base_sha,
    read_file_at_ref,
    ref_exists,
    repository_identity,
    resolve_source_head_identity,
    tree_sha,
    working_tree_context,
)

HEAD_FORMATS = ["markdown", "json", "sarif"]
# Verify owns the PR artifact contract and writes packet.json only; the
# reviewer-facing Markdown surface is pr-comment.md.
HEAD_PACKET_FORMATS = ["json"]
DEFAULT_OUT_DIR = Path("agents-shipgate-reports")
BASE_CACHE_KEEP_ENTRIES = 16
MAX_HUMAN_AUTHORIZATION_BYTES = 1024 * 1024


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
    config_path = _resolve_under_workspace(git_root, config)
    config_relative = _relative_to_workspace(git_root, config_path, "--config")
    out_dir = _resolve_under_workspace(git_root, out or DEFAULT_OUT_DIR)
    baseline_path = _resolve_under_workspace(git_root, baseline) if baseline else None
    policy_pack_paths = (
        [_resolve_under_workspace(git_root, path) for path in policy_packs]
        if policy_packs
        else None
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    _clear_trusted_handoff(out_dir)
    verifier_path = out_dir / "verifier.json"
    verify_run_path = out_dir / "verify-run.json"
    pr_comment_path = out_dir / "pr-comment.md"

    if not config_path.is_file():
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
                command="agents-shipgate verify --preview --json",
                why=(
                    "Shipgate could not find the configured manifest; run verify "
                    "preview, then correct --config or initialize shipgate.yaml."
                ),
            ),
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
            baseline_path=baseline_path,
            policy_pack_paths=policy_pack_paths or [],
            plugins_enabled=plugins_enabled,
            no_heuristics=no_heuristics,
            fail_on=fail_on,
            pr_comment_style=pr_comment_style,
        )
        return verifier, None, 2

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
                base_notes.append(f"Could not collect diff for {base}...{head}: {exc}")
        else:
            diff_unavailable = True
            base_status = "ref_missing"
            base_notes.append(
                f"Base ref {base!r} is not available locally; run with fetch-depth: 0 "
                "or fetch the base before verify."
            )

    if not archive_head:
        try:
            worktree_paths, worktree_diff = working_tree_context(git_root)
            changed_files = _dedupe_paths([*changed_files, *worktree_paths])
            diff_text = _join_diff_text(diff_text, worktree_diff)
        except Exception as exc:  # noqa: BLE001 - local context degrades only.
            diff_unavailable = True
            base_notes.append(f"Could not collect working-tree diff context: {exc}")

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
        )
        _write_artifacts(
            verifier,
            verifier_path,
            verify_run_path,
            pr_comment_path,
            report=None,
            git_root=git_root,
            config_path=config_path,
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
            )
        _write_artifacts(
            verifier,
            verifier_path,
            verify_run_path,
            pr_comment_path,
            report=None,
            git_root=git_root,
            config_path=config_path,
            baseline_path=baseline_path,
            policy_pack_paths=policy_pack_paths or [],
            plugins_enabled=plugins_enabled,
            no_heuristics=no_heuristics,
            fail_on=fail_on,
            pr_comment_style=pr_comment_style,
        )
        return verifier, None, 0

    if diff_from is not None:
        base_status = "diff_from_provided"
        base_report = _resolve_under_workspace(git_root, diff_from)
        base_notes.append(f"Using explicit diff reference: {_display_path(base_report, git_root)}")
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

    try:
        if archive_head:
            head_tmp = tempfile.TemporaryDirectory(prefix="agents-shipgate-verify-head-")
            head_tree_dir = Path(head_tmp.name) / "head"
            archive_tree(git_root, head, head_tree_dir)
            head_input_root = head_tree_dir
            head_tree = tree_sha(git_root, head)
            head_config_path = head_tree_dir / config_relative
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
                ),
                capability_lock_callback=capture_capability_lock,
            )
        head_exit_code = _apply_strict_plugins(
            report, head_exit_code, strict_plugins=strict_plugins
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
            report=report,
            head_status=head_status,
            head_exit_code=head_exit_code,
            out_dir=out_dir,
            ci_mode=ci_mode,
        )
        try:
            try:
                _write_artifacts(
                    verifier,
                    verifier_path,
                    verify_run_path,
                    pr_comment_path,
                    report=report,
                    git_root=git_root,
                    config_path=head_config_path,
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
                [f"Could not materialize base tree {base!r}: {exc}"],
            )

        base_config = base_tree_dir / config_relative
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
                _base_report, base_exit = run_scan(
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
                [f"Base scan failed without changing the head gate: {exc}"],
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
        "version": 1,
        "agents_shipgate_version": __version__,
        "base_tree": base_tree,
        "config": config_relative.as_posix(),
        "baseline": {
            "path": (
                _display_path(baseline_path.resolve(), git_root)
                if baseline_path is not None
                else None
            ),
            "sha256": _sha256_file(baseline_path),
        },
        "policy_packs": [
            {
                "path": _display_path(path.resolve(), git_root),
                "sha256": _sha256_file(path),
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
        candidate = _resolve_under_workspace(git_root, path)
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
    candidate = _resolve_under_workspace(git_root, path)
    try:
        relative = candidate.relative_to(git_root)
    except ValueError:
        return candidate
    return tree_dir / relative


def _can_merge_without_human(
    *,
    merge_verdict: MergeVerdict,
    release_decision: ReleaseDecision | None,
    capability_review: VerifierCapabilityReview | None = None,
) -> bool:
    """Pure merge projection; contradictory passed substrate fails closed."""

    if release_decision is None:
        if merge_verdict == "mergeable" and _self_approval_note(capability_review):
            raise ValueError("mergeable not-applicable projection contradicts a touched trust root")
        return merge_verdict == "mergeable"
    if release_decision.decision != "passed":
        return False
    contradictions: list[str] = []
    if merge_verdict != "mergeable":
        contradictions.append("merge verdict is not mergeable")
    if _self_approval_note(capability_review) is not None:
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


def _self_approval_note(capability_review: VerifierCapabilityReview | None) -> str | None:
    """The explicit self-approval prohibition when this PR edits the rules that
    evaluate it.

    A coding agent must never silently self-approve a change to its own release
    gate (reward hacking). When the head scan flags a weakened policy or a
    touched trust root, that prohibition is surfaced as the verifier headline
    and the human-review reason — not left implicit in a fix_task instruction.
    """
    if capability_review is None:
        return None
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


def _verifier_headline(
    *,
    report: ReadinessReport | None,
    merge_verdict: MergeVerdict,
    head_status: str,
    capability_review: VerifierCapabilityReview | None = None,
) -> str | None:
    # An agent editing the rules that evaluate its own change must see the
    # self-approval prohibition first, ahead of the generic scan headline.
    note = _self_approval_note(capability_review)
    if note is not None:
        return note
    if report is not None and report.agent_summary is not None:
        return report.agent_summary.headline
    if head_status == "skipped":
        return "No agent-capability changes detected; Shipgate did not need to run."
    if merge_verdict == "unknown":
        return "Shipgate could not complete the scan; human review required."
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
        command = fix_task.verification_command
        if not command:
            raise ValueError("agent-safe verifier repair requires an exact rerun command")
        return derive_agent_control(
            reason=reason,
            next_action=CodingAgentCommandAction(
                kind="repair",
                command=command,
                why=fix_task.instructions[0] if fix_task.instructions else reason,
            ),
            verify_required=True,
            allowed_next_commands=[command],
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

    review_reason = _self_approval_note(capability_review) or reason
    unsafe_block = bool(release_decision is not None and release_decision.decision == "blocked")
    return derive_agent_control(
        reason=reason,
        next_action=HumanControlAction(
            kind="stop" if unsafe_block or execution == "failed" else "review",
            why=review_reason,
        ),
        verify_required=release_decision is not None,
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
    safe_recovery = first_next_action_override is not None or base_status in {
        "ref_missing",
        "archive_failed",
        "missing_manifest",
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
            _display_path(base_report, git_root) if base_report is not None else None
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
    ):
        path = out_dir / name
        if path.is_file() or path.is_symlink():
            with contextlib.suppress(OSError):
                path.unlink()


def _clear_trusted_handoff(out_dir: Path) -> None:
    """Remove every prior terminal/projection artifact before a new run."""

    for name in (
        "agent-handoff.json",
        "verification-plan.json",
        "verification-unit-result.json",
        "verification-artifacts.json",
        "verification-receipt.json",
        "human-authorization.json",
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
    logical_config = (
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
    portable_diff_from_path: Path | None = None
    if diff_from_path is not None and diff_from_path.is_file():
        portable_diff_from_path = verifier_path.with_name("verification-base-report.json")
        shutil.copyfile(diff_from_path, portable_diff_from_path)
        verifier.artifacts["verification_base_report_json"] = _display_path(
            portable_diff_from_path.resolve(), git_root
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
        baseline_path=baseline_path,
        diff_from_path=portable_diff_from_path,
        policy_pack_paths=policy_pack_paths,
        evaluation_date=resolved_date,
        options={
            "ci_mode": verifier.mode,
            "fail_on": sorted(fail_on or []),
            "no_heuristics": no_heuristics,
            "plugins_enabled": plugins_enabled is not False,
            **resolved_options,
        },
        plugins_enabled=plugins_enabled,
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


def _relative_to_workspace(workspace: Path, path: Path, label: str) -> Path:
    try:
        return path.resolve().relative_to(workspace)
    except ValueError as exc:
        raise ConfigError(f"{label} must resolve inside --workspace: {path}") from exc


def _dedupe_paths(paths: list[str]) -> list[str]:
    return sorted({path for path in paths if path})


def _join_diff_text(left: str, right: str) -> str:
    if left and right:
        return f"{left}\n{right}"
    return left or right


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def _preview_init_command(workspace: Path) -> str:
    return _shell_join(
        [
            "shipgate",
            "init",
            "--workspace",
            str(workspace),
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
) -> str:
    parts = [
        "agents-shipgate",
        "verify",
        "--workspace",
        str(workspace),
        "--config",
        str(config),
    ]
    if base is not None:
        parts.extend(["--base", base])
    if head is not None:
        parts.extend(["--head", head])
    if out is not None:
        parts.extend(["--out", str(out)])
    parts.extend(["--ci-mode", "advisory", "--json"])
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
    root = workspace.resolve()
    config_path = _resolve_under_workspace(root, config)
    out_dir = _resolve_under_workspace(root, out or DEFAULT_OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    _clear_trusted_handoff(out_dir)
    verifier_path = out_dir / "verifier.json"
    verify_run_path = out_dir / "verify-run.json"
    agent_handoff_path = out_dir / "agent-handoff.json"
    pr_comment_path = out_dir / "pr-comment.md"
    manifest_present = config_path.exists()

    changed_files: list[str] = []
    diff_text = ""
    notes: list[str] = []
    diff_unavailable = False
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
    )

    if diff_unavailable and manifest_present:
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
        baseline_path=None,
        policy_pack_paths=[],
        plugins_enabled=None,
        no_heuristics=False,
        fail_on=None,
        pr_comment_style=pr_comment_style,
    )
    return verifier, None, 0


__all__ = ["run_preview", "run_verify"]
