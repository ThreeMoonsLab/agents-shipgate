"""One row per host-capability change, projected from the drift payload.

`audit --host --drift` already decides what changed; this module only says
it in a form a reviewer can act on. Every field is read from the payload —
`risk` is the engine's severity, `expansion_signals` is the engine's word
on which changes widen authority — so this is a projection, never a second
opinion about the same change (#651).

The renderer, `--json`, and `check`'s text format share these rows, so the
three cannot describe one change three ways.
"""

from __future__ import annotations

from typing import Any

from agents_shipgate.core.host_grants import host_grant_expansion_signals
from agents_shipgate.schemas.capability_diff import CapabilityDiffRow as CapabilityDiffRow

ABSENT = "—"

#: Direction is deliberately coarse here. Presence is certain: a grant is
#: in one side and not the other. *Width* is not — deciding that
#: `Bash(npm *)` -> `Bash(npm test:*)` narrows needs the pattern lattice in
#: #657, so a change the engine has not called an expansion is reported as
#: `changed`, not guessed to be narrowing.
ADDED = "added"
REMOVED = "removed"
WIDENED = "widened"
CHANGED = "changed"


def _grant_value(grant: dict[str, Any] | None) -> str:
    """What a reader recognises this grant by.

    A workflow has no single name — its authority *is* the combination of
    access and triggers, so both sides render that combination or the row
    reads "workflow -> workflow" and says nothing.
    """

    if not grant:
        return ABSENT
    kind = str(grant.get("kind") or "")
    if kind == "workflow":
        parts = [str(grant.get("access") or "")]
        if grant.get("write_all"):
            parts.append("write-all")
        if "permission_contexts" in grant:
            for context in grant["permission_contexts"]:
                if context["state"] != "explicit":
                    reason = "repository defaults" if context["state"] == "repository_default" else "unresolved permissions"
                    parts.append(f"{context['job']}: {reason} (unknown)")
                elif not context["permissions"]:
                    parts.append(f"{context['job']}: no token permissions")
                else:
                    for scope, level in context["permissions"].items():
                        permission = f"{level}-all" if scope == "*" else f"{scope}: {level}"
                        parts.append(f"{context['job']}: {permission}")
        else:
            parts.extend(str(scope) for scope in grant.get("write_scopes") or [])
        if grant.get("pull_request_target"):
            parts.append("pull_request_target")
        other_triggers = [name for name in grant.get("triggers", []) if name != "pull_request_target"]
        if other_triggers:
            parts.append("on: " + ", ".join(other_triggers))
        for call in grant.get("reusable_calls") or []:
            forwarding = "secrets: inherit → " if call.get("secrets_inherit") else "uses: "
            parts.append(f"{call['job']}: {forwarding}{call['uses']}")
        return ", ".join(part for part in parts if part) or kind
    # `event` names a hook's trigger. Without it a hook row rendered as
    # "hook", which tells a reviewer a hook changed and not which one (#689).
    for key in ("rule", "server", "name", "event", "value", "permission"):
        value = grant.get(key)
        if value:
            return str(value)
    return kind or ABSENT


def _subject(grant: dict[str, Any]) -> str:
    host = str(grant.get("host") or "")
    source = str(grant.get("source") or "")
    kind = str(grant.get("kind") or "")
    if source and host:
        return f"{host} {source}"
    return source or host or kind


def _why(grant: dict[str, Any], direction: str) -> str:
    """Why a reviewer should care, in the reviewer's terms.

    Stated as what the grant *permits*, never as a prediction about what the
    agent will do with it — the engine reads configuration, not behaviour.
    """

    kind = str(grant.get("kind") or "")
    access = str(grant.get("access") or "")
    wildcard = bool(grant.get("wildcard"))
    if kind == "mcp_server":
        if direction == REMOVED:
            return "an MCP tool surface is no longer offered to the agent"
        return "an MCP tool surface the agent may call has changed"
    if kind == "permission_rule":
        disposition = grant.get("disposition")
        if disposition in {"deny", "ask"}:
            condition = "denial" if disposition == "deny" else "confirmation requirement"
            if direction == REMOVED:
                return f"removes a {condition} the agent was subject to"
            return f"a {condition} the agent is subject to"
        if direction == REMOVED:
            return "removes a permission the agent previously had here"
        if wildcard and access == "admin":
            return "matches any command of this kind, without a prompt"
        if wildcard:
            return "matches every target of this kind, without a prompt"
        return "runs without a prompt"
    if kind == "workflow":
        reasons = []
        if grant.get("pull_request_target"):
            reasons.append("uses the privileged pull_request_target event context")
        if access in {"admin", "write"} or grant.get("write_all"):
            reasons.append("grants write permissions to workflow jobs")
        if any(context["state"] != "explicit" for context in grant.get("permission_contexts", [])):
            reasons.append("some effective token permissions are unknown; repository defaults or unresolved declarations require review")
        for call in grant.get("reusable_calls") or []:
            if call.get("secrets_inherit"):
                reasons.append(f"passes the caller's available secrets to {call['uses']}")
        return "; ".join(reasons) or "changes the workflow's own authority"
    if kind == "hook":
        return "changes what runs around the agent's actions"
    if kind == "instruction_trust_root":
        return "changes instructions the agent is given"
    return f"changes a {kind or 'host'} grant"


def capability_diff_rows(payload: dict[str, Any]) -> list[CapabilityDiffRow]:
    """Every typed grant change in ``payload``, one row each."""

    expansions = set(payload.get("expansion_signals") or [])
    rows: list[CapabilityDiffRow] = []
    for change in payload.get("changes") or []:
        before_grant = change.get("baseline")
        after_grant = change.get("current")
        grant = after_grant or before_grant
        if not grant:
            continue
        if before_grant is None:
            direction = ADDED
        elif after_grant is None:
            direction = REMOVED
        else:
            direction = CHANGED
        expands = bool(expansions.intersection(host_grant_expansion_signals([change])))
        if direction == CHANGED and expands:
            direction = WIDENED
        rows.append(
            CapabilityDiffRow(
                subject=_subject(grant),
                before=_grant_value(before_grant),
                after=_grant_value(after_grant),
                direction=direction,
                why=_why(grant, direction),
                severity=str(grant.get("risk") or "unknown"),
                expands=expands,
            )
        )
    return sorted(
        rows,
        key=lambda row: (_SEVERITY_ORDER.get(row.severity, 9), row.subject, row.after),
    )


#: Most severe first: a reviewer reads the top of a table, so the ordering
#: is part of the answer rather than a presentation detail.
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


__all__ = ["CapabilityDiffRow", "capability_diff_rows", "ABSENT"]
