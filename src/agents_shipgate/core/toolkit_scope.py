"""Carriage codec for dynamically-loaded agent-toolkit scope bounds.

A :class:`~agents_shipgate.core.domain.ToolkitScopeBound` is the
statically-parsed least-privilege allowlist of a runtime-loaded toolkit
(see the domain model for the motivation). The verifier needs to diff
that bound base-vs-head, but the base side is only available through the
serialized base ``report.json`` — so the bound is carried as a
``ToolSurfacePolicyFact`` inside ``tool_surface_facts.policies`` (a free
``kind`` string; no ``report_schema_version`` bump).

This module owns the *single* encode/decode contract used by both the
report builder (``core/lenses/tool_surface.py``) and the diff-aware check
(``checks/verify_capability_scope.py``) so the two never drift. It
deliberately imports nothing from ``tool_surface`` to avoid an import
cycle and recomputes its own stable hash.
"""

from __future__ import annotations

import hashlib
import json

from agents_shipgate.core.domain import ToolkitScopeBound
from agents_shipgate.schemas.surfaces import ToolSurfacePolicyFact

# ``ToolSurfacePolicyFact.kind`` is a free-form string, so a new kind is
# purely additive — it carries the toolkit bound without a schema bump.
TOOLKIT_BOUND_POLICY_KIND = "toolkit_scope_bound"

# ``summary`` doubles as the human-readable rendering AND the carriage of
# the scope set (``value_hash`` is only a change-detection digest). The
# unbounded sentinel is distinct from an empty-but-bounded allowlist
# (``configuration={"actions": {}}`` — bounded to nothing), so the two
# decode to different ``bounded`` states.
UNBOUNDED_SUMMARY = "(unbounded — full toolkit surface)"
# Config-bound / unreadable-binding sentinels (docs/engineering/
# config-bound-capability-detection.md). Same carriage trick: the summary
# is both the rendering and the round-trip encoding, so the base side of a
# verify diff recovers ``config_binding`` (and the bound config file's
# path) from the serialized base report without any schema change.
CONFIG_BOUND_SUMMARY_BARE = "(config-bound)"
_CONFIG_BOUND_SUMMARY_PREFIX = "(config-bound: "
UNKNOWN_BINDING_SUMMARY = "(binding unknown — configuration not statically readable)"
_SCOPE_SEP = ", "


def policy_key_for(bound: ToolkitScopeBound) -> str:
    """The ``ToolSurfacePolicyFact.key`` used to match a bound base-vs-head.

    Keyed on ``provider:binding`` so multiple toolkit instances of the same
    provider (e.g. a customer toolkit and an invoice toolkit) stay distinct
    rather than collapsing to one (last-wins). ``binding`` is the Python
    variable the toolkit was assigned to; an inline/unbound toolkit uses an
    empty binding. A renamed single toolkit is re-paired by the check's
    leftover-singleton fallback, so keying on the binding does not let a
    rename-plus-broaden slip through.
    """
    return f"{bound.provider}:{bound.binding or ''}"


def _bound_summary(bound: ToolkitScopeBound) -> str:
    if bound.config_binding == "config":
        if bound.config_path:
            return f"{_CONFIG_BOUND_SUMMARY_PREFIX}{bound.config_path})"
        return CONFIG_BOUND_SUMMARY_BARE
    if bound.config_binding == "unknown":
        return UNKNOWN_BINDING_SUMMARY
    if not bound.bounded:
        return UNBOUNDED_SUMMARY
    return _SCOPE_SEP.join(sorted(bound.scopes))


def _bound_value_hash(bound: ToolkitScopeBound) -> str:
    value: dict[str, object] = {
        "bounded": bound.bounded,
        "scopes": sorted(bound.scopes),
    }
    if bound.config_binding in {"config", "unknown"}:
        # Only the new binding states extend the hash payload: literal and
        # absent bounds keep the legacy payload so their serialized
        # ``value_hash`` stays byte-identical across the upgrade.
        value["config_binding"] = bound.config_binding
        value["config_path"] = bound.config_path
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def bound_to_policy_fact(bound: ToolkitScopeBound) -> ToolSurfacePolicyFact:
    """Encode a toolkit bound as a carried ``ToolSurfacePolicyFact``."""
    return ToolSurfacePolicyFact(
        kind=TOOLKIT_BOUND_POLICY_KIND,
        key=policy_key_for(bound),
        value_hash=_bound_value_hash(bound),
        summary=_bound_summary(bound),
    )


def bound_from_policy_fact(fact: ToolSurfacePolicyFact) -> ToolkitScopeBound | None:
    """Decode a carried policy fact back into a ``ToolkitScopeBound``.

    Returns ``None`` for facts that are not toolkit bounds. The decoded bound
    recovers ``provider`` / ``binding`` / ``bounded`` / ``scopes`` (the fields
    needed to diff and to key per-instance); constructor/source provenance is
    not carried, so it falls back to neutral defaults.
    """
    if fact.kind != TOOLKIT_BOUND_POLICY_KIND:
        return None
    provider, _, binding = fact.key.partition(":")
    binding_value = binding or None
    summary = fact.summary or ""
    if summary == UNBOUNDED_SUMMARY:
        return ToolkitScopeBound(
            provider=provider,
            constructor="",
            bounded=False,
            scopes=[],
            binding=binding_value,
            config_binding="absent",
        )
    if summary == UNKNOWN_BINDING_SUMMARY:
        return ToolkitScopeBound(
            provider=provider,
            constructor="",
            bounded=False,
            scopes=[],
            binding=binding_value,
            config_binding="unknown",
        )
    if summary == CONFIG_BOUND_SUMMARY_BARE or summary.startswith(
        _CONFIG_BOUND_SUMMARY_PREFIX
    ):
        config_path = None
        if summary.startswith(_CONFIG_BOUND_SUMMARY_PREFIX):
            config_path = summary[len(_CONFIG_BOUND_SUMMARY_PREFIX) : -1] or None
        return ToolkitScopeBound(
            provider=provider,
            constructor="",
            bounded=False,
            scopes=[],
            binding=binding_value,
            config_binding="config",
            config_path=config_path,
        )
    scopes = [token for token in summary.split(_SCOPE_SEP) if token] if summary else []
    return ToolkitScopeBound(
        provider=provider,
        constructor="",
        bounded=True,
        scopes=sorted(scopes),
        binding=binding_value,
        config_binding="literal",
    )


def toolkit_bound_facts(
    bounds: list[ToolkitScopeBound] | tuple[ToolkitScopeBound, ...],
) -> list[ToolSurfacePolicyFact]:
    """Encode every bound, keeping one fact per ``provider:binding`` instance.

    Deterministic: deduped by policy key (so distinct toolkit instances stay
    separate) and returned sorted by key so the serialized
    ``tool_surface_facts.policies`` stay byte-stable.
    """
    by_key: dict[str, ToolSurfacePolicyFact] = {}
    for bound in bounds:
        fact = bound_to_policy_fact(bound)
        by_key[fact.key] = fact
    return [by_key[key] for key in sorted(by_key)]


__all__ = [
    "CONFIG_BOUND_SUMMARY_BARE",
    "TOOLKIT_BOUND_POLICY_KIND",
    "UNBOUNDED_SUMMARY",
    "UNKNOWN_BINDING_SUMMARY",
    "bound_from_policy_fact",
    "bound_to_policy_fact",
    "policy_key_for",
    "toolkit_bound_facts",
]
