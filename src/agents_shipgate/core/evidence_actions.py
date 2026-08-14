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

from agents_shipgate.schemas.report import EvidenceCoverageDecision, EvidenceGap

# Longest subject we inline into a one-line headline. Gap subjects are
# usually short identifiers, but ``source_warning`` rows carry the whole
# warning text; a headline is a lead, not the evidence.
_MAX_SUBJECT_CHARS = 120

# A gap subject is a repository-derived value — a tool name, an agent id, a
# loader message — and the headline is interpolated into single-line surfaces
# (the CLI ``Reason:`` line, the GitHub step summary). A subject carrying
# newlines or control characters would forge lines below the real one, so
# whitespace and control runs collapse to one space before it is inlined.
_CONTROL_RUN = re.compile(r"[\s\x00-\x1f\x7f-\x9f]+")


def one_line(value: str) -> str:
    """Collapse whitespace and control runs so a value stays on its own line."""

    return _CONTROL_RUN.sub(" ", value).strip()


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
    """The surface a gap names, normalized — empty when it names none.

    The schema accepts any string for ``next_action.path``, including one
    that is only whitespace or control characters. Rendering normalizes such
    a value to nothing, so deciding addressability on the raw string put a
    blank row ahead of a real one: it won ranking, printed ``Fix at .``,
    hid the real target from every surface, and suppressed the truthful
    no-machine-fix route. Every consumer asks this function instead, so
    "addressable" means the same thing in ranking, the reason, the agent
    summary, and the verifier fix task.
    """

    return one_line(gap.next_action.path or "")


def is_addressable_gap(gap: EvidenceGap) -> bool:
    """True when the gap names a surface a coding agent can navigate to."""

    return bool(evidence_gap_target(gap))


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
    """

    action = gap.next_action
    text = one_line(action.expects)
    path = evidence_gap_target(gap)
    if path and path not in text:
        if not text.endswith((".", "!", "?")):
            text = f"{text}."
        text = f"{text} Target: {path}."
    if include_command and action.command:
        text = f"{text}\nRun: {one_line(action.command)}"
    return text


__all__ = [
    "actionable_evidence_gaps",
    "evidence_gap_action_text",
    "evidence_gap_headline",
    "evidence_gap_target",
    "is_addressable_gap",
    "one_line",
    "primary_evidence_gap",
]
