from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, cast

from agents_shipgate.core.adopter_text import (
    DUPLICATE_ACROSS_ARTIFACTS,
    DUPLICATE_IN_SOURCE_ARTIFACT,
    DUPLICATE_TOOL_IN_SOURCE,
    REPEATED_SOURCE_ENTRY,
    duplicate_tool_observation_message,
    overlapping_binding_message,
)
from agents_shipgate.core.domain import (
    SURFACE_PARTIAL,
    LoadedToolSource,
    SemanticClaim,
    SemanticIssue,
    Tool,
    ToolIdentityAssessment,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.source_warnings import (
    ambiguous_inventory_merge_warning,
    invalid_tool_binding_warning,
    self_referential_inventory_warning,
    unbound_inventory_duplicate_warning,
    unknown_binding_member_source,
    unknown_inventory_source_warning,
    unmatched_binding_member,
    zero_observation_binding_member,
)
from agents_shipgate.schemas.manifest import (
    ToolIdentityBindingConfig,
    ToolIdentityConfig,
    ToolObservationSelectorConfig,
    ToolSourceConfig,
)
from agents_shipgate.schemas.manifest._artifacts import _normalized_declared_path

_MCP_LIKE = {
    "mcp",
    "codex_config_mcp",
    "codex_plugin_mcp_inventory",
    "n8n_mcp_client_tool",
}


@dataclass(frozen=True)
class SelectorResolution:
    matches: tuple[Tool, ...]
    kind: str | None = None
    message: str | None = None

    @property
    def resolved(self) -> bool:
        return len(self.matches) == 1 and self.kind is None


@dataclass(frozen=True)
class IdentityAliases:
    """Every identity a manifest selector may name one canonical tool by.

    Binding rewrites identity twice over. The canonical ``tool_id`` moves from
    an observation-derived hash to a binding-derived one, and the row keeps only
    its *primary* observation's ``source_type``/``source_id``. Both are things
    an already-written manifest row names, and Shipgate scaffolds rows carrying
    both — ``_action_selector`` emits ``tool`` *and* ``tool_id`` *and*
    ``source_id`` — so applying the inventory remediation invalidated the exact
    declaration the tool had just told the user to write (#386 review).

    A canonical tool *is* the union of its observations, so it answers to every
    identity those observations had. ``tool_ids`` therefore carries the id each
    member would have had unbound alongside the current one, and ``sources``
    carries one pair per member.

    Consumers must go through :meth:`matches`; comparing the canonical fields
    directly is what left ``_action_has_policy_control`` and
    ``_matching_suppression`` behind when the aliases were first added.
    """

    tool_ids: frozenset[str]
    sources: frozenset[tuple[str, str]]

    def matches(
        self,
        *,
        tool_id: str | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
    ) -> bool:
        """Does this tool answer to every qualifier the selector supplied?

        ``source_type`` and ``source_id`` given together must be satisfied by
        the *same* observation. Checking them independently would let a selector
        pair one member's type with another member's id and match a tool neither
        observation describes.
        """

        if tool_id and tool_id not in self.tool_ids:
            return False
        if source_type is None and source_id is None:
            return True
        return any(
            (source_type is None or pair[0] == source_type)
            and (source_id is None or pair[1] == source_id)
            for pair in self.sources
        )


def _observation_tool_id(observation_id: str) -> str:
    """The canonical id an observation carries while it is unbound.

    Mirrors ``build_tool_identity_catalog`` exactly; the two must not drift, or
    a pre-binding row would alias to an id the catalog never issued.
    """

    return _stable_id("tool_v2", {"observation_id": observation_id})


def identity_aliases(
    *,
    tool_id: str,
    source_type: str,
    source_id: str | None,
    identity: Any,
) -> IdentityAliases:
    """Build aliases from a canonical row plus its identity evidence.

    ``identity`` is duck-typed over ``ToolIdentityAssessment`` (in-memory) and
    ``ToolIdentityEvidence`` (the wire projection carried on ``ActionFact``), so
    one implementation serves both the tool catalog and the action surface.
    """

    tool_ids = {tool_id}
    sources = {(source_type, source_id or "")}
    if identity is not None:
        for observation_id in getattr(identity, "observation_ids", ()) or ():
            if isinstance(observation_id, str) and observation_id:
                tool_ids.add(_observation_tool_id(observation_id))
        for claim in getattr(identity, "claims", ()) or ():
            evidence = getattr(claim, "evidence", None) or {}
            claim_type = evidence.get("source_type")
            if isinstance(claim_type, str):
                claim_id = evidence.get("source_id")
                sources.add((claim_type, claim_id if isinstance(claim_id, str) else ""))
    return IdentityAliases(frozenset(tool_ids), frozenset(sources))


def tool_identity_aliases(tool: Tool) -> IdentityAliases:
    """Aliases for a canonical :class:`Tool`."""

    return identity_aliases(
        tool_id=tool.id,
        source_type=tool.source_type,
        source_id=tool.source_id,
        identity=tool.identity_assessment,
    )


def action_identity_aliases(action: Any) -> IdentityAliases:
    """Aliases for an ``ActionFact``, read from its semantic evidence."""

    assessment = getattr(action, "semantic_assessment", None)
    return identity_aliases(
        tool_id=action.tool_id,
        source_type=action.source_type,
        source_id=action.source_id,
        identity=getattr(assessment, "identity", None),
    )


@dataclass(frozen=True)
class ToolSelectorIndex:
    """One-pass lookup index for repeated manifest selector resolution."""

    tools: tuple[Tool, ...]
    #: Canonical ids only. ``agent_bindings`` reads ``set(by_id)`` as the whole
    #: catalog to partition reachable/possible/unbound, so an alias in here
    #: invents a catalog member and trips the tool_catalog/binding-graph
    #: consistency invariant. Aliases live in ``by_selectable_id``.
    by_id: dict[str, Tool]
    by_name: dict[str, tuple[Tool, ...]]
    #: Canonical ids plus the ids each bound observation had while unbound.
    #: Resolution only.
    by_selectable_id: dict[str, Tool]
    #: Per canonical tool id, every identity a selector may name it by.
    aliases: dict[str, IdentityAliases]

    @classmethod
    def build(cls, tools: Sequence[Tool]) -> ToolSelectorIndex:
        by_name: dict[str, list[Tool]] = defaultdict(list)
        for tool in tools:
            by_name[tool.name].append(tool)
        aliases = {tool.id: tool_identity_aliases(tool) for tool in tools}
        by_id = {tool.id: tool for tool in tools}
        # Current ids are inserted last so a real id always outranks an alias.
        # A bound observation is consumed, so the id it *would* have had unbound
        # is not issued to anything else — but resolution must not depend on
        # that argument holding for every future binding shape.
        by_selectable_id: dict[str, Tool] = {}
        for tool in tools:
            for alias in sorted(aliases[tool.id].tool_ids):
                by_selectable_id.setdefault(alias, tool)
        by_selectable_id.update(by_id)
        return cls(
            tools=tuple(tools),
            by_id=by_id,
            by_name={
                name: tuple(sorted(matches, key=lambda tool: tool.id))
                for name, matches in by_name.items()
            },
            by_selectable_id=by_selectable_id,
            aliases=aliases,
        )

    def _aliases(self, tool: Tool) -> IdentityAliases:
        return self.aliases.get(tool.id) or tool_identity_aliases(tool)

    def resolve(self, selector: Any) -> SelectorResolution:
        tool_id = _selector_value(selector, "tool_id")
        name = _selector_value(selector, "tool")
        provider = _selector_value(selector, "provider")
        source_type = _selector_value(selector, "source_type")
        source_id = _selector_value(selector, "source_id")

        if tool_id:
            match = self.by_selectable_id.get(tool_id)
            candidates: Sequence[Tool] = (match,) if match is not None else ()
        elif name:
            candidates = self.by_name.get(name, ())
        else:
            return SelectorResolution(
                (),
                "unresolved_tool_selector",
                "selector has no tool or tool_id",
            )
        if provider:
            candidates = tuple(tool for tool in candidates if tool.provider == provider)
        if source_type or source_id:
            candidates = tuple(
                tool
                for tool in candidates
                if self._aliases(tool).matches(
                    source_type=source_type or None, source_id=source_id or None
                )
            )

        if len(candidates) == 1:
            return SelectorResolution((candidates[0],))
        rendered = _render_selector(selector)
        if not candidates:
            return SelectorResolution(
                (),
                "unresolved_tool_selector",
                f"Tool selector {rendered} matched no canonical tool",
            )
        return SelectorResolution(
            tuple(candidates),
            "ambiguous_tool_selector",
            f"Tool selector {rendered} matched {len(candidates)} canonical tools",
        )


def configured_tool_source(
    tool: Tool,
    by_configured_id: Mapping[str, ToolSourceConfig],
) -> ToolSourceConfig | None:
    """The one ``tool_sources`` entry that speaks for this action, or ``None``.

    ``None`` covers three situations that all mean "no source-wide declaration
    applies here", and each is a deliberate refusal rather than a gap:

    * nothing in ``tool_sources`` configures the surface this action came from
      — a per-scan adapter reading a top-level manifest section, say — so there
      is no entry whose declaration could be about it;
    * a reviewed ``tool_identity`` binding merged observations from **several**
      configured sources. Their credentials are not the same fact, and picking
      one would apply a declaration written about one deployment to another;
    * the action carries no configured provenance at all.

    Never falls back to ``tool.source_id``. That id is minted by the adapter
    and shares a namespace with configured ids, so the fallback is exactly the
    join that applied an MCP row's reviewed authority to OpenAI API actions.
    """

    configured = {
        source_id
        for source_id in tool.configured_source_ids
        if source_id in by_configured_id
    }
    if len(configured) != 1:
        return None
    return by_configured_id[configured.pop()]


def build_tool_identity_catalog(
    loaded_sources: list[LoadedToolSource],
    config: ToolIdentityConfig,
    repeated_artifacts: frozenset[str] = frozenset(),
) -> tuple[list[Tool], list[str]]:
    """Build canonical tools without ever joining observations by name.

    Every adapter result first becomes a source-scoped observation. Observations
    are combined only when a reviewed ``tool_identity.bindings[]`` entry names
    every member exactly. Invalid bindings apply nowhere and make the affected
    identity non-pass-eligible.

    A ``<framework>.tool_inventories[].source_id`` entry is *desugared* into
    those same reviewed bindings rather than merged by a second code path
    (#386). The manifest — the trust root — is what asserts the join; the
    inventory file never joins itself to anything by name.
    """

    observations = _observations(loaded_sources, repeated_artifacts)
    synthesized, warnings = _inventory_completion_bindings(
        loaded_sources, observations, config
    )
    bindings = [*config.bindings, *synthesized]
    by_member: dict[tuple[str, str, str], list[Tool]] = defaultdict(list)
    for tool in observations:
        by_member[(tool.source_type, tool.source_id or "", tool.name)].append(tool)
    # A binding member naming a source that produced nothing is a different
    # mistake from one naming a tool the source does not expose: no binding
    # over that source can ever resolve, so the arithmetic ("matched 0
    # observations") is not the answer the reader needs.
    #
    # And "produced nothing" splits again. A source the loader actually read
    # but that yielded no tools is repaired at `agent_bindings`. A
    # source id that is not configured at all — a typo — is repaired by
    # correcting the selector, and no binding declaration can help it. The
    # two sets are what tell them apart; deriving both from observations
    # alone conflated them.
    # The ids adapters *minted*, which is what a binding member selector names.
    # Deliberately not ``configured_source_ids``: since #410 that spelling means
    # the ``tool_sources`` entry a result was produced *for*, and the two sets
    # differ wherever an adapter derives its own ids.
    loaded_source_ids = {loaded.source_id.strip() for loaded in loaded_sources}
    observed_source_ids = {tool.source_id for tool in observations if tool.source_id}

    selected_by_binding: dict[str, list[Tool]] = {}
    binding_issues: dict[str, list[SemanticIssue]] = defaultdict(list)
    observation_bindings: dict[str, list[str]] = defaultdict(list)

    for binding in bindings:
        selected: list[Tool] = []
        invalid_messages: list[str] = []
        for member in binding.members:
            matches = [
                tool
                for tool in observations
                if tool.source_id == member.source_id
                and tool.name == member.tool
                and (member.source_type is None or tool.source_type == member.source_type)
            ]
            if len(matches) != 1:
                if matches or member.source_id in observed_source_ids:
                    invalid_messages.append(
                        unmatched_binding_member(
                            member.source_id, member.tool, len(matches)
                        )
                    )
                elif member.source_id in loaded_source_ids:
                    invalid_messages.append(
                        zero_observation_binding_member(member.source_id, member.tool)
                    )
                else:
                    invalid_messages.append(
                        unknown_binding_member_source(member.source_id, member.tool)
                    )
                selected.extend(matches)
            else:
                selected.append(matches[0])
        if invalid_messages:
            message = invalid_tool_binding_warning(binding.id, invalid_messages)
            warnings.append(message)
            for tool in selected or observations:
                if tool.observation_id:
                    binding_issues[tool.observation_id].append(
                        _identity_issue("invalid_tool_binding", message, binding.id)
                    )
            continue
        selected_by_binding[binding.id] = selected
        for tool in selected:
            assert tool.observation_id is not None
            observation_bindings[tool.observation_id].append(binding.id)

    observations_by_id = {
        tool.observation_id: tool for tool in observations if tool.observation_id
    }
    for observation_id, binding_ids in observation_bindings.items():
        if len(binding_ids) <= 1:
            continue
        # The observation id detected the overlap; it is not what the reader
        # opens. Name the tool and the file it came from (#329) — the id is
        # already on the row this issue is attached to. Indexed directly:
        # every key here came from an observation, and a KeyError is a better
        # answer to that invariant breaking than a message naming ''.
        overlapping = observations_by_id[observation_id]
        message = overlapping_binding_message(
            tool_name=overlapping.name,
            file_path=_source_file(overlapping),
            source_id=overlapping.source_id or "",
            binding_ids=binding_ids,
        )
        warnings.append(message)
        binding_issues[observation_id].append(
            _identity_issue("invalid_tool_binding", message, "tool_identity.bindings")
        )

    consumed: set[str] = set()
    canonical: list[Tool] = []
    bindings_by_id = {binding.id: binding for binding in bindings}
    for binding_id in sorted(selected_by_binding):
        members = selected_by_binding[binding_id]
        if any(
            len(observation_bindings[tool.observation_id or ""]) != 1
            for tool in members
        ):
            continue
        binding = bindings_by_id[binding_id]
        primary = _binding_primary(binding, members)
        merged, merge_issues = _merge_bound_observations(primary, members)
        observation_ids = sorted(tool.observation_id or "" for tool in members)
        tool_id = _stable_id(
            "tool_v2",
            {"binding_id": binding.id, "provider": binding.provider},
        )
        issues = [
            *merge_issues,
            *(issue for oid in observation_ids for issue in binding_issues.get(oid, [])),
        ]
        merged.id = tool_id
        merged.provider = binding.provider
        merged.observation_ids = observation_ids
        # The union, not the primary's. A reviewed binding may join
        # observations from several configured sources, and an authority
        # declared on one of them does not speak for the others.
        merged.configured_source_ids = sorted(
            {
                configured
                for member in members
                for configured in member.configured_source_ids
            }
        )
        merged.identity_assessment = _assessment(
            tool_id=tool_id,
            provider=binding.provider,
            status="conflicting" if issues else "declared",
            binding_id=binding.id,
            primary=primary,
            members=members,
            issues=issues,
        )
        canonical.append(merged)
        consumed.update(observation_ids)

    for tool in observations:
        observation_id = tool.observation_id or ""
        if observation_id in consumed:
            continue
        issues = binding_issues.get(observation_id, [])
        provider = tool.provider or tool.source_id or tool.source_type
        tool_id = _stable_id("tool_v2", {"observation_id": observation_id})
        tool.id = tool_id
        tool.provider = provider
        tool.observation_ids = [observation_id]
        tool.identity_assessment = _assessment(
            tool_id=tool_id,
            provider=provider,
            status="conflicting" if issues else "structural",
            binding_id=None,
            primary=tool,
            members=[tool],
            issues=issues,
        )
        canonical.append(tool)

    ids = [tool.id for tool in canonical]
    if len(ids) != len(set(ids)):
        raise RuntimeError("canonical tool identity hash collision")
    return sorted(canonical, key=lambda tool: (tool.id, tool.name)), list(dict.fromkeys(warnings))


def resolve_tool_selector(tools: Sequence[Tool], selector: Any) -> SelectorResolution:
    """Resolve a one-to-one manifest selector with no name fallback."""

    return ToolSelectorIndex.build(tools).resolve(selector)


def resolve_selectors_by_tool_id(
    tools: list[Tool],
    selectors: Iterable[Any],
    *,
    manifest_path: str,
    ambiguous_kind: str = "ambiguous_tool_selector",
    unresolved_kind: str = "unresolved_tool_selector",
    copy_tools: bool = True,
) -> tuple[dict[str, Any], list[Tool]]:
    """Resolve declarations and attach fail-closed issues to candidates."""

    resolved: dict[str, Any] = {}
    mutable = [tool.model_copy() for tool in tools] if copy_tools else tools
    selector_index = ToolSelectorIndex.build(mutable)
    by_id = selector_index.by_id
    for index, selector in enumerate(selectors):
        result = selector_index.resolve(selector)
        if result.resolved:
            tool = result.matches[0]
            if tool.id in resolved:
                _append_identity_issue(
                    by_id[tool.id],
                    _identity_issue(
                        "ambiguous_tool_selector",
                        f"Multiple {manifest_path} entries resolve to {tool.id}",
                        f"{manifest_path}/{index}",
                    ),
                )
                continue
            resolved[tool.id] = selector
            continue
        issue_kind = (
            ambiguous_kind
            if result.kind == "ambiguous_tool_selector"
            else unresolved_kind
        )
        issue = _identity_issue(
            issue_kind,
            result.message or "tool selector did not resolve",
            f"{manifest_path}/{index}",
        )
        # Ambiguous selectors poison every candidate because each could have
        # received the policy.  An unresolved selector applies nowhere; one
        # deterministic catalog-level witness is sufficient to fail closed
        # without multiplying the same configuration gap by tool count.
        targets = list(result.matches) or mutable[:1]
        for target in targets:
            _append_identity_issue(by_id[target.id], issue)
    return resolved, mutable


#: Prefix of every binding id ``_inventory_completion_bindings`` synthesizes.
#: Deterministic and derived from the manifest entry, so tool ids stay stable
#: across runs, and distinctive enough that a hand-written binding id colliding
#: with one is a typo rather than a coincidence — a collision is resolved in
#: favour of the reviewed entry.
_INVENTORY_BINDING_PREFIX = "tool_inventory:"


def _inventory_completion_bindings(
    loaded_sources: list[LoadedToolSource],
    observations: list[Tool],
    config: ToolIdentityConfig,
) -> tuple[list[ToolIdentityBindingConfig], list[str]]:
    """Desugar ``tool_inventories[].source_id`` into reviewed identity bindings.

    Without the field an inventory is an independent source: its entries become
    additional observations that share names with the ones static extraction
    already produced, so the catalog grows, the ``incomplete_surface`` gap keyed
    to the *original* source stays open, and the action selectors that used to
    resolve become ambiguous (#386). The manifest naming the completed source is
    what licenses the join — this function only turns that declaration into the
    binding a reviewer would otherwise have written by hand, one per matched
    name, with the inventory as ``primary`` so the merged tool carries the
    inventory's high extraction confidence.

    Three cases deliberately do not merge:

    * an inventory entry the completed source does not expose — it is a tool
      static extraction missed, which is the whole reason inventories exist, so
      it stays a standalone observation;
    * a name the completed source exposes more than once — the inventory alone
      does not say *which* observation it describes;
    * an observation a reviewed ``tool_identity.bindings[]`` entry already
      claims — an explicit human declaration outranks a desugared one, and
      double-claiming would invalidate both.
    """

    reviewed_members = {
        (member.source_id, member.tool)
        for binding in config.bindings
        for member in binding.members
    }
    # Seeded with the reviewed ids so a hand-written binding always keeps the
    # name, and grown as bindings are synthesized so two entries can never
    # collide into one key in ``bindings_by_id`` and silently drop a merge.
    claimed_ids = {binding.id for binding in config.bindings}
    loaded_source_ids = {loaded.source_id.strip() for loaded in loaded_sources}
    by_source_name: dict[tuple[str, str], list[Tool]] = defaultdict(list)
    for tool in observations:
        by_source_name[(tool.source_id or "", tool.name)].append(tool)

    synthesized: list[ToolIdentityBindingConfig] = []
    warnings: list[str] = []
    for loaded in loaded_sources:
        target = (loaded.completes_source_id or "").strip()
        inventory_id = loaded.source_id.strip()
        if not target:
            if loaded.is_tool_inventory:
                warnings.extend(
                    _unbound_inventory_warnings(
                        loaded, inventory_id, observations, reviewed_members
                    )
                )
            continue
        if target == inventory_id:
            warnings.append(self_referential_inventory_warning(inventory_id))
            continue
        if target not in loaded_source_ids:
            warnings.append(
                unknown_inventory_source_warning(
                    inventory_id,
                    target,
                    sorted(loaded_source_ids - {inventory_id}),
                )
            )
            continue
        ambiguous: list[str] = []
        for name in dict.fromkeys(tool.name for tool in loaded.tools):
            target_matches = by_source_name.get((target, name), [])
            if not target_matches:
                continue
            if len(target_matches) > 1:
                ambiguous.append(name)
                continue
            if len(by_source_name.get((inventory_id, name), [])) != 1:
                # Unreachable while ``_observations`` rejects duplicate
                # observation identities; skipping rather than reporting
                # ambiguity keeps the message honest if that ever changes.
                continue
            if (inventory_id, name) in reviewed_members or (
                target,
                name,
            ) in reviewed_members:
                continue
            binding_id = f"{_INVENTORY_BINDING_PREFIX}{inventory_id}#{name}"
            if binding_id in claimed_ids:
                continue
            claimed_ids.add(binding_id)
            inventory_selector = ToolObservationSelectorConfig(
                source_id=inventory_id, tool=name
            )
            synthesized.append(
                ToolIdentityBindingConfig(
                    id=binding_id,
                    provider=target,
                    reason=(
                        f"reviewed tool inventory {inventory_id!r} completes "
                        f"source {target!r} (tool_inventories[].source_id)"
                    ),
                    primary=inventory_selector,
                    members=[
                        ToolObservationSelectorConfig(source_id=target, tool=name),
                        inventory_selector,
                    ],
                )
            )
        if ambiguous:
            warnings.append(
                ambiguous_inventory_merge_warning(inventory_id, target, ambiguous)
            )
    return synthesized, warnings


def _unbound_inventory_warnings(
    loaded: LoadedToolSource,
    inventory_id: str,
    observations: list[Tool],
    reviewed_members: set[tuple[str, str]],
) -> list[str]:
    """Name an inventory that shadows the low-confidence source it duplicates.

    An inventory referenced without ``source_id`` is a legitimate way to declare
    tools no adapter can see, so overlap alone is not the complaint. The
    complaint is overlap with an observation that is *not yet* high confidence
    and that no reviewed binding claims: that pairing is the #386 shape, where
    the file the gate asked for is added beside the gap instead of closing it.
    One row per shadowed source, so a 40-tool inventory does not emit 40
    warnings into a gating count.
    """

    names = set(dict.fromkeys(tool.name for tool in loaded.tools))
    shadowed: dict[str, list[str]] = defaultdict(list)
    for tool in observations:
        source_id = tool.source_id or ""
        if source_id == inventory_id or tool.name not in names:
            continue
        if tool.extraction_confidence == "high":
            continue
        if (source_id, tool.name) in reviewed_members or (
            inventory_id,
            tool.name,
        ) in reviewed_members:
            continue
        shadowed[source_id].append(tool.name)
    return [
        unbound_inventory_duplicate_warning(
            inventory_id, source_id, sorted(dict.fromkeys(shadowed[source_id]))
        )
        for source_id in sorted(shadowed)
    ]


def _observations(
    loaded_sources: list[LoadedToolSource],
    repeated_artifacts: frozenset[str] = frozenset(),
) -> list[Tool]:
    observations: list[Tool] = []
    # Which *read* first produced each identity and out of which file, not
    # merely that something did. Three mistakes land here — a repeated manifest
    # entry, a duplicate definition inside one artifact, and two files
    # declaring one capability under one name — and they are repaired in
    # different places, so the check has to know which it saw (#329 review).
    # The file is half of that: without it, two files each naming an MCP server
    # ``github`` once were reported as a manifest entry repeated twice.
    seen: dict[tuple[str, str, str], tuple[int, str | None]] = {}
    for read_index, loaded in enumerate(loaded_sources):
        source_id = loaded.source_id.strip()
        if not source_id:
            # Ours, not theirs — and only since `tool_sources[].id` became
            # non-blank at manifest load (#329 review). A blank id used to be
            # reachable from a valid manifest, which made this sentence false
            # for the case that actually produced it. What remains is a loader
            # contract violation: no manifest edit can cause or repair it.
            raise InputParseError(
                "A tool source loader returned tools without naming the source "
                "they came from. That is a defect in the loader, not in this "
                "repository's configuration — check report.json "
                "loaded_adapters[] if a third-party adapter is installed.",
                details={"source_id": loaded.source_id},
            )
        for original in loaded.tools:
            if original.source_id is not None and original.source_id != source_id:
                raise InputParseError(
                    f"A tool source loader reported tool {original.name!r} as "
                    "belonging to a different source than the one it was read "
                    "from. That is a defect in the loader, not in this "
                    "repository's configuration — check report.json "
                    "loaded_adapters[] if a third-party adapter is installed.",
                    details={
                        "tool_name": original.name,
                        "tool_source_id": original.source_id,
                        "loaded_source_id": source_id,
                    },
                )
            # The extraction graph is no longer consumed after catalog
            # construction.  A shallow copy isolates identity fields while
            # avoiding a second recursive copy of every parameter schema.
            tool = original.model_copy()
            tool.source_id = source_id
            locator = _native_locator(tool)
            key = (tool.source_type, source_id, locator)
            first = seen.get(key)
            if first is not None:
                first_read, first_file = first
                # Theirs, and repairable — but only if the message names what
                # to open. The identity triple that detected the collision is
                # kept in ``details`` for machine consumers and bug reports
                # (#329); the sentence names the tool and the one file to edit.
                source_file = _source_file(tool)
                # Two reads of one source id is always a repeated entry. One
                # read is *not* always a duplicate definition: the OpenAI and
                # Anthropic loaders aggregate every configured artifact into a
                # single ``LoadedToolSource``, so listing one file twice under
                # ``openai_api.tools`` collides inside one read and used to be
                # reported as a defect in a perfectly valid file (#329
                # review). The manifest settles it — a path it declares twice
                # in one list is a repeated entry whatever the loader did with
                # it — and it is read from the config rather than inferred
                # from the observations, which cannot tell the two apart.
                declared_twice = source_file is not None and (
                    _normalized_declared_path(source_file) in repeated_artifacts
                )
                # Two *different* files first. Both other causes assert
                # something about a single artifact — that the manifest names
                # it twice, or that it defines the tool twice — and neither is
                # true when the identity is path-free (every MCP-like source
                # type) and two files declare the same server. The MCP reader
                # reconciles that within one ``tool_sources`` entry; what
                # reaches here has no reader that can, so it is reported with
                # the repairs that do exist rather than an invented one.
                if (
                    first_file is not None
                    and source_file is not None
                    and first_file != source_file
                ):
                    cause = DUPLICATE_ACROSS_ARTIFACTS
                elif first_read != read_index or declared_twice:
                    cause = REPEATED_SOURCE_ENTRY
                else:
                    cause = DUPLICATE_IN_SOURCE_ARTIFACT
                raise InputParseError(
                    duplicate_tool_observation_message(
                        tool_name=tool.name,
                        file_path=source_file,
                        source_id=source_id,
                        cause=cause,
                        other_file_path=first_file,
                    ),
                    details={
                        "failure": DUPLICATE_TOOL_IN_SOURCE,
                        "cause": cause,
                        "source_type": tool.source_type,
                        "source_id": source_id,
                        "native_locator": locator,
                        "tool_name": tool.name,
                        "source_file": source_file,
                        "other_source_file": first_file,
                    },
                )
            seen[key] = (read_index, _source_file(tool))
            observation_id = _stable_id(
                "obs_v1",
                {
                    "source_type": tool.source_type,
                    "source_id": source_id,
                    "native_locator": locator,
                },
            )
            tool.native_locator = locator
            tool.observation_id = observation_id
            tool.observation_ids = [observation_id]
            # Which configured ``tool_sources`` entry produced this
            # observation, carried from the dispatcher rather than re-derived
            # from ``source_id`` — the two namespaces overlap (#410 review).
            tool.configured_source_ids = (
                [loaded.configured_source_id] if loaded.configured_source_id else []
            )
            tool.provider = source_id
            observations.append(tool)
    return observations


def _source_file(tool: Tool) -> str | None:
    """The file this tool was read from, when the loader recorded one.

    Only two fields can answer this, and only one of them structurally.
    ``source_path`` is the typed field: the adapters that set it set a path and
    nothing else. ``source_ref`` is accepted as a fallback *only* when it
    carries no ``#``, because that is the shape the plain-path producers write
    (Google ADK, MCP) and any other shape is a locator we would be guessing at.

    ``source_location`` and ``source_pointer`` are deliberately absent.
    They are separate optional provenance fields and a third-party adapter may
    legally set either without a path at all — ``agent.py:12`` and
    ``/tools/0`` both reached an edit action as if they were files (#329
    review 3). A value that names no file is worth less than no value: the
    caller has a review route for exactly that case.
    """

    if tool.source_path:
        return tool.source_path
    if tool.source_ref and "#" not in tool.source_ref:
        return tool.source_ref.strip() or None
    return None


def _native_locator(tool: Tool) -> str:
    method = tool.annotations.get("httpMethod")
    path = tool.annotations.get("path")
    if method and path:
        return f"{str(method).upper()} {path}"
    if tool.source_type in _MCP_LIKE:
        return tool.name
    if tool.source_ref:
        return f"{tool.source_ref}#{tool.name}"
    if tool.source_location:
        return f"{tool.source_location}#{tool.name}"
    if tool.source_pointer:
        return f"{tool.source_pointer}#{tool.name}"
    return tool.name


def _binding_primary(binding: ToolIdentityBindingConfig, members: list[Tool]) -> Tool:
    matches = [
        tool
        for tool in members
        if tool.source_id == binding.primary.source_id
        and tool.name == binding.primary.tool
        and (
            binding.primary.source_type is None
            or tool.source_type == binding.primary.source_type
        )
    ]
    if len(matches) != 1:
        raise RuntimeError("validated binding primary did not resolve uniquely")
    return matches[0]


def _merge_bound_observations(primary: Tool, members: list[Tool]) -> tuple[Tool, list[SemanticIssue]]:
    merged = primary.model_copy(deep=True)
    issues: list[SemanticIssue] = []
    for member in sorted(members, key=lambda tool: tool.observation_id or ""):
        if member.observation_id == primary.observation_id:
            continue
        if (
            member.input_schema
            and merged.input_schema
            and _schema_signature(member.input_schema) != _schema_signature(merged.input_schema)
        ):
            issues.append(
                _identity_issue(
                    "conflicting_tool_identity",
                    "bound observations expose different input schemas",
                    member.source_pointer or member.source_ref,
                )
            )
        for key, value in member.annotations.items():
            if key in merged.annotations and merged.annotations[key] != value:
                issues.append(
                    _identity_issue(
                        "conflicting_tool_identity",
                        f"bound observations disagree on annotation {key!r}",
                        member.source_pointer or member.source_ref,
                    )
                )
                continue
            merged.annotations[key] = value
        existing_hints = {
            json.dumps(hint.model_dump(mode="json"), sort_keys=True, default=str)
            for hint in merged.risk_hints
        }
        for hint in member.risk_hints:
            key = json.dumps(hint.model_dump(mode="json"), sort_keys=True, default=str)
            if key not in existing_hints:
                merged.risk_hints.append(hint.model_copy(deep=True))
                existing_hints.add(key)
        if merged.auth.type and member.auth.type and merged.auth.type != member.auth.type:
            issues.append(
                _identity_issue(
                    "conflicting_tool_identity",
                    "bound observations disagree on authentication type",
                    member.source_pointer or member.source_ref,
                )
            )
        if merged.auth.mode != "unknown" and member.auth.mode != "unknown" and merged.auth.mode != member.auth.mode:
            issues.append(
                _identity_issue(
                    "conflicting_tool_identity",
                    "bound observations disagree on authority mode",
                    member.source_pointer or member.source_ref,
                )
            )
        merged.auth.scopes = sorted(set(merged.auth.scopes) | set(member.auth.scopes))
        merged.auth.alternatives.extend(
            alternative.model_copy(deep=True) for alternative in member.auth.alternatives
        )
        merged.auth.invalid_annotations.extend(member.auth.invalid_annotations)
    issues.extend(_backfill_preserved_evidence(merged, primary, members))
    _carry_unproven_tool_set(merged, members)
    return merged, issues


def _carry_unproven_tool_set(merged: Tool, members: list[Tool]) -> None:
    """Keep a member's *set*-level incompleteness on the canonical tool.

    The merge starts from the primary and copies nothing about extraction
    across, which is deliberate: promoting the primary's fidelity is what
    naming a reviewed inventory is *for* (#386). That reasoning holds only for
    claims about one tool's own interface, though. An identity assertion proves
    two observations describe the same operation; it says nothing about whether
    the module one of them came from exposes further tools nobody enumerated.

    So an ADK observation carrying ``dynamic_agent_kwargs`` merged into a
    high-confidence OpenAPI primary produced a high, pass-eligible canonical
    tool and a `passed` verdict, with the module's gap nowhere in the report
    (#400 review). Set-scoped reasons now survive the merge and cap the result;
    interface-scoped ones still resolve in the primary's favour.
    """

    unproven = [
        member
        for member in members
        if member.extraction.get("tool_set_proven") is False
    ]
    if not unproven:
        return
    carried: set[str] = set()
    for member in unproven:
        raw_gaps = member.extraction.get("surface_gaps")
        if isinstance(raw_gaps, list):
            carried.update(
                value for value in raw_gaps if isinstance(value, str) and value
            )
    existing = merged.extraction.get("surface_gaps")
    if isinstance(existing, list):
        carried.update(
            value for value in existing if isinstance(value, str) and value
        )
    merged.extraction["surface"] = SURFACE_PARTIAL
    merged.extraction["surface_gaps"] = sorted(carried)
    merged.extraction["tool_set_proven"] = False
    if merged.extraction_confidence == "high":
        merged.extraction["confidence"] = "medium"
        merged.extraction_confidence = "medium"


#: Tool fields a member may fill in when the primary has nothing there, and
#: whether a *populated* disagreement between contributors is a conflict.
#:
#: Source identity (``source_type``/``source_id``/locators) is deliberately
#: absent: the canonical row keeps the primary's identity, and a selector naming
#: another member resolves through :class:`IdentityAliases` instead.
#:
#: ``description`` and ``parameters`` backfill but never conflict. Prose differs
#: benignly whenever a reviewed inventory rewords a docstring, and ``parameters``
#: restates ``input_schema``, which is already compared — coarsely, on purpose —
#: by ``_schema_signature``. Reporting either as an identity conflict would
#: manufacture noise, not catch a disagreement.
_BACKFILL_FIELDS: tuple[tuple[str, bool], ...] = (
    ("description", False),
    ("input_schema", False),
    ("output_schema", True),
    ("parameters", False),
    ("function_signature", True),
    ("owner", True),
)
#: ``auth.source`` backfills but never conflicts, and that is not an oversight.
#: It names the *extractor* that produced the auth record — ``google_adk_static``
#: on an AST observation, ``mcp`` on the inventory reading of the same tool — so
#: two observations of one capability disagree by construction. Conflict-checking
#: it fired ``conflicting_tool_identity`` on every completed ADK tool and took
#: ``pass_eligible_actions`` to 0, which is the opposite of what this change is
#: for. ``credential_mode`` is a real claim about the credential and is checked.
_BACKFILL_AUTH_FIELDS: tuple[tuple[str, bool], ...] = (
    ("type", False),
    ("credential_mode", True),
    ("source", False),
)


def _donor_reference(donor: Tool) -> dict[str, Any]:
    """``SourceReference`` kwargs for the observation that supplied a value."""

    return {
        "type": donor.source_type,
        "ref": donor.source_ref,
        "location": donor.source_location,
        "path": donor.source_path,
        "start_line": donor.source_start_line,
        "end_line": donor.source_end_line,
        "start_column": donor.source_start_column,
        "pointer": donor.source_pointer,
    }


def _evidence_key(field: str, value: Any) -> Any:
    """Comparable form of a field value, for detecting real disagreement.

    Schemas compare through ``_schema_signature`` so a reviewed inventory adding
    formats, bounds, or ``additionalProperties: false`` reads as a refinement
    rather than a contradiction — the same compatibility rule bound
    ``input_schema`` already uses. Everything else compares exactly.
    """

    if field.endswith("_schema"):
        return json.dumps(_schema_signature(value), sort_keys=True, default=str)
    return value


def _backfill_preserved_evidence(
    merged: Tool, primary: Tool, members: list[Tool]
) -> list[SemanticIssue]:
    """Fill the primary's gaps from its members, and report real disagreements.

    A binding promotes the primary's *extraction fidelity* — that is the whole
    point of naming a reviewed inventory as primary — but the merge started from
    the primary and copied nothing else across, so completing a source also
    erased that source's own evidence. An n8n tool with ``apiKey``/``unscoped``
    auth, an output schema, and an owner came back high-confidence with unknown
    auth, ``{}`` output, and no owner: the closed ``incomplete_surface`` gap was
    replaced by ``partial_authority_evidence``, the same non-monotonic trade
    #386 is about (#386 review).

    Backfilling alone then had its own silent failure. Filling "the first
    non-empty value" resolves a genuine disagreement by observation-id order:
    two members reporting ``owner: team-a`` and ``owner: team-b`` produced a
    tool owned by ``team-a``, no issues, and ``pass_eligible=True``. Every
    contributor to a conflict-checked field is compared here, primary included,
    and more than one distinct populated value is ``conflicting_tool_identity``
    — which makes the identity non-pass-eligible via ``_assessment`` rather than
    letting a contradiction ride under a high-confidence label.
    """

    issues: list[SemanticIssue] = []
    ordered = sorted(members, key=lambda tool: tool.observation_id or "")

    def resolve(
        field: str,
        conflict_checked: bool,
        current: Any,
        candidates: list[tuple[Tool, Any]],
    ) -> Any:
        populated = [(tool, value) for tool, value in candidates if value]
        if conflict_checked:
            distinct = {_evidence_key(field, value) for _, value in populated}
            if len(distinct) > 1:
                donors = ", ".join(
                    sorted({tool.source_id or tool.source_type for tool, _ in populated})
                )
                issues.append(
                    _identity_issue(
                        "conflicting_tool_identity",
                        f"bound observations disagree on {field} ({donors})",
                        primary.source_pointer or primary.source_ref,
                    )
                )
        if current:
            return current
        if not populated:
            return current
        donor, value = populated[0]
        # The row keeps the primary's locators, so a value taken from a member
        # would otherwise be reported against an artifact that does not contain
        # it. Record who actually supplied it.
        merged.evidence_provenance[field] = _donor_reference(donor)
        return deepcopy(value)

    for field, conflict_checked in _BACKFILL_FIELDS:
        value = resolve(
            field,
            conflict_checked,
            getattr(merged, field),
            [(tool, getattr(tool, field)) for tool in ordered],
        )
        setattr(merged, field, value)
    for field, conflict_checked in _BACKFILL_AUTH_FIELDS:
        value = resolve(
            f"auth.{field}",
            conflict_checked,
            getattr(merged.auth, field),
            [(tool, getattr(tool.auth, field)) for tool in ordered],
        )
        setattr(merged.auth, field, value)
    # ``unknown`` is the absence of an authority claim, not a claim of none, so
    # a plain falsiness test would skip it. Disagreement between two *known*
    # modes is already reported by the per-member check above.
    if merged.auth.mode == "unknown":
        for tool in ordered:
            if tool.auth.mode != "unknown":
                merged.auth.mode = tool.auth.mode
                merged.evidence_provenance["auth.mode"] = _donor_reference(tool)
                break
    if not merged.auth.explicit and any(tool.auth.explicit for tool in ordered):
        merged.auth.explicit = True
    return issues


def _schema_signature(schema: Any) -> Any:
    """Binding identity uses the callable interface, not schema refinements.

    Reviewed inventories commonly add formats, bounds, item schemas, and
    ``additionalProperties: false`` that AST adapters cannot recover. Those
    refinements are compatible. Parameter names, requiredness, and coarse
    scalar/container types must still agree.
    """

    if not isinstance(schema, dict):
        return schema
    properties = schema.get("properties")
    if isinstance(properties, dict):
        return {
            "type": "object",
            "required": sorted(str(value) for value in schema.get("required") or []),
            "properties": {
                str(name): _coarse_schema_type(value)
                for name, value in sorted(properties.items())
            },
        }
    return {"type": _coarse_schema_type(schema)}


def _coarse_schema_type(value: Any) -> str:
    if not isinstance(value, dict):
        return "unknown"
    schema_type = value.get("type")
    if schema_type in {"integer", "number"}:
        return "number"
    return str(schema_type or "unknown")


def _assessment(
    *,
    tool_id: str,
    provider: str,
    status: str,
    binding_id: str | None,
    primary: Tool,
    members: list[Tool],
    issues: list[SemanticIssue],
) -> ToolIdentityAssessment:
    claims = [
        SemanticClaim(
            dimension="identity",
            value=tool.observation_id or "",
            confidence="high",
            provenance_kind="static_declaration",
            basis="reviewed_declaration" if binding_id else "protocol_structure",
            source="tool_identity_binding" if binding_id else "source_observation",
            source_pointer=tool.source_pointer or tool.source_ref,
            evidence={
                "source_type": tool.source_type,
                "source_id": tool.source_id,
                "native_locator": tool.native_locator,
            },
        )
        for tool in sorted(members, key=lambda item: item.observation_id or "")
    ]
    unique_issues = _unique_issues(issues)
    return ToolIdentityAssessment(
        tool_id=tool_id,
        status=cast(Any, status),
        provider=provider,
        binding_id=binding_id,
        primary_observation_id=primary.observation_id or "",
        observation_ids=sorted(tool.observation_id or "" for tool in members),
        claims=claims,
        issues=unique_issues,
        pass_eligible=status in {"declared", "structural"} and not unique_issues,
    )


def _append_identity_issue(tool: Tool, issue: SemanticIssue) -> None:
    assessment = tool.identity_assessment
    if assessment is None:
        return
    issues = _unique_issues([*assessment.issues, issue])
    tool.identity_assessment = assessment.model_copy(
        update={
            "status": "conflicting" if issue.kind != "unresolved_tool_selector" else "partial",
            "issues": issues,
            "pass_eligible": False,
        }
    )


def _identity_issue(kind: str, message: str, pointer: str | None) -> SemanticIssue:
    return SemanticIssue(
        kind=cast(Any, kind),
        dimension="identity",
        message=message,
        source="tool_identity",
        source_pointer=pointer,
    )


def _unique_issues(issues: list[SemanticIssue]) -> list[SemanticIssue]:
    by_key = {
        (issue.kind, issue.message, issue.source, issue.source_pointer): issue
        for issue in issues
    }
    return [by_key[key] for key in sorted(by_key, key=lambda row: tuple(value or "" for value in row))]


def _selector_value(selector: Any, field: str) -> str | None:
    value = getattr(selector, field, None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(selector, dict):
        raw = selector.get(field)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _render_selector(selector: Any) -> str:
    values = {
        field: _selector_value(selector, field)
        for field in ("tool", "tool_id", "provider", "source_type", "source_id")
    }
    return json.dumps({key: value for key, value in values.items() if value}, sort_keys=True)


def _stable_id(prefix: str, value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{prefix}_{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


__all__ = [
    "IdentityAliases",
    "SelectorResolution",
    "ToolSelectorIndex",
    "action_identity_aliases",
    "identity_aliases",
    "tool_identity_aliases",
    "build_tool_identity_catalog",
    "resolve_selectors_by_tool_id",
    "resolve_tool_selector",
]
