"""The declared facts a changed OpenShell policy row names for a human (#968).

The row's value cell is a count and a digest. A reviewer needs to know *which*
filesystem path left a list, which binary selector changed and which
destination or request declaration came or went, so this module reads that off
the same normalized ``facts`` the comparator reads and says it in words.

Presentation only. It decides no direction, expansion, severity or check
outcome: ``compare_openshell_grants`` still does, and its limits stay in the
row's ``why``. Every value printed is one the inventory already publishes — a
document whose policy text would change under redaction is never read into a
grant (``_openshell_public_value`` in ``host_grants``) — and each is passed
through that same sanitizer again, so a grant that reached here some other way
(an older saved baseline) still withholds a secret-shaped value. Nothing is
printed that the typed policy does not hold: no raw policy text, comment, rule
name, query value or credential option value.
"""

from __future__ import annotations

import re
from typing import Any

from agents_shipgate.core.host_grants import _openshell_public_value
from agents_shipgate.core.openshell_compare import _canonical

#: Filesystem paths, or changed binary selectors, one list names before counting the rest.
_ENTRY_LIMIT = 5
#: Destination entries one row names before counting the rest.
_NETWORK_LIMIT = 8
#: Binary selectors one destination entry names before counting the rest.
_BINARY_LIMIT = 3
#: Request rules one declaration names before counting the rest.
_RULE_LIMIT = 3
#: The longest value printed whole. The typed policy admits a 4096-byte path.
_VALUE_CHARS = 120

#: Said when a changed policy differs only in fields this output does not name.
_NOT_NAMED = (
    "no filesystem, Landlock, process, destination or request declaration differs "
    "in the fields this output names"
)

_REDACTED = "<redacted>"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

#: Endpoint options a reader is told are set, by name and never by value.
_OPTION_FIELDS = (
    "tls",
    "allow_encoded_slash",
    "websocket_credential_rewrite",
    "request_body_credential_rewrite",
    "allow_uninspected_credentials",
    "credential_signing",
    "signing_service",
    "signing_region",
    "credential_binding",
    "allowed_ips",
    "json_rpc",
    "mcp",
    "graphql_persisted_queries",
)
#: Request constraints a matcher carries beyond a method and a path, by name.
_CONSTRAINT_FIELDS = ("command", "query", "operation_type", "operation_name", "fields", "params")


def _value(raw: Any) -> str:
    """One published value as it may be printed: sanitized, one line, bounded."""

    text = str(raw)
    if _openshell_public_value(text) != text:
        return _REDACTED
    text = _CONTROL.sub(" ", text)
    return text if len(text) <= _VALUE_CHARS else text[: _VALUE_CHARS - 1] + "…"


def _list(tokens: list[str], limit: int) -> str:
    shown, rest = tokens[:limit], len(tokens) - limit
    return ", ".join(shown) + (f", and {rest} more" if rest > 0 else "")


def _entries(entries: list[str], limit: int) -> list[str]:
    """At most ``limit`` entries, then how many more there are."""

    shown, rest = entries[:limit], len(entries) - limit
    return shown + ([f"and {rest} more"] if rest > 0 else [])


def _signed(old: Any, new: Any, limit: int) -> list[str]:
    """``+added`` then ``-removed`` values, each side bounded on its own."""

    old_set, new_set = set(old or []), set(new or [])
    tokens: list[str] = []
    for sign, values in (("+", sorted(new_set - old_set)), ("-", sorted(old_set - new_set))):
        tokens.extend(f"{sign}{_value(item)}" for item in values[:limit])
        if len(values) > limit:
            tokens.append(f"and {len(values) - limit} more")
    return tokens


def _policy(grant: dict[str, Any] | None) -> dict[str, Any] | None:
    facts = (grant or {}).get("facts")
    policy = facts.get("policy") if isinstance(facts, dict) else None
    return policy if isinstance(policy, dict) else None


def _matcher(matcher: dict[str, Any]) -> str:
    parts = [str(matcher[key]) for key in ("method", "path") if matcher.get(key)]
    tool = matcher.get("tool")
    if isinstance(tool, dict):
        tool = "|".join(map(str, tool.get("any", [])[:_RULE_LIMIT]))
    if tool:
        parts.append(f"tool {tool}")
    named = [key for key in _CONSTRAINT_FIELDS if matcher.get(key)]
    text = _value(" ".join(parts) or "(no method or path)")
    return text + (f"[{', '.join(named)}]" if named else "")


def _declaration(endpoint: dict[str, Any]) -> str:
    """An endpoint's request declaration, without any value an option holds."""

    protocol = endpoint.get("protocol") or "l4"
    protocol = "l4" if protocol == "tcp" else protocol
    parts = [protocol]
    if protocol != "l4":
        parts.append(endpoint.get("enforcement") or "audit")
    if endpoint.get("access"):
        parts.append(str(endpoint["access"]))
    if endpoint.get("path"):
        parts.append(f"path {_value(endpoint['path'])}")
    allows = sorted(_matcher(rule["allow"]) for rule in endpoint.get("rules") or [])
    if allows:
        parts.append(f"allow {_list(allows, _RULE_LIMIT)}")
    denies = sorted(_matcher(rule) for rule in endpoint.get("deny_rules") or [])
    if denies:
        parts.append(f"deny {_list(denies, _RULE_LIMIT)}")
    options = [name for name in _OPTION_FIELDS if endpoint.get(name)]
    if endpoint.get("persisted_queries") not in {"", "deny", None}:
        options.append("persisted_queries")
    if endpoint.get("graphql_max_body_bytes", 65536) != 65536:
        options.append("graphql_max_body_bytes")
    if options:
        parts.append("options " + ", ".join(options))
    return "(" + ", ".join(parts) + ")"


def _destinations(endpoint: dict[str, Any]) -> list[str]:
    host = endpoint.get("host") or ",".join(sorted(endpoint.get("allowed_ips") or []))
    ports = endpoint.get("ports") or [endpoint.get("port")]
    return [f"{_value(host)}:{port}" for port in sorted(set(ports))]


#: destination -> declaration identity -> (declaration text, binary selectors)
_Groups = dict[str, dict[str, tuple[str, set[str]]]]


def _groups(policy: dict[str, Any]) -> _Groups:
    """Each endpoint declaration with the binary selectors of every rule that holds it."""

    groups: _Groups = {}
    for rule in (policy.get("network_policies") or {}).values():
        binaries = {str(item["path"]) for item in rule.get("binaries") or []}
        for endpoint in rule.get("endpoints") or []:
            identity = _canonical(endpoint)
            text = _declaration(endpoint)
            for destination in _destinations(endpoint):
                entry = groups.setdefault(destination, {}).setdefault(identity, (text, set()))
                entry[1].update(binaries)
    return groups


def _via(binaries: set[str]) -> str:
    if not binaries:
        return "no binary selector"
    return _list([_value(item) for item in sorted(binaries)], _BINARY_LIMIT)


def _merged(items: list[tuple[str, str, str]]) -> list[str]:
    """Destinations that changed alike are one entry: ``(label, destination, tail)``."""

    merged: dict[tuple[str, str], list[str]] = {}
    for label, destination, tail in items:
        merged.setdefault((label, tail), []).append(destination)
    entries = []
    for (label, tail), destinations in merged.items():
        noun = "destination" if len(destinations) == 1 else "destinations"
        entries.append(f"{label + ' ' if label else ''}{noun} {_list(destinations, _ENTRY_LIMIT)} {tail}")
    return entries


def _network_changes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    before, after = _groups(old), _groups(new)
    items: list[tuple[str, str, str]] = []
    for destination in sorted(before.keys() | after.keys()):
        left, right = before.get(destination, {}), after.get(destination, {})
        gone = {key: value for key, value in left.items() if key not in right}
        came = {key: value for key, value in right.items() if key not in left}
        for key in sorted(left.keys() & right.keys()):
            moved = _signed(left[key][1], right[key][1], _ENTRY_LIMIT)
            if moved:
                items.append(("", destination, f"{left[key][0]} binaries {' '.join(moved)}"))
        if len(gone) == 1 and len(came) == 1:
            (old_text, old_bins), (new_text, new_bins) = next(iter(gone.values())), next(iter(came.values()))
            moved = _signed(old_bins, new_bins, _ENTRY_LIMIT)
            transition = (
                f"{old_text} → {new_text}" if old_text != new_text
                else f"{new_text} other options changed (not shown)"
            )
            items.append(("", destination, transition + (f", binaries {' '.join(moved)}" if moved else "")))
            continue
        items.extend(("removed", destination, f"{text} via {_via(binaries)}") for text, binaries in gone.values())
        items.extend(("added", destination, f"{text} via {_via(binaries)}") for text, binaries in came.values())
    return _entries(_merged(items), _NETWORK_LIMIT)


def _filesystem_text(section: dict[str, Any] | None) -> str:
    if section is None:
        return "omitted (runtime defaults)"
    lists = [
        f"{name} {_list([_value(path) for path in section.get(name) or []], _ENTRY_LIMIT)}"
        for name in ("read_only", "read_write") if section.get(name)
    ]
    return ", ".join([*lists, f"include_workdir {str(bool(section.get('include_workdir'))).lower()}"])


def _filesystem_changes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    left, right = old.get("filesystem_policy"), new.get("filesystem_policy")
    if left == right:
        return []
    if left is None or right is None:
        return [f"filesystem {_filesystem_text(left)} → {_filesystem_text(right)}"]
    entries = [
        f"filesystem {name} {' '.join(moved)}"
        for name in ("read_only", "read_write")
        if (moved := _signed(left.get(name), right.get(name), _ENTRY_LIMIT))
    ]
    if bool(left.get("include_workdir")) != bool(right.get("include_workdir")):
        entries.append(
            f"filesystem include_workdir {str(bool(left.get('include_workdir'))).lower()}"
            f" → {str(bool(right.get('include_workdir'))).lower()}"
        )
    return entries


def _landlock(policy: dict[str, Any]) -> str:
    return str((policy.get("landlock") or {}).get("compatibility", "best_effort"))


def _process_changes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    left, right = old.get("process") or {}, new.get("process") or {}
    return [
        f"process {name} {_value(left.get(name) or '(unset)')} → {_value(right.get(name) or '(unset)')}"
        for name in ("run_as_user", "run_as_group")
        if (left.get(name) or "") != (right.get(name) or "")
    ]


def openshell_policy_change(
    before: dict[str, Any] | None, after: dict[str, Any] | None
) -> str | None:
    """What differs between two readings of one selected policy, in its published facts.

    ``None`` when either side publishes no policy, so a grant that carries no
    facts is described by the counts and digest it always was.
    """

    if before is None or after is None:
        return None
    old, new = _policy(before), _policy(after)
    if old is None or new is None:
        return None
    left, right = before["facts"], after["facts"]
    parts = [
        f"{name} {_value(left.get(name))} → {_value(right.get(name))}"
        for name in ("role", "runtime_version", "policy_schema_version")
        if left.get(name) != right.get(name)
    ]
    parts.extend(_filesystem_changes(old, new))
    if _landlock(old) != _landlock(new):
        parts.append(f"landlock {_value(_landlock(old))} → {_value(_landlock(new))}")
    parts.extend(_process_changes(old, new))
    parts.extend(_network_changes(old, new))
    head = f"{_value(right.get('role'))}, OpenShell {_value(right.get('runtime_version'))}"
    return f"{head}: " + ("; ".join(parts) if parts else _NOT_NAMED)


def _defaulted_audit_endpoints(facts: dict[str, Any], policy: dict[str, Any]) -> int:
    """Inspected endpoints whose ``enforcement`` the document left to the upstream default."""

    defaulted = set(facts.get("defaulted_fields") or [])
    count = 0
    for key, rule in (policy.get("network_policies") or {}).items():
        escaped = key.replace("~", "~0").replace("/", "~1")
        for index, endpoint in enumerate(rule.get("endpoints") or []):
            if endpoint.get("protocol") in {"", "tcp", None}:
                continue
            if f"/network_policies/{escaped}/endpoints/{index}/enforcement" in defaulted:
                count += 1
    return count


def openshell_policy_declarations(grant: dict[str, Any] | None) -> str | None:
    """What a wholly added or removed selected policy declares, with its defaults named.

    A document that appears or disappears establishes no replacement runtime
    policy, so this lists declarations and where a default stood in for one,
    and says nothing about whether authority grew.
    """

    policy = _policy(grant)
    if grant is None or policy is None:
        return None
    facts = grant["facts"]
    parts = [f"filesystem {_filesystem_text(policy.get('filesystem_policy'))}"]
    landlock = policy.get("landlock")
    parts.append(
        f"landlock {_landlock(policy)}" if landlock is not None else "landlock omitted (best_effort)"
    )
    process = policy.get("process")
    parts.append(
        "process omitted (driver default)" if process is None
        else "process " + (", ".join(
            f"{name} {_value(process[name])}" for name in ("run_as_user", "run_as_group") if process.get(name)
        ) or "identity unset")
    )
    declared = [
        ("", destination, f"{text} via {_via(binaries)}")
        for destination, groups in sorted(_groups(policy).items())
        for text, binaries in groups.values()
    ]
    parts.extend(_entries(_merged(declared), _NETWORK_LIMIT))
    audit = _defaulted_audit_endpoints(facts, policy)
    if audit:
        parts.append(f"{audit} inspected endpoint(s) default to audit enforcement")
    return "; ".join(parts)


__all__ = ["openshell_policy_change", "openshell_policy_declarations"]
