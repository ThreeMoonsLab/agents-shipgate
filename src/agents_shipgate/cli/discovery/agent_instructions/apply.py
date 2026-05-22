"""Per-target decision tree for ``--agent-instructions``.

Pure function ``render_targets`` returns the rendered content for each
requested target without touching the filesystem. ``apply_agent_instructions``
applies the decision tree against a workspace and returns one
:class:`TargetOutcome` per requested target.

Status enum:

- ``created_with_block`` — host file did not exist; we created it with an H1
  preamble (where applicable) plus the managed block.
- ``appended`` — host file existed without our markers; block appended.
- ``unchanged`` — markers present, content already current.
- ``updated`` — markers present, content differed; we rewrote the block.
- ``migrated`` — markers present at an older version; block + version bumped.
- ``would_render`` — synthetic dry-run status; emitted when ``write=False``.
- ``skipped_newer_version`` — markers present at a *newer* version than this
  CLI; refused to downgrade.
- ``skipped_ambiguous`` — multiple/mismatched markers; refused to guess.
- ``skipped_user_modified`` — cursor MDC file content does not match any
  shipped render, or a Codex skill file was edited by the user; refused to
  overwrite.
- ``skipped_symlink`` — the host path is a symlink; refused to follow it
  outside the workspace.
- ``skipped_directory_template`` — the directory form
  ``.github/PULL_REQUEST_TEMPLATE/`` exists; v1 only handles the file form.
- ``created_file_tree`` — created a repo-scoped skill bundle (Codex or
  Claude Code).
- ``migrated_and_repaired`` — rewrote prior-version skill files and
  recreated at least one missing file in the same pass.

Every ``skipped_*`` status contributes 2 to the exit code (matching the
``skipped_existing_target`` precedent in :mod:`ci_workflow`); other statuses
contribute 0.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from agents_shipgate.cli.discovery.agent_instructions.managed_block import (
    UpsertStatus,
    upsert,
)
from agents_shipgate.cli.discovery.agent_instructions.renderers import (
    claude_code_skill as claude_code_skill_renderer,
)
from agents_shipgate.cli.discovery.agent_instructions.renderers import (
    codex_skill as codex_skill_renderer,
)
from agents_shipgate.cli.discovery.agent_instructions.renderers import (
    cursor as cursor_renderer,
)
from agents_shipgate.cli.discovery.agent_instructions.renderers import (
    render_agents_md,
    render_claude_code_skill_bundle_text,
    render_claude_code_skill_files,
    render_claude_md,
    render_codex_skill_bundle_text,
    render_codex_skill_files,
    render_cursor_file,
    render_pr_template,
)
from agents_shipgate.cli.discovery.agent_instructions.targets import (
    BLOCK_VERSION,
    SPECS,
)

PR_TEMPLATE_LOWER = ".github/pull_request_template.md"
PR_TEMPLATE_UPPER = ".github/PULL_REQUEST_TEMPLATE.md"
PR_TEMPLATE_DIR = ".github/PULL_REQUEST_TEMPLATE"

H1_PREAMBLES = {
    "agents-md": "# Agents\n",
    "claude-md": "# Claude Code Instructions\n",
    "pr-template": "",  # PR templates conventionally have no preamble.
}

_SKIPPED_STATUSES = frozenset(
    {
        "skipped_newer_version",
        "skipped_ambiguous",
        "skipped_user_modified",
        "skipped_symlink",
        "skipped_directory_template",
    }
)


def _first_symlink_in_chain(path: Path, workspace: Path) -> Path | None:
    """Return the first symlink encountered between ``workspace`` (exclusive)
    and ``path`` (inclusive), or ``None`` if no existing component is a
    symlink.

    Walking the parent chain prevents the directory-symlink escape: a
    workspace where ``.github -> /tmp/outside`` would otherwise route
    ``.github/pull_request_template.md`` writes outside the workspace even
    though the file itself is not a symlink. Non-existent intermediates
    cannot be symlinks, so the walk stops at the first missing component.
    """
    workspace_real = workspace.resolve()
    try:
        relative_parts = path.relative_to(workspace_real).parts
    except ValueError:
        # Caller bug: target was not under workspace lexically. Refuse.
        return path
    cur = workspace_real
    for part in relative_parts:
        cur = cur / part
        if cur.is_symlink():
            return cur
        if not cur.exists():
            return None
    return None


@dataclass
class TargetOutcome:
    """Result of applying or rendering a single target."""

    name: str
    path: str
    status: str
    block_version: int = BLOCK_VERSION
    message: str = ""
    rendered: str | None = None  # populated only on dry-run
    files: list[dict[str, str]] | None = None  # populated for file-tree targets

    def to_json(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.name,
            "path": self.path,
            "status": self.status,
            "block_version": self.block_version,
        }
        if self.message:
            payload["message"] = self.message
        if self.rendered is not None:
            payload["rendered"] = self.rendered
        if self.files is not None:
            payload["files"] = list(self.files)
        return payload

    @property
    def exit_contribution(self) -> int:
        return 2 if self.status in _SKIPPED_STATUSES else 0


@dataclass
class ApplyResult:
    """Aggregate result returned by :func:`apply_agent_instructions`."""

    requested: list[str]
    targets: list[TargetOutcome]
    block_version: int = BLOCK_VERSION

    @property
    def exit_code(self) -> int:
        return max((t.exit_contribution for t in self.targets), default=0)

    def to_json(self) -> dict[str, object]:
        return {
            "requested": list(self.requested),
            "block_version": self.block_version,
            "targets": [t.to_json() for t in self.targets],
        }


# --- rendering -------------------------------------------------------------


def _rendered_inner(name: str) -> str:
    if name == "agents-md":
        return render_agents_md()
    if name == "claude-md":
        return render_claude_md()
    if name == "codex-skill":
        return render_codex_skill_bundle_text()
    if name == "claude-code-skill":
        return render_claude_code_skill_bundle_text()
    if name == "pr-template":
        return render_pr_template()
    if name == "cursor":
        return render_cursor_file()
    raise ValueError(f"unknown target {name!r}")  # pragma: no cover - guarded by selector


def render_targets(workspace: Path, requested: Iterable[str]) -> list[TargetOutcome]:
    """Pure rendering pass for dry-run output. Does not read existing files."""
    workspace = workspace.resolve()
    outcomes: list[TargetOutcome] = []
    for name in requested:
        spec = SPECS[name]
        # Lexical join only — never resolve(). Resolving would follow a
        # symlink at the host path and report a path outside the workspace,
        # which would mislead callers in the dry-run JSON.
        path = workspace / spec.relative_path
        outcomes.append(
            TargetOutcome(
                name=name,
                path=str(path),
                status="would_render",
                rendered=_rendered_inner(name),
                files=(
                    _file_payload(workspace, _FILE_TREE_RENDERERS[name]())
                    if spec.is_file_tree
                    else None
                ),
            )
        )
    return outcomes


# --- applying --------------------------------------------------------------


def _resolve_pr_template_path(workspace: Path) -> tuple[Path, str | None]:
    """Pick the PR template path to use.

    Returns (path, error_status). On ambiguity the path is the canonical lower
    form and ``error_status`` is the status enum to surface.

    Case-insensitive filesystems (macOS APFS, Windows NTFS) report the same
    inode for both casings — ``Path.samefile`` collapses them so we do not
    falsely report ``skipped_ambiguous`` when only one PR template actually
    exists on disk.
    """
    upper = workspace / PR_TEMPLATE_UPPER
    lower = workspace / PR_TEMPLATE_LOWER
    directory = workspace / PR_TEMPLATE_DIR
    if directory.is_dir():
        return lower, "skipped_directory_template"
    upper_exists = upper.is_file()
    lower_exists = lower.is_file()
    if upper_exists and lower_exists:
        try:
            same = upper.samefile(lower)
        except OSError:
            same = False
        if same:
            # Same inode (case-insensitive FS) — there is only one file on
            # disk. Use the lowercase canonical path for output stability.
            return lower, None
        # Two genuinely distinct files. Pick the one with our marker; if
        # neither has it, refuse with skipped_ambiguous.
        for candidate in (lower, upper):
            try:
                if b"agents-shipgate:start" in candidate.read_bytes():
                    return candidate, None
            except OSError:
                continue
        return lower, "skipped_ambiguous"
    if upper_exists:
        return upper, None
    return lower, None


def _apply_managed_block_target(
    name: str, path: Path, workspace: Path
) -> TargetOutcome:
    inner = _rendered_inner(name)
    preamble = H1_PREAMBLES.get(name, "")
    # Refuse to follow symlinks anywhere in the parent chain. A symlinked
    # directory above the target file (e.g., ``.github -> /tmp/outside``)
    # would otherwise route the write outside the workspace.
    symlink = _first_symlink_in_chain(path, workspace)
    if symlink is not None:
        return TargetOutcome(
            name=name,
            path=str(path),
            status="skipped_symlink",
            message=(
                f"{symlink} is a symlink; refusing to follow it. "
                "Replace the symlink with a regular file or directory before "
                "re-running."
            ),
        )
    if not path.exists():
        # Compose H1 preamble (if any) + managed block from a virtual empty
        # host. ``upsert`` handles the empty-host case for us.
        empty_host = preamble.encode("utf-8")
        # If the preamble is non-empty, append a blank line separator before
        # the block so the result reads as "# Heading\n\n<block>".
        result = upsert(empty_host, inner=inner, version=BLOCK_VERSION)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(result.new_bytes)
        return TargetOutcome(
            name=name,
            path=str(path),
            status="created_with_block",
            block_version=result.block_version,
            message=f"Created {path} with managed block (v{result.block_version}).",
        )

    host = path.read_bytes()
    result = upsert(host, inner=inner, version=BLOCK_VERSION)
    if result.status is UpsertStatus.AMBIGUOUS:
        return TargetOutcome(
            name=name,
            path=str(path),
            status="skipped_ambiguous",
            block_version=BLOCK_VERSION,
            message=(
                f"{path} contains ambiguous agents-shipgate markers. "
                "Resolve manually before re-running."
            ),
        )
    if result.status is UpsertStatus.NEWER_VERSION:
        return TargetOutcome(
            name=name,
            path=str(path),
            status="skipped_newer_version",
            block_version=result.block_version,
            message=(
                f"{path} contains a newer block version (v{result.block_version}); "
                f"this CLI ships v{BLOCK_VERSION}. Upgrade the CLI."
            ),
        )
    if result.status is UpsertStatus.UNCHANGED:
        return TargetOutcome(
            name=name,
            path=str(path),
            status="unchanged",
            block_version=result.block_version,
            message=f"{path} already up to date (v{result.block_version}).",
        )

    path.write_bytes(result.new_bytes)
    if result.status is UpsertStatus.APPENDED:
        return TargetOutcome(
            name=name,
            path=str(path),
            status="appended",
            block_version=result.block_version,
            message=f"Appended managed block (v{result.block_version}) to {path}.",
        )
    if result.status is UpsertStatus.MIGRATED:
        return TargetOutcome(
            name=name,
            path=str(path),
            status="migrated",
            block_version=result.block_version,
            message=f"Migrated {path} block to v{result.block_version}.",
        )
    return TargetOutcome(
        name=name,
        path=str(path),
        status="updated",
        block_version=result.block_version,
        message=f"Updated managed block (v{result.block_version}) in {path}.",
    )


def _apply_cursor(path: Path, workspace: Path) -> TargetOutcome:
    rendered = render_cursor_file()
    rendered_bytes = rendered.encode("utf-8")
    rendered_sha = hashlib.sha256(rendered_bytes).hexdigest()
    symlink = _first_symlink_in_chain(path, workspace)
    if symlink is not None:
        return TargetOutcome(
            name="cursor",
            path=str(path),
            status="skipped_symlink",
            message=(
                f"{symlink} is a symlink; refusing to follow it. "
                "Replace the symlink with a regular file or directory before "
                "re-running."
            ),
        )
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rendered_bytes)
        return TargetOutcome(
            name="cursor",
            path=str(path),
            status="created_with_block",
            message=f"Created {path}.",
        )
    existing = path.read_bytes()
    existing_sha = hashlib.sha256(existing).hexdigest()
    if existing_sha == rendered_sha:
        return TargetOutcome(
            name="cursor",
            path=str(path),
            status="unchanged",
            message=f"{path} already up to date.",
        )
    if existing_sha in cursor_renderer.PRIOR_RENDER_SHA256:
        path.write_bytes(rendered_bytes)
        return TargetOutcome(
            name="cursor",
            path=str(path),
            status="migrated",
            message=f"Migrated {path} to current renderer (v{BLOCK_VERSION}).",
        )
    return TargetOutcome(
        name="cursor",
        path=str(path),
        status="skipped_user_modified",
        message=(
            f"{path} differs from any shipped render; not overwriting. "
            "Delete the file or revert to a shipped version before re-running."
        ),
    )


_FILE_TREE_RENDERERS: dict[str, Callable[[], dict[str, str]]] = {
    "codex-skill": render_codex_skill_files,
    "claude-code-skill": render_claude_code_skill_files,
}

_FILE_TREE_MODULES = {
    "codex-skill": codex_skill_renderer,
    "claude-code-skill": claude_code_skill_renderer,
}


def _apply_file_tree(
    name: str,
    path: Path,
    workspace: Path,
    render_fn: Callable[[], dict[str, str]],
    prior_sha: dict[str, tuple[str, ...]],
) -> TargetOutcome:
    rendered_files = render_fn()
    target_paths = {rel: workspace / rel for rel in rendered_files}
    files = _file_payload(workspace, rendered_files)

    for target in target_paths.values():
        symlink = _first_symlink_in_chain(target, workspace)
        if symlink is not None:
            return TargetOutcome(
                name=name,
                path=str(path),
                status="skipped_symlink",
                files=files,
                message=(
                    f"{symlink} is a symlink; refusing to follow it. "
                    "Replace the symlink with a regular file or directory before "
                    "re-running."
                ),
            )

    existing: list[str] = []
    missing: list[str] = []
    prior_version: list[str] = []
    for rel, content in rendered_files.items():
        target = target_paths[rel]
        if not target.exists():
            missing.append(rel)
            continue
        existing.append(rel)
        if not target.is_file():
            return TargetOutcome(
                name=name,
                path=str(path),
                status="skipped_user_modified",
                files=files,
                message=f"{target} exists but is not a regular file; refusing to overwrite.",
            )
        current = target.read_text(encoding="utf-8")
        if current == content:
            continue
        current_sha = hashlib.sha256(current.encode("utf-8")).hexdigest()
        if current_sha in prior_sha.get(rel, ()):
            prior_version.append(rel)
            continue
        return TargetOutcome(
            name=name,
            path=str(path),
            status="skipped_user_modified",
            files=files,
            message=(
                f"{target} does not match a shipped Agents Shipgate {name} file; "
                "refusing to overwrite user edits. Edit the file manually or remove "
                "the skill directory before re-running."
            ),
        )

    for rel, content in rendered_files.items():
        target = target_paths[rel]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    if not existing:
        status = "created_file_tree"
        message = f"Wrote {name} skill bundle to {path}"
    elif prior_version and missing:
        status = "migrated_and_repaired"
        message = f"Updated {name} skill bundle and repaired missing file(s) at {path}"
    elif prior_version:
        status = "migrated"
        message = f"Updated {name} skill bundle at {path}"
    elif missing:
        status = "updated"
        message = f"Repaired missing {name} skill file(s) under {path}"
    else:
        status = "unchanged"
        message = f"{name} skill bundle already current at {path}"
    return TargetOutcome(
        name=name,
        path=str(path),
        status=status,
        files=files,
        message=message,
    )


def _file_payload(workspace: Path, files: dict[str, str]) -> list[dict[str, str]]:
    return [
        {"path": str(workspace / rel), "content": content}
        for rel, content in files.items()
    ]


def apply_agent_instructions(
    workspace: Path, requested: Iterable[str], *, write: bool
) -> ApplyResult:
    """Apply the per-target decision tree against ``workspace``.

    With ``write=False`` this is a pure rendering pass (no host files read).
    With ``write=True`` each target is created/appended/updated/skipped per
    its decision tree and the on-disk file is mutated accordingly.
    """
    workspace = workspace.resolve()
    requested_list = list(requested)
    if not write:
        return ApplyResult(
            requested=requested_list,
            targets=render_targets(workspace, requested_list),
        )

    outcomes: list[TargetOutcome] = []
    for name in requested_list:
        spec = SPECS[name]
        if name == "pr-template":
            path, override_status = _resolve_pr_template_path(workspace)
            if override_status == "skipped_directory_template":
                outcomes.append(
                    TargetOutcome(
                        name=name,
                        path=str(workspace / PR_TEMPLATE_DIR),
                        status="skipped_directory_template",
                        message=(
                            f"{workspace / PR_TEMPLATE_DIR} directory present; "
                            "v1 only handles the single-file PR template form. "
                            "Add the snippet manually to one of the directory templates."
                        ),
                    )
                )
                continue
            if override_status == "skipped_ambiguous":
                outcomes.append(
                    TargetOutcome(
                        name=name,
                        path=str(path),
                        status="skipped_ambiguous",
                        message=(
                            f"Both {workspace / PR_TEMPLATE_LOWER} and "
                            f"{workspace / PR_TEMPLATE_UPPER} exist without an "
                            "agents-shipgate marker. Delete one before re-running."
                        ),
                    )
                )
                continue
        else:
            # Lexical join — never resolve(). Resolving a symlink target
            # would write outside the workspace; the per-target helpers
            # check ``is_symlink`` and refuse before touching the file.
            path = workspace / spec.relative_path

        if spec.is_file_tree:
            outcomes.append(
                _apply_file_tree(
                    name, path, workspace,
                    _FILE_TREE_RENDERERS[name],
                    _FILE_TREE_MODULES[name].PRIOR_RENDER_SHA256,
                )
            )
        elif name == "cursor":
            outcomes.append(_apply_cursor(path, workspace))
        else:
            outcomes.append(_apply_managed_block_target(name, path, workspace))

    return ApplyResult(requested=requested_list, targets=outcomes)
