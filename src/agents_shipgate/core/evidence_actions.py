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

import json
import re
import unicodedata

from agents_shipgate.schemas.report import EvidenceCoverageDecision, EvidenceGap
from agents_shipgate.schemas.text import has_visible_content, is_default_ignorable

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
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)

def _is_noncharacter(char: str) -> bool:
    """True for a Unicode noncharacter — permanently reserved, never rendered.

    Same hazard as a Default_Ignorable code point: nothing reaches the reader,
    so two different repository objects render identically. Two of them are
    worse than invisible. PyYAML's reader accepts ``[#xE000-#xFFFD]`` and
    *rejects* U+FFFE and U+FFFF outright, so an agent name carrying one made
    the generated declaration scaffold unparseable — the document quoting the
    name in a comment could not be loaded at all (PR #401 review). Escaping
    them here fixes every sink at once rather than one file's comment writer,
    and ``undisplay_literal`` still inverts it.
    """

    point = ord(char)
    return 0xFDD0 <= point <= 0xFDEF or (point & 0xFFFE) == 0xFFFE


def _escape(char: str) -> str:
    return f"<U+{ord(char):04X}>"


_ESCAPE_INTRODUCER = "<"
_ESCAPE_PATTERN = re.compile(r"<U\+([0-9A-F]{4,6})>")


def _needs_escape(char: str, *, injective: bool) -> bool:
    """True for anything that must not reach a reader as itself.

    Three classes always: characters that could end or reorder a line;
    characters that render as nothing, and so let one value impersonate
    another — Default_Ignorable code points and Unicode noncharacters alike;
    and lone surrogates, which no UTF-8 sink accepts.

    ``injective`` adds the escape introducer. Identity-bearing values need it —
    without it ``a\nb.yaml`` and the literal filename ``a<U+000A>b.yaml``
    render the same and a reader cannot tell which file is meant. Prose does
    not: a warning is not opened as a file, and escaping every ``<`` would
    mangle ordinary text like ``<script>`` for no gain.
    """

    return (
        (injective and char == _ESCAPE_INTRODUCER)
        or unicodedata.category(char) in {"Cc", "Cs"}
        or char in _BIDI_CONTROLS
        or char in {"\u2028", "\u2029"}
        or is_default_ignorable(char)
        or _is_noncharacter(char)
    )


def yaml_scalar(value: str) -> str:
    """Render ``value`` as a YAML scalar that parses back to exactly ``value``.

    Manifest guidance interpolates identifiers a user then copies verbatim, and
    a source id is an unconstrained string that commonly embeds the configured
    path (``google_adk:agents/agent.py``). Written bare into a flow mapping, a
    value containing ``,`` splits into two keys — ``source_id: google_adk:agent``
    plus a stray ``prod.py`` — so the exact text the tool prescribed fails
    manifest validation under ``extra="forbid"`` (#386 review). ``#``, ``{``,
    ``}``, ``:`` and leading indicators are the same class of hazard.

    JSON strings are a subset of both YAML 1.1 and 1.2 double-quoted scalars,
    escape sequences included, so ``json.dumps`` is a total encoder here.

    ``ensure_ascii`` is left at its default, and that is the load-bearing part.
    Emitting non-ASCII literally is prettier but not total: PyYAML *rejects* a
    stream carrying C1 controls such as U+0080 or U+009F (and U+007F DEL), and
    silently normalizes U+0085 NEL to a space — so an id containing NEL
    round-tripped to a *different* id and the remediation named the wrong
    source, while the others made the prescribed entry unparseable outright
    (#386 review). Lone surrogates, which a path decoded with
    ``surrogateescape`` can carry, fail the same way. Escaping every non-ASCII
    code point costs readability on accented identifiers and buys a scalar that
    always parses back to the value it names.
    """

    return json.dumps(value)


def display_literal(value: str) -> str:
    """Render an *identity-bearing* value visibly, reversibly, and injectively.

    For a path or any other value that names something in the repository.
    Every character that would not reach the reader as itself becomes a
    ``<U+XXXX>`` escape; **everything else is preserved exactly** — runs of
    spaces, NBSP, U+3000, every visible script. Nothing is folded, nothing is
    trimmed, and nothing is deleted, so ``configs/foo  bar.yaml`` stays a
    two-space filename and an escaped character stays recoverable from the
    rendering.

    The encoding is **injective**, which is the point. Escaping only control
    characters left ``a\nb.yaml`` and the literal filename ``a<U+000A>b.yaml``
    rendering identically, so a reader could not tell which file was meant.
    ``<`` is therefore escaped too: a literal ``<`` renders ``<U+003C>``, and
    ``<U+000A>`` in the output can only have come from a real U+000A.

    Default_Ignorable code points are escaped rather than passed through for
    the same reason — ``shipgate\u200b.yaml`` is visually indistinguishable
    from ``shipgate.yaml`` and would name the wrong object. Escaping keeps the
    identity recoverable while making the difference visible; the durable value
    in ``report.json`` is untouched either way.

    Contrast :func:`one_line`, which additionally folds whitespace. That is
    right for prose and wrong for anything a reader will open or run.
    """

    return "".join(
        _escape(char) if _needs_escape(char, injective=True) else char
        for char in value
    )


def undisplay_literal(rendered: str) -> str:
    """Invert :func:`display_literal`.

    Exists so injectivity is testable rather than asserted: a round-trip over
    adversarial values is what proves two different repository objects cannot
    render the same way.
    """

    return _ESCAPE_PATTERN.sub(lambda match: chr(int(match.group(1), 16)), rendered)


def one_line(value: str) -> str:
    """Render repository-derived *prose* safely on one line.

    Whitespace runs collapse to a single space and line-breaking characters
    become a visible ``<U+XXXX>`` escape; every visible script and every
    invisible joiner passes through. For gap subjects, ``why``/``expects``
    text, and loader warnings — text a human reads, where collapsing a stray
    newline into a space is the friendly rendering.

    **Not** for paths or commands. Folding whitespace inside those rewrites
    them: it renames ``configs/foo  bar.yaml`` and rewrites
    ``python -c 'print("a  b")'`` into a program that prints something else.
    Use :func:`display_literal` there.

    This answers only the display question. Ask :func:`has_visible_content`
    whether a value names anything, and :func:`is_publishable_command` whether
    a command may be handed to anyone.
    """

    # Fold first, then escape. Folding a newline into a space is the friendly
    # prose rendering; escaping it first would leave `<U+000A>` mid-sentence.
    # Non-whitespace controls, bidi marks, surrogates, and invisibles still
    # escape — but not `<`, which is ordinary punctuation in a warning.
    folded = _WHITESPACE_RUN.sub(" ", value)
    return "".join(
        _escape(char) if _needs_escape(char, injective=False) else char
        for char in folded
    ).strip()


def _is_unsafe_in_command(char: str) -> bool:
    return (
        unicodedata.category(char) in {"Cc", "Cf", "Cs", "Co", "Cn"}
        or char in _BIDI_CONTROLS
        or is_default_ignorable(char)
        # Every whitespace character except U+0020. A leading NBSP, NEL, or
        # U+2028 is part of ``argv[0]``, so silently dropping it publishes a
        # different program from the one that was written.
        or (char.isspace() and char != " ")
    )


def is_publishable_command(value: str | None) -> bool:
    """True when a command can be handed over exactly as written.

    Deliberately all-or-nothing, and deliberately evaluated on the **authored
    value before any trimming**. Validating a trimmed copy let a boundary
    character be removed before it could be seen: ``"\\u00a0agents-shipgate
    scan"`` has ``shlex.split(...)[0] == "\\u00a0agents-shipgate"``, yet
    trimming first made it look like a clean ``agents-shipgate`` invocation
    and published one (#362 review 5).

    A publishable command undergoes **no transformation at all** — it is
    published byte for byte. Even trimming U+0020 was too much: ``printf
    foo\\ `` is a valid two-token command whose second argument ends in a
    space, and dropping that space leaves ``printf foo\\``, which
    ``shlex.split`` refuses to parse at all (#362 review 6). A control, a bidi
    mark, an invisible code point, or any whitespace other than U+0020
    anywhere in the string suppresses the command entirely; the caller
    publishes no affordance rather than a repaired one.
    """

    if value is None:
        return False
    if any(_is_unsafe_in_command(char) for char in value):
        return False
    return has_visible_content(value)


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
    "declaration_below_inferred_evidence": (
        "a declared effect does not account for the evidence inferred for it"
    ),
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
    return display_literal(path) if has_visible_content(path) else ""


def evidence_gap_command(gap: EvidenceGap) -> str:
    """The command a gap offers, if it can be published exactly as written.

    Empty when the action carries no command or when the command is not
    publishable — see :func:`is_publishable_command` for why an unsafe command
    is dropped rather than cleaned up.
    """

    command = gap.next_action.command
    return command if is_publishable_command(command) else ""


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
    """Rank-1 gap: the first *addressable* one, else the first gap.

    Addressable means a visible ``next_action.path`` **or** a publishable
    ``next_action.command`` — the two are independently nullable and either
    alone is actionable. ``evidence_gaps`` already arrives in the decision
    engine's deterministic order (binding, then semantic, then policy, then
    extraction/source), so preferring the first addressable row keeps that
    order and only skips rows nobody can act on. Returns ``None`` only for reports with no gaps at
    all — compatibility reports from before ``evidence_gaps`` existed.
    """

    addressable = actionable_evidence_gaps(evidence)
    if addressable:
        return addressable[0]
    return evidence.evidence_gaps[0] if evidence.evidence_gaps else None


# The same fact, said about the subject a source-scoped row actually names.
#
# A row whose subject is a ``tool_sources`` entry is about the source, and a
# phrase written for one action reads as a mismatch beside it — "an action has
# no declared authority (crm [tool_source])" describes neither the subject nor
# the edit. Keyed by kind like ``_GAP_PHRASE`` and pinned by
# ``test_every_gap_kind_a_source_can_own_has_its_own_phrase`` to the kinds that
# can carry ``subject_kind: tool_source``, so a kind that starts being routed
# to a source cannot keep an action's copy.
_SOURCE_SCOPED_GAP_PHRASE: dict[str, str] = {
    "missing_authority_evidence": "a tool source has no declared authority",
}


def evidence_gap_headline(gap: EvidenceGap) -> str:
    """Name the gap in one clause: what is unproven, and about what."""

    phrase = _GAP_PHRASE.get(gap.kind, gap.kind.replace("_", " "))
    if gap.subject_kind == "tool_source":
        phrase = _SOURCE_SCOPED_GAP_PHRASE.get(gap.kind, phrase)
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

    # Decide on the authored value and render the survivors literally: these
    # are enum-ish tokens a reader types back, not prose. Folding whitespace
    # inside one would change the token; escaping a blank one would make it
    # look like a value that exists.
    return [
        display_literal(value)
        for value in gap.next_action.accepted_values
        if has_visible_content(value)
    ]


__all__ = [
    "yaml_scalar",
    "actionable_evidence_gaps",
    "display_literal",
    "undisplay_literal",
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
