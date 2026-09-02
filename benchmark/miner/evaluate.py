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
# <repo>/src — the checkout this miner lives in. evaluate.py is at
# <repo>/benchmark/miner/evaluate.py, so parents[2] is the repo root.
_REPO_SRC = Path(__file__).resolve().parents[2] / "src"


def shipgate_cmd() -> list[str]:
    return [sys.executable, "-m", "agents_shipgate"]


def _ensure_repo_src_on_path() -> None:
    """Make in-process ``import agents_shipgate`` resolve to THIS checkout.

    ``cli_env`` only fixes child subprocesses; ``evaluate_pr`` imports
    ``agents_shipgate.triggers`` in the PARENT to decide run/skip. Run by hand
    against an older installed copy (or an editable ``.pth`` for a different
    checkout), the parent would otherwise compute trigger decisions from a
    stale catalog. Idempotent, and a no-op under pytest where conftest already
    front-loads src. Must run before the first agents_shipgate import.
    """

    src = str(_REPO_SRC)
    if src not in sys.path:
        sys.path.insert(0, src)


def cli_env() -> dict[str, str]:
    """Environment for child ``python -m agents_shipgate`` runs.

    Two pins, both load-bearing:

    - ``AGENTS_SHIPGATE_AGENT_MODE=0`` so stdout shapes don't flip when the
      miner itself runs inside a coding-agent shell (``CLAUDECODE=1``).
    - the checkout's ``src/`` prepended to ``PYTHONPATH`` so the child imports
      THIS source tree, never a stale ``agents-shipgate`` wheel on the machine.
      Without it, the documented ``python -m benchmark.miner …`` commands are
      only hermetic under pytest (conftest sets ``PYTHONPATH``); run by hand
      against an older installed copy they silently resolve to it.
    """

    env = dict(os.environ)
    env["AGENTS_SHIPGATE_AGENT_MODE"] = "0"
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(_REPO_SRC) + (os.pathsep + existing if existing else "")
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
        env=cli_env(),
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
    notes: str = "",
) -> MinedRow:
    """Return the mined row for one base/head pair in a local clone.

    ``notes`` seeds the row's notes (the candidate's provenance, e.g.
    ``closed_unmerged``); evaluation appends its own notes after it.
    """

    row = MinedRow(
        repo=repo,
        pr_number=pr_number,
        pr_url=pr_url,
        title=title,
        merged_at=merged_at,
        base_sha=base_sha,
        head_sha=head_sha,
        notes=notes,
    )
    try:
        return _evaluate(row, repo_path=repo_path, force_run=force_run)
    except Exception as exc:  # noqa: BLE001 - one PR must not abort the run.
        row.status = STATUS_ERROR
        row.notes = _append_note(row.notes, f"exception:{type(exc).__name__}:{exc}")
        return row


def _evaluate(row: MinedRow, *, repo_path: Path, force_run: bool) -> MinedRow:
    # The row contract says base_sha/head_sha are commit ids. Callers may
    # pass revision expressions (HEAD, <sha>^1); resolve them up front so
    # downstream corpus tooling can treat the columns as SHAs.
    for attr in ("base_sha", "head_sha"):
        resolved = _rev_parse(repo_path, getattr(row, attr))
        if resolved is None:
            row.status = STATUS_ERROR
            row.notes = _append_note(row.notes, f"unresolvable_{attr}")
            return row
        setattr(row, attr, resolved)
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

    _ensure_repo_src_on_path()
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
    _verify_pr(
        row,
        repo_path,
        base_wt,
        head_wt,
        manifest,
        tmp_path,
        subdir=subdir,
        manifest_injected=row.init_status == "written",
    )
    return True


def _verify_pr(
    row: MinedRow,
    repo_path: Path,
    base_wt: Path,
    head_wt: Path,
    head_manifest: Path,
    tmp_path: Path,
    *,
    subdir: str,
    manifest_injected: bool,
) -> None:
    """Run the real PR verifier to produce the per-PR receipt fields.

    ``manifest_injected`` distinguishes the miner's synthetic cold-start
    manifest from a real ``shipgate.yaml`` the PR itself changed, which
    decides how the base side is set up (see below). These are the receipt
    fields; the v0.1 scan fields above are the cold-start whole-surface
    state.
    """

    manifest_rel = f"{subdir}/shipgate.yaml" if subdir else "shipgate.yaml"
    head_commit = _commit_manifest(head_wt, manifest_rel)
    if head_commit is None:
        row.notes = _append_note(row.notes, "verify_head_commit_failed")
        return
    # Self-sufficient base setup: _capability_delta usually created the base
    # worktree and copied the manifest, but its earlier failures must not
    # also cost us the verify receipt.
    if not base_wt.exists():
        try:
            _worktree_add(repo_path, base_wt, row.base_sha)
        except RuntimeError:
            row.notes = _append_note(row.notes, "verify_base_worktree_failed")
            return
    base_manifest = base_wt / manifest_rel
    # Decide the manifest's kind from git history at the REAL base commit,
    # NOT from base_wt on disk: _capability_delta already copied the manifest
    # into the base worktree for its export, so a filesystem check would
    # mis-read a PR-added manifest as preexisting.
    base_had_manifest = (
        _git(base_wt, ["cat-file", "-e", f"{row.base_sha}:{manifest_rel}"]).returncode == 0
    )

    if manifest_injected:
        # Synthetic apparatus: force the identical manifest onto base so it
        # cancels out of the base...head diff — it is not a PR change.
        if not base_manifest.parent.is_dir():
            row.notes = _append_note(row.notes, "verify_base_subdir_missing")
            return
        shutil.copyfile(head_manifest, base_manifest)

    baseline_path: Path | None = None
    if manifest_injected or base_had_manifest:
        # Base has a manifest in scope (synthetic on both sides, or the PR
        # EDITED a real one): commit it so the diff is clean, then baseline-gate.
        # Without a baseline, verify counts the repo's PRE-EXISTING blockers
        # (release_decision.py only demotes baseline-MATCHED findings); a
        # base-tree baseline makes the receipt reflect the PR's new findings —
        # a docs-only PR scored a "blocked" receipt from standing surface
        # until this was added.
        base_sha = _commit_manifest(base_wt, manifest_rel)
        if base_sha is None:
            row.notes = _append_note(row.notes, "verify_base_commit_failed")
            return
        baseline_path = tmp_path / "base-baseline.json"
        baseline = _run(
            [
                *shipgate_cmd(),
                "baseline",
                "save",
                "--config",
                str(base_manifest),
                "--out",
                str(baseline_path),
            ]
        )
        if baseline.returncode != 0 or not baseline_path.is_file():
            row.notes = _append_note(row.notes, "verify_baseline_failed")
            return
    else:
        # The PR ADDED a real manifest: read the real base commit (no manifest)
        # so the addition stays a visible trust-root diff. Copying head's
        # manifest onto base would erase exactly the SHIP-VERIFY-TRUST-ROOT-
        # TOUCHED signal verify exists to report. No base tool surface → no
        # baseline (nothing pre-existed); verify reads the commit, so the stray
        # working-tree copy from _capability_delta is irrelevant.
        base_sha = row.base_sha

    # Re-parent head'' = head-tree (with the head-side manifest) onto base',
    # so verify's three-dot base...head diff is exactly the PR's delta: an
    # injected manifest committed independently on each side would otherwise
    # show as "added" and falsely fire the trust-root signal.
    head_tree = _git(head_wt, ["rev-parse", f"{head_commit}^{{tree}}"])
    if head_tree.returncode != 0 or not head_tree.stdout.strip():
        row.notes = _append_note(row.notes, "verify_head_tree_failed")
        return
    reparented = _git(
        head_wt,
        [
            "-c",
            "user.email=miner@local",
            "-c",
            "user.name=shipgate-miner",
            "commit-tree",
            head_tree.stdout.strip(),
            "-p",
            base_sha,
            "-m",
            "miner: PR head for verify",
        ],
    )
    head_sha = reparented.stdout.strip()
    if reparented.returncode != 0 or not head_sha:
        row.notes = _append_note(row.notes, "verify_reparent_failed")
        return
    cmd = [
        *shipgate_cmd(),
        "verify",
        "--workspace",
        str(head_wt),
        "--config",
        manifest_rel,
        "--base",
        base_sha,
        "--head",
        head_sha,
    ]
    if baseline_path is not None:
        cmd += ["--baseline", str(baseline_path.resolve())]
    cmd += [
        "--ci-mode",
        "advisory",
        "--format",
        "json",
        "--out",
        str((tmp_path / "verify-reports").resolve()),
    ]
    result = _run(cmd)
    payload = _parse_json(result.stdout)
    if payload is None:
        row.notes = _append_note(
            row.notes, f"verify_unparseable_exit_{result.returncode}"
        )
        return
    row.verify_verdict = str(payload.get("merge_verdict") or "")
    can_merge = payload.get("can_merge_without_human")
    row.verify_can_merge = can_merge if isinstance(can_merge, bool) else None
    release_decision = payload.get("release_decision") or {}
    if isinstance(release_decision, dict):
        row.verify_decision = str(release_decision.get("decision") or "")
    review = payload.get("capability_review") or {}
    if isinstance(review, dict):
        trust = review.get("trust_root_touched")
        row.verify_trust_root_touched = trust if isinstance(trust, bool) else None
        weakened = review.get("policy_weakened")
        row.verify_policy_weakened = weakened if isinstance(weakened, bool) else None
        row.verify_cap_added = _as_int(review.get("added"))
        row.verify_cap_modified = _as_int(review.get("modified"))
        row.verify_cap_removed = _as_int(review.get("removed"))


def _commit_manifest(worktree: Path, manifest_rel: str) -> str | None:
    """Commit the injected manifest on the worktree's detached HEAD.

    Returns the new commit SHA (object-db only — no branch moves), or the
    current HEAD when the manifest is already tracked and unchanged.
    """

    add = _git(worktree, ["add", "--", manifest_rel])
    if add.returncode != 0:
        return None
    status = _git(worktree, ["status", "--porcelain", "--", manifest_rel])
    if status.returncode != 0:
        return None
    if status.stdout.strip():
        commit = _git(
            worktree,
            [
                "-c",
                "user.email=miner@local",
                "-c",
                "user.name=shipgate-miner",
                "commit",
                "-q",
                "--no-verify",
                "-m",
                "miner: inject cold-start manifest",
            ],
        )
        if commit.returncode != 0:
            return None
    head = _git(worktree, ["rev-parse", "HEAD"])
    if head.returncode != 0:
        return None
    return head.stdout.strip() or None


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
            "agent-boundary-json",
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
    _record_head_report(row, report)
    return True


def _tool_count(report: dict) -> int | None:
    """Total tools the scan enumerated.

    Lives in ``tool_surface.total_tools`` (``tool_inventory`` length is the
    fallback) — NOT in ``summary``. The previous read looked in ``summary``
    for ``tools_scanned``/``tool_count``, keys ``ReportSummary`` does not
    carry, so ``tools_scanned`` came back null on every run — blanking the
    denominator the IE-threshold ratio (low_confidence_tools / total_tools)
    is calibrated against.
    """
    surface = report.get("tool_surface") or {}
    total = surface.get("total_tools")
    if isinstance(total, int):
        return total
    inventory = report.get("tool_inventory")
    if isinstance(inventory, list):
        return len(inventory)
    return None


def _record_head_report(row: MinedRow, report: dict) -> None:
    """Project a parsed head ``report.json`` onto the row.

    Pure (no I/O) so the head-decision/evidence capture is unit-testable
    without running a scan.
    """
    decision = report.get("release_decision") or {}
    row.head_decision = str(decision.get("decision") or "")
    row.head_blockers = _safe_len(decision.get("blockers"))
    row.head_review_items = _safe_len(decision.get("review_items"))
    coverage = decision.get("evidence_coverage") or {}
    row.evidence_gaps = _safe_len(coverage.get("evidence_gaps"))
    row.tools_scanned = _tool_count(report)


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


def _rev_parse(repo_path: Path, rev: str) -> str | None:
    result = _git(repo_path, ["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"])
    sha = result.stdout.strip()
    if result.returncode != 0 or not sha:
        return None
    return sha


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
