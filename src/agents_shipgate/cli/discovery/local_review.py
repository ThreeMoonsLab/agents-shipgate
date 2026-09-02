"""Private Git exclusion lifecycle for ``init --local-review``."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from agents_shipgate.cli.discovery.gitignore_block import REPORTS_DIR_NAME
from agents_shipgate.cli.verify.git import git_directories, git_path
from agents_shipgate.core.agent_controls import git_root_for
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.manifest_provenance import LOCAL_REVIEW_MANIFEST_NAME
from agents_shipgate.invocation import render_command

LOCAL_REVIEW_EXCLUDE_VERSION = 1


class LocalReviewExcludeStatus(StrEnum):
    CREATED = "created"
    APPENDED = "appended"
    UNCHANGED = "unchanged"
    UPDATED = "updated"
    MIGRATED = "migrated"


@dataclass(frozen=True)
class LocalReviewExcludeOutcome:
    status: LocalReviewExcludeStatus
    path: str
    entries: tuple[str, str]
    block_id: str
    start_marker: str
    end_marker: str
    changed: bool
    message: str
    cleanup_command: str
    previous_bytes: bytes | None = field(default=None, repr=False)
    previous_existed: bool = field(default=False, repr=False)
    written_bytes: bytes | None = field(default=None, repr=False)

    def to_json(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "path": self.path,
            "entries": list(self.entries),
            "block_id": self.block_id,
            "changed": self.changed,
            "message": self.message,
            "scope": "checkout",
            "recovery": {
                "action": "run_command",
                "command": self.cleanup_command,
                "path": self.path,
                "start_marker": self.start_marker,
                "end_marker": self.end_marker,
            },
        }


@dataclass(frozen=True)
class LocalReviewCleanupOutcome:
    manifest_path: str
    manifest_removed: bool
    exclude_path: str
    exclude_block_removed: bool
    block_id: str
    reports_path: str

    def to_json(self) -> dict[str, object]:
        return {
            "manifest": {"path": self.manifest_path, "removed": self.manifest_removed},
            "git_private_exclude": {
                "path": self.exclude_path,
                "block_id": self.block_id,
                "removed": self.exclude_block_removed,
            },
            "reports": {
                "path": self.reports_path,
                "removed": False,
                "managed_exclusion_removed": self.exclude_block_removed,
            },
        }


def ensure_local_review_excludes(workspace: Path) -> LocalReviewExcludeOutcome:
    """Exclude the ephemeral manifest and reports without tracked changes."""

    workspace, git_root, exclude_path, block_id = _local_review_paths(workspace)
    workspace_relative = workspace.relative_to(git_root)
    prefix = "" if workspace_relative == Path(".") else workspace_relative.as_posix() + "/"
    entries = (
        _literal_gitignore_path(f"/{prefix}{LOCAL_REVIEW_MANIFEST_NAME}"),
        _literal_gitignore_path(f"/{prefix}{REPORTS_DIR_NAME}/"),
    )
    start_marker = _start_marker(block_id, LOCAL_REVIEW_EXCLUDE_VERSION)
    end_marker = _end_marker(block_id)
    cleanup_command = render_command(
        ["init", "--workspace", str(workspace), "--local-review", "--undo", "--json"]
    )

    previous_existed, previous, previous_identity, previous_mode = _read_regular_file(
        exclude_path
    )
    rendered = _render_block(
        entries=entries,
        start_marker=start_marker,
        end_marker=end_marker,
        newline=b"\r\n" if b"\r\n" in previous else b"\n",
    )
    updated, found, prior_version = _upsert_block(
        previous,
        rendered=rendered,
        block_id=block_id,
    )
    if prior_version is not None and prior_version > LOCAL_REVIEW_EXCLUDE_VERSION:
        raise ConfigError(
            "The local-review block in the Git private exclude file was written "
            f"by a newer CLI (v{prior_version}); upgrade Agents Shipgate before changing it."
        )
    if found and updated == previous:
        return LocalReviewExcludeOutcome(
            status=LocalReviewExcludeStatus.UNCHANGED,
            path=str(exclude_path),
            entries=entries,
            block_id=block_id,
            start_marker=start_marker,
            end_marker=end_marker,
            changed=False,
            message=f"Local-review exclusions are already present in {exclude_path}.",
            cleanup_command=cleanup_command,
        )

    _atomic_replace(
        exclude_path,
        updated,
        expected_existed=previous_existed,
        expected_identity=previous_identity,
        mode=previous_mode,
    )
    if not previous_existed:
        status = LocalReviewExcludeStatus.CREATED
    elif not found:
        status = LocalReviewExcludeStatus.APPENDED
    elif prior_version is not None and prior_version < LOCAL_REVIEW_EXCLUDE_VERSION:
        status = LocalReviewExcludeStatus.MIGRATED
    else:
        status = LocalReviewExcludeStatus.UPDATED
    return LocalReviewExcludeOutcome(
        status=status,
        path=str(exclude_path),
        entries=entries,
        block_id=block_id,
        start_marker=start_marker,
        end_marker=end_marker,
        changed=True,
        message=(
            f"Added checkout-local review exclusions to {exclude_path}; "
            "tracked .gitignore was not modified."
        ),
        cleanup_command=cleanup_command,
        previous_bytes=previous,
        previous_existed=previous_existed,
        written_bytes=updated,
    )


def rollback_local_review_excludes(outcome: LocalReviewExcludeOutcome) -> None:
    """Restore exact prior bytes when the manifest write fails immediately."""

    if not outcome.changed:
        return
    path = Path(outcome.path)
    existed, current, identity, mode = _read_regular_file(path)
    if not existed or current != outcome.written_bytes:
        raise ConfigError(
            "The Git private exclude file changed after Shipgate wrote it; "
            f"refusing to overwrite concurrent edits in {path}."
        )
    if outcome.previous_existed:
        _atomic_replace(
            path,
            outcome.previous_bytes or b"",
            expected_existed=True,
            expected_identity=identity,
            mode=mode,
        )
    else:
        _unlink_if_identity(path, expected_identity=identity)


def cleanup_local_review(workspace: Path) -> LocalReviewCleanupOutcome:
    """Remove only the reserved manifest and this checkout's managed block."""

    workspace, _git_root, exclude_path, block_id = _local_review_paths(workspace)
    manifest = workspace / LOCAL_REVIEW_MANIFEST_NAME
    manifest_exists, _manifest_bytes, manifest_identity, _manifest_mode = _read_regular_file(
        manifest
    )
    exclude_exists, host, exclude_identity, exclude_mode = _read_regular_file(exclude_path)
    updated, removed = _remove_block(host, block_id=block_id)
    if removed:
        if updated:
            _atomic_replace(
                exclude_path,
                updated,
                expected_existed=exclude_exists,
                expected_identity=exclude_identity,
                mode=exclude_mode,
            )
        else:
            _unlink_if_identity(exclude_path, expected_identity=exclude_identity)
    if manifest_exists:
        _unlink_if_identity(manifest, expected_identity=manifest_identity)
    return LocalReviewCleanupOutcome(
        manifest_path=str(manifest.resolve()),
        manifest_removed=manifest_exists,
        exclude_path=str(exclude_path),
        exclude_block_removed=removed,
        block_id=block_id,
        reports_path=str((workspace / REPORTS_DIR_NAME).resolve()),
    )


def local_review_side_effects(
    *,
    workspace: Path,
    manifest_status: str,
    exclude: LocalReviewExcludeOutcome | None,
) -> dict[str, object]:
    """Inventory effects without claiming an exclusion that was not written."""

    manifest = (workspace / LOCAL_REVIEW_MANIFEST_NAME).resolve()
    reports = (workspace / REPORTS_DIR_NAME).resolve()
    cleanup_command = render_command(
        [
            "init",
            "--workspace",
            str(workspace.resolve()),
            "--local-review",
            "--undo",
            "--json",
        ]
    )
    side_effects: list[dict[str, object]] = []
    if exclude is not None:
        side_effects.append({"kind": "git_private_exclude", **exclude.to_json()})
    side_effects.append(
        {
            "kind": "manifest",
            "status": manifest_status,
            "path": str(manifest),
            "changed": manifest_status == "written",
            "recovery": {"action": "run_command", "command": cleanup_command},
        }
    )
    return {
        "ephemeral": True,
        "release_authoritative": False,
        "cleanup_command": cleanup_command,
        "side_effects": side_effects,
        "reports": {
            "path": str(reports),
            "git_visibility": "excluded" if exclude is not None else "not_excluded",
            "created_by_init": False,
            "recovery": {"action": "run_command", "command": cleanup_command},
        },
        "durable_adoption": {
            "command": render_command(
                ["init", "--workspace", str(workspace.resolve()), "--write", "--json"]
            ),
            "manifest": str((workspace / "shipgate.yaml").resolve()),
        },
    }


def _local_review_paths(workspace: Path) -> tuple[Path, Path, Path, str]:
    workspace = workspace.resolve()
    git_root = git_root_for(workspace)
    if git_root is None:
        raise ConfigError("--local-review requires a Git checkout")
    git_root = git_root.resolve()
    try:
        workspace_relative = workspace.relative_to(git_root)
    except ValueError as exc:
        raise ConfigError("--local-review workspace is outside its Git checkout") from exc
    git_dir, common_dir = git_directories(workspace)
    if git_dir != common_dir:
        raise ConfigError(
            "--local-review cannot safely use a linked Git worktree because "
            ".git/info/exclude is shared by every worktree. Use a standalone "
            "clone for the external review."
        )
    exclude_path = git_path(workspace, "info/exclude")
    identity = f"{git_dir}\0{workspace_relative.as_posix()}"
    block_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return workspace, git_root, exclude_path, block_id


def _read_regular_file(
    path: Path,
) -> tuple[bool, bytes, tuple[int, int] | None, int | None]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return False, b"", None, None
    except OSError as exc:
        raise ConfigError(f"Could not open {path} without following links: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ConfigError(f"Refusing to use a non-regular file: {path}")
        if metadata.st_nlink != 1:
            raise ConfigError(f"Refusing to use a multiply-linked file: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read()
        return True, payload, (metadata.st_dev, metadata.st_ino), stat.S_IMODE(metadata.st_mode)
    finally:
        os.close(descriptor)


def _current_identity(path: Path) -> tuple[int, int] | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ConfigError(f"Refusing to replace a non-regular or multiply-linked file: {path}")
    return metadata.st_dev, metadata.st_ino


def _atomic_replace(
    path: Path,
    payload: bytes,
    *,
    expected_existed: bool,
    expected_identity: tuple[int, int] | None,
    mode: int | None,
) -> None:
    if not path.parent.is_dir():
        raise ConfigError(f"Git private exclude directory does not exist: {path.parent}")
    descriptor, temporary = tempfile.mkstemp(prefix=".agents-shipgate-", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, mode if mode is not None else 0o600)
        current = _current_identity(path)
        if (current is not None) != expected_existed or current != expected_identity:
            raise ConfigError(
                f"{path} changed while Shipgate was preparing its update; "
                "refusing to overwrite concurrent edits."
            )
        os.replace(temporary_path, path)
    except OSError as exc:
        raise ConfigError(f"Could not atomically update {path}: {exc}") from exc
    finally:
        temporary_path.unlink(missing_ok=True)


def _unlink_if_identity(path: Path, *, expected_identity: tuple[int, int] | None) -> None:
    if _current_identity(path) != expected_identity:
        raise ConfigError(f"{path} changed before cleanup; refusing to remove it.")
    try:
        path.unlink()
    except OSError as exc:
        raise ConfigError(f"Could not remove {path}: {exc}") from exc


def _literal_gitignore_path(value: str) -> str:
    if any(char in value for char in "\x00\r\n"):
        raise ConfigError("Local-review workspace path cannot contain control characters")
    return re.sub(r"([\\*?\[\]])", r"\\\1", value)


def _start_marker(block_id: str, version: int) -> str:
    return f"# agents-shipgate:local-review:start v={version} id={block_id}"


def _end_marker(block_id: str) -> str:
    return f"# agents-shipgate:local-review:end id={block_id}"


def _block_matches(
    host: bytes, *, block_id: str
) -> tuple[list[re.Match[bytes]], list[re.Match[bytes]]]:
    encoded_id = re.escape(block_id.encode("ascii"))
    starts = list(
        re.finditer(
            rb"(?m)^# agents-shipgate:local-review:start v=(\d+) id="
            + encoded_id
            + rb"\r?$",
            host,
        )
    )
    ends = list(
        re.finditer(
            rb"(?m)^# agents-shipgate:local-review:end id=" + encoded_id + rb"\r?$",
            host,
        )
    )
    return starts, ends


def _block_bounds(host: bytes, *, block_id: str) -> tuple[int, int, int] | None:
    starts, ends = _block_matches(host, block_id=block_id)
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or starts[0].start() >= ends[0].start():
        raise ConfigError("Local-review block in Git private exclude file is ambiguous")
    line_start = host.rfind(b"\n", 0, starts[0].start()) + 1
    newline_after_end = host.find(b"\n", ends[0].end())
    line_end = len(host) if newline_after_end < 0 else newline_after_end + 1
    return line_start, line_end, int(starts[0].group(1))


def _render_block(
    *, entries: tuple[str, str], start_marker: str, end_marker: str, newline: bytes
) -> bytes:
    nl = newline.decode("ascii")
    return (
        f"{start_marker}{nl}"
        "# Ephemeral manifest and reports from `agents-shipgate init --local-review`."
        f"{nl}{entries[0]}{nl}{entries[1]}{nl}{end_marker}{nl}"
    ).encode()


def _upsert_block(
    host: bytes,
    *,
    rendered: bytes,
    block_id: str,
) -> tuple[bytes, bool, int | None]:
    bounds = _block_bounds(host, block_id=block_id)
    if bounds is None:
        newline = b"\r\n" if b"\r\n" in host else b"\n"
        prefix = host
        if prefix and not prefix.endswith(newline):
            prefix += newline
        if prefix and not prefix.endswith(newline + newline):
            prefix += newline
        return prefix + rendered, False, None
    line_start, line_end, prior_version = bounds
    return host[:line_start] + rendered + host[line_end:], True, prior_version


def _remove_block(host: bytes, *, block_id: str) -> tuple[bytes, bool]:
    bounds = _block_bounds(host, block_id=block_id)
    if bounds is None:
        return host, False
    line_start, line_end, _version = bounds
    updated = host[:line_start] + host[line_end:]
    if line_start and updated[:line_start].endswith(b"\n\n"):
        updated = updated[: line_start - 1] + updated[line_start:]
    return updated, True


__all__ = [
    "LOCAL_REVIEW_EXCLUDE_VERSION",
    "LocalReviewCleanupOutcome",
    "LocalReviewExcludeOutcome",
    "LocalReviewExcludeStatus",
    "cleanup_local_review",
    "ensure_local_review_excludes",
    "local_review_side_effects",
    "rollback_local_review_excludes",
]
