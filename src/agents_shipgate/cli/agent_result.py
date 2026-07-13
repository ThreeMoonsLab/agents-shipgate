from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.agent_boundary import (
    build_agent_boundary_result as project_agent_boundary_result,
)
from agents_shipgate.core.agent_boundary import (
    evaluate_agent_boundary,
)
from agents_shipgate.core.boundary_diff import BoundaryChangeSet, BoundaryInputIssue
from agents_shipgate.core.boundary_registry import is_agent_boundary_path
from agents_shipgate.core.codex_boundary import parse_unified_diff
from agents_shipgate.schemas.agent_boundary import AgentBoundaryResultV1
from agents_shipgate.schemas.agent_result import AgentResultV2
from agents_shipgate.schemas.codex_boundary_result import CodexBoundaryResultV2
from agents_shipgate.triggers import (
    SURFACE_CLASS_CAPABILITY,
    _git_diff_context,
    load_triggers,
    result_has_surface_class,
)
from agents_shipgate.triggers import evaluate as evaluate_trigger


def build_codex_agent_result(
    *,
    agent: str = "codex",
    workspace: Path,
    diff_text: str,
    config: Path,
    policy: Path | None,
    input_issues: list[BoundaryInputIssue] | None = None,
) -> CodexBoundaryResultV2:
    """Frozen v2 compatibility projection from the central assessment."""

    return _assessment_for_diff(
        agent=agent,
        workspace=workspace,
        diff_text=diff_text,
        config=config,
        policy=policy,
        input_mode="provided_diff",
        input_issues=input_issues,
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
) -> AgentBoundaryResultV1:
    assessment = _assessment_for_diff(
        agent=agent,
        workspace=workspace,
        diff_text=diff_text,
        config=config,
        policy=policy,
        input_mode=input_mode,
        input_issues=input_issues,
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
):
    workspace = workspace.resolve()
    diff_files = parse_unified_diff(diff_text)
    changed_files = sorted({item.path for item in diff_files if item.path})
    config_path = config if config.is_absolute() else workspace / config
    manifest_present = config_path.is_file()
    trigger = evaluate_trigger(
        paths=changed_files,
        diff_text=diff_text,
        manifest_present=manifest_present,
        user_requested=True,
    )
    declared = _declared_tool_surfaces_changed(
        workspace=workspace,
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
        input_mode=input_mode,  # type: ignore[arg-type]
        input_issues=input_issues,
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
        if path in declared_set or is_agent_boundary_path(path):
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


def _declared_tool_surfaces_changed(
    *,
    workspace: Path,
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
    equals the declared path or sits under it. Two exclusions keep this from
    becoming noise: a declared path that resolves to the workspace root (e.g.
    ``codex_config`` with ``path: .``) is dropped — it would otherwise match
    every file, including docs — and changed files the boundary evaluator
    already inspects are dropped, since ``check`` did evaluate those.
    """

    if not changed_files or not config_path.is_file():
        return []
    try:
        manifest = load_manifest(config_path)
    except Exception:  # noqa: BLE001 - enrichment must never break the check.
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
    if not declared:
        return []
    matched = [
        changed
        for changed in changed_files
        if not is_agent_boundary_path(changed)
        and any(changed == decl or changed.startswith(f"{decl}/") for decl in declared)
    ]
    return sorted(matched)


def git_boundary_change_set(
    *,
    workspace: Path,
    base: str | None,
    head: str | None,
) -> BoundaryChangeSet:
    workspace = workspace.resolve()
    if bool(base) != bool(head):
        raise RuntimeError(
            "--base and --head must be provided together; omit both to check "
            "local uncommitted changes."
        )
    if base and head:
        revspec = f"{base}...{head}"
    else:
        revspec = ""
    try:
        changed_paths, diff_text = _git_diff_context(revspec, cwd=workspace)
    except Exception as exc:  # noqa: BLE001 - normalize git probe failures for CLI.
        raise RuntimeError(str(exc) or "git diff failed") from exc
    issues: list[BoundaryInputIssue] = []
    if not revspec:
        diff_text, issues = _append_untracked_diffs(
            workspace=workspace,
            changed_paths=changed_paths,
            diff_text=diff_text,
        )
    return BoundaryChangeSet(
        mode="git_range" if revspec else "worktree",
        scope="repository",
        completeness="partial" if issues else "complete",
        diff_text=diff_text,
        changed_paths=tuple(sorted(changed_paths)),
        issues=tuple(issues),
    )


def agent_result_json_payload(result: AgentResultV2) -> dict[str, Any]:
    payload = result.model_dump(mode="json", exclude_none=True)
    payload["control"] = result.control.model_dump(mode="json")
    return payload


def agent_result_json(result: AgentResultV2) -> str:
    return json.dumps(agent_result_json_payload(result), indent=2, sort_keys=False)


_MAX_BOUNDARY_FILE_BYTES = 128 * 1024
_MAX_UNTRACKED_BYTES = 1024 * 1024


def _append_untracked_diffs(
    *,
    workspace: Path,
    changed_paths: list[str],
    diff_text: str,
) -> tuple[str, list[BoundaryInputIssue]]:
    represented = {item.path for item in parse_unified_diff(diff_text) if item.path}
    appended: list[str] = []
    issues: list[BoundaryInputIssue] = []
    consumed = 0
    for path in sorted(set(changed_paths) - represented):
        if not _potential_boundary_or_capability_path(path):
            continue
        candidate = workspace / path
        text: str | None = None
        issue_code: str | None = None
        if not _safe_untracked_file(workspace, candidate):
            issue_code = "boundary_input_symlink_or_external"
        else:
            try:
                size = candidate.stat().st_size
                if size > _MAX_BOUNDARY_FILE_BYTES or consumed + size > _MAX_UNTRACKED_BYTES:
                    issue_code = "boundary_input_oversized"
                else:
                    raw = candidate.read_bytes()
                    if b"\x00" in raw:
                        issue_code = "boundary_input_binary"
                    else:
                        text = raw.decode("utf-8")
                        consumed += size
            except UnicodeDecodeError:
                issue_code = "boundary_input_non_utf8"
            except OSError:
                issue_code = "boundary_input_unreadable"
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
        if not _is_relevant_untracked(path, text):
            continue
        appended.append(_new_file_diff(path, text))
    if not appended:
        return diff_text, issues
    return (
        "\n".join([diff_text.rstrip("\n"), *appended]).lstrip("\n") + "\n",
        issues,
    )


def _safe_untracked_file(workspace: Path, candidate: Path) -> bool:
    try:
        relative = candidate.relative_to(workspace)
    except ValueError:
        return False
    current = workspace
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return False
    try:
        candidate.resolve().relative_to(workspace)
    except (OSError, ValueError):
        return False
    return candidate.is_file()


def _new_file_diff(path: str, text: str) -> str:
    lines = text.splitlines()
    body = "\n".join(f"+{line}" for line in lines)
    hunk = f"@@ -0,0 +1,{len(lines)} @@\n{body}\n" if lines else "@@ -0,0 +0,0 @@\n"
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        f"{hunk}"
    )


def _potential_boundary_or_capability_path(path: str) -> bool:
    if is_agent_boundary_path(path):
        return True
    result = evaluate_trigger(
        paths=[path],
        diff_text="",
        manifest_present=False,
        user_requested=False,
    )
    return result_has_surface_class(result, SURFACE_CLASS_CAPABILITY) or path.endswith(".py")


def _is_relevant_untracked(path: str, text: str) -> bool:
    if is_agent_boundary_path(path):
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
