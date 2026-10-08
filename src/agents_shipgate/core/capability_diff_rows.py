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
facts, package and argument digest, a hook's published handlers (#819), and
one change for a replacement or move the engine established (#795).

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

from agents_shipgate.core.claude_permission_rules import is_carve_out
from agents_shipgate.core.hook_command_shape import MAX_COMMAND_CHARS
from agents_shipgate.core.hook_matcher_reach import NO_TOOL_NAME
from agents_shipgate.core.host_grants import (
    _PLAIN_TOKEN_RE,
    AGENT_RULE_INPUTS,
    DETAIL_NOT_SHOWN,
    HOOK_HANDLER_SETTINGS,
    MAX_SHAPE_COMMANDS,
    MAX_SHAPE_REDIRECTS,
    UNTRUSTED_INPUT_TRIGGERS,
    PermissionRuleAssessment,
    PermissionRuleReplacement,
    _permission_key,
    agent_launch_key,
    agent_rule_gains,
    agent_rule_text,
    checkout_ref_key,
    hook_dependency_only_change,
    hook_loading_basis,
    hook_runs_for_no_tool_call,
    host_grant_direction_unknown,
    host_grant_expansion_signals,
    local_reusable_target,
    permission_rule_assessment,
    permission_rule_replacements,
    published_setting_value,
    published_workflow_label,
    pull_request_code_ref,
    reusable_call_target,
    secret_mapping_key,
    step_action_key,
)
from agents_shipgate.core.host_settings import rate_claude_setting, setting_value_text
from agents_shipgate.core.mcp_host_selection import UNATTRIBUTED_MCP_HOST
from agents_shipgate.core.openshell_compare import compare_openshell_grants
from agents_shipgate.core.openshell_row_detail import (
    openshell_policy_change,
    openshell_policy_declarations,
)
from agents_shipgate.core.permission_lattice import (
    exec_equivalent_argument,
    parse_rule,
    permission_pairing_group,
    same_grant,
    subsumes,
)
from agents_shipgate.core.permission_residual import residual_prefix_note
from agents_shipgate.core.unread_inputs import UnreadMcpDeclaration
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


def _job_entry_changes(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    field: str,
    key: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The entries of ``field`` only one side of a changed workflow declares (#823).

    Compared by ``key``, the way the comparator decides whether the grant
    changed, so a renamed or reordered step appears on neither side.
    """

    if not _is_workflow_pair(before, after):
        return [], []

    def only_in(side: list[dict[str, Any]], other: list[dict[str, Any]]) -> list[dict[str, Any]]:
        surplus = Counter(key(item) for item in side) - Counter(key(item) for item in other)
        picked: list[dict[str, Any]] = []
        for item in side:
            if surplus[key(item)] > 0:
                surplus[key(item)] -= 1
                picked.append(item)
        return picked

    old, new = (before or {}).get(field, []), (after or {}).get(field, [])
    return only_in(old, new), only_in(new, old)


def _agent_label(agent: str) -> str:
    """How a reviewer recognises the agent a step launches."""

    return {"claude": "claude -p", "codex": "codex exec"}.get(agent, agent)


def _agent_launch_value(item: dict[str, Any]) -> str:
    """One agent launch as a cell shows it: where, which agent, and its declared settings."""

    where = f"{item['job']}/{item['step']}"
    label = _agent_label(str(item["agent"]))
    reason = item.get("unresolved_reason")
    if reason:
        return f"{where}: runs {label} (unresolved: {str(reason).replace('_', ' ')})"
    cli = item["agent"] in {"claude", "codex"}
    parts = []
    for setting in item.get("settings") or []:
        unread = setting.get("unresolved_reason")
        name = str(setting["name"])
        if unread == "unread_arguments":
            # Compared by its digest alone, so the cell shows the digest (#823 review cycle 4).
            parts.append(f"{name} (not read; digest {setting['value']})")
        # A redacted value is compared as published, so the cell shows it (#823 review F2).
        elif unread and not (unread == "redacted" and setting.get("value") is not None):
            parts.append(f"{name} (unresolved: {str(unread).replace('_', ' ')})")
        elif setting.get("value") is None:
            parts.append(name)
        else:
            parts.append(f"{name} {setting['value']}" if cli else f"{name}: {setting['value']}")
    if not parts:
        return f"{where}: runs {label} with no permission {'flags' if cli else 'inputs'}"
    return f"{where}: runs {label} with " + "; ".join(parts)


def _checkout_ref_value(item: dict[str, Any]) -> str:
    where = f"{item['job']}/{item['step']}"
    reason = item.get("unresolved_reason")
    if reason:
        return f"{where}: checkout ref (unresolved: {str(reason).replace('_', ' ')})"
    if item.get("ref") is None:
        return f"{where}: checkout of the default ref"
    return f"{where}: checkout of ref {item['ref']}"


def _agent_launch_reasons(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    gone: list[dict[str, Any]],
    new: list[dict[str, Any]],
    gone_checkouts: list[dict[str, Any]],
    new_checkouts: list[dict[str, Any]],
) -> list[str]:
    """What changed in how an agent is launched, and which of it widens (#823).

    Only a documented rule the engine claims — ``agent_rule_gains(...).claimed``,
    the rule behind ``workflow_agent_widened_*`` — is called a widening, and
    the sentence says which rule and where. A rule gained where a step of the
    job this audit does not read is gone, or whose launch held, in an
    input the rule is read from, a ``${{ }}`` expression or an argument list
    this audit does not read, or that moved in from another job, is named and
    not called a widening, as the engine claims no expansion for it. Every other
    agent-launch or checkout edit is a change: its settings are compared as
    published text, and nothing here ranks one value against another.
    """

    def where(item: dict[str, Any]) -> str:
        return f"{item['job']}/{item['step']}"

    gains = agent_rule_gains(before, after)
    reasons: list[str] = []
    # Launches a sentence about a rule already names.
    named: set[str] = set()
    for _job, rule, detail, entry in gains.claimed:
        named.add(where(entry))
        reasons.append(f"an agent launch now {agent_rule_text(rule, detail)} ({where(entry)})")
    for (_job, rule, detail, entry), source in gains.unread_before:
        named.add(where(entry))
        reasons.append(
            f"an agent launch now {agent_rule_text(rule, detail)} ({where(entry)}), which is not counted as a "
            f"widening: a step in this job that may launch the agent in a form this audit does not read "
            f"is gone ({where(source)}), and this launch may be that step rewritten in a form this audit "
            "reads, which may already have done the same"
        )
    for (_job, rule, detail, entry), setting, how in gains.setting_before:
        named.add(where(entry))
        held = (
            "held a `${{ }}` expression, whose substituted text this audit does not read"
            if how == "expression"
            else "was not a plain list of words this audit reads, so no rule was read from it"
        )
        reasons.append(
            f"an agent launch now {agent_rule_text(rule, detail)} ({where(entry)}), which is not counted as a "
            f"widening: before, this job's {setting} {held}, and it may already have done the same"
        )
    for (_job, rule, detail, entry), source in gains.moved:
        named.update({where(entry), where(source)})
        reasons.append(
            f"an agent launch that {agent_rule_text(rule, detail)} moved between jobs ({where(source)} → "
            f"{where(entry)}), which is not counted as a widening: the launch already met that "
            "rule in the job it left, and it now runs with the receiving job's token permissions"
        )
    # The step label only words the sentence; what changed was decided by the
    # comparator's key, which never reads it.
    old = {where(item) for item in gone}
    now = {where(item) for item in new}
    groups: dict[str, list[str]] = {}
    for item in (*new, *gone):
        label = where(item)
        if label in named:
            continue
        verb = "changed" if label in old and label in now else ("added" if label in now else "removed")
        groups.setdefault(verb, [])
        if label not in groups[verb]:
            groups[verb].append(label)
    phrases = [
        f"{wording} ({', '.join(groups[verb])})"
        for verb, wording in (
            ("changed", "an agent launch's declared settings changed"),
            ("added", "a step now launches an agent"),
            # What was established is that the step declares no launch this
            # audit reads, not that it starts no agent (#823 review).
            ("removed", "a step no longer declares an agent launch this audit reads"),
        )
        if verb in groups
    ]
    if phrases:
        reasons.append(
            f"{_joined_words(phrases)}; agent launch settings are compared as declared text, "
            "and a change that gains no documented widening rule is not counted as a widening"
        )
    if "removed" in groups and after is not None:
        reasons.append(
            "a step that no longer declares one may still start an agent in a way this audit "
            "does not read, such as an action outside its table, a script, or a `run:` this "
            "audit does not read as a launch (more than one command, quoting, an expansion, `npx`, "
            "`codex` options before `exec`), so this row does not say that it no longer starts one"
        )
    # A rule is read only from literal text a `${{ }}` expression cannot
    # reach, so the row says where that leaves text unread (#823 review).
    expressions = list(dict.fromkeys(
        f"{setting['name']} at {where(item)}"
        for item in new if item.get("form") == "read"
        for setting in item.get("settings") or []
        if setting.get("holds_expression") and setting["name"] in AGENT_RULE_INPUTS
    ))
    if expressions:
        reasons.append(
            "an agent launch setting holds a `${{ }}` expression (" + ", ".join(expressions) + "), "
            "which GitHub substitutes before the action reads it; documented widening rules are "
            "read only from the literal text the expression cannot reach, so this row does not "
            "say whether the text it reaches meets one"
        )
    # An argument input that is not a plain list of words is compared by its
    # digest and read for no rule (#823 review cycle 4).
    unread_arguments = list(dict.fromkeys(
        f"{setting['name']} at {where(item)}"
        for item in new
        for setting in item.get("settings") or []
        if setting.get("unresolved_reason") == "unread_arguments"
    ))
    if unread_arguments:
        reasons.append(
            "an agent launch's argument input is not a plain list of words this audit reads ("
            + ", ".join(unread_arguments) + "); none of its text is published and it is compared by "
            "a digest only, so this row does not say whether it meets a documented widening rule"
        )
    unread = list(dict.fromkeys(where(item) for item in new if item.get("form") != "read"))
    if unread:
        reasons.append(
            f"a step launches an agent in a form this audit does not read ({', '.join(unread)}); "
            "its settings are not compared, so this row does not say what that agent may do"
        )
    # A checkout step on one side only, such as one in an added job, is
    # worded as added or removed, not as a changed ref (#823 review cycle 3).
    old_checkouts = {where(item) for item in gone_checkouts}
    new_checkouts_at = {where(item) for item in new_checkouts}
    checkout_groups: dict[str, list[str]] = {}
    for item in (*new_checkouts, *gone_checkouts):
        label = where(item)
        verb = (
            "changed" if label in old_checkouts and label in new_checkouts_at
            else ("added" if label in new_checkouts_at else "removed")
        )
        checkout_groups.setdefault(verb, [])
        if label not in checkout_groups[verb]:
            checkout_groups[verb].append(label)
    checkout_phrases = [
        f"{wording} ({', '.join(checkout_groups[verb])})"
        for verb, wording in (
            ("changed", "a checkout's declared ref changed"),
            ("added", "a step now declares a checkout"),
            ("removed", "a step no longer declares a checkout"),
        )
        if verb in checkout_groups
    ]
    if checkout_phrases:
        reasons.append(
            f"{_joined_words(checkout_phrases)}; a ref names which commit's code the job runs "
            "and adds no scope"
        )
    return reasons


#: How many agent steps one note names before counting the rest.
_NOTE_STEP_LIMIT = 5


def _agent_composition_note(grant: dict[str, Any]) -> str | None:
    """The job facts beside each agent step, as a note on the workflow's row (#823).

    Named, never scored: an untrusted-input trigger, the job's write scopes,
    the secrets the job references and a checkout of pull request code in the
    job. It is not a verdict and moves no direction; it says where on the
    workflow an agent already runs with those facts.
    """

    launches = grant.get("agent_launches") or []
    if not launches:
        return None
    triggers = [name for name in grant.get("triggers", []) if name in UNTRUSTED_INPUT_TRIGGERS]
    contexts = {context["job"]: context for context in grant.get("permission_contexts", [])}
    by_job: dict[str, list[dict[str, Any]]] = {}
    for item in launches:
        by_job.setdefault(str(item["job"]), []).append(item)
    notes: list[str] = []
    named = 0
    for job, items in by_job.items():
        shown = items[: max(0, _NOTE_STEP_LIMIT - named)]
        if not shown:
            break
        named += len(shown)
        steps = ", ".join(
            f"{item['job']}/{item['step']} ({_agent_label(str(item['agent']))})" for item in shown
        )
        facts: list[str] = []
        if triggers:
            noun = "trigger" if len(triggers) == 1 else "triggers"
            facts.append(f"the untrusted-input {noun} {_joined_words(triggers)}")
        context = contexts.get(job) or {}
        writes = [scope for scope, level in (context.get("permissions") or {}).items() if level == "write"]
        if "*" in writes:
            facts.append("write-all token permissions")
        elif writes:
            noun = "scope" if len(writes) == 1 else "scopes"
            facts.append(f"the write {noun} {_joined_words(writes)}")
        secrets = sorted({name for item in items for name in item.get("job_secrets", [])})
        if secrets:
            noun = "secret" if len(secrets) == 1 else "secrets"
            facts.append(f"the {noun} {_joined_words(secrets)}")
        pull_request_code = [
            f"{checkout['job']}/{checkout['step']}"
            for checkout in grant.get("checkout_refs", [])
            if checkout["job"] == job and pull_request_code_ref(checkout.get("ref"))
        ]
        if pull_request_code:
            facts.append(f"a checkout of pull request code ({', '.join(pull_request_code)})")
        note = f"an agent runs at {steps}"
        if facts:
            note += " beside " + _joined_words(facts)
        notes.append(note)
    rest = len(launches) - named
    if rest:
        notes.append(f"{rest} more agent step(s) run in this workflow")
    return "; ".join(notes)


def _joined_words(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"

#: Direction is deliberately coarse here. Presence is certain: a grant is
#: in one side and not the other. *Width* is not — deciding that
#: `Bash(npm *)` -> `Bash(npm test:*)` narrows needs the pattern lattice in
#: #657, so a change the engine has not called an expansion is reported as
#: `changed`, not guessed to be narrowing.
ADDED = "added"
REMOVED = "removed"
WIDENED = "widened"
CHANGED = "changed"
#: A joined change only: a removed and an added rule Claude Code documents as
#: one grant, such as `Bash(git add:*)` and `Bash(git add *)` (#918).
RESPELLED = "respelled"
#: The words a row's `why` ends with when the engine cannot establish which
#: way an edit that may widen authority went (#820): an MCP or loaded-hook
#: edit, an unestablished plugin enablement, or a setting no documented rule
#: orders. The row is named, never silent, and the Claude Code Stop hook,
#: which renders this text at install time, announces it beside widenings.
DIRECTION_UNKNOWN = "authority direction is unknown"


#: Why a same-repository called workflow's own permissions were not read (#921).
_CALLEE_NOT_READ = {
    "not_read": "was not read on this side",
    "limited": "has a limit this audit cannot compare past",
    "cycle": "calls back into its own chain of calls",
    "too_deep": "is past the depth of calls this audit follows",
}


def _write_scopes_text(scopes: Sequence[str]) -> str:
    return "write-all" if "*" in scopes else ", ".join(f"{scope}: write" for scope in sorted(scopes))


def _callee_suffix(call: dict[str, Any]) -> str:
    """What reading a same-repository callee established, beside its call (#921).

    Shown on the call, so a change to the called workflow's restrictions alone
    reads as a change on the caller's row and not as identical sides.
    """

    status = call.get("callee_permissions")
    if status is None:
        return ""
    if status == "read":
        scopes = call.get("callee_write_scopes") or []
        return f" (called jobs may hold {_write_scopes_text(scopes)})" if scopes else " (called jobs hold no write scope)"
    return " (called workflow not read)"


def _ceiling_reasons(grant: dict[str, Any]) -> list[str]:
    """A calling job's write scopes, said as the ceiling they are (#921).

    The scopes a job that calls a reusable workflow declares bound what the
    called workflow's jobs may hold; GitHub lets the called workflow keep or
    reduce them, never raise them. Whether its jobs hold them is stated only
    where the called workflow's own permissions were read.
    """

    contexts = {str(context["job"]): context for context in grant.get("permission_contexts") or []}
    reasons: list[str] = []
    for call in grant.get("reusable_calls") or []:
        context = contexts.get(str(call["job"]))
        if context is None or context["state"] != "explicit":
            continue
        ceiling = [scope for scope, level in context["permissions"].items() if level == "write"]
        if not ceiling:
            continue
        lead = (
            f"{call['job']}'s permissions are a ceiling for the workflow it calls "
            f"({_write_scopes_text(ceiling)})"
        )
        status = call.get("callee_permissions")
        if status == "read":
            reached = call.get("callee_write_scopes") or []
            if not reached:
                reasons.append(
                    f"{lead}; that workflow's own permissions give none of its jobs a write scope from it"
                )
            elif sorted(reached) == sorted(ceiling):
                reasons.append(f"{lead}; a job in that workflow may hold all of it")
            else:
                reasons.append(
                    f"{lead}; a job in that workflow may hold {_write_scopes_text(reached)}, "
                    "and its own permissions withhold the rest"
                )
            continue
        if status is None and local_reusable_target(str(call["uses"]), "") is None:
            why_unread = "is in another repository and is not read"
        else:
            why_unread = _CALLEE_NOT_READ.get(str(status), "was not read")
        reasons.append(
            f"{lead}; that workflow {why_unread}, so whether its jobs hold them is not established"
        )
    return reasons


#: One job's reusable call on each side of a changed workflow: job, before, after.
CallChange = tuple[str, str, str]


def _reusable_call_changes(
    before: dict[str, Any] | None, after: dict[str, Any] | None
) -> list[CallChange]:
    """The jobs that call a different reusable target or reference on each side (#924)."""

    if not _is_workflow_pair(before, after):
        return []
    assert before is not None and after is not None
    old = {str(call["job"]): str(call["uses"]) for call in before.get("reusable_calls", [])}
    new = {str(call["job"]): str(call["uses"]) for call in after.get("reusable_calls", [])}
    return [(job, old[job], new[job]) for job in sorted(old.keys() & new.keys()) if old[job] != new[job]]


def _call_change_reasons(changes: list[CallChange]) -> list[str]:
    """A changed reusable reference, said as code named differently, never as authority (#924)."""

    repinned = [item for item in changes if reusable_call_target(item[1]) == reusable_call_target(item[2])]
    retargeted = [item for item in changes if item not in repinned]
    reasons: list[str] = []
    if repinned:
        # The workflow is the same, so only its two references are printed.
        pairs = ", ".join(
            f"{job}: {old.rpartition('@')[2]} → {new.rpartition('@')[2]}" for job, old, new in repinned
        )
        reasons.append(
            f"the called code reference changed ({pairs}); it adds no declared scope or secret, "
            "and this audit compares references as text, so it establishes neither what either "
            "one runs nor that they run the same code"
        )
    if retargeted:
        pairs = ", ".join(f"{job}: {old} → {new}" for job, old, new in retargeted)
        reasons.append(f"the called workflow changed ({pairs})")
    return reasons


def _openshell_cell(cell: str, grant: dict[str, Any] | None) -> str:
    """A wholly added or removed OpenShell policy, with what it declares beside its digest (#968).

    The counts and the facts digest stay, so the cell still identifies the
    document; the declarations are display only and imply no direction.
    """

    declared = openshell_policy_declarations(grant)
    return f"{cell}; declared: {declared}" if declared else cell


def _grant_value(
    grant: dict[str, Any] | None,
    *,
    redact_permission_arguments: bool = False,
    step_actions: list[dict[str, Any]] | None = None,
    secret_mappings: list[SecretMapping] | None = None,
    agent_launches: list[dict[str, Any]] | None = None,
    checkout_refs: list[dict[str, Any]] | None = None,
) -> str:
    """What a reader recognises this grant by.

    A workflow has no single name — its authority *is* the combination of
    access and triggers, so both sides render that combination or the row
    reads "workflow -> workflow" and says nothing. ``step_actions``,
    ``secret_mappings``, ``agent_launches`` and ``checkout_refs`` are the
    entries this side alone declares; unchanged ones are not repeated.
    """

    if not grant:
        return ABSENT
    kind = str(grant.get("kind") or "")
    if kind == "openshell_policy":
        facts = grant["facts"]
        policy = facts["policy"]
        endpoints = sum(len(rule["endpoints"]) for rule in policy["network_policies"].values())
        return f"{facts['role']}, OpenShell {facts['runtime_version']}, {endpoints} endpoint(s), facts {grant['config_sha256']}"
    if kind == "permission_rule" and redact_permission_arguments:
        from agents_shipgate.core.host_boundary import _safe_rule

        return _safe_rule(str(grant.get("rule") or ""))
    if kind == "workflow":
        access = str(grant.get("access") or "")
        parts = [f"access: {access}"] if access else []
        if grant.get("write_all"):
            parts.append("write-all")
        calling = {str(call["job"]) for call in grant.get("reusable_calls") or []}
        if "permission_contexts" in grant:
            for context in grant["permission_contexts"]:
                # A calling job runs no step: its scopes are a ceiling for the
                # workflow it calls, not its own token (#921).
                ceiling = "ceiling " if str(context["job"]) in calling else ""
                if context["state"] != "explicit":
                    reason = "repository defaults" if context["state"] == "repository_default" else "unresolved permissions"
                    parts.append(f"{context['job']}: {ceiling}{reason} (unknown)")
                elif not context["permissions"]:
                    parts.append(f"{context['job']}: {ceiling}no token permissions")
                else:
                    for scope, level in context["permissions"].items():
                        permission = f"{level}-all" if scope == "*" else f"{scope}: {level}"
                        parts.append(f"{context['job']}: {ceiling}{permission}")
        else:
            parts.extend(str(scope) for scope in grant.get("write_scopes") or [])
        if grant.get("pull_request_target"):
            parts.append("pull_request_target")
        other_triggers = [name for name in grant.get("triggers", []) if name != "pull_request_target"]
        if other_triggers:
            parts.append("on: " + ", ".join(other_triggers))
        for call in grant.get("reusable_calls") or []:
            forwarding = "secrets: inherit → " if call.get("secrets_inherit") else "uses: "
            parts.append(f"{call['job']}: {forwarding}{call['uses']}{_callee_suffix(call)}")
        parts.extend(_secret_mapping_value(item) for item in secret_mappings or [])
        parts.extend(_step_action_value(item) for item in step_actions or [])
        parts.extend(_checkout_ref_value(item) for item in checkout_refs or [])
        parts.extend(_agent_launch_value(item) for item in agent_launches or [])
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


#: Why an MCP row names no host (#936): its file is published under
#: ``unknown`` because no declaration this entry reads selects it.
MCP_HOST_NOT_ESTABLISHED = (
    "host not established: no declaration this entry reads selects this file, "
    "and another host's plugin manifest sits beside it"
)


#: The `why` of a removed MCP server.
MCP_REMOVED_WHY = "an MCP tool surface is no longer offered to the agent"
#: The `why` of a removed MCP server while a changed input this entry does
#: not read may declare MCP servers for it (#929): what the removal
#: establishes is about the read declaration, not about what is offered.
MCP_REMOVED_UNREAD_DECLARATION = (
    "the server is no longer declared in this source; whether it is still offered "
    "through a changed declaration this entry does not read is not established"
)


def _unread_declaration_may_offer(
    grant: dict[str, Any], declarations: Sequence[UnreadMcpDeclaration]
) -> bool:
    """Whether a changed, unread MCP declaration shares this removed server's host and plugin scope (#929).

    One does when both hold:

    - **plugin scope**: the removed server's file lies inside the
      declaration's plugin directory, that directory or one below it (the
      repository root holds every file), compared case-insensitively as #936
      selects a `.mcp.json`; and
    - **host**: the declaration's coverage item names the server's host, or
      the server was published under ``unknown`` (#936), whose host is not
      established to differ.

    Paths are the published ones on both sides; a path component redaction
    rewrote matches neither, so such a row keeps its wording. The declaration
    is never read, matched by server name or paired with the row.
    """

    host = str(grant.get("host") or "")
    path = str(grant.get("source") or "").split("#", 1)[0].casefold()
    directory = path.rpartition("/")[0]
    for declaration in declarations:
        root = declaration.root.casefold()
        if root and directory != root and not directory.startswith(root + "/"):
            continue
        if host == UNATTRIBUTED_MCP_HOST or host in declaration.hosts:
            return True
    return False


def _subject(grant: dict[str, Any]) -> str:
    host = str(grant.get("host") or "")
    source = str(grant.get("source") or "")
    kind = str(grant.get("kind") or "")
    if host == UNATTRIBUTED_MCP_HOST and source:
        # A host-neutral subject (#936): `unknown` is not a host to name.
        return source
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
    agent_reasons: list[str] | None = None,
    call_changes: list[CallChange] | None = None,
) -> str:
    """Why a reviewer should care, in the reviewer's terms.

    Stated as what the grant *permits*, never as a prediction about what the
    agent will do with it — the engine reads configuration, not behaviour.
    A workflow that launches an agent ends with the job facts beside each
    agent step (#823), read off ``grant``, the side the row describes.
    """

    kind = str(grant.get("kind") or "")
    access = str(grant.get("access") or "")
    wildcard = bool(grant.get("wildcard"))
    if kind == "mcp_server":
        if direction == REMOVED:
            return MCP_REMOVED_WHY
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
        if exec_equivalent_argument(str(grant.get("rule") or "")) is not None:
            return "reaches arbitrary code through a launcher, without a prompt"
        if wildcard and access == "admin":
            return "matches any command of this kind, without a prompt"
        if wildcard:
            return "matches every target of this kind, without a prompt"
        return "runs without a prompt"
    if kind == "workflow":
        reasons = []
        contexts = grant.get("permission_contexts")
        # The event context, the job tokens and a calling job's ceiling are
        # three facts, said apart (#920, #921): a read-only token under
        # `pull_request_target` is not a write grant.
        if grant.get("pull_request_target"):
            reasons.append("uses the privileged pull_request_target event context")
        if contexts is None:
            # A legacy grant names its write scopes and nothing about jobs.
            if access in {"admin", "write"} or grant.get("write_all"):
                reasons.append("grants write permissions to workflow jobs")
        else:
            calling = {str(call["job"]) for call in grant.get("reusable_calls") or []}
            if any(
                context["state"] == "explicit" and str(context["job"]) not in calling
                and "write" in context["permissions"].values()
                for context in contexts
            ):
                reasons.append("grants write permissions to workflow jobs")
            if grant.get("pull_request_target") and contexts and all(
                context["state"] == "explicit" and "write" not in context["permissions"].values()
                for context in contexts
            ):
                reasons.append("every job declares read-only or no token permissions")
            if grant.get("pull_request_target") and any(
                context["state"] == "repository_default" for context in contexts
            ):
                reasons.append(
                    "a job that declares no token permissions may run with the token GitHub "
                    "documents as read/write for pull_request_target runs, even from a fork"
                )
            reasons.extend(_ceiling_reasons(grant))
        if any(context["state"] != "explicit" for context in contexts or []):
            reasons.append("some effective token permissions are unknown; repository defaults or unresolved declarations require review")
        reasons.extend(_call_change_reasons(call_changes or []))
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
        reasons.extend(agent_reasons or [])
        why = "; ".join(reasons) or "changes the workflow's own authority"
        # A removed workflow runs nothing any more, so it gets no note.
        note = None if direction == REMOVED else _agent_composition_note(grant)
        return f"{why}; {note}" if note else why
    if kind == "hook":
        # The basis, stated in the row, because the row is what a reviewer
        # reads: a parsed hook file is not proof a host loads it (#714).
        basis = hook_loading_basis(grant)
        # A hook no tool name can trigger changes nothing around the agent's
        # tool calls; the matcher note beside this names why (#940).
        runs = (
            _NO_TOOL_CALL
            if direction != REMOVED and hook_runs_for_no_tool_call(grant)
            else "changes what runs around the agent's actions"
        )
        if basis == "host_configuration":
            return runs
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
                f"{runs}; this repository's project settings enable the plugin that "
                "selects this hook"
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


def review_grant_evidence(
    rows: Sequence[CapabilityDiffRow], indexes: Sequence[int],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None] | None:
    """Reader identities for a proven review group, before serialization (#839).

    Never recover these from display labels. The comparison serializes the
    guidance derived here; plain reloaded rows have no raw evidence.
    """
    evidence = [getattr(rows[index], "_grant_evidence", None) for index in indexes]
    if not evidence or any(item is None for item in evidence):
        return None
    before = [item[0] for item in evidence if item[0] is not None]
    after = [item[1] for item in evidence if item[1] is not None]
    if len(before) > 1 or len(after) > 1:
        return None
    return (before[0] if before else None, after[0] if after else None)


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
    (`8 repository-declared host capability row(s)`), and `diff`'s summary
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


#: The declaration fact for an `npx` package named with no version (#933).
NO_EXACT_VERSION = "package spec has no exact version"
#: What that declaration leaves open, as npm documents `npx` resolving it
#: (https://docs.npmjs.com/cli/v11/commands/npm-exec#description). Said as a
#: limit of this read, not as registry code selected on every launch.
NPX_LOCAL_OR_REGISTRY = (
    "launch resolution not established: npx may resolve a local project dependency "
    "or fall back to the registry/cache"
)


def _mcp_source_note(before: dict[str, Any] | None, after: dict[str, Any] | None) -> str | None:
    source = (after or {}).get("launch_source")
    if not source or source.get("pin") != "mutable":
        return None
    old = (before or {}).get("launch_source") or {}
    def label(value: dict[str, Any]) -> str:
        package = value.get("package")
        return f" ({published_workflow_label(str(package))})" if package else ""
    if source.get("resolution") == "local_project_or_registry":
        # The observed declaration and the launch it leaves open, said apart
        # (#933): an unversioned `npx` package is not "mutable" on its own.
        if old.get("pin") == "pinned":
            return (
                f"launch source moved from pinned{label(old)} to a package spec with no "
                f"exact version{label(source)}; {NPX_LOCAL_OR_REGISTRY}"
            )
        return f"{NO_EXACT_VERSION}{label(source)}; {NPX_LOCAL_OR_REGISTRY}"
    if old.get("pin") == "pinned":
        return f"launch source moved from pinned{label(old)} to mutable{label(source)}"
    return f"launch source is mutable{label(source)}"


#: The `why` of an added or changed hook whose every matcher matches no tool name (#940).
_NO_TOOL_CALL = "declares a hook no tool call can trigger"


def _unmatched_matcher_note(grant: dict[str, Any] | None) -> str | None:
    """The matchers of a hook grant that can match no tool name, named (#940).

    Read from the ``matcher_reach`` the engine published on each handler,
    never re-derived from the published matcher, which redaction and the
    length bound may have changed. ``None`` when no handler's matcher is one.
    """

    if not grant or grant.get("kind") != "hook" or not isinstance(grant.get("handlers"), list):
        return None
    matchers = sorted({
        published_workflow_label(str(handler.get("matcher")))
        for handler in grant["handlers"]
        if isinstance(handler, dict) and handler.get("matcher_reach") == NO_TOOL_NAME
    })
    if not matchers:
        return None
    shown = ", ".join(matchers[:3])
    if len(matchers) > 3:
        shown += f" (+{len(matchers) - 3} more)"
    label = f"matcher {shown}" if len(matchers) == 1 else f"matchers {shown}"
    if hook_runs_for_no_tool_call(grant):
        partial = ""
    else:
        partial = f", so {'its' if len(matchers) == 1 else 'their'} handlers run for no tool call"
    return (
        f"{label} can match no tool name{partial} (Claude Code compares a "
        f"{grant.get('event')} matcher with the tool's name; a permission rule "
        "pattern belongs in a handler's if field)"
    )


def _inline_allow_note(grant: dict[str, Any] | None) -> str | None:
    if (
        not grant or grant.get("host") != "claude-code" or grant.get("event") != "PreToolUse"
        or hook_loading_basis(grant) not in {"host_configuration", "project_enabled_plugin"}
    ):
        return None
    matchers = sorted({
        published_workflow_label(str(handler.get("matcher") or "all"))
        for handler in grant.get("handlers") or [] if handler.get("inline_allow") is True
    })
    if not matchers:
        return None
    shown = ", ".join(matchers[:3])
    if len(matchers) > 3:
        shown += f" (+{len(matchers) - 3} more)"
    return f"inline allow auto-approves matched tool calls without a prompt (matcher {shown}); host exceptions and deny/ask rules still apply"


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


def _more(count: int, noun: str) -> str:
    return f" (+{count} more {noun}{'s' if count != 1 else ''})" if count else ""


def _digest_text(digest: Any) -> str:
    """A published digest as text prints it: its first twelve hex digits (#819)."""

    return f"sha256:{str(digest)[:12]}" if digest else "none"


def _mcp_cell(value: str, grant: dict[str, Any] | None) -> str:
    """An added or removed MCP server with the launch facts its grant publishes."""

    if not grant or value == ABSENT:
        return value
    facts = [
        fact
        for fact in (
            _mcp_launch(grant),
            f"package {grant['package']}" if grant.get("package") else None,
            "env keys " + _names(_key_names(grant["env_keys"])) if grant.get("env_keys") else None,
            "header keys " + _names(_key_names(grant["header_keys"])) if grant.get("header_keys") else None,
        )
        if fact
    ]
    return f"{value} ({'; '.join(facts)})" if facts else value


#: Every published MCP fact a changed row compares (#795).
_MCP_FIELDS = ("transport", "endpoint", "env_keys", "header_keys")


def _mcp_args_change(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """The difference in two readings' published package and argument digest (#819).

    Nothing when either reading does not publish them, as a grant from a
    ``0.6`` snapshot or a saved baseline does not. The digest stands for
    every argument but the package, so an edit to the package alone names
    the package alone.
    """

    if "args_sha256" not in before or "args_sha256" not in after:
        return []
    parts: list[str] = []
    old, new = before.get("package"), after.get("package")
    if old != new:
        parts.append(f"package {old or '(none shown)'} → {new or '(none shown)'}")
    old, new = before.get("args_sha256"), after.get("args_sha256")
    if old != new:
        parts.append(f"launch arguments changed ({_digest_text(old)} → {_digest_text(new)})")
    return parts


def _mcp_change(name: str, before: dict[str, Any], after: dict[str, Any]) -> str | None:
    """What differs between two readings of one MCP server, in its published fields.

    ``None`` when either side does not publish every field, so a grant read
    from an older snapshot is never described by a difference it cannot show.
    A command's path and other settings are not published, so a change
    confined to them says what was compared and that the change is
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
    parts.extend(_mcp_args_change(before, after))
    for field, label in (("env_keys", "env keys"), ("header_keys", "header keys")):
        old, new = set(before[field] or []), set(after[field] or [])
        added, removed = sorted(new - old), sorted(old - new)
        if added or removed:
            tokens = [f"+{key}" for key in _key_names(added)] + [f"-{key}" for key in _key_names(removed)]
            parts.append(f"{label} {_names(tokens)}")
    if not parts:
        compared = all("args_sha256" in grant for grant in (before, after))
        return f"{name}: {_mcp_unshown_change(after, args_compared=compared)}"
    return f"{name}: " + "; ".join(parts)


def _mcp_unshown_change(grant: dict[str, Any], *, args_compared: bool = False) -> str:
    """A change confined to what the grant does not publish, in the words of what was compared.

    Only the command's name, or the URL's recorded value, the launch
    arguments (#819: the package and the digest of the rest), and the env and
    header key names are compared. `npx` → `./npx` and `/usr/local/bin/node`
    → `./scripts/node` change the command while its name stays the same, so
    the sentence names the command's path as what this output does not show.
    The digest binds every argument as ``config_sha256``'s input holds it, so
    once the arguments are compared no argument is named as unshown. A URL
    that is not printed is named `url as recorded`, never by its value, and a
    URL server that declares no arguments is not said to have compared them.
    A grant read before the arguments were published names them as not
    shown, as it did.
    """

    launch = _mcp_launch(grant)
    arguments = (
        "launch arguments, "
        if args_compared and (grant.get("transport") != "url" or grant.get("args_sha256") is not None)
        else ""
    )
    if grant.get("transport") == "url":
        compared = "url as recorded" if _mcp_endpoint(grant) == _URL_NOT_SHOWN else launch or "url"
        unshown = "the URL's query or another setting"
    else:
        compared = launch or "command name"
        unshown = "the command's path or another setting" if arguments else "the command's path or arguments"
    return (
        f"no difference in the {compared}, {arguments}env key names or header key names; the "
        f"change is in a detail this output does not show, such as {unshown}"
    )


#: How many hook handlers an added or removed hook's cell lists before counting.
_HANDLER_LIMIT = 3

#: Why a hook declaration published no handler: it is not in the shape the
#: reader establishes (#819).
_HOOK_SHAPE_REASON = (
    "the declaration is not a list of matcher groups whose hooks are objects and whose "
    "commands are strings"
)
#: A hook handler's published fields, in the order a row names them: its
#: group's matcher, its command, its ``args`` and timeout (#819, #972), and the
#: documented settings the reader publishes (#971, #972).
_HANDLER_FIELDS = ("matcher", "command", "args", "timeout", *HOOK_HANDLER_SETTINGS)

#: What a hook row says when its declaration is outside that shape.
_HOOK_SHAPE_NOT_READ = f"matcher, command, args, timeout and settings not shown: {_HOOK_SHAPE_REASON}"

#: What a row says it compared when no published handler field differs.
_HOOK_COMPARED = "the " + ", ".join(_HANDLER_FIELDS[:-1]) + f" or {_HANDLER_FIELDS[-1]}"

#: What a hook's published handlers do not show, and so where a change the
#: rows cannot name may be (#819). The command and the arguments are digested
#: whole, so no part of either is among them; a setting such as ``if`` or
#: ``statusMessage`` is not published (#972).
_HOOK_UNSHOWN = (
    "the if or statusMessage field or another field not published, or a matcher, timeout or "
    "setting published redacted or shortened"
)
#: A handler's cell when it publishes no field.
_NO_HANDLER_FACTS = "no matcher, command, args, timeout or setting"


def _command_text(command: dict[str, Any]) -> str:
    """A published hook command as one line: its executable's name and its digest, never its text (#819)."""

    return f"{command.get('executable') or DETAIL_NOT_SHOWN} {_digest_text(command.get('sha256'))}"


#: Why a hook command is not described, in the words a row uses (#934).
_SHAPE_LIMIT_TEXT = {
    "too_long": f"is longer than {MAX_COMMAND_CHARS:,} characters",
    "unsupported_syntax": "uses shell syntax this output does not describe",
    "unsupported_shell": "is written for a shell this output does not describe",
}
#: The counts a command's shape publishes, with the words a row names them by.
_SHAPE_COUNTS = (
    ("statements", "simple commands"),
    ("pipes", "pipes"),
    ("substitutions", "command substitutions"),
    ("control_flow", "conditionals and loops"),
    ("quoted", "quoted strings"),
    ("unnamed", "commands with no plain name"),
)
_OPEN_THE_CONFIG = "open the config to read the change"


def _shape_limit_text(limit: Any) -> str:
    return _SHAPE_LIMIT_TEXT.get(str(limit), "is not described")


def _plain_shape(command: dict[str, Any], *, ignore_script: bool = False) -> bool:
    """Whether a described command is one program with arguments only (#934).

    One command, no pipe, substitution, conditional or redirect, a program
    name the row already prints as its executable, and (unless
    ``ignore_script``) no script path: a shape would repeat what
    ``executable`` says.
    """

    shape = command.get("shape")
    if not isinstance(shape, dict):
        return False
    names = list(shape.get("commands") or [])
    executable = command.get("executable")
    keys = ("pipes", "substitutions", "control_flow", "redirects") + (() if ignore_script else ("script",))
    return (
        shape.get("statements") == 1
        and not any(shape.get(key) for key in keys)
        and (names == [executable] or (not names and shape.get("unnamed") == 1))
    )


def _shape_summary(command: dict[str, Any]) -> str:
    """What an added or removed hook's command is made of, beyond its first word (#934).

    Empty for a plain command whose program name and digest already say all
    its shape would: one program, no script path, pipe, redirect,
    substitution or conditional. A command that is not described says so, and
    that the config has to be opened to read it.
    """

    limit = command.get("shape_limit")
    if limit:
        return f"not described: it {_shape_limit_text(limit)}; open the config to read it"
    shape = command.get("shape")
    if not isinstance(shape, dict) or _plain_shape(command):
        return ""
    names = list(shape.get("commands") or [])
    items: list[str] = []
    if _plain_shape(command, ignore_script=True):
        # One program and its script path: the executable already names the program.
        return f"script {shape['script']}"
    if names:
        items.append("runs " + ", ".join(names) + _more(int(shape.get("commands_more") or 0), "other program"))
    if shape.get("unnamed"):
        items.append(_count(int(shape["unnamed"]), "command") + " with no plain name")
    if shape.get("script"):
        items.append(f"script {shape['script']}")
    for key, noun in (("pipes", "pipe"), ("substitutions", "command substitution")):
        if shape.get(key):
            items.append(_count(int(shape[key]), noun))
    if shape.get("control_flow"):
        items.append(_count(int(shape["control_flow"]), "conditional or loop keyword"))
    redirects = list(shape.get("redirects") or [])
    if redirects:
        total = len(redirects) + int(shape.get("redirects_more") or 0)
        shown = redirects[:_NAME_LIMIT]
        items.append(f"{_count(total, 'redirect')} ({', '.join(shown)}{', …' if total > len(shown) else ''})")
    return "; ".join(items)


def _count(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _shape_differences(old: dict[str, Any], new: dict[str, Any], *, programs: bool = True) -> list[str]:
    """How two described commands differ in what a shape publishes (#934), in a reviewer's words.

    ``programs`` is false when one program replaced another, which the
    executables already say.
    """

    facts: list[str] = []
    old_names, new_names = list(old.get("commands") or []), list(new.get("commands") or [])
    gained = [name for name in new_names if name not in old_names]
    lost = [name for name in old_names if name not in new_names]
    if programs and (gained or lost):
        shown = [*(f"+{name}" for name in gained), *(f"-{name}" for name in lost)]
        facts.append(
            "programs " + ", ".join(shown[:_NAME_LIMIT]) + _more(max(0, len(shown) - _NAME_LIMIT), "other change")
        )
    if int(old.get("commands_more") or 0) != int(new.get("commands_more") or 0):
        facts.append(
            f"programs past the first {MAX_SHAPE_COMMANDS}: "
            f"{int(old.get('commands_more') or 0)} → {int(new.get('commands_more') or 0)}"
        )
    if old.get("script") != new.get("script"):
        facts.append(f"script {old.get('script') or '(none)'} → {new.get('script') or '(none)'}")
    for key, noun in _SHAPE_COUNTS:
        before, after = int(old.get(key) or 0), int(new.get(key) or 0)
        if before != after:
            facts.append(f"{noun} {before} → {after}")
    old_redirects, new_redirects = list(old.get("redirects") or []), list(new.get("redirects") or [])
    if old_redirects != new_redirects or old.get("redirects_more") != new.get("redirects_more"):
        gained_redirects = [item for item in new_redirects if item not in old_redirects]
        lost_redirects = [item for item in old_redirects if item not in new_redirects]
        shown = [*(f"+{item}" for item in gained_redirects), *(f"-{item}" for item in lost_redirects)]
        facts.append(
            "redirects " + (", ".join(shown[:_NAME_LIMIT]) or "reordered or past the first "
                            f"{MAX_SHAPE_REDIRECTS}")
            + _more(max(0, len(shown) - _NAME_LIMIT), "other change")
        )
    if facts and new_names and new_names == old_names:
        facts.insert(0, "same programs (" + ", ".join(new_names) + ")")
    return facts


def _command_shape_change(old: dict[str, Any], new: dict[str, Any]) -> str:
    """What the shapes of two commands add to ``command changed``, or nothing for a grant that has none (#934).

    The digests moved, so the commands differ. When both are described and
    differ in what a shape publishes, the differences are named. When they do
    not, or when either is not described, the change is in text this output
    does not show, and the row says the config has to be opened to read it.
    """

    old_limit, new_limit = old.get("shape_limit"), new.get("shape_limit")
    old_shape, new_shape = old.get("shape"), new.get("shape")
    if not (old_limit or isinstance(old_shape, dict)) or not (new_limit or isinstance(new_shape, dict)):
        return ""
    if old_limit or new_limit:
        reasons = [
            f"{side} command {_shape_limit_text(limit)}"
            for side, limit in (("base", old_limit), ("head", new_limit))
            if limit
        ]
        return f"; not described: {' and '.join(reasons)}; the digest moved, {_OPEN_THE_CONFIG}"
    # One program replaced by another: the executables already say so.
    replaced = (
        _plain_shape(old, ignore_script=True) and _plain_shape(new, ignore_script=True)
        and old.get("executable") != new.get("executable")
    )
    facts = _shape_differences(old_shape, new_shape, programs=not replaced)
    if replaced and not facts:
        return ""
    if facts:
        return "; " + "; ".join(facts)
    return (
        "; same programs and structure; the change is in an argument or in quoted text this "
        f"output does not show, {_OPEN_THE_CONFIG}"
    )


def _args_text(args: dict[str, Any]) -> str:
    """A hook's published ``args`` as one line: the script path it publishes and its digest (#972)."""

    return f"{args.get('script') or DETAIL_NOT_SHOWN} {_digest_text(args.get('sha256'))}"


def _args_changes(label: str, old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """The difference in two readings of one handler's ``args`` (#972).

    The digest stands for every argument but the published script, so an edit
    to the script alone names the script alone, as an MCP server's package is
    named (:func:`_mcp_args_change`).
    """

    parts: list[str] = []
    old_script, new_script = old.get("script"), new.get("script")
    if old_script != new_script:
        parts.append(f"{label} script {old_script or '(none shown)'} → {new_script or '(none shown)'}")
    if old.get("sha256") != new.get("sha256"):
        parts.append(
            f"{label} changed ({_digest_text(old.get('sha256'))} → {_digest_text(new.get('sha256'))})"
        )
    return parts


def _handler_value(field: str, value: Any) -> str:
    """A published handler field as a row prints it (#819).

    A timeout, and a setting (#971, #972), is printed as its JSON reads, so
    one written as text is quoted and ``5`` → ``"5"`` or ``true`` →
    ``"true"`` never reads as the same value twice (#819 review, cycles 5 and
    6). Only the bounded text of an integer too long to publish, and
    ``<not-shown>``, are printed bare: neither is a plain token, so no string
    value is published as either.
    """

    if value is None:
        return "(none)"
    if field == "command":
        summary = _shape_summary(value)
        return _command_text(value) + (f" ({summary})" if summary else "")
    if field == "args":
        return _args_text(value)
    if field == "matcher" and value == "":
        return '""'
    if field in _JSON_HANDLER_FIELDS and (
        not isinstance(value, str) or _PLAIN_TOKEN_RE.fullmatch(value)
    ):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


#: The handler fields printed as their JSON reads.
_JSON_HANDLER_FIELDS = frozenset({"timeout", *HOOK_HANDLER_SETTINGS})


def _handler_facts(handler: dict[str, Any]) -> list[str]:
    """What one published handler declares, in a reviewer's words.

    A handler whose command is published is a command handler, so its
    ``type "command"`` is not repeated beside it; any other type is named.
    """

    return [
        f"{field} {_handler_value(field, handler.get(field))}"
        for field in _HANDLER_FIELDS
        if handler.get(field) is not None
        and not (field == "type" and handler[field] == "command" and handler.get("command"))
    ]


def _hook_cell(value: str, grant: dict[str, Any] | None) -> str:
    """An added or removed hook with the handlers its grant publishes (#819).

    A grant read before handlers were published renders its event alone, as it did.
    """

    if not grant or value == ABSENT or "handlers" not in grant:
        return value
    if grant["handlers"] is None:
        return f"{value} ({_HOOK_SHAPE_NOT_READ})"
    return f"{value} ({_listed_handlers(grant)})"


def _listed_handlers(grant: dict[str, Any]) -> str:
    """The handlers a grant publishes, as a cell lists them: at most three, then a count (#819)."""

    handlers = grant["handlers"]
    total = len(handlers) + int(grant.get("omitted_handlers") or 0)
    if not total:
        return "no handlers"
    if total == 1 and handlers:
        return "; ".join(_handler_facts(handlers[0])) or f"a handler with {_NO_HANDLER_FACTS}"
    listed = [
        f"handler {index}: {', '.join(_handler_facts(handler)) or _NO_HANDLER_FACTS}"
        for index, handler in enumerate(handlers[:_HANDLER_LIMIT], start=1)
    ]
    return "; ".join(listed) + _more(total - len(listed), "handler")


def _published_json(value: Any) -> str:
    """A published value as its JSON reads, so ``5`` and ``5.0`` differ as they do there."""

    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _compared_json(field: str, value: Any) -> str:
    """A handler field as two readings of it are compared (#934).

    A command's shape is read from the same text its digest is, so it adds
    no difference of its own; it is rendered beside a change of the command.
    A shell setting that moves it is its own difference, named on its own.
    """

    if field == "command" and isinstance(value, dict):
        return _published_json({key: value.get(key) for key in ("executable", "sha256")})
    return _published_json(value)


def _handler_changes(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[str] | None:
    """Field differences between two readings of one event's handlers (#819).

    ``None`` when the published handlers are the same ones in a different
    order. With the same number of handlers, handler N is compared with
    handler N and each differing field is named with its before and after; a
    command by its executable's name and digest, since its text is never
    published, and ``args`` by the script path and digest they publish
    (#972). Otherwise the handlers only one side declares are listed as
    removed or added, since nothing establishes which of them another
    replaced. Values compare as the JSON publishes them: a timeout of ``5``
    and one of ``5.0`` are two values there, and the row names both.
    """

    parts: list[str] = []
    old_json, new_json = list(map(_published_json, before)), list(map(_published_json, after))
    if len(before) == len(after) and old_json != new_json and sorted(old_json) == sorted(new_json):
        return None
    if len(before) == len(after):
        several = len(after) > 1
        for index, (old, new) in enumerate(zip(before, after, strict=True), start=1):
            for field in _HANDLER_FIELDS:
                old_value, new_value = old.get(field), new.get(field)
                if _compared_json(field, old_value) == _compared_json(field, new_value):
                    continue
                label = f"handler {index} {field}" if several else field
                if field == "command" and old_value and new_value:
                    parts.append(
                        f"{label} changed ({_command_text(old_value)} → {_command_text(new_value)}"
                        f"{_command_shape_change(old_value, new_value)})"
                    )
                elif field == "args" and old_value and new_value:
                    parts.extend(_args_changes(label, old_value, new_value))
                else:
                    parts.append(
                        f"{label} {_handler_value(field, old_value)} → {_handler_value(field, new_value)}"
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
            facts = ", ".join(_handler_facts(handler)) or _NO_HANDLER_FACTS
            parts.append(f"{sign}handler ({facts})")
    return parts


def _changed_hook_scripts(
    before: dict[str, Any], after: dict[str, Any]
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """Each selected script whose reading differs, with both readings, once per path (#702).

    Only for a dependency-only change, whose declaration is identical on both
    sides, so the entries pair by position: a malformed group's entry can
    share a handler number with the handler after it.
    """

    changed: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    old, new = before.get("script_inputs") or [], after.get("script_inputs") or []
    for index in range(max(len(old), len(new))):
        left = old[index] if index < len(old) else {}
        right = new[index] if index < len(new) else {}
        if left != right:
            path = published_workflow_label(
                str(right.get("path") or left.get("path") or "unresolved path")
            )
            changed.setdefault(path, (left, right))
    return [(path, left, right) for path, (left, right) in changed.items()]


def hook_script_named_by(row: CapabilityDiffRow, path: str) -> bool:
    """Whether a dependency-only hook row names this script as changed (#702).

    The coverage text reads it, so a changed script whose change is on the
    declaring hook's row is not described as a change no row shows.
    """

    if not row.why.startswith(_HOOK_SCRIPT_WHY):
        return False
    named = row.why.split(";", 1)[0][len(_HOOK_SCRIPT_WHY):].strip()
    return path in named.split(", ")


_HOOK_SCRIPT_WHY = "selected script bytes changed:"


def _hook_dependency_change(before: dict[str, Any], after: dict[str, Any]) -> str:
    def digest(item: dict[str, Any]) -> str:
        return str(item.get("sha256") or item.get("limit") or "not read")[:64]

    parts = [
        f"script {path} bytes {digest(left)} → {digest(right)}"
        for path, left, right in _changed_hook_scripts(before, after)
    ]
    shown = "; ".join(parts[:3])
    if len(parts) > 3:
        shown += f"; {len(parts) - 3} more dependency changes"
    return shown


def _hook_change(event: str, before: dict[str, Any], after: dict[str, Any]) -> str | None:
    """What differs between two readings of one hook event, in its published handlers (#819).

    ``None`` when either reading does not publish handlers, as a ``0.6``
    grant or a saved baseline's grant does not, so it renders
    ``event → event`` as it did. The row exists because ``config_sha256``
    changed. When no published field differs, the change is in something the
    handlers do not show, and the text says so rather than print the same
    handlers twice. When the published handlers are the same ones in a
    different order, the text says so, and that a detail it does not show may
    differ too: equal published handlers never establish equal handlers,
    since a field such as ``if`` is not published (#819 review, cycle 4;
    #972). When either side lists fewer handlers than it declares, only the
    first ones were compared, and a handler past them is named among what is
    not shown (#819 review). When only one side's declaration is outside the
    documented shape, that side is named and the other side's handlers are
    listed as an added or removed hook's are, so a change that brings a
    declaration into the shape never reads as though the new one were outside
    it (#819 review, cycle 5).
    """

    if hook_dependency_only_change(before, after):
        return f"{event}: {_hook_dependency_change(before, after)}"
    if "handlers" not in before or "handlers" not in after:
        return None
    old, new = before["handlers"], after["handlers"]
    if old is None and new is None:
        return f"{event}: {_HOOK_SHAPE_NOT_READ}"
    if old is None or new is None:
        unread, read, grant = ("base", "head", after) if old is None else ("head", "base", before)
        return (
            f"{event}: {unread} matcher, command, args, timeout and settings not shown "
            f"({_HOOK_SHAPE_REASON}); "
            f"{read} ({_listed_handlers(grant)})"
        )
    changes = _handler_changes(old, new)
    parts = changes or []
    old_more, new_more = int(before.get("omitted_handlers") or 0), int(after.get("omitted_handlers") or 0)
    # A side that counts handlers past the bound lists exactly the bound, and
    # the other lists no more, so the longer list is the bound (#819 review).
    bound = max(len(old), len(new))
    if old_more != new_more:
        parts.append(f"handlers past the first {bound}: {old_more} → {new_more}")
    past = f"a handler past the first {bound}, " if old_more or new_more else ""
    if changes is None:
        parts.insert(
            0,
            "the published handlers in a different order; a detail this output does not show "
            f"may also differ, such as {past}{_HOOK_UNSHOWN}",
        )
    if not parts:
        compared = f" of the first {bound} handlers" if past else ""
        return (
            f"{event}: no difference in {_HOOK_COMPARED}{compared}; the change is "
            f"in a detail this output does not show, such as {past}{_HOOK_UNSHOWN}"
        )
    shown = parts[:_NAME_LIMIT]
    rest = len(parts) - len(shown)
    return f"{event}: " + "; ".join(shown) + (f"; and {rest} more" if rest else "")


def _permission_cell(value: str, grant: dict[str, Any] | None) -> str:
    if not grant or value == ABSENT or not grant.get("disposition"):
        return value
    return f"{grant['disposition']}: {value}"


_RESTRICTION_NOUN = {"deny": "denial", "ask": "confirmation requirement"}


def _rule_list(rules: Sequence[str], disposition: str) -> str:
    """Rules as the cells print them, at most ``_NAME_LIMIT`` before a count."""

    shown = [f"{disposition}: {rule}" for rule in list(rules)[:_NAME_LIMIT]]
    rest = len(rules) - len(shown)
    return ", ".join(shown) + (f" and {rest} more" if rest else "")


def _permission_rule_why(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    direction: str,
    assessment: PermissionRuleAssessment,
    *,
    redacted: bool,
    expands: bool,
    paired: frozenset[tuple[str, str, str]] | set[tuple[str, str, str]] = frozenset(),
) -> str | None:
    """The rule model's reading of one Claude Code rule row, or ``None`` (#918, #938, #969, #974).

    A route that redacts rule arguments names no other rule: it says what the
    model established, and leaves the rules to the cells it may print.
    ``paired`` is the arrivals of a narrowing the replacement pairing already
    explains, which keep their wording.
    """

    grant = after or before
    if not grant or grant.get("host") != "claude-code":
        return None
    key = _permission_key(grant)
    disposition = str(grant.get("disposition"))
    if key in assessment.unconsulted:
        tool = parse_rule(str(grant["rule"])).tool.strip()
        basis = "from Claude Code 2.1.210, file permission checks read Read(path) and Edit(path) rules only"
        if direction == REMOVED:
            lost = "permission" if disposition == "allow" else _RESTRICTION_NOUN.get(disposition, "rule")
            return (
                f"removes a path-scoped {tool} rule Claude Code never consulted ({basis}), "
                f"so no effective {lost} is removed"
            )
        return (
            f"Claude Code accepts a path-scoped {tool} rule but never consults it ({basis}), "
            "so it allows and restricts nothing"
        )
    if disposition in _RESTRICTION_NOUN and after is not None and _is_claude_carve_out(after):
        follows = after.get("carves_from")
        noun = _RESTRICTION_NOUN[disposition]
        if not follows:
            return (
                f"a carve-out listed before every {disposition} rule it could except paths from "
                "in this source, so it excepts nothing"
                if follows is not None else
                "a carve-out; which earlier rules of this source it follows was not recorded"
            )
        named = "" if redacted else f" ({_rule_list(follows, disposition)})"
        reading = (
            f"a carve-out: paths it matches are excepted from the earlier {disposition} "
            f"rules it follows in this source{named}"
        )
        if expands:
            return f"{reading}; it now excepts paths from a rule declared at the base, so part of a {noun} is lifted"
        return f"{reading}; it lifts no {noun} the base declared"
    if disposition in _RESTRICTION_NOUN and direction == REMOVED and _is_claude_carve_out(grant):
        follows = grant.get("carves_from")
        noun = "denied" if disposition == "deny" else "subject to confirmation"
        if follows:
            named = "" if redacted else f" from {_rule_list(follows, disposition)}"
            return f"removes a carve-out; the paths it excepted{named} are {noun} again"
        if follows is not None:
            return "removes a carve-out that excepted nothing: it was listed before every rule it could carve from"
        return f"removes a carve-out; whatever it excepted is {noun} again"
    twin = assessment.respelled.get(key)
    if twin is not None and direction == REMOVED:
        named = "" if redacted else f": {disposition}: {twin}"
        return f"removes this rule; the source still declares the same grant written another way{named}"
    cover = assessment.covered.get(key)
    if cover is not None and after is not None and (key[0], key[1], key[3]) not in paired:
        if same_grant(cover, str(after["rule"])):
            named = "" if redacted else f" as allow: {cover}"
            return f"the same grant this source already declared at the base{named}, written another way; adds nothing"
        named = (
            f"allow: {cover}, declared in this source at the base,"
            if not redacted else "an allow rule declared in this source at the base"
        )
        return f"runs without a prompt, but adds nothing: {named} already matches everything it matches"
    narrowed = assessment.narrowed_into.get(key)
    if narrowed and direction == REMOVED:
        named = "" if redacted else f": {_rule_list(narrowed, 'allow')}"
        return (
            f"removes this allow rule; it is narrowed to {len(narrowed)} added rule(s) "
            f"in this source that match only part of what it matched{named}"
        )
    return None


def _is_claude_carve_out(grant: dict[str, Any]) -> bool:
    return grant.get("host") == "claude-code" and is_carve_out(str(grant.get("rule") or ""))


def _carve_out_change(
    before: dict[str, Any] | None, after: dict[str, Any] | None, *, redacted: bool
) -> str | None:
    """A carve-out whose position changed: what it follows, then and now (#974)."""

    if not before or not after or not _is_claude_carve_out(after):
        return None
    old, new = before.get("carves_from"), after.get("carves_from")
    if old == new:
        return None
    if redacted:
        return "the earlier rules this carve-out follows changed"
    disposition = str(after.get("disposition"))

    def listed(rules: list[str] | None) -> str:
        if rules is None:
            return "not recorded"
        return _rule_list(rules, disposition) if rules else "no earlier rule"

    return f"{disposition}: {after['rule']} follows {listed(old)} → {listed(new)}"


def _link_rows(
    rows: list[CapabilityDiffRow],
    changes: list[dict[str, Any]],
    views: list[_RowView],
    replacements: list[PermissionRuleReplacement],
    assessment: PermissionRuleAssessment | None = None,
) -> list[_RowView]:
    """Join the rows of a replacement, respelling or move the engine established (#795).

    A replacement is exactly what `permission_rule_replacements` returns, the
    pairs whose direction the permission lattice decided. A respelling is a
    removed and an added rule of one host, source and disposition that
    Claude Code documents as one grant (`Bash(x:*)` and `Bash(x *)`), each the
    only such rule on its side (#918). A move is the exact
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

    for item in replacements:
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

    for gone_key, arrived_key in assessment.respelled_pairs if assessment else ():
        gone, arrived = removals.get(gone_key), additions.get(arrived_key)
        if gone is None or arrived is None or gone in linked or arrived in linked:
            continue
        reading = (
            "a trailing `:*` as a trailing ` *`"
            if gone_key[3].endswith(":*)") or arrived_key[3].endswith(":*)")
            else "`Bash(*)` as `Bash`"
        )
        join(
            gone, arrived, RESPELLED,
            f"the same grant written another way (Claude Code reads {reading}); it "
            "matches exactly what it matched and adds nothing",
        )

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
    payload: dict[str, Any], *, redact_permission_arguments: bool = False,
    current_grants: Sequence[dict[str, Any]] = (),
    unread_mcp_declarations: Sequence[UnreadMcpDeclaration] = (),
) -> list[CapabilityDiffRow]:
    """Every typed grant change in ``payload``, one row each.

    ``unread_mcp_declarations`` are the changed inputs of the same comparison
    no reader read that may declare MCP servers (#929). A removed MCP server
    sharing one's host and plugin scope says only that it is no longer
    declared in its source; it moves no direction, ``expands`` or severity.
    """

    expansions = set(payload.get("expansion_signals") or [])
    # One reading of every changed Claude Code rule against its own source,
    # shared with the drift signals above, the pairing and the wording (#918).
    assessment = permission_rule_assessment(payload.get("changes") or [], current_grants or None)
    replacements = permission_rule_replacements(
        payload.get("changes") or [], assessment=assessment
    )
    arrived_allows: dict[tuple[str, str], list[str]] = {}
    # Deny and ask rules are evaluated before allow in every settings file,
    # so one arriving for the same tool may take the matches away (#858).
    arrived_restrictions: dict[str, set[str]] = {}
    for change in payload.get("changes") or []:
        grant = change.get("current")
        if not grant or grant.get("kind") != "permission_rule":
            continue
        if not change.get("baseline") and grant.get("disposition") == "allow":
            key = (grant["host"], grant.get("source", ""))
            arrived_allows.setdefault(key, []).append(str(grant["rule"]))
        elif grant.get("disposition") in {"deny", "ask"}:
            arrived_restrictions.setdefault(grant["host"], set()).add(
                permission_pairing_group(str(grant["rule"])).lower()
            )
    narrowed = {
        (item.host, item.source, item.after_rule)
        for item in replacements
        if item.direction == "narrowed"
    }
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
        expands = bool(expansions.intersection(host_grant_expansion_signals(
            [change], comparison_changes=payload.get("changes") or [],
            assessment=assessment,
        )))
        # The public signal text has no source. An identical rule added in
        # another file must not mark this source's decided narrowing (#858).
        if (
            after_grant and after_grant.get("kind") == "permission_rule"
            and after_grant.get("disposition") == "allow"
            and (after_grant["host"], after_grant.get("source", ""), str(after_grant["rule"]))
            in narrowed
        ):
            expands = False
        if direction == CHANGED and expands:
            direction = WIDENED
        gone_steps, new_steps = _step_action_changes(before_grant, after_grant)
        gone_secrets, new_secrets = _secret_mapping_changes(before_grant, after_grant)
        gone_agents, new_agents = _job_entry_changes(
            before_grant, after_grant, "agent_launches", agent_launch_key
        )
        gone_checkouts, new_checkouts = _job_entry_changes(
            before_grant, after_grant, "checkout_refs", checkout_ref_key
        )
        agent_reasons = (
            _agent_launch_reasons(
                before_grant, after_grant, gone_agents, new_agents, gone_checkouts, new_checkouts
            )
            if grant.get("kind") == "workflow"
            else []
        )
        why = _why(
            grant, direction,
            gone_steps=gone_steps, new_steps=new_steps,
            gone_secrets=gone_secrets, new_secrets=new_secrets,
            agent_reasons=agent_reasons,
            call_changes=_reusable_call_changes(before_grant, after_grant),
        )
        kind = grant.get("kind")
        if kind == "openshell_policy":
            comparison = compare_openshell_grants(before_grant, after_grant)
            direction = comparison.direction if comparison.direction in {"widened", "narrowed"} else CHANGED
            why = comparison.explanation + "; declared policy only; runtime enforcement and freshness are unverified"
        if (
            kind == "plugin_or_app" and after_grant is not None
            and not str(after_grant.get("name", "")).startswith("marketplace:")
        ):
            if after_grant.get("enabled") is False:
                why = "declares this plugin or app disabled"
            elif expands:
                why = "enables this plugin or app"
        if not expands and host_grant_direction_unknown(
            change, comparison_changes=payload.get("changes") or [],
        ):
            # Named as such, never silent, and never assumed to narrow (#820).
            if kind == "mcp_server":
                why = "MCP edit"
            elif kind == "hook" and hook_loading_basis(grant) == "host_configuration":
                why = "hook edit"
            why += f"; {DIRECTION_UNKNOWN}"
        if (
            direction == REMOVED and grant.get("kind") == "permission_rule"
            and grant.get("disposition") == "allow"
            and any(
                subsumes(arrival, str(grant["rule"])) is True
                for arrival in arrived_allows.get((grant["host"], grant.get("source", "")), [])
            )
            and not arrived_restrictions.get(grant["host"], set()).intersection(
                {permission_pairing_group(str(grant["rule"])).lower(), "*"}
            )
        ):
            # Wording only: ambiguity still forbids a pair or signal suppression.
            why = "removes this allow rule; another added allow rule still covers its matches"
        if grant.get("kind") == "permission_rule" and (
            rule_why := _permission_rule_why(
                before_grant, after_grant, direction, assessment,
                redacted=redact_permission_arguments, expands=expands,
                paired=narrowed,
            )
        ):
            why = rule_why
        if hook_dependency_only_change(before_grant, after_grant):
            # The digests are the change cell (`_hook_change`); the why names
            # the scripts once, so the text does not print them twice.
            scripts = [path for path, _left, _right in _changed_hook_scripts(before_grant, after_grant)]
            named = ", ".join(scripts[:3]) + (f", {len(scripts) - 3} more" if len(scripts) > 3 else "")
            # A script the host runs changed: what that does to the agent's
            # authority is not established, and it is not silent (#820).
            why = (
                f"{_HOOK_SCRIPT_WHY} {named}; "
                f"declaration unchanged, selected by {hook_loading_basis(after_grant)}; "
                "compares file bytes only, not permissions or runtime behavior; "
                f"{DIRECTION_UNKNOWN}"
            )
        # The examples spell out the rule's prefix, which a route that
        # redacts rule arguments must not print beside the redacted rule.
        note = (
            None
            if redact_permission_arguments
            else residual_prefix_note(after_grant, current_grants)
        )
        if note:
            why = f"{why}; {note}"
        if why == MCP_REMOVED_WHY and _unread_declaration_may_offer(grant, unread_mcp_declarations):
            why = MCP_REMOVED_UNREAD_DECLARATION
        if grant.get("kind") == "mcp_server" and grant.get("host") == UNATTRIBUTED_MCP_HOST:
            why = f"{why}; {MCP_HOST_NOT_ESTABLISHED}"
        if grant.get("kind") == "mcp_server" and (note := _mcp_source_note(before_grant, after_grant)):
            why = f"{why}; {note}"
        if grant.get("kind") == "hook" and (note := _unmatched_matcher_note(after_grant)):
            why = f"{why}; {note}"
        if grant.get("kind") == "hook" and (note := _inline_allow_note(after_grant)):
            why = f"{why}; {note}"
        row = CapabilityDiffRow(
            subject=_subject(grant),
            before=_grant_value(
                before_grant,
                redact_permission_arguments=redact_permission_arguments,
                step_actions=gone_steps,
                secret_mappings=gone_secrets,
                agent_launches=gone_agents,
                checkout_refs=gone_checkouts,
            ),
            after=_grant_value(
                after_grant,
                redact_permission_arguments=redact_permission_arguments,
                step_actions=new_steps,
                secret_mappings=new_secrets,
                agent_launches=new_agents,
                checkout_refs=new_checkouts,
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
                change=_carve_out_change(
                    before_grant, after_grant, redacted=redact_permission_arguments
                ),
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
        elif kind == "openshell_policy" and before_grant and after_grant:
            view = _RowView(
                before=row.before,
                after=row.after,
                change=openshell_policy_change(before_grant, after_grant),
            )
        elif kind == "openshell_policy":
            view = _RowView(
                before=_openshell_cell(row.before, before_grant),
                after=_openshell_cell(row.after, after_grant),
            )
        else:
            view = _RowView(before=row.before, after=row.after)
        # Kept with the row through sorting, never emitted as row fields.
        # Only the bounded advisory projection is serialized by the comparator.
        object.__setattr__(row, "_grant_evidence", (before_grant, after_grant))
        rows.append(row)
        views.append(view)
        changes.append(change)
    if not redact_permission_arguments:
        # Redacted rules read alike, so a joined `allow: Bash(<redacted-arguments>)
        # → allow: Bash(<redacted-arguments>)` would show a change whose sides
        # look identical. Those routes keep the removal and addition as two rows.
        views = _link_rows(rows, changes, views, replacements, assessment)
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
