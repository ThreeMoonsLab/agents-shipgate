from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_shipgate.core.codex_boundary import (
    evaluate_codex_boundary_result,
    parse_unified_diff,
)
from agents_shipgate.schemas.agent_result_v1 import AgentResultV1
from agents_shipgate.triggers import evaluate as evaluate_trigger


def build_codex_agent_result(
    *,
    workspace: Path,
    diff_text: str,
    config: Path,
    policy: Path,
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
        policy_path=policy,
        trigger=trigger,
    )


def agent_result_json_payload(result: AgentResultV1) -> dict[str, Any]:
    return result.model_dump(mode="json", exclude_none=True)


def agent_result_json(result: AgentResultV1) -> str:
    return json.dumps(agent_result_json_payload(result), indent=2, sort_keys=False)
