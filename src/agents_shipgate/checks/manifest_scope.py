from __future__ import annotations

import re
from dataclasses import dataclass

from agents_shipgate.checks.base import tool_finding
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import Tool
from agents_shipgate.core.risk_hints import (
    has_risk_tag,
    is_effectively_read_only,
    is_write_tool,
    risk_tags,
)

STOPWORDS = {
    "a",
    "about",
    "after",
    "all",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "before",
    "but",
    "by",
    "can",
    "do",
    "does",
    "don",
    "dont",
    "during",
    "each",
    "except",
    "for",
    "from",
    "has",
    "have",
    "if",
    "in",
    "inside",
    "into",
    "is",
    "it",
    "its",
    "may",
    "must",
    "never",
    "no",
    "nor",
    "not",
    "of",
    "on",
    "onto",
    "or",
    "our",
    "outside",
    "over",
    "per",
    "should",
    "so",
    "than",
    "that",
    "the",
    "their",
    "them",
    "then",
    "these",
    "they",
    "this",
    "those",
    "to",
    "under",
    "unless",
    "until",
    "upon",
    "via",
    "was",
    "we",
    "were",
    "will",
    "with",
    "within",
    "without",
    "would",
    "you",
    "your",
}

# Verbs that name something an agent *does*. A prohibited action and a tool
# overlap only when they share a verb (#1035): sharing nouns alone —
# "customer", "data", "ticket" — says the tool touches the same subject, not
# that it performs the prohibited action. A prohibited action's verbs are the
# words of this list it contains ("Customer data must never be **exported**");
# only when it contains none does its leading word count as the verb
# ("**buy** stock"). Compared after ``_stem`` and ``ACTION_SYNONYMS``.
ACTION_VERBS = {
    "approve",
    "archive",
    "ban",
    "book",
    "buy",
    "call",
    "cancel",
    "capture",
    "change",
    "charge",
    "close",
    "create",
    "delete",
    "deploy",
    "destroy",
    "disable",
    "disclose",
    "drop",
    "edit",
    "email",
    "enable",
    "erase",
    "execute",
    "export",
    "grant",
    "install",
    "invite",
    "issue",
    "kill",
    "lock",
    "merge",
    "message",
    "modify",
    "move",
    "notify",
    "overwrite",
    "pay",
    "post",
    "publish",
    "purchase",
    "purge",
    "push",
    "refund",
    "remove",
    "reset",
    "restart",
    "revoke",
    "rotate",
    "run",
    "sell",
    "send",
    "set",
    "share",
    "shutdown",
    "sign",
    "spend",
    "submit",
    "suspend",
    "terminate",
    "trade",
    "transfer",
    "truncate",
    "update",
    "upload",
    "void",
    "wipe",
    "wire",
    "withdraw",
    "write",
}

# Verbs that name one action, so "delete customer data" meets a tool that
# "purges all data for a customer" and "modify user permissions" meets
# ``update_user_permissions`` — matches substring containment used to make by
# accident (#1035). Verb to verb only, and applied only to a word in verb
# position on either side, never to an object: "issue refunds" meets
# ``stripe.create_refund``, but the noun in ``github.create_issue`` stays an
# issue. Every equivalence the matcher draws is listed here; nothing is fuzzy.
ACTION_SYNONYMS = {
    "alter": "update",
    "change": "update",
    "close": "terminate",
    "destroy": "delete",
    "disclose": "share",
    "edit": "update",
    "email": "send",
    "erase": "delete",
    "issue": "create",
    "mail": "send",
    "make": "create",
    "message": "send",
    "modify": "update",
    "purchase": "buy",
    "purge": "delete",
    "remove": "delete",
    "run": "execute",
    "wipe": "delete",
}

# Nouns that name an action, read as its verb only in verb position: the
# last word of a noun-first tool name (``loan_approval``), or a prohibited
# action that contains no verb at all ("customer data deletion").
ACTION_NOMINALIZATIONS = {
    "approval": "approve",
    "cancellation": "cancel",
    "deletion": "delete",
    "deployment": "deploy",
    "disclosure": "disclose",
    "execution": "execute",
    "modification": "modify",
    "payment": "pay",
    "removal": "remove",
    "termination": "terminate",
}

# Verbs that tool names and descriptions start with but that no prohibited
# action is about. Knowing them tells a verb-first name (``get_post``,
# ``comment_on_issue``) from a noun-first one (``disk_wipe``).
TOOL_VERBS = {
    "add",
    "apply",
    "assign",
    "attach",
    "calculate",
    "check",
    "comment",
    "compute",
    "count",
    "describe",
    "download",
    "escalate",
    "estimate",
    "fetch",
    "find",
    "flag",
    "generate",
    "get",
    "handle",
    "insert",
    "link",
    "list",
    "log",
    "look",
    "lookup",
    "mark",
    "open",
    "preview",
    "process",
    "query",
    "read",
    "render",
    "reply",
    "request",
    "retrieve",
    "save",
    "schedule",
    "search",
    "show",
    "start",
    "stop",
    "store",
    "sync",
    "track",
    "upsert",
    "validate",
    "verify",
    "view",
    "watch",
}

# Words that end a direct object: "run reports *for* customers" is about
# reports, and "deploy *to* production" has only a destination.
_PREPOSITIONS = frozenset(
    {
        "about",
        "across",
        "after",
        "against",
        "as",
        "at",
        "before",
        "between",
        "by",
        "during",
        "for",
        "from",
        "in",
        "into",
        "of",
        "on",
        "onto",
        "over",
        "per",
        "through",
        "to",
        "under",
        "upon",
        "via",
        "with",
        "within",
    }
)

# Two words that spell one action ("shut down" and ``shutdown``; "e-mail").
_PHRASES = {
    ("e", "mail"): "email",
    ("power", "off"): "shutdown",
    ("shut", "down"): "shutdown",
}

# Words that open the clause naming what the prohibited action lacks or is
# limited by: "send external email *without preview*". The clause is not part
# of the action, so its words never count towards an overlap.
_QUALIFIERS = ("without", "unless", "except", "outside")

READ_ONLY_TERMS = {
    "answer",
    "lookup",
    "lookups",
    "read",
    "readonly",
    "read-only",
    "search",
    "status",
    "view",
}

WRITE_TERMS = {
    "cancel",
    "create",
    "delete",
    "email",
    "issue",
    "modify",
    "refund",
    "remove",
    "send",
    "update",
    "write",
}


def run(context: ScanContext):
    findings = []
    purpose_text = " ".join(context.manifest.agent.declared_purpose).lower()
    prohibited_actions = context.manifest.agent.prohibited_actions

    if _purpose_is_read_only(purpose_text):
        for tool in context.tools:
            if is_write_tool(tool) and not is_effectively_read_only(tool):
                findings.append(
                    tool_finding(
                        tool=tool,
                        check_id="SHIP-SCOPE-TOOL-OUTSIDE-PURPOSE",
                        title=f"{tool.name} appears outside the declared read-only purpose",
                        severity="high",
                        category="scope",
                        evidence={
                            "declared_purpose": context.manifest.agent.declared_purpose,
                            "risk_tags": risk_tags(tool, min_confidence="medium"),
                        },
                        confidence="medium",
                        recommendation=(
                            f"Remove {tool.name} from this release or update the declared purpose "
                            "and release review to cover write-capable tools."
                        ),
                        context=context,
                        provenance_kind="keyword_heuristic",
                        policy_evidence_pointer="/agent/declared_purpose",
                    )
                )

    for tool in context.tools:
        if is_effectively_read_only(tool):
            continue
        for prohibited_index, prohibited in enumerate(prohibited_actions):
            if _prohibited_action_is_mitigated(context, tool, prohibited):
                continue
            if _tool_matches_prohibited_action(tool, prohibited):
                findings.append(
                    tool_finding(
                        tool=tool,
                        check_id="SHIP-SCOPE-PROHIBITED-TOOL-PRESENT",
                        title=f"{tool.name} appears to overlap with a prohibited action",
                        # A word match is advisory and never moves the verdict
                        # (#1055, Option A; ``advisory_when_policy_ineligible``
                        # in the catalog), so it is not labelled ``high``.
                        severity="medium",
                        category="scope",
                        evidence={
                            "prohibited_action": prohibited,
                            "risk_tags": risk_tags(tool, min_confidence="medium"),
                        },
                        confidence="medium",
                        recommendation=(
                            f"Remove {tool.name}, narrow its policy, or revise prohibited_actions "
                            "so the manifest and tool surface do not contradict each other."
                        ),
                        context=context,
                        provenance_kind="keyword_heuristic",
                        policy_evidence_pointer=(
                            f"/agent/prohibited_actions/{prohibited_index}"
                        ),
                    )
                )
                break

    return findings


def _purpose_is_read_only(purpose_text: str) -> bool:
    if not purpose_text:
        return False
    tokens = _tokens(purpose_text.replace("_", "-"))
    has_read_terms = bool(tokens & READ_ONLY_TERMS)
    has_write_terms = bool(tokens & WRITE_TERMS)
    return has_read_terms and not has_write_terms


def _tool_matches_prohibited_action(tool: Tool, prohibited_action: str) -> bool:
    action_tokens = _tokens(prohibited_action)
    if not action_tokens:
        return False
    action, qualifier, safeguard = _split_qualifier(prohibited_action)
    safeguard_words = _word_stems(safeguard)
    # A tool named for the whole safeguard a "without" clause names is that
    # safeguard: ``send_email_preview`` against "send external email without
    # preview" is the preview, not the unpreviewed send. Only the whole
    # clause counts — ``send_customer_email`` against "send email without
    # customer consent" is the send (#1035). The risk-tag rules still run.
    names_safeguard = (
        qualifier == "without"
        and bool(safeguard_words)
        and safeguard_words <= _word_stems(tool.name)
    )
    if not names_safeguard and _words_match(_parse_action(action), _parse_tool(tool)):
        return True
    if {"email", "external"} & action_tokens and has_risk_tag(
        tool,
        {"external_write", "customer_communication"},
        min_confidence="medium",
    ):
        return True
    if "refund" in action_tokens and has_risk_tag(
        tool,
        {"financial_action"},
        min_confidence="medium",
    ) and is_write_tool(tool):
        return True
    if "cancel" in action_tokens and has_risk_tag(
        tool,
        {"destructive"},
        min_confidence="medium",
    ):
        return True
    return False


def _prohibited_action_is_mitigated(
    context: ScanContext, tool: Tool, prohibited_action: str
) -> bool:
    tokens = _tokens(prohibited_action)
    if "approval" in tokens and "approval" in tool.resolved_controls:
        return True
    if "confirmation" in tokens and "confirmation" in tool.resolved_controls:
        return True
    if "idempotency" in tokens and "idempotency" in tool.resolved_controls:
        return True
    return False


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]+", text.lower())
        if token not in STOPWORDS
    }


def _split_qualifier(text: str) -> tuple[str, str | None, str]:
    """Split a prohibited action at its first qualifier word.

    ``"send external email without preview"`` → ``("send external email ",
    "without", " preview")``. Without a qualifier the whole text is the action.
    """

    parts = re.split(
        rf"\b({'|'.join(_QUALIFIERS)})\b", text, maxsplit=1, flags=re.IGNORECASE
    )
    if len(parts) == 3:
        return parts[0], parts[1].lower(), parts[2]
    return text, None, ""


@dataclass(frozen=True)
class _ActionWords:
    """A prohibited action read as verbs and the things they act on."""

    verbs: frozenset[str]
    # The direct object: nouns before the verb back to a preposition ("customer
    # data must never be exported") and after it up to one ("run reports for
    # customers" → ``report``).
    objects: frozenset[str]
    # The phrase after that preposition, used only when there is no direct
    # object: "deploy to production", "push to main".
    destinations: frozenset[str]


@dataclass(frozen=True)
class _ToolClause:
    verb: str
    objects: frozenset[str]


@dataclass(frozen=True)
class _ToolWords:
    # One clause from the name and one from the description's first sentence.
    clauses: tuple[_ToolClause, ...]
    words: frozenset[str]


def _words_match(action: _ActionWords, tool: _ToolWords) -> bool:
    """Whether a tool clause has the action's verb and acts on its object.

    Whole words on both sides, never substrings (#1035). Containment let "or"
    match "priority", "for" and "order", so adding "for a customer" to a
    description was enough to overlap "delete or export customer data". The
    verb and the object must come from the *same* tool clause: ``close_ticket``
    closes a ticket, so a description that mentions a customer does not make
    it "close customer accounts".
    """

    for clause in tool.clauses:
        if clause.verb not in action.verbs:
            continue
        if action.objects:
            if action.objects & clause.objects:
                return True
        elif action.destinations & tool.words:
            return True
    return False


def _parse_action(text: str) -> _ActionWords:
    sequence = _sequence(text, conjunctions=True)
    words = [
        (index, stem)
        for index, stem in enumerate(sequence)
        if stem is not None and stem != _CONJUNCTION
    ]
    # The verb is the first listed verb, joined by "or"/"and" to any that
    # follow it ("delete or export customer data"). A listed verb anywhere
    # else is a noun: the email in "send email", the refunds in "issue
    # refunds".
    first = next((index for index, stem in words if _action_verb(stem) is not None), None)
    if first is not None:
        positions = [first]
        while (
            positions[-1] + 2 < len(sequence)
            and sequence[positions[-1] + 1] == _CONJUNCTION
            and (follower := sequence[positions[-1] + 2]) is not None
            and _action_verb(follower) is not None
        ):
            positions.append(positions[-1] + 2)
        verbs = {_action_verb(str(sequence[index])) for index in positions}
    else:
        # No listed verb: a noun that names one ("customer data deletion"),
        # else the leading word ("tweet").
        first = next((index for index, stem in words if _nominal_verb(stem) is not None), None)
        if first is not None:
            verbs = {_nominal_verb(str(sequence[first]))}
        elif words:
            first = words[0][0]
            verbs = {_SYNONYM_STEMS.get(words[0][1], words[0][1])}
        else:
            return _ActionWords(frozenset(), frozenset(), frozenset())
        positions = [first]
    objects: list[str] = []
    for stem in reversed(sequence[:first]):
        if stem is None:
            break
        if stem != _CONJUNCTION:
            objects.append(stem)
    index = positions[-1] + 1
    while index < len(sequence) and sequence[index] is not None:
        if sequence[index] != _CONJUNCTION:
            objects.append(str(sequence[index]))
        index += 1
    destinations: list[str] = []
    index += 1
    while index < len(sequence) and sequence[index] is not None:
        if sequence[index] != _CONJUNCTION:
            destinations.append(str(sequence[index]))
        index += 1
    return _ActionWords(
        frozenset(verb for verb in verbs if verb),
        frozenset(objects),
        frozenset(destinations),
    )


def _parse_tool(tool: Tool) -> _ToolWords:
    clauses: list[_ToolClause] = []
    segments = [segment for segment in re.split(r"[./:\s]+", tool.name) if segment]
    if segments:
        last = [stem for stem in _sequence(segments[-1]) if stem is not None]
        namespace = {
            stem
            for segment in segments[:-1]
            for stem in _sequence(segment)
            if stem is not None
        }
        position = next(
            (index for index, stem in enumerate(last) if _tool_verb(stem) is not None),
            None,
        )
        if position is not None:
            if len(last) == 1:
                objects = namespace  # ``users.remove``, ``invoice.void``
            elif position == len(last) - 1:
                objects = set(last[:position])  # noun-first: ``disk_wipe``
            else:
                objects = set(last[position + 1 :])  # verb-first: ``create_issue``
            clauses.append(_ToolClause(str(_tool_verb(last[position])), frozenset(objects)))
    sentence = re.split(r"[.!?;\n]", tool.description or "", maxsplit=1)[0]
    described = _sequence(sentence)
    if described and described[0] is not None and _tool_verb(described[0]) is not None:
        span: list[str] = []
        for stem in described[1:]:
            if stem is None:
                break
            span.append(stem)
        if span:
            # The head of the object phrase: "Close a customer's support
            # *ticket*" acts on a ticket, not on a customer.
            clauses.append(_ToolClause(str(_tool_verb(described[0])), frozenset(span[-1:])))
    return _ToolWords(
        tuple(clauses),
        frozenset(_word_stems(f"{tool.name} {tool.description or ''}")),
    )


def _action_verb(stem: str) -> str | None:
    canonical = _SYNONYM_STEMS.get(stem, stem)
    return canonical if canonical in _ACTION_VERB_STEMS else None


def _nominal_verb(stem: str) -> str | None:
    verb = _NOMINAL_STEMS.get(stem)
    return _SYNONYM_STEMS.get(verb, verb) if verb is not None else None


def _tool_verb(stem: str) -> str | None:
    return (
        _action_verb(stem)
        or _nominal_verb(stem)
        or (stem if stem in _TOOL_VERB_STEMS else None)
    )


def _sequence(text: str, *, conjunctions: bool = False) -> list[str | None]:
    """The words of ``text`` in order as stems, with ``None`` for a preposition.

    Stopwords and one-letter tokens are dropped; the two-word spellings in
    ``_PHRASES`` are joined first. With ``conjunctions``, "and", "or" and
    "nor" are kept as ``_CONJUNCTION``.
    """

    out: list[str | None] = []
    for word in _raw_words(text):
        if word in _PREPOSITIONS:
            out.append(None)
        elif conjunctions and word in {"and", "nor", "or"}:
            out.append(_CONJUNCTION)
        elif len(word) > 1 and word not in STOPWORDS:
            out.append(_stem(word))
    return out


def _raw_words(text: str) -> list[str]:
    """Lower-case words of ``text``, split on punctuation and camelCase.

    A tool named ``crm.delete_customer_data`` or ``deleteCustomerData``
    contributes ``delete``, ``customer`` and ``data``. "shut down" becomes
    ``shutdown`` and "e-mail" ``email``.
    """

    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    raw = re.findall(r"[a-z][a-z0-9]*", spaced.lower())
    words: list[str] = []
    index = 0
    while index < len(raw):
        phrase = _PHRASES.get((raw[index], raw[index + 1])) if index + 1 < len(raw) else None
        if phrase is not None:
            words.append(phrase)
            index += 2
            continue
        words.append(raw[index])
        index += 1
    return words


def _canonical_words(text: str) -> list[str]:
    """The stems of ``text`` in order, with prepositions and stopwords dropped.

    :func:`_stem` makes "refunds" meet "refund" and "deletes" meet "delete" —
    the inflections substring containment used to catch, without its false
    matches inside other words. No synonym is applied: synonyms are for verbs.
    """

    return [stem for stem in _sequence(text) if stem is not None]


def _word_stems(text: str) -> set[str]:
    """The set of :func:`_canonical_words` of ``text``."""

    return set(_canonical_words(text))


def _stem(word: str) -> str:
    """A deliberately small English stemmer, applied to both sides alike.

    Strips a plural or third-person ``-s``, then ``-ing`` or ``-ed``, then a
    final ``e``, then one letter of a final doubled consonant, each only while
    at least three letters remain. "Deletes", "deleted" and "deleting" all
    become "delet"; "cancelled" and "cancel" both become "cancel". It only
    has to make two spellings of one word agree, never produce a real word.
    """

    stem = word
    if stem.endswith("s") and not stem.endswith("ss") and len(stem) >= 4:
        stem = stem[:-1]
    for suffix in ("ing", "ed"):
        if stem.endswith(suffix) and len(stem) - len(suffix) >= 3:
            stem = stem[: -len(suffix)]
            break
    if stem.endswith("e") and len(stem) >= 4:
        stem = stem[:-1]
    if len(stem) >= 4 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
        stem = stem[:-1]
    return stem


_SYNONYM_STEMS = {_stem(word): _stem(canonical) for word, canonical in ACTION_SYNONYMS.items()}
_ACTION_VERB_STEMS = frozenset(
    _SYNONYM_STEMS.get(_stem(verb), _stem(verb)) for verb in ACTION_VERBS
)
_NOMINAL_STEMS = {_stem(noun): _stem(verb) for noun, verb in ACTION_NOMINALIZATIONS.items()}
# Sequence marker for "and"/"or"/"nor"; never a stem (stems are lower-case words).
_CONJUNCTION = "&"
_TOOL_VERB_STEMS = frozenset(_stem(verb) for verb in TOOL_VERBS)
