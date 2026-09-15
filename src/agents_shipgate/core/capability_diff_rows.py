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

from collections import Counter
from typing import Any

from agents_shipgate.core.host_grants import (
    hook_loading_basis,
    host_grant_expansion_signals,
    step_action_key,
)
from agents_shipgate.schemas.capability_diff import CapabilityDiffRow as CapabilityDiffRow

ABSENT = "—"


def _step_action_changes(
    before: dict[str, Any] | None, after: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The step references only one side of a changed workflow declares (#771).

    Compared as each job's multiset of references, the way the comparator
    decides whether the grant changed at all, so a reordered or renamed step
    appears on neither side. Each entry keeps its job and step as evidence.
    """

    if not (
        before and after
        and before.get("kind") == after.get("kind") == "workflow"
        and "permission_contexts" in before and "permission_contexts" in after
    ):
        return [], []

    def only_in(side: list[dict[str, Any]], other: list[dict[str, Any]]) -> list[dict[str, Any]]:
        surplus = Counter(step_action_key(item) for item in side) - Counter(
            step_action_key(item) for item in other
        )
        picked: list[dict[str, Any]] = []
        for item in side:
            key = step_action_key(item)
            if surplus[key] > 0:
                surplus[key] -= 1
                picked.append(item)
        return picked

    # A workflow whose steps declare no listed reference omits the key.
    old, new = before.get("step_actions", []), after.get("step_actions", [])
    return only_in(old, new), only_in(new, old)


def _step_action_value(item: dict[str, Any]) -> str:
    reason = item.get("unresolved_reason")
    suffix = f" (unresolved: {str(reason).replace('_', ' ')})" if reason else ""
    if reason in {"steps_not_a_list", "step_not_a_mapping"}:
        return f"{item['job']}/{item['step']}: not a readable step{suffix}"
    uses = "<not a string>" if item.get("uses") is None else str(item["uses"])
    return f"{item['job']}/{item['step']}: uses {uses}{suffix}"


def _moved_between_jobs(
    gone: list[dict[str, Any]], new: list[dict[str, Any]]
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, Any]]]:
    """Pair a reference that left one job with the same reference arriving in another.

    What remains is a reference that changed, was added or was removed.
    """

    arriving = list(new)
    moved: list[tuple[dict[str, Any], dict[str, Any]]] = []
    changed: list[dict[str, Any]] = []
    for item in gone:
        reference = step_action_key(item)[1:]
        match = next(
            (
                other for other in arriving
                if step_action_key(other)[1:] == reference and other["job"] != item["job"]
            ),
            None,
        )
        if match is None:
            changed.append(item)
        else:
            arriving.remove(match)
            moved.append((item, match))
    return moved, [*changed, *arriving]

#: Direction is deliberately coarse here. Presence is certain: a grant is
#: in one side and not the other. *Width* is not — deciding that
#: `Bash(npm *)` -> `Bash(npm test:*)` narrows needs the pattern lattice in
#: #657, so a change the engine has not called an expansion is reported as
#: `changed`, not guessed to be narrowing.
ADDED = "added"
REMOVED = "removed"
WIDENED = "widened"
CHANGED = "changed"


def _grant_value(
    grant: dict[str, Any] | None,
    *,
    redact_permission_arguments: bool = False,
    step_actions: list[dict[str, Any]] | None = None,
) -> str:
    """What a reader recognises this grant by.

    A workflow has no single name — its authority *is* the combination of
    access and triggers, so both sides render that combination or the row
    reads "workflow -> workflow" and says nothing. ``step_actions`` are the
    step references this side alone declares; unchanged ones are not repeated.
    """

    if not grant:
        return ABSENT
    kind = str(grant.get("kind") or "")
    if kind == "permission_rule" and redact_permission_arguments:
        from agents_shipgate.core.host_boundary import _safe_rule

        return _safe_rule(str(grant.get("rule") or ""))
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
        parts.extend(_step_action_value(item) for item in step_actions or [])
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


def _why(
    grant: dict[str, Any],
    direction: str,
    *,
    gone_steps: list[dict[str, Any]] | None = None,
    new_steps: list[dict[str, Any]] | None = None,
) -> str:
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
        moved, changed_steps = _moved_between_jobs(gone_steps or [], new_steps or [])
        if moved:
            # The same declared code, now under another job's token context.
            pairs = ", ".join(
                f"{old['job']}/{old['step']} → {now['job']}/{now['step']}" for old, now in moved
            )
            reasons.append(
                f"a step's action reference moved between jobs ({pairs}); the same "
                "reference now runs with the receiving job's token permissions and adds no scope"
            )
        if changed_steps:
            # A reference names code, not scopes: moving a SHA to a branch
            # changes what runs under the job's token, and adds no permission.
            labels = list(dict.fromkeys(f"{item['job']}/{item['step']}" for item in changed_steps))
            reasons.append(
                "a step's action reference changed ("
                + ", ".join(labels)
                + "); it names different code to run with that job's existing "
                "token permissions and adds no scope"
            )
        return "; ".join(reasons) or "changes the workflow's own authority"
    if kind == "hook":
        # The basis, stated in the row, because the row is what a reviewer
        # reads: a parsed hook file is not proof a host loads it (#714).
        basis = hook_loading_basis(grant)
        if basis == "host_configuration":
            return "changes what runs around the agent's actions"
        if direction == REMOVED:
            # A removal is described from the baseline's grant, which may have
            # been recorded without its basis. Claim nothing about selection.
            return (
                "removes a hook declared in this file; whether a host loaded it "
                "is not established"
            )
        if basis == "declared_only":
            return (
                "declares a hook that no settings file, plugin manifest or marketplace entry "
                "in this repository selects; whether a host loads it is not established"
            )
        if basis == "plugin_selected":
            return (
                "changes a hook a plugin in this repository selects; whether that plugin "
                "is installed or enabled is not established"
            )
        return (
            "changes a hook declared in this file; how a host would load it is not "
            "established"
        )
    if kind == "instruction_trust_root":
        return "changes instructions the agent is given"
    return f"changes a {kind or 'host'} grant"


def capability_diff_rows(
    payload: dict[str, Any], *, redact_permission_arguments: bool = False
) -> list[CapabilityDiffRow]:
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
        # Classify original typed evidence; redaction affects display values only.
        expands = bool(expansions.intersection(host_grant_expansion_signals([change])))
        if direction == CHANGED and expands:
            direction = WIDENED
        gone_steps, new_steps = _step_action_changes(before_grant, after_grant)
        rows.append(
            CapabilityDiffRow(
                subject=_subject(grant),
                before=_grant_value(
                    before_grant,
                    redact_permission_arguments=redact_permission_arguments,
                    step_actions=gone_steps,
                ),
                after=_grant_value(
                    after_grant,
                    redact_permission_arguments=redact_permission_arguments,
                    step_actions=new_steps,
                ),
                direction=direction,
                why=_why(grant, direction, gone_steps=gone_steps, new_steps=new_steps),
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
