"""Bounded explanatory examples for #829; never a permission decision.

Documentation reviewed 2026-09-27:
https://code.claude.com/docs/en/permissions#wildcard-patterns
https://code.claude.com/docs/en/permissions#compound-commands
https://git-scm.com/docs/git-push

Claude matches command text, not Git option semantics. The sole trailing
wildcard includes the bare command and respects its preceding space; terminal
legacy :* has the same meaning. Compound commands require separate checks.
Only simple literal prefixes and fixed simple examples are admitted here.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from agents_shipgate.core.permission_lattice import subsumes

# No quotes, substitutions, shell operators, arbitrary globs or redaction
# markers. Reordered flags remain text, never normalized as equivalent rules.
_PREFIX = re.compile(r"Bash\((git push(?: [A-Za-z0-9_./:+=-]+)*)(?: \*|:\*)\)")

# git-push documents --delete / empty-source refspecs, forced +refspecs,
# conditional --force-with-lease, and forced/deleted mirrored refs. #829 also
# names a flag placed after the remote, which a prefix deny on `--force`
# never matches as text (matcher evidence, not a claim about Git's option
# parsing). These examples describe requests, not successful updates or
# runtime authorization.
_EXAMPLES = (
    "git push --delete origin main",
    "git push origin :main",
    "git push origin +main",
    "git push --force-with-lease origin main",
    "git push --mirror origin",
    "git push origin main --force",
)


def residual_prefix_note(
    grant: dict[str, Any] | None, current_grants: Sequence[dict[str, Any]]
) -> str | None:
    """Explain remaining examples only within one observed source's rules."""

    if not grant or (
        grant.get("host") != "claude-code"
        or grant.get("kind") != "permission_rule"
        or grant.get("disposition") != "allow"
        or not isinstance(grant.get("rule"), str)
        or not _PREFIX.fullmatch(grant["rule"])
        or not grant.get("source")
    ):
        return None
    rule = grant["rule"]
    denies = [
        item.get("rule")
        for item in current_grants
        if item.get("host") == grant["host"]
        and item.get("source") == grant["source"]
        and item.get("kind") == "permission_rule"
        and item.get("disposition") == "deny"
    ]
    if not denies or any(
        not isinstance(deny, str)
        or not _PREFIX.fullmatch(deny)
        or subsumes(rule, deny) is not True
        or subsumes(deny, rule) is not False
        for deny in denies
    ):
        return None
    examples = [
        command for command in _EXAMPLES
        if subsumes(rule, f"Bash({command})") is True
        and all(subsumes(deny, f"Bash({command})") is False for deny in denies)
    ]
    if not examples:
        return None
    return (
        "deny prefixes in this source do not cover all forms matched by this allow rule; "
        "examples: " + "; ".join(examples)
        + ". Other sources, ask rules, permission mode and runtime restrictions may still apply"
    )
