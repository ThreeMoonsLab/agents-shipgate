"""Hand the installed Stop hook the verify run the agent already did.

A governed turn verifies twice: the coding agent runs ``verify`` because the
previous result told it to, and then the Stop hook runs an identical ``verify``
because it has no way to know that happened. The second run changes nothing and
costs the user a second or more of every turn.

This records the finished run where the hook already keeps its own state — the
git directory, alongside ``last_verified_signature`` — so the hook can *report*
that result instead of recomputing it, but only when it can prove the repository
has not moved since (see :func:`agents_shipgate.cli.verify.git.worktree_identity`
and the identity fields below).

Two rules this module exists to keep:

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
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agents_shipgate.cli.verify.git import commit_sha, git_path, worktree_identity

STATE_FILENAME = "agents-shipgate-hooks-state.json"
RECORD_KEY = "last_verify"


def record_verify_for_hooks(
    *,
    git_root: Path,
    config: str,
    ci_mode: str,
    base_ref: str | None,
    head_ref: str | None,
    decision: str,
    blockers: int,
    review_items: int,
    control: dict[str, Any],
) -> None:
    """Record a completed worktree verify for the installed Stop hook.

    Fail-soft by construction: any problem skips the record and the hook simply
    runs its own verify, which is the behavior this replaces.
    """

    try:
        identity = worktree_identity(git_root)
        if identity is None:
            return
        record = {
            "identity": identity,
            "config": config,
            "ci_mode": ci_mode,
            "base_ref": base_ref or "",
            # The base is a moving ref; pin what it pointed at. A base that
            # advanced between the two runs is a different comparison.
            "base_commit": (commit_sha(git_root, base_ref) or "") if base_ref else "",
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


__all__ = ["RECORD_KEY", "STATE_FILENAME", "record_verify_for_hooks"]
