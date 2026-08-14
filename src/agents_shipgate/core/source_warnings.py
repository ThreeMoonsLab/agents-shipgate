"""Canonical source-warning text, and render-time grouping by mechanism.

A loader that cannot resolve six imported tool symbols emits six warnings.
They are one fact — "static analysis cannot resolve this agent's tool
symbols" — restated once per symbol, and a reader who fixes the mechanism
clears all six at once. This module holds both halves of that:

* the canonical message builders producers call, so the wording of a
  mechanism lives in one place; and
* :func:`group_source_warnings`, which folds the rows of a *known* mechanism
  into one line naming the cause, the fix, and the affected subjects.

Grouping is **render time only**. ``report.source_warnings`` and
``evidence_coverage.source_warning_count`` stay exactly as the loaders
produced them, because that count is a gating input
(``evidence_below_ie_threshold``) and folding it would silently recalibrate
the ``insufficient_evidence`` threshold. Rendered surfaces show mechanisms;
the JSON keeps warnings.

Three rules keep grouping from inventing facts:

* **Only registered mechanisms group.** Warning text is loader output, not a
  format we control. A message that does not decode exactly as a registered
  mechanism wrote it is its own group, rendered verbatim.
* **Decoding is exact, not delimiter-guessing.** Every variable a mechanism
  interpolates is ``repr()`` of a string, so the decoder reads a *string
  literal* at each field position rather than splitting on the surrounding
  prose. A value containing the literal separator (`` references unresolved
  tool ``, ``, tool=``) is read whole instead of being cut in half, and a
  composite message — two invalid binding members joined by ``"; "`` — fails
  to decode rather than silently reporting only the first member.
  Regex-with-rebuild cannot do this: the rebuild is byte-identical for *any*
  successful match, so it validates nothing.
* **Rows keep their tuples.** A mechanism declares which of its fields are
  *context* (part of the group key — two ADK agents never merge) and which
  are *subjects* (listed, as whole tuples, so a binding id stays attached to
  its tool). Counts quoted in the prose come from distinct subjects;
  ``SourceWarningGroup.count`` stays the raw row count, which is what the
  "(N warnings)" suffix and any gating-adjacent display need.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from agents_shipgate.core.evidence_actions import one_line

_Fields = tuple[str, ...]
_GroupKey = tuple[str, _Fields]


# --- canonical message text --------------------------------------------------


def adk_unresolved_tool_warning(agent_name: str, symbol: str) -> str:
    """Warning for an ADK tools[] entry that static analysis cannot resolve."""

    return _ADK_UNRESOLVED_TOOL.write(agent_name, symbol)


# Every literal segment of the binding-warning prose exists once, so the
# builders below and the mechanism specs at the bottom of the file are the
# same words by construction rather than by review.
_BINDING_HEAD = "Invalid tool binding "
_BINDING_SOURCE = ": member source_id="
_BINDING_TOOL = ", tool="
_BINDING_ZERO_KNOWN = " matched 0 observations because configured source "
_BINDING_ZERO_KNOWN_TAIL = (
    " produced no tool observations at all — a source with no observations "
    "cannot be a tool_identity.bindings member; declare the agent's reviewed "
    "wiring at shipgate.yaml#agent_bindings.declarations instead"
)
_BINDING_ZERO_UNKNOWN = " matched 0 observations because no tool source with id "
_BINDING_ZERO_UNKNOWN_TAIL = (
    " is configured — correct the member to name a configured "
    "shipgate.yaml#tool_sources[].id"
)


def invalid_tool_binding_warning(binding_id: str, reasons: Sequence[str]) -> str:
    """Warning for a ``tool_identity.bindings`` entry that applies nowhere."""

    return f"{_BINDING_HEAD}{binding_id!r}: " + "; ".join(reasons)


def unmatched_binding_member(source_id: str, tool: str, match_count: int) -> str:
    """Reason fragment for a member selector that did not resolve to one tool."""

    return (
        f"member source_id={source_id!r}, tool={tool!r} "
        f"matched {match_count} observations"
    )


def zero_observation_binding_member(source_id: str, tool: str) -> str:
    """Reason fragment for a binding member on a *configured* but empty source.

    ``tool_identity.bindings`` joins observations that sources actually
    produced; it cannot conjure one for a source that produced none. Naming
    only the arithmetic ("matched 0 observations") sends the reader back to
    declare more bindings over the same empty source. State the rule, and
    name the surface that does declare unproven wiring.

    Reserved for a source the manifest configured and the loader read: that
    is the case where ``agent_bindings`` is the right lever. A member naming
    a source that does not exist gets
    :func:`unknown_binding_member_source` instead, because no binding
    declaration can repair a selector that points at nothing.
    """

    return (
        f"member source_id={source_id!r}, tool={tool!r}"
        f"{_BINDING_ZERO_KNOWN}{source_id!r}{_BINDING_ZERO_KNOWN_TAIL}"
    )


def unknown_binding_member_source(source_id: str, tool: str) -> str:
    """Reason fragment for a member naming a source that is not configured.

    A typo in ``source_id`` and a configured-but-empty source both "match 0
    observations", and they need opposite repairs: this one is fixed by
    correcting the selector, and no ``agent_bindings`` declaration can help.
    """

    return (
        f"member source_id={source_id!r}, tool={tool!r}"
        f"{_BINDING_ZERO_UNKNOWN}{source_id!r}{_BINDING_ZERO_UNKNOWN_TAIL}"
    )


# --- grouping ----------------------------------------------------------------


@dataclass(frozen=True)
class SourceWarningGroup:
    """One mechanism, plus every raw warning that restated it.

    ``message`` is the *display* projection: normalized to one line, safe to
    interpolate into ``report.md``, the packet renderers, the CLI, and
    ``fix_task.instructions[]``. ``warnings`` keeps the loader's bytes, so
    nothing that gates or counts moves.
    """

    message: str
    warnings: tuple[str, ...]

    @property
    def count(self) -> int:
        """Raw warnings folded in — never a count of distinct subjects."""

        return len(self.warnings)


def group_source_warnings(warnings: Sequence[str]) -> list[SourceWarningGroup]:
    """Fold the rows of a known mechanism; leave everything else alone.

    Order is stable: groups appear in first-warning order, and subjects
    inside a group appear in the order the loader reported them.
    """

    order: list[_GroupKey] = []
    rows: dict[_GroupKey, list[tuple[str, _Fields]]] = {}
    mechanisms: dict[_GroupKey, _Mechanism | None] = {}
    for index, warning in enumerate(warnings):
        match = _match(warning)
        if match is None:
            # Unrecognized text never merges: the index keeps each row in its
            # own group while preserving input order.
            key = (f"\x00raw:{index}", ())
            mechanism = None
            subject: _Fields = ()
        else:
            mechanism, fields = match
            key = (mechanism.name, tuple(fields[name] for name in mechanism.context))
            subject = tuple(fields[name] for name in mechanism.subjects)
        if key not in rows:
            order.append(key)
            rows[key] = []
            mechanisms[key] = mechanism
        rows[key].append((warning, subject))

    groups: list[SourceWarningGroup] = []
    for key in order:
        members = rows[key]
        raw = tuple(warning for warning, _ in members)
        mechanism = mechanisms[key]
        if mechanism is None:
            # An unrecognized warning is loader text nobody validated. It is
            # still going into report.md, packet.md/html, the CLI, and
            # fix_task.instructions[] — none of which collapse newlines — so
            # the *display* copy is normalized even though nothing about it
            # was understood. `warnings` keeps the raw bytes.
            groups.append(SourceWarningGroup(message=one_line(raw[0]), warnings=raw))
            continue
        subjects = _unique(subject for _, subject in members)
        groups.append(
            SourceWarningGroup(
                # Mechanism renders interpolate `repr()` of decoded values, so
                # a control character is already escaped rather than literal.
                # Normalizing anyway keeps one rule for every group.
                message=one_line(mechanism.render(key[1], subjects)),
                warnings=raw,
            )
        )
    return groups


@dataclass(frozen=True)
class _Mechanism:
    """One warning shape: how it is written, and how its rows recombine.

    ``parts`` are the literal segments around the fields, so ``build`` and
    ``parse`` are generated from a single spec and cannot drift. ``repeats``
    names field pairs the producer always writes equal (a binding message
    prints its ``source_id`` twice); a message where they differ was not
    written by this mechanism.
    """

    name: str
    parts: tuple[str, ...]
    fields: _Fields
    context: _Fields
    subjects: _Fields
    render: Callable[[_Fields, list[_Fields]], str]
    repeats: tuple[tuple[str, str], ...] = field(default=())

    def build(self, *values: str) -> str:
        """Assemble a message from already-``repr``-rendered field values."""

        out = self.parts[0]
        for value, tail in zip(values, self.parts[1:], strict=True):
            out = f"{out}{value}{tail}"
        return out

    def write(self, *values: str) -> str:
        """Write a message from raw field values."""

        return self.build(*(repr(value) for value in values))

    def parse(self, message: str) -> dict[str, str] | None:
        """Decode a message this mechanism wrote, or return ``None``.

        Walks literal part / string literal / literal part …, so each field is
        delimited by its own quoting rather than by the prose around it. The
        message must be consumed exactly: a trailing ``"; member …"`` from a
        second invalid binding member leaves input unread and is rejected,
        which is what keeps a composite row verbatim instead of silently
        reporting only its first member.
        """

        values: list[str] = []
        index = 0
        for part in self.parts[:-1]:
            if not message.startswith(part, index):
                return None
            index += len(part)
            read = _read_string_literal(message, index)
            if read is None:
                return None
            value, index = read
            values.append(value)
        tail = self.parts[-1]
        if not message.startswith(tail, index) or index + len(tail) != len(message):
            return None
        decoded = dict(zip(self.fields, values, strict=True))
        return (
            None
            if any(decoded[left] != decoded[right] for left, right in self.repeats)
            else decoded
        )


def _read_string_literal(text: str, index: int) -> tuple[str, int] | None:
    """Read one canonical ``repr()`` string literal at ``index``.

    Canonical is the point: the slice must be exactly what ``repr`` of the
    decoded value produces, so a hand-written or differently-quoted literal
    is not mistaken for producer output.
    """

    if index >= len(text) or text[index] not in "\"'":
        return None
    quote = text[index]
    cursor = index + 1
    while cursor < len(text):
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == quote:
            raw = text[index : cursor + 1]
            try:
                value = ast.literal_eval(raw)
            except (SyntaxError, ValueError):
                return None
            if not isinstance(value, str) or repr(value) != raw:
                return None
            return value, cursor + 1
        cursor += 1
    return None


def _match(warning: str) -> tuple[_Mechanism, dict[str, str]] | None:
    for mechanism in _MECHANISMS:
        fields = mechanism.parse(warning)
        if fields is not None:
            return mechanism, fields
    return None


def _unique(values: object) -> list[_Fields]:
    return list(dict.fromkeys(values))  # type: ignore[arg-type]


def _join(values: Sequence[str]) -> str:
    return ", ".join(values)


def _render_adk_unresolved_tools(context: _Fields, subjects: list[_Fields]) -> str:
    """Name the cause and the two levers that close it, once per agent.

    The agent is part of the group key, so two agents that both fail on the
    same symbol stay two lines — merging them would report one symbol as two.
    Values arrive decoded and are re-``repr``'d here, so the rendered text
    quotes them exactly as the producer did.
    """

    (agent,) = context
    symbols = [repr(symbol) for (symbol,) in subjects]
    noun = "tool symbol" if len(symbols) == 1 else "tool symbols"
    return (
        f"Google ADK agent {agent!r} references {len(symbols)} {noun} that "
        "static analysis could not resolve in this entrypoint (imported or "
        f"dynamically constructed): {_join(symbols)}. Declare the agent's "
        "reviewed wiring at shipgate.yaml#agent_bindings.declarations, or add "
        "a reviewed tool inventory under google_adk.tool_inventories, then "
        "rerun the scan."
    )


def _render_zero_observation_bindings(
    context: _Fields, subjects: list[_Fields]
) -> str:
    """Say the rule once, rather than the arithmetic once per binding."""

    (source,) = context
    members = _join([f"{binding!r} → {tool!r}" for binding, tool in subjects])
    noun = "entry" if len(subjects) == 1 else "entries"
    verb = "names" if len(subjects) == 1 else "name"
    return (
        f"{len(subjects)} tool_identity.bindings {noun} {verb} configured "
        f"source {source!r}, which produced no tool observations at all, so no "
        "binding over it can ever resolve; a source with no observations "
        "cannot be a tool_identity.bindings member. Declare the agent's "
        "reviewed wiring at shipgate.yaml#agent_bindings.declarations "
        f"instead. Members: {members}."
    )


def _render_unknown_binding_sources(context: _Fields, subjects: list[_Fields]) -> str:
    """A selector pointing at nothing is fixed by correcting the selector."""

    (source,) = context
    members = _join([f"{binding!r} → {tool!r}" for binding, tool in subjects])
    noun = "entry" if len(subjects) == 1 else "entries"
    verb = "names" if len(subjects) == 1 else "name"
    return (
        f"{len(subjects)} tool_identity.bindings {noun} {verb} source "
        f"{source!r}, for which no tool source is configured. Correct the "
        "member to name a configured shipgate.yaml#tool_sources[].id — no "
        "agent_bindings declaration can repair a selector that points at "
        f"nothing. Members: {members}."
    )


_ADK_UNRESOLVED_TOOL = _Mechanism(
    name="adk_unresolved_tool",
    parts=("Google ADK agent ", " references unresolved tool ", "."),
    fields=("agent", "symbol"),
    context=("agent",),
    subjects=("symbol",),
    render=_render_adk_unresolved_tools,
)

_ZERO_OBSERVATION_BINDING = _Mechanism(
    name="zero_observation_binding_member",
    parts=(
        _BINDING_HEAD,
        _BINDING_SOURCE,
        _BINDING_TOOL,
        _BINDING_ZERO_KNOWN,
        _BINDING_ZERO_KNOWN_TAIL,
    ),
    fields=("binding", "source", "tool", "source_again"),
    context=("source",),
    subjects=("binding", "tool"),
    render=_render_zero_observation_bindings,
    repeats=(("source", "source_again"),),
)

_UNKNOWN_BINDING_SOURCE = _Mechanism(
    name="unknown_binding_member_source",
    parts=(
        _BINDING_HEAD,
        _BINDING_SOURCE,
        _BINDING_TOOL,
        _BINDING_ZERO_UNKNOWN,
        _BINDING_ZERO_UNKNOWN_TAIL,
    ),
    fields=("binding", "source", "tool", "source_again"),
    context=("source",),
    subjects=("binding", "tool"),
    render=_render_unknown_binding_sources,
    repeats=(("source", "source_again"),),
)

# Ordered: the first mechanism whose builder round-trips wins. The two
# binding mechanisms share a prefix, so their distinct tails are what select
# between them.
_MECHANISMS: tuple[_Mechanism, ...] = (
    _ADK_UNRESOLVED_TOOL,
    _ZERO_OBSERVATION_BINDING,
    _UNKNOWN_BINDING_SOURCE,
)


__all__ = [
    "SourceWarningGroup",
    "adk_unresolved_tool_warning",
    "group_source_warnings",
    "invalid_tool_binding_warning",
    "unknown_binding_member_source",
    "unmatched_binding_member",
    "zero_observation_binding_member",
]
