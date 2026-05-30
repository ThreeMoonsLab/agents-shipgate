from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from agents_shipgate import __version__
from agents_shipgate.cli._helpers import _apply_strict_plugins
from agents_shipgate.cli.scan.orchestrator import run_scan
from agents_shipgate.core.errors import AgentsShipgateError, ConfigError, InputParseError
from agents_shipgate.report.json_report import report_json_payload
from agents_shipgate.schemas.report import AgentSummary, ReadinessReport, ReleaseDecision
from agents_shipgate.schemas.verification import VerificationContext
from agents_shipgate.schemas.verifier import (
    MergeVerdict,
    VerifierArtifact,
    VerifierBaseStatus,
    VerifierCapabilityReview,
    VerifierFixTask,
    VerifierHumanReview,
    VerifierNextAction,
    merge_verdict_for,
)
from agents_shipgate.triggers import evaluate

from .capability_review import build_capability_review
from .fix_task import build_fix_task
from .git import (
    archive_tree,
    diff_context,
    ensure_git_workspace,
    git_path,
    ref_exists,
    tree_sha,
    working_tree_context,
)
from .pr_comment import render_pr_comment

HEAD_FORMATS = ["markdown", "json", "sarif"]
# Verify owns the PR artifact contract and writes packet.json only; the
# reviewer-facing Markdown surface is pr-comment.md.
HEAD_PACKET_FORMATS = ["json"]
DEFAULT_OUT_DIR = Path("agents-shipgate-reports")
BASE_CACHE_KEEP_ENTRIES = 16


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
    verifier_path = out_dir / "verifier.json"
    pr_comment_path = out_dir / "pr-comment.md"

    changed_files: list[str] = []
    diff_text = ""
    base_status: VerifierBaseStatus = "not_requested"
    base_tree: str | None = None
    base_report: Path | None = None
    base_notes: list[str] = []
    diff_unavailable = False

    head_exists = ref_exists(git_root, head)
    if not head_exists:
        raise ConfigError(f"Head ref does not exist locally: {head}")

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
            pr_comment_path,
            report=None,
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
            pr_comment_path,
            report=None,
            pr_comment_style=pr_comment_style,
        )
        return verifier, None, 0

    if diff_from is not None:
        base_status = "diff_from_provided"
        base_report = _resolve_under_workspace(git_root, diff_from)
        base_notes.append(
            f"Using explicit diff reference: {_display_path(base_report, git_root)}"
        )
    elif base and base_exists:
        base_status, base_tree, base_report, cache_notes = _prepare_base_report(
            git_root=git_root,
            base=base,
            config_relative=config_relative,
            baseline_path=baseline_path,
            policy_packs=policy_pack_paths or [],
            plugins_enabled=plugins_enabled,
            no_heuristics=no_heuristics,
            verbose=verbose,
        )
        base_notes.extend(cache_notes)

    report: ReadinessReport | None = None
    head_status = "failed"
    head_exit_code = 4
    scan_error: BaseException | None = None
    head_tmp: tempfile.TemporaryDirectory[str] | None = None
    head_config_path = config_path
    head_policy_pack_paths = policy_pack_paths
    try:
        if archive_head:
            head_tmp = tempfile.TemporaryDirectory(prefix="agents-shipgate-verify-head-")
            head_tree_dir = Path(head_tmp.name) / "head"
            archive_tree(git_root, head, head_tree_dir)
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
        report, head_exit_code = run_scan(
            config_path=head_config_path,
            output_dir=out_dir,
            formats=HEAD_FORMATS,
            ci_mode=ci_mode,
            fail_on=fail_on,
            baseline_path=baseline_path,
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
                diff_text_available=bool(diff_text),
                trigger_result=trigger,
            ),
        )
        head_exit_code = _apply_strict_plugins(
            report, head_exit_code, strict_plugins=strict_plugins
        )
        head_status = "succeeded"
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
                    pr_comment_path,
                    report=report,
                    pr_comment_style=pr_comment_style,
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
) -> tuple[VerifierBaseStatus, str | None, Path | None, list[str]]:
    notes: list[str] = []
    try:
        base_tree = tree_sha(git_root, base)
    except Exception as exc:  # noqa: BLE001 - optional base enrichment.
        return "archive_failed", None, None, [f"Could not resolve base tree: {exc}"]

    cache_report = _cache_report_path(
        base_tree=base_tree,
        config_relative=config_relative,
        git_root=git_root,
        baseline_path=baseline_path,
        policy_packs=policy_packs,
        plugins_enabled=plugins_enabled,
        no_heuristics=no_heuristics,
    )
    if cache_report.exists():
        return "cache_hit", base_tree, cache_report, [
            f"Reused cached base report for tree {base_tree}."
        ]

    with tempfile.TemporaryDirectory(prefix="agents-shipgate-verify-") as tmp:
        tmp_root = Path(tmp)
        base_tree_dir = tmp_root / "base"
        base_out = tmp_root / "reports"
        try:
            archive_tree(git_root, base, base_tree_dir)
        except Exception as exc:  # noqa: BLE001 - optional base enrichment.
            return "archive_failed", base_tree, None, [
                f"Could not materialize base tree {base!r}: {exc}"
            ]

        base_config = base_tree_dir / config_relative
        if not base_config.is_file():
            return "missing_manifest", base_tree, None, [
                f"Base tree does not contain {config_relative.as_posix()}."
            ]

        try:
            with _without_github_step_summary():
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
                )
        except Exception as exc:  # noqa: BLE001 - optional base enrichment.
            return "scan_failed", base_tree, None, [
                f"Base scan failed without changing the head gate: {exc}"
            ]
        if base_exit not in {0, 20}:
            return "scan_failed", base_tree, None, [
                f"Base scan exited {base_exit}; diff enrichment disabled."
            ]
        source_report = base_out / "report.json"
        if not source_report.is_file():
            return "scan_failed", base_tree, None, [
                "Base scan did not produce report.json; diff enrichment disabled."
            ]
        _copy_report_to_cache(source_report, cache_report)
        _prune_base_scan_cache(cache_report.parents[1], keep=BASE_CACHE_KEEP_ENTRIES)
        notes.append(f"Cached base report for tree {base_tree}.")
    return "succeeded", base_tree, cache_report, notes


def _cache_report_path(
    *,
    base_tree: str,
    config_relative: Path,
    git_root: Path,
    baseline_path: Path | None,
    policy_packs: list[Path],
    plugins_enabled: bool | None,
    no_heuristics: bool,
) -> Path:
    payload = {
        "version": 1,
        "agents_shipgate_version": __version__,
        "base_tree": base_tree,
        "config": config_relative.as_posix(),
        "baseline": (
            _display_path(baseline_path.resolve(), git_root)
            if baseline_path is not None
            else None
        ),
        "policy_packs": [
            _display_path(path.resolve(), git_root) for path in policy_packs
        ],
        "plugins_enabled": plugins_enabled,
        "no_heuristics": no_heuristics,
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
    *, merge_verdict: MergeVerdict, release_decision: ReleaseDecision | None
) -> bool:
    if merge_verdict != "mergeable":
        return False
    if release_decision is None:
        return True
    if release_decision.evidence_coverage.human_review_recommended:
        return False
    return not (release_decision.blockers or release_decision.review_items)


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


def _human_review(
    *,
    merge_verdict: MergeVerdict,
    release_decision: ReleaseDecision | None,
    capability_review: VerifierCapabilityReview | None = None,
) -> VerifierHumanReview:
    note = _self_approval_note(capability_review)
    required = note is not None or merge_verdict in {
        "human_review_required",
        "blocked",
        "insufficient_evidence",
        "unknown",
    }
    if not required:
        return VerifierHumanReview(required=False, why=None)
    # A self-approval prohibition is the most important reason a human is
    # needed; surface it ahead of the generic release-decision reason.
    reason = release_decision.reason if release_decision is not None else None
    return VerifierHumanReview(
        required=True,
        why=note
        or reason
        or "A human must review this agent-capability change before merge.",
    )


def _first_next_action(
    *,
    merge_verdict: MergeVerdict,
    fix_task: VerifierFixTask | None,
    agent_summary: AgentSummary | None,
    reason: str | None,
) -> VerifierNextAction:
    if merge_verdict == "mergeable":
        return VerifierNextAction(
            actor="coding_agent",
            kind="none",
            command=None,
            why="No agent-capability changes gate this PR; safe to merge.",
        )
    # The fix_task is the single repair contract; the headline next-step must
    # not contradict it. Borrow the agent summary's concrete action (e.g. an
    # apply-patches command) only when its implied actor agrees with the
    # fix_task routing — otherwise derive the pointer from the fix_task so that
    # actor, command, and why all come from one source.
    actor = fix_task.actor if fix_task is not None else "human"
    recommended = (
        agent_summary.first_recommended_action if agent_summary is not None else None
    )
    if recommended is not None:
        # The PR comment infers a recommendation's actor the same way: a
        # runnable command implies the coding agent, an info note a human.
        recommended_actor = "coding_agent" if recommended.kind == "command" else "human"
        if fix_task is None or recommended_actor == actor:
            return VerifierNextAction(
                actor=actor,
                kind=recommended.kind,
                command=recommended.command,
                why=recommended.why,
            )
    if fix_task is not None:
        why = (
            fix_task.instructions[0]
            if fix_task.instructions
            else (reason or "Human review required before merge.")
        )
        if actor == "coding_agent":
            return VerifierNextAction(
                actor=actor,
                kind="command",
                command=fix_task.verification_command,
                why=why,
            )
        return VerifierNextAction(actor=actor, kind="review", command=None, why=why)
    return VerifierNextAction(
        actor=actor,
        kind="review",
        command=None,
        why=reason or "Human review required before merge.",
    )


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
    base_report: Path | None,
    base_notes: list[str],
    report: ReadinessReport | None,
    head_status: str,
    head_exit_code: int,
    out_dir: Path,
    ci_mode: str | None = None,
    preview: bool = False,
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
    merge_verdict = merge_verdict_for(decision=decision, head_status=head_status)
    agent_summary_model = report.agent_summary if report is not None else None
    capability_review = build_capability_review(report) if report is not None else None
    human_review = _human_review(
        merge_verdict=merge_verdict,
        release_decision=release_decision_model,
        capability_review=capability_review,
    )
    fix_task = build_fix_task(
        report,
        merge_verdict=merge_verdict,
        capability_review=capability_review,
        base_ref=base,
        head_ref=head,
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
        base_report_json=(
            _display_path(base_report, git_root) if base_report is not None else None
        ),
        base_notes=base_notes,
        head_status=head_status,  # type: ignore[arg-type]
        head_report_json=artifacts.get("report_json") if report is not None else None,
        head_exit_code=head_exit_code,
        release_decision=release_decision,
        agent_summary=(
            agent_summary_model.model_dump(mode="json")
            if agent_summary_model is not None
            else None
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
        can_merge_without_human=_can_merge_without_human(
            merge_verdict=merge_verdict,
            release_decision=release_decision_model,
        ),
        headline=_verifier_headline(
            report=report,
            merge_verdict=merge_verdict,
            head_status=head_status,
            capability_review=capability_review,
        ),
        human_review=human_review,
        first_next_action=_first_next_action(
            merge_verdict=merge_verdict,
            fix_task=fix_task,
            agent_summary=agent_summary_model,
            reason=release_decision_model.reason if release_decision_model else None,
        ),
        fix_task=fix_task,
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
        "pr_comment": out_dir / "pr-comment.md",
    }
    if include_scan_artifacts:
        candidates = {
            "report_markdown": out_dir / "report.md",
            "report_json": out_dir / "report.json",
            "report_sarif": out_dir / "report.sarif",
            "packet_json": out_dir / "packet.json",
            **candidates,
        }
    return {
        key: _display_path(path.resolve(), git_root)
        for key, path in candidates.items()
    }


def _write_artifacts(
    verifier: VerifierArtifact,
    verifier_path: Path,
    pr_comment_path: Path,
    *,
    report: ReadinessReport | None,
    pr_comment_style: str,
) -> None:
    verifier_path.parent.mkdir(parents=True, exist_ok=True)
    verifier_path.write_text(
        json.dumps(verifier.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    pr_comment_path.write_text(
        render_pr_comment(verifier, report=report, style=pr_comment_style),
        encoding="utf-8",
    )
    # Keep report.json as the authoritative artifact, but validate the
    # in-memory report can still produce the canonical public payload.
    if report is not None:
        report_json_payload(report)


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
    verifier_path = out_dir / "verifier.json"
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

    if diff_unavailable:
        next_action = VerifierNextAction(
            actor="coding_agent",
            kind="fetch_base",
            command=None,
            why=(
                "Preview could not inspect the requested PR diff; make the base "
                "and head refs available locally, then rerun preview or verify."
            ),
        )
        headline = "Shipgate preview could not inspect the requested PR diff."
    elif manifest_present:
        next_action = VerifierNextAction(
            actor="coding_agent",
            kind="command",
            command="agents-shipgate verify --base origin/main --head HEAD --json",
            why="Shipgate is already set up here; run verify on the PR diff.",
        )
        headline = (
            "Shipgate is configured; run verify on the PR to get a merge verdict."
        )
    elif trigger.get("should_run") or trigger.get("dry_run_recommended"):
        trigger_action = trigger.get("next_action") or {}
        next_action = VerifierNextAction(
            actor="coding_agent",
            kind=trigger_action.get("kind") or "command",
            command=trigger_action.get("command")
            or "agents-shipgate detect --workspace . --json",
            why=trigger_action.get("why")
            or "This change looks agent-related; detect tool surfaces first.",
        )
        headline = (
            "Shipgate is relevant to this diff; detect the workspace before "
            "initializing the gate."
        )
    elif not (base or head):
        next_action = VerifierNextAction(
            actor="coding_agent",
            kind="command",
            command=(
                "agents-shipgate init --workspace . --write --ci "
                "--agent-instructions=all"
            ),
            why=(
                "No PR diff was supplied and no shipgate.yaml was found; "
                "initialize Shipgate if this workspace contains an agent."
            ),
        )
        headline = (
            "Shipgate is not set up here yet; initialize it to gate "
            "agent-capability PRs."
        )
    else:
        trigger_action = trigger.get("next_action") or {}
        next_action = VerifierNextAction(
            actor="coding_agent",
            kind=trigger_action.get("kind") or "none",
            command=trigger_action.get("command"),
            why=trigger_action.get("why") or "No Shipgate-relevant changes detected.",
        )
        headline = "No Shipgate-relevant agent-capability changes detected in this diff."

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
        head_status="skipped",
        head_exit_code=0,
        mode="preview",
        merge_verdict="unknown",
        can_merge_without_human=False,
        headline=headline,
        human_review=VerifierHumanReview(required=False, why=None),
        first_next_action=next_action,
        artifacts={
            "verifier_json": _display_path(verifier_path.resolve(), root),
            "pr_comment": _display_path(pr_comment_path.resolve(), root),
        },
    )
    _write_artifacts(
        verifier,
        verifier_path,
        pr_comment_path,
        report=None,
        pr_comment_style=pr_comment_style,
    )
    return verifier, None, 0


__all__ = ["run_preview", "run_verify"]
