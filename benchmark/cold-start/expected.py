"""Expected host-capability changes for one cold-start case, from the files alone (#660).

Derived from the configuration each host documents — Claude Code's settings
reference and MCP documentation, Cursor's MCP documentation — and deliberately
never from ``agents_shipgate``. Deriving the expectation from the engine's own
reader would make every key the engine does not read look like a correct
silence, which is the failure this harness exists to catch (the census in
``census.py`` makes the same argument for paths).

A case's expectation is fixed before the candidate runs:

* ``changes`` — every capability change the two file versions differ by;
* ``scope`` — ``supported`` when every change is of a kind the published
  support matrix claims, ``unsupported`` when a documented capability key
  outside that matrix changed, ``refusal_expected`` when a side does not parse;
* ``unclassified_keys`` — top-level keys this module cannot place. They make the
  scope ``unclassified`` rather than being silently treated as benign.
"""

from __future__ import annotations

import json
from typing import Any

#: Claude Code settings keys that change what the agent may do or what runs,
#: per code.claude.com/docs/en/settings-reference. Split by whether
#: docs/host-boundary-support.md claims the kind as supported: "permission
#: modes/rules, sandbox/network, additional paths, MCP restrictions, plugins,
#: hooks" for Claude Code.
CLAUDE_SUPPORTED_KEYS = frozenset({
    "permissions", "hooks", "sandbox", "enabledPlugins", "additionalDirectories",
    "disableAllHooks", "allowManagedHooksOnly", "allowManagedPermissionRulesOnly",
    "skipDangerousModePermissionPrompt", "enableAllProjectMcpServers",
    # "MCP restrictions" in the matrix: which project `.mcp.json` servers load.
    "enabledMcpjsonServers", "disabledMcpjsonServers",
})
CLAUDE_UNSUPPORTED_CAPABILITY_KEYS = frozenset({
    "apiKeyHelper", "awsAuthRefresh", "awsCredentialExport", "otelHeadersHelper",
    "statusLine", "fileSuggestion", "subagentStatusLine", "env",
    "extraKnownMarketplaces",
    "forceLoginMethod", "forceLoginOrgUUID",
})
#: Keys with no capability effect: presentation, model choice, housekeeping.
CLAUDE_BENIGN_KEYS = frozenset({
    "$schema", "model", "fallbackModel", "availableModels", "theme", "outputStyle",
    "cleanupPeriodDays", "includeCoAuthoredBy", "includeGitInstructions", "attribution",
    "spinnerTipsEnabled", "spinnerVerbs", "alwaysThinkingEnabled", "effortLevel",
    "language", "autoUpdates", "autoUpdatesChannel", "companyAnnouncements",
    "prefersReducedMotion", "verbose", "respectGitignore", "feedbackSurveyState",
    "autoCompactEnabled", "showTurnDuration", "terminalProgressBarEnabled",
    "plansDirectory",
})
PERMISSION_LISTS = ("allow", "ask", "deny")
PERMISSION_SCALARS = frozenset({"defaultMode", "disableBypassPermissionsMode"})


def _parse(text: str | None) -> tuple[Any, bool]:
    """(document, parsed). An absent file is an empty document, not an error."""

    if text is None:
        return {}, True
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        return None, False
    return (document if isinstance(document, dict) else None), isinstance(document, dict)


def _change(kind: str, key: str, before: Any, after: Any) -> dict[str, Any] | None:
    if before == after:
        return None
    direction = "added" if before is None else "removed" if after is None else "changed"
    return {"kind": kind, "key": key, "direction": direction, "before": before, "after": after}


def _map_changes(kind: str, prefix: str, before: Any, after: Any) -> list[dict[str, Any]]:
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    return [
        change
        for name in sorted(set(before) | set(after))
        if (change := _change(kind, f"{prefix}{name}", before.get(name), after.get(name)))
    ]


def _list_changes(kind: str, prefix: str, before: Any, after: Any) -> list[dict[str, Any]]:
    before_items = {str(item) for item in before} if isinstance(before, list) else set()
    after_items = {str(item) for item in after} if isinstance(after, list) else set()
    return [
        {"kind": kind, "key": f"{prefix}{item}", "direction": "added", "before": None, "after": item}
        for item in sorted(after_items - before_items)
    ] + [
        {"kind": kind, "key": f"{prefix}{item}", "direction": "removed", "before": item, "after": None}
        for item in sorted(before_items - after_items)
    ]


def claude_settings(before_text: str | None, after_text: str | None) -> dict[str, Any]:
    before, before_ok = _parse(before_text)
    after, after_ok = _parse(after_text)
    if not (before_ok and after_ok):
        return {"scope": "refusal_expected", "changes": [], "unclassified_keys": [],
                "unparsed": [side for side, ok in (("base", before_ok), ("head", after_ok)) if not ok]}
    changes: list[dict[str, Any]] = []
    scope = "supported"
    unclassified: list[str] = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        if key == "permissions":
            old_p = old if isinstance(old, dict) else {}
            new_p = new if isinstance(new, dict) else {}
            for disposition in PERMISSION_LISTS:
                changes += _list_changes("permission_rule", f"{disposition}:", old_p.get(disposition), new_p.get(disposition))
            changes += _list_changes("additional_path", "", old_p.get("additionalDirectories"), new_p.get("additionalDirectories"))
            for scalar in sorted(PERMISSION_SCALARS):
                if change := _change("setting", f"permissions.{scalar}", old_p.get(scalar), new_p.get(scalar)):
                    changes.append(change)
            other = {k for k in set(old_p) | set(new_p) if old_p.get(k) != new_p.get(k)} - set(PERMISSION_LISTS) - PERMISSION_SCALARS - {"additionalDirectories"}
            unclassified += [f"permissions.{k}" for k in sorted(other)]
        elif key == "hooks":
            changes += _map_changes("hook", "", old, new)
        elif key == "sandbox":
            changes += _map_changes("setting", "sandbox.", old, new)
        elif key == "enabledPlugins":
            changes += _map_changes("plugin", "", old, new)
        elif key == "additionalDirectories":
            changes += _list_changes("additional_path", "", old, new)
        elif key in {"enabledMcpjsonServers", "disabledMcpjsonServers"}:
            changes += _list_changes("setting", f"{key}:", old, new)
        elif key in CLAUDE_SUPPORTED_KEYS:
            if change := _change("setting", key, old, new):
                changes.append(change)
        elif key in CLAUDE_UNSUPPORTED_CAPABILITY_KEYS:
            if change := _change("unsupported_setting", key, old, new):
                changes.append(change)
            scope = "unsupported"
        elif key in CLAUDE_BENIGN_KEYS:
            continue
        else:
            unclassified.append(key)
    if unclassified and scope == "supported":
        scope = "unclassified"
    return {"scope": scope, "changes": changes, "unclassified_keys": unclassified}


def mcp_file(before_text: str | None, after_text: str | None) -> dict[str, Any]:
    """`.mcp.json` / `.cursor/mcp.json`: servers by name, whole entry compared."""

    before, before_ok = _parse(before_text)
    after, after_ok = _parse(after_text)
    if not (before_ok and after_ok):
        return {"scope": "refusal_expected", "changes": [], "unclassified_keys": [],
                "unparsed": [side for side, ok in (("base", before_ok), ("head", after_ok)) if not ok]}
    changes = _map_changes("mcp_server", "", before.get("mcpServers"), after.get("mcpServers"))
    unclassified = sorted(
        key for key in set(before) | set(after)
        if key not in {"mcpServers", "$schema"} and before.get(key) != after.get(key)
    )
    scope = "unclassified" if unclassified else "supported"
    return {"scope": scope, "changes": changes, "unclassified_keys": unclassified}


def expected(kind: str, before_text: str | None, after_text: str | None) -> dict[str, Any]:
    if kind == ".claude/settings.json":
        return claude_settings(before_text, after_text)
    if kind in {".mcp.json", ".cursor/mcp.json"}:
        return mcp_file(before_text, after_text)
    raise ValueError(f"no expectation reader for {kind}")
