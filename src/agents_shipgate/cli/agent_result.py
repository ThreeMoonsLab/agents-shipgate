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
from agents_shipgate.schemas.agent_result_v1 import AgentResultV1
from agents_shipgate.triggers import _git_diff_context
from agents_shipgate.triggers import evaluate as evaluate_trigger


def build_codex_agent_result(
    *,
    agent: str = "codex",
    workspace: Path,
    diff_text: str,
    config: Path,
    policy: Path | None,
) -> AgentResultV1:
    workspace = workspace.resolve()
    changed_files = sorted({item.path for item in parse_unified_diff(diff_text) if item.path})
    config_path = config if config.is_absolute() else workspace / config
    trigger = evaluate_trigger(
        paths=changed_files,
        diff_text=diff_text,
        manifest_present=config_path.is_file(),
        user_requested=True,
    )
    return evaluate_codex_boundary_result(
        workspace=workspace,
        diff_text=diff_text,
        agent=agent,
        policy_path=policy,
        trigger=trigger,
        capability_surfaces_changed=_declared_tool_surfaces_changed(
            workspace=workspace,
            config_path=config_path,
            changed_files=changed_files,
        ),
    )


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


def agent_result_json_payload(result: AgentResultV1) -> dict[str, Any]:
    return result.model_dump(mode="json", exclude_none=True)


def agent_result_json(result: AgentResultV1) -> str:
    return json.dumps(agent_result_json_payload(result), indent=2, sort_keys=False)
