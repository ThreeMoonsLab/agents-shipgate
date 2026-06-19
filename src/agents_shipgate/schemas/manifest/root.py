from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from agents_shipgate.schemas.common import Severity
from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG
from agents_shipgate.schemas.manifest.action_surface import ActionSurfaceConfig
from agents_shipgate.schemas.manifest.agent import AgentConfig
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
from agents_shipgate.schemas.manifest.tool_sources import ToolSourceConfig
from agents_shipgate.schemas.manifest.validation import ValidationConfig


class AgentsShipgateManifest(BaseModel):
    model_config = STRICT_MODEL_CONFIG

    version: str
    project: ProjectConfig
    agent: AgentConfig
    environment: EnvironmentConfig
    tool_sources: list[ToolSourceConfig] = Field(default_factory=list)
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
    ci: CiConfig = Field(default_factory=CiConfig)
    baseline: BaselineConfig = Field(default_factory=BaselineConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    organization: OrganizationConfig | None = None
    # v0.22 (verifier cycle): declared human acknowledgements of trust-root
    # weakening (roadmap §5.4). Empty by default. Each entry is *declared*
    # evidence — never inferred — and editing this list in shipgate.yaml is
    # itself a trust-root change (SHIP-VERIFY-TRUST-ROOT-TOUCHED).
    human_ack: list[HumanAckDeclaration] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_sources_and_scope_text(self) -> AgentsShipgateManifest:
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
