from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agents_shipgate.schemas.manifest._authority import (
    validate_authority_co_requirements,
    validate_authority_scopes,
)
from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG
from agents_shipgate.schemas.text import (
    VISIBLE_CONTENT_PATTERN,
    has_visible_content,
)

#: v0.20 (PR #111 review fix P1 #3): the curated set of built-in
#: source types that may legitimately appear in ``tool_sources[].type``.
#: Used for documentation only — ``ToolSourceConfig.type`` is open
#: (``str``) so third-party adapters from the ``agents_shipgate.adapters``
#: entry-point group can declare custom source types. Unknown source
#: types fail at dispatcher time via ``AdapterRegistry.require``.
BUILTIN_TOOL_SOURCE_TYPES: tuple[str, ...] = (
    "mcp",
    "openapi",
    "openai_agents_sdk",
    "google_adk",
    "langchain",
    "crewai",
    "codex_config",
    "codex_plugin",
    "conductor",
)



def builtin_tool_source_types_text() -> str:
    """The accepted ``tool_sources[].type`` values, as prose for a message.

    Three messages name this list — ``AdapterRegistry.require``'s remediation
    and the two ``SHIP-DIAG-UNKNOWN-ADAPTER-SOURCE-TYPE`` routes — and all
    three used to type it out. Two of them had dropped ``codex_config`` and
    ``conductor``, so the structured recovery for an unresolved source type
    told an adopter that two accepted values were not accepted (#441). The
    ``init`` scaffold's comment renders from the same tuple.
    """

    return ", ".join(BUILTIN_TOOL_SOURCE_TYPES)


#: Built-in adapters that are intentionally NOT permitted in
#: ``tool_sources[]`` because they are per_scan-only and ingest
#: configuration from their dedicated top-level manifest section.
#: Placing one of these in ``tool_sources`` is a user mistake; we
#: reject it at manifest-load time with a clear error rather than
#: silently no-op'ing it in pass 1 of the dispatcher.
BUILTIN_PER_SCAN_ONLY_TOOL_SOURCE_TYPES: frozenset[str] = frozenset(
    {"n8n", "openai_api", "anthropic_api", "validation"}
)


#: The reviewed modes a source may declare. Identical to
#: ``ActionAuthorityMode``: the same claim, made once for a whole source
#: instead of once per action.
SourceAuthorityMode = Literal["none", "scoped", "unscoped", "ambient"]


class SourceAuthorityConfig(BaseModel):
    """Reviewed authority for every action a tool source contributes (#410).

    Authority is a fact about a *deployment*, not about a function: the six
    Salesforce tools behind one OAuth client share one answer, and asking for
    it once per tool asks the same infrastructure question six times. That is
    not merely tedious — it is what breeds the copy-paste that breeds wrong
    answers, and a wrong authority declaration is the one that makes an
    unscoped production credential read as ``mode: none``.

    Declared here, it applies to every action of the source. An
    ``action_surface.actions[]`` row that declares its own ``authority``
    overrides it for that action, and the resolver treats both spellings
    identically: same conflict rule against the source's own published
    evidence, same refusal to stand in for authority the source itself
    publishes ambiguously.

    ``scopes`` lives inside this block rather than beside it, because unlike an
    action row there is no sibling permission list for a source to own.
    """

    model_config = STRICT_MODEL_CONFIG

    mode: SourceAuthorityMode
    auth_type: str | None = None
    credential_mode: str | None = None
    scopes: list[str] = Field(default_factory=list)
    reason: str | None = None

    @field_validator("scopes")
    @classmethod
    def validate_concrete_scopes(cls, scopes: list[str]) -> list[str]:
        return validate_authority_scopes(scopes, label="tool_sources[].authority.scopes")

    @model_validator(mode="after")
    def validate_co_requirements(self) -> SourceAuthorityConfig:
        validate_authority_co_requirements(
            mode=self.mode,
            auth_type=self.auth_type,
            scopes=self.scopes,
            reason=self.reason,
            mode_label="tool_sources[].authority.mode",
            credential_mode=self.credential_mode,
        )
        return self


class SourceBindingConfig(BaseModel):
    """Reviewed statement that this source's published surface *is* the surface under review.

    Binding is real information for an **agent**: a catalog may hold 63 OpenAPI
    operations of which the agent wires 5, and #385 drew that boundary
    deliberately — catalog membership is never evidence of capability.

    For a **tool server** there is no such gap. The repository under review is
    the tool surface: anything in its published ``tools/list`` is callable by
    any client that connects to it, there is no root agent, and there is
    nothing to select between. Stating that one structural fact used to cost
    one ``agent_bindings.declarations[].tools`` row per tool — 116 of them for
    ``github/github-mcp-server`` — which is what breeds the copy-paste that
    breeds wrong answers, and is where an adopter stops (#432).

    Declared here it applies to every tool the source contributes, exactly as
    ``authority`` applies to every action it contributes. It is **additive and
    widening**: it can only move tools *into* the analysed surface, where every
    check then judges them. A source with no ``binding`` block behaves exactly
    as before.

    It stays a human declaration. Inferring "this source binds everything" from
    the source's own content is the #268 attack, and the point of this block is
    to make the statement one line, not to remove it.
    """

    model_config = STRICT_MODEL_CONFIG

    #: Spelled exactly as ``agent_bindings.declarations[].complete``, and for
    #: the same reason: the closed-world assertion is the thing being reviewed,
    #: so there is no ``false`` to write. Presence of the block is the claim.
    complete: Literal[True] = True
    # ``pattern`` is carried for the reason ``action_surface.actions[].override``
    # carries it: the *published* schema must reject what the runtime rejects.
    # Without it the schema accepted ``reason: ''`` that the CLI refuses, which
    # is worse than no schema. The validator below stays the authority.
    reason: str = Field(pattern=VISIBLE_CONTENT_PATTERN)

    @field_validator("complete", mode="before")
    @classmethod
    def require_literal_true(cls, value: Any) -> Any:
        """Reject what the published schema rejects, in the same direction.

        ``docs/manifest-v0.1.json`` says ``type: boolean`` and this file is
        advertised for live editor validation, but the runtime coerced
        ``complete: 1`` to ``True`` — so an editor refused a manifest the CLI
        accepted, about a *reviewed assertion*. A ``strict`` constraint cannot
        be applied to a ``Literal`` schema, so the rule is stated here instead;
        the schema already carries it.

        The older ``agent_bindings.declarations[].complete`` is deliberately
        left as it is: tightening a field that has shipped is a compatibility
        decision of its own, and this one has no history to break.
        """

        if value is not None and not isinstance(value, bool):
            raise ValueError(
                "tool_sources[].binding.complete is the reviewed closed-world "
                "assertion and must be written as the boolean true"
            )
        return value

    @field_validator("reason")
    @classmethod
    def require_visible_reason(cls, value: str) -> str:
        # ``strip()`` is not the question. A reason made only of U+200B renders
        # as nothing to the reviewer this block exists for, and this field is
        # the whole record that anyone reviewed the published surface.
        if not has_visible_content(value):
            raise ValueError(
                "tool_sources[].binding.reason must record how the published "
                "surface was reviewed, with visible content; a reviewed "
                "declaration with a blank reason is not a reviewed declaration"
            )
        return value.strip()


class ToolSourceConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    # The non-blank rule is stated twice on purpose, and the parity test in
    # `tests/test_config.py` is what keeps the two from drifting. The
    # validator below owns the *message* — it names the keys this id is
    # joined on, which a generic constraint error cannot — while the pattern
    # is what reaches `docs/manifest-v0.1.json`, where a consumer validating a
    # manifest against the published schema would otherwise accept an id the
    # runtime refuses (#329 review). `\S` is a search, so " orders " passes it
    # and is then stripped, and "   " fails in both places.
    id: str = Field(json_schema_extra={"pattern": r"\S"})
    # v0.20 (PR #111 review fix P1 #3): opened from a closed Literal to
    # ``str`` so manifests can reference third-party per_source adapters
    # registered via the ``agents_shipgate.adapters`` entry-point group.
    # Without this relaxation, ``ToolSourceConfig.model_validate``
    # rejected ``type: my_custom_source`` at manifest-load time —
    # before adapter discovery had a chance to register the loader —
    # making the v0.20 third-party adapter surface unusable for its
    # main advertised use case.
    #
    # Built-in source types are enumerated in
    # ``BUILTIN_TOOL_SOURCE_TYPES`` above (documentation only). Unknown
    # source types are still rejected with a routable ``ConfigError``
    # (exit 2) at dispatch time via ``AdapterRegistry.require``. Typos
    # in built-in names therefore fail loudly with the same exit code
    # as before — just from a different code path.
    #
    # Per_scan-only built-ins (``n8n``, ``openai_api``, ``anthropic_api``,
    # ``validation``) remain explicitly REJECTED here (model validator
    # below) because they ingest config from their dedicated top-level
    # manifest section, not from ``tool_sources[]``.
    type: str
    path: str | None = None
    trust: str | None = None
    mode: str | None = None
    optional: bool = False
    # #410 increment 3. Additive: a source with no ``authority`` block behaves
    # exactly as before, and every existing per-action declaration keeps
    # working and keeps winning.
    authority: SourceAuthorityConfig | None = None
    # #432. Additive and widening: a source with no ``binding`` block behaves
    # exactly as before, and a source that has one can only move its own tools
    # into the analysed surface, never out of it.
    binding: SourceBindingConfig | None = None

    @field_validator("id", mode="before")
    @classmethod
    def canonical_source_id(cls, value: Any) -> Any:
        """A source id is a join key, so it is stripped and never blank.

        ``tool_identity.bindings[].members[].source_id`` and
        ``tool_inventories[].source_id`` are already stripped where they are
        declared, and this side was not — so ``id: " orders "`` matched none
        of them and quietly completed nothing. A blank id matches nothing at
        all and is a manifest error, not the loader defect the duplicate-check
        message downstream would otherwise call it (#329 review).
        """

        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError(
                "tool_sources[].id must name the source, but is blank; it is "
                "the key tool_inventories[].source_id and "
                "tool_identity.bindings[].members[].source_id join on"
            )
        return normalized

    @model_validator(mode="after")
    def reject_per_scan_only_builtins(self) -> ToolSourceConfig:
        # Each per_scan-only built-in has its own dedicated manifest
        # section (``n8n.workflows``, ``openai_api.tools``, …); putting
        # it in ``tool_sources`` is always a user mistake. Third-party
        # per_scan adapters (also discovered via entry points) do NOT
        # require ``tool_sources`` entries either; the dispatcher
        # iterates them in pass-2 regardless.
        if self.type in BUILTIN_PER_SCAN_ONLY_TOOL_SOURCE_TYPES:
            raise ValueError(
                f"tool_sources entry {self.id!r} declares type "
                f"{self.type!r}, which is a per_scan-only built-in. "
                f"Move this configuration to the top-level "
                f"``{self.type}:`` manifest section."
            )
        return self

    @model_validator(mode="after")
    def require_path_when_needed(self) -> ToolSourceConfig:
        if (
            self.type
            in {
                "mcp",
                "openapi",
                "google_adk",
                "langchain",
                "crewai",
                "codex_config",
                "codex_plugin",
                "conductor",
            }
            and not self.path
        ):
            raise ValueError(f"tool source {self.id!r} requires path")
        if self.type == "codex_plugin" and self.mode not in {
            None,
            "package",
            "marketplace",
        }:
            raise ValueError(
                f"tool source {self.id!r} has invalid codex_plugin mode "
                f"{self.mode!r}; expected 'package' or 'marketplace'"
            )
        return self
