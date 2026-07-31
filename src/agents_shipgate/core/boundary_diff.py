"""Shared unified-diff plumbing for the boundary check family.

Common diff/text helpers used by ``agents_shipgate.core.codex_boundary``
and ``agents_shipgate.core.host_boundary``: unified-diff parsing,
hunk application, changed-file text resolution, and canonical
hashing/serialization. Semantics-free by design — consumers add their
own rule evaluation on top.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agents_shipgate.schemas.agent_result_v1 import AgentResultDiagnostic


@dataclass(frozen=True)
class DiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class DiffFile:
    old_path: str | None
    new_path: str | None
    added_lines: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)
    hunks: list[DiffHunk] = field(default_factory=list)
    is_deleted: bool = False
    is_new: bool = False
    is_rename: bool = False

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""


@dataclass(frozen=True)
class ResolvedFileText:
    old_text: str | None
    new_text: str | None
    source: str
    old_sha256: str | None
    new_sha256: str | None


@dataclass(frozen=True)
class BoundaryInputIssue:
    code: str
    path: str
    message: str


@dataclass(frozen=True)
class BoundaryChangeSet:
    mode: Literal["worktree", "git_range", "provided_diff"]
    scope: Literal["repository"]
    completeness: Literal["complete", "partial", "unknown"]
    diff_text: str
    changed_paths: tuple[str, ...]
    issues: tuple[BoundaryInputIssue, ...] = ()
    manifest_text_snapshot: str | None = None


def git_diff_path_token(prefix: str, path: str) -> str:
    """Render one repository path as a Git-compatible diff token.

    Raw diff headers are whitespace-delimited.  Synthetic headers therefore
    must C-quote names with leading whitespace, controls, non-ASCII bytes, or
    Git quoting characters; otherwise a legal repository name can be parsed as
    a different path and fall into the invalid-path sentinel.
    """

    raw = f"{prefix}{path}".encode()
    if raw and all(0x21 <= byte <= 0x7E and byte not in {0x22, 0x5C} for byte in raw):
        return raw.decode("ascii")
    escapes = {
        0x07: r"\a",
        0x08: r"\b",
        0x09: r"\t",
        0x0A: r"\n",
        0x0B: r"\v",
        0x0C: r"\f",
        0x0D: r"\r",
        0x22: r"\"",
        0x5C: r"\\",
    }
    rendered = "".join(
        escapes.get(byte, chr(byte) if 0x20 <= byte <= 0x7E else f"\\{byte:03o}")
        for byte in raw
    )
    return f'"{rendered}"'


def parse_unified_diff(diff_text: str) -> list[DiffFile]:
    files_out: list[DiffFile] = []
    current: dict[str, Any] | None = None
    current_hunk: DiffHunk | None = None

    def finish() -> None:
        nonlocal current, current_hunk
        if current is None:
            return
        if current_hunk is not None:
            current.setdefault("hunks", []).append(current_hunk)
            current_hunk = None
        files_out.append(
            DiffFile(
                old_path=current.get("old_path"),
                new_path=current.get("new_path"),
                added_lines=current.get("added_lines", []),
                removed_lines=current.get("removed_lines", []),
                hunks=current.get("hunks", []),
                is_deleted=bool(current.get("is_deleted")),
                is_new=bool(current.get("is_new")),
                is_rename=bool(current.get("is_rename")),
            )
        )
        current = None

    for raw_line in diff_text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw_line.startswith("diff --git "):
            finish()
            try:
                old_token, remainder = _parse_git_path_token(
                    raw_line[len("diff --git ") :]
                )
                new_token, trailing = _parse_git_path_token(remainder)
                if trailing.strip():
                    raise ValueError("unexpected diff-header suffix")
                old_path = _strip_diff_prefix(old_token)
                new_path = _strip_diff_prefix(new_token)
            except (UnicodeDecodeError, ValueError):
                old_path = "\0invalid-diff-path"
                new_path = "\0invalid-diff-path"
            current = {
                "old_path": old_path,
                "new_path": new_path,
                "header_old_path": old_path,
                "header_new_path": new_path,
                "added_lines": [],
                "removed_lines": [],
                "hunks": [],
                "is_deleted": False,
                "is_new": False,
                "is_rename": False,
            }
            current_hunk = None
            continue
        if current is None:
            continue
        if raw_line.startswith("deleted file mode"):
            current["is_deleted"] = True
        elif raw_line.startswith("new file mode"):
            current["is_new"] = True
        elif raw_line.startswith("rename from "):
            value = _parse_git_path_value(raw_line[len("rename from ") :])
            current["old_path"] = (
                value
                if value == current.get("header_old_path")
                else "\0invalid-diff-path"
            )
            current["is_rename"] = True
        elif raw_line.startswith("rename to "):
            value = _parse_git_path_value(raw_line[len("rename to ") :])
            current["new_path"] = (
                value
                if value == current.get("header_new_path")
                else "\0invalid-diff-path"
            )
            current["is_rename"] = True
        elif raw_line.startswith("--- "):
            value = _parse_git_path_value(raw_line[4:])
            parsed = None if value == "/dev/null" else _strip_diff_prefix(value)
            if (
                (parsed is None and not current.get("is_new"))
                or (
                    parsed is not None
                    and parsed != current.get("header_old_path")
                )
            ):
                current["old_path"] = "\0invalid-diff-path"
            else:
                current["old_path"] = parsed
        elif raw_line.startswith("+++ "):
            value = _parse_git_path_value(raw_line[4:])
            parsed = None if value == "/dev/null" else _strip_diff_prefix(value)
            if (
                (parsed is None and not current.get("is_deleted"))
                or (
                    parsed is not None
                    and parsed != current.get("header_new_path")
                )
            ):
                current["new_path"] = "\0invalid-diff-path"
            else:
                current["new_path"] = parsed
            if value == "/dev/null":
                current["is_deleted"] = True
        elif raw_line.startswith("@@ "):
            if current_hunk is not None:
                current.setdefault("hunks", []).append(current_hunk)
            current_hunk = _parse_hunk_header(raw_line)
        elif raw_line.startswith("+") and not raw_line.startswith("+++"):
            current["added_lines"].append(raw_line[1:])
            if current_hunk is not None:
                current_hunk.lines.append(("+", raw_line[1:]))
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            current["removed_lines"].append(raw_line[1:])
            if current_hunk is not None:
                current_hunk.lines.append(("-", raw_line[1:]))
        elif raw_line.startswith(" ") and current_hunk is not None:
            current_hunk.lines.append((" ", raw_line[1:]))
    finish()
    return files_out


def _parse_hunk_header(line: str) -> DiffHunk:
    match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
    if not match:
        return DiffHunk(old_start=0, old_count=0, new_start=0, new_count=0)
    old_start, old_count, new_start, new_count = match.groups()
    return DiffHunk(
        old_start=int(old_start),
        old_count=int(old_count or "1"),
        new_start=int(new_start),
        new_count=int(new_count or "1"),
    )


def _strip_diff_prefix(value: str) -> str:
    if value.startswith("a/") or value.startswith("b/"):
        return value[2:]
    return value


def _parse_git_path_value(value: str) -> str:
    """Decode one Git pathname, failing into a structural-review sentinel."""

    try:
        candidate = value
        if not value.lstrip(" ").startswith('"'):
            # Generic unified diffs conventionally append a timestamp after a
            # TAB. Split it before token parsing; otherwise the TAB becomes
            # part of the repository pathname and can hide a protected file.
            candidate = value.partition("\t")[0]
        token, trailing = _parse_git_path_token(candidate)
        if trailing:
            raise ValueError("unexpected path suffix")
        return token
    except (UnicodeDecodeError, ValueError):
        return "\0invalid-diff-path"


def _parse_git_path_token(value: str) -> tuple[str, str]:
    """Parse Git's raw or C-quoted pathname token exactly."""

    value = value.lstrip(" ")
    if not value:
        raise ValueError("missing Git path")
    if not value.startswith('"'):
        token, separator, remainder = value.partition(" ")
        return token, remainder.lstrip(" ") if separator else ""

    data = bytearray()
    index = 1
    escapes = {
        "a": 0x07,
        "b": 0x08,
        "f": 0x0C,
        "n": 0x0A,
        "r": 0x0D,
        "t": 0x09,
        "v": 0x0B,
        "\\": 0x5C,
        '"': 0x22,
    }
    while index < len(value):
        char = value[index]
        if char == '"':
            token = bytes(data).decode("utf-8", errors="strict")
            return token, value[index + 1 :].lstrip(" ")
        if char != "\\":
            data.extend(char.encode("utf-8"))
            index += 1
            continue
        index += 1
        if index >= len(value):
            raise ValueError("truncated Git path escape")
        escaped = value[index]
        if escaped in escapes:
            data.append(escapes[escaped])
            index += 1
            continue
        if escaped in "01234567":
            end = index
            while end < len(value) and end < index + 3 and value[end] in "01234567":
                end += 1
            data.append(int(value[index:end], 8))
            index = end
            continue
        raise ValueError("unsupported Git path escape")
    raise ValueError("unterminated Git path quote")


def _resolve_changed_file_text(
    workspace: Path,
    diff_file: DiffFile,
    diagnostics: list[AgentResultDiagnostic],
    read_cache: Any | None = None,
    *,
    preserve_rename_source: bool = False,
) -> ResolvedFileText:
    path = diff_file.path
    if diff_file.is_deleted:
        old_text = _old_text_from_hunks(diff_file)
        workspace_text, workspace_present = _existing_workspace_text(
            workspace,
            path,
            read_cache=read_cache,
        )
        if workspace_present and (
            workspace_text is None or workspace_text != old_text
        ):
            resolved = _unresolved_text("deleted_file_workspace_mismatch")
            diagnostics.append(
                _content_source_diagnostic(path, resolved, level="warning")
            )
            return resolved
        resolved = ResolvedFileText(
            old_text=old_text,
            new_text="" if old_text is not None else None,
            source="diff_deleted_file",
            old_sha256=_sha256_text(old_text) if old_text is not None else None,
            new_sha256=_sha256_text("") if old_text is not None else None,
        )
        diagnostics.append(_content_source_diagnostic(path, resolved))
        return resolved
    if diff_file.is_new:
        new_text = _new_text_from_hunks(diff_file)
        workspace_text, workspace_present = _existing_workspace_text(
            workspace,
            path,
            read_cache=read_cache,
        )
        if workspace_present and (
            workspace_text is None or workspace_text != new_text
        ):
            resolved = _unresolved_text("new_file_workspace_mismatch")
            diagnostics.append(
                _content_source_diagnostic(path, resolved, level="warning")
            )
            return resolved
        resolved = ResolvedFileText(
            old_text="",
            new_text=new_text,
            source="diff_new_file",
            old_sha256=_sha256_text(""),
            new_sha256=_sha256_text(new_text),
        )
        diagnostics.append(_content_source_diagnostic(path, resolved))
        return resolved

    if diff_file.is_rename and preserve_rename_source:
        # A rename diff still has two content-bearing sides. The previous
        # resolver treated every rename as ``old_text=""`` because a head
        # worktree only retains the destination path. That converted a
        # byte-identical rename into a capability addition and lost removals
        # when a recognized source was renamed out of an adapter path.
        #
        # Prefer the destination when the workspace contains the head tree and
        # reverse the hunks to reconstruct the source. Also support a base-tree
        # workspace by reading the source and applying the hunks forward.
        new_path = _safe_workspace_path(workspace, diff_file.new_path or "")
        if new_path is not None and new_path.is_file():
            new_text = _read_changed_text(
                new_path,
                workspace=workspace,
                read_cache=read_cache,
            )
            if new_text is not None:
                old_text = (
                    _apply_hunks(new_text, diff_file.hunks, direction="reverse")
                    if diff_file.hunks
                    else new_text
                )
                if old_text is not None:
                    resolved = ResolvedFileText(
                        old_text=old_text,
                        new_text=new_text,
                        source="workspace_renamed_head_file",
                        old_sha256=_sha256_text(old_text),
                        new_sha256=_sha256_text(new_text),
                    )
                    diagnostics.append(_content_source_diagnostic(path, resolved))
                    return resolved

        old_path = _safe_workspace_path(workspace, diff_file.old_path or "")
        if old_path is not None and old_path.is_file():
            old_text = _read_changed_text(
                old_path,
                workspace=workspace,
                read_cache=read_cache,
            )
            if old_text is not None:
                new_text = (
                    _apply_hunks(old_text, diff_file.hunks, direction="forward")
                    if diff_file.hunks
                    else old_text
                )
                if new_text is not None:
                    resolved = ResolvedFileText(
                        old_text=old_text,
                        new_text=new_text,
                        source="workspace_renamed_base_file",
                        old_sha256=_sha256_text(old_text),
                        new_sha256=_sha256_text(new_text),
                    )
                    diagnostics.append(_content_source_diagnostic(path, resolved))
                    return resolved

        resolved = _unresolved_text("renamed_file_unresolved")
        diagnostics.append(_content_source_diagnostic(path, resolved, level="warning"))
        return resolved

    if diff_file.is_rename:
        head_path = _safe_workspace_path(workspace, path)
        if head_path is None or head_path.is_symlink() or not head_path.is_file():
            resolved = _unresolved_text("renamed_file_unresolved")
            diagnostics.append(_content_source_diagnostic(path, resolved, level="warning"))
            return resolved
        try:
            if read_cache is None:
                new_text = head_path.read_text(encoding="utf-8")
                read_error = None
            else:
                new_text, read_error = read_cache.read(
                    head_path, containment_root=workspace
                )
            if read_error is not None or new_text is None:
                raise OSError(read_error or "read failed")
        except (OSError, UnicodeDecodeError):
            resolved = _unresolved_text("renamed_file_read_failed")
            diagnostics.append(_content_source_diagnostic(path, resolved, level="warning"))
            return resolved
        resolved = ResolvedFileText(
            old_text="",
            new_text=new_text,
            source="workspace_renamed_file",
            old_sha256=_sha256_text(""),
            new_sha256=_sha256_text(new_text),
        )
        diagnostics.append(_content_source_diagnostic(path, resolved))
        return resolved

    head_path = _safe_workspace_path(workspace, path)
    if head_path is None:
        resolved = _unresolved_text("path_outside_workspace")
        diagnostics.append(_content_source_diagnostic(path, resolved, level="warning"))
        return resolved
    if not head_path.is_file():
        resolved = _unresolved_text("workspace_file_missing")
        diagnostics.append(_content_source_diagnostic(path, resolved, level="warning"))
        return resolved
    try:
        if read_cache is None:
            workspace_text = head_path.read_text(encoding="utf-8")
            read_error = None
        else:
            workspace_text, read_error = read_cache.read(
                head_path, containment_root=workspace
            )
        if read_error is not None or workspace_text is None:
            raise OSError(read_error or "read failed")
    except (OSError, UnicodeDecodeError) as exc:
        resolved = _unresolved_text("workspace_read_failed")
        diagnostics.append(
            AgentResultDiagnostic(
                level="warning",
                code="content_source",
                message=(
                    "Could not read changed coding-agent boundary file "
                    f"({type(exc).__name__})."
                ),
                path=path,
            )
        )
        return resolved

    if _is_insertion_only_change(diff_file):
        reversed_text = _apply_hunks(workspace_text, diff_file.hunks, direction="reverse")
        if reversed_text is not None:
            resolved = ResolvedFileText(
                old_text=reversed_text,
                new_text=workspace_text,
                source="workspace_already_contains_diff_head",
                old_sha256=_sha256_text(reversed_text),
                new_sha256=_sha256_text(workspace_text),
            )
            diagnostics.append(_content_source_diagnostic(path, resolved))
            return resolved

    applied = _apply_hunks(workspace_text, diff_file.hunks, direction="forward")
    if applied is not None:
        resolved = ResolvedFileText(
            old_text=workspace_text,
            new_text=applied,
            source="diff_applied_to_workspace_base",
            old_sha256=_sha256_text(workspace_text),
            new_sha256=_sha256_text(applied),
        )
        diagnostics.append(_content_source_diagnostic(path, resolved))
        return resolved

    reversed_text = _apply_hunks(workspace_text, diff_file.hunks, direction="reverse")
    if reversed_text is not None:
        resolved = ResolvedFileText(
            old_text=reversed_text,
            new_text=workspace_text,
            source="workspace_already_contains_diff_head",
            old_sha256=_sha256_text(reversed_text),
            new_sha256=_sha256_text(workspace_text),
        )
        diagnostics.append(_content_source_diagnostic(path, resolved))
        return resolved

    resolved = _unresolved_text("diff_workspace_mismatch")
    diagnostics.append(_content_source_diagnostic(path, resolved, level="warning"))
    return resolved


def _unresolved_text(source: str) -> ResolvedFileText:
    return ResolvedFileText(
        old_text=None,
        new_text=None,
        source=source,
        old_sha256=None,
        new_sha256=None,
    )


def _read_changed_text(
    path: Path,
    *,
    workspace: Path,
    read_cache: Any | None,
) -> str | None:
    """Read one contained diff side without converting failures to content."""

    try:
        if read_cache is None:
            return path.read_text(encoding="utf-8")
        text, error = read_cache.read(path, containment_root=workspace)
        return text if error is None else None
    except (OSError, UnicodeDecodeError):
        return None


def _existing_workspace_text(
    workspace: Path,
    path: str,
    *,
    read_cache: Any | None,
) -> tuple[str | None, bool]:
    """Return a present workspace side without treating unsafe aliases as text."""

    lexical = workspace / path
    try:
        present = lexical.exists() or lexical.is_symlink()
    except OSError:
        return None, True
    if not present:
        return None, False
    safe = _safe_workspace_path(workspace, path)
    try:
        if (
            safe is None
            or lexical.is_symlink()
            or not lexical.is_file()
        ):
            return None, True
    except OSError:
        return None, True
    return (
        _read_changed_text(
            lexical,
            workspace=workspace,
            read_cache=read_cache,
        ),
        True,
    )


def _content_source_diagnostic(
    path: str,
    resolved: ResolvedFileText,
    *,
    level: str = "info",
) -> AgentResultDiagnostic:
    return AgentResultDiagnostic(
        level=level,  # type: ignore[arg-type]
        code="content_source",
        message=f"Evaluated coding-agent boundary file from {resolved.source}.",
        path=path,
    )


def _evaluated_file_record(path: str, resolved: ResolvedFileText) -> dict[str, Any]:
    return {
        "path": path,
        "source": resolved.source,
        "old_sha256": resolved.old_sha256,
        "new_sha256": resolved.new_sha256,
    }


def _new_text_from_hunks(diff_file: DiffFile) -> str:
    if diff_file.hunks:
        lines = [
            text
            for hunk in diff_file.hunks
            for kind, text in hunk.lines
            if kind in {" ", "+"}
        ]
        return _join_lines(lines)
    if diff_file.added_lines:
        return _join_lines(diff_file.added_lines)
    return ""


def _old_text_from_hunks(diff_file: DiffFile) -> str | None:
    if diff_file.hunks:
        lines = [
            text
            for hunk in diff_file.hunks
            for kind, text in hunk.lines
            if kind in {" ", "-"}
        ]
        return _join_lines(lines)
    if diff_file.removed_lines:
        return _join_lines(diff_file.removed_lines)
    return None


def _apply_hunks(
    text: str,
    hunks: list[DiffHunk],
    *,
    direction: str,
) -> str | None:
    if not hunks:
        return text
    lines = text.splitlines()
    offset = 0
    for hunk in hunks:
        if direction == "forward":
            start = hunk.old_start - 1 if hunk.old_count > 0 else hunk.old_start
            expected = [value for kind, value in hunk.lines if kind in {" ", "-"}]
            replacement = [value for kind, value in hunk.lines if kind in {" ", "+"}]
        else:
            start = hunk.new_start - 1 if hunk.new_count > 0 else hunk.new_start
            expected = [value for kind, value in hunk.lines if kind in {" ", "+"}]
            replacement = [value for kind, value in hunk.lines if kind in {" ", "-"}]
        index = start + offset
        if index < 0:
            return None
        if index > len(lines):
            return None
        if index + len(expected) > len(lines):
            return None
        if lines[index : index + len(expected)] != expected:
            return None
        lines[index : index + len(expected)] = replacement
        offset += len(replacement) - len(expected)
    return _join_lines(lines, final_newline=text.endswith("\n"))


def _is_insertion_only_change(diff_file: DiffFile) -> bool:
    return any(
        any(kind == "+" for kind, _ in hunk.lines)
        and not any(kind == "-" for kind, _ in hunk.lines)
        for hunk in diff_file.hunks
    )


def _join_lines(lines: list[str], *, final_newline: bool = True) -> str:
    if not lines:
        return ""
    text = "\n".join(lines)
    return f"{text}\n" if final_newline else text


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _safe_workspace_path(workspace: Path, value: str) -> Path | None:
    try:
        candidate = (workspace / value).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    try:
        candidate.relative_to(workspace)
    except ValueError:
        return None
    return candidate
