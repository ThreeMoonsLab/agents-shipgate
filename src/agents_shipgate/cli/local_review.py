"""Provision and validate the side-effect-contained local-review workspace.

The manifest and reports live at reserved workspace-root paths.  Git ignores
them through an exact managed ``info/exclude`` block, while a content-addressed
binding under this checkout's private Git directory prevents an arbitrary file
from opting itself into provisional semantics.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_shipgate.core.errors import ConfigError
from agents_shipgate.invocation import render_command
from agents_shipgate.schemas.local_review import LocalReviewBinding
from agents_shipgate.schemas.verification_identity import content_id

LOCAL_REVIEW_MANIFEST_NAME = ".agents-shipgate-local-review.yaml"
LOCAL_REVIEW_REPORTS_NAME = ".agents-shipgate-local-review-reports"
_BINDING_DIRECTORY = Path("agents-shipgate/local-review")
_REPORTS_OWNER_NAME = ".agents-shipgate-owner.json"
_REPORTS_OWNER_SCHEMA_VERSION = "shipgate.local_review_reports_owner/v1"


@dataclass(frozen=True)
class LocalReviewPaths:
    workspace: Path
    repository_root: Path
    manifest: Path
    reports: Path
    reports_owner_file: Path
    exclude_file: Path
    binding_file: Path
    worktree_git_dir: Path
    exclude_patterns: tuple[str, str]
    marker_start: str
    marker_end: str


@dataclass(frozen=True)
class LocalReviewProvision:
    binding: LocalReviewBinding
    manifest_status: str
    side_effects: tuple[dict[str, Any], ...]
    cleanup_command: str

    def to_json(self) -> dict[str, Any]:
        cleanup_effects = [
            {
                "kind": effect["kind"],
                "path": effect["path"],
                "removes": effect["recovery"]["removes"],
            }
            for effect in self.side_effects
            if effect["recovery"]["kind"] == "command"
        ]
        return {
            "provenance": self.binding.provenance.model_dump(mode="json"),
            "binding_path": next(
                effect["path"] for effect in self.side_effects if effect["kind"] == "binding"
            ),
            "manifest_path": str(Path(self.binding.workspace) / self.binding.manifest_path),
            "reports_path": str(Path(self.binding.workspace) / self.binding.reports_path),
            "side_effects": list(self.side_effects),
            "cleanup": {
                "command": self.cleanup_command,
                "effects": cleanup_effects,
            },
        }


def local_review_paths(workspace: Path) -> LocalReviewPaths:
    # Runtime-local by design. Importing ``cli.verify.git`` executes the
    # ``cli.verify`` package initializer, which imports the verify command; the
    # command imports this module. Keeping the dependency outside module import
    # time prevents that command/local-review cycle while still sharing Git's
    # hardened path resolvers.
    from agents_shipgate.cli.verify.git import (
        ensure_git_workspace,
        git_path_entry,
        worktree_git_dir,
    )

    root = workspace.resolve()
    repository = ensure_git_workspace(root)
    git_dir = worktree_git_dir(repository)
    try:
        workspace_relative = root.relative_to(repository)
    except ValueError as exc:  # pragma: no cover - ensure_git_workspace establishes this.
        raise ConfigError("Local review workspace must remain inside its Git repository") from exc
    key = hashlib.sha256(str(root).encode("utf-8")).hexdigest()
    manifest = root / LOCAL_REVIEW_MANIFEST_NAME
    reports = root / LOCAL_REVIEW_REPORTS_NAME
    manifest_repo_path = (workspace_relative / manifest.name).as_posix()
    reports_repo_path = (workspace_relative / reports.name).as_posix()
    patterns = tuple(sorted((f"/{manifest_repo_path}", f"/{reports_repo_path}/")))
    marker = f"agents-shipgate:local-review:{key}"
    return LocalReviewPaths(
        workspace=root,
        repository_root=repository,
        manifest=manifest,
        reports=reports,
        reports_owner_file=reports / _REPORTS_OWNER_NAME,
        exclude_file=git_path_entry(repository, "info/exclude"),
        binding_file=git_dir / _BINDING_DIRECTORY / f"{key}.json",
        worktree_git_dir=git_dir,
        exclude_patterns=(patterns[0], patterns[1]),
        marker_start=f"# {marker}:start",
        marker_end=f"# {marker}:end",
    )


def local_review_cleanup_command(workspace: Path) -> str:
    return render_command(
        [
            "init",
            "--workspace",
            str(workspace.resolve()),
            "--local-review",
            "--cleanup",
            "--json",
        ]
    )


def local_review_reports_effect(
    paths: LocalReviewPaths,
    *,
    existed_before: bool,
) -> dict[str, Any]:
    """Describe the reports-directory side effect a local verify owns."""

    cleanup = local_review_cleanup_command(paths.workspace)
    return _effect(
        "reports",
        paths.reports,
        "reused" if existed_before else "created",
        cleanup,
        recovery_removes="the exact bound local-review reports directory and its contents",
        detail={"operation": "directory"},
    )


def provision_local_review(workspace: Path, *, manifest_text: str) -> LocalReviewProvision:
    paths = local_review_paths(workspace)
    _validate_reserved_paths(paths, allow_missing_binding=True)
    status_before = _porcelain(paths.repository_root)
    binding_before = _load_binding_if_present(
        paths,
        require_manifest_hash=False,
        require_reports_owner=True,
    )
    reserved_collisions = [path for path in (paths.manifest, paths.reports) if _lexists(path)]
    if reserved_collisions and binding_before is None:
        raise ConfigError(
            "Refusing unmanaged local-review path collision: "
            f"{', '.join(str(path) for path in reserved_collisions)}. "
            "Move or remove the path before retrying."
        )
    if binding_before is None and _exclude_block_state(paths) != "absent":
        raise _metadata_recovery_error(
            paths,
            "an orphaned, duplicate, or malformed local-review exclude block exists",
        )

    exclude_before = _snapshot(paths.exclude_file)
    manifest_before = _snapshot(paths.manifest)
    binding_bytes_before = _snapshot(paths.binding_file)
    reports_created = False
    cleanup = local_review_cleanup_command(paths.workspace)
    try:
        exclude_status, exclude_separator = _install_exclude_block(
            paths,
            expected_separator=(
                binding_before.exclude_separator if binding_before is not None else None
            ),
        )
        if manifest_before is None:
            _atomic_write(paths.manifest, manifest_text.encode("utf-8"))
            manifest_status = "written"
        else:
            manifest_status = "skipped_existing"
        manifest_bytes = _read_regular(paths.manifest, label="local-review manifest")
        exclude_file_preexisting = (
            binding_before.exclude_file_preexisting
            if binding_before is not None
            else _shared_exclude_file_preexisting(
                paths,
                physical_file_preexisting=exclude_before is not None,
            )
        )
        (
            reports_device,
            reports_inode,
            reports_owner_id,
            reports_status,
            reports_created,
        ) = _provision_reports_owner(paths, binding_before=binding_before)
        binding = _build_binding(
            paths,
            manifest_bytes,
            exclude_file_preexisting=exclude_file_preexisting,
            exclude_separator=exclude_separator,
            reports_directory_device=reports_device,
            reports_directory_inode=reports_inode,
            reports_owner_id=reports_owner_id,
        )
        rendered_binding = (
            json.dumps(binding.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        binding_status = (
            "unchanged"
            if binding_bytes_before == rendered_binding
            else "created"
            if binding_bytes_before is None
            else "updated"
        )
        if binding_status != "unchanged":
            _atomic_write(paths.binding_file, rendered_binding)

        _validate_binding(paths, binding, require_manifest_hash=True)
        status_after = _porcelain(paths.repository_root)
        if status_after != status_before:
            raise ConfigError(
                "Local review changed Git porcelain status; provisioning was rolled back."
            )
    except Exception as exc:
        restoration_errors = _restore_many(
            (
                (paths.binding_file, binding_bytes_before),
                (paths.manifest, manifest_before),
                (paths.exclude_file, exclude_before),
            )
        )
        if reports_created:
            for candidate in (paths.reports_owner_file, paths.reports):
                try:
                    candidate.unlink() if candidate == paths.reports_owner_file else candidate.rmdir()
                except FileNotFoundError:
                    pass
                except OSError as restore_exc:
                    restoration_errors.append(f"{candidate}: {restore_exc}")
        _remove_empty_parent(paths.binding_file.parent, stop=paths.worktree_git_dir)
        if restoration_errors:
            raise ConfigError(
                "Local-review provisioning failed and best-effort rollback could not "
                "restore every side effect. Preserve and inspect these exact paths: "
                + "; ".join(restoration_errors)
            ) from exc
        raise

    effects = (
        _effect(
            "exclude_block",
            paths.exclude_file,
            exclude_status,
            cleanup,
            recovery_removes=(
                "only the managed block delimited by "
                f"{paths.marker_start!r} and {paths.marker_end!r}"
                + (
                    " and the exclude file created for it, but only when that file "
                    "contains nothing else"
                    if not binding.exclude_file_preexisting
                    else "; the pre-existing exclude file itself is preserved"
                )
            ),
            detail={
                "operation": "managed_block",
                "file_operation": exclude_status,
                "exclude_file_preexisting": binding.exclude_file_preexisting,
                "start_marker": paths.marker_start,
                "end_marker": paths.marker_end,
                "patterns": list(paths.exclude_patterns),
            },
        ),
        _effect(
            "manifest",
            paths.manifest,
            manifest_status,
            cleanup,
            recovery_removes="the exact bound local-review manifest file",
            detail={"operation": "file"},
        ),
        _effect(
            "binding",
            paths.binding_file,
            binding_status,
            cleanup,
            recovery_removes="the exact worktree-local binding metadata file",
            detail={"operation": "file"},
        ),
        _effect(
            "reports",
            paths.reports,
            reports_status,
            cleanup,
            recovery_removes=(
                "the exact inode-bound local-review reports directory, its owner "
                "sentinel, and verifier-generated contents"
            ),
            detail={
                "operation": "owned_directory",
                "owner_sentinel": str(paths.reports_owner_file),
                "owner_id": reports_owner_id,
            },
        ),
    )
    return LocalReviewProvision(
        binding=binding,
        manifest_status=manifest_status,
        side_effects=effects,
        cleanup_command=cleanup,
    )


def load_local_review_binding(workspace: Path) -> tuple[LocalReviewPaths, LocalReviewBinding]:
    paths = local_review_paths(workspace)
    _validate_reserved_paths(paths, allow_missing_binding=False)
    binding = _load_binding_if_present(
        paths,
        require_manifest_hash=True,
        require_reports_owner=True,
    )
    if binding is None:
        raise ConfigError(
            "No typed local-review binding exists for this worktree. Run "
            f"`{render_command(['init', '--workspace', str(paths.workspace), '--local-review', '--json'])}`."
        )
    return paths, binding


def cleanup_local_review(workspace: Path) -> tuple[dict[str, Any], ...]:
    paths = local_review_paths(workspace)
    _validate_reserved_paths(paths, allow_missing_binding=False)
    binding = _load_binding_if_present(
        paths,
        require_manifest_hash=True,
        require_reports_owner=False,
    )
    if binding is None:  # pragma: no cover - reserved-path validation routes this first.
        raise ConfigError("No typed local-review binding exists for cleanup")
    status_before = _porcelain(paths.repository_root)
    # Keep the authority boundary retryable until cleanup has proved that the
    # worktree view is unchanged. Reports are generated output and may already
    # be gone after a failed cleanup, but the manifest, binding, and exact
    # exclude block are restored together on every later failure. This avoids
    # the dangerous orphan-block state where neither verify nor cleanup can
    # authenticate the reserved workspace paths.
    exclude_before = _snapshot(paths.exclude_file)
    manifest_before = _snapshot(paths.manifest)
    binding_before = _snapshot(paths.binding_file)
    exclude_mutated = False
    effects: list[dict[str, Any]] = []
    if _lexists(paths.reports):
        _validate_reports_owner(paths, binding)
        _ensure_plain_directory(paths.reports, label="local-review reports")
        try:
            shutil.rmtree(paths.reports)
        except OSError as exc:
            cleanup = local_review_cleanup_command(paths.workspace)
            raise ConfigError(
                "Could not remove the bound local-review reports directory; "
                "manifest, binding, and Git exclude metadata remain intact. "
                f"Resolve the filesystem error and rerun `{cleanup}`: {exc}"
            ) from exc
        effects.append(_effect("reports", paths.reports, "removed", None))

    try:
        # load_local_review_binding proved all three entries are singly linked,
        # byte-bound, and carry the exact managed block. Deleting the two files
        # before replacing the block is safe only because the snapshots above
        # are restored as one transaction on any exception below.
        paths.manifest.unlink()
        effects.append(_effect("manifest", paths.manifest, "removed", None))
        paths.binding_file.unlink()
        effects.append(_effect("binding", paths.binding_file, "removed", None))
        assert exclude_before is not None  # authenticated by load_local_review_binding
        rendered, removed = _remove_exclude_block(
            exclude_before,
            paths,
            separator=binding.exclude_separator,
        )
        if not removed:  # pragma: no cover - the binding validator rejects this first.
            raise ConfigError("Authenticated local-review Git exclude block disappeared")
        if not binding.exclude_file_preexisting and not rendered:
            _unlink_if_unchanged(paths.exclude_file, expected=exclude_before)
            exclude_mutated = True
            effects.append(
                _effect(
                    "exclude_block",
                    paths.exclude_file,
                    "removed_with_created_file",
                    None,
                    detail={"operation": "managed_block_and_created_file"},
                )
            )
        else:
            _atomic_write_if_unchanged(
                paths.exclude_file,
                expected=exclude_before,
                data=rendered,
            )
            exclude_mutated = True
            effects.append(
                _effect(
                    "exclude_block",
                    paths.exclude_file,
                    "removed",
                    None,
                    detail={
                        "operation": "managed_block",
                        "preserved_preexisting_file": binding.exclude_file_preexisting,
                    },
                )
            )
        status_after = _porcelain(paths.repository_root)
        if status_after != status_before:
            raise ConfigError("Local-review cleanup changed Git porcelain status")
    except Exception as exc:
        restoration_errors: list[str] = []
        restore_entries = [
            (paths.binding_file, binding_before),
            (paths.manifest, manifest_before),
        ]
        if exclude_mutated:
            restore_entries.append((paths.exclude_file, exclude_before))
        for path, snapshot in restore_entries:
            try:
                _restore(path, snapshot)
            except Exception as restore_exc:  # pragma: no cover - hard filesystem failure.
                restoration_errors.append(f"{path}: {restore_exc}")
        try:
            if _porcelain(paths.repository_root) != status_before:
                restoration_errors.append("Git porcelain status did not return to its snapshot")
        except Exception as restore_exc:  # pragma: no cover - hard Git failure.
            restoration_errors.append(f"Git status recheck: {restore_exc}")

        cleanup = local_review_cleanup_command(paths.workspace)
        if restoration_errors:
            rebind = render_command(
                [
                    "init",
                    "--workspace",
                    str(paths.workspace),
                    "--local-review",
                    "--json",
                ]
            )
            raise ConfigError(
                "Local-review cleanup failed and could not restore all authenticated "
                "metadata. Repair the listed exact paths, then run "
                f"`{rebind}` to validate/rebind them and `{cleanup}` to retry cleanup. "
                f"Restoration errors: {'; '.join(restoration_errors)}"
            ) from exc
        raise ConfigError(
            "Local-review cleanup stopped safely. The manifest, worktree binding, "
            "and exact Git exclude block were restored; generated reports may already "
            f"have been removed. Rerun `{cleanup}`. Cause: {exc}"
        ) from exc

    _remove_empty_parent(paths.binding_file.parent, stop=paths.worktree_git_dir)
    return tuple(effects)


def is_reserved_local_review_manifest(path: Path) -> bool:
    return path.name == LOCAL_REVIEW_MANIFEST_NAME


def _build_binding(
    paths: LocalReviewPaths,
    manifest_bytes: bytes,
    *,
    exclude_file_preexisting: bool,
    exclude_separator: str,
    reports_directory_device: int,
    reports_directory_inode: int,
    reports_owner_id: str,
) -> LocalReviewBinding:
    payload = {
        "workspace": str(paths.workspace),
        "repository_root": str(paths.repository_root),
        "manifest_path": paths.manifest.relative_to(paths.workspace).as_posix(),
        "reports_path": paths.reports.relative_to(paths.workspace).as_posix(),
        "manifest_sha256": f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}",
        "exclude_file": str(paths.exclude_file),
        "exclude_file_preexisting": exclude_file_preexisting,
        "exclude_separator": exclude_separator,
        "exclude_patterns": list(paths.exclude_patterns),
        "reports_directory_device": reports_directory_device,
        "reports_directory_inode": reports_directory_inode,
        "reports_owner_id": reports_owner_id,
    }
    return LocalReviewBinding(binding_id=content_id(payload), **payload)


def _load_binding_if_present(
    paths: LocalReviewPaths,
    *,
    require_manifest_hash: bool,
    require_reports_owner: bool,
) -> LocalReviewBinding | None:
    if not _lexists(paths.binding_file):
        return None
    data = _read_regular(paths.binding_file, label="local-review binding")
    try:
        binding = LocalReviewBinding.model_validate_json(data)
    except Exception as exc:
        raise _metadata_recovery_error(
            paths,
            f"the binding is invalid at {paths.binding_file} ({exc})",
        ) from exc
    _validate_binding(
        paths,
        binding,
        require_manifest_hash=require_manifest_hash,
        require_reports_owner=require_reports_owner,
    )
    return binding


def _validate_binding(
    paths: LocalReviewPaths,
    binding: LocalReviewBinding,
    *,
    require_manifest_hash: bool,
    require_reports_owner: bool = True,
) -> None:
    expected = {
        "workspace": str(paths.workspace),
        "repository_root": str(paths.repository_root),
        "manifest_path": paths.manifest.relative_to(paths.workspace).as_posix(),
        "reports_path": paths.reports.relative_to(paths.workspace).as_posix(),
        "exclude_file": str(paths.exclude_file),
        "exclude_patterns": list(paths.exclude_patterns),
    }
    for field, value in expected.items():
        if getattr(binding, field) != value:
            raise ConfigError(
                f"Local-review binding does not belong to this worktree: {field} differs."
            )
    if _lexists(paths.reports):
        _validate_reports_owner(paths, binding)
    elif require_reports_owner:
        raise _metadata_recovery_error(paths, "the bound reports directory is missing")
    manifest = _read_regular(paths.manifest, label="local-review manifest")
    if require_manifest_hash:
        observed = f"sha256:{hashlib.sha256(manifest).hexdigest()}"
        if observed != binding.manifest_sha256:
            rebind = render_command(
                [
                    "init",
                    "--workspace",
                    str(paths.workspace),
                    "--local-review",
                    "--json",
                ]
            )
            cleanup = local_review_cleanup_command(paths.workspace)
            raise ConfigError(
                "Local-review manifest changed after its binding was written. Re-run "
                f"`{rebind}` to validate and rebind it, then run `{cleanup}` to remove "
                "the rebound manifest safely."
            )
    exclude = _read_optional_regular(paths.exclude_file, label="Git exclude file")
    owned_block = _owned_exclude_chunk(paths, binding.exclude_separator)
    if exclude is None or exclude.count(owned_block) != 1:
        raise _metadata_recovery_error(
            paths,
            "the bound Git exclude block is missing, duplicated, or changed",
        )


def _reports_owner_payload(
    paths: LocalReviewPaths,
    *,
    device: int,
    inode: int,
) -> dict[str, Any]:
    return {
        "schema_version": _REPORTS_OWNER_SCHEMA_VERSION,
        "workspace": str(paths.workspace),
        "repository_root": str(paths.repository_root),
        "reports_path": paths.reports.relative_to(paths.workspace).as_posix(),
        "device": device,
        "inode": inode,
    }


def _reports_owner_bytes(
    paths: LocalReviewPaths,
    *,
    device: int,
    inode: int,
) -> tuple[str, bytes]:
    payload = _reports_owner_payload(paths, device=device, inode=inode)
    owner_id = content_id(payload)
    document = {**payload, "owner_id": owner_id}
    return owner_id, (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _provision_reports_owner(
    paths: LocalReviewPaths,
    *,
    binding_before: LocalReviewBinding | None,
) -> tuple[int, int, str, str, bool]:
    if binding_before is not None:
        _validate_reports_owner(paths, binding_before)
        return (
            binding_before.reports_directory_device,
            binding_before.reports_directory_inode,
            binding_before.reports_owner_id,
            "unchanged",
            False,
        )
    try:
        paths.reports.mkdir()
    except OSError as exc:
        raise ConfigError(
            f"Could not create the reserved local-review reports directory {paths.reports}: {exc}"
        ) from exc
    metadata = paths.reports.lstat()
    owner_id, owner_bytes = _reports_owner_bytes(
        paths,
        device=metadata.st_dev,
        inode=metadata.st_ino,
    )
    try:
        _atomic_write(paths.reports_owner_file, owner_bytes)
    except Exception as exc:
        rollback_errors: list[str] = []
        for candidate, operation in (
            (paths.reports_owner_file, "unlink"),
            (paths.reports, "rmdir"),
        ):
            try:
                if operation == "unlink":
                    candidate.unlink(missing_ok=True)
                else:
                    candidate.rmdir()
            except OSError as rollback_exc:
                rollback_errors.append(f"{candidate}: {rollback_exc}")
        if rollback_errors:
            raise ConfigError(
                "Could not write the local-review reports owner sentinel and "
                "best-effort rollback left effects at: " + "; ".join(rollback_errors)
            ) from exc
        raise ConfigError(
            "Could not write the local-review reports owner sentinel; all "
            f"provisioning effects were rolled back: {exc}"
        ) from exc
    return metadata.st_dev, metadata.st_ino, owner_id, "created", True


def _validate_reports_owner(
    paths: LocalReviewPaths,
    binding: LocalReviewBinding,
) -> None:
    try:
        metadata = paths.reports.lstat()
    except OSError as exc:
        raise _metadata_recovery_error(
            paths,
            f"the bound reports directory cannot be inspected ({exc})",
        ) from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise _metadata_recovery_error(
            paths,
            "the bound reports path was replaced by a link or non-directory",
        )
    observed_identity = (metadata.st_dev, metadata.st_ino)
    expected_identity = (
        binding.reports_directory_device,
        binding.reports_directory_inode,
    )
    if observed_identity != expected_identity:
        raise _metadata_recovery_error(
            paths,
            "the bound reports directory was removed or replaced",
        )
    owner_id, owner_bytes = _reports_owner_bytes(
        paths,
        device=metadata.st_dev,
        inode=metadata.st_ino,
    )
    if owner_id != binding.reports_owner_id:
        raise _metadata_recovery_error(paths, "the reports owner identity is inconsistent")
    observed = _read_optional_regular(
        paths.reports_owner_file,
        label="local-review reports owner sentinel",
    )
    if observed != owner_bytes:
        raise _metadata_recovery_error(
            paths,
            "the bound reports owner sentinel is missing or changed",
        )


def _shared_exclude_file_preexisting(
    paths: LocalReviewPaths,
    *,
    physical_file_preexisting: bool,
) -> bool:
    """Carry the original shared-exclude existence fact across workspace bindings."""

    inherited_absence = False
    for directory in _peer_binding_directories(paths):
        _validate_parent_components(directory / "placeholder", label="binding directory")
        try:
            entries = list(directory.iterdir())
        except OSError as exc:
            raise ConfigError(
                f"Could not inspect local-review binding directory {directory}: {exc}"
            ) from exc
        for entry in entries:
            if entry == paths.binding_file:
                continue
            data = _read_regular(entry, label="peer local-review binding")
            try:
                peer = LocalReviewBinding.model_validate_json(data)
            except Exception as exc:
                raise _metadata_recovery_error(
                    paths,
                    f"peer binding metadata is invalid at {entry} ({exc})",
                ) from exc
            if peer.exclude_file == str(paths.exclude_file) and not peer.exclude_file_preexisting:
                inherited_absence = True
    return physical_file_preexisting and not inherited_absence


def _peer_binding_directories(paths: LocalReviewPaths) -> tuple[Path, ...]:
    """Every per-worktree binding directory sharing this common exclude file."""

    common_git_dir = paths.exclude_file.parent.parent
    candidates: set[Path] = {common_git_dir / _BINDING_DIRECTORY}
    worktrees = common_git_dir / "worktrees"
    if _lexists(worktrees):
        _validate_parent_components(worktrees / "placeholder", label="Git worktrees directory")
        metadata = worktrees.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise ConfigError(f"Git worktrees metadata must be a real directory: {worktrees}")
        try:
            worktree_entries = list(worktrees.iterdir())
        except OSError as exc:
            raise ConfigError(
                f"Could not inspect Git worktrees metadata {worktrees}: {exc}"
            ) from exc
        for entry in worktree_entries:
            metadata = entry.lstat()
            if not stat.S_ISDIR(metadata.st_mode):
                raise ConfigError(f"Git worktree metadata must be a real directory: {entry}")
            candidates.add(entry / _BINDING_DIRECTORY)
    return tuple(sorted(directory for directory in candidates if _lexists(directory)))


def _metadata_recovery_error(paths: LocalReviewPaths, cause: str) -> ConfigError:
    rebind = render_command(
        ["init", "--workspace", str(paths.workspace), "--local-review", "--json"]
    )
    return ConfigError(
        "Local-review metadata recovery required: "
        f"{cause}. Preserve user data: move, do not overwrite, the exact reserved "
        f"paths {paths.manifest}, {paths.reports}, and {paths.binding_file} to a safe "
        f"location as needed. In {paths.exclude_file}, remove only the managed bytes "
        f"delimited by {paths.marker_start!r} and {paths.marker_end!r}; preserve every "
        f"other byte. Then run `{rebind}` to create and authenticate a fresh binding."
    )


def _validate_reserved_paths(paths: LocalReviewPaths, *, allow_missing_binding: bool) -> None:
    for path, label in (
        (paths.exclude_file, "Git exclude file"),
        (paths.binding_file, "local-review binding"),
        (paths.reports_owner_file, "local-review reports owner sentinel"),
    ):
        _validate_parent_components(path, label=label)
    if _lexists(paths.manifest):
        _read_regular(paths.manifest, label="local-review manifest")
    if _lexists(paths.reports):
        _ensure_plain_directory(paths.reports, label="local-review reports")
    if _lexists(paths.binding_file):
        _read_regular(paths.binding_file, label="local-review binding")
    elif not allow_missing_binding and _lexists(paths.manifest):
        raise _metadata_recovery_error(
            paths,
            "the reserved manifest has no worktree binding metadata",
        )
    if _lexists(paths.exclude_file):
        _read_regular(paths.exclude_file, label="Git exclude file")
    for candidate in (paths.manifest, paths.reports):
        relative = candidate.relative_to(paths.repository_root).as_posix()
        result = _run_read_only_git(
            paths.repository_root,
            "ls-files",
            "--error-unmatch",
            "--",
            relative,
        )
        if result.returncode == 0:
            raise ConfigError(f"Refusing tracked local-review path collision: {candidate}")


def _install_exclude_block(
    paths: LocalReviewPaths,
    *,
    expected_separator: str | None = None,
) -> tuple[str, str]:
    before = _read_optional_regular(paths.exclude_file, label="Git exclude file")
    data = before or b""
    block = _managed_block(paths).encode("utf-8")
    if data.count(block) == 1:
        if expected_separator is None:
            raise _metadata_recovery_error(
                paths,
                "an unmanaged local-review exclude block already exists",
            )
        owned = _owned_exclude_chunk(paths, expected_separator)
        if data.count(owned) != 1:
            raise _metadata_recovery_error(
                paths,
                "the managed exclude block no longer has its bound byte framing",
            )
        return "unchanged", expected_separator
    if data.count(block) > 1:
        raise _metadata_recovery_error(
            paths,
            "duplicate local-review blocks exist in the Git exclude file",
        )
    marker_start = paths.marker_start.encode("utf-8")
    marker_end = paths.marker_end.encode("utf-8")
    if marker_start in data or marker_end in data:
        raise _metadata_recovery_error(
            paths,
            "the local-review exclude block is malformed",
        )
    separator = (
        "" if not data or data.endswith(b"\n\n") else "\n" if data.endswith(b"\n") else "\n\n"
    )
    _atomic_write_if_unchanged(
        paths.exclude_file,
        expected=before,
        data=data + _owned_exclude_chunk(paths, separator),
    )
    return ("created" if before is None else "updated"), separator


def _managed_block(paths: LocalReviewPaths) -> str:
    return "\n".join([paths.marker_start, *paths.exclude_patterns, paths.marker_end, ""])


def _owned_exclude_chunk(paths: LocalReviewPaths, separator: str) -> bytes:
    return (separator + _managed_block(paths)).encode("utf-8")


def _remove_exclude_block(
    data: bytes,
    paths: LocalReviewPaths,
    *,
    separator: str,
) -> tuple[bytes, bool]:
    block = _managed_block(paths).encode("utf-8")
    owned = _owned_exclude_chunk(paths, separator)
    if data.count(block) > 1:
        raise _metadata_recovery_error(
            paths,
            "duplicate local-review blocks exist in the Git exclude file",
        )
    if owned not in data:
        marker_start = paths.marker_start.encode("utf-8")
        marker_end = paths.marker_end.encode("utf-8")
        if marker_start in data or marker_end in data:
            raise _metadata_recovery_error(
                paths,
                "the local-review exclude block is malformed or its byte framing changed",
            )
        return data, False
    return data.replace(owned, b"", 1), True


def _effect(
    kind: str,
    path: Path,
    status: str,
    cleanup_command: str | None,
    *,
    recovery_removes: str | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recovery: dict[str, Any] = {"kind": "none"}
    if cleanup_command is not None:
        recovery = {
            "kind": "command",
            "command": cleanup_command,
            "path": str(path),
            "removes": recovery_removes or f"the side effect at {path}",
        }
    return {
        "kind": kind,
        "path": str(path),
        "status": status,
        "recovery": recovery,
        **(detail or {}),
    }


def _porcelain(repository: Path) -> bytes:
    result = _run_read_only_git(
        repository,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignored=no",
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ConfigError(f"Could not prove local-review Git status invariance: {detail}")
    return result.stdout


def _run_read_only_git(
    repository: Path,
    *args: str,
) -> subprocess.CompletedProcess[bytes]:
    """Run one fixed, read-only Git query without repository configuration."""

    environment = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PROTOCOL_FROM_USER": "0",
    }
    try:
        return subprocess.run(
            ["git", "--no-replace-objects", "-C", str(repository), *args],
            capture_output=True,
            check=False,
            env=environment,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConfigError(f"Could not run local-review Git query: {exc}") from exc


def _read_regular(path: Path, *, label: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ConfigError(f"Could not inspect {label} {path}: {exc}") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ConfigError(f"{label} must be one singly-linked regular file: {path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"Could not read {label} {path}: {exc}") from exc


def _read_optional_regular(path: Path, *, label: str) -> bytes | None:
    if not _lexists(path):
        return None
    return _read_regular(path, label=label)


def _ensure_plain_directory(path: Path, *, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ConfigError(f"Could not inspect {label} {path}: {exc}") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise ConfigError(f"{label} must be a real directory, not a link or file: {path}")


def _snapshot(path: Path) -> bytes | None:
    if not _lexists(path):
        return None
    return _read_regular(path, label="local-review side effect")


def _atomic_write(path: Path, data: bytes) -> None:
    _validate_parent_components(path, label="local-review write target")
    previous_mode: int | None = None
    if _lexists(path):
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ConfigError(f"Refusing to overwrite non-regular or hard-linked path: {path}")
        previous_mode = stat.S_IMODE(metadata.st_mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    _validate_parent_components(path, label="local-review write target")
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if previous_mode is not None:
            temporary.chmod(previous_mode)
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink()


def _atomic_write_if_unchanged(
    path: Path,
    *,
    expected: bytes | None,
    data: bytes,
) -> None:
    observed = _read_optional_regular(path, label="Git exclude file")
    if observed != expected:
        raise ConfigError(
            f"Refusing to replace {path} because it changed concurrently; retry so "
            "the new bytes are preserved."
        )
    _atomic_write(path, data)


def _unlink_if_unchanged(path: Path, *, expected: bytes) -> None:
    observed = _read_optional_regular(path, label="Git exclude file")
    if observed != expected:
        raise ConfigError(
            f"Refusing to remove {path} because it changed concurrently; retry so "
            "the new bytes are preserved."
        )
    path.unlink()


def _restore(path: Path, before: bytes | None) -> None:
    if before is None:
        if _lexists(path):
            path.unlink()
        return
    _atomic_write(path, before)


def _restore_many(entries: tuple[tuple[Path, bytes | None], ...]) -> list[str]:
    errors: list[str] = []
    for path, before in entries:
        try:
            _restore(path, before)
        except Exception as exc:  # noqa: BLE001 - rollback must attempt every entry.
            errors.append(f"{path}: {exc}")
    return errors


def _validate_parent_components(path: Path, *, label: str) -> None:
    """Reject symlink/non-directory ancestors before touching private metadata."""

    current = path.parent
    chain: list[Path] = []
    while current != current.parent:
        chain.append(current)
        current = current.parent
    for parent in reversed(chain):
        if not _lexists(parent):
            continue
        try:
            metadata = parent.lstat()
        except OSError as exc:
            raise ConfigError(f"Could not inspect parent of {label} {parent}: {exc}") from exc
        if not stat.S_ISDIR(metadata.st_mode):
            raise ConfigError(
                f"Refusing {label}: every parent must be a real directory, not a link: {parent}"
            )


def _lexists(path: Path) -> bool:
    """Return true for every directory entry, including a broken symlink."""

    return os.path.lexists(path)


def _exclude_block_state(paths: LocalReviewPaths) -> str:
    data = _read_optional_regular(paths.exclude_file, label="Git exclude file")
    if data is None:
        return "absent"
    block = _managed_block(paths).encode("utf-8")
    if data.count(block) == 1:
        return "present"
    if (
        block in data
        or paths.marker_start.encode("utf-8") in data
        or paths.marker_end.encode("utf-8") in data
    ):
        return "malformed"
    return "absent"


def _remove_empty_parent(path: Path, *, stop: Path) -> None:
    current = path
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


__all__ = [
    "LOCAL_REVIEW_MANIFEST_NAME",
    "LOCAL_REVIEW_REPORTS_NAME",
    "LocalReviewPaths",
    "LocalReviewProvision",
    "cleanup_local_review",
    "is_reserved_local_review_manifest",
    "load_local_review_binding",
    "local_review_cleanup_command",
    "local_review_paths",
    "local_review_reports_effect",
    "provision_local_review",
]
