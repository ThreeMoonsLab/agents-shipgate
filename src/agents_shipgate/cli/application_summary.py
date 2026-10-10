"""A reviewer-first reading of an application comparison (#914).

``diff --application`` answers with one row per tool binding. That is exact and
too long to review: a hundred rows share three causes, every effect reads
``write (provisional: unknown effect)``, and every row asks the same question.
This module reads the rows once and states what a reviewer decides: one
finding per changed agent, the first thing not read, one line per shared
cause, and a question specific to what changed.

It is presentation. It reads a finished payload and changes nothing in it: no
status, row, direction, gap or exit code. Nothing it says is absent from a row,
and every statement names the rows (by index into ``rows``) it summarizes. A
statement that something was not read is never dropped to shorten a line:
a finding with an unresolved binding or an unread reach is ``partial``, and the
full list stays in the rows.

The text and the JSON ``summary`` block are one content model. ``summary_lines``
renders the block and nothing else, so another renderer (the PR comment) can
print the same finding.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

#: The most tools a finding describes one line each.
MAX_TOOL_LINES = 4
#: More tools than this are counted rather than listed in a finding's header.
MAX_HEADER_TOOLS = 6
MAX_CAUSE_LINES = 4
MAX_QUESTION_LINES = 2
#: Findings beyond this many print their header line only.
MAX_FINDINGS_FULL = 3
#: Per-row text is printed under the summary up to this many rows. Above it the
#: rows are in ``--json``, which always carries all of them.
DETAIL_ROW_LIMIT = 10

_KINDS = ("added", "removed", "changed", "not_established")
_MARKS = {"added": "+", "removed": "-", "changed": "~", "not_established": "?"}
_CANDIDATE_NOUN = {"added": "addition", "removed": "removal", "changed": "change"}
_CREDENTIAL_TARGETS = ("header", "auth", "query", "field", "userinfo", "keyword", "server_env")

#: Libraries whose effect is named by a function, not a method on an object.
FUNCTION_LIBRARIES = frozenset({"builtins", "subprocess", "os", "shutil", "asyncio", "io"})

_QUOTED = re.compile(r"""(?<!\w)'[^']*'(?!\w)|(?<!\w)"[^"]*"(?!\w)|`[^`]*`""")
_LOCATION = re.compile(r"[\w.\-/]+\.py(?::\d+)?")


def effect_phrase(effect: dict[str, Any]) -> str:
    """One effect beyond HTTP as a reviewer reads it (#913)."""

    words = [effect["family"], effect["operation"]]
    if effect["family"] in {"cloud", "messaging"} and effect.get("service"):
        words.append(effect["service"])
    if effect.get("statement"):
        words.append(effect["statement"] + (" on" if effect.get("target") else ""))
    if effect.get("target"):
        words.append(effect["target"])
    if effect.get("shell"):
        words.append("through a shell")
    library = effect["library"]
    call = effect["call"] if library in FUNCTION_LIBRARIES else f"{library} {effect['call']}"
    if effect["family"] == "database" and effect.get("service"):
        call = f"{effect['service']}, {call}"
    return " ".join(words) + f" ({call})"


def credential_phrase(item: dict[str, Any]) -> str:
    """A credential a call or object sends: where it comes from, where it goes."""

    target = next(
        (
            f"{kind} {item[kind]}" if item[kind] else kind
            for kind in _CREDENTIAL_TARGETS
            if kind in item
        ),
        "a credential",
    )
    sources = [f"env {name}" for name in item.get("env", [])]
    if item.get("from"):
        sources.append("a value made from model-supplied " + ", ".join(item["from"]))
    if item.get("literal"):
        sources.append("a literal (not printed)")
    return f"{', '.join(sources) or 'a computed value'} → {target}"


def bound_phrase(binding: dict[str, Any] | None) -> str:
    alternatives = (binding or {}).get("bound_when")
    return "only when " + " or ".join(alternatives) if alternatives else "unconditionally"


def pattern_of(reason: str) -> str:
    """A reason with the names and places that vary taken out.

    Two reasons with one pattern state one cause about different tools or
    lines. The pattern only groups; the reason itself is always kept.
    """

    return _LOCATION.sub("…", _QUOTED.sub("…", reason))


def _object_text(binding: dict[str, Any]) -> str:
    from agents_shipgate.inputs.object_tools import object_display

    return object_display({"identity": binding["object"]})


def _params(binding: dict[str, Any]) -> list[str]:
    properties = (binding.get("input_schema") or {}).get("properties")
    return list(properties) if isinstance(properties, dict) else []


def _what_changed(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """What differs between a tool's two sides, in the words a reviewer uses."""

    changes: list[str] = []
    old = (before.get("definition") or {}).get("implementation_sha256")
    new = (after.get("definition") or {}).get("implementation_sha256")
    if old != new:
        changes.append("implementation changed")
    if any(before.get(key) != after.get(key) for key in ("signature", "input_schema", "output_schema")):
        added = [name for name in _params(after) if name not in _params(before)]
        removed = [name for name in _params(before) if name not in _params(after)]
        if added or removed:
            changes.append(
                "arguments "
                + ", ".join([*(f"+{name}" for name in added), *(f"-{name}" for name in removed)])
            )
        else:
            changes.append("signature changed")
    if before.get("bound_when") != after.get("bound_when"):
        changes.append(f"bound {bound_phrase(before)} → {bound_phrase(after)}")
    if before.get("agent_settings") != after.get("agent_settings"):
        changes.append("declared agent settings changed")
    if before.get("object") != after.get("object"):
        identity_before, identity_after = before.get("object") or {}, after.get("object") or {}
        keys = sorted(
            key
            for key in identity_before.keys() | identity_after.keys()
            if identity_before.get(key) != identity_after.get(key)
        )
        changes.append("tool object changed" + (f" ({', '.join(keys)})" if keys else ""))
    return changes or ["binding details changed"]


def _reach_items(binding: dict[str, Any]) -> list[dict[str, Any]]:
    """Each distinct outbound call or library effect a tool's code reaches.

    Call sites of one call are one item, the way the row's own text groups
    them; the item keeps the first site and the union of what the sites send.
    """

    reach = binding.get("reach") or {}
    items: dict[tuple[Any, ...], dict[str, Any]] = {}
    for call in reach.get("calls") or []:
        method = call.get("method") or "an unread method"
        key = ("http", method, call["url"], call.get("graphql"))
        text = f"{method} {call['url']}" + (f" (GraphQL {call['graphql']})" if call.get("graphql") else "")
        item = items.setdefault(
            key, {"text": text, "short": f"{method} {call['url']}", "at": call["at"], "host": [], "credentials": [], "model_supplied": []}
        )
        _merge(item, call, "credential_sources", "credentials", credential_phrase)
        _merge(item, call, "model_supplied", "model_supplied", lambda x: f"{x['param']} → {x['into']}")
    for effect in reach.get("effects") or []:
        phrase = effect_phrase(effect)
        key = ("effect", phrase)
        short = " ".join(
            [effect["family"], effect["operation"]]
            + ([effect["service"]] if effect.get("service") and effect["family"] != "database" else [])
            + ([effect["target"]] if effect.get("target") else [])
        )
        item = items.setdefault(
            key,
            {"text": phrase, "short": short, "at": effect["at"], "host": [], "credentials": [], "model_supplied": []},
        )
        for host in effect.get("host") or []:
            if host not in item["host"]:
                item["host"].append(host)
        _merge(item, effect, "credential_sources", "credentials", credential_phrase)
        _merge(item, effect, "model_supplied", "model_supplied", lambda x: f"{x['param']} → {x['into']}")
    return list(items.values())


def _merge(item: dict[str, Any], source: dict[str, Any], key: str, into: str, phrase: Any) -> None:
    for value in source.get(key) or []:
        text = phrase(value)
        if text not in item[into]:
            item[into].append(text)


_REACH_PREFIX = {
    "reaches": "reaches",
    "reaches_new": "now also reaches",
    "reaches_dropped": "no longer reaches",
    "reaches_same": "still reaches",
    "reaches_changed": "still reaches",
}


def _reach_fact(item: dict[str, Any], kind: str = "reaches") -> dict[str, Any]:
    fact: dict[str, Any] = {
        "kind": kind,
        "text": f"{_REACH_PREFIX[kind]} {item['text']}",
        "target": item["text"],
        "short": item["short"],
        "at": item["at"],
    }
    for key in ("host", "credentials", "model_supplied"):
        if item[key]:
            fact[key] = item[key]
    return fact


def _unresolved(binding: dict[str, Any] | None) -> dict[str, Any] | None:
    """The first hop a tool's reach stopped at, and how many more there are."""

    reach = (binding or {}).get("reach") or {}
    limits = reach.get("limits") or []
    if not limits:
        return None
    first = limits[0]
    return {
        "at": first["at"],
        "why": first["why"],
        "count": len(limits) + reach.get("more_limits", 0),
    }


def _entry(index: int, row: dict[str, Any]) -> dict[str, Any]:
    """One tool of a finding: what the row says, in the reviewer's order."""

    change = row["change"]
    kind = row["candidate_change"] if change == "not_established" else change
    before, after = row["before"], row["after"]
    observed = after if after is not None else before
    facts: list[dict[str, Any]] = []
    if kind == "changed" and before is not None and after is not None:
        facts.extend({"kind": "change", "text": text} for text in _what_changed(before, after))
    if observed is not None and observed.get("object"):
        fact = {"kind": "object", "text": _object_text(observed)}
        credentials = [credential_phrase(item) for item in observed["object"].get("credential_sources") or []]
        if credentials:
            fact["credentials"] = credentials
        facts.append(fact)
    if observed is not None:
        current = _reach_items(observed)
        if kind == "removed":
            facts.extend(_reach_fact(item, "reaches_dropped") for item in current)
        elif kind == "changed" and before is not None and after is not None:
            previous = {i["text"]: i for i in _reach_items(before)}
            for item in current:
                old = previous.get(item["text"])
                if old is None:
                    facts.append(_reach_fact(item, "reaches_new"))
                    continue
                # The same call, sending something it did not or no longer
                # sending something it did: a change to the reach itself.
                fields = ("host", "credentials", "model_supplied")
                added = {key: [v for v in item[key] if v not in old[key]] for key in fields}
                dropped = {key: [v for v in old[key] if v not in item[key]] for key in fields}
                if any(added.values()) or any(dropped.values()):
                    fact = _reach_fact({**item, **added}, "reaches_changed")
                    if any(dropped.values()):
                        fact["dropped"] = {key: value for key, value in dropped.items() if value}
                    facts.append(fact)
                else:
                    facts.append(_reach_fact(item, "reaches_same"))
            now = {i["text"] for i in current}
            facts.extend(_reach_fact(i, "reaches_dropped") for i in previous.values() if i["text"] not in now)
        else:
            facts.extend(_reach_fact(item) for item in current)
    entry: dict[str, Any] = {
        "row": index,
        "tool": row["tool"],
        "change": change,
        "candidate_change": row["candidate_change"],
        "facts": facts,
    }
    hop = _unresolved(observed) if change != "not_established" else None
    if hop is not None:
        entry["unresolved"] = hop
    if observed is not None and observed.get("effect_evidence"):
        evidence = observed["effect_evidence"]
        entry["effect"] = {"effect": evidence["conservative_effect"], "status": evidence["status"]}
    return entry


_REACH_KINDS = frozenset(_REACH_PREFIX)


def _has_capability(entry: dict[str, Any]) -> bool:
    """The tool reaches something, or is a tool object: a question about it exists."""

    return any(fact["kind"] in _REACH_KINDS or fact["kind"] == "object" for fact in entry["facts"])


def _reach_moved(entry: dict[str, Any]) -> bool:
    """What the tool reaches, or the object it is, is among the change."""

    return any(
        fact["kind"] in {"reaches", "reaches_new", "reaches_dropped", "reaches_changed", "object"}
        for fact in entry["facts"]
    )


def _kind(entry: dict[str, Any]) -> str:
    change = entry["change"]
    return change if change != "not_established" else (entry["candidate_change"] or change)


def _mark(entry: dict[str, Any]) -> str:
    """``+`` added, ``-`` removed, ``~`` changed; a leading ``?`` is a candidate."""

    return ("?" if entry["change"] == "not_established" else "") + _MARKS[_kind(entry)]


#: Added first: what an agent gained is the first thing a reviewer asks.
_DISPLAY_ORDER = {"added": 0, "changed": 1, "removed": 2}


def _display_key(entry: dict[str, Any]) -> tuple[int, int, int]:
    return (entry["change"] == "not_established", _DISPLAY_ORDER[_kind(entry)], entry["row"])


def _capability_words(entries: list[dict[str, Any]], kinds: frozenset[str]) -> list[str]:
    """Short names of what a finding's tools reach, for its header."""

    words: list[str] = []
    for entry in entries:
        if entry["change"] == "not_established":
            continue
        for fact in entry["facts"]:
            if fact["kind"] in kinds:
                word = fact["short"]
            elif fact["kind"] == "object" and entry["change"] == "added" and "reaches" in kinds:
                word = fact["text"].split(";")[0]
            else:
                continue
            if word not in words:
                words.append(word)
    return words


def _names(tools: list[str], limit: int = 4) -> str:
    shown = ", ".join(tools[:limit])
    return shown + (f", and {len(tools) - limit} more" if len(tools) > limit else "")


def _reach_question(agent: str, tool: str, fact: dict[str, Any]) -> str:
    sending = "; ".join(fact.get("credentials", [])[:2])
    controlled = fact.get("model_supplied", [])
    shown = ", ".join(controlled[:3]) + (", …" if len(controlled) > 3 else "")
    if fact["kind"] == "reaches_changed":
        # The reach is the same call; what it sends is what changed.
        parts = ([f"send {sending}"] if sending else []) + (
            [f"let the model control {shown}"] if controlled else []
        )
        return f"Should {tool} now {' and '.join(parts)} in its call to {fact['target']}?"
    return (
        f"Should {agent} reach {fact['target']} through {tool}"
        + (f", sending {sending}" if sending else "")
        + (f", with the model controlling {shown}" if controlled else "")
        + "?"
    )


def _questions(agent: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Questions specific to what changed, each naming the rows it asks about."""

    asked: dict[str, list[int]] = {}

    def ask(text: str, rows: list[int]) -> None:
        known = asked.setdefault(text, [])
        known.extend(row for row in rows if row not in known)

    plain: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    for entry in entries:
        if entry["change"] == "not_established":
            continue
        added = entry["change"] == "added"
        row = [entry["row"]]
        for fact in entry["facts"]:
            if fact["kind"] == "reaches_new" or (fact["kind"] == "reaches" and added):
                ask(_reach_question(agent, entry["tool"], fact), row)
            elif fact["kind"] == "reaches_changed" and (fact.get("credentials") or fact.get("model_supplied")):
                ask(_reach_question(agent, entry["tool"], fact), row)
            elif fact["kind"] == "object" and added:
                sending = f", sending {'; '.join(fact['credentials'][:2])}" if fact.get("credentials") else ""
                ask(f"Should {agent} hold {fact['text'].split(';')[0]}{sending}?", row)
        if added and not _has_capability(entry):
            plain.append(entry)
        elif entry["change"] == "removed":
            removed.append(entry)
        elif entry["change"] == "changed":
            changed.append(entry)
    if plain:
        unread = any("unresolved" in entry for entry in plain)
        ask(
            f"Should {agent} be able to call {_names([e['tool'] for e in plain])}? "
            + (
                f"What {'it' if len(plain) == 1 else 'they'} reach{'es' if len(plain) == 1 else ''} was not read in full."
                if unread
                else "No outbound call or library effect was established."
            ),
            [e["row"] for e in plain],
        )
    if removed:
        ask(
            f"Is it intended that {agent} no longer holds {_names([e['tool'] for e in removed])}?",
            [e["row"] for e in removed],
        )
    if changed:
        kinds = [
            word
            for word in ("implementation", "arguments", "signature", "bound", "tool object")
            if any(
                fact["kind"] == "change" and fact["text"].startswith(word)
                for e in changed
                for fact in e["facts"]
            )
        ]
        ask(
            f"Do the changes to {_names([e['tool'] for e in changed])}"
            + (f" ({', '.join(kinds)})" if kinds else "")
            + f" still match what {agent} should be able to do?",
            [e["row"] for e in changed],
        )
    return [{"question": text, "rows": rows} for text, rows in asked.items()]


def _side_gaps(payload: dict[str, Any]) -> list[tuple[str, str | None, str]]:
    """Every gap and limit a side names, as (side, agent, reason)."""

    out: list[tuple[str, str | None, str]] = []
    for side in ("base", "head"):
        data = payload.get(side) or {}
        attributed: dict[str, str | None] = {}
        for gap in data.get("coverage_gaps") or []:
            attributed.setdefault(gap["reason"], gap.get("agent"))
        for reason in data.get("limits") or []:
            agent = attributed.get(reason)
            if agent is None:
                # A multi-scope answer prefixes a limit with its scope.
                agent = next(
                    (owner for known, owner in attributed.items() if reason.endswith(": " + known)),
                    None,
                )
            out.append((side, agent, reason))
    return out


def _first_named(reasons: list[str]) -> str:
    """The first reason that names a place; else the first."""

    return next((reason for reason in reasons if pattern_of(reason) != reason), reasons[0])


def _place(text: str) -> str | None:
    found = _LOCATION.search(text)
    return found.group(0) if found else None


def build_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """The reviewer-first block for a finished comparison payload."""

    rows: list[dict[str, Any]] = payload["rows"]
    order: dict[tuple[str, str], list[int]] = {}
    for index, row in enumerate(rows):
        order.setdefault((row["agent_source"], row["agent"]), []).append(index)

    # One cause once, however many agents and rows share it.
    found: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        # Sides in a fixed order: the summary must not depend on how a reader
        # of the JSON happens to order a row's keys.
        for side, reasons in sorted(row["uncertainty"].items()):
            for reason in reasons:
                cause = found.setdefault(
                    pattern_of(reason),
                    {"example": reason, "reasons": set(), "rows": set(), "sides": set(), "agents": []},
                )
                cause["reasons"].add(reason)
                cause["rows"].add(index)
                cause["sides"].add(side)
                if row["agent"] not in cause["agents"]:
                    cause["agents"].append(row["agent"])
    patterns = list(found)
    position = {pattern: index for index, pattern in enumerate(patterns)}
    causes = [
        {
            "pattern": pattern,
            "example": found[pattern]["example"],
            "reasons": len(found[pattern]["reasons"]),
            "rows": sorted(found[pattern]["rows"]),
            "sides": sorted(found[pattern]["sides"]),
            "agents": found[pattern]["agents"],
        }
        for pattern in patterns
    ]

    # Reasons already stated elsewhere in the output: by a row, or as a
    # scope limit under the scope line.
    tied = {reason for row in rows for reasons in row["uncertainty"].values() for reason in reasons}
    tied |= set((payload.get("scope_selection") or {}).get("limits") or [])
    by_agent: dict[str | None, list[tuple[str, str]]] = {}
    for side, agent, reason in _side_gaps(payload):
        if reason not in tied:
            by_agent.setdefault(agent, []).append((side, reason))

    findings: list[dict[str, Any]] = []
    claimed: set[str | None] = set()
    for (source, name), idxs in order.items():
        entries = [_entry(index, rows[index]) for index in idxs]
        counts = Counter(rows[index]["change"] for index in idxs)
        # One agent name built in several files states its limits once.
        limits = [] if name in claimed else by_agent.get(name, [])
        claimed.add(name)
        reasons_here = [
            (index, position[pattern_of(reason)], reason)
            for index in idxs
            for _side, reasons in sorted(rows[index]["uncertainty"].items())
            for reason in reasons
        ]
        own = sorted({cause for _, cause, _ in reasons_here})
        observed = rows[idxs[0]]["after"] or rows[idxs[0]]["before"] or {}
        first: dict[str, Any] | None = None
        if reasons_here:
            # A reason that names a place is a hop; one that does not is the
            # generic statement that the graph is incomplete.
            index, _, reason = next(
                (item for item in reasons_here if pattern_of(item[2]) != item[2]), reasons_here[0]
            )
            first = {"kind": "binding", "text": reason, "row": index}
        elif limits:
            first = {"kind": "agent", "text": _first_named([reason for _, reason in limits]), "row": None}
        else:
            hopped = next((e for e in entries if "unresolved" in e), None)
            if hopped is not None:
                hop = hopped["unresolved"]
                first = {"kind": "reach", "text": f"{hop['at']} {hop['why']}", "row": hopped["row"]}
        if first is not None:
            first["at"] = _place(first["text"])
        finding: dict[str, Any] = {
            "agent": name,
            "agent_source": observed.get("agent_source", source),
            "status": "partial" if first is not None else "compared",
            "rows": idxs,
            "counts": {kind: counts.get(kind, 0) for kind in _KINDS},
            "first_unresolved": first,
            "capabilities": _capability_words(entries, frozenset({"reaches", "reaches_new"})),
            "changed_capabilities": _capability_words(entries, frozenset({"reaches_changed"})),
            "unchanged_capabilities": _capability_words(entries, frozenset({"reaches_same"})),
            "tools": entries,
            "causes": own,
            "questions": _questions(name, entries),
        }
        if limits:
            finding["agent_limits"] = {
                "count": len({reason for _, reason in limits}),
                "sides": sorted({side for side, _ in limits}),
                "first": _first_named([reason for _, reason in limits]),
            }
        findings.append(finding)

    effects = Counter(
        (entry["effect"]["effect"], entry["effect"]["status"])
        for finding in findings
        for entry in finding["tools"]
        if "effect" in entry and entry["change"] != "not_established"
    )
    unestablished = sum(1 for row in rows if row["change"] == "not_established")
    summary: dict[str, Any] = {
        "counts": {"total": len(rows), **{kind: sum(1 for r in rows if r["change"] == kind) for kind in _KINDS}},
        "findings": findings,
        "effects": [{"effect": e, "status": s, "count": n} for (e, s), n in effects.items()],
        "causes": causes,
        "not_read": [
            {
                "agent": agent,
                "count": len({reason for _, reason in items}),
                "sides": sorted({side for side, _ in items}),
                "first": _first_named([reason for _, reason in items]),
            }
            for agent, items in by_agent.items()
            if agent not in claimed
        ],
    }
    if unestablished:
        summary["question"] = (
            f"Resolve the named causes before treating the {unestablished} candidate "
            f"change{'s' if unestablished != 1 else ''} as {'a change' if unestablished == 1 else 'changes'}."
        )
    return summary


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _tool_line(entry: dict[str, Any]) -> str:
    parts: list[str] = []
    reaches = [f for f in entry["facts"] if f["kind"] in _REACH_KINDS]
    for fact in entry["facts"]:
        if fact["kind"] in _REACH_KINDS:
            continue
        text = fact["text"]
        if fact.get("credentials"):
            text += " [credential: " + "; ".join(fact["credentials"]) + "]"
        parts.append(text)
    for fact in reaches[:2]:
        now = "now " if fact["kind"] == "reaches_changed" else ""
        extra = []
        if fact.get("host"):
            extra.append(now + "host " + ", ".join(fact["host"]))
        for credential in fact.get("credentials", [])[:2]:
            extra.append(now + "credential " + credential)
        if fact.get("model_supplied"):
            shown = fact["model_supplied"][:3]
            more = len(fact["model_supplied"]) - 3
            extra.append(now + "model-supplied " + "; ".join(shown) + (f"; and {more} more" if more > 0 else ""))
        for key, value in (fact.get("dropped") or {}).items():
            extra.append(f"no longer {key.replace('_', '-')} " + "; ".join(value[:2]))
        parts.append(f"{fact['text']} at {fact['at']}" + (f" [{'; '.join(extra)}]" if extra else ""))
    if len(reaches) > 2:
        parts.append(f"and {len(reaches) - 2} more reach{'es' if len(reaches) != 3 else ''}")
    if not reaches and not any(f["kind"] == "object" for f in entry["facts"]):
        if entry["change"] == "changed":
            parts.append("no outbound call or library effect established, before or after")
        elif entry["change"] == "added":
            parts.append("no outbound call or library effect established")
        elif entry["change"] == "removed":
            parts.append("no outbound call or library effect established before")
    hop = entry.get("unresolved")
    if hop:
        more = hop["count"] - 1
        parts.append(f"first unread: {hop['at']} {hop['why']}" + (f" (and {more} more)" if more else ""))
    candidate = " (candidate, not established)" if entry["change"] == "not_established" else ""
    return f"{_mark(entry)}{entry['tool']}{candidate}: " + "; ".join(parts)


def _effects_line(effects: list[dict[str, Any]]) -> str | None:
    from agents_shipgate.report.human_order import EFFECT_EVIDENCE_LABELS

    if not effects:
        return None
    return "effect evidence " + ", ".join(
        f"{item['count']} {item['effect']} ({EFFECT_EVIDENCE_LABELS.get(item['status'], item['status'])})"
        for item in effects
    )


def finding_lines(finding: dict[str, Any], *, qualify: bool = False, full: bool = True) -> list[str]:
    """One finding as text; the first line stands alone."""

    entries = sorted(finding["tools"], key=_display_key)
    counts = finding["counts"]
    established = [e for e in entries if e["change"] != "not_established"]
    name = finding["agent"] + (f" ({finding['agent_source']})" if qualify else "")
    if len(entries) <= MAX_HEADER_TOOLS:
        what = _names([f"{_mark(e)}{e['tool']}" for e in entries], MAX_HEADER_TOOLS)
    else:
        parts = []
        for kind in ("added", "removed", "changed"):
            if counts[kind]:
                names = _names([f"{_MARKS[kind]}{e['tool']}" for e in established if e["change"] == kind], 3)
                parts.append(f"{counts[kind]} {kind} ({names})")
        if counts["not_established"]:
            candidates = Counter(e["candidate_change"] or "" for e in entries if e["change"] == "not_established")
            detail = ", ".join(
                f"{n} candidate {_CANDIDATE_NOUN.get(kind, 'change')}s" for kind, n in sorted(candidates.items())
            )
            parts.append(f"{counts['not_established']} not established ({detail})")
        what = "; ".join(parts)
    notes: list[str] = []
    if established:
        words, changed_words = finding["capabilities"], finding["changed_capabilities"]
        if words:
            notes.append("new reach: " + ", ".join(words[:3]) + (f", and {len(words) - 3} more" if len(words) > 3 else ""))
        if changed_words:
            notes.append("changed reach: " + ", ".join(changed_words[:2]) + (", …" if len(changed_words) > 2 else ""))
        if not words and not changed_words:
            if counts["added"]:
                notes.append("reach: none established")
            elif counts["changed"]:
                unchanged = finding["unchanged_capabilities"]
                notes.append(
                    "no new reach established"
                    + (
                        "; still reaches " + ", ".join(unchanged[:2]) + (", …" if len(unchanged) > 2 else "")
                        if unchanged
                        else ""
                    )
                )
    first = finding["first_unresolved"]
    if first is not None:
        notes.append(f"not read beyond {first['at']}" if first["at"] else f"not read: {first['text']}")
    lines = [f"{name} [{finding['status']}]: {what}" + (f" ({'; '.join(notes)})" if notes else "")]
    if not full:
        return lines
    # A tool that reaches something gets its own line; the rest do too while
    # the finding is small enough to read as a list.
    # A candidate gets a line only when the finding has nothing established to
    # show: beside established rows it is counted, and its cause is below.
    candidates_too = not established
    shown = (
        [e for e in entries if e["change"] != "not_established" or (candidates_too and _has_capability(e))]
        if len(entries) <= MAX_TOOL_LINES
        else [e for e in entries if _reach_moved(e) and (candidates_too or e["change"] != "not_established")][
            :MAX_TOOL_LINES
        ]
    )
    for entry in shown:
        lines.append("  " + _tool_line(entry))
    rest = [e for e in established if e not in shown]
    if rest:
        groups = Counter(
            (e["change"], "; ".join(f["text"] for f in e["facts"] if f["kind"] == "change")) for e in rest
        )
        described = "; ".join(
            f"{n} {kind}" + (f" ({what})" if what else "") for (kind, what), n in groups.most_common(3)
        )
        unread = sum(1 for e in rest if "unresolved" in e)
        same = [
            fact["short"]
            for e in rest
            for fact in e["facts"]
            if fact["kind"] == "reaches_same"
        ]
        still = list(dict.fromkeys(same))
        lines.append(
            f"  and {len(rest)} more: {described}"
            + (f"; still reaching {', '.join(still[:3])}" + (f", and {len(still) - 3} more" if len(still) > 3 else "") if still else "")
            + (f"; reach not read in full for {unread}" if unread else "")
        )
    if "agent_limits" in finding:
        limits = finding["agent_limits"]
        lines.append(
            f"  this agent's binding graph is also incomplete: {_plural(limits['count'], 'limit')} "
            f"({'/'.join(limits['sides'])}); first: {limits['first']}"
        )
    questions = finding["questions"]
    for item in questions[:MAX_QUESTION_LINES]:
        lines.append(f"  Review: {item['question']}")
    if len(questions) > MAX_QUESTION_LINES:
        lines[-1] += f" (and {len(questions) - MAX_QUESTION_LINES} more in --json)"
    return lines


def summary_lines(summary: dict[str, Any]) -> list[str]:
    """The text a reviewer reads first, from the ``summary`` block alone."""

    total = summary["counts"]
    lines: list[str] = []
    if total["total"]:
        kinds = ", ".join(f"{total[kind]} {kind.replace('_', ' ')}" for kind in _KINDS if total[kind])
        effects = _effects_line(summary["effects"])
        lines.append(
            f"Findings: {_plural(len(summary['findings']), 'changed agent')}, {_plural(total['total'], 'row')} ({kinds})"
            + (f"; {effects}" if effects else "")
            + "."
        )
    names = Counter(f["agent"] for f in summary["findings"])
    for position, finding in enumerate(summary["findings"]):
        lines.extend(
            finding_lines(finding, qualify=names[finding["agent"]] > 1, full=position < MAX_FINDINGS_FULL)
        )
    if len(summary["findings"]) > MAX_FINDINGS_FULL:
        more = len(summary["findings"]) - MAX_FINDINGS_FULL
        lines.append(f"{_plural(more, 'more agent')}: tool lines and questions are in --json summary.findings.")
    causes = summary["causes"]
    if causes:
        unestablished = total["not_established"]
        lines.append(
            f"Not established: {_plural(unestablished, 'row')} "
            f"{'has' if unestablished == 1 else 'share'} {_plural(len(causes), 'cause')}."
        )
        for cause in causes[:MAX_CAUSE_LINES]:
            extra = []
            if cause["reasons"] > 1:
                extra.append(f"{cause['reasons'] - 1} similar")
            extra.append(f"{_plural(len(cause['rows']), 'row')}, {'/'.join(cause['sides'])}")
            lines.append(f"  - {cause['example']} [{'; '.join(extra)}]")
        if len(causes) > MAX_CAUSE_LINES:
            lines.append(f"  - and {len(causes) - MAX_CAUSE_LINES} more causes (see --json summary.causes)")
        lines.append(f"  Review: {summary['question']}")
    not_read = summary["not_read"]
    if not_read:
        owners = [item["agent"] or "no single agent" for item in not_read]
        limits = sum(item["count"] for item in not_read)
        lines.append(
            f"Also not read, and not tied to a row above: {_plural(limits, 'limit')} on {', '.join(owners[:4])}"
            + (f", and {len(owners) - 4} more" if len(owners) > 4 else "")
            + f"; first: {not_read[0]['first']}"
        )
    return lines
