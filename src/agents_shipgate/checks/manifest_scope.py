from __future__ import annotations

import re

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
# that it performs the prohibited action. The prohibited action's own leading
# word always counts as its verb ("buy stock" → ``buy``), so this list only
# has to cover a verb that is not first ("delete or export customer data" →
# ``export``). Compared after ``_canonical_words``.
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

# Spellings that name one action, so "delete customer data" meets a tool
# that "purges all data for a customer" and "modify user permissions" meets
# ``update_user_permissions`` — matches substring containment used to make by
# accident (#1035). An explicit table, applied to both sides after ``_stem``:
# every equivalence the matcher draws is listed here, and nothing is fuzzy.
ACTION_SYNONYMS = {
    "alter": "update",
    "approval": "approve",
    "cancellation": "cancel",
    "change": "update",
    "close": "terminate",
    "deletion": "delete",
    "deployment": "deploy",
    "destroy": "delete",
    "edit": "update",
    "erase": "delete",
    "execution": "execute",
    "issue": "create",
    "mail": "email",
    "make": "create",
    "modification": "update",
    "modify": "update",
    "payment": "pay",
    "purchase": "buy",
    "purge": "delete",
    "removal": "delete",
    "remove": "delete",
    "run": "execute",
    "termination": "terminate",
    "wipe": "delete",
}

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
    if qualifier == "without" and _word_stems(tool.name) & _word_stems(safeguard):
        # The tool is named for the safeguard the prohibited action lacks:
        # ``send_email_preview`` against "send external email without
        # preview" is the preview, not the unpreviewed send (#1035).
        return False
    # Whole words on both sides, never substrings (#1035). Containment let
    # "or" match "priority", "for" and "order", so adding "for a customer" to
    # a description was enough to overlap "delete or export customer data".
    # Two shared words are required, and one must be a verb: the action's own
    # leading word, or one of ``ACTION_VERBS``. A shared subject alone
    # ("customer", "data") is not the prohibited action.
    action_words = _canonical_words(action)
    overlap = set(action_words) & _word_stems(f"{tool.name} {tool.description or ''}")
    if len(overlap) >= 2 and overlap & (_ACTION_VERB_STEMS | set(action_words[:1])):
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


def _canonical_words(text: str) -> list[str]:
    """The words of ``text`` in order, as the prohibited-action matcher compares them.

    Splits on every non-alphanumeric character and on camelCase, so a tool
    named ``crm.delete_customer_data`` or ``deleteCustomerData`` contributes
    ``delete``, ``customer`` and ``data``. Joins the two-word spellings in
    ``_PHRASES``, drops stopwords and one-letter tokens, reduces each word by
    :func:`_stem` so "refunds" meets "refund" and "deletes" meets "delete" —
    the inflections substring containment used to catch, without its false
    matches inside other words — and then maps it through
    ``ACTION_SYNONYMS``.
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
    out: list[str] = []
    for word in words:
        if len(word) <= 1 or word in STOPWORDS:
            continue
        stem = _stem(word)
        out.append(_SYNONYM_STEMS.get(stem, stem))
    return out


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
