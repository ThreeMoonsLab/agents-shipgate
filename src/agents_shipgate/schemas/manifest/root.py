from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from agents_shipgate.schemas.common import Severity
from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG
from agents_shipgate.schemas.manifest.action_surface import ActionSurfaceConfig
from agents_shipgate.schemas.manifest.agent import AgentConfig
from agents_shipgate.schemas.manifest.agent_bindings import AgentBindingsConfig
from agents_shipgate.schemas.manifest.anthropic import AnthropicConfig
from agents_shipgate.schemas.manifest.baseline import BaselineConfig
from agents_shipgate.schemas.manifest.checks import ChecksConfig
from agents_shipgate.schemas.manifest.ci import CiConfig
from agents_shipgate.schemas.manifest.codex_plugin import CodexPluginsConfig
from agents_shipgate.schemas.manifest.crewai import CrewAiConfig
from agents_shipgate.schemas.manifest.environment import EnvironmentConfig
from agents_shipgate.schemas.manifest.google_adk import GoogleAdkConfig
from agents_shipgate.schemas.manifest.human_ack import HumanAckDeclaration
from agents_shipgate.schemas.manifest.langchain import LangChainConfig
from agents_shipgate.schemas.manifest.n8n import N8nConfig
from agents_shipgate.schemas.manifest.openai_api import OpenAIApiConfig
from agents_shipgate.schemas.manifest.organization import OrganizationConfig
from agents_shipgate.schemas.manifest.output import OutputConfig
from agents_shipgate.schemas.manifest.permissions import PermissionsConfig
from agents_shipgate.schemas.manifest.policies import PoliciesConfig
from agents_shipgate.schemas.manifest.project import ProjectConfig
from agents_shipgate.schemas.manifest.risk_overrides import RiskOverridesConfig
from agents_shipgate.schemas.manifest.severity_overrides import (
    OverrideAcknowledgement,
    SeverityOverrideEntry,
)
from agents_shipgate.schemas.manifest.tool_identity import ToolIdentityConfig
from agents_shipgate.schemas.manifest.tool_sources import ToolSourceConfig
from agents_shipgate.schemas.manifest.validation import ValidationConfig

# Kept here rather than imported from ci.release_decision: the manifest schema
# must not depend on the decision engine, and this string is the scaffold's
# published placeholder.
REVIEW_REQUIRED_SENTINEL = "<REVIEW_REQUIRED>"


def _sentinel_paths(node: object, path: str = "") -> list[str]:
    """Dotted paths of every unfilled scaffold placeholder in a manifest."""

    if isinstance(node, dict):
        return [
            found
            for key, value in node.items()
            for found in _sentinel_paths(value, f"{path}.{key}" if path else str(key))
        ]
    if isinstance(node, (list, tuple)):
        return [
            found
            for index, value in enumerate(node)
            for found in _sentinel_paths(value, f"{path}[{index}]")
        ]
    if isinstance(node, str) and node.strip() == REVIEW_REQUIRED_SENTINEL:
        return [path or "<root>"]
    return []


def _unfilled_sentinel_error(unfilled: list[str]) -> ValueError:
    """One wording for the placeholder rejection, whenever it is detected."""

    listed = ", ".join(unfilled[:5])
    more = f" (+{len(unfilled) - 5} more)" if len(unfilled) > 5 else ""
    return ValueError(
        f"{REVIEW_REQUIRED_SENTINEL} is an unfilled scaffold placeholder "
        f"and is not reviewed evidence: {listed}{more}. Replace each one "
        "with a reviewed value, or delete the field if your answer does "
        "not take it."
    )


class AgentsShipgateManifest(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    version: str
    project: ProjectConfig
    agent: AgentConfig
    environment: EnvironmentConfig
    tool_sources: list[ToolSourceConfig] = Field(default_factory=list)
    tool_identity: ToolIdentityConfig = Field(default_factory=ToolIdentityConfig)
    openai_api: OpenAIApiConfig | None = None
    anthropic: AnthropicConfig | None = None
    google_adk: GoogleAdkConfig | None = None
    langchain: LangChainConfig | None = None
    crewai: CrewAiConfig | None = None
    codex_plugins: CodexPluginsConfig | None = None
    n8n: N8nConfig | None = None
    validation: ValidationConfig | None = None
    policies: PoliciesConfig = Field(default_factory=PoliciesConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    risk_overrides: RiskOverridesConfig = Field(default_factory=RiskOverridesConfig)
    checks: ChecksConfig = Field(default_factory=ChecksConfig)
    action_surface: ActionSurfaceConfig = Field(default_factory=ActionSurfaceConfig)
    agent_bindings: AgentBindingsConfig = Field(default_factory=AgentBindingsConfig)
    ci: CiConfig = Field(default_factory=CiConfig)
    baseline: BaselineConfig = Field(default_factory=BaselineConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    organization: OrganizationConfig | None = None
    # v0.22 (verifier cycle): declared human acknowledgements of trust-root
    # weakening (roadmap §5.4). Empty by default. Each entry is *declared*
    # evidence — never inferred — and editing this list in shipgate.yaml is
    # itself a trust-root change (SHIP-VERIFY-TRUST-ROOT-TOUCHED).
    human_ack: list[HumanAckDeclaration] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def reject_raw_review_sentinels(cls, data: Any) -> Any:
        """Catch a placeholder before the field it landed in rejects it first.

        The after-validator below can only see values that already parsed, so a
        sentinel in a *typed* field never reached it: pasting the binding
        scaffold with ``complete: <REVIEW_REQUIRED>`` — where the schema accepts
        only ``true`` — failed with "Input should be True", which does not tell
        the reader they pasted an unfinished scaffold. Reading the raw input
        first makes one wording cover every field, whatever its type.
        """

        if isinstance(data, dict):
            unfilled = sorted(_sentinel_paths(data))
            if unfilled:
                raise _unfilled_sentinel_error(unfilled)
        return data

    @model_validator(mode="after")
    def reject_unfilled_review_sentinels(self) -> AgentsShipgateManifest:
        """A scaffold placeholder must never read as reviewed evidence.

        The declaration scaffold ships ``<REVIEW_REQUIRED>`` in every slot a
        human owns, and tells the reader that a block still containing one
        closes nothing. Nothing enforced that: the manifest only checks that
        fields like ``authority.auth_type`` are non-blank, so a sentinel
        satisfied them and a verbatim paste was assessed as a reviewed
        declaration. Rejecting the sentinel at load time makes the promise true
        by construction and fails closed — an unfinished scaffold cannot change
        a verdict.
        """

        unfilled = sorted(_sentinel_paths(self.model_dump(mode="python")))
        if unfilled:
            raise _unfilled_sentinel_error(unfilled)
        return self

    @model_validator(mode="after")
    def require_sources_and_scope_text(self) -> AgentsShipgateManifest:
        source_ids = [source.id for source in self.tool_sources]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("tool_sources[].id values must be unique")
        has_google_adk = (
            any(source.type == "google_adk" for source in self.tool_sources)
            or self.google_adk is not None
            and self.google_adk.has_inputs()
        )
        has_langchain = (
            any(source.type == "langchain" for source in self.tool_sources)
            or self.langchain is not None
            and self.langchain.has_inputs()
        )
        has_crewai = (
            any(source.type == "crewai" for source in self.tool_sources)
            or self.crewai is not None
            and self.crewai.has_inputs()
        )
        has_codex_plugin = any(
            source.type == "codex_plugin" for source in self.tool_sources
        )
        has_codex_config = any(
            source.type == "codex_config" for source in self.tool_sources
        )
        has_n8n = self.n8n is not None and self.n8n.has_inputs()
        has_anthropic = self.anthropic is not None and self.anthropic.has_inputs()
        if (
            not self.tool_sources
            and self.openai_api is None
            and not has_anthropic
            and not has_google_adk
            and not has_langchain
            and not has_crewai
            and not has_n8n
            and not has_codex_plugin
            and not has_codex_config
        ):
            raise ValueError(
                "At least one of tool_sources, openai_api, anthropic, google_adk, "
                "langchain, crewai, n8n, codex_config, or codex_plugin is required"
            )
        if (
            not self.agent.declared_purpose
            and not self.agent.instructions_preview
            and not (self.openai_api and self.openai_api.prompt_files)
            and not (self.anthropic and self.anthropic.prompt_files)
            and not has_google_adk
            and not has_langchain
            and not has_crewai
            and not has_n8n
            and not has_codex_plugin
            and not has_codex_config
        ):
            raise ValueError(
                "agent.declared_purpose, agent.instructions_preview, "
                "openai_api.prompt_files, anthropic.prompt_files, framework "
                "inputs, n8n inputs, codex_config, or codex_plugin inputs are required"
            )
        return self

    def severity_overrides(self) -> dict[str, Severity]:
        """Back-compat accessor: ``{check_id: severity}`` scalar form.

        Pre-v0.17 callers passed this dict directly to
        ``apply_severity_overrides``. v0.17 introduced the rich shape
        (``SeverityOverrideEntry``) but the scalar projection is still
        useful when the caller does not need reason/expires metadata.
        """
        return {
            check_id: entry.severity
            for check_id, entry in self.checks.severity_overrides.items()
        }

    def severity_override_entries(self) -> dict[str, SeverityOverrideEntry]:
        """v0.17 (M1): rich ``{check_id: SeverityOverrideEntry}`` map.

        The current ``apply_severity_overrides`` implementation consumes
        this form so it can record ``reason`` and ``expires`` in the
        per-override audit row.
        """
        return self.checks.severity_overrides

    def acknowledge_overrides(self) -> list[OverrideAcknowledgement]:
        """v0.17 (M1): list of explicit override acknowledgements."""
        return self.checks.acknowledge_overrides

    def human_ack_declarations(self) -> list[HumanAckDeclaration]:
        """v0.22: declared human acknowledgements of trust-root weakening."""
        return self.human_ack
