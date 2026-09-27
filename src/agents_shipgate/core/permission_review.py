"""Conditional human questions from established shell declarations (#839).

This module receives reader facts, never rendered cells. It neither selects a
replacement nor decides authority: the comparator supplies the proven relation.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from agents_shipgate.core.permission_lattice import subsumes
from agents_shipgate.core.privacy import redact_text
from agents_shipgate.schemas.host_comparison import PermissionReviewGuidance

# Deliberately bounded to literal commands and terminal prefix patterns. Shell
# operators, quoting, substitutions, interior globs and private projections are
# not evidence from which this entry offers a specific choice. The bare tool
# name is the whole tool, as `Bash(*)` is.
_RULE = re.compile(r"Bash(?:\((?:\*|[A-Za-z0-9_./:+=-]+(?: [A-Za-z0-9_./:+=-]+)*(?: \*|:\*)?)\))?\Z")
_RUNTIME_LIMIT = (
    "These are repository declarations. Runtime access, credentials, other permission "
    "layers and task intent are not established."
)


def _supported(rule: object) -> bool:
    return (
        isinstance(rule, str) and len(rule) <= 512 and bool(_RULE.fullmatch(rule))
        and redact_text(rule) == rule
    )


def permission_review_guidance(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    *,
    relation: str,
    before_grants: Sequence[dict[str, Any]],
    after_grants: Sequence[dict[str, Any]],
    redacted: bool = False,
    incomplete_context: bool = False,
) -> PermissionReviewGuidance | None:
    """Project one established subject; absent means this is not a shell case.

    Inventory context includes unchanged rules. Unknown or conflicting context
    withholds specific choices, rather than inventing effective access. No
    command, patch, verdict or permission is produced here.
    """

    grants = [grant for grant in (before, after) if grant is not None]
    if not grants or any(
        grant.get("host") != "claude-code"
        or grant.get("kind") != "permission_rule"
        or not str(grant.get("rule", "")).startswith("Bash")
        for grant in grants
    ):
        return None

    source = grants[0].get("source")
    displayable = not (
        not isinstance(source, str) or not source or len(source) > 512
        or redact_text(source) != source
        or any(ord(char) < 32 or char in "<>" for char in source)
        or any(grant.get("source") != source for grant in grants)
    )

    def withheld(reason: str) -> PermissionReviewGuidance:
        # The source is a path, not rule arguments: once established it is
        # still named, so the reader knows where to look (#839 review).
        return PermissionReviewGuidance(
            case="unavailable", source=source if displayable else None,
            limitations=[reason, _RUNTIME_LIMIT],
        )

    if redacted:
        return withheld("Rule arguments are redacted; specific guidance is unavailable.")
    if any(not _supported(grant.get("rule")) for grant in grants):
        return withheld("Rule evidence is unsupported, private or too long; specific guidance is unavailable.")
    if not displayable:
        return withheld("One exact, displayable source identity is not established.")
    if any(grant.get("disposition") not in {"allow", "deny"} for grant in grants):
        return withheld("This choice supports allow and deny declarations only.")
    evidence = dict(
        source=source,
        before_rule=before["rule"] if before else None,
        after_rule=after["rule"] if after else None,
        before_disposition=before["disposition"] if before else None,
        after_disposition=after["disposition"] if after else None,
    )
    if incomplete_context:
        return PermissionReviewGuidance(
            case="unavailable", **evidence,
            limitations=[
                "Relevant comparison context is incomplete; consult the named coverage limits. "
                "A resolver is not established by this guidance.", _RUNTIME_LIMIT,
            ],
        )
    for side, selected, inventory in (
        ("base", before, before_grants), ("head", after, after_grants),
    ):
        if selected is None:
            continue
        for context in inventory:
            if context.get("host") != "claude-code":
                continue
            # The inventory's broad category also holds MCP enablement. Those
            # settings do not change Bash rule matching or shell disposition.
            # Keep unknown settings as a limit rather than guessing relevance.
            mode = (
                context.get("kind") == "permission_mode"
                and context.get("setting") not in {
                    "enableAllProjectMcpServers", "enabledMcpjsonServers",
                }
            )
            other_disposition = (
                context.get("kind") == "permission_rule"
                and context.get("disposition") != selected["disposition"]
                and str(context.get("rule", "")).startswith(("Bash", "*"))
            )
            if not mode and not other_disposition:
                continue
            if other_disposition and _supported(context.get("rule")) and (
                subsumes(selected["rule"], context["rule"]) is False
                and subsumes(context["rule"], selected["rule"]) is False
            ):
                continue
            return PermissionReviewGuidance(
                case="unavailable", **evidence,
                limitations=[
                    f"The {side} inventory contains a host permission setting or a potentially "
                    "overlapping rule with another disposition. Resolve that context before "
                    "choosing scope; effective access is not established here.", _RUNTIME_LIMIT,
                ],
            )

    if before and after:
        if relation in {"widened", "narrowed"} and (
            before["disposition"] == after["disposition"] == "allow"
        ):
            case = "allow_widened" if relation == "widened" else "allow_narrowed"
            question = (
                f"Does this task need the command matches of {after['rule']} beyond {before['rule']}?"
                if relation == "widened" else
                f"Is restricting this allow declaration from {before['rule']} to {after['rule']} intended?"
            )
        elif relation == "moved" and before["rule"] == after["rule"]:
            case = "disposition_moved"
            question = (
                f"Is moving {before['rule']} from {before['disposition']} "
                f"to {after['disposition']} intended for this task?"
            )
        else:
            return withheld("A supported replacement relation is not established.")
    else:
        grant = after or before
        assert grant is not None
        added = after is not None
        case = "declaration_added" if added else "declaration_removed"
        question = (
            f"Should this source {'add' if added else 'remove'} the "
            f"{grant['disposition']} declaration {grant['rule']} for this task?"
        )
    return PermissionReviewGuidance(
        case=case, **evidence, question=question,
        choices=[
            "If intentional, record the rationale in the existing PR discussion; the declaration delta remains visible.",
            "If another scope is intended, the declaration owner chooses it at the named source. "
            + ("The previous value is a reference, not an automatically safe fix."
               if before else "This addition has no previous declaration to restore."),
            "If disputed, preserve the compared refs and evidence for triage; do not suppress the finding or rewrite policy to clear it.",
        ],
        limitations=[_RUNTIME_LIMIT],
        verification="Compare the resulting declarations against the original base; a rerun establishes a declaration delta, not intent or runtime access.",
    )
