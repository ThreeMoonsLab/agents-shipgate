"""Advisory declaration scaffold for evidence gaps that need a human.

Several evidence gaps can only be closed by a reviewed human declaration —
what a tool's effect is, what authority it runs with, which object is the root
agent. The decision engine already generates the exact manifest snippet each
one wants (``EvidenceGapAction.declaration_template``), but until now those
snippets were only reachable inside ``report.json`` at
``release_decision.evidence_coverage.evidence_gaps[].next_action.declaration_template``,
which made a one-time, three-line task look like schema archaeology.

This module assembles them into one reviewable YAML snippet next to
``report.json``, the same way ``suggested-inventory.json`` is written for
low-confidence sources. It asserts nothing: every value the human owns stays
``<REVIEW_REQUIRED>``, and a file full of sentinels satisfies no gap. Deciding
remains entirely the decision engine's job.

**Self-sufficiency is the point** (#388). The file a user is told to edit was
the one file that did not say what a legal answer looks like: ``effect:
<REVIEW_REQUIRED>`` with the nine accepted values sitting in ``report.json``,
and an ``agent_bindings.root`` block with two blanks whose answer the scan had
already observed. Every sentinel now carries the vocabulary or the shape it
takes, as a comment, and where the scan observed candidates they are listed for
a human to confirm. Nothing about the human declaration property changes — a
comment is not a value.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

import yaml

from agents_shipgate.ci.release_decision import REVIEW_REQUIRED_SENTINEL
from agents_shipgate.core.evidence_actions import display_literal, yaml_scalar
from agents_shipgate.schemas.bindings import AgentBindingNode
from agents_shipgate.schemas.report import EvidenceGap, ReadinessReport

# Which template field an action's ``accepted_values`` is the vocabulary FOR.
#
# ``accepted_values`` is overloaded on the wire: for most gap kinds it lists
# the manifest *keys* a repair must set (``["agent", "complete:true", …]``),
# and for these two it lists the legal *values* of one field. Only the second
# reading may be printed above a blank — rendering key names as "accepted
# values" would tell a reviewer that `agent` is a legal `effect`.
#
# The values themselves are never copied here. The annotation is rendered from
# the gap's own ``accepted_values``, so the two artifacts cannot disagree about
# the vocabulary; only this routing could ever drift, and a test pins it.
_VOCABULARY_FIELD_BY_ACTION_KIND: dict[str, str] = {
    "declare_action_effect": "effect",
    "declare_action_authority": "authority.mode",
}

# Fields whose answer is not drawn from a closed set. A vocabulary cannot be
# printed for these, so the comment says what shape the answer takes and which
# other answers make the field required — the co-requirement rules the header
# states in general, restated where the reviewer's cursor actually is.
#
# Keyed by the tail of the template path, matched longest-suffix-first, so one
# entry covers ``google_adk.tool_inventories[].path`` and its three sibling
# framework blocks.
_FIELD_HINTS: dict[str, str] = {
    "authority.auth_type": (
        "how this action authenticates, in your own words "
        "(api_key, oauth2, service_account, workload_identity, …). "
        "Required for every mode except `none`; delete this line for `none`."
    ),
    "authority.reason": (
        "why this authority is the right one. Required for `unscoped` and "
        "`ambient`; optional otherwise."
    ),
    "scopes": (
        "the exact permission strings this action is granted, one per line. "
        "Required and non-empty for `mode: scoped`; must be empty (delete "
        "this block) for every other mode."
    ),
    "root.object": (
        "the agent's declared name — what `Agent(name=…)` was given, not the "
        "Python variable it was assigned to."
    ),
    "root.source_id": "the shipgate.yaml#tool_sources[].id that defines it.",
    "declarations.complete": (
        "`true`, and only once you have checked BOTH lists below — the tools "
        "and the handoffs. This one word closes the world over each of them: "
        "add anything reachable that is missing (a dynamically wired tool, a "
        "sub-agent nothing static names) and delete anything listed that this "
        "agent cannot reach. Both lists were read off what was observed; the "
        "claim that they are complete is yours."
    ),
    "declarations.reason": (
        "how you checked both lists — the files and constructs you read for "
        "the tools AND for the handoffs, so the next reviewer can re-check "
        "them."
    ),
    "declarations.handoffs": (
        "every agent this one can hand off to, by name. Covered by "
        "`complete:` above, so an agent missing here is asserted unreachable."
    ),
    "override": (
        "this block is the second of two ways out. Either delete it and raise "
        "`effect` to the observed effect listed below, or keep `effect` as it "
        "stands and fill in the reason — the declaration wins either way, but "
        "a declaration below the evidence is recorded rather than assumed."
    ),
    "override.evidence": (
        "filled in from what this scan observed; leave it exactly as written. "
        "An override is only accepted when it names precisely the inferred "
        "effects sitting above the declared one, so that new evidence — or "
        "evidence that has since disappeared — re-opens the question instead "
        "of passing under an old answer."
    ),
    "override.reason": (
        "why the declared effect is right despite the evidence above, in your "
        "own words. This is a reviewed exception, not a correction: it is "
        "always reported for a reviewer to read, and it never blocks."
    ),
    "tool_inventories.path": (
        "repo-relative path where you saved the reviewed inventory (for "
        "example `inventories/tools.json`). Start from the skeleton written "
        "next to report.json."
    ),
}

# Enough candidates to choose from without turning the block into a listing.
_MAX_RENDERED_CANDIDATES = 10


def scaffold_for_report(report: ReadinessReport) -> str | None:
    """Render the scaffold for a report, or ``None`` when nothing is owed."""

    decision = report.release_decision
    if decision is None or decision.evidence_coverage is None:
        return None
    return build_declaration_scaffold(
        decision.evidence_coverage.evidence_gaps,
        agents=report.binding_surface_facts.agents,
    )


def build_declaration_scaffold(
    gaps: Sequence[EvidenceGap],
    *,
    agents: Sequence[AgentBindingNode] = (),
) -> str | None:
    """Render the paste-ready scaffold, or ``None`` when nothing is owed.

    Deterministic: gaps are consumed in the order the decision engine emitted
    them, and templates aimed at the same manifest target are merged once.

    ``agents`` is the binding graph's observed agent nodes. They are rendered
    as commented candidates under an ``agent_bindings.root`` block — the value
    the scan computed, offered for confirmation rather than asserted (#388).
    """

    # Group by the manifest path plus the subject the template is about, then
    # merge. Two gaps on one tool (an undeclared effect and an undeclared
    # authority) want ONE ``action_surface.actions`` row, so emitting them as
    # two blocks would hand the human something invalid to paste.
    sections: list[dict[str, Any]] = []
    by_target: dict[tuple[str, str, str], dict[str, Any]] = {}
    for gap in gaps:
        action = gap.next_action
        template = getattr(action, "declaration_template", None)
        if not isinstance(template, dict) or not template:
            continue
        path = str(getattr(action, "path", "") or "shipgate.yaml")
        # Key on the rendered selector, not the display name: two canonical
        # tools can share a name, and folding those into one row would produce
        # a declaration that resolves neither of them.
        target = (
            path,
            str(gap.subject or ""),
            str(template.get("tool_id") or template.get("tool") or ""),
        )
        vocabulary = _vocabulary_for(action)
        existing = by_target.get(target)
        if existing is None:
            entry: dict[str, Any] = {
                "path": path,
                "kinds": [str(gap.kind)],
                "template": dict(template),
                "vocabulary": dict(vocabulary),
            }
            by_target[target] = entry
            sections.append(entry)
            continue
        if str(gap.kind) not in existing["kinds"]:
            existing["kinds"].append(str(gap.kind))
        for key, value in template.items():
            existing["template"].setdefault(key, value)
        for field, values in vocabulary.items():
            existing["vocabulary"].setdefault(field, values)

    sections = _drop_duplicate_blocks(sections)
    if not sections:
        return None

    lines = [
        "# Declaration scaffold generated by agents-shipgate.",
        "#",
        "# Each block below is what one evidence gap needs before this repository",
        f"# can reach a `passed` verdict. Replace every {REVIEW_REQUIRED_SENTINEL}",
        "# with a reviewed value, merge the block into shipgate.yaml at the path",
        "# named above it, then re-run verification.",
        "#",
        "# These are human declarations on purpose. Agents Shipgate will not guess",
        "# a tool's effect, its authority, or which object is the root agent, and",
        f"# a block still containing {REVIEW_REQUIRED_SENTINEL} closes nothing.",
        "#",
        "# Every blank carries the values it accepts, or the shape its answer",
        "# takes, on the comment line above it. Where a field is only required",
        "# for some answers the comment says so — delete the lines your answer",
        "# does not take.",
    ]
    for entry in sections:
        # Each block is its own YAML document. Concatenated mappings would
        # repeat top-level keys (two `tool:` roots), which is not a file a
        # reader or a parser can make sense of.
        lines.append("")
        lines.append("---")
        lines.append(f"# closes: {', '.join(entry['kinds'])}")
        lines.append(f"# merge into: {entry['path']}")
        _emit_mapping(
            entry["template"],
            path="",
            depth=0,
            vocabulary=entry["vocabulary"],
            agents=agents,
            out=lines,
        )
    return "\n".join(lines) + "\n"


def _vocabulary_for(action: Any) -> dict[str, list[str]]:
    """The ``field -> accepted values`` this action publishes, if any."""

    field = _VOCABULARY_FIELD_BY_ACTION_KIND.get(str(getattr(action, "kind", "")))
    values = list(getattr(action, "accepted_values", ()) or ())
    return {field: values} if field and values else {}


def _drop_duplicate_blocks(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse blocks that would render identically at the same path.

    One mechanism restated per subject produces one gap row per subject — six
    unresolved ADK tool symbols are six ``source_warning`` rows — and each
    carries the same repair. Keyed on the subject alone those are six identical
    ``tool_inventories`` blocks to paste, which reads as six separate things to
    do. Byte-identical content at one path is one instruction.
    """

    kept: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in sections:
        key = (entry["path"], json.dumps(entry["template"], sort_keys=True, default=str))
        first = seen.get(key)
        if first is None:
            seen[key] = entry
            kept.append(entry)
            continue
        for kind in entry["kinds"]:
            if kind not in first["kinds"]:
                first["kinds"].append(kind)
        for field, values in entry["vocabulary"].items():
            first["vocabulary"].setdefault(field, values)
    return kept


def _hint_for(path: str) -> str | None:
    """The guidance registered for this template path, longest suffix first."""

    parts = path.split(".")
    for start in range(len(parts)):
        hint = _FIELD_HINTS.get(".".join(parts[start:]))
        if hint is not None:
            return hint
    return None


def _annotate(
    path: str,
    depth: int,
    vocabulary: dict[str, list[str]],
    agents: Sequence[AgentBindingNode],
    out: list[str],
) -> None:
    """Write the comment lines that belong above the value at ``path``."""

    pad = "  " * depth
    for line in _candidate_lines(path, agents):
        out.append(f"{pad}# {line}")
    hint = _hint_for(path)
    if hint is not None:
        out.extend(_wrapped_comment(hint, pad))
    values = vocabulary.get(path)
    if values:
        out.extend(_wrapped_comment("accepted: " + " | ".join(values), pad))


def _candidate_lines(
    path: str, agents: Sequence[AgentBindingNode]
) -> list[str]:
    """Observed root-agent candidates, for a human to confirm.

    Instance 1 of #388: the scan resolves the agent objects and then hands the
    reader two blanks. Naming what it saw removes the guessing game without
    filling anything in — inferring ``agent_bindings.root`` from AST evidence
    is the self-declaration surface #268 closed, and this does not do that.
    """

    if not path.endswith("agent_bindings.root") or not agents:
        return []
    lines = [
        "shipgate observed these agent objects — confirm one and fill it in:"
    ]
    # By name rather than by the graph's agent_id order: the reader is choosing
    # between names, and an id-hash ordering looks arbitrary to them.
    ordered = sorted(agents, key=lambda agent: (agent.name, agent.source_id or ""))
    for agent in ordered[:_MAX_RENDERED_CANDIDATES]:
        # ``display_literal`` because these are repository-controlled identity
        # strings, and the caller writes them into a YAML `#` comment. An
        # `Agent(name="...\nroot:\n  object: X")` would otherwise close the
        # comment and render a *filled-in* root block for a reader to paste —
        # a self-declaration the scaffold exists to refuse (#268). Injective,
        # so the escape still names exactly one object.
        name = display_literal(agent.name)
        source = (
            f", source_id: {display_literal(agent.source_id)}"
            if agent.source_id
            else ""
        )
        where = (
            f"  ({display_literal(agent.source_ref)})" if agent.source_ref else ""
        )
        lines.append(f"    object: {name}{source}{where}")
    remaining = len(agents) - _MAX_RENDERED_CANDIDATES
    if remaining > 0:
        lines.append(
            f"    (+{remaining} more — see report.json "
            "binding_surface_facts.agents)"
        )
    return lines


def _wrapped_comment(text: str, pad: str) -> list[str]:
    """``text`` as comment lines that stay inside a readable column."""

    limit = 78
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and len(pad) + 2 + len(candidate) > limit:
            lines.append(f"{pad}# {current}")
            current = word
            continue
        current = candidate
    if current:
        lines.append(f"{pad}# {current}")
    return lines


def _scalar(value: Any) -> str:
    """``value`` as the one-line YAML scalar ``yaml.safe_dump`` would emit.

    One line is the load-bearing part. ``safe_dump`` renders a string holding a
    newline as a *multi-line* single-quoted scalar whose continuation lines it
    indents relative to the key it is dumping — knowledge this emitter does not
    have when it formats a leaf on its own, so pasting the result at depth 2
    produced continuation lines indented less than their key. Tool names come
    from repository JSON, so that is reachable input, not a hypothetical.

    ``yaml_scalar`` is the escape hatch for exactly those: a JSON string is a
    valid YAML double-quoted scalar, total over anything a loader can produce,
    and always one line. Ordinary values never reach it, so the common
    rendering stays byte-identical to ``safe_dump``.
    """

    text = yaml.safe_dump(
        value, default_flow_style=True, sort_keys=False, width=1 << 30
    ).strip()
    if text.endswith("..."):
        text = text[:-3].strip()
    # Only a string can carry the newline that forces the multi-line form.
    return yaml_scalar(value) if isinstance(value, str) and "\n" in text else text


def _emit_mapping(
    node: dict[str, Any],
    *,
    path: str,
    depth: int,
    vocabulary: dict[str, list[str]],
    agents: Sequence[AgentBindingNode],
    out: list[str],
    first_prefix: str | None = None,
) -> None:
    """Render a mapping in the block layout ``yaml.safe_dump`` produces.

    Hand-rolled because comments have to be interleaved and PyYAML has no way
    to carry them. The layout is not a second opinion about YAML style: a test
    asserts that stripping the comment lines back out yields exactly what
    ``yaml.safe_dump`` writes for the same template, so the two cannot drift.

    ``first_prefix`` is the ``- `` of an enclosing sequence item, which YAML
    puts on the same line as the item's first key.
    """

    pad = "  " * depth
    for index, (key, value) in enumerate(node.items()):
        child = f"{path}.{key}" if path else str(key)
        on_dash = index == 0 and first_prefix is not None
        lead = first_prefix if on_dash else pad
        # The dash line's own annotation belongs above the dash, at the
        # sequence's indentation rather than the item's.
        _annotate(child, depth - 1 if on_dash else depth, vocabulary, agents, out)
        if isinstance(value, dict) and value:
            out.append(f"{lead}{key}:")
            _emit_mapping(
                value,
                path=child,
                depth=depth + 1,
                vocabulary=vocabulary,
                agents=agents,
                out=out,
            )
        elif isinstance(value, (list, tuple)):
            if not value:
                out.append(f"{lead}{key}: []")
                continue
            out.append(f"{lead}{key}:")
            _emit_sequence(
                value,
                path=child,
                depth=depth,
                vocabulary=vocabulary,
                agents=agents,
                out=out,
            )
        else:
            # An empty mapping renders through the same scalar path (`{}`).
            out.append(f"{lead}{key}: {_scalar(value)}")


def _emit_sequence(
    items: Iterable[Any],
    *,
    path: str,
    depth: int,
    vocabulary: dict[str, list[str]],
    agents: Sequence[AgentBindingNode],
    out: list[str],
) -> None:
    """Render a sequence: dashes at the key's indent, content one deeper.

    Scalar items carry no annotation of their own — the guidance for a list
    (``scopes``) is about the list, and was already written above its key.
    """

    pad = "  " * depth
    for item in items:
        if isinstance(item, dict) and item:
            _emit_mapping(
                item,
                path=path,
                depth=depth + 1,
                vocabulary=vocabulary,
                agents=agents,
                out=out,
                first_prefix=f"{pad}- ",
            )
            continue
        out.append(f"{pad}- {_scalar(item)}")


__all__ = ["build_declaration_scaffold", "scaffold_for_report"]
