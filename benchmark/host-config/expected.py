"""Expected changes and directions for #659 host-config PRs, from the files and host docs.

Extends `benchmark/cold-start/expected.py` (Claude settings, `.mcp.json`,
`.cursor/mcp.json`) with Codex `config.toml`, VS Code `mcp.json` and GitHub
workflow permissions, and adds a direction to every change. Nothing here reads
`agents_shipgate`: an oracle derived from the engine's own reader would score a
key the engine skips as a correct silence.

Direction is `widening`, `narrowing` or `changed`. `changed` means documented
semantics do not settle whether authority grew (an MCP server's command
changed); the scorer counts it as a change without a direction claim.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml

_COLD = Path(__file__).resolve().parent.parent / "cold-start" / "expected.py"
spec = importlib.util.spec_from_file_location("cold_start_expected", _COLD)
cold = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(cold)

_RANK = {"none": 0, "read": 1, "write": 2}
_WIDER_MODES = {"acceptEdits", "bypassPermissions", "auto"}


def _direct(change: dict[str, Any]) -> dict[str, Any]:
    kind, key, direction = change["kind"], change["key"], change["direction"]
    before, after = change.get("before"), change.get("after")
    verdict = "changed"
    if kind == "permission_rule":
        disposition = key.split(":", 1)[0]
        grows = (disposition == "allow") == (direction == "added")
        verdict = "widening" if grows else "narrowing"
    elif kind in {"additional_path", "mcp_server", "hook", "plugin"} and direction in {"added", "removed"}:
        verdict = "widening" if direction == "added" else "narrowing"
    elif kind == "plugin" and direction == "changed":
        verdict = "widening" if after is True else "narrowing" if after is False else "changed"
    elif kind == "setting":
        if key == "permissions.defaultMode":
            if after in _WIDER_MODES and before not in _WIDER_MODES:
                verdict = "widening"
            elif before in _WIDER_MODES and after not in _WIDER_MODES:
                verdict = "narrowing"
        elif key == "enableAllProjectMcpServers":
            verdict = "widening" if after is True else "narrowing" if before is True else "changed"
        elif key == "sandbox.enabled":
            verdict = "narrowing" if after is True else "widening" if before is True else "changed"
    return {**change, "semantic_direction": verdict}


CODEX_SUPPORTED = frozenset({
    # docs/host-boundary-support.md, Codex: "sandbox, approvals, network, MCP/app approvals, hooks".
    "approval_policy", "sandbox_mode", "sandbox_workspace_write", "network_access",
    "web_search", "mcp_servers", "apps", "profile", "profiles",
})
CODEX_UNSUPPORTED_CAPABILITY = frozenset({
    "shell_environment_policy", "notify", "model_providers", "projects", "tools",
    "trust_level", "experimental_use_exec_command_tool", "features",
})
CODEX_BENIGN = frozenset({
    "model", "model_provider", "model_reasoning_effort", "model_reasoning_summary",
    "model_verbosity", "model_context_window", "model_max_output_tokens", "hide_agent_reasoning",
    "show_raw_agent_reasoning", "file_opener", "tui", "history", "disable_response_storage",
    "project_doc_max_bytes", "preferred_auth_method", "instructions",
})


def codex_config(before_text: str | None, after_text: str | None) -> dict[str, Any]:
    def parse(text):
        if text is None:
            return {}, True
        try:
            return tomllib.loads(text), True
        except tomllib.TOMLDecodeError:
            return None, False
    before, bok = parse(before_text)
    after, aok = parse(after_text)
    if not (bok and aok):
        return {"scope": "refusal_expected", "changes": [], "unclassified_keys": []}
    changes, scope, unclassified = [], "supported", []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        if key in {"mcp_servers", "apps", "profiles"}:
            changes += cold._map_changes({"mcp_servers": "mcp_server", "apps": "plugin", "profiles": "setting"}[key],
                                         "" if key != "profiles" else "profiles.", old, new)
        elif key == "sandbox_workspace_write":
            changes += cold._map_changes("setting", "sandbox_workspace_write.", old, new)
        elif key in CODEX_SUPPORTED:
            change = cold._change("setting", key, old, new)
            if change:
                verdict = "changed"
                if key == "approval_policy":
                    verdict = "widening" if new == "never" else "narrowing" if old == "never" else "changed"
                elif key == "sandbox_mode":
                    order = {"read-only": 0, "workspace-write": 1, "danger-full-access": 2}
                    if old in order and new in order:
                        verdict = "widening" if order[new] > order[old] else "narrowing"
                elif key in {"network_access", "web_search"}:
                    verdict = "widening" if new in (True, "enabled", "live") else "narrowing" if old in (True, "enabled", "live") else "changed"
                changes.append({**change, "semantic_direction": verdict})
        elif key in CODEX_UNSUPPORTED_CAPABILITY:
            scope = "unsupported"
            changes.append({"kind": "unsupported_setting", "key": key, "direction": "changed", "before": old, "after": new})
        elif key in CODEX_BENIGN:
            continue
        else:
            unclassified.append(key)
    if unclassified and scope == "supported":
        scope = "unclassified"
    return {"scope": scope, "changes": [c if "semantic_direction" in c else _direct(c) for c in changes], "unclassified_keys": unclassified}


def vscode_mcp(before_text: str | None, after_text: str | None) -> dict[str, Any]:
    before, bok = cold._parse(before_text)
    after, aok = cold._parse(after_text)
    if not (bok and aok):
        return {"scope": "refusal_expected", "changes": [], "unclassified_keys": []}
    changes = [_direct(c) for c in cold._map_changes("mcp_server", "", before.get("servers"), after.get("servers"))]
    unclassified = sorted(k for k in set(before) | set(after) if k not in {"servers", "inputs", "$schema"} and before.get(k) != after.get(k))
    return {"scope": "unclassified" if unclassified else "supported", "changes": changes, "unclassified_keys": unclassified}


_MISSING = object()


def _permission_map(value: Any) -> dict[str, str] | str:
    if value is _MISSING or value is None:
        return "repository_default"
    if value == "read-all":
        return {"*": "read"}
    if value == "write-all":
        return {"*": "write"}
    if value == {}:
        return {}
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    return "unparsed"


def workflow(before_text: str | None, after_text: str | None) -> dict[str, Any]:
    def parse(text):
        if text is None:
            return {}, True
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError:
            return None, False
        return (doc if isinstance(doc, dict) else None), isinstance(doc, dict)
    before, bok = parse(before_text)
    after, aok = parse(after_text)
    if not (bok and aok):
        return {"scope": "refusal_expected", "changes": [], "unclassified_keys": []}

    def triggers(doc):
        on = doc.get("on", doc.get(True))
        if isinstance(on, str):
            return {on}
        if isinstance(on, list):
            return {str(item) for item in on}
        if isinstance(on, dict):
            return {str(item) for item in on}
        return set()

    def effective(doc):
        top = doc.get("permissions", _MISSING)
        jobs = doc.get("jobs") if isinstance(doc.get("jobs"), dict) else {}
        return {name: _permission_map(job.get("permissions", top) if isinstance(job, dict) else top) for name, job in jobs.items()}

    changes = []
    old_jobs, new_jobs = effective(before), effective(after)
    for job in sorted(set(old_jobs) | set(new_jobs)):
        old, new = old_jobs.get(job, "absent"), new_jobs.get(job, "absent")
        if old == new:
            continue
        if isinstance(old, dict) and isinstance(new, dict):
            for scope_name in sorted(set(old) | set(new)):
                a, b = old.get(scope_name, old.get("*", "none")), new.get(scope_name, new.get("*", "none"))
                if a == b:
                    continue
                ra, rb = _RANK.get(a), _RANK.get(b)
                verdict = "changed" if ra is None or rb is None else "widening" if rb > ra else "narrowing"
                changes.append({"kind": "workflow_permission", "key": f"{job}:{scope_name}", "direction": "changed", "before": a, "after": b, "semantic_direction": verdict})
        else:
            verdict = "changed"
            if old == "absent" and isinstance(new, dict):
                verdict = "widening" if any(_RANK.get(v, 0) > 0 for v in new.values()) else "changed"
            elif new == "absent":
                verdict = "narrowing"
            changes.append({"kind": "workflow_permission", "key": f"{job}:*", "direction": "changed", "before": str(old), "after": str(new), "semantic_direction": verdict})
    if "pull_request_target" in triggers(after) - triggers(before):
        changes.append({"kind": "workflow_trigger", "key": "pull_request_target", "direction": "added", "before": None, "after": "pull_request_target", "semantic_direction": "widening"})
    return {"scope": "supported", "changes": changes, "unclassified_keys": []}


def expected(kind: str, before_text: str | None, after_text: str | None) -> dict[str, Any]:
    if kind in {".claude/settings.json", ".mcp.json", ".cursor/mcp.json"}:
        result = cold.expected(kind, before_text, after_text)
        return {**result, "changes": [_direct(c) for c in result["changes"]]}
    if kind == ".codex/config.toml":
        return codex_config(before_text, after_text)
    if kind == ".vscode/mcp.json":
        return vscode_mcp(before_text, after_text)
    if kind == ".github/workflows/":
        return workflow(before_text, after_text)
    raise ValueError(kind)


if __name__ == "__main__":
    print(json.dumps(expected(sys.argv[1], Path(sys.argv[2]).read_text() if sys.argv[2] != "-" else None,
                              Path(sys.argv[3]).read_text() if sys.argv[3] != "-" else None), indent=2))
