"""Manifest schema package.

The schema for ``shipgate.yaml`` is decomposed into one module per
top-level manifest section. This ``__init__`` re-exports every public
symbol so ``from agents_shipgate.schemas.manifest import X`` keeps
working for every X that used to live in the legacy monolithic
``schemas/manifest.py`` module.

If you are adding a new manifest section:

1. Create ``schemas/manifest/<section>.py`` with the Pydantic models.
2. Add the field on ``AgentsShipgateManifest`` in ``root.py``.
3. Re-export the new models below (under the alphabetised section block).
4. Regenerate ``docs/manifest-v0.1.json`` via
   ``python scripts/generate_schemas.py``.
5. If the section names a file an adapter reads, type the field as
   ``ArtifactPathConfig`` and ``declared_paths`` picks it up automatically.
   A plain ``str`` path field does not derive — register it in
   ``declared_paths._UNTYPED_PATH_FIELDS`` or it will never reach
   ``input_set_id`` (see issue #299).

Schema layering (enforced by ``tests/test_schema_boundaries.py``):
modules in this package may import from ``schemas.common`` and from
each other, but must NOT import from ``agents_shipgate.core.*``.
"""

from agents_shipgate.schemas.manifest._artifacts import (
    ArtifactPathConfig,
    NamedArtifactPathConfig,
)
from agents_shipgate.schemas.manifest._common import STRICT_MODEL_CONFIG
from agents_shipgate.schemas.manifest.action_surface import (
    ActionApprovalConfig,
    ActionAuthorityConfig,
    ActionAuthorityMode,
    ActionDeclarationConfig,
    ActionEffect,
    ActionEvidenceConfig,
    ActionPolicyConfig,
    ActionPolicyMatchConfig,
    ActionRiskTag,
    ActionSafeguardsConfig,
    ActionSurfaceConfig,
)
from agents_shipgate.schemas.manifest.agent import (
    AgentConfig,
    AgentSdkConfig,
)
from agents_shipgate.schemas.manifest.agent_bindings import (
    AgentBindingDeclarationConfig,
    AgentBindingRootConfig,
    AgentBindingsConfig,
    BoundToolSelectorConfig,
)
from agents_shipgate.schemas.manifest.anthropic import AnthropicConfig
from agents_shipgate.schemas.manifest.baseline import (
    BaselineConfig,
    BaselineIntegrityMode,
)
from agents_shipgate.schemas.manifest.checks import (
    ChecksConfig,
    SuppressionConfig,
)
from agents_shipgate.schemas.manifest.ci import CiConfig
from agents_shipgate.schemas.manifest.codex_plugin import (
    CodexPluginMcpInventoryConfig,
    CodexPluginsConfig,
)
from agents_shipgate.schemas.manifest.crewai import CrewAiConfig
from agents_shipgate.schemas.manifest.environment import EnvironmentConfig
from agents_shipgate.schemas.manifest.google_adk import GoogleAdkConfig
from agents_shipgate.schemas.manifest.human_ack import HumanAckDeclaration
from agents_shipgate.schemas.manifest.langchain import LangChainConfig
from agents_shipgate.schemas.manifest.n8n import N8nConfig
from agents_shipgate.schemas.manifest.openai_api import OpenAIApiConfig
from agents_shipgate.schemas.manifest.organization import (
    OrganizationAuditConfig,
    OrganizationConfig,
    OrganizationExceptionPolicyConfig,
    OrganizationTeamConfig,
)
from agents_shipgate.schemas.manifest.output import (
    OutputConfig,
    PacketOutputConfig,
)
from agents_shipgate.schemas.manifest.permissions import PermissionsConfig
from agents_shipgate.schemas.manifest.policies import (
    PoliciesConfig,
    PolicyToolEntry,
)
from agents_shipgate.schemas.manifest.policy_packs import PolicyPackConfig
from agents_shipgate.schemas.manifest.project import ProjectConfig
from agents_shipgate.schemas.manifest.risk_overrides import (
    RiskOverridesConfig,
    ToolRiskOverride,
)
from agents_shipgate.schemas.manifest.root import AgentsShipgateManifest
from agents_shipgate.schemas.manifest.severity_overrides import (
    OverrideAcknowledgement,
    SeverityOverrideEntry,
)
from agents_shipgate.schemas.manifest.tool_identity import (
    ToolIdentityBindingConfig,
    ToolIdentityConfig,
    ToolObservationSelectorConfig,
)
from agents_shipgate.schemas.manifest.tool_sources import (
    BUILTIN_PER_SCAN_ONLY_TOOL_SOURCE_TYPES,
    BUILTIN_TOOL_SOURCE_TYPES,
    ToolSourceConfig,
)
from agents_shipgate.schemas.manifest.validation import (
    ValidationConfig,
    ValidationEvidenceConfig,
    ValidationRequiredEvidenceConfig,
)

__all__ = [
    # tool_sources constants
    "BUILTIN_PER_SCAN_ONLY_TOOL_SOURCE_TYPES",
    "BUILTIN_TOOL_SOURCE_TYPES",
    # _common (re-exported for back-compat — was a non-underscore
    # module symbol of the legacy schemas.manifest module)
    "STRICT_MODEL_CONFIG",
    # action_surface
    "ActionApprovalConfig",
    "ActionAuthorityConfig",
    "ActionAuthorityMode",
    "ActionDeclarationConfig",
    "ActionEffect",
    "ActionEvidenceConfig",
    "ActionPolicyConfig",
    "ActionPolicyMatchConfig",
    "ActionRiskTag",
    "ActionSafeguardsConfig",
    "ActionSurfaceConfig",
    # agent
    "AgentConfig",
    "AgentSdkConfig",
    "AgentBindingDeclarationConfig",
    "AgentBindingRootConfig",
    "AgentBindingsConfig",
    "BoundToolSelectorConfig",
    # root
    "AgentsShipgateManifest",
    # anthropic
    "AnthropicConfig",
    # _artifacts
    "ArtifactPathConfig",
    # baseline
    "BaselineConfig",
    "BaselineIntegrityMode",
    # checks
    "ChecksConfig",
    # ci
    "CiConfig",
    # codex_plugin
    "CodexPluginMcpInventoryConfig",
    "CodexPluginsConfig",
    # crewai
    "CrewAiConfig",
    # environment
    "EnvironmentConfig",
    # google_adk
    "GoogleAdkConfig",
    # human_ack
    "HumanAckDeclaration",
    # langchain
    "LangChainConfig",
    # n8n
    "N8nConfig",
    # _artifacts (named)
    "NamedArtifactPathConfig",
    # openai_api
    "OpenAIApiConfig",
    # organization
    "OrganizationAuditConfig",
    "OrganizationConfig",
    "OrganizationExceptionPolicyConfig",
    "OrganizationTeamConfig",
    # output
    "OutputConfig",
    # severity_overrides
    "OverrideAcknowledgement",
    # output (packet)
    "PacketOutputConfig",
    # permissions
    "PermissionsConfig",
    # policies
    "PoliciesConfig",
    # policy_packs
    "PolicyPackConfig",
    # policies (entry)
    "PolicyToolEntry",
    # project
    "ProjectConfig",
    # risk_overrides
    "RiskOverridesConfig",
    # severity_overrides
    "SeverityOverrideEntry",
    # checks (suppression)
    "SuppressionConfig",
    # tool_sources
    "ToolSourceConfig",
    # tool_identity
    "ToolIdentityBindingConfig",
    "ToolIdentityConfig",
    "ToolObservationSelectorConfig",
    # risk_overrides
    "ToolRiskOverride",
    # validation
    "ValidationConfig",
    "ValidationEvidenceConfig",
    "ValidationRequiredEvidenceConfig",
]
