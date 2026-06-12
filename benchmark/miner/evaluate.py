"""Evaluate one merged PR (base/head SHAs in a local clone) through the engine.

Network-free: everything here runs against an existing local clone. The flow
mirrors what a repo adopting Shipgate would have experienced at merge time:

1. trigger evaluation on the PR diff (the organic run/skip gate);
2. ``shipgate check`` — the boundary gate, no manifest needed;
3. head worktree: ``init --write`` when no manifest exists (the cold-start
   path), then ``scan`` for the release decision and evidence gaps;
4. base worktree with the same manifest: ``capability export`` on both sides
   plus ``capability diff`` for the authority delta.

Every engine invocation goes through ``sys.executable -m agents_shipgate`` so
the evaluated code is the one importable from this environment (avoids the
stale-PATH-shadow footgun), with ``AGENTS_SHIPGATE_AGENT_MODE=0`` pinned so
output shapes stay stable inside coding-agent shells.

One PR's failure must never abort a mining run: every step degrades into row
fields (``status``, ``notes``), and exceptions are converted to error rows.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from benchmark.miner.rows import (
    STATUS_ERROR,
    STATUS_EVALUATED,
    STATUS_INIT_SKIP,
    STATUS_SCAN_FAILED,
    STATUS_TRIGGER_SKIP,
    MinedRow,
)

_GIT_TIMEOUT = 120
_CLI_TIMEOUT = 420


def shipgate_cmd() -> list[str]:
    return [sys.executable, "-m", "agents_shipgate"]


def _cli_env() -> dict[str, str]:
    env = dict(os.environ)
    # Pin agent-mode off so stdout shapes don't flip when the miner itself
    # runs inside a coding-agent shell (CLAUDECODE=1 auto-detection).
    env["AGENTS_SHIPGATE_AGENT_MODE"] = "0"
    return env


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = _CLI_TIMEOUT,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_cli_env(),
        check=False,
    )


def _git(repo: Path, args: list[str], *, timeout: int = _GIT_TIMEOUT) -> subprocess.CompletedProcess:
    return _run(["git", "-C", str(repo), *args], timeout=timeout)


def evaluate_pr(
    *,
    repo_path: Path,
    base_sha: str,
    head_sha: str,
    repo: str = "",
    pr_number: int = 0,
    pr_url: str = "",
    title: str = "",
    merged_at: str = "",
    force_run: bool = False,
) -> MinedRow:
    """Return the mined row for one base/head pair in a local clone."""

    row = MinedRow(
        repo=repo,
        pr_number=pr_number,
        pr_url=pr_url,
        title=title,
        merged_at=merged_at,
        base_sha=base_sha,
        head_sha=head_sha,
    )
    try:
        return _evaluate(row, repo_path=repo_path, force_run=force_run)
    except Exception as exc:  # noqa: BLE001 - one PR must not abort the run.
        row.status = STATUS_ERROR
        row.notes = _append_note(row.notes, f"exception:{type(exc).__name__}:{exc}")
        return row


def _evaluate(row: MinedRow, *, repo_path: Path, force_run: bool) -> MinedRow:
    names = _git(repo_path, ["diff", "--name-only", row.base_sha, row.head_sha])
    body = _git(repo_path, ["diff", row.base_sha, row.head_sha])
    if names.returncode != 0 or body.returncode != 0:
        row.status = STATUS_ERROR
        row.notes = _append_note(row.notes, "git_diff_failed")
        return row
    changed = [line.strip() for line in names.stdout.splitlines() if line.strip()]
    row.files_changed = len(changed)

    manifest_at_head = (
        _git(repo_path, ["cat-file", "-e", f"{row.head_sha}:shipgate.yaml"]).returncode == 0
    )

    from agents_shipgate.triggers import evaluate as evaluate_trigger

    trigger = evaluate_trigger(
        paths=changed,
        diff_text=body.stdout,
        manifest_present=manifest_at_head,
        user_requested=False,
    )
    row.trigger_run = bool(trigger.get("run_shipgate"))
    row.trigger_rationale = str(trigger.get("rationale") or "")[:200]

    if not row.trigger_run and not force_run:
        row.status = STATUS_TRIGGER_SKIP
        return row

    with tempfile.TemporaryDirectory(prefix="shipgate-miner-") as tmp:
        tmp_path = Path(tmp)
        head_wt = tmp_path / "head"
        base_wt = tmp_path / "base"
        _worktree_add(repo_path, head_wt, row.head_sha)
        try:
            _boundary_check(row, head_wt, body.stdout, tmp_path)
            evaluated = _cold_start_scan(row, head_wt, head_wt, base_wt, repo_path, tmp_path)
            if not evaluated and not row.head_decision:
                # Monorepo fallback: retry at the deepest common directory of
                # the changed files — capability surfaces in example/monorepo
                # layouts live below the root (stripe/ai PR #232 pattern).
                subdir = _common_directory(changed)
                if subdir and (head_wt / subdir).is_dir():
                    row.notes = _append_note(row.notes, f"retry_at:{subdir}")
                    _cold_start_scan(
                        row,
                        head_wt / subdir,
                        head_wt,
                        base_wt,
                        repo_path,
                        tmp_path,
                        subdir=subdir,
                    )
            if row.head_decision:
                row.status = STATUS_EVALUATED
            elif row.init_status in {"", "not_agent_project", "failed"}:
                row.status = STATUS_INIT_SKIP
            else:
                row.status = STATUS_SCAN_FAILED
            return row
        finally:
            _worktree_remove(repo_path, head_wt)
            _worktree_remove(repo_path, base_wt)


def _cold_start_scan(
    row: MinedRow,
    scan_root: Path,
    head_wt: Path,
    base_wt: Path,
    repo_path: Path,
    tmp_path: Path,
    *,
    subdir: str = "",
) -> bool:
    """Run init-if-needed + scan + capability delta rooted at ``scan_root``.

    Returns True when the scan produced a release decision. Mutates ``row``
    in place; failures land in ``row.notes`` and leave fields blank.
    """

    manifest = scan_root / "shipgate.yaml"
    if manifest.is_file():
        row.init_status = "preexisting"
    else:
        row.init_status = _run_init(scan_root)
    if not manifest.is_file():
        return False
    if not _head_scan(row, scan_root, tmp_path):
        return False
    _capability_delta(
        row, repo_path, base_wt, head_wt, manifest, tmp_path, subdir=subdir
    )
    return True


def _boundary_check(row: MinedRow, head_wt: Path, diff_text: str, tmp_path: Path) -> None:
    diff_file = tmp_path / "pr.diff"
    diff_file.write_text(diff_text, encoding="utf-8")
    result = _run(
        [
            *shipgate_cmd(),
            "check",
            "--agent",
            "claude-code",
            "--workspace",
            str(head_wt),
            "--diff",
            str(diff_file),
            "--format",
            "agent-json",
        ]
    )
    payload = _parse_json(result.stdout)
    if payload is None:
        row.notes = _append_note(row.notes, "check_unparseable")
        return
    row.check_decision = str(payload.get("decision") or "")
    rules = payload.get("violated_rules") or []
    ids = sorted(
        {
            str(rule.get("check_id") or rule.get("id") or "")
            for rule in rules
            if isinstance(rule, dict)
        }
        - {""}
    )
    row.check_rule_ids = ",".join(ids)


def _run_init(head_wt: Path) -> str:
    result = _run(
        [*shipgate_cmd(), "init", "--workspace", str(head_wt), "--write", "--json"]
    )
    if (head_wt / "shipgate.yaml").is_file():
        return "written"
    if result.returncode == 0:
        return "not_agent_project"
    return "failed"


def _head_scan(row: MinedRow, scan_root: Path, tmp_path: Path) -> bool:
    out_dir = tmp_path / "head-reports"
    result = _run(
        [
            *shipgate_cmd(),
            "scan",
            "--config",
            str(scan_root / "shipgate.yaml"),
            "--format",
            "json",
            "--out",
            str(out_dir),
            "--ci-mode",
            "advisory",
        ]
    )
    report_path = out_dir / "report.json"
    if not report_path.is_file():
        stderr_head = (result.stderr or "").strip().splitlines()
        detail = stderr_head[0][:160] if stderr_head else ""
        row.notes = _append_note(
            row.notes,
            f"head_scan_failed_exit_{result.returncode}"
            + (f":{detail}" if detail else ""),
        )
        return False
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        row.notes = _append_note(row.notes, "head_report_unparseable")
        return False
    decision = report.get("release_decision") or {}
    row.head_decision = str(decision.get("decision") or "")
    row.head_blockers = _safe_len(decision.get("blockers"))
    row.head_review_items = _safe_len(decision.get("review_items"))
    coverage = decision.get("evidence_coverage") or {}
    row.evidence_gaps = _safe_len(coverage.get("evidence_gaps"))
    summary = report.get("summary") or {}
    tools = summary.get("tools_scanned", summary.get("tool_count"))
    row.tools_scanned = int(tools) if isinstance(tools, int) else None
    return True


def _capability_delta(
    row: MinedRow,
    repo_path: Path,
    base_wt: Path,
    head_wt: Path,
    head_manifest: Path,
    tmp_path: Path,
    *,
    subdir: str = "",
) -> None:
    head_lock = tmp_path / "head.lock.json"
    if not _capability_export(head_manifest, head_lock):
        row.notes = _append_note(row.notes, "head_lock_failed")
        return
    if not base_wt.exists():
        _worktree_add(repo_path, base_wt, row.base_sha)
    base_root = (base_wt / subdir) if subdir else base_wt
    if not base_root.is_dir():
        row.notes = _append_note(row.notes, "base_subdir_missing")
        return
    base_manifest = base_root / "shipgate.yaml"
    if not base_manifest.is_file():
        # The cold-start comparison: same manifest applied to both trees so
        # the delta reflects the PR's tool-surface change, not manifest drift.
        shutil.copyfile(head_manifest, base_manifest)
    base_lock = tmp_path / "base.lock.json"
    if not _capability_export(base_manifest, base_lock):
        row.notes = _append_note(row.notes, "base_lock_failed")
        return
    result = _run(
        [
            *shipgate_cmd(),
            "capability",
            "diff",
            "--base",
            str(base_lock),
            "--head",
            str(head_lock),
            "--json",
        ]
    )
    payload = _parse_json(result.stdout)
    if payload is None:
        row.notes = _append_note(row.notes, "cap_diff_unparseable")
        return
    summary = payload.get("summary") or {}
    row.cap_added = _as_int(summary.get("added"))
    row.cap_removed = _as_int(summary.get("removed"))
    row.cap_changed = _as_int(summary.get("changed"))
    changed = payload.get("changed") or []
    row.cap_broadened = sum(
        1
        for item in changed
        if isinstance(item, dict) and str(item.get("direction") or "") == "broadened"
    )


def _capability_export(config: Path, out: Path) -> bool:
    result = _run(
        [
            *shipgate_cmd(),
            "capability",
            "export",
            "--config",
            str(config),
            "--out",
            str(out),
        ]
    )
    return result.returncode == 0 and out.is_file()


def _worktree_add(repo_path: Path, destination: Path, sha: str) -> None:
    result = _git(
        repo_path,
        ["worktree", "add", "--detach", "--force", str(destination), sha],
        timeout=_CLI_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git worktree add failed for {sha}: {result.stderr.strip()}")


def _worktree_remove(repo_path: Path, destination: Path) -> None:
    if not destination.exists():
        return
    _git(repo_path, ["worktree", "remove", "--force", str(destination)])


def _common_directory(changed: list[str]) -> str:
    """Deepest common directory of the changed paths, '' when it is the root."""

    if not changed:
        return ""
    split = [path.split("/")[:-1] for path in changed]
    prefix: list[str] = []
    for parts in zip(*split, strict=False):
        first = parts[0]
        if all(part == first for part in parts):
            prefix.append(first)
        else:
            break
    return "/".join(prefix)


def _parse_json(text: str) -> dict | None:
    text = text.strip()
    if not text:
        return None
    # Tolerate a stray non-JSON prefix line (e.g. a warning) before the object.
    start = text.find("{")
    if start == -1:
        return None
    try:
        payload = json.loads(text[start:])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _safe_len(value: object) -> int | None:
    return len(value) if isinstance(value, list) else None


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _append_note(notes: str, note: str) -> str:
    return f"{notes};{note}" if notes else note
