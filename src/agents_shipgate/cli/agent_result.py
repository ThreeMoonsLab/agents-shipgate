from __future__ import annotations

import json
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from agents_shipgate.cli.verify.git import (
    BinaryCapabilityDiffError,
    carries_manifest_like_yaml,
    read_file_at_ref,
    removes_a_yaml_file,
)
from agents_shipgate.config.loader import load_manifest_text
from agents_shipgate.core.agent_boundary import (
    build_agent_boundary_result as project_agent_boundary_result,
)
from agents_shipgate.core.agent_boundary import (
    evaluate_agent_boundary,
)
from agents_shipgate.core.agent_controls import git_root_for
from agents_shipgate.core.boundary_diff import (
    BoundaryChangeSet,
    BoundaryInputIssue,
    git_diff_path_token,
)
from agents_shipgate.core.boundary_registry import is_agent_boundary_path
from agents_shipgate.core.codex_boundary import parse_unified_diff
from agents_shipgate.core.errors import ConfigError, InputParseError
from agents_shipgate.core.trust_roots import (
    inspect_lexical_path_identity,
    is_configured_manifest,
    read_identity_bound_text,
    trust_root_class_for,
)
from agents_shipgate.inputs.codex_plugin import resolve_local_codex_marketplace_roots
from agents_shipgate.schemas.agent_boundary import AgentBoundaryResultV1
from agents_shipgate.schemas.agent_control import project_legacy_agent_control
from agents_shipgate.schemas.agent_result import AgentResultV2
from agents_shipgate.schemas.codex_boundary_result import (
    CODEX_BOUNDARY_RESULT_SCHEMA_VERSION,
    CodexBoundaryResultV2,
)
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.triggers import (
    SURFACE_CLASS_CAPABILITY,
    _git_diff_context,
    load_triggers,
    result_has_surface_class,
)
from agents_shipgate.triggers import evaluate as evaluate_trigger

_MANIFEST_SNAPSHOT_UNSET = object()


def build_codex_agent_result(
    *,
    agent: str = "codex",
    workspace: Path,
    diff_text: str,
    config: Path,
    policy: Path | None,
    input_issues: list[BoundaryInputIssue] | None = None,
    input_mode: str = "provided_diff",
    base: str | None = None,
    head: str | None = None,
    verification_replayable: bool | None = None,
    base_manifest_absent: bool | None = None,
    requested_workspace: Path | None = None,
    changed_files_override: list[str] | None = None,
    manifest_text_snapshot: str | None | object = _MANIFEST_SNAPSHOT_UNSET,
) -> CodexBoundaryResultV2:
    """Frozen v2 compatibility projection from the central assessment."""

    return _assessment_for_diff(
        agent=agent,
        workspace=workspace,
        diff_text=diff_text,
        config=config,
        policy=policy,
        input_mode=input_mode,
        input_issues=input_issues,
        base=base,
        head=head,
        verification_replayable=verification_replayable,
        base_manifest_absent=base_manifest_absent,
        requested_workspace=requested_workspace,
        changed_files_override=changed_files_override,
        manifest_text_snapshot=manifest_text_snapshot,
    ).legacy_result


def build_agent_boundary_result(
    *,
    agent: str = "codex",
    workspace: Path,
    diff_text: str,
    config: Path,
    policy: Path | None,
    input_mode: str = "provided_diff",
    input_issues: list[BoundaryInputIssue] | None = None,
    base: str | None = None,
    head: str | None = None,
    verification_replayable: bool | None = None,
    base_manifest_absent: bool | None = None,
    requested_workspace: Path | None = None,
    changed_files_override: list[str] | None = None,
    manifest_text_snapshot: str | None | object = _MANIFEST_SNAPSHOT_UNSET,
) -> AgentBoundaryResultV1:
    assessment = _assessment_for_diff(
        agent=agent,
        workspace=workspace,
        diff_text=diff_text,
        config=config,
        policy=policy,
        input_mode=input_mode,
        input_issues=input_issues,
        base=base,
        head=head,
        verification_replayable=verification_replayable,
        base_manifest_absent=base_manifest_absent,
        requested_workspace=requested_workspace,
        changed_files_override=changed_files_override,
        manifest_text_snapshot=manifest_text_snapshot,
    )
    return project_agent_boundary_result(assessment)


def _assessment_for_diff(
    *,
    agent: str,
    workspace: Path,
    diff_text: str,
    config: Path,
    policy: Path | None,
    input_mode: str,
    input_issues: list[BoundaryInputIssue] | None,
    base: str | None,
    head: str | None,
    verification_replayable: bool | None,
    base_manifest_absent: bool | None,
    requested_workspace: Path | None,
    changed_files_override: list[str] | None,
    manifest_text_snapshot: str | None | object,
):
    workspace_as_given = requested_workspace or workspace
    workspace = workspace.resolve()
    diff_files = parse_unified_diff(diff_text)
    changed_files = sorted(
        {
            *(changed_files_override or []),
            *(
                path
                for item in diff_files
                for path in (item.new_path, item.old_path)
                if path
            ),
        }
    )
    config_path = _lexical_config_path(
        workspace,
        config,
        requested_workspace=workspace_as_given,
    )
    manifest_text = (
        _read_manifest_snapshot(workspace, config_path)
        if manifest_text_snapshot is _MANIFEST_SNAPSHOT_UNSET
        else manifest_text_snapshot
    )
    assert manifest_text is None or isinstance(manifest_text, str)
    manifest_present = manifest_text is not None
    manifest: AgentsShipgateManifest | None = None
    if manifest_text is not None:
        try:
            manifest = load_manifest_text(manifest_text, source=config_path)
        except ConfigError:
            # Invalid manifest content remains present trigger context, but it
            # cannot safely contribute declarations.
            manifest = None
    if verification_replayable is None:
        verification_replayable = input_mode != "provided_diff"
    adoption_candidate = any(
        item.is_new
        and not item.is_deleted
        and not item.is_rename
        and item.new_path
        and (
            trust_root_class_for(item.new_path.replace("\\", "/")) == "manifest"
            or is_configured_manifest(
                config_path,
                item.new_path,
                workspace=workspace,
            )
        )
        for item in diff_files
    )
    if base_manifest_absent is None and adoption_candidate:
        comparison_ref = (
            base
            if input_mode == "git_range" and base
            else "HEAD"
            if input_mode == "worktree"
            else None
        )
        if comparison_ref is not None:
            no_manifest = (
                carries_manifest_like_yaml(
                    workspace,
                    comparison_ref,
                    protected_names=frozenset({"shipgate.yaml"}),
                )
                is False
            )
            no_yaml_removed = (
                removes_a_yaml_file(
                    workspace,
                    base if input_mode == "git_range" else None,
                    head if input_mode == "git_range" and head else "HEAD",
                )
                is False
            )
            base_manifest_absent = no_manifest and no_yaml_removed
    trigger = evaluate_trigger(
        paths=changed_files,
        diff_text=diff_text,
        manifest_present=manifest_present,
        user_requested=True,
    )
    declared = _declared_tool_surfaces_changed(
        workspace=workspace,
        manifest=manifest,
        config_path=config_path,
        changed_files=changed_files,
    )
    return evaluate_agent_boundary(
        workspace=workspace,
        diff_text=diff_text,
        actor=agent,
        policy_path=policy,
        trigger=trigger,
        manifest_present=manifest_present,
        capability_surfaces_changed=declared,
        undeclared_capability_surfaces=_undeclared_tool_surfaces_changed(
            diff_files=diff_files,
            changed_files=changed_files,
            declared=declared,
        ),
        changed_files_override=changed_files,
        input_mode=input_mode,  # type: ignore[arg-type]
        input_issues=input_issues,
        config_path=config_path,
        requested_workspace=workspace_as_given,
        base=base,
        head=head,
        verification_replayable=verification_replayable,
        base_manifest_absent=base_manifest_absent,
    )


def _lexical_config_path(
    workspace: Path,
    config: Path,
    *,
    requested_workspace: Path | None = None,
) -> Path:
    """Keep local-control manifest identity exact without breaking absence recovery."""

    requested_anchor = (
        requested_workspace.resolve()
        if requested_workspace is not None
        else workspace
    )
    candidate = config if config.is_absolute() else requested_anchor / config
    if config.is_absolute() and requested_workspace is not None:
        lexical_workspace = Path(
            os.path.abspath(os.path.normpath(os.fspath(requested_workspace)))
        )
        lexical_config = Path(os.path.normpath(os.fspath(config)))
        try:
            requested_tail = lexical_config.relative_to(lexical_workspace)
        except ValueError:
            pass
        else:
            # An invocation may enter the workspace through an external
            # symlink or filesystem alias. Map only the proven tail onto the
            # canonical workspace; repository-internal links remain visible to
            # the component inspector below and are rejected.
            candidate = requested_anchor / requested_tail
    lexical = Path(os.path.normpath(os.fspath(candidate)))
    try:
        relative = lexical.relative_to(workspace)
    except ValueError as exc:
        raise ConfigError(f"--config must be inside --workspace: {config}") from exc

    issue = inspect_lexical_path_identity(workspace, relative)
    if issue is None:
        # The shared inspector deliberately permits missing suffix components;
        # the existing unconfigured-repository preview route handles them.
        try:
            metadata = lexical.lstat()
        except FileNotFoundError:
            return lexical
        except OSError as exc:
            raise ConfigError(f"--config could not be inspected safely: {config}") from exc
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ConfigError(
                "--config must identify one singly-linked regular file; "
                f"non-regular or hardlinked manifest refused: {config}"
            )
        return lexical
    try:
        requested = issue.requested.relative_to(workspace).as_posix()
    except ValueError:
        requested = issue.requested.as_posix()
    if issue.kind == "symlink":
        raise ConfigError(
            f"--config must not contain symlink components: {requested}"
        )
    if issue.kind == "alias":
        if issue.actual is None:
            actual = "a differently spelled filesystem entry"
        else:
            try:
                actual = issue.actual.relative_to(workspace).as_posix()
            except ValueError:
                actual = issue.actual.as_posix()
        raise ConfigError(
            "--config must use the exact filesystem spelling: "
            f"{requested} resolves to {actual}"
        )
    raise ConfigError(
        f"--config could not be inspected safely: {requested}: {issue.detail}"
    )


def _undeclared_tool_surfaces_changed(
    *,
    diff_files: list[Any],
    changed_files: list[str],
    declared: list[str],
) -> list[str]:
    """Changed files that are tool/capability surfaces the manifest does NOT declare.

    ``verify`` only gates *declared* tool sources, so an undeclared surface (a
    new MCP/OpenAPI/tool-inventory/codex-plugin file, or an SDK/n8n tool the
    manifest does not list) escapes the gate even though ``check`` returns a
    clean ``allow``. Each changed file is classified per-file against only the
    *tool-source* trigger rules (so the manifest, prompts/policies, the CI gate,
    and ``.codex`` boundary config are never mislabeled), excluding boundary
    paths (the boundary evaluator already inspects those) and files the manifest
    already declares (``verify`` gates those). Computed independently of the
    declared set so a mixed diff — one declared surface plus one undeclared —
    still surfaces the undeclared one.
    """
    if not changed_files:
        return []
    declared_set = set(declared)
    added_by_path = {
        item.path: "\n".join(getattr(item, "added_lines", []) or [])
        for item in diff_files
        if item.path
    }
    catalog = load_triggers()
    undeclared: list[str] = []
    for path in changed_files:
        if (
            path in declared_set
            or is_agent_boundary_path(path)
            or _is_non_deployed_fixture_path(path)
        ):
            continue
        # Per-file classification: a glob rule matches on the path, a
        # diff_contains rule (n8n, @function_tool) on this file's added lines.
        result = evaluate_trigger(
            paths=[path],
            diff_text=added_by_path.get(path, ""),
            manifest_present=False,
            user_requested=False,
            triggers=catalog,
        )
        # Require the evaluator's WINNING verdict, not just a matched rule: a
        # docs/test file that incidentally mentions ``@tool`` also matches
        # ``TRIGGER-DOCS-ONLY-NEGATIVE``, which beats ``run_shipgate`` so the
        # catalog skips it. Only treat the file as a tool surface when the
        # trigger actually runs AND a tool-source rule is what carried it.
        if result.get("run_shipgate") and result_has_surface_class(
            result, SURFACE_CLASS_CAPABILITY
        ):
            undeclared.append(path)
    return sorted(dict.fromkeys(undeclared))


def _is_non_deployed_fixture_path(path: str) -> bool:
    """Exclude conventional test artifacts from undeclared deployment inference.

    This filter is intentionally applied only after declared surfaces have
    been computed.  A fixture explicitly named by ``shipgate.yaml`` therefore
    remains a real capability surface and is still routed through verify. Test
    directories may appear below a package root, but fixture/golden names are
    excluded only at the repository root or beneath an explicit test root so a
    production path such as ``services/fixtures`` remains in scope.
    """

    parts = PurePosixPath(path.replace("\\", "/")).parts
    lowered = tuple(part.lower() for part in parts)
    test_roots = {"test", "tests"}
    fixture_roots = {
        "__snapshots__",
        "fixture",
        "fixtures",
        "golden",
        "goldens",
    }
    if any(part in test_roots for part in lowered[:-1]):
        return True
    if lowered and lowered[0] in fixture_roots:
        return True
    return len(lowered) >= 4 and lowered[0] == "samples" and lowered[2] == "expected"


def _declared_tool_surfaces_changed(
    *,
    workspace: Path,
    manifest: AgentsShipgateManifest | None,
    config_path: Path,
    changed_files: list[str],
) -> list[str]:
    """Return changed files the manifest declares as tool sources.

    These are capability surfaces ``verify`` scans but the boundary evaluator
    does not, so a clean boundary ``allow`` over one of them must defer to
    ``verify``. Best-effort: an absent or invalid manifest yields no signal so
    the boundary check degrades to its prior behavior rather than failing.

    A declared ``tool_sources[].path`` may be a single file (``mcp``,
    ``openapi``) or a directory the loader scans recursively
    (``openai_agents_sdk``, ``google_adk``, ``langchain``, ``crewai``,
    ``codex_plugin``, ``codex_config``), so a changed file matches when it
    equals the declared path or sits under it. Valid local roots referenced by
    a declared Codex marketplace are included because ``verify`` scans those
    packages transitively. Two exclusions keep this from becoming noise: a
    declared path that resolves to the workspace root (e.g. ``codex_config``
    with ``path: .``) is dropped — it would otherwise match every file,
    including docs — and changed files the boundary evaluator already
    inspects are dropped, since ``check`` did evaluate those.
    """

    if not changed_files or manifest is None:
        return []
    manifest_dir = config_path.parent
    declared: set[str] = set()
    for source in getattr(manifest, "tool_sources", None) or []:
        path = getattr(source, "path", None)
        if not isinstance(path, str) or not path:
            continue
        try:
            rel = (manifest_dir / path).resolve().relative_to(workspace).as_posix()
        except ValueError:
            continue
        if rel in {"", "."}:  # whole-workspace root: matching all files is noise.
            continue
        declared.add(rel)
    declared.update(
        _declared_codex_marketplace_roots(
            manifest=manifest,
            manifest_dir=manifest_dir,
            workspace=workspace,
        )
    )
    if not declared:
        return []
    matched = [
        changed
        for changed in changed_files
        if not is_agent_boundary_path(changed)
        and any(changed == decl or changed.startswith(f"{decl}/") for decl in declared)
    ]
    return sorted(matched)


def _declared_codex_marketplace_roots(
    *,
    manifest: AgentsShipgateManifest,
    manifest_dir: Path,
    workspace: Path,
) -> set[str]:
    """Return contained local plugin roots reached through marketplaces."""

    roots: set[str] = set()
    for source in manifest.tool_sources:
        if (
            source.type != "codex_plugin"
            or source.mode != "marketplace"
            or not source.path
        ):
            continue
        try:
            marketplace_roots = resolve_local_codex_marketplace_roots(
                marketplace_path=manifest_dir / str(source.path),
                base_dir=manifest_dir,
            )
        except (InputParseError, OSError, RuntimeError, UnicodeError):
            continue
        for root in marketplace_roots:
            try:
                rel = root.relative_to(workspace).as_posix()
            except ValueError:
                continue
            if rel not in {"", "."}:
                roots.add(rel)
    return roots


def git_boundary_change_set(
    *,
    workspace: Path,
    base: str | None,
    head: str | None,
    config_path: Path | None = None,
) -> BoundaryChangeSet:
    """Collect the diff ``check`` evaluates.

    ``config_path`` is threaded so an untracked manifest under a non-default
    name reaches the evaluator at all: the untracked-content filter below only
    admits recognized boundary or capability paths, so a repository run with
    ``--config new-gate.yml`` produced an empty change set and ``allow``.
    """

    requested_workspace = workspace
    repository_root = git_root_for(workspace)
    workspace = repository_root or workspace.resolve()
    if config_path is not None:
        config_path = _lexical_config_path(
            workspace,
            config_path,
            requested_workspace=requested_workspace,
        )
    if bool(base) != bool(head):
        raise RuntimeError(
            "--base and --head must be provided together; omit both to check "
            "local uncommitted changes."
        )
    if base and head:
        revspec = f"{base}...{head}"
    else:
        revspec = ""
    if repository_root is None:
        raise RuntimeError("Workspace is not inside a Git repository.")
    try:
        changed_paths, diff_text = _git_diff_context(revspec, cwd=workspace)
    except BinaryCapabilityDiffError as exc:
        return BoundaryChangeSet(
            mode="git_range" if revspec else "worktree",
            scope="repository",
            completeness="partial",
            diff_text=exc.diff_text,
            changed_paths=exc.changed_paths,
            issues=tuple(
                BoundaryInputIssue(
                    code="boundary_input_binary",
                    path=path,
                    message=(
                        "A recognized tracked boundary or capability source is "
                        "binary and cannot be evaluated statically."
                    ),
                )
                for path in exc.paths
            ),
        )
    except ConfigError as exc:
        # Preserve trust-root/index-identity failures so ``shipgate check``
        # keeps the typed exit-2 contract instead of degrading them into the
        # generic diff-unavailable recovery path (which exits successfully).
        if (
            "Git index flags hide paths from worktree collection" in str(exc)
            or "refuses Git filter attributes" in str(exc)
            or "repository-local diff" in str(exc)
        ):
            raise
        raise RuntimeError(str(exc) or "git diff failed") from exc
    except Exception as exc:  # noqa: BLE001 - normalize git probe failures for CLI.
        raise RuntimeError(str(exc) or "git diff failed") from exc
    changed_paths = sorted(
        {
            *changed_paths,
            *(
                path
                for item in parse_unified_diff(diff_text)
                for path in (item.new_path, item.old_path)
                if path
            ),
        }
    )
    issues: list[BoundaryInputIssue] = []
    manifest_text_snapshot: str | None = None
    if revspec and config_path is not None:
        try:
            relative = config_path.relative_to(workspace)
            manifest_text_snapshot = read_file_at_ref(workspace, head, relative)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ConfigError(
                f"Configured manifest could not be read from head ref {head!r}: {exc}"
            ) from exc
    elif not revspec:
        changed_paths, manifest_text_snapshot = _bind_worktree_config_to_head(
            workspace=workspace,
            config_path=config_path,
            changed_paths=changed_paths,
        )
        diff_text, issues = _append_untracked_diffs(
            workspace=workspace,
            changed_paths=changed_paths,
            diff_text=diff_text,
            config_path=config_path,
        )
    return BoundaryChangeSet(
        mode="git_range" if revspec else "worktree",
        scope="repository",
        completeness="partial" if issues else "complete",
        diff_text=diff_text,
        changed_paths=tuple(sorted(changed_paths)),
        issues=tuple(issues),
        manifest_text_snapshot=manifest_text_snapshot,
    )


def _bind_worktree_config_to_head(
    *,
    workspace: Path,
    config_path: Path | None,
    changed_paths: list[str],
) -> tuple[list[str], str | None]:
    """Keep ignored/index-hidden manifest bytes inside local check context."""

    if config_path is None:
        return changed_paths, None
    try:
        relative = config_path.relative_to(workspace)
    except ValueError as exc:
        raise ConfigError(
            f"--config must be inside --workspace: {config_path}"
        ) from exc
    try:
        worktree_text = read_identity_bound_text(
            workspace,
            relative,
            max_bytes=_MAX_MANIFEST_BYTES,
        )
        head_text = read_file_at_ref(workspace, "HEAD", relative)
    except FileNotFoundError:
        return changed_paths, None
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ConfigError(
            f"Configured manifest {relative.as_posix()} could not be bound "
            f"to HEAD: {exc}"
        ) from exc
    if worktree_text == head_text:
        return changed_paths, worktree_text
    return sorted({*changed_paths, relative.as_posix()}), worktree_text


def agent_result_json_payload(result: AgentResultV2) -> dict[str, Any]:
    payload = result.model_dump(mode="json", exclude_none=True)
    # ``shipgate.codex_boundary_result/v2`` is a frozen deprecated projection:
    # its published schema forbids unknown properties and knows only the three
    # pre-contract-20 states. Emit the legacy control shape there so the
    # promise holds, exactly as ``pending_review[]`` stays off this format.
    payload["control"] = (
        project_legacy_agent_control(result.control)
        if result.schema_version == CODEX_BOUNDARY_RESULT_SCHEMA_VERSION
        else result.control.model_dump(mode="json")
    )
    return payload


def agent_result_json(result: AgentResultV2) -> str:
    return json.dumps(agent_result_json_payload(result), indent=2, sort_keys=False)


_MAX_BOUNDARY_FILE_BYTES = 128 * 1024
_MAX_UNTRACKED_BYTES = 1024 * 1024
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024


def _read_manifest_snapshot(workspace: Path, config_path: Path) -> str | None:
    try:
        relative = config_path.relative_to(workspace)
    except ValueError as exc:
        raise ConfigError(
            f"--config must be inside --workspace: {config_path}"
        ) from exc
    try:
        return read_identity_bound_text(
            workspace,
            relative,
            max_bytes=_MAX_MANIFEST_BYTES,
        )
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ConfigError(
            f"Configured manifest {relative.as_posix()} could not be read "
            f"through an identity-bound snapshot: {exc}"
        ) from exc


def _append_untracked_diffs(
    *,
    workspace: Path,
    changed_paths: list[str],
    diff_text: str,
    config_path: Path | None = None,
) -> tuple[str, list[BoundaryInputIssue]]:
    represented = {
        path
        for item in parse_unified_diff(diff_text)
        for path in (item.new_path, item.old_path)
        if path
    }
    appended: list[str] = []
    issues: list[BoundaryInputIssue] = []
    consumed = 0
    for path in sorted(set(changed_paths) - represented):
        if not _potential_boundary_or_capability_path(
            path,
            config_path,
            workspace=workspace,
        ):
            continue
        if any(marker in path for marker in ("\x00", "\n", "\r")):
            issues.append(
                BoundaryInputIssue(
                    code="boundary_path_not_diff_representable",
                    path=path,
                    message=(
                        "A recognized boundary path contains a control character "
                        "that cannot be represented safely in a unified diff."
                    ),
                )
            )
            continue
        text: str | None = None
        issue_code: str | None = None
        raw, read_issue = _read_exact_untracked_file(
            workspace,
            Path(path),
            max_bytes=min(
                _MAX_BOUNDARY_FILE_BYTES,
                _MAX_UNTRACKED_BYTES - consumed,
            ),
        )
        if read_issue is not None:
            issue_code = read_issue
        elif raw is not None:
            if b"\x00" in raw:
                issue_code = "boundary_input_binary"
            else:
                try:
                    text = raw.decode("utf-8")
                    consumed += len(raw)
                except UnicodeDecodeError:
                    issue_code = "boundary_input_non_utf8"
        if issue_code:
            issues.append(
                BoundaryInputIssue(
                    code=issue_code,
                    path=path,
                    message=(
                        "A recognized untracked boundary or capability source could "
                        "not be evaluated safely."
                    ),
                )
            )
            appended.append(_new_file_diff(path, ""))
            continue
        assert text is not None
        if not _is_relevant_untracked(
            path,
            text,
            config_path,
            workspace=workspace,
        ):
            continue
        appended.append(_new_file_diff(path, text))
    if not appended:
        return diff_text, issues
    return (
        "\n".join([diff_text.rstrip("\n"), *appended]).lstrip("\n") + "\n",
        issues,
    )


def _read_exact_untracked_file(
    workspace: Path,
    relative: Path,
    *,
    max_bytes: int,
) -> tuple[bytes | None, str | None]:
    """Read one untracked source through a stable, non-aliased descriptor."""

    if relative.is_absolute() or ".." in relative.parts or max_bytes < 0:
        return None, "boundary_input_symlink_or_external"
    issue = inspect_lexical_path_identity(workspace, relative)
    if issue is not None:
        return None, "boundary_input_symlink_or_external"
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptors: list[int] = []
    try:
        if os.open in os.supports_dir_fd:
            directory_flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            current = os.open(workspace, directory_flags)
            descriptors.append(current)
            for component in relative.parts[:-1]:
                current = os.open(component, directory_flags, dir_fd=current)
                descriptors.append(current)
            descriptor = os.open(relative.name, flags, dir_fd=current)
        else:
            descriptor = os.open(workspace / relative, flags)
        descriptors.append(descriptor)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            return None, "boundary_input_symlink_or_external"
        if opened.st_size > max_bytes:
            return None, "boundary_input_oversized"
        raw = bytearray()
        while chunk := os.read(descriptor, min(64 * 1024, max_bytes + 1 - len(raw))):
            raw.extend(chunk)
            if len(raw) > max_bytes:
                return None, "boundary_input_oversized"
        after = os.fstat(descriptor)
    except (OSError, NotImplementedError):
        return None, "boundary_input_unreadable"
    finally:
        for descriptor_to_close in reversed(descriptors):
            try:
                os.close(descriptor_to_close)
            except OSError:
                pass
    try:
        current_metadata = (workspace / relative).lstat()
    except OSError:
        return None, "boundary_input_unreadable"
    if (
        _stat_identity(opened) != _stat_identity(after)
        or _stat_identity(opened) != _stat_identity(current_metadata)
        or inspect_lexical_path_identity(workspace, relative) is not None
    ):
        return None, "boundary_input_unreadable"
    return bytes(raw), None


def _stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _new_file_diff(path: str, text: str) -> str:
    lines = text.splitlines()
    body = "\n".join(f"+{line}" for line in lines)
    hunk = f"@@ -0,0 +1,{len(lines)} @@\n{body}\n" if lines else "@@ -0,0 +0,0 @@\n"
    return (
        "diff --git "
        f"{git_diff_path_token('a/', path)} {git_diff_path_token('b/', path)}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        f"+++ {git_diff_path_token('b/', path)}\n"
        f"{hunk}"
    )


def _potential_boundary_or_capability_path(
    path: str,
    config_path: Path | None = None,
    *,
    workspace: Path | None = None,
) -> bool:
    if (
        is_agent_boundary_path(path)
        or trust_root_class_for(path) is not None
        or is_configured_manifest(
            config_path,
            path,
            workspace=workspace,
        )
    ):
        return True
    result = evaluate_trigger(
        paths=[path],
        diff_text="",
        manifest_present=False,
        user_requested=False,
    )
    return result_has_surface_class(result, SURFACE_CLASS_CAPABILITY) or path.endswith(".py")


def _is_relevant_untracked(
    path: str,
    text: str,
    config_path: Path | None = None,
    *,
    workspace: Path | None = None,
) -> bool:
    if (
        is_agent_boundary_path(path)
        or trust_root_class_for(path) is not None
        or is_configured_manifest(
            config_path,
            path,
            workspace=workspace,
        )
    ):
        return True
    result = evaluate_trigger(
        paths=[path],
        diff_text=text,
        manifest_present=False,
        user_requested=False,
    )
    return bool(result.get("run_shipgate")) and result_has_surface_class(
        result, SURFACE_CLASS_CAPABILITY
    )
