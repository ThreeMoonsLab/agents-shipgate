"""One ranking of evidence gaps, shared by every surface that names one.

An ``insufficient_evidence`` verdict is reported in three places at once: the
decision ``reason``, the ``Improve evidence:`` line under it, and
``agent_summary.first_recommended_action`` — the field the agent contract
routes coding agents to. Each of them used to answer "what is wrong here?"
separately, so the reason led with a symptom count while the line beneath it
named a concrete file and the field agents read said no machine-applicable fix
existed. They now project the same selected gap through this module.

Selection is ranking only: ``evidence_gaps`` is a projection of the counts
``build_release_decision`` already decided on, so choosing a different gap to
lead with can never move a verdict.
"""

from __future__ import annotations

import re
import unicodedata

from agents_shipgate.schemas.report import EvidenceCoverageDecision, EvidenceGap

# Longest subject we inline into a one-line headline. Gap subjects are
# usually short identifiers, but ``source_warning`` rows carry the whole
# warning text; a headline is a lead, not the evidence.
_MAX_SUBJECT_CHARS = 120

# Three separate questions, deliberately kept apart (#362 review 4):
#
#   1. *Display* — how does this value render on one line without forging a
#      second one? ``one_line``. It never deletes a character that carries
#      identity, because the value it is rendering is a real repository path
#      or instruction and a silently different string is a lie about the
#      repository.
#   2. *Visibility* — does this value name anything a reader could see and
#      open? ``has_visible_content``. A string made only of invisible
#      code points names nothing, whatever its length.
#   3. *Executability* — is this command safe to publish **as written**?
#      ``is_publishable_command``. Nothing here ever rewrites a command:
#      deleting a zero-width character from ``r​m -rf`` produces a
#      different program, so an unsafe command is suppressed, never repaired.

# ``\s`` is Unicode-aware, so U+3000 and friends collapse too.
_WHITESPACE_RUN = re.compile(r"\s+")

# Rewriting the text after them is the whole point of these, so they are the
# one class that is escaped rather than passed through: left intact, a forged
# suffix can be made to display as if it were the real target.
_BIDI_CONTROLS = frozenset(
    "؜‎‏‪‫‬‭‮⁦⁧⁨⁩"
)

# Unicode Default_Ignorable_Code_Point, the code points that render as
# nothing. Used for *visibility*, never for rewriting: a joiner inside
# ``agents/👩‍💻.yaml`` or a Persian identifier's ZWNJ is load-bearing,
# so it stays in the display and only an all-invisible value is rejected.
_DEFAULT_IGNORABLE_RANGES: tuple[tuple[int, int], ...] = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)


def _is_default_ignorable(char: str) -> bool:
    point = ord(char)
    return any(start <= point <= end for start, end in _DEFAULT_IGNORABLE_RANGES)


def _escape(char: str) -> str:
    return f"<U+{ord(char):04X}>"


def one_line(value: str) -> str:
    """Render a repository-derived value safely on one line, without rewriting it.

    Whitespace runs collapse to a single space; control characters (``Cc``)
    and bidi controls become a visible ``<U+XXXX>`` escape. Everything else —
    every visible script, and every invisible joiner that carries identity —
    passes through unchanged.

    Escaping rather than deleting is the point. An earlier version dropped
    general category ``Cf`` wholesale, which turned ``agents/👩‍💻.yaml``
    into a different filename and, worse, let a command be *repaired* into a
    program the repository never wrote. A display projection may make a value
    legible; it may not make it something else.

    This answers only the display question. Ask :func:`has_visible_content`
    whether a value names anything, and :func:`is_publishable_command` whether
    a command may be handed to anyone.
    """

    escaped = "".join(
        _escape(char)
        if (unicodedata.category(char) == "Cc" or char in _BIDI_CONTROLS)
        and not char.isspace()
        else char
        for char in value
    )
    return _WHITESPACE_RUN.sub(" ", escaped).strip()


def has_visible_content(value: str) -> bool:
    """True when at least one character renders as something a reader can see.

    Whitespace, controls, unassigned/surrogate/private-use code points, and
    Default_Ignorable code points (ZWSP, ZWJ, VS16, CGJ, bidi controls, …)
    all render as nothing on their own. A "path" made only of those names no
    surface, however long the string is.
    """

    return any(
        not char.isspace()
        and unicodedata.category(char) not in {"Cc", "Cf", "Cs", "Co", "Cn"}
        and not _is_default_ignorable(char)
        for char in value
    )


def is_publishable_command(value: str | None) -> bool:
    """True when a command can be handed over exactly as written.

    Deliberately all-or-nothing. Sanitizing a command is not a safe operation:
    removing a zero-width character from ``r​m -rf /tmp/x`` yields a
    command that does something the original could not. So a command
    containing any control, bidi, or invisible code point is suppressed
    entirely — the caller publishes no affordance rather than a repaired one.
    """

    if value is None:
        return False
    candidate = value.strip()
    if not has_visible_content(candidate):
        return False
    return not any(
        unicodedata.category(char) in {"Cc", "Cf", "Cs", "Co", "Cn"}
        or char in _BIDI_CONTROLS
        or _is_default_ignorable(char)
        or (char.isspace() and char != " ")
        for char in candidate
    )


# One short phrase per gap kind, in the voice of "what is unproven here".
# ``test_every_evidence_gap_kind_has_a_phrase`` pins this to the schema
# Literal so a new gap kind cannot ship with a raw enum name as its copy.
_GAP_PHRASE: dict[str, str] = {
    "low_confidence_tool": "a tool was extracted with low confidence",
    "source_warning": "a source loader degraded while reading declared inputs",
    "incomplete_surface": "the tool surface could not be fully enumerated",
    "missing_effect_evidence": "an action has no declared effect",
    "inferred_effect_only": "an action's effect is inferred, not declared",
    "conflicting_effect_evidence": "an action carries conflicting effect evidence",
    "missing_authority_evidence": "an action has no declared authority",
    "partial_authority_evidence": "an action's authority is only partly declared",
    "conflicting_authority_evidence": "an action carries conflicting authority evidence",
    "invalid_semantic_annotation": "a semantic annotation is invalid",
    "incomplete_tool_identity": "a tool identity is incomplete",
    "conflicting_tool_identity": "bound observations disagree about one tool identity",
    "unresolved_tool_selector": "a manifest tool selector resolves to nothing",
    "ambiguous_tool_selector": "a manifest tool selector resolves to several tools",
    "ambiguous_legacy_tool_identity": "a legacy tool identity is ambiguous",
    "invalid_tool_binding": "a tool_identity binding does not apply",
    "missing_binding_evidence": "the agent's tool bindings are unproven",
    "partial_binding_evidence": "the agent's tool binding graph is incomplete",
    "conflicting_binding_evidence": "declared and structural binding evidence disagree",
    "ambiguous_root_agent": "the root agent is ambiguous",
    "unresolved_agent_binding": "an agent binding target does not resolve",
    "unresolved_bound_tool": "a bound tool does not resolve",
    "incomplete_handoff_graph": "the agent handoff graph is incomplete",
    "invalid_binding_annotation": "a binding annotation is invalid",
    "invalid_evidence_provenance": "an evidence provenance claim is invalid",
    "inferred_policy_applicability": "policy applicability is inferred, not declared",
    "mixed_policy_evidence": "policy evidence mixes declared and inferred sources",
    "unknown_policy_evidence": "policy applicability is unknown",
    "conflicting_policy_evidence": "policy evidence conflicts",
}


def evidence_gap_target(gap: EvidenceGap) -> str:
    """The surface a gap names, rendered for display — empty when it names none.

    The schema accepts any string for ``next_action.path``, including one made
    only of whitespace, controls, or invisible code points. Such a value names
    nothing, so deciding on the raw string put a blank row ahead of a real one:
    it won ranking, printed ``Fix at .``, hid the real target from every
    surface, and suppressed the truthful no-machine-fix route.

    Visibility decides; :func:`one_line` only renders. A path containing a
    joiner among visible characters keeps the joiner.
    """

    path = gap.next_action.path or ""
    return one_line(path) if has_visible_content(path) else ""


def evidence_gap_command(gap: EvidenceGap) -> str:
    """The command a gap offers, if it can be published exactly as written.

    Empty when the action carries no command or when the command is not
    publishable — see :func:`is_publishable_command` for why an unsafe command
    is dropped rather than cleaned up.
    """

    command = gap.next_action.command
    return command.strip() if is_publishable_command(command) else ""


def is_addressable_gap(gap: EvidenceGap) -> bool:
    """True when the gap offers somewhere to go or something to run.

    Both halves count. ``path`` and ``command`` are independently nullable on
    the wire, and a ``provide_source`` row carrying only an exact regeneration
    command is as actionable as one naming a file — reading the path alone let
    ``Improve evidence:`` print ``Run: …`` while the field agents read said no
    machine-applicable fix existed (#362 review 4).
    """

    return bool(evidence_gap_target(gap)) or bool(evidence_gap_command(gap))


def actionable_evidence_gaps(evidence: EvidenceCoverageDecision) -> list[EvidenceGap]:
    """Every gap whose next action names a file, key, or pointer to open.

    A gap without one still needs fixing, but only a human deciding what
    evidence to go find can close it.
    """

    return [gap for gap in evidence.evidence_gaps if is_addressable_gap(gap)]


def primary_evidence_gap(evidence: EvidenceCoverageDecision) -> EvidenceGap | None:
    """Rank-1 gap: the first one that names a path, else the first gap.

    ``evidence_gaps`` already arrives in the decision engine's deterministic
    order (binding, then semantic, then policy, then extraction/source), so
    preferring the first *addressable* row keeps that order and only skips
    rows nobody can act on. Returns ``None`` only for reports with no gaps at
    all — compatibility reports from before ``evidence_gaps`` existed.
    """

    addressable = actionable_evidence_gaps(evidence)
    if addressable:
        return addressable[0]
    return evidence.evidence_gaps[0] if evidence.evidence_gaps else None


def evidence_gap_headline(gap: EvidenceGap) -> str:
    """Name the gap in one clause: what is unproven, and about what."""

    phrase = _GAP_PHRASE.get(gap.kind, gap.kind.replace("_", " "))
    subject = one_line(gap.subject)
    if len(subject) > _MAX_SUBJECT_CHARS:
        subject = f"{subject[: _MAX_SUBJECT_CHARS - 1].rstrip()}…"
    return f"{phrase} ({subject})" if subject else phrase


def evidence_gap_action_text(gap: EvidenceGap, *, include_command: bool = True) -> str:
    """Render one gap's next action: what to do, and where.

    Every field here is repository-derived — a policy pack authors
    ``expects``, and a semantic gap's ``path`` embeds a tool name — and this
    text reaches the CLI ``Improve evidence:``/``Next action:`` lines and the
    GitHub step summary, none of which collapse newlines
    (``_safe_markdown_text`` escapes Markdown, not line breaks). Each field is
    forced onto one line here rather than at each call site, so a value
    carrying ``\\nControl: complete`` cannot forge a line below the real one.

    ``include_command=True`` adds the one newline this function does emit —
    the deliberate ``Run:`` separator, which
    ``cli/verify/command.py`` splits on and sanitizes line by line.
    ``include_command=False`` keeps the result strictly single-line for
    surfaces (``agent_summary.first_recommended_action.why``, the CLI
    ``Next action:`` line) whose contract is one line of text.

    A **command-only** row is the exception: when the action names no path,
    the command is the only thing locating the work, so it is rendered inline
    on single-line surfaces too rather than dropped.
    """

    action = gap.next_action
    text = one_line(action.expects)
    target = evidence_gap_target(gap)
    command = evidence_gap_command(gap)
    if target and target not in text:
        if not text.endswith((".", "!", "?")):
            text = f"{text}."
        text = f"{text} Target: {target}."
    if not command:
        return text
    if not target:
        if command not in text:
            if not text.endswith((".", "!", "?")):
                text = f"{text}."
            text = f"{text} Run: {command}."
        return text
    if include_command:
        text = f"{text}\nRun: {command}"
    return text


def evidence_gap_accepted_values(gap: EvidenceGap) -> list[str]:
    """Accepted values worth showing: normalized, blanks dropped.

    A list of blanks rendered as ``Accepted values: , .`` — an affordance that
    named nothing.
    """

    values = [one_line(value) for value in gap.next_action.accepted_values]
    return [value for value in values if has_visible_content(value)]


__all__ = [
    "actionable_evidence_gaps",
    "has_visible_content",
    "is_publishable_command",
    "evidence_gap_accepted_values",
    "evidence_gap_action_text",
    "evidence_gap_command",
    "evidence_gap_headline",
    "evidence_gap_target",
    "is_addressable_gap",
    "one_line",
    "primary_evidence_gap",
]
