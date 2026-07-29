"""Hand the installed Stop hook the verify run the agent already did.

A governed turn verifies twice: the coding agent runs ``verify`` because the
previous result told it to, and then the Stop hook runs an identical ``verify``
because it has no way to know that happened. The second run changes nothing and
costs the user a second or more of every turn.

This records the finished run where the hook already keeps its own state — the
git directory, alongside ``last_verified_signature`` — so the hook can *report*
that result instead of recomputing it, but only when it can prove the
repository has not moved and that the run it is reporting is the run it would
otherwise have performed.

Three rules this module exists to keep:

- **Never the workspace.** An earlier attempt read ``verifier.json`` out of the
  reports directory, which anything in the workspace can write — including the
  agent whose work is being judged. The git directory is the same trust tier as
  the signature cache the hook already keeps: forging it can only make an
  advisory hook echo a forged state, exactly as forging
  ``last_verified_signature`` already makes it skip verification. PR-time verify
  and CI read none of this.
- **Never more trusted than fresh.** The record carries no verdict the hook
  would not have gotten from a fresh run, and the hook routes it through the
  same switch. On any mismatch — or any doubt — the hook re-verifies.
- **Only a run the hook could have produced.** The hook invokes verify with
  workspace, config, base, head and ci-mode and nothing else. A run that used a
  baseline, a diff reference, policy packs, an authorization file, a fail-on
  set, or non-default plugin/heuristic modes answered a *different* question,
  so it is never recorded. Same for a run whose inputs the hook's identity
  cannot see (an ignored tool source) or whose worktree moved mid-scan.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agents_shipgate.cli.verify.git import (
    commit_sha,
    git_path,
    ignored_paths,
    worktree_identity,
)

STATE_FILENAME = "agents-shipgate-hooks-state.json"
RECORD_KEY = "last_verify"


def record_verify_for_hooks(
    *,
    git_root: Path,
    config: str,
    ci_mode: str,
    base_ref: str | None,
    base_commit_before: str | None,
    head_ref: str | None,
    identity_before: str | None,
    input_paths: list[str],
    input_set_id: str | None,
    decision: str,
    blockers: int,
    review_items: int,
    control: dict[str, Any],
) -> None:
    """Record a completed default-shaped worktree verify for the Stop hook.

    ``identity_before`` is the worktree identity captured *before* the scan
    started. Recomputing it here and requiring the two to match closes the
    window where an edit lands while the scan is running: without it, the
    verdict for the pre-edit tree would be filed under the post-edit state, and
    the hook would report a result for work it never saw. ``base_commit_before``
    does the same for a base ref that advances mid-scan.

    Fail-soft by construction: any problem skips the record and the hook simply
    runs its own verify, which is the behavior this replaces.
    """

    try:
        if identity_before is None:
            return
        if worktree_identity(git_root) != identity_before:
            return
        if base_ref:
            current_base = commit_sha(git_root, base_ref)
            if not current_base or current_base != base_commit_before:
                return
        elif base_commit_before:
            return
        # An ignored input is read by the scan and invisible to the identity,
        # so a repository that has one can never reuse. Refusing the record is
        # the whole mitigation: the hook needs no knowledge of the manifest.
        ignored = ignored_paths(git_root, input_paths)
        if ignored is None or ignored:
            return
        record = {
            "identity": identity_before,
            "input_set_id": input_set_id,
            "config": config,
            "ci_mode": ci_mode,
            "base_ref": base_ref or "",
            "base_commit": base_commit_before or "",
            "head_ref": head_ref or "",
            "decision": decision,
            "blockers": blockers,
            "review_items": review_items,
            "control": control,
        }
        state = _read_state(git_root)
        state[RECORD_KEY] = record
        _write_state(git_root, state)
    except Exception:  # noqa: BLE001 - an advisory optimization never fails a run.
        return


def discard_hook_verify_record(git_root: Path) -> None:
    """Drop any recorded run — used when this run must not be reusable."""

    try:
        state = _read_state(git_root)
        if state.pop(RECORD_KEY, None) is not None:
            _write_state(git_root, state)
    except Exception:  # noqa: BLE001 - advisory only.
        return


def _state_file(git_root: Path) -> Path:
    return git_path(git_root, STATE_FILENAME)


def _read_state(git_root: Path) -> dict[str, Any]:
    path = _state_file(git_root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(git_root: Path, data: dict[str, Any]) -> None:
    # Merge-then-atomic-replace, matching the hook: the same file carries the
    # hook's verification signature and the session's approved surfaces, and a
    # torn advisory cache is worse than a stale one.
    path = _state_file(git_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp.write_text(payload, encoding="utf-8")
    os.replace(temp, path)


__all__ = [
    "RECORD_KEY",
    "STATE_FILENAME",
    "discard_hook_verify_record",
    "record_verify_for_hooks",
]
