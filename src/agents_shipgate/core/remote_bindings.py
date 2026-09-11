"""Carriage codec for agent -> remote tool-surface bindings.

An :class:`~agents_shipgate.core.domain.AgentRemoteBinding` is what a static
reader established about the connection an agent mounts: the endpoint, the
credential *reference*, the tool filter, and the transport. Those four are the
agent's authority even when nothing behind them was enumerated — *this agent
will call whatever ``<endpoint>`` advertises, under ``<reference>``* — so the
verifier has to diff them base-vs-head.

The base side is only available through the serialized base ``report.json``, so
each binding rides as ``ToolSurfacePolicyFact`` rows inside
``tool_surface_facts.policies``. That is exactly the carriage
``core.toolkit_scope`` already uses for dynamically-loaded toolkit bounds, for
the same reason: ``ToolSurfacePolicyFact.kind`` is a free string, so a new kind
is additive and needs no ``report_schema_version`` bump.

This module owns the *single* encode/decode contract used by the report builder
(``core/lenses/tool_surface.py``) and the capability-delta projection
(``core/findings/verifier_blocks.py``) so the two cannot drift. It imports
nothing from ``tool_surface`` to avoid an import cycle and computes its own
stable hash.

Four rows, not one
------------------
Each binding emits one row per axis. The axes then diff independently through
the ordinary ``_diff_policies`` set comparison, which is what makes an
endpoint-only, credential-reference-only or filter-only change land as its own
named delta. Leaf inventory is deliberately **not** an axis: supplying one
completes a different claim, and structurally separate rows are the only way
"an inventory cannot conceal an independent connection change" holds by
construction rather than by convention (#538).

What is hashed
--------------
``value_hash`` is computed over the *published* (already redacted) value, never
over withheld bytes. Credential material written literally into source — URL
userinfo, a sensitive query value, a hardcoded header — is redacted before it
reaches this module, so a change confined to the secret itself is not named.
Publishing a digest of a low-entropy secret is a worse trade than that gap, and
the gap is reported: the axis still flips status when literal credential
material appears or disappears, and the binding carries an
``endpoint_credentials_redacted`` limitation while it is there.
"""

from __future__ import annotations

import hashlib
import json
import re

from agents_shipgate.core.domain import AgentRemoteBinding, RemoteBindingStatus
from agents_shipgate.schemas.surfaces import ToolSurfacePolicyFact

#: Namespace prefix shared by every carried remote-binding row. The capability
#: projection selects on it, so nothing else may use it.
REMOTE_BINDING_KIND_PREFIX = "remote_binding."

#: ``ToolSurfacePolicyFact.kind`` per axis. The suffix after the prefix is the
#: axis name and is used verbatim in reviewer-facing labels.
ENDPOINT_KIND = f"{REMOTE_BINDING_KIND_PREFIX}endpoint"
CREDENTIAL_REFS_KIND = f"{REMOTE_BINDING_KIND_PREFIX}credential_refs"
TOOL_FILTER_KIND = f"{REMOTE_BINDING_KIND_PREFIX}tool_filter"
TRANSPORT_KIND = f"{REMOTE_BINDING_KIND_PREFIX}transport"

#: The endpoint row is the binding's presence anchor: it is emitted for every
#: binding whatever the transport, so "this agent gained/lost a remote binding"
#: is one member rather than one per axis.
ANCHOR_KIND = ENDPOINT_KIND

REMOTE_BINDING_KINDS: tuple[str, ...] = (
    ENDPOINT_KIND,
    CREDENTIAL_REFS_KIND,
    TOOL_FILTER_KIND,
    TRANSPORT_KIND,
)

#: Reviewer-facing axis label per kind. Used to build the capability member's
#: subject, so it names something a person can look for in the source rather
#: than repeating the wire kind.
AXIS_LABELS: dict[str, str] = {
    ENDPOINT_KIND: "endpoint",
    CREDENTIAL_REFS_KIND: "credential reference",
    TOOL_FILTER_KIND: "tool filter",
    TRANSPORT_KIND: "transport",
}

#: Status sentinels. A rendered value can never be mistaken for one in
#: practice, and a rendered *list* value can never be one at all: ``(`` is
#: escaped on the way in, so a filter value spelled ``(absent)`` renders as
#: ``%28absent)`` and stays distinguishable from the sentinel that says the
#: argument was not there.
_UNRESOLVED = "(unresolved)"
_ABSENT = "(absent)"
_NOT_READ = "(not read)"
_REDACTED = "(literal credential — withheld)"
_ENV_PREFIX = "(environment reference: "
#: A list argument that *is* present and lists nothing. Kept distinct from
#: ``_ABSENT`` so the change from one spelling to the other is still *named* —
#: they are different source text. What it does **not** decide is direction:
#: whether an empty list bounds anything is the consuming framework's
#: semantics, and the projection settles it. ADK's ``_is_tool_selected``
#: returns ``True`` for any falsy ``tool_filter``, so an empty list is no
#: filter at all (PR #540 review).
_EMPTY = "(empty list)"

#: How a decoded list-valued summary reads back. ``values`` is a real value
#: list; ``absent`` is the argument not being there, which for a tool filter is
#: the *widest* state rather than an empty set and must never be compared as
#: one; ``opaque`` is every status that establishes nothing comparable.
LIST_VALUES = "values"
LIST_ABSENT = "absent"
LIST_OPAQUE = "opaque"

_SEP = ", "
_ESCAPES = {"%": "%25", ",": "%2C", "(": "%28"}
_KEY_ESCAPES = {"%": "%25", ":": "%3A"}
_UNESCAPE_RE = re.compile(r"%(25|28|2C|3A)")
_UNESCAPE_VALUES = {"25": "%", "28": "(", "2C": ",", "3A": ":"}


def _escape(value: str, table: dict[str, str]) -> str:
    # ``%`` first: escaping it afterwards would double-escape the markers the
    # other entries just introduced.
    out = value.replace("%", table["%"])
    for char, replacement in table.items():
        if char == "%":
            continue
        out = out.replace(char, replacement)
    return out


def _unescape(value: str) -> str:
    # One pass, so ``%252C`` decodes to the literal ``%2C`` rather than to a
    # comma. A sequential replace cannot do that.
    return _UNESCAPE_RE.sub(lambda m: _UNESCAPE_VALUES[m.group(1)], value)


def binding_identity(binding: AgentRemoteBinding) -> tuple[str, str, str]:
    """The tuple that decides whether two observations are one binding."""
    return binding.source_id, binding.agent, binding.slot


def policy_key_for(binding: AgentRemoteBinding) -> str:
    """The ``ToolSurfacePolicyFact.key`` that identifies one binding.

    ``agent:source_id:slot``, every component escaped so the ``:`` separator
    stays unambiguous. Three components, each for a reason:

    * the **agent**, so two agents mounting the same endpoint stay two bindings
      rather than collapsing into one;
    * the **source id**, because two configured sources may each declare an
      agent of the same name, and a two-component key silently dropped one of
      their bindings;
    * the **slot** — the toolset variable, or an ordinal within the agent's
      tool list — so one agent's several bindings stay apart.

    The source *line* is deliberately not a component, so line movement,
    reformatting and comments produce no delta.
    """
    return ":".join(
        _escape(part, _KEY_ESCAPES)
        for part in (binding.agent, binding.source_id, binding.slot)
    )


def decode_policy_key(key: str) -> tuple[str, str, str]:
    """``(agent, source_id, slot)`` from a carried key.

    Best effort on a malformed or older two-component key: missing components
    read as empty rather than raising, because a base report is an input and a
    reader must not crash the head scan over one.
    """
    parts = [_unescape(part) for part in key.split(":")]
    parts += [""] * (3 - len(parts))
    return parts[0], parts[1], parts[2]


def encode_list(values: list[str]) -> str:
    """Render a value list so it survives the round trip through ``summary``.

    ``,`` is escaped, so joining on ``", "`` stays unambiguous even for a value
    that contains the separator. Ordinary identifiers — environment variable
    names, MCP tool names — are unaffected, so the rendered summary stays
    readable.
    """
    return _SEP.join(_escape(value, _ESCAPES) for value in sorted(values))


def decode_list(summary: str | None) -> list[str]:
    """Recover a value list from a carried summary. ``[]`` for a sentinel."""
    kind, values = decode_list_summary(summary)
    return values if kind == LIST_VALUES else []


def decode_list_summary(summary: str | None) -> tuple[str, list[str]]:
    """``(kind, values)`` for one carried list-valued summary.

    The kind matters as much as the values. An *absent* tool filter is not an
    empty one: with no filter every tool the endpoint advertises is reachable,
    so comparing it as ``set()`` would report the widest possible state as the
    narrowest and invert the direction of the change. Everything the reader
    could not establish reads as ``opaque``, which is never compared as a set
    at all.
    """
    if summary is None:
        return LIST_OPAQUE, []
    if summary == _ABSENT:
        return LIST_ABSENT, []
    if summary == _EMPTY or summary == "":
        # Present and listing nothing. Comparable as a set; whether the empty
        # set *bounds* anything is a framework question the caller answers.
        return LIST_VALUES, []
    if summary in {_UNRESOLVED, _NOT_READ, _REDACTED} or summary.startswith(
        _ENV_PREFIX
    ):
        return LIST_OPAQUE, []
    return LIST_VALUES, sorted(
        _unescape(token) for token in summary.split(_SEP) if token
    )


def _status_sentinel(status: RemoteBindingStatus) -> str | None:
    if status == "unresolved":
        return _UNRESOLVED
    if status == "absent":
        return _ABSENT
    if status == "not_read":
        return _NOT_READ
    if status == "redacted":
        return _REDACTED
    return None


def _endpoint_summary(binding: AgentRemoteBinding) -> str:
    sentinel = _status_sentinel(binding.endpoint_status)
    if sentinel is not None:
        return sentinel
    if binding.endpoint_status == "environment_reference":
        return f"{_ENV_PREFIX}{binding.endpoint_env_ref or ''})"
    return binding.endpoint or _ABSENT


def _list_summary(
    status: RemoteBindingStatus,
    values: list[str],
) -> str:
    sentinel = _status_sentinel(status)
    if sentinel is not None:
        return sentinel
    return encode_list(values) or _EMPTY


def _scalar_summary(status: RemoteBindingStatus, value: str | None) -> str:
    sentinel = _status_sentinel(status)
    if sentinel is not None:
        return sentinel
    return value or _ABSENT


def _value_hash(status: RemoteBindingStatus, value: object) -> str:
    payload = json.dumps(
        {"status": status, "value": value},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def binding_policy_facts(
    binding: AgentRemoteBinding,
) -> list[ToolSurfacePolicyFact]:
    """Encode one binding as its four carried policy facts."""
    key = policy_key_for(binding)
    endpoint_value = (
        binding.endpoint_env_ref
        if binding.endpoint_status == "environment_reference"
        else binding.endpoint
    )
    return [
        ToolSurfacePolicyFact(
            kind=ENDPOINT_KIND,
            key=key,
            value_hash=_value_hash(binding.endpoint_status, endpoint_value),
            summary=_endpoint_summary(binding),
        ),
        ToolSurfacePolicyFact(
            kind=CREDENTIAL_REFS_KIND,
            key=key,
            value_hash=_value_hash(
                binding.credential_status, sorted(binding.credential_refs)
            ),
            summary=_list_summary(binding.credential_status, binding.credential_refs),
        ),
        ToolSurfacePolicyFact(
            kind=TOOL_FILTER_KIND,
            key=key,
            value_hash=_value_hash(binding.filter_status, sorted(binding.tool_filter)),
            summary=_list_summary(binding.filter_status, binding.tool_filter),
        ),
        ToolSurfacePolicyFact(
            kind=TRANSPORT_KIND,
            key=key,
            value_hash=_value_hash(binding.transport_status, binding.transport),
            summary=_scalar_summary(binding.transport_status, binding.transport),
        ),
    ]


def remote_binding_facts(
    bindings: list[AgentRemoteBinding] | tuple[AgentRemoteBinding, ...],
) -> list[ToolSurfacePolicyFact]:
    """Encode every binding, one row per ``(kind, key)``.

    Deterministic: deduped on the diff's own ``(kind, key)`` identity so a
    binding observed twice cannot emit two colliding rows, and returned sorted
    so the serialized ``tool_surface_facts.policies`` stay byte-stable.
    """
    by_identity: dict[tuple[str, str], ToolSurfacePolicyFact] = {}
    for binding in bindings:
        for fact in binding_policy_facts(binding):
            by_identity[(fact.kind, fact.key)] = fact
    return [by_identity[identity] for identity in sorted(by_identity)]


def is_remote_binding_kind(kind: str) -> bool:
    """Whether a carried policy kind belongs to this codec."""
    return kind in REMOTE_BINDING_KINDS


def axis_label(kind: str) -> str:
    """Reviewer-facing axis name for a carried policy kind."""
    return AXIS_LABELS.get(kind, kind.removeprefix(REMOTE_BINDING_KIND_PREFIX))


__all__ = [
    "ANCHOR_KIND",
    "LIST_ABSENT",
    "LIST_OPAQUE",
    "LIST_VALUES",
    "AXIS_LABELS",
    "CREDENTIAL_REFS_KIND",
    "ENDPOINT_KIND",
    "REMOTE_BINDING_KIND_PREFIX",
    "REMOTE_BINDING_KINDS",
    "TOOL_FILTER_KIND",
    "TRANSPORT_KIND",
    "axis_label",
    "binding_identity",
    "binding_policy_facts",
    "decode_list",
    "decode_list_summary",
    "decode_policy_key",
    "encode_list",
    "is_remote_binding_kind",
    "policy_key_for",
    "remote_binding_facts",
]
