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

import re
from dataclasses import asdict, dataclass
from typing import Any

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


@dataclass(frozen=True)
class CapabilityDiffRow:
    subject: str
    before: str
    after: str
    direction: str
    why: str
    severity: str
    #: The engine called this change an expansion of authority. Kept apart
    #: from ``direction`` on purpose: presence is a fact this module can
    #: read off both sides, whereas widening is a judgement, and only the
    #: engine's `expansion_signals` may make it.
    expands: bool = False

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


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
        for scope in grant.get("write_scopes") or []:
            parts.append(str(scope).split(":", 1)[-1].strip())
        if grant.get("pull_request_target"):
            parts.append("pull_request_target")
        return ", ".join(part for part in parts if part) or kind
    for key in ("rule", "server", "name", "value", "permission"):
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
        return "a new MCP tool surface the agent may call"
    if kind == "permission_rule":
        if direction == REMOVED:
            return "a permission the agent previously had here"
        if grant.get("disposition") == "deny":
            return "a denial the agent is subject to"
        if wildcard and access == "admin":
            return "matches any command of this kind, without a prompt"
        if wildcard:
            return "matches every target of this kind, without a prompt"
        return "runs without a prompt"
    if kind == "workflow":
        reasons = []
        if grant.get("pull_request_target"):
            reasons.append("runs with repository secrets on fork pull requests")
        if access in {"admin", "write"} or grant.get("write_all"):
            reasons.append("the workflow can write to the repository")
        return "; ".join(reasons) or "changes the workflow's own authority"
    if kind == "hook":
        return "changes what runs around the agent's actions"
    if kind == "instruction_trust_root":
        return "changes instructions the agent is given"
    return f"changes a {kind or 'host'} grant"


def _expansion_keys(payload: dict[str, Any]) -> set[str]:
    """Grant identities the engine called an expansion.

    Signals read ``<signal>: <host>:<value>``; the host/value pair is what
    ties one back to a row. Parsing the engine's own signal is what keeps
    `widened` from becoming this module's opinion.
    """

    keys: set[str] = set()
    for signal in payload.get("expansion_signals") or []:
        text = str(signal)
        matched = re.match(r"[a-z_]+:\s*([^:]+):(.+)$", text)
        if matched:
            keys.add(f"{matched.group(1).strip()}:{matched.group(2).strip()}")
            continue
        # `workflow_write_changed: .github/workflows/ci.yml` names a source
        # rather than a host/value pair.
        single = re.match(r"[a-z_]+:\s*(.+)$", text)
        if single:
            keys.add(single.group(1).strip())
    return keys


def capability_diff_rows(payload: dict[str, Any]) -> list[CapabilityDiffRow]:
    """Every typed grant change in ``payload``, one row each."""

    expansions = _expansion_keys(payload)
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
        value = _grant_value(after_grant or before_grant)
        expands = (
            direction != REMOVED
            and (
                f"{grant.get('host')}:{value}" in expansions
                or str(grant.get("source") or "") in expansions
            )
        )
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
