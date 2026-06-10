from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_shipgate.core.codex_boundary import (
    evaluate_codex_boundary_result,
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
    )


def git_diff_text(
    *,
    workspace: Path,
    base: str | None,
    head: str | None,
) -> str:
    workspace = workspace.resolve()
    if base and head:
        revspec = f"{base}...{head}"
    elif base:
        revspec = base
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
