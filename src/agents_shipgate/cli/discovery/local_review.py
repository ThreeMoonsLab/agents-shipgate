"""Private Git exclusion for ``init --local-review``.

The mode writes its manifest into the evaluated workspace so existing
manifest-relative tool-source paths keep their meaning.  It keeps that file
and the report directory out of the checkout's visible change set through
``.git/info/exclude`` rather than editing the repository's tracked
``.gitignore``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from agents_shipgate.cli.discovery.gitignore_block import REPORTS_DIR_NAME
from agents_shipgate.cli.verify.git import git_path
from agents_shipgate.core.agent_controls import git_root_for
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.manifest_provenance import LOCAL_REVIEW_MANIFEST_NAME
from agents_shipgate.invocation import render_command

LOCAL_REVIEW_EXCLUDE_VERSION = 1


class LocalReviewExcludeStatus(StrEnum):
    CREATED = "created"
    APPENDED = "appended"
    UNCHANGED = "unchanged"


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
    _previous_bytes: bytes | None = field(default=None, repr=False)
    _previous_existed: bool = field(default=False, repr=False)

    def to_json(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "path": self.path,
            "entries": list(self.entries),
            "block_id": self.block_id,
            "changed": self.changed,
            "message": self.message,
            "recovery": {
                "action": "remove_managed_block",
                "path": self.path,
                "start_marker": self.start_marker,
                "end_marker": self.end_marker,
            },
        }


def ensure_local_review_excludes(workspace: Path) -> LocalReviewExcludeOutcome:
    """Exclude the local manifest and reports without touching tracked files.

    Failure is fatal and happens before the manifest is written.  A local
    review that cannot prove its generated files are private must not create
    them and hope the caller notices ``git status`` later.
    """

    workspace = workspace.resolve()
    git_root = git_root_for(workspace)
    if git_root is None:
        raise ConfigError("--local-review requires a Git checkout")
    try:
        workspace_relative = workspace.relative_to(git_root.resolve())
    except ValueError as exc:
        raise ConfigError("--local-review workspace is outside its Git checkout") from exc

    exclude_path = _git_exclude_path(workspace)
    _validate_exclude_target(exclude_path)

    prefix = "" if workspace_relative == Path(".") else workspace_relative.as_posix() + "/"
    manifest_entry = _literal_gitignore_path(f"/{prefix}{LOCAL_REVIEW_MANIFEST_NAME}")
    reports_entry = _literal_gitignore_path(f"/{prefix}{REPORTS_DIR_NAME}/")
    entries = (manifest_entry, reports_entry)
    block_id = hashlib.sha256(workspace_relative.as_posix().encode("utf-8")).hexdigest()[:16]
    start_marker = (
        f"# agents-shipgate:local-review:start v={LOCAL_REVIEW_EXCLUDE_VERSION} id={block_id}"
    )
    end_marker = f"# agents-shipgate:local-review:end id={block_id}"

    previous_existed = exclude_path.exists()
    previous = exclude_path.read_bytes() if previous_existed else b""
    rendered = _render_block(
        entries=entries,
        start_marker=start_marker,
        end_marker=end_marker,
        newline=b"\r\n" if b"\r\n" in previous else b"\n",
    )
    updated, present = _upsert_block(
        previous,
        rendered=rendered,
        start_marker=start_marker.encode("utf-8"),
        end_marker=end_marker.encode("utf-8"),
    )
    if present and updated == previous:
        return LocalReviewExcludeOutcome(
            status=LocalReviewExcludeStatus.UNCHANGED,
            path=str(exclude_path),
            entries=entries,
            block_id=block_id,
            start_marker=start_marker,
            end_marker=end_marker,
            changed=False,
            message=f"Local-review exclusions are already present in {exclude_path}.",
        )

    try:
        exclude_path.write_bytes(updated)
    except OSError as exc:
        raise ConfigError(
            f"Could not update private Git exclude file {exclude_path}: {exc}"
        ) from exc
    return LocalReviewExcludeOutcome(
        status=(
            LocalReviewExcludeStatus.APPENDED
            if previous_existed
            else LocalReviewExcludeStatus.CREATED
        ),
        path=str(exclude_path),
        entries=entries,
        block_id=block_id,
        start_marker=start_marker,
        end_marker=end_marker,
        changed=True,
        message=(
            f"Added private local-review exclusions to {exclude_path}; "
            "tracked .gitignore was not modified."
        ),
        _previous_bytes=previous,
        _previous_existed=previous_existed,
    )


def rollback_local_review_excludes(outcome: LocalReviewExcludeOutcome) -> None:
    """Restore the exact exclude bytes after a later setup write fails."""

    if not outcome.changed:
        return
    path = Path(outcome.path)
    if outcome._previous_existed:
        path.write_bytes(outcome._previous_bytes or b"")
    else:
        path.unlink(missing_ok=True)


def local_review_side_effects(
    *,
    workspace: Path,
    manifest_status: str,
    exclude: LocalReviewExcludeOutcome | None,
) -> dict[str, object]:
    """One JSON inventory of current effects and their exact cleanup paths."""

    manifest = (workspace / LOCAL_REVIEW_MANIFEST_NAME).resolve()
    reports = (workspace / REPORTS_DIR_NAME).resolve()
    side_effects: list[dict[str, object]] = []
    if exclude is not None:
        side_effects.append({"kind": "git_private_exclude", **exclude.to_json()})
    side_effects.append(
        {
            "kind": "manifest",
            "status": manifest_status,
            "path": str(manifest),
            "changed": manifest_status == "written",
            "recovery": {"action": "remove_file", "path": str(manifest)},
        }
    )
    return {
        "ephemeral": True,
        "release_authoritative": False,
        "side_effects": side_effects,
        "reports": {
            "path": str(reports),
            "git_visibility": "excluded",
            "created_by_init": False,
            "recovery": {"action": "remove_directory", "path": str(reports)},
        },
        "durable_adoption": {
            "command": render_command(["init", "--workspace", str(workspace), "--write", "--json"]),
            "manifest": str((workspace / "shipgate.yaml").resolve()),
        },
    }


def _git_exclude_path(workspace: Path) -> Path:
    # Reuse verify's bounded, no-shell Git collector. Keeping Git execution in
    # that audited module avoids introducing another meta-CLI subprocess
    # surface into the static scanner package.
    return git_path(workspace, "info/exclude")


def _validate_exclude_target(path: Path) -> None:
    if not path.parent.is_dir():
        raise ConfigError(f"Git private exclude directory does not exist: {path.parent}")
    if path.is_symlink():
        raise ConfigError(f"Git private exclude file is a symlink; refusing to write: {path}")
    if path.exists():
        if not path.is_file():
            raise ConfigError(f"Git private exclude path is not a regular file: {path}")
        try:
            if path.stat().st_nlink != 1:
                raise ConfigError(
                    f"Git private exclude file has multiple hard links; refusing to write: {path}"
                )
        except OSError as exc:
            raise ConfigError(f"Could not inspect Git private exclude file {path}: {exc}") from exc


def _literal_gitignore_path(value: str) -> str:
    if any(char in value for char in "\x00\r\n"):
        raise ConfigError("Local-review workspace path cannot contain control characters")
    escaped = re.sub(r"([\\*?\[\]])", r"\\\1", value)
    if escaped.startswith(("#", "!")):
        escaped = "\\" + escaped
    if escaped.endswith(" "):
        escaped = escaped[:-1] + "\\ "
    return escaped


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
    host: bytes, *, rendered: bytes, start_marker: bytes, end_marker: bytes
) -> tuple[bytes, bool]:
    starts = list(re.finditer(rb"(?m)^" + re.escape(start_marker) + rb"\r?$", host))
    ends = list(re.finditer(rb"(?m)^" + re.escape(end_marker) + rb"\r?$", host))
    if not starts and not ends:
        newline = b"\r\n" if b"\r\n" in host else b"\n"
        prefix = host
        if prefix and not prefix.endswith(newline):
            prefix += newline
        if prefix and not prefix.endswith(newline + newline):
            prefix += newline
        return prefix + rendered, False
    if len(starts) != 1 or len(ends) != 1 or starts[0].start() >= ends[0].start():
        raise ConfigError("Local-review block in Git private exclude file is ambiguous")
    line_start = host.rfind(b"\n", 0, starts[0].start()) + 1
    newline_after_end = host.find(b"\n", ends[0].end())
    line_end = len(host) if newline_after_end < 0 else newline_after_end + 1
    return host[:line_start] + rendered + host[line_end:], True


__all__ = [
    "LOCAL_REVIEW_EXCLUDE_VERSION",
    "LocalReviewExcludeOutcome",
    "LocalReviewExcludeStatus",
    "ensure_local_review_excludes",
    "local_review_side_effects",
    "rollback_local_review_excludes",
]
