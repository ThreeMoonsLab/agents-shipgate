from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agents_shipgate.schemas.manifest._authority import (
    validate_authority_co_requirements,
    validate_authority_scopes,
)
from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG

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
        )
        return self


class ToolSourceConfig(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    id: str
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
