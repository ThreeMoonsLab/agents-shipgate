"""Builders and validators for the reproducible verification identity graph."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata
import json
import os
import platform
import stat
import tempfile
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml
from packaging.requirements import Requirement

from agents_shipgate import __version__
from agents_shipgate.checks.registry import _plugins_enabled, check_catalog
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.static_inputs import active_static_input_snapshot
from agents_shipgate.inputs.protocol import REGISTRY
from agents_shipgate.schemas.manifest.declared_paths import (
    declared_manifest_input_paths,
)
from agents_shipgate.schemas.verification_identity import (
    VerificationArtifactManifest,
    VerificationArtifactRef,
    VerificationBlob,
    VerificationEngineRequirement,
    VerificationExecutor,
    VerificationGitSubject,
    VerificationInputSet,
    VerificationPlan,
    VerificationReceipt,
    VerificationSubject,
    VerificationTask,
    VerificationUnitResult,
    content_id,
)

_GENERATED_DISTRIBUTION_DIRECTORY_NAMES = frozenset({"agents-shipgate-reports"})
_MAX_PLAN_INPUT_FILE_BYTES = 64 * 1024 * 1024


def sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def sha256_file(path: Path) -> str:
    snapshot = active_static_input_snapshot()
    if snapshot is not None and snapshot.has(path):
        return sha256_bytes(
            snapshot.read_bytes(
                path,
                max_bytes=_MAX_PLAN_INPUT_FILE_BYTES,
            )
        )
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def build_blob(*, path: Path, logical_path: str, source: str) -> VerificationBlob:
    snapshot = active_static_input_snapshot()
    data = (
        snapshot.read_bytes(path, max_bytes=_MAX_PLAN_INPUT_FILE_BYTES)
        if snapshot is not None and snapshot.has(path)
        else path.read_bytes()
    )
    return VerificationBlob(
        path=logical_path,
        sha256=sha256_bytes(data),
        size_bytes=len(data),
        source=source,  # type: ignore[arg-type]
    )


def build_engine_requirement(*, plugins_enabled: bool | None) -> VerificationEngineRequirement:
    effective_plugins_enabled = _plugins_enabled(plugins_enabled)
    engine_distribution_sha256 = _engine_distribution_sha256()
    source_project = _source_project_metadata()
    if source_project is None:
        metadata = importlib.metadata.metadata("agents-shipgate")
        declared_requirements = metadata.get_all("Requires-Dist") or []
    else:
        declared_requirements = list(source_project.get("dependencies") or [])
    if effective_plugins_enabled:
        declared_requirements.extend(_plugin_dependency_requirements())
    requirements = _installed_dependency_closure(declared_requirements)
    adapters = sorted(REGISTRY.clone()._adapters)  # noqa: SLF001 - public contract digest.
    plugin_entries = _effective_plugin_entries(
        source_project=source_project,
        engine_distribution_sha256=engine_distribution_sha256,
    )
    if effective_plugins_enabled and any(
        entry.endswith(":unresolved-distribution") for entry in plugin_entries
    ):
        raise ValueError(
            "an enabled Agents Shipgate plugin has no resolvable distribution identity"
        )
    checks = [
        row.model_dump(mode="json")
        for row in check_catalog(plugins_enabled=effective_plugins_enabled)
    ]
    payload = {
        "package": "agents-shipgate",
        "version": __version__,
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "platform": platform.system().lower(),
        "engine_distribution_sha256": engine_distribution_sha256,
        "dependency_set_sha256": content_id(requirements),
        "adapter_set_sha256": content_id(adapters),
        "plugin_set_sha256": content_id(
            {"enabled": effective_plugins_enabled, "entries": plugin_entries}
        ),
        "policy_catalog_sha256": content_id(checks),
    }
    return VerificationEngineRequirement(
        engine_requirement_id=content_id(payload),
        **payload,
    )


def build_executor(engine: VerificationEngineRequirement) -> VerificationExecutor:
    payload = {
        "engine_requirement_id": engine.engine_requirement_id,
        "runtime_sha256": content_id(
            {
                "engine_requirement_id": engine.engine_requirement_id,
                "canonical_json": "sorted-keys-utf8-v1",
            }
        ),
    }
    return VerificationExecutor(executor_id=content_id(payload), **payload)


def validate_engine_requirement(
    required: VerificationEngineRequirement,
    *,
    plugins_enabled: bool | None,
) -> None:
    """Require the local installed engine to equal the plan descriptor."""

    actual = build_engine_requirement(plugins_enabled=plugins_enabled)
    if actual != required:
        mismatches = [
            field
            for field in type(required).model_fields
            if getattr(actual, field) != getattr(required, field)
        ]
        raise ValueError(
            "local verification engine does not satisfy the plan: " + ", ".join(sorted(mismatches))
        )


def build_verification_plan(
    *,
    git_root: Path,
    input_root: Path,
    config_path: Path,
    config_logical_path: str,
    base_ref: str | None,
    head_ref: str,
    archived_head: bool,
    repository_id: str,
    base_commit_sha: str | None,
    base_tree_sha: str | None,
    source_head_commit_sha: str | None = None,
    head_commit_sha: str | None,
    head_tree_sha: str | None,
    merge_base_sha: str | None,
    changed_files: list[str],
    diff_text: str,
    baseline_path: Path | None,
    diff_from_path: Path | None,
    policy_pack_paths: list[Path],
    evaluation_date: str,
    options: dict[str, Any],
    plugins_enabled: bool | None,
    worktree_overlay_paths: list[str] | None = None,
    diff_logical_path: str = "verification-input.diff",
    external_input_root: Path | None = None,
    captured_input_paths: list[Path] | None = None,
) -> VerificationPlan:
    effective_plugins_enabled = _plugins_enabled(plugins_enabled)
    normalized_options = dict(options)
    normalized_options["plugins_enabled"] = effective_plugins_enabled
    overlay_paths = sorted(
        set(changed_files if worktree_overlay_paths is None else worktree_overlay_paths)
    )
    if not archived_head:
        # The evaluated change set is merge-base-relative, while the overlay is
        # HEAD-relative. Keeping the latter path set inside the content-
        # addressed inputs makes cancellations and overlapping edits
        # reproducible without polluting policy evaluation with non-effective
        # paths.
        normalized_options["worktree_overlay_paths"] = overlay_paths
    overlay = _worktree_overlay(git_root, overlay_paths) if not archived_head else []
    overlay_hash = content_id(overlay) if overlay else None
    if not archived_head and source_head_commit_sha is not None:
        raise ValueError("worktree-overlay plans cannot declare a source head commit")
    if (
        archived_head
        and source_head_commit_sha is not None
        and source_head_commit_sha != head_commit_sha
    ):
        raise ValueError("authorization source head must equal the evaluated committed head")
    git_subject = VerificationGitSubject(
        repository_id=repository_id,
        base_ref=base_ref,
        base_commit_sha=base_commit_sha,
        base_tree_sha=base_tree_sha,
        head_ref=head_ref,
        source_head_commit_sha=source_head_commit_sha,
        head_commit_sha=head_commit_sha,
        head_tree_sha=head_tree_sha,
        merge_base_sha=merge_base_sha,
        snapshot_kind="committed_tree" if archived_head else "worktree_overlay",
        worktree_overlay_sha256=overlay_hash,
    )
    subject = VerificationSubject(subject_id=content_id(git_subject), git=git_subject)

    config_blob = build_blob(
        path=config_path,
        logical_path=config_logical_path,
        source="git_blob" if archived_head else "worktree",
    )
    diff_bytes = diff_text.encode("utf-8")
    diff_blob = VerificationBlob(
        path=diff_logical_path,
        sha256=sha256_bytes(diff_bytes),
        size_bytes=len(diff_bytes),
        source="generated",
    )
    # Changed files are hashed under the root the scan actually read: the
    # archived tree for a committed snapshot, the worktree otherwise.
    changed_root = input_root if archived_head else git_root
    excluded_paths = {
        _lexical_path(path)
        for path in [
            config_path,
            *policy_pack_paths,
            *([baseline_path] if baseline_path is not None else []),
            *([diff_from_path] if diff_from_path is not None else []),
            *(changed_root / path for path in changed_files),
        ]
    }
    # Prefer what the adapters actually read. Fall back to what the manifest
    # declares they will read when no read boundary was observed — an unevaluated
    # plan, or a committed-tree scan the worktree snapshot cannot see. Both
    # branches must reach every adapter input: a path that lands in neither can
    # change bytes while ``input_set_id`` stays byte-identical.
    candidate_input_paths = (
        _manifest_declared_input_paths(config_path=config_path, input_root=input_root)
        if captured_input_paths is None
        else list(captured_input_paths)
    )
    tool_sources = _blobs(
        [
            path
            for path in candidate_input_paths
            if _lexical_path(path) not in excluded_paths
        ],
        root=input_root,
        source="git_blob" if archived_head else "worktree",
    )
    bundle_root = external_input_root or input_root
    policy_blobs = _blobs(
        policy_pack_paths,
        root=bundle_root,
        source="external_input",
    )
    changed_blobs = _existing_changed_blobs(
        changed_files,
        root=input_root if archived_head else git_root,
        source="git_blob" if archived_head else "worktree",
    )
    baseline_blob = _optional_blob(baseline_path, root=bundle_root)
    diff_from_blob = _optional_blob(diff_from_path, root=bundle_root)
    inputs_payload = {
        "evaluation_date": evaluation_date,
        "config": config_blob,
        "diff": diff_blob,
        "baseline": baseline_blob,
        "diff_from": diff_from_blob,
        "policy_packs": policy_blobs,
        "tool_sources": tool_sources,
        "changed_paths": sorted(set(changed_files)),
        "changed_files": changed_blobs,
        "options": normalized_options,
    }
    inputs = VerificationInputSet(
        input_set_id=content_id(
            {
                key: (
                    value.model_dump(mode="json")
                    if hasattr(value, "model_dump")
                    else [item.model_dump(mode="json") for item in value]
                    if isinstance(value, list) and value and hasattr(value[0], "model_dump")
                    else value
                )
                for key, value in inputs_payload.items()
            }
        ),
        **inputs_payload,
    )
    engine = build_engine_requirement(plugins_enabled=effective_plugins_enabled)
    task_payload = {
        "kind": "evaluate",
        "shard": 0,
        "shard_count": 1,
        "input_paths": sorted(
            {
                config_blob.path,
                diff_blob.path,
                *(blob.path for blob in tool_sources),
                *(b.path for b in policy_blobs),
                *(blob.path for blob in changed_blobs),
                *([baseline_blob.path] if baseline_blob is not None else []),
                *([diff_from_blob.path] if diff_from_blob is not None else []),
            }
        ),
    }
    task = VerificationTask(task_id=content_id(task_payload), **task_payload)
    request_payload = {
        "subject_id": subject.subject_id,
        "input_set_id": inputs.input_set_id,
        "engine_requirement_id": engine.engine_requirement_id,
        "task_ids": [task.task_id],
    }
    return VerificationPlan(
        request_id=content_id(request_payload),
        subject=subject,
        inputs=inputs,
        engine=engine,
        tasks=[task],
    )


def build_unit_result(
    *,
    plan: VerificationPlan,
    normalized_ir: dict[str, Any],
    status: str = "succeeded",
    issues: list[str] | None = None,
) -> VerificationUnitResult:
    executor = build_executor(plan.engine)
    payload = {
        "request_id": plan.request_id,
        "task_id": plan.tasks[0].task_id,
        "executor": executor,
        "status": status,
        "normalized_ir": normalized_ir,
        "issues": list(issues or []),
    }
    return VerificationUnitResult(
        unit_result_id=content_id(
            {
                key: value.model_dump(mode="json") if hasattr(value, "model_dump") else value
                for key, value in payload.items()
            }
        ),
        **payload,
    )


def build_terminal_receipt(
    *,
    plan: VerificationPlan,
    unit_results: list[VerificationUnitResult],
    decision: str | None,
    merge_verdict: str,
    can_merge_without_human: bool,
    artifact_paths: dict[str, Path],
    artifact_root: Path | None = None,
    attempt_id: str | None = None,
) -> tuple[VerificationArtifactManifest, VerificationReceipt]:
    executor_ids = {result.executor.executor_id for result in unit_results}
    if len(executor_ids) != 1:
        raise ValueError("all unit results must bind one exact executor")
    for result in unit_results:
        if result.request_id != plan.request_id:
            raise ValueError("unit result request_id does not match plan")
        if result.task_id not in {task.task_id for task in plan.tasks}:
            raise ValueError("unit result task is not present in the plan")
        if result.executor.engine_requirement_id != plan.engine.engine_requirement_id:
            raise ValueError("unit result executor does not satisfy the plan engine")
        if result.status != "succeeded":
            raise ValueError("failed unit results cannot produce a terminal receipt")
    if {result.task_id for result in unit_results} != {task.task_id for task in plan.tasks}:
        raise ValueError("terminal receipt requires exactly one result for every task")
    if len(unit_results) != len(plan.tasks):
        raise ValueError("terminal receipt requires exactly one result for every task")

    decision_payload = {
        "request_id": plan.request_id,
        "unit_result_ids": sorted(result.unit_result_id for result in unit_results),
        "decision": decision,
        "merge_verdict": merge_verdict,
        "can_merge_without_human": can_merge_without_human,
    }
    decision_id = content_id(decision_payload)
    existing_paths = {
        name: path.resolve() for name, path in sorted(artifact_paths.items()) if path.is_file()
    }
    if not existing_paths:
        raise ValueError("terminal receipt requires at least one artifact")
    resolved_root = (
        artifact_root.resolve()
        if artifact_root is not None
        else next(iter(existing_paths.values())).parent
    )
    refs: dict[str, VerificationArtifactRef] = {}
    for name, path in existing_paths.items():
        try:
            logical_path = path.relative_to(resolved_root).as_posix()
        except ValueError as exc:
            raise ValueError(f"receipt artifact {name!r} escapes the artifact root") from exc
        refs[name] = VerificationArtifactRef(
            path=logical_path,
            sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
            media_type=_media_type(path),
        )
    manifest_payload = {
        "request_id": plan.request_id,
        "decision_id": decision_id,
        "artifacts": refs,
    }
    manifest = VerificationArtifactManifest(
        artifact_set_id=content_id(
            {
                key: (
                    {name: ref.model_dump(mode="json") for name, ref in value.items()}
                    if key == "artifacts"
                    else value
                )
                for key, value in manifest_payload.items()
            }
        ),
        **manifest_payload,
    )
    receipt_payload = {
        "request_id": plan.request_id,
        "subject_id": plan.subject.subject_id,
        "input_set_id": plan.inputs.input_set_id,
        "engine_requirement_id": plan.engine.engine_requirement_id,
        "executor_id": next(iter(executor_ids)),
        "decision_id": decision_id,
        "artifact_set_id": manifest.artifact_set_id,
        "unit_result_ids": sorted(result.unit_result_id for result in unit_results),
        "attempt_id": attempt_id,
        "decision": decision,
        "merge_verdict": merge_verdict,
        "can_merge_without_human": can_merge_without_human,
        "artifact_manifest": manifest,
    }
    return manifest, VerificationReceipt(
        receipt_id=content_id(
            {
                key: value.model_dump(mode="json") if hasattr(value, "model_dump") else value
                for key, value in receipt_payload.items()
                if key != "attempt_id"
            }
        ),
        **receipt_payload,
    )


def validate_receipt_artifacts(receipt: VerificationReceipt, *, root: Path) -> None:
    resolved: dict[str, Path] = {}
    for name, ref in receipt.artifact_manifest.artifacts.items():
        path = Path(ref.path)
        candidate = path if path.is_absolute() else root / path
        if not candidate.is_file():
            raise ValueError(f"receipt artifact {name!r} is missing: {ref.path}")
        if sha256_file(candidate) != ref.sha256:
            raise ValueError(f"receipt artifact {name!r} hash does not match")
        if candidate.stat().st_size != ref.size_bytes:
            raise ValueError(f"receipt artifact {name!r} size does not match")
        resolved[name] = candidate

    manifest_path = root / "verification-artifacts.json"
    if not manifest_path.is_file():
        raise ValueError("terminal artifact manifest is missing")
    external_manifest = VerificationArtifactManifest.model_validate(
        _load_json_object(manifest_path)
    )
    if external_manifest != receipt.artifact_manifest:
        raise ValueError("terminal artifact manifest disagrees with the receipt")

    plan_path = resolved.get("verification_plan_json")
    if plan_path is None:
        raise ValueError("receipt does not bind a verification plan")
    plan = VerificationPlan.model_validate(_load_json_object(plan_path))
    diff_path = resolved.get("verification_input_diff")
    if diff_path is None:
        raise ValueError("receipt does not bind the verification diff input")
    if sha256_file(diff_path) != plan.inputs.diff.sha256:
        raise ValueError("receipt diff artifact disagrees with the verification plan")
    if diff_path.stat().st_size != plan.inputs.diff.size_bytes:
        raise ValueError("receipt diff artifact size disagrees with the verification plan")
    if plan.inputs.diff_from is not None:
        base_report_path = resolved.get("verification_base_report_json")
        if base_report_path is None:
            raise ValueError("receipt does not bind the verification base report input")
        if sha256_file(base_report_path) != plan.inputs.diff_from.sha256:
            raise ValueError("receipt base report disagrees with the verification plan")
        if base_report_path.stat().st_size != plan.inputs.diff_from.size_bytes:
            raise ValueError("receipt base report size disagrees with the verification plan")
    mirrors = {
        "request_id": plan.request_id,
        "subject_id": plan.subject.subject_id,
        "input_set_id": plan.inputs.input_set_id,
        "engine_requirement_id": plan.engine.engine_requirement_id,
    }
    for field, expected in mirrors.items():
        if getattr(receipt, field) != expected:
            raise ValueError(f"receipt {field} disagrees with the verification plan")

    unit_paths = [
        path
        for name, path in sorted(resolved.items())
        if name.startswith("verification_unit_result")
    ]
    if not unit_paths:
        raise ValueError("receipt does not bind any verification unit results")
    units = [VerificationUnitResult.model_validate(_load_json_object(path)) for path in unit_paths]
    if sorted(unit.unit_result_id for unit in units) != sorted(receipt.unit_result_ids):
        raise ValueError("receipt unit_result_ids disagree with the bound unit artifacts")
    if any(unit.request_id != receipt.request_id for unit in units):
        raise ValueError("receipt contains a unit result for another request")
    if any(unit.status != "succeeded" for unit in units):
        raise ValueError("terminal receipt contains a failed unit result")
    if {unit.executor.executor_id for unit in units} != {receipt.executor_id}:
        raise ValueError("receipt executor_id disagrees with its unit results")

    verify_run_path = resolved.get("verify_run_json")
    if verify_run_path is None:
        raise ValueError("receipt does not bind verify-run.json")
    from agents_shipgate.schemas.verify_run import VerifyRunArtifact

    verify_run = VerifyRunArtifact.model_validate(_load_json_object(verify_run_path))
    if verify_run.request_id != receipt.request_id:
        raise ValueError("verify-run request_id disagrees with the receipt")
    if verify_run.decision_id != receipt.decision_id:
        raise ValueError("verify-run decision_id disagrees with the receipt")
    if verify_run.executor.executor_id != receipt.executor_id:
        raise ValueError("verify-run executor_id disagrees with the receipt")
    if sorted(verify_run.unit_result_ids) != sorted(receipt.unit_result_ids):
        raise ValueError("verify-run unit_result_ids disagree with the receipt")
    outcome = verify_run.outcome
    if (
        outcome.decision,
        outcome.merge_verdict,
        outcome.can_merge_without_human,
    ) != (
        receipt.decision,
        receipt.merge_verdict,
        receipt.can_merge_without_human,
    ):
        raise ValueError("verify-run outcome disagrees with the receipt")

    _validate_verify_run_artifact_refs(
        verify_run=verify_run,
        receipt=receipt,
        resolved=resolved,
    )

    _validate_projection_ids(receipt, resolved)


@contextlib.contextmanager
def validated_receipt_snapshot(
    *,
    receipt_path: Path,
    root: Path,
    allowed_artifact_names: frozenset[str] | None = None,
    max_artifact_count: int = 64,
    max_artifact_size: int = 64 * 1024 * 1024,
    max_total_size: int = 256 * 1024 * 1024,
) -> Iterator[tuple[VerificationReceipt, Path]]:
    """Yield one immutable, validated snapshot of a terminal artifact closure.

    Every source file is opened beneath ``root`` with no-follow directory/file
    descriptors, read once, checked against the embedded receipt, and copied
    into a private temporary directory.  Consumers must parse only the yielded
    snapshot so validation and use operate on the exact same bytes.
    """

    source_root = root.resolve(strict=True)
    expected_receipt = source_root / "verification-receipt.json"
    if receipt_path.resolve(strict=True) != expected_receipt:
        raise ValueError("receipt path must name the terminal receipt in artifacts-root")
    receipt_bytes = _read_regular_file_beneath(
        source_root,
        "verification-receipt.json",
        max_size=4 * 1024 * 1024,
    )
    receipt = VerificationReceipt.model_validate(_json_object_bytes(receipt_bytes, "receipt"))
    artifact_refs = receipt.artifact_manifest.artifacts
    if len(artifact_refs) > max_artifact_count:
        raise ValueError("receipt binds too many artifacts")
    if allowed_artifact_names is not None:
        unexpected = sorted(set(artifact_refs) - allowed_artifact_names)
        if unexpected:
            raise ValueError(
                "receipt binds artifacts outside the allowed execution closure: "
                + ", ".join(unexpected)
            )
    paths = [ref.path for ref in artifact_refs.values()]
    if len(paths) != len(set(paths)):
        raise ValueError("receipt binds more than one artifact name to the same path")
    declared_total = 0
    for name, ref in artifact_refs.items():
        if ref.size_bytes > max_artifact_size:
            raise ValueError(f"receipt artifact {name!r} exceeds the trusted size limit")
        declared_total += ref.size_bytes
        if declared_total > max_total_size:
            raise ValueError("receipt artifact closure exceeds the trusted total size limit")

    with tempfile.TemporaryDirectory(prefix="agents-shipgate-receipt-snapshot-") as raw:
        snapshot_root = Path(raw)
        (snapshot_root / "verification-receipt.json").write_bytes(receipt_bytes)
        for name, ref in receipt.artifact_manifest.artifacts.items():
            data = _read_regular_file_beneath(
                source_root,
                ref.path,
                max_size=ref.size_bytes,
            )
            if len(data) != ref.size_bytes:
                raise ValueError(f"receipt artifact {name!r} size does not match")
            if sha256_bytes(data) != ref.sha256:
                raise ValueError(f"receipt artifact {name!r} hash does not match")
            target = snapshot_root / ref.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

        manifest_bytes = _read_regular_file_beneath(
            source_root,
            "verification-artifacts.json",
            max_size=4 * 1024 * 1024,
        )
        external_manifest = VerificationArtifactManifest.model_validate(
            _json_object_bytes(manifest_bytes, "terminal artifact manifest")
        )
        if external_manifest != receipt.artifact_manifest:
            raise ValueError("terminal artifact manifest disagrees with the receipt")
        (snapshot_root / "verification-artifacts.json").write_bytes(manifest_bytes)

        validate_receipt_artifacts(receipt, root=snapshot_root)
        yield receipt, snapshot_root


def load_validated_receipt_artifacts(
    *,
    receipt_path: Path,
    root: Path,
    allowed_artifact_names: frozenset[str] | None = None,
    max_artifact_count: int = 64,
    max_artifact_size: int = 64 * 1024 * 1024,
    max_total_size: int = 256 * 1024 * 1024,
) -> tuple[VerificationReceipt, dict[str, bytes]]:
    """Load receipt-bound artifact bytes from one validated snapshot."""

    with validated_receipt_snapshot(
        receipt_path=receipt_path,
        root=root,
        allowed_artifact_names=allowed_artifact_names,
        max_artifact_count=max_artifact_count,
        max_artifact_size=max_artifact_size,
        max_total_size=max_total_size,
    ) as (
        receipt,
        snapshot_root,
    ):
        artifacts = {
            name: (snapshot_root / ref.path).read_bytes()
            for name, ref in receipt.artifact_manifest.artifacts.items()
        }
    return receipt, artifacts


def _read_regular_file_beneath(root: Path, logical_path: str, *, max_size: int) -> bytes:
    parts = Path(logical_path).parts
    if not parts or Path(logical_path).is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"receipt artifact path is not portable: {logical_path!r}")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptors: list[int] = []
    try:
        current = os.open(root, directory_flags)
        descriptors.append(current)
        for part in parts[:-1]:
            current = os.open(part, directory_flags, dir_fd=current)
            descriptors.append(current)
        file_descriptor = os.open(parts[-1], file_flags, dir_fd=current)
        descriptors.append(file_descriptor)
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"receipt artifact is not a regular file: {logical_path}")
        if before.st_size > max_size:
            raise ValueError(f"receipt artifact exceeds its size limit: {logical_path}")
        chunks: list[bytes] = []
        remaining = max_size + 1
        while remaining:
            chunk = os.read(file_descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(file_descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValueError(f"receipt artifact changed while it was read: {logical_path}")
        if len(data) > max_size:
            raise ValueError(f"receipt artifact exceeds its size limit: {logical_path}")
        return data
    except OSError as exc:
        raise ValueError(f"could not safely read receipt artifact {logical_path!r}: {exc}") from exc
    finally:
        for descriptor in reversed(descriptors):
            with contextlib.suppress(OSError):
                os.close(descriptor)


def _json_object_bytes(data: bytes, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not one UTF-8 JSON object") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return payload


def _validate_verify_run_artifact_refs(
    *,
    verify_run: Any,
    receipt: VerificationReceipt,
    resolved: dict[str, Path],
) -> None:
    """Close every non-cyclic verify-run artifact ref over receipt-bound bytes."""

    for name, nested_ref in verify_run.artifacts.items():
        # Older v3 runs could include the initial handoff even though the final
        # handoff necessarily embeds verify-run reproducibility. Treat that
        # edge as non-authoritative for compatibility; current writers omit it.
        if name == "agent_handoff_json":
            continue
        manifest_ref = receipt.artifact_manifest.artifacts.get(name)
        current_path = resolved.get(name)
        if manifest_ref is None or current_path is None:
            raise ValueError(f"verify-run artifact {name!r} is not bound by the terminal receipt")
        if not nested_ref.sha256:
            raise ValueError(f"verify-run artifact {name!r} lacks a content hash")
        nested_sha256 = (
            nested_ref.sha256
            if nested_ref.sha256.startswith("sha256:")
            else f"sha256:{nested_ref.sha256}"
        )
        if nested_sha256 != manifest_ref.sha256:
            raise ValueError(
                f"verify-run artifact {name!r} hash disagrees with the terminal receipt"
            )
        if sha256_file(current_path) != nested_sha256:
            raise ValueError(
                f"verify-run artifact {name!r} hash disagrees with current artifact bytes"
            )


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not parse receipt artifact {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"receipt artifact {path.name} must contain one JSON object")
    return payload


def _validate_projection_ids(
    receipt: VerificationReceipt,
    resolved: dict[str, Path],
) -> None:
    expected = {
        "request_id": receipt.request_id,
        "subject_id": receipt.subject_id,
        "input_set_id": receipt.input_set_id,
        "engine_requirement_id": receipt.engine_requirement_id,
        "decision_id": receipt.decision_id,
    }
    for name in ("report_json", "packet_json", "verifier_json"):
        path = resolved.get(name)
        if path is None:
            continue
        payload = _load_json_object(path)
        for field, value in expected.items():
            if payload.get(field) != value:
                raise ValueError(f"receipt artifact {name!r} has inconsistent {field}")
    handoff_path = resolved.get("agent_handoff_json")
    if handoff_path is not None:
        handoff = _load_json_object(handoff_path)
        reproducibility = handoff.get("reproducibility")
        if not isinstance(reproducibility, dict):
            raise ValueError("agent handoff lacks reproducibility identity")
        handoff_expected = {**expected, "executor_id": receipt.executor_id}
        for field, value in handoff_expected.items():
            if reproducibility.get(field) != value:
                raise ValueError(f"agent handoff has inconsistent {field}")


def validate_plan_inputs(
    plan: VerificationPlan,
    *,
    root: Path,
    diff_path: Path | None = None,
    bundle_root: Path | None = None,
) -> None:
    """Fail closed unless every portable plan blob matches the supplied root."""

    blobs = [
        plan.inputs.config,
        *plan.inputs.tool_sources,
        *plan.inputs.policy_packs,
        *plan.inputs.changed_files,
    ]
    if plan.inputs.baseline is not None:
        blobs.append(plan.inputs.baseline)
    if plan.inputs.diff_from is not None:
        blobs.append(plan.inputs.diff_from)
    resolved_root = root.resolve()
    resolved_bundle_root = (bundle_root or resolved_root).resolve()
    for blob in blobs:
        candidate_root = (
            resolved_bundle_root
            if blob.source in {"external_input", "generated"}
            else resolved_root
        )
        candidate = (candidate_root / blob.path).resolve()
        if candidate != candidate_root and candidate_root not in candidate.parents:
            raise ValueError(f"plan input escapes supplied root: {blob.path}")
        if not candidate.is_file():
            raise ValueError(f"plan input is missing: {blob.path}")
        if sha256_file(candidate) != blob.sha256:
            raise ValueError(f"plan input hash does not match: {blob.path}")
        if candidate.stat().st_size != blob.size_bytes:
            raise ValueError(f"plan input size does not match: {blob.path}")
    resolved_diff = (diff_path or (resolved_root / plan.inputs.diff.path)).resolve()
    if not resolved_diff.is_file():
        raise ValueError(f"plan diff input is missing: {plan.inputs.diff.path}")
    if sha256_file(resolved_diff) != plan.inputs.diff.sha256:
        raise ValueError("plan diff input hash does not match")
    if resolved_diff.stat().st_size != plan.inputs.diff.size_bytes:
        raise ValueError("plan diff input size does not match")


def _manifest_declared_input_paths(*, config_path: Path, input_root: Path) -> list[Path]:
    """Expand every adapter input path the manifest declares, under ``input_root``.

    Covers the framework blocks as well as ``tool_sources`` — ``prompt_files``
    and the rest of the declared artifact paths are adapter inputs exactly like
    an MCP export is, and omitting them let a prompt edit leave ``input_set_id``
    unchanged (issue #299).
    """

    snapshot = active_static_input_snapshot()
    config_text = (
        snapshot.read_bytes(
            config_path,
            max_bytes=_MAX_PLAN_INPUT_FILE_BYTES,
        ).decode("utf-8", errors="strict")
        if snapshot is not None and snapshot.contains(config_path)
        else config_path.read_text(encoding="utf-8")
    )
    try:
        raw = yaml.safe_load(config_text)
    except yaml.YAMLError:
        # An unparseable manifest cannot declare inputs. It fails visibly in
        # the loader; identity construction must not raise a second, worse
        # error over the same bytes, which are already hashed as the config blob.
        return []
    resolved_root = input_root.resolve()
    paths: list[Path] = []
    for declared in declared_manifest_input_paths(raw):
        candidate = (config_path.parent / declared).resolve()
        if candidate != resolved_root and resolved_root not in candidate.parents:
            # Fail closed rather than drop it: an input outside the root cannot
            # be hashed portably, and silently skipping it is the hole this
            # enumeration exists to close. Routed as an input error because the
            # manifest is what has to change — `resolve_input_path` rejects the
            # same declaration the moment an adapter actually reads it.
            raise InputParseError(
                f"Declared input resolves outside the verification input root: {declared}"
            )
        if snapshot is not None and snapshot.contains(candidate):
            if snapshot.has(candidate):
                paths.append(candidate)
            else:
                paths.extend(snapshot.paths_under(candidate))
        else:
            paths.extend(_expand_path(candidate))
    return paths


def _lexical_path(path: Path) -> Path:
    return Path(os.path.abspath(os.path.normpath(os.fspath(path))))


def _expand_path(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(candidate for candidate in path.rglob("*") if candidate.is_file())
    return []


def _blobs(paths: list[Path], *, root: Path, source: str) -> list[VerificationBlob]:
    out: list[VerificationBlob] = []
    snapshot = active_static_input_snapshot()
    candidates: set[Path] = set()
    for candidate in paths:
        lexical = _lexical_path(candidate)
        if snapshot is not None and snapshot.contains(lexical):
            if snapshot.has(lexical):
                candidates.add(lexical)
        elif candidate.is_file():
            candidates.add(candidate.resolve())
    for path in sorted(candidates):
        logical = path.relative_to(root.resolve()).as_posix()
        out.append(build_blob(path=path, logical_path=logical, source=source))
    return sorted(out, key=lambda blob: blob.path)


def _optional_blob(path: Path | None, *, root: Path) -> VerificationBlob | None:
    if path is None:
        return None
    snapshot = active_static_input_snapshot()
    if snapshot is not None and snapshot.contains(path):
        if not snapshot.has(path):
            return None
    elif not path.is_file():
        return None
    resolved = path.resolve()
    try:
        logical = resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        logical = path.name
    return build_blob(path=resolved, logical_path=logical, source="external_input")


def _existing_changed_blobs(paths: list[str], *, root: Path, source: str) -> list[VerificationBlob]:
    candidates = [(root / path).resolve() for path in paths]
    return _blobs(candidates, root=root, source=source)


def _worktree_overlay(root: Path, paths: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    snapshot = active_static_input_snapshot()
    for relative in sorted(set(paths)):
        candidate = (root / relative).resolve()
        if root.resolve() not in candidate.parents:
            raise ValueError(f"worktree path escapes repository: {relative}")
        present = (
            snapshot.has(candidate)
            if snapshot is not None and snapshot.contains(candidate)
            else candidate.is_file()
        )
        captured_mode = (
            snapshot.mode(candidate)
            if present and snapshot is not None and snapshot.contains(candidate)
            else stat.S_IMODE(candidate.stat().st_mode)
            if present
            else None
        )
        rows.append(
            {
                "path": relative,
                "status": "present" if present else "deleted",
                "sha256": sha256_file(candidate) if present else None,
                "git_mode": (
                    "100755"
                    if captured_mode is not None and captured_mode & stat.S_IXUSR
                    else "100644"
                    if present
                    else None
                ),
            }
        )
    return rows


def _media_type(path: Path) -> str:
    if path.suffix == ".json":
        return "application/json"
    if path.suffix in {".md", ".txt"}:
        return "text/plain; charset=utf-8"
    if path.suffix == ".sarif":
        return "application/sarif+json"
    return "application/octet-stream"


def _installed_dependency_closure(requirements: list[str]) -> list[str]:
    pending = list(requirements)
    seen: set[str] = set()
    resolved: list[str] = []
    while pending:
        raw = pending.pop()
        try:
            requirement = Requirement(raw)
        except Exception:  # noqa: BLE001 - malformed installed metadata fails visibly below.
            resolved.append(f"invalid:{raw}")
            continue
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        name = _normalized_distribution_name(requirement.name)
        if name in seen:
            continue
        seen.add(name)
        try:
            distribution = importlib.metadata.distribution(requirement.name)
        except importlib.metadata.PackageNotFoundError:
            # Optional/extras requirements not installed are still identity
            # relevant because their absence affects available adapters.
            resolved.append(f"{name}=absent")
            continue
        resolved.append(
            f"{name}=={distribution.version}@{_distribution_record_sha256(distribution)}"
        )
        pending.extend(distribution.metadata.get_all("Requires-Dist") or [])
    return sorted(resolved)


def _engine_distribution_sha256() -> str:
    package_root = Path(__file__).resolve().parents[1]
    files: dict[str, str] = {}
    _add_distribution_tree(files, package_root, logical_root="")
    if not (package_root / "_meta").is_dir():
        # A source checkout does not contain Hatch's force-included wheel
        # directories. Project the exact force-include mapping into the same
        # logical package paths so a checkout and its built wheel have one
        # engine-content identity.
        repository_root = package_root.parents[1]
        mappings = (
            ("samples", "_fixtures"),
            ("AGENTS.md", "_meta/AGENTS.md"),
            ("STABILITY.md", "_meta/STABILITY.md"),
            ("docs/triggers.json", "_meta/triggers.json"),
            (".claude/commands/shipgate.md", "_meta/claude-command/shipgate.md"),
            ("docs/checks", "_meta/checks"),
            (
                "policies/codex-boundary.shipgate.yaml",
                "_meta/policies/codex-boundary.shipgate.yaml",
            ),
            (
                "policies/mcp-permissions.shipgate.yaml",
                "_meta/policies/mcp-permissions.shipgate.yaml",
            ),
            (
                "policies/host-boundary.shipgate.yaml",
                "_meta/policies/host-boundary.shipgate.yaml",
            ),
            (
                "policies/templates/agent-boundary-default.shipgate.yaml",
                "_meta/policies/agent-boundary.shipgate.yaml",
            ),
            ("policies/templates", "_meta/policies/templates"),
            ("adoption-kits", "_adoption_kits"),
        )
        for source, logical in mappings:
            candidate = repository_root / source
            if not candidate.exists():
                raise ValueError(f"source distribution input is missing: {source}")
            _add_distribution_tree(files, candidate, logical_root=logical)
    return content_id([{"path": path, "sha256": digest} for path, digest in sorted(files.items())])


def _add_distribution_tree(
    files: dict[str, str],
    source: Path,
    *,
    logical_root: str,
) -> None:
    candidates = [source] if source.is_file() else sorted(source.rglob("*"))
    for path in candidates:
        relative_parts = () if source.is_file() else path.relative_to(source).parts
        if (
            not path.is_file()
            or "__pycache__" in relative_parts
            or _GENERATED_DISTRIBUTION_DIRECTORY_NAMES.intersection(relative_parts)
            or path.suffix in {".pyc", ".pyo"}
        ):
            continue
        relative = "" if source.is_file() else path.relative_to(source).as_posix()
        logical = "/".join(part for part in (logical_root, relative) if part)
        digest = sha256_file(path)
        existing = files.get(logical)
        if existing is not None and existing != digest:
            raise ValueError(f"distribution inputs conflict at logical path: {logical}")
        files[logical] = digest


def _distribution_record_sha256(
    distribution: importlib.metadata.Distribution,
) -> str:
    record = distribution.read_text("RECORD")
    if record is not None:
        return sha256_bytes(record.encode("utf-8"))
    metadata = distribution.read_text("METADATA") or ""
    return sha256_bytes(metadata.encode("utf-8"))


def _source_project_metadata() -> dict[str, Any] | None:
    package_root = Path(__file__).resolve().parents[1]
    if (package_root / "_meta").is_dir():
        return None
    pyproject = package_root.parents[1] / "pyproject.toml"
    if not pyproject.is_file():
        return None
    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = payload.get("project")
    return project if isinstance(project, dict) else None


def _effective_plugin_entries(
    *,
    source_project: dict[str, Any] | None,
    engine_distribution_sha256: str,
) -> list[str]:
    entries: list[str] = []
    for entry in importlib.metadata.entry_points():
        if not entry.group.startswith("agents_shipgate"):
            continue
        distribution = getattr(entry, "dist", None)
        distribution_name = str(
            distribution.metadata.get("Name") if distribution is not None else ""
        )
        if source_project is not None and _normalized_distribution_name(distribution_name) == (
            "agents-shipgate"
        ):
            continue
        entries.append(
            f"{entry.group}:{entry.name}:{entry.value}:"
            f"{_entry_point_distribution(entry, engine_distribution_sha256)}"
        )
    if source_project is not None:
        for group, rows in (source_project.get("entry-points") or {}).items():
            if not str(group).startswith("agents_shipgate") or not isinstance(rows, dict):
                continue
            for name, value in rows.items():
                entries.append(
                    f"{group}:{name}:{value}:agents-shipgate=={__version__}@"
                    f"{engine_distribution_sha256}"
                )
    return sorted(set(entries))


def _plugin_dependency_requirements() -> list[str]:
    requirements: list[str] = []
    seen: set[str] = set()
    for entry in importlib.metadata.entry_points():
        if not entry.group.startswith("agents_shipgate"):
            continue
        distribution = getattr(entry, "dist", None)
        if distribution is None:
            continue
        name = _normalized_distribution_name(str(distribution.metadata.get("Name") or "unknown"))
        if name == "agents-shipgate" or name in seen:
            continue
        seen.add(name)
        requirements.extend(distribution.metadata.get_all("Requires-Dist") or [])
    return requirements


def _entry_point_distribution(
    entry: importlib.metadata.EntryPoint,
    engine_distribution_sha256: str,
) -> str:
    distribution = getattr(entry, "dist", None)
    distributions = [distribution] if distribution is not None else []
    if not distributions:
        top_level = entry.value.split(":", 1)[0].split(".", 1)[0]
        for name in importlib.metadata.packages_distributions().get(top_level, []):
            try:
                distributions.append(importlib.metadata.distribution(name))
            except importlib.metadata.PackageNotFoundError:
                continue
    if not distributions:
        return "unresolved-distribution"
    rows = []
    for item in distributions:
        name = _normalized_distribution_name(str(item.metadata.get("Name") or "unknown"))
        version = str(item.version or "unknown")
        digest = (
            engine_distribution_sha256
            if name == "agents-shipgate"
            else _distribution_record_sha256(item)
        )
        rows.append(f"{name}=={version}@{digest}")
    return ",".join(sorted(set(rows)))


def _normalized_distribution_name(value: str) -> str:
    return value.lower().replace("_", "-").replace(".", "-")


__all__ = [
    "build_engine_requirement",
    "build_executor",
    "build_terminal_receipt",
    "build_unit_result",
    "build_verification_plan",
    "sha256_file",
    "validate_receipt_artifacts",
    "validate_engine_requirement",
    "validate_plan_inputs",
]
