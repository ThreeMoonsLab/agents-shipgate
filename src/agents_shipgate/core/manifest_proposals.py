"""Fail-closed classification for agent-authored manifest proposals.

This module does not approve a manifest change.  It recognizes one narrow
authoring-safe shape: valid built-in ``tool_sources`` rows appended to an
otherwise byte-semantically unchanged manifest.  The resulting concrete diff
still goes through verify and remains a protected-surface review item.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from agents_shipgate.core.boundary_diff import (
    DiffFile,
    ResolvedFileText,
    _resolve_changed_file_text,
)
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.manifest.tool_sources import BUILTIN_TOOL_SOURCE_TYPES

_SAFE_SOURCE_KEYS = frozenset({"id", "type", "path", "mode"})
# MCP/OpenAPI/Conductor declarations name a single protocol artifact. The
# remaining built-in framework/config adapters legitimately accept a file or a
# directory; containment/existence is checked here and verify validates the
# concrete loader shape.
_FILE_SOURCE_TYPES = frozenset({"mcp", "openapi", "conductor"})


@dataclass(frozen=True)
class ToolSourceProposalAssessment:
    """Semantic result for one proposed ``shipgate.yaml`` change."""

    proposal_safe: bool
    reason: str
    added_source_ids: tuple[str, ...] = ()


def assess_coverage_increasing_tool_source_proposal(
    *,
    workspace: Path,
    diff_file: DiffFile,
    resolved: ResolvedFileText | None = None,
    manifest_dir: Path | None = None,
) -> ToolSourceProposalAssessment:
    """Recognize an append-only, coverage-increasing manifest proposal.

    Safety is deliberately structural and monotonic:

    * both manifest sides must parse and validate;
    * every non-``tool_sources`` value must remain identical;
    * all existing source rows must remain identical and in the same order;
    * added rows may use only built-in adapters and non-authority fields; and
    * every added path must resolve from the manifest directory to an
      existing, non-symlink workspace artifact of the expected broad shape.

    A safe result authorizes proposal authorship only.  It never supplies or
    asserts approval, action semantics, bindings, policy evidence, or release
    authority.
    """

    root = workspace.resolve()
    source_root = manifest_dir if manifest_dir is not None else root
    source_root = source_root if source_root.is_absolute() else root / source_root
    try:
        source_root.relative_to(root)
    except ValueError:
        return _unsafe("manifest directory resolves outside the workspace")
    if resolved is None:
        resolved = _resolve_changed_file_text(root, diff_file, [])
    if resolved.old_text is None or resolved.new_text is None:
        return _unsafe("manifest diff could not be resolved against the workspace")

    try:
        old_payload = _load_yaml_mapping(resolved.old_text)
        new_payload = _load_yaml_mapping(resolved.new_text)
        AgentsShipgateManifest.model_validate(old_payload)
        AgentsShipgateManifest.model_validate(new_payload)
    except (TypeError, ValueError, ValidationError, YAMLError) as exc:
        return _unsafe(f"manifest side is invalid: {type(exc).__name__}")

    old_without_sources = deepcopy(old_payload)
    new_without_sources = deepcopy(new_payload)
    old_had_sources = "tool_sources" in old_payload
    old_sources = old_without_sources.pop("tool_sources", [])
    new_sources = new_without_sources.pop("tool_sources", [])
    if old_without_sources != new_without_sources:
        return _unsafe("proposal changes manifest keys outside tool_sources")
    if not isinstance(old_sources, list) or not isinstance(new_sources, list):
        return _unsafe("tool_sources must remain a list")
    if len(new_sources) <= len(old_sources):
        return _unsafe("proposal does not append a tool source")
    if new_sources[: len(old_sources)] != old_sources:
        return _unsafe("proposal removes, reorders, or modifies an existing tool source")

    additions = new_sources[len(old_sources) :]
    added_rows_reason = _validate_exact_added_rows(
        diff_file=diff_file,
        old_had_sources=old_had_sources,
        additions=additions,
    )
    if added_rows_reason is not None:
        return _unsafe(added_rows_reason)
    added_ids: list[str] = []
    for row in additions:
        reason = _validate_added_source(
            containment_root=root,
            source_root=source_root,
            row=row,
        )
        if reason is not None:
            return _unsafe(reason)
        assert isinstance(row, dict)  # proved by _validate_added_source
        added_ids.append(str(row["id"]))

    return ToolSourceProposalAssessment(
        proposal_safe=True,
        reason=(
            "proposal only appends valid built-in tool_sources entries; the "
            "concrete manifest diff still requires deterministic verification"
        ),
        added_source_ids=tuple(added_ids),
    )


def _load_yaml_mapping(text: str) -> dict[str, Any]:
    loader = YAML(typ="safe")
    loader.allow_duplicate_keys = False
    payload = loader.load(text)
    if not isinstance(payload, dict):
        raise TypeError("manifest must be a YAML mapping")
    return dict(payload)


def _validate_added_source(
    *,
    containment_root: Path,
    source_root: Path,
    row: Any,
) -> str | None:
    if not isinstance(row, dict):
        return "added tool source must be a mapping"
    if not set(row).issubset(_SAFE_SOURCE_KEYS):
        return "added tool source contains authority-bearing or unsupported fields"
    source_id = row.get("id")
    source_type = row.get("type")
    source_path = row.get("path")
    mode = row.get("mode")
    if not isinstance(source_id, str) or not source_id.strip():
        return "added tool source id must be non-empty"
    if source_type not in BUILTIN_TOOL_SOURCE_TYPES:
        return "proposal-safe additions are limited to built-in tool source types"
    if not isinstance(source_path, str) or not source_path.strip():
        return "added tool source path must be non-empty"
    if source_type == "codex_plugin":
        if mode not in {"package", "marketplace"}:
            return "proposal-safe Codex plugin additions require an explicit mode"
    elif mode is not None:
        return "only Codex plugin additions may declare mode"

    normalized = source_path.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or normalized in {"", "."} or ".." in pure.parts:
        return "added tool source path must be a contained non-root relative path"
    candidate = source_root.joinpath(*pure.parts)
    try:
        candidate_relative = PurePosixPath(
            candidate.relative_to(containment_root).as_posix()
        )
    except ValueError:
        return "added tool source path resolves outside the workspace"
    if _path_has_symlink(containment_root, candidate_relative):
        return "added tool source path must not traverse a symlink"
    try:
        candidate.resolve().relative_to(containment_root)
    except (OSError, ValueError):
        return "added tool source path resolves outside the workspace"
    if not candidate.exists():
        return "added tool source path does not exist"

    if source_type in _FILE_SOURCE_TYPES and not candidate.is_file():
        return f"{source_type} proposal-safe source must be a file"
    if source_type == "codex_plugin":
        if mode == "package":
            plugin_manifest = candidate / ".codex-plugin" / "plugin.json"
            plugin_manifest_relative = PurePosixPath(
                plugin_manifest.relative_to(containment_root).as_posix()
            )
            if (
                not candidate.is_dir()
                or _path_has_symlink(containment_root, plugin_manifest_relative)
                or not plugin_manifest.is_file()
            ):
                return "Codex plugin package lacks .codex-plugin/plugin.json"
        elif not candidate.is_file():
            return "Codex plugin marketplace source must be a file"
    return None


def _validate_exact_added_rows(
    *,
    diff_file: DiffFile,
    old_had_sources: bool,
    additions: list[Any],
) -> str | None:
    """Require the textual diff to contain exactly the parsed source rows."""

    if diff_file.removed_lines:
        return "proposal-safe manifest changes must be insertion-only"
    if not diff_file.added_lines:
        return "proposal-safe manifest change contains no added source lines"
    # Intentionally over-restrict the authoring safelist: even a quoted ``#``
    # in an otherwise valid id/path is human-routed so comments or approval
    # claims cannot be smuggled into the proposal-only exception.
    if any(not line.strip() or "#" in line for line in diff_file.added_lines):
        return "proposal-safe source lines must not contain comments or blank additions"
    added_text = "\n".join(diff_file.added_lines) + "\n"
    snippet = f"tool_sources:\n{added_text}" if old_had_sources else added_text
    try:
        payload = _load_yaml_mapping(snippet)
    except (TypeError, ValueError, YAMLError):
        return "added lines are not a standalone tool_sources append"
    if set(payload) != {"tool_sources"} or payload.get("tool_sources") != additions:
        return "added lines contain content outside the appended tool source rows"
    return None


def _path_has_symlink(root: Path, path: PurePosixPath) -> bool:
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _unsafe(reason: str) -> ToolSourceProposalAssessment:
    return ToolSourceProposalAssessment(proposal_safe=False, reason=reason)


__all__ = [
    "ToolSourceProposalAssessment",
    "assess_coverage_increasing_tool_source_proposal",
]
