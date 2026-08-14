"""Canonical source-warning text, and render-time grouping by mechanism.

A loader that cannot resolve six imported tool symbols emits six warnings.
They are one fact — "static analysis cannot resolve this agent's tool
symbols" — restated once per symbol, and a reader who fixes the mechanism
clears all six at once. This module holds both halves of that:

* the canonical message builders producers call, so the wording of a
  mechanism lives in one place; and
* :func:`group_source_warnings`, which folds warnings that differ only in
  their quoted subjects back into one line naming the mechanism and listing
  the subjects.

Grouping is **render time only**. ``report.source_warnings`` and
``evidence_coverage.source_warning_count`` stay exactly as the loaders
produced them, because that count is a gating input
(``evidence_below_ie_threshold``) and folding it would silently recalibrate
the ``insufficient_evidence`` threshold. Rendered surfaces show mechanisms;
the JSON keeps warnings.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

# A "slot" is a quoted literal — the part of a warning that names the
# subject the loader tripped over. Everything outside the quotes is the
# mechanism, so two warnings with identical text outside their quotes are
# the same fact about different subjects. A template is the tuple of literal
# segments between the slots; using the tuple itself as the group key keeps
# an arbitrary warning body from colliding with a sentinel character.
_SLOT_PATTERN = re.compile(r"'[^']*'|\"[^\"]*\"")

_Template = tuple[str, ...]


def adk_unresolved_tool_warning(agent_name: str, symbol: str) -> str:
    """Warning for an ADK tools[] entry that static analysis cannot resolve."""

    return f"Google ADK agent {agent_name!r} references unresolved tool {symbol!r}."


def invalid_tool_binding_warning(binding_id: str, reasons: Sequence[str]) -> str:
    """Warning for a ``tool_identity.bindings`` entry that applies nowhere."""

    return f"Invalid tool binding {binding_id!r}: " + "; ".join(reasons)


def unmatched_binding_member(source_id: str, tool: str, match_count: int) -> str:
    """Reason fragment for a member selector that did not resolve to one tool."""

    return (
        f"member source_id={source_id!r}, tool={tool!r} "
        f"matched {match_count} observations"
    )


def zero_observation_binding_member(source_id: str, tool: str) -> str:
    """Reason fragment for a binding member on a source that produced nothing.

    ``tool_identity.bindings`` joins observations that sources actually
    produced; it cannot conjure one for a source that produced none. Naming
    only the arithmetic ("matched 0 observations") sends the reader back to
    declare more bindings over the same empty source. State the rule, and
    name the surface that does declare unproven wiring.
    """

    return (
        f"{unmatched_binding_member(source_id, tool, 0)} because source "
        f"{source_id!r} produced no tool observations at all — a source with "
        "no observations cannot be a tool_identity.bindings member; declare "
        "the agent's reviewed wiring at "
        "shipgate.yaml#agent_bindings.declarations instead"
    )


@dataclass(frozen=True)
class SourceWarningGroup:
    """One mechanism, plus every raw warning that restated it."""

    message: str
    warnings: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.warnings)


def group_source_warnings(warnings: Sequence[str]) -> list[SourceWarningGroup]:
    """Fold warnings that differ only in their quoted subjects.

    Order is stable: groups appear in first-warning order, and the subjects
    listed inside a group appear in the order the loader reported them.
    """

    order: list[_Template] = []
    members: dict[_Template, list[tuple[str, tuple[str, ...]]]] = {}
    for warning in warnings:
        template, slots = _split(warning)
        if template not in members:
            order.append(template)
            members[template] = []
        members[template].append((warning, slots))

    groups: list[SourceWarningGroup] = []
    for template in order:
        rows = members[template]
        raw = tuple(warning for warning, _ in rows)
        if len(rows) == 1:
            slots = rows[0][1]
        else:
            slots = tuple(
                _join_slot(_unique([row[1][index] for row in rows]))
                for index in range(len(rows[0][1]))
            )
        renderer = _MECHANISM_RENDERERS.get(template, _render_generic)
        groups.append(
            SourceWarningGroup(
                message=renderer(template, slots, len(rows)),
                warnings=raw,
            )
        )
    return groups


def _split(warning: str) -> tuple[_Template, tuple[str, ...]]:
    """Separate a warning into its literal segments and its quoted slots."""

    parts: list[str] = []
    slots: list[str] = []
    index = 0
    for match in _SLOT_PATTERN.finditer(warning):
        parts.append(warning[index : match.start()])
        slots.append(match.group(0))
        index = match.end()
    parts.append(warning[index:])
    return tuple(parts), tuple(slots)


def _fill(template: _Template, slots: Sequence[str]) -> str:
    out = template[0]
    for value, tail in zip(slots, template[1:], strict=True):
        out = f"{out}{value}{tail}"
    return out


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _join_slot(values: Sequence[str]) -> str:
    return ", ".join(values)


def _render_generic(template: _Template, slots: Sequence[str], count: int) -> str:
    """Default: the mechanism's own sentence, listing the varying subjects."""

    return _fill(template, slots)


def _render_adk_unresolved_tools(
    template: _Template, slots: Sequence[str], count: int
) -> str:
    """Name the cause and the two levers that close it, once for all symbols.

    The per-symbol form says what failed but not what to do, and an agent
    that reads six of them has read one instruction six times.
    """

    agent, symbols = slots[0], slots[1]
    noun = "tool symbol" if count == 1 else "tool symbols"
    return (
        f"Google ADK agent {agent} references {count} {noun} that static "
        f"analysis could not resolve in this entrypoint (imported or "
        f"dynamically constructed): {symbols}. Declare the agent's reviewed "
        "wiring at shipgate.yaml#agent_bindings.declarations, or add a "
        "reviewed tool inventory under google_adk.tool_inventories, then "
        "rerun the scan."
    )


def _render_zero_observation_bindings(
    template: _Template, slots: Sequence[str], count: int
) -> str:
    """Say the rule once, rather than the arithmetic once per binding."""

    bindings, source, tools = slots[0], slots[1], slots[2]
    noun = "entry" if count == 1 else "entries"
    verb = "names" if count == 1 else "name"
    return (
        f"{count} tool_identity.bindings {noun} ({bindings}) {verb} source "
        f"{source}, which produced no tool observations at all, so no binding "
        "over it can ever resolve; a source with no observations cannot be a "
        "tool_identity.bindings member. Declare the agent's reviewed wiring at "
        "shipgate.yaml#agent_bindings.declarations instead. Members named: "
        f"{tools}."
    )


def _template_of(message: str) -> _Template:
    return _split(message)[0]


# Mechanisms whose fix is known well enough to state. Keys are derived from
# the builders above, so a producer and its renderer cannot drift: change the
# message text and the key changes with it.
_MECHANISM_RENDERERS: dict[
    _Template, Callable[[_Template, Sequence[str], int], str]
] = {
    _template_of(adk_unresolved_tool_warning("<agent>", "<symbol>")): (
        _render_adk_unresolved_tools
    ),
    _template_of(
        invalid_tool_binding_warning(
            "<binding>", [zero_observation_binding_member("<source>", "<tool>")]
        )
    ): _render_zero_observation_bindings,
}


__all__ = [
    "SourceWarningGroup",
    "adk_unresolved_tool_warning",
    "group_source_warnings",
    "invalid_tool_binding_warning",
    "unmatched_binding_member",
    "zero_observation_binding_member",
]
