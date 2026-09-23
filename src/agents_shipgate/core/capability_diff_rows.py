"""One row per host-capability change, projected from the drift payload.

`audit --host --drift` already decides what changed; this module only says
it in a form a reviewer can act on. Every field is read from the payload —
`risk` is the engine's severity, `expansion_signals` is the engine's word
on which changes widen authority — so this is a projection, never a second
opinion about the same change (#651).

The renderer, `--json`, and `check`'s text format share these rows, so the
three cannot describe one change three ways. The text projections read them
through :func:`review_changes`, which adds what the published row leaves to
the reader: a permission rule's disposition, an MCP server's published launch
facts and arguments, a hook's published handlers (#819), and one change for a
replacement or move the engine established (#795).

Those presentation facts are published too, so a machine consumer reads what a
human reads: the rule's disposition on the row itself, and the joined changes,
their direction, the counters and the review question in the comparison's
``review`` block, built here and never re-derived by a renderer (#795 slice 2).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from agents_shipgate.core.host_grants import (
    hook_loading_basis,
    host_grant_expansion_signals,
    permission_rule_replacements,
    published_setting_value,
    published_workflow_label,
    secret_mapping_key,
    step_action_key,
)
from agents_shipgate.core.host_settings import rate_claude_setting, setting_value_text
from agents_shipgate.schemas.capability_diff import CapabilityDiffRow as CapabilityDiffRow

ABSENT = "—"

#: Grant kinds that publish a `setting` and its `value`.
_SETTING_KINDS = frozenset({"permission_mode", "sandbox"})

#: A job and one named secret mapping its reusable call declares (#693).
SecretMapping = tuple[str, dict[str, Any]]


def _is_workflow_pair(before: dict[str, Any] | None, after: dict[str, Any] | None) -> bool:
    return bool(
        before and after
        and before.get("kind") == after.get("kind") == "workflow"
        and "permission_contexts" in before and "permission_contexts" in after
    )


def _secret_mapping_changes(
    before: dict[str, Any] | None, after: dict[str, Any] | None
) -> tuple[list[SecretMapping], list[SecretMapping]]:
    """The named secret mappings only one side of a changed workflow declares (#693).

    Compared as each job's set of facts, the way the comparator decides
    whether the grant changed, so reordering the ``secrets:`` keys or
    re-quoting a value appears on neither side.
    """

    if not _is_workflow_pair(before, after):
        return [], []

    def mappings(grant: dict[str, Any]) -> list[SecretMapping]:
        return [
            (str(call["job"]), entry)
            for call in grant.get("reusable_calls", [])
            for entry in call.get("secret_mappings", [])
        ]

    def only_in(side: list[SecretMapping], other: list[SecretMapping]) -> list[SecretMapping]:
        surplus = Counter((job, secret_mapping_key(entry)) for job, entry in side) - Counter(
            (job, secret_mapping_key(entry)) for job, entry in other
        )
        picked: list[SecretMapping] = []
        for job, entry in side:
            key = (job, secret_mapping_key(entry))
            if surplus[key] > 0:
                surplus[key] -= 1
                picked.append((job, entry))
        return picked

    old, new = mappings(before or {}), mappings(after or {})
    return only_in(old, new), only_in(new, old)


def _secret_mapping_value(item: SecretMapping) -> str:
    job, entry = item
    reason = entry.get("unresolved_reason")
    suffix = f" (unresolved: {str(reason).replace('_', ' ')})" if reason else ""
    if entry.get("destination") is None:
        return f"{job}: secrets{suffix}"
    source = f" ← secrets.{entry['source']}" if entry.get("source") is not None else ""
    return f"{job}: secret {entry['destination']}{source}{suffix}"


def _secret_mapping_reasons(gone: list[SecretMapping], new: list[SecretMapping]) -> list[str]:
    """Say whether a destination was added, removed, or now names another source.

    A destination on both sides whose facts differ is a changed source; one
    on a single side was added or removed. An entry whose value this audit does
    not read is described as unread rather than named, so no sentence claims a
    source name that was never seen. The wording never ranks the names.
    """

    def label(item: SecretMapping) -> str:
        job, entry = item
        return f"{job}/{entry['destination']}" if entry.get("destination") is not None else f"{job}/secrets"

    def destination(item: SecretMapping) -> tuple[str, str | None]:
        return item[0], item[1].get("destination")

    def named(item: SecretMapping) -> bool:
        return item[1].get("form") == "secret"

    arriving = {destination(item) for item in new}
    before = {destination(item): item for item in gone}
    groups: dict[str, list[SecretMapping]] = {}
    for item in new:
        was = before.get(destination(item))
        if was is None:
            groups.setdefault("added" if named(item) else "added_unread", []).append(item)
        elif named(was) and named(item):
            groups.setdefault("remapped", []).append(item)
        elif named(item):
            groups.setdefault("now_named", []).append(item)
        elif named(was):
            groups.setdefault("now_unread", []).append(item)
        else:
            groups.setdefault("reformed", []).append(item)
    for item in gone:
        if destination(item) not in arriving:
            groups.setdefault("removed" if named(item) else "removed_unread", []).append(item)

    reasons = []
    for key, wording in (
        ("remapped", "a reusable workflow's secret now comes from a different named source"),
        ("now_named", "a reusable workflow's secret now comes from a named source, where its value was not readable before"),
        ("now_unread", "a reusable workflow's secret no longer comes from a named source, and its new value is not readable"),
        ("reformed", "a reusable workflow's secret is passed in a different unreadable form"),
        ("added", "a reusable workflow is now passed a named secret"),
        ("added_unread", "a reusable workflow is now passed a secret whose value is not readable"),
        ("removed", "a reusable workflow is no longer passed a named secret"),
        ("removed_unread", "a reusable workflow is no longer passed a secret whose value is not readable"),
    ):
        items = groups.get(key)
        if items:
            labels = ", ".join(dict.fromkeys(label(item) for item in items))
            reasons.append(f"{wording} ({labels})")
    if reasons:
        # Scoped to the mapping: the same row may also carry a real widening,
        # such as a new `contents: write` or `secrets: inherit`, and this
        # sentence must not appear to deny that one.
        subject = (
            "a secret's name"
            if groups.keys() & {"remapped", "now_named", "now_unread", "added", "removed"}
            else "an unreadable value's form"
        )
        reasons.append(
            f"{subject} does not establish the secret's privilege, whether the caller "
            "has it, or what the called workflow does with it, so this mapping change "
            "is not itself counted as a widening"
        )
    return reasons


def _step_action_changes(
    before: dict[str, Any] | None, after: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The step references only one side of a changed workflow declares (#771).

    Compared as each job's multiset of references, the way the comparator
    decides whether the grant changed at all, so a reordered or renamed step
    appears on neither side. Each entry keeps its job and step as evidence.
    """

    if not _is_workflow_pair(before, after):
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
    old, new = (before or {}).get("step_actions", []), (after or {}).get("step_actions", [])
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
    secret_mappings: list[SecretMapping] | None = None,
) -> str:
    """What a reader recognises this grant by.

    A workflow has no single name — its authority *is* the combination of
    access and triggers, so both sides render that combination or the row
    reads "workflow -> workflow" and says nothing. ``step_actions`` and
    ``secret_mappings`` are the step references and named secrets this side
    alone declares; unchanged ones are not repeated.
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
        parts.extend(_secret_mapping_value(item) for item in secret_mappings or [])
        parts.extend(_step_action_value(item) for item in step_actions or [])
        return ", ".join(part for part in parts if part) or kind
    if kind in _SETTING_KINDS and grant.get("setting"):
        # A setting row read `True` or `dontAsk` alone, which names no setting
        # (#827). It names both, the value as the settings file spells it.
        setting = str(grant["setting"])
        return f"{setting}: {setting_value_text(setting, published_setting_value(grant))}"
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
    gone_secrets: list[SecretMapping] | None = None,
    new_secrets: list[SecretMapping] | None = None,
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
        reasons.extend(_secret_mapping_reasons(gone_secrets or [], new_secrets or []))
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
        if basis == "project_enabled_plugin":
            # Loaded like a settings hook, so it reads as one, and names why.
            return (
                "changes what runs around the agent's actions; this repository's project "
                "settings enable the plugin that selects this hook"
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
    if kind == "permission_mode" and grant.get("host") == "claude-code" and grant.get("setting"):
        # The basis of the rating the row's severity carries, from the one
        # table `check` rates the same value with (#827).
        basis = rate_claude_setting(
            str(grant["setting"]), published_setting_value(grant)
        ).basis
        if basis:
            return f"removes a setting that {basis}" if direction == REMOVED else basis
    return f"changes a {kind or 'host'} grant"


#: A replacement or move the engine established, shared by the two rows it joins.
@dataclass(frozen=True, eq=False)
class _Link:
    direction: str
    before: str
    after: str
    why: str


@dataclass(frozen=True)
class _RowView:
    """How a reviewer reads one row: its cells, a field difference, and any link."""

    before: str
    after: str
    change: str | None = None
    link: _Link | None = None


#: The attribute a built row carries its view on. The view is not a row field:
#: `--json`, `verifier.json`, `check` rows and the control envelope publish the
#: row exactly as before (#795), and equality ignores it. A row read back from
#: JSON has no view and renders from its published values alone.
_VIEW = "_review_view"


@dataclass(frozen=True)
class ReviewChange:
    """One change as the text projections print it (#795).

    Usually one published row. Two rows join into one change only where the
    engine itself established the link: the allow rule the permission lattice
    decided another replaced (`widened` or `narrowed`), or the exact same rule
    text that left one disposition and arrived in another (`moved`). Nothing is
    paired by likeness, and ``row_indexes`` names the rows it stands for, by
    position in the sequence it was read from.
    """

    severity: str
    direction: str
    subject: str
    before: str
    after: str
    #: The field-level difference, in place of ``before → after``, when both
    #: sides name the same grant (an MCP server whose launch changed, a hook
    #: whose matcher, command or timeout changed).
    change: str | None
    why: str
    expands: bool
    #: Where its rows stand in the sequence passed to :func:`review_changes`.
    #: The changes of one sequence partition it: every row belongs to exactly
    #: one change, which is what lets a summary count rows and changes apart.
    row_indexes: tuple[int, ...]

    @property
    def rows(self) -> int:
        """How many published rows this change stands for."""

        return len(self.row_indexes)


def review_changes(rows: Sequence[CapabilityDiffRow]) -> list[ReviewChange]:
    """The changes a reviewer reads, in the rows' order.

    A linked pair is printed where its first row stands, and only when both of
    its rows are present; a pair split by a caller prints as two rows.
    """

    views = [getattr(row, _VIEW, None) for row in rows]
    members: dict[_Link, list[int]] = {}
    for index, view in enumerate(views):
        if view is not None and view.link is not None:
            members.setdefault(view.link, []).append(index)
    joined = {group[1]: group[0] for group in members.values() if len(group) == 2}
    changes: list[ReviewChange] = []
    for index, row in enumerate(rows):
        if index in joined:
            continue
        view = views[index]
        link = view.link if view is not None else None
        group = members.get(link, []) if link is not None else []
        if link is not None and len(group) == 2:
            other = rows[group[1]]
            changes.append(
                ReviewChange(
                    severity=min(
                        (row.severity, other.severity),
                        key=lambda severity: _SEVERITY_ORDER.get(severity, 9),
                    ),
                    direction=link.direction,
                    subject=row.subject,
                    before=link.before,
                    after=link.after,
                    change=None,
                    why=link.why,
                    expands=row.expands or other.expands,
                    row_indexes=tuple(group),
                )
            )
            continue
        changes.append(
            ReviewChange(
                severity=row.severity,
                direction=row.direction,
                subject=row.subject,
                before=view.before if view is not None else row.before,
                after=view.after if view is not None else row.after,
                change=view.change if view is not None else None,
                why=row.why,
                expands=row.expands,
                row_indexes=(index,),
            )
        )
    return changes


def review_question(changes: Sequence[ReviewChange]) -> str:
    """One bounded question for a reviewer, asked only about changes that exist (#795).

    Where a change joins rows, the question names the row count too: the
    control headline beside it in `verify` and the PR comment counts rows
    (`8 repository-declared host capability change(s)`), and `diff`'s summary
    already says `from 8 rows`.

    It lives beside :func:`review_changes` because it is one of the facts the
    published comparison now carries: the text and `--json` must not be able to
    ask and record two different questions about the same rows.
    """

    # `capability`, not `permission`: a change may be an MCP server, a hook, a
    # workflow grant or instructions as well as a permission rule.
    rows = sum(change.rows for change in changes)
    bridge = f" (from {rows} rows)" if rows != len(changes) else ""
    if len(changes) == 1:
        return f"Review question: Does the team intend this declared capability change{bridge}?"
    return (
        f"Review question: Does the team intend these {len(changes)} declared capability "
        f"changes{bridge}?"
    )


#: How many names one field difference lists before counting the rest.
_NAME_LIMIT = 5


def _names(tokens: list[str]) -> str:
    """At most ``_NAME_LIMIT`` tokens, then how many more there are."""

    shown = tokens[:_NAME_LIMIT]
    rest = len(tokens) - len(shown)
    return " ".join(shown) + (f" and {rest} more" if rest else "")


def _key_names(keys: Any) -> list[str]:
    """Env or header key names as they may be printed: token-shaped names are redacted (#802)."""

    return [published_workflow_label(str(key)) for key in keys or []]


#: The only URL text may print: the engine's sanitized form, one of the five
#: schemes it sanitizes, a host and optional port, and no path but `/` or
#: `/<redacted-path>`. The sanitizer returns any other URL as written, so a
#: `${SLACK_MCP_BASE}/hooks/<secret>` value, a URL without a scheme or a custom
#: scheme's path would otherwise print verbatim (#795 review, #723).
_PRINTABLE_URL = re.compile(r"(?:https?|wss?|sse)://[^\s/?#@]+(?:/|/<redacted-path>)?")

#: What a URL server's launch fact reads when its URL is not in that form.
_URL_NOT_SHOWN = "not shown"


def _mcp_endpoint(grant: dict[str, Any]) -> str | None:
    """The grant's published endpoint as text may print it, or ``None`` when it has none.

    A command server's endpoint is its command's name and passes through the
    #802 label redaction, the only guard between a token-shaped command name
    and this text. A URL server's endpoint prints only in the sanitized form
    of :data:`_PRINTABLE_URL`; any other value reads ``not shown``. The JSON
    grant is unchanged: this decides only what the text repeats.
    """

    endpoint = grant.get("endpoint")
    if not endpoint:
        return None
    if grant.get("transport") == "url" and not _PRINTABLE_URL.fullmatch(str(endpoint)):
        return _URL_NOT_SHOWN
    return published_workflow_label(str(endpoint))


def _mcp_launch(grant: dict[str, Any]) -> str | None:
    """The published command name or URL, labelled for what it is.

    A command server's grant publishes only the name of its command's first
    word, never its path: `npx`, `./npx` and `./tools/npx` all publish `npx`.
    So the label is `command name`, and a reader is never told the command
    itself when only its name was compared.
    """

    endpoint = _mcp_endpoint(grant)
    if endpoint is None:
        return None
    kind = "url" if grant.get("transport") == "url" else "command name"
    return f"{kind} {endpoint}"


def _quoted_word(word: str) -> str:
    """A published word as a command line writes it: quoted when it is empty or holds whitespace (#819)."""

    if word and not any(char.isspace() for char in word):
        return word
    return "'" + word.replace("'", "'\\''") + "'"


def _more(count: int, noun: str) -> str:
    return f" (+{count} more {noun}{'s' if count != 1 else ''})" if count else ""


def _args_text(args: list[str] | None, omitted: int) -> str:
    """Published MCP arguments as one line, with the count past the bound (#819)."""

    if args is None:
        return "(not a list)"
    if not args and not omitted:
        return "(none)"
    return " ".join(_quoted_word(word) for word in args) + _more(omitted, "argument")


def _mcp_cell(value: str, grant: dict[str, Any] | None) -> str:
    """An added or removed MCP server with the launch facts its grant publishes."""

    if not grant or value == ABSENT:
        return value
    args = grant.get("args")
    facts = [
        fact
        for fact in (
            _mcp_launch(grant),
            "args " + _args_text(args, int(grant.get("omitted_args") or 0))
            if args or grant.get("omitted_args") or ("args" in grant and args is None)
            else None,
            "env keys " + _names(_key_names(grant["env_keys"])) if grant.get("env_keys") else None,
            "header keys " + _names(_key_names(grant["header_keys"])) if grant.get("header_keys") else None,
        )
        if fact
    ]
    return f"{value} ({'; '.join(facts)})" if facts else value


#: Every published MCP fact a changed row compares (#795).
_MCP_FIELDS = ("transport", "endpoint", "env_keys", "header_keys")


def _mcp_args_change(before: dict[str, Any], after: dict[str, Any]) -> str | None:
    """The difference in two readings' published launch arguments, or ``None`` (#819).

    ``None`` too when either reading does not publish them, as a grant from a
    ``0.6`` snapshot or a saved baseline does not.
    """

    if "args" not in before or "args" not in after:
        return None
    old = (before["args"], int(before.get("omitted_args") or 0))
    new = (after["args"], int(after.get("omitted_args") or 0))
    if old == new:
        return None
    return f"args {_args_text(*old)} → {_args_text(*new)}"


def _mcp_change(name: str, before: dict[str, Any], after: dict[str, Any]) -> str | None:
    """What differs between two readings of one MCP server, in its published fields.

    ``None`` when either side does not publish every field, so a grant read
    from an older snapshot is never described by a difference it cannot show.
    A command's path, its arguments and other settings are not published, so a
    change confined to them says what was compared and that the change is
    elsewhere, rather than ``name → name`` or a claim that the command is
    unchanged. Two different endpoints that print alike, such as two URLs
    neither of which is printed, read ``url changed (not shown)``.
    """

    if any(key not in grant for grant in (before, after) for key in _MCP_FIELDS):
        return None
    parts: list[str] = []
    old_launch, new_launch = _mcp_launch(before), _mcp_launch(after)
    if old_launch != new_launch:
        if (
            old_launch and new_launch
            and before.get("transport") == after.get("transport")
        ):
            parts.append(f"{old_launch} → {_mcp_endpoint(after)}")
        else:
            parts.append(f"{old_launch or 'no command or url'} → {new_launch or 'no command or url'}")
    elif old_launch is not None and before.get("endpoint") != after.get("endpoint"):
        # Two published endpoints that print alike: neither URL is in the
        # printable form, or a redaction wrote two command names the same way.
        # Printing the shared text on both sides would read as no change.
        kind = "url" if after.get("transport") == "url" else "command name"
        parts.append(f"{kind} changed ({_URL_NOT_SHOWN})")
    arguments = _mcp_args_change(before, after)
    if arguments is not None:
        parts.append(arguments)
    for field, label in (("env_keys", "env keys"), ("header_keys", "header keys")):
        old, new = set(before[field] or []), set(after[field] or [])
        added, removed = sorted(new - old), sorted(old - new)
        if added or removed:
            tokens = [f"+{key}" for key in _key_names(added)] + [f"-{key}" for key in _key_names(removed)]
            parts.append(f"{label} {_names(tokens)}")
    if not parts:
        bounded = any(int(grant.get("omitted_args") or 0) for grant in (before, after))
        return f"{name}: {_mcp_unshown_change(after, args_compared='args' in before, args_bounded=bounded)}"
    return f"{name}: " + "; ".join(parts)


def _mcp_unshown_change(
    grant: dict[str, Any], *, args_compared: bool = False, args_bounded: bool = False
) -> str:
    """A change confined to what the grant does not publish, in the words of what was compared.

    Only the command's name, or the URL's recorded value, the published
    arguments, and the env and header key names are compared. `npx` → `./npx`
    and `/usr/local/bin/node` → `./scripts/node` change the command while its
    name stays the same, so the sentence names the command's path as what this
    output does not show; an argument is published redacted and bounded, so a
    change inside a redacted or shortened one is not shown either (#819). When
    either side declares more arguments than are published (``args_bounded``),
    only the first N were compared, and an argument past them is named among
    what is not shown (#819 review). A URL that is not printed is named
    `url as recorded`, never by its value, and a URL server that declares no
    arguments is not said to have compared them. A grant read before arguments
    were published names them as not shown, as it did.
    """

    launch = _mcp_launch(grant)
    arguments = (
        "arguments, "
        if args_compared
        and "args" in grant
        and (grant.get("transport") != "url" or grant.get("args") or grant.get("omitted_args"))
        else ""
    )
    past_bound = ""
    if arguments and args_bounded:
        shown = len(grant.get("args") or [])
        arguments = f"the first {shown} arguments, "
        past_bound = f"an argument past the first {shown}, "
    if grant.get("transport") == "url":
        compared = "url as recorded" if _mcp_endpoint(grant) == _URL_NOT_SHOWN else launch or "url"
        unshown = f"{past_bound}the URL's query or another setting"
    else:
        compared = launch or "command name"
        unshown = (
            f"{past_bound}the command's path, a redacted or shortened argument, or another setting"
            if arguments
            else "the command's path or arguments"
        )
    return (
        f"no difference in the {compared}, {arguments}env key names or header key names; the "
        f"change is in a detail this output does not show, such as {unshown}"
    )


#: How many hook handlers an added or removed hook's cell lists before counting.
_HANDLER_LIMIT = 3

#: What a hook row says when its declaration is not in the shape the reader
#: establishes, and so no handler was published (#819).
_HOOK_SHAPE_NOT_READ = (
    "matcher, command and timeout not shown: the declaration is not a list of matcher "
    "groups whose hooks are objects"
)


def _command_text(command: dict[str, Any]) -> str:
    """A published hook command summary as one line: assignments, ``argv0``, its words, the count past the bound."""

    words = [f"{key}=<redacted>" for key in command.get("env_keys") or []]
    words += [str(command.get("argv0") or ""), *(command.get("args") or [])]
    return " ".join(_quoted_word(word) for word in words) + _more(
        int(command.get("omitted_args") or 0), "argument"
    )


def _handler_value(field: str, value: Any) -> str:
    if value is None:
        return "(none)"
    if field == "command":
        return _command_text(value)
    if field == "matcher" and value == "":
        return '""'
    return str(value)


#: A hook handler's published fields, in the order a row names them.
_HANDLER_FIELDS = ("matcher", "type", "command", "timeout")


def _handler_facts(handler: dict[str, Any]) -> list[str]:
    """What one published handler declares, in a reviewer's words. ``type command`` goes unsaid."""

    return [
        f"{field} {_handler_value(field, handler.get(field))}"
        for field in _HANDLER_FIELDS
        if handler.get(field) is not None and not (field == "type" and handler[field] == "command")
    ]


def _hook_cell(value: str, grant: dict[str, Any] | None) -> str:
    """An added or removed hook with the handlers its grant publishes (#819).

    A grant read before handlers were published renders its event alone, as it did.
    """

    if not grant or value == ABSENT or "handlers" not in grant:
        return value
    handlers = grant["handlers"]
    if handlers is None:
        return f"{value} ({_HOOK_SHAPE_NOT_READ})"
    total = len(handlers) + int(grant.get("omitted_handlers") or 0)
    if not total:
        return f"{value} (no handlers)"
    if total == 1 and handlers:
        facts = "; ".join(_handler_facts(handlers[0])) or "a handler with no matcher, command or timeout"
        return f"{value} ({facts})"
    listed = [
        f"handler {index}: {', '.join(_handler_facts(handler)) or 'no matcher, command or timeout'}"
        for index, handler in enumerate(handlers[:_HANDLER_LIMIT], start=1)
    ]
    rest = total - len(listed)
    return f"{value} ({'; '.join(listed)}{_more(rest, 'handler')})"


def _published_json(value: Any) -> str:
    """A published value as its JSON reads, so ``5`` and ``5.0`` differ as they do there."""

    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _handler_changes(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[str]:
    """Field differences between two readings of one event's handlers (#819).

    With the same number of handlers, handler N is compared with handler N
    and each differing field is named with its before and after. Otherwise
    the handlers only one side declares are listed as removed or added, since
    nothing establishes which of them another replaced. Values compare as the
    JSON publishes them: a timeout of ``5`` and one of ``5.0`` are two values
    there, and the row names both.
    """

    parts: list[str] = []
    old_json, new_json = list(map(_published_json, before)), list(map(_published_json, after))
    if len(before) == len(after) and old_json != new_json and sorted(old_json) == sorted(new_json):
        return ["the same handlers in a different order"]
    if len(before) == len(after):
        several = len(after) > 1
        for index, (old, new) in enumerate(zip(before, after, strict=True), start=1):
            for field in _HANDLER_FIELDS:
                if _published_json(old.get(field)) != _published_json(new.get(field)):
                    label = f"handler {index} {field}" if several else field
                    parts.append(
                        f"{label} {_handler_value(field, old.get(field))} → "
                        f"{_handler_value(field, new.get(field))}"
                    )
        return parts
    remaining = list(zip(new_json, after, strict=True))
    removed: list[dict[str, Any]] = []
    for text, handler in zip(old_json, before, strict=True):
        match = next((pair for pair in remaining if pair[0] == text), None)
        if match is not None:
            remaining.remove(match)
        else:
            removed.append(handler)
    added = [handler for _text, handler in remaining]
    for sign, handlers in (("-", removed), ("+", added)):
        for handler in handlers:
            facts = ", ".join(_handler_facts(handler)) or "no matcher, command or timeout"
            parts.append(f"{sign}handler ({facts})")
    return parts


def _hook_change(event: str, before: dict[str, Any], after: dict[str, Any]) -> str | None:
    """What differs between two readings of one hook event, in its published handlers (#819).

    ``None`` when either reading does not publish handlers, as a ``0.6``
    grant or a saved baseline's grant does not, so it renders
    ``event → event`` as it did. The row exists
    because ``config_sha256`` changed; when no published field differs, the
    change is in something the handlers do not show, and the text says so
    rather than print the same handlers twice. When either side lists fewer
    handlers than it declares, or summarizes a command with fewer arguments
    than it has, the sentence says only the first ones were compared and names
    a handler or command argument past them among what it does not show
    (#819 review).
    """

    if "handlers" not in before or "handlers" not in after:
        return None
    old, new = before["handlers"], after["handlers"]
    if old is None or new is None:
        return f"{event}: {_HOOK_SHAPE_NOT_READ}"
    parts = _handler_changes(old, new)
    old_more, new_more = int(before.get("omitted_handlers") or 0), int(after.get("omitted_handlers") or 0)
    if old_more != new_more:
        parts.append(f"handlers past the first {len(new)}: {old_more} → {new_more}")
    if not parts:
        compared, past = "", ""
        if old_more or new_more:
            compared = f" of the first {len(new)} handlers"
            past = f"a handler past the first {len(new)}, "
        bounded = [
            handler["command"]
            for handler in (*old, *new)
            if isinstance(handler.get("command"), dict) and int(handler["command"].get("omitted_args") or 0)
        ]
        if bounded:
            past += f"a command argument past the first {len(bounded[0].get('args') or [])}, "
        return (
            f"{event}: no difference in the matcher, type, command summary or timeout{compared}; "
            f"the change is in a detail this output does not show, such as {past}a redacted or "
            "shortened word or another hook setting"
        )
    shown = parts[:_NAME_LIMIT]
    rest = len(parts) - len(shown)
    return f"{event}: " + "; ".join(shown) + (f"; and {rest} more" if rest else "")


def _permission_cell(value: str, grant: dict[str, Any] | None) -> str:
    if not grant or value == ABSENT or not grant.get("disposition"):
        return value
    return f"{grant['disposition']}: {value}"


def _link_rows(
    rows: list[CapabilityDiffRow],
    changes: list[dict[str, Any]],
    views: list[_RowView],
) -> list[_RowView]:
    """Join the rows of a replacement or move the engine established (#795).

    A replacement is exactly what `permission_rule_replacements` returns, the
    pairs whose direction the permission lattice decided. A move is the exact
    same rule text that left one disposition and arrived in one other, in one
    host and source, with no other removal or addition of that text there. A
    row both could claim joins the replacement, whose direction the engine
    decided; the move's other half stays its own row.
    """

    removals: dict[tuple[str, str, str, str], int] = {}
    additions: dict[tuple[str, str, str, str], int] = {}
    for index, change in enumerate(changes):
        before, after = change.get("baseline"), change.get("current")
        if before is not None and after is not None:
            continue  # A changed grant keeps its rule and disposition; it joins nothing.
        grant, sink = (before, removals) if after is None else (after, additions)
        if not grant or grant.get("kind") != "permission_rule":
            continue
        key = (str(grant["host"]), str(grant.get("source", "")), str(grant.get("disposition")), str(grant["rule"]))
        sink[key] = index
    linked: set[int] = set()
    joined = list(views)

    def join(first: int, second: int, direction: str, why: str) -> None:
        link = _Link(direction=direction, before=views[first].before, after=views[second].after, why=why)
        joined[first] = _RowView(before=views[first].before, after=views[first].after, link=link)
        joined[second] = _RowView(before=views[second].before, after=views[second].after, link=link)
        linked.update((first, second))

    for item in permission_rule_replacements(changes):
        gone = removals.get((item.host, item.source, "allow", item.before_rule))
        arrived = additions.get((item.host, item.source, "allow", item.after_rule))
        if gone is None or arrived is None:
            continue
        # The lattice's reading, in the reviewer's words: which rule covers which.
        reading = (
            "the new rule matches everything the old rule matched"
            if item.direction == "widened"
            else "the new rule matches only what the old rule matched"
        )
        join(gone, arrived, item.direction, f"{reading}; {rows[arrived].why}")

    by_text: dict[tuple[str, str, str], tuple[list[int], list[int]]] = {}
    for sink, side in ((removals, 0), (additions, 1)):
        for (host, source, _disposition, rule), index in sink.items():
            by_text.setdefault((host, source, rule), ([], []))[side].append(index)
    for gone_indexes, arrived_indexes in by_text.values():
        if len(gone_indexes) != 1 or len(arrived_indexes) != 1:
            continue
        gone, arrived = gone_indexes[0], arrived_indexes[0]
        old = changes[gone]["baseline"].get("disposition")
        new = changes[arrived]["current"].get("disposition")
        if gone in linked or arrived in linked or not old or not new:
            continue
        join(gone, arrived, "moved", f"the same rule moved from {old} to {new}; {rows[arrived].why}")
    return joined


def capability_diff_rows(
    payload: dict[str, Any], *, redact_permission_arguments: bool = False
) -> list[CapabilityDiffRow]:
    """Every typed grant change in ``payload``, one row each."""

    expansions = set(payload.get("expansion_signals") or [])
    rows: list[CapabilityDiffRow] = []
    views: list[_RowView] = []
    changes: list[dict[str, Any]] = []
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
        gone_secrets, new_secrets = _secret_mapping_changes(before_grant, after_grant)
        why = _why(
            grant, direction,
            gone_steps=gone_steps, new_steps=new_steps,
            gone_secrets=gone_secrets, new_secrets=new_secrets,
        )
        row = CapabilityDiffRow(
            subject=_subject(grant),
            before=_grant_value(
                before_grant,
                redact_permission_arguments=redact_permission_arguments,
                step_actions=gone_steps,
                secret_mappings=gone_secrets,
            ),
            after=_grant_value(
                after_grant,
                redact_permission_arguments=redact_permission_arguments,
                step_actions=new_steps,
                secret_mappings=new_secrets,
            ),
            direction=direction,
            why=why,
            severity=str(grant.get("risk") or "unknown"),
            expands=expands,
            # A permission grant's identity is its disposition and its rule, so
            # a row with both sides has one disposition; every other kind has
            # none. Published, unlike the cells below, because it is a fact of
            # the grant and not a rendering of it (#795 follow-up).
            disposition=(
                str(grant["disposition"])
                if grant.get("kind") == "permission_rule" and grant.get("disposition")
                else None
            ),
        )
        kind = grant.get("kind")
        if kind == "permission_rule":
            view = _RowView(
                before=_permission_cell(row.before, before_grant),
                after=_permission_cell(row.after, after_grant),
            )
        elif kind == "mcp_server" and before_grant and after_grant:
            view = _RowView(
                before=row.before,
                after=row.after,
                change=_mcp_change(row.after, before_grant, after_grant),
            )
        elif kind == "mcp_server":
            view = _RowView(
                before=_mcp_cell(row.before, before_grant),
                after=_mcp_cell(row.after, after_grant),
            )
        elif kind == "hook" and before_grant and after_grant:
            view = _RowView(
                before=row.before,
                after=row.after,
                change=_hook_change(row.after, before_grant, after_grant),
            )
        elif kind == "hook":
            view = _RowView(
                before=_hook_cell(row.before, before_grant),
                after=_hook_cell(row.after, after_grant),
            )
        else:
            view = _RowView(before=row.before, after=row.after)
        rows.append(row)
        views.append(view)
        changes.append(change)
    if not redact_permission_arguments:
        # Redacted rules read alike, so a joined `allow: Bash(<redacted-arguments>)
        # → allow: Bash(<redacted-arguments>)` would show a change whose sides
        # look identical. Those routes keep the removal and addition as two rows.
        views = _link_rows(rows, changes, views)
    for row, view in zip(rows, views, strict=True):
        object.__setattr__(row, _VIEW, view)
    return sorted(
        rows,
        key=lambda row: (_SEVERITY_ORDER.get(row.severity, 9), row.subject, row.after),
    )


#: Most severe first: a reviewer reads the top of a table, so the ordering
#: is part of the answer rather than a presentation detail.
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


__all__ = [
    "ABSENT",
    "CapabilityDiffRow",
    "ReviewChange",
    "capability_diff_rows",
    "review_changes",
    "review_question",
]
