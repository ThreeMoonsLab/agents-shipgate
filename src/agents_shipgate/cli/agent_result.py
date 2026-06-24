from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.codex_boundary import (
    evaluate_codex_boundary_result,
    is_boundary_path,
    parse_unified_diff,
)
from agents_shipgate.schemas.codex_boundary_result import CodexBoundaryResultV1
from agents_shipgate.triggers import _git_diff_context, load_triggers
from agents_shipgate.triggers import evaluate as evaluate_trigger

# Trigger rule IDs that mark a changed file as a *tool/capability source* —
# the surfaces ``verify`` compiles into the capability delta. Deliberately
# EXCLUDES the other ``run_shipgate`` rules that are not declarable tool
# sources: ``TRIGGER-SHIPGATE-MANIFEST`` (the manifest itself),
# ``TRIGGER-PROMPTS-OR-POLICIES`` (prompts/policies), ``TRIGGER-SHIPGATE-CI-
# WORKFLOW`` (the gate), and ``TRIGGER-CODEX-BOUNDARY-CONFIG-CHANGED`` (which
# the boundary check itself evaluates). A change to one of those must not be
# mislabeled an "undeclared tool surface".
_TOOL_SOURCE_TRIGGER_IDS = frozenset(
    {
        "TRIGGER-MCP-EXPORT-CHANGED",
        "TRIGGER-OPENAPI-SPEC-CHANGED",
        "TRIGGER-STATIC-TOOL-INVENTORY-CHANGED",
        "TRIGGER-CODEX-PLUGIN-CHANGED",
        "TRIGGER-N8N-WORKFLOW-CHANGED",
        "TRIGGER-FUNCTION-TOOL-DECORATOR",
    }
)


def build_codex_agent_result(
    *,
    agent: str = "codex",
    workspace: Path,
    diff_text: str,
    config: Path,
    policy: Path | None,
) -> CodexBoundaryResultV1:
    workspace = workspace.resolve()
    diff_files = parse_unified_diff(diff_text)
    changed_files = sorted({item.path for item in diff_files if item.path})
    config_path = config if config.is_absolute() else workspace / config
    trigger = evaluate_trigger(
        paths=changed_files,
        diff_text=diff_text,
        manifest_present=config_path.is_file(),
        user_requested=True,
    )
    declared = _declared_tool_surfaces_changed(
        workspace=workspace,
        config_path=config_path,
        changed_files=changed_files,
    )
    return evaluate_codex_boundary_result(
        workspace=workspace,
        diff_text=diff_text,
        agent=agent,
        policy_path=policy,
        trigger=trigger,
        capability_surfaces_changed=declared,
        undeclared_capability_surfaces=_undeclared_tool_surfaces_changed(
            diff_files=diff_files,
            changed_files=changed_files,
            declared=declared,
        ),
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
        if path in declared_set or is_boundary_path(path):
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
        if result.get("run_shipgate") and any(
            rule.get("id") in _TOOL_SOURCE_TRIGGER_IDS
            for rule in result.get("matched_rules", [])
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
        if not is_boundary_path(changed)
        and any(changed == decl or changed.startswith(f"{decl}/") for decl in declared)
    ]
    return sorted(matched)


def git_diff_text(
    *,
    workspace: Path,
    base: str | None,
    head: str | None,
) -> str:
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
        _, diff_text = _git_diff_context(revspec, cwd=workspace)
    except Exception as exc:  # noqa: BLE001 - normalize git probe failures for CLI.
        raise RuntimeError(str(exc) or "git diff failed") from exc
    return diff_text


def agent_result_json_payload(result: CodexBoundaryResultV1) -> dict[str, Any]:
    return result.model_dump(mode="json", exclude_none=True)


def agent_result_json(result: CodexBoundaryResultV1) -> str:
    return json.dumps(agent_result_json_payload(result), indent=2, sort_keys=False)
