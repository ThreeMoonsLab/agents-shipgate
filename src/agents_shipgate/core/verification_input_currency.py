"""Origin-aware live validation of the input blobs a verification plan binds."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from agents_shipgate.core.input_directory_identity import (
    directory_input_exclusions,
    validate_directory_inputs,
)
from agents_shipgate.core.static_inputs import (
    DEFAULT_STATIC_INPUT_FILES,
    DEFAULT_STATIC_INPUT_TOTAL_BYTES,
    StaticInputSnapshot,
    read_static_input_bytes,
)
from agents_shipgate.core.trust_roots import IdentityBoundReadSession, IdentityReadBudget
from agents_shipgate.schemas.verification_identity import VerificationPlan, validate_portable_path

MAX_CURRENCY_INPUT_BYTES = 64 * 1024 * 1024
MAX_CURRENCY_TOTAL_BYTES = DEFAULT_STATIC_INPUT_TOTAL_BYTES


def write_portable_input(
    path: Path, *, root: Path, category: str, source_logical_path: str | None = None
) -> Path:
    """Export exactly the captured auxiliary bytes into the request bundle."""

    data = read_static_input_bytes(path, max_bytes=MAX_CURRENCY_INPUT_BYTES)
    digest = hashlib.sha256(data).hexdigest()
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", path.name).strip("._") or "input"
    # Equal bytes at a/policy.yaml and b/policy.yaml still have two live
    # origins. Keep both without leaking an absolute checkout/archive path.
    origin_suffix = (
        "-" + hashlib.sha256(_portable(source_logical_path).encode()).hexdigest()[:16]
        if source_logical_path is not None else ""
    )
    target = root / "verification-inputs" / category / f"{digest[:16]}{origin_suffix}-{safe_name}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def _portable(value: object) -> str:
    if not isinstance(value, str) or ":" in value or value == ".":
        raise ValueError("input origin must be a portable relative file path")
    return validate_portable_path(value)


def auxiliary_input_origin(
    path: Path, *, input_root: Path, git_root: Path, generated: bool = False
) -> dict[str, str | None]:
    """Describe the actual source before its portable copy loses the location.

    Absolute external locations are deliberately not exported. They are imports
    of captured bytes, whose portable copies (not their former origins) remain
    the input to this request.
    """

    if generated:
        return {"kind": "generated", "path": None}
    lexical = Path(os.path.abspath(os.path.normpath(os.fspath(path))))
    for root, kind in (
        (input_root, "git_blob" if input_root != git_root else "worktree"),
        (git_root, "worktree"),
    ):
        try:
            relative = lexical.relative_to(root).as_posix()
        except ValueError:
            continue
        return {"kind": kind, "path": validate_portable_path(relative)}
    return {"kind": "external_snapshot", "path": None}


def validate_current_plan_inputs(
    plan: VerificationPlan, *, root: Path, artifacts_root: Path
) -> None:
    """Reconfirm recorded inputs after the caller validates live HEAD/tree.

    Both evaluated-head and worktree blobs must still describe the matching
    local checkout. Historical/generated/imported inputs instead belong to the
    captured artifact graph. This is intentionally separate from worker replay.
    """

    validate_bound_plan_inputs(plan, root=root, artifacts_root=artifacts_root, live_origins=True)


def validate_bound_plan_inputs(
    plan: VerificationPlan, *, root: Path, artifacts_root: Path,
    live_origins: bool, diff_path: Path | None = None,
) -> None:
    """Bind bytes and membership in one observation, for replay or live reads.

    Replay consumes captured auxiliary copies; only a current-authority read
    also checks their original workspace paths. Neither mode evaluates policy.
    """

    from agents_shipgate.core.verification_identity import validate_dependency_inputs

    root = Path(os.path.abspath(root))
    artifacts_root = Path(os.path.abspath(artifacts_root))
    if diff_path is not None:
        diff_path = Path(os.path.abspath(diff_path))
    exclusions = directory_input_exclusions(plan)

    blobs = [
        plan.inputs.config, *plan.inputs.tool_sources, *plan.inputs.changed_files,
        *plan.inputs.policy_packs, plan.inputs.diff,
        *([plan.inputs.baseline] if plan.inputs.baseline is not None else []),
        *([plan.inputs.diff_from] if plan.inputs.diff_from is not None else []),
    ]
    portable = {blob.path for blob in blobs if blob.source in {"external_input", "artifact"}}
    declaration = plan.inputs.options.get("input_origins")
    origins = {}
    if declaration is not None:
        if (
            not isinstance(declaration, dict)
            or set(declaration) != {"version", "external"}
            or type(declaration["version"]) is not int
            or declaration["version"] != 1
            or not isinstance(declaration["external"], list)
        ):
            raise ValueError("invalid recorded input provenance; re-run verification")
        for row in declaration["external"]:
            if not isinstance(row, dict) or set(row) != {"input_path", "kind", "path"}:
                raise ValueError("invalid external input provenance")
            if not isinstance(row["kind"], str):
                raise ValueError("invalid external input origin kind")
            input_path = _portable(row["input_path"])
            if input_path in origins or input_path not in portable:
                raise ValueError("duplicate or unmatched external input provenance")
            if row["kind"] in {"worktree", "git_blob"}:
                _portable(row["path"])
                if row["kind"] == "git_blob" and plan.subject.git.snapshot_kind != "committed_tree":
                    raise ValueError("worktree request cannot claim a committed input origin")
            elif row["kind"] in {"generated", "external_snapshot"}:
                if row["path"] is not None:
                    raise ValueError("frozen input cannot name a live origin")
            else:
                raise ValueError("unknown external input provenance")
            origins[input_path] = row
    if set(origins) != portable:
        raise ValueError("external input origin is unavailable; re-run verification")

    expected: dict[Path, tuple[str, int]] = {}
    artifact_inputs: dict[Path, tuple[str, int]] = {}

    def bind(target: dict[Path, tuple[str, int]], path: Path, digest: str, size: int) -> None:
        value = (digest, size)
        if path in target and target[path] != value:
            raise ValueError("conflicting input identities for the same path")
        target[path] = value

    for blob in blobs:
        _portable(blob.path)
        if blob is plan.inputs.diff and diff_path is not None:
            bind(expected, diff_path, blob.sha256, blob.size_bytes)
            continue
        if blob.source in {"worktree", "git_blob"}:
            bind(expected, root / blob.path, blob.sha256, blob.size_bytes)
        elif blob.source in {"generated", "external_input", "artifact"}:
            bind(artifact_inputs, Path(blob.path), blob.sha256, blob.size_bytes)
            origin = origins.get(blob.path)
            if live_origins and origin is not None and origin["path"] is not None:
                bind(expected, root / origin["path"], blob.sha256, blob.size_bytes)
        else:
            raise ValueError("unknown recorded input source")
    # Anchor artifact traversal at the bundle root, including reports outside
    # the workspace. An immediate-parent anchor would miss an earlier symlink.
    # Workspace bytes, named dependencies and artifact inputs share one budget.
    budget = IdentityReadBudget(
        max_entries=DEFAULT_STATIC_INPUT_FILES * 32,
        max_total_bytes=MAX_CURRENCY_TOTAL_BYTES,
    )
    snapshot = StaticInputSnapshot(
        root, budget=budget, excluded_paths=[root / path for path in exclusions],
        external_paths=[diff_path] if diff_path is not None else [],
    )
    artifacts = IdentityBoundReadSession(artifacts_root, budget=budget)

    def check(data: bytes, digest: str, size: int) -> None:
        if len(data) != size or "sha256:" + hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("recorded input changed since verification")

    for collection, reader in ((expected, snapshot), (artifact_inputs, artifacts)):
        for path, (digest, size) in collection.items():
            if size > MAX_CURRENCY_INPUT_BYTES:
                raise ValueError("recorded input exceeds the currency read limit")
            check(reader.read_bytes(path, max_bytes=MAX_CURRENCY_INPUT_BYTES), digest, size)
    validate_dependency_inputs(plan, root=root, snapshot=snapshot)
    validate_directory_inputs(plan, snapshot=snapshot)
    snapshot.finish()
    artifacts.finish()
