from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

from agents_shipgate.schemas.instruction_structure import InstructionStructureEvidence

HOST_GRANTS_INVENTORY_SCHEMA_VERSION = "0.7"
HOST_GRANTS_BASELINE_SCHEMA_VERSION = "0.7"
HOST_GRANTS_DRIFT_SCHEMA_VERSION = "0.7"

HostName = Literal["codex", "claude-code", "cursor", "vscode", "github"]
HostGrantScope = Literal["repository", "local_static"]


# Frozen v0.1 models. They remain readable but are never emitted by current code.
class HostMcpServerGrantV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str
    file: str
    server: str
    transport: str
    command_or_url: str | None = None
    env_keys: list[str] = Field(default_factory=list)
    config_sha256: str


class HostPermissionRuleGrantV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    kind: str
    rule: str
    wildcard: bool = False


class HostHookGrantV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    event: str
    config_sha256: str


class HostWorkflowGrantV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    triggers: list[str] = Field(default_factory=list)
    pull_request_target: bool = False
    write_all: bool = False
    write_scopes: list[str] = Field(default_factory=list)


class HostGrantsInventoryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host_grants_inventory_schema_version: Literal["0.1"] = "0.1"
    workspace: str
    mcp_servers: list[HostMcpServerGrantV1] = Field(default_factory=list)
    permission_rules: list[HostPermissionRuleGrantV1] = Field(default_factory=list)
    hooks: list[HostHookGrantV1] = Field(default_factory=list)
    workflows: list[HostWorkflowGrantV1] = Field(default_factory=list)
    codex_config_present: list[str] = Field(default_factory=list)
    parse_warnings: list[str] = Field(default_factory=list)


class HostGrantsInventoryArtifactV1(RootModel[HostGrantsInventoryV1]):
    root: HostGrantsInventoryV1


class HostArtifactV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    host: HostName
    scope: HostGrantScope
    path: str
    kind: Literal[
        "config", "mcp", "hooks", "workflow", "instructions", "requirements"
    ]
    parse_status: Literal["parsed", "failed", "unsupported"]
    redacted_sha256: str | None = None


class HostCoverageV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: HostName
    scope: HostGrantScope
    status: Literal["complete", "partial", "experimental"]
    sources_expected: list[str] = Field(default_factory=list)
    sources_observed: list[str] = Field(default_factory=list)
    issue_ids: list[str] = Field(default_factory=list)


class HostInventoryIssueV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_id: str
    kind: Literal[
        "parse_failed",
        "unreadable",
        "unsupported",
        "unresolved_precedence",
        "dynamic_source_excluded",
        "remote_source_excluded",
    ]
    host: HostName
    source: str
    message: str
    blocking: bool


class HostGrantBaseV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grant_id: str
    host: HostName
    scope: HostGrantScope
    source: str
    config_sha256: str
    access: Literal["none", "read", "write", "execute", "external", "admin", "unknown"]
    risk: Literal["none", "low", "medium", "high", "critical", "unknown"]


class HostMcpServerGrantV2(HostGrantBaseV2):
    kind: Literal["mcp_server"] = "mcp_server"
    server: str
    transport: str
    endpoint: str | None = None
    env_keys: list[str] = Field(default_factory=list)
    header_keys: list[str] = Field(default_factory=list)


class HostPermissionRuleGrantV2(HostGrantBaseV2):
    kind: Literal["permission_rule"] = "permission_rule"
    disposition: Literal["allow", "ask", "deny"]
    rule: str
    wildcard: bool = False


class HostPermissionModeGrantV2(HostGrantBaseV2):
    kind: Literal["permission_mode"] = "permission_mode"
    setting: str
    value: str


class HostHookGrantV2(HostGrantBaseV2):
    kind: Literal["hook"] = "hook"
    event: str


class HostSandboxGrantV2(HostGrantBaseV2):
    kind: Literal["sandbox"] = "sandbox"
    setting: str
    value: str


class HostAdditionalPathGrantV2(HostGrantBaseV2):
    kind: Literal["additional_path"] = "additional_path"
    path: str


class HostPluginGrantV2(HostGrantBaseV2):
    kind: Literal["plugin_or_app"] = "plugin_or_app"
    name: str
    enabled: bool | None = None


class HostProfileGrantV2(HostGrantBaseV2):
    kind: Literal["profile"] = "profile"
    profile: str
    resolved: bool


class HostRequirementGrantV2(HostGrantBaseV2):
    kind: Literal["requirement"] = "requirement"
    requirement: str
    value: str


class HostWorkflowGrantV2(HostGrantBaseV2):
    kind: Literal["workflow"] = "workflow"
    triggers: list[str] = Field(default_factory=list)
    pull_request_target: bool = False
    write_all: bool = False
    write_scopes: list[str] = Field(default_factory=list)


class HostInstructionGrantV2(HostGrantBaseV2):
    kind: Literal["instruction_trust_root"] = "instruction_trust_root"
    path: str


HostGrantV2 = Annotated[
    HostMcpServerGrantV2
    | HostPermissionRuleGrantV2
    | HostPermissionModeGrantV2
    | HostHookGrantV2
    | HostSandboxGrantV2
    | HostAdditionalPathGrantV2
    | HostPluginGrantV2
    | HostProfileGrantV2
    | HostRequirementGrantV2
    | HostWorkflowGrantV2
    | HostInstructionGrantV2,
    Field(discriminator="kind"),
]


class HostGrantsInventoryV2(BaseModel):
    """Typed, redacted static inventory emitted by ``audit --host``."""

    model_config = ConfigDict(extra="forbid")

    host_grants_inventory_schema_version: Literal["0.2"] = "0.2"
    workspace: str
    scope: HostGrantScope = "repository"
    artifacts: list[HostArtifactV2] = Field(default_factory=list)
    host_coverage: list[HostCoverageV2] = Field(default_factory=list)
    grants: list[HostGrantV2] = Field(default_factory=list)
    issues: list[HostInventoryIssueV2] = Field(default_factory=list)
    excluded_scopes: list[str] = Field(default_factory=list)
    static_analysis_only: Literal[True] = True
    runtime_session_verified: Literal[False] = False


class HostGrantsNormalizedSnapshotV2(BaseModel):
    """Portable, redacted subset bound into a v0.2 baseline."""

    model_config = ConfigDict(extra="forbid")

    scope: HostGrantScope
    artifacts: list[HostArtifactV2] = Field(default_factory=list)
    grants: list[HostGrantV2] = Field(default_factory=list)
    host_coverage: list[HostCoverageV2] = Field(default_factory=list)


class HostGrantsBaselineV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host_grants_schema_version: Literal["0.2"] = "0.2"
    scope: HostGrantScope
    inventory_sha256: str
    inventory: HostGrantsNormalizedSnapshotV2


class HostGrantChangeV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grant_id: str
    baseline: dict[str, Any] | None = None
    current: dict[str, Any] | None = None


class HostArtifactChangeV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    baseline: dict[str, Any] | None = None
    current: dict[str, Any] | None = None


class HostCoverageChangeV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: HostName
    baseline: dict[str, Any] | None = None
    current: dict[str, Any] | None = None


class HostGrantsDriftV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host_grants_schema_version: Literal["0.2"] = "0.2"
    baseline_file: str
    scope: HostGrantScope
    comparison_status: Literal["comparable", "incomparable"]
    baseline_sha256: str | None = None
    current_sha256: str | None = None
    has_drift: bool | None
    changes: list[HostGrantChangeV2] = Field(default_factory=list)
    artifact_changes: list[HostArtifactChangeV2] = Field(default_factory=list)
    coverage_changes: list[HostCoverageChangeV2] = Field(default_factory=list)
    expansion_signals: list[str] = Field(default_factory=list)
    issues: list[HostInventoryIssueV2] = Field(default_factory=list)
    incomparable_reasons: list[str] = Field(default_factory=list)
    next_action: str | None = None


class HostGrantsInventoryArtifactV2(RootModel[HostGrantsInventoryV2]):
    root: HostGrantsInventoryV2


class HostGrantsBaselineArtifactV2(RootModel[HostGrantsBaselineV2]):
    root: HostGrantsBaselineV2


class HostGrantsDriftArtifactV2(RootModel[HostGrantsDriftV2]):
    root: HostGrantsDriftV2


# Frozen, permissive drift reader retained for pre-0.2 artifacts.
class HostGrantsDriftV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host_grants_schema_version: str
    baseline_file: str
    baseline_sha256: str | None = None
    current_sha256: str | None = None
    has_drift: bool
    drift: dict[str, Any] = Field(default_factory=dict)
    expansion_signals: list[str] = Field(default_factory=list)
    parse_warnings: list[str] = Field(default_factory=list)
    load_error: str | None = None


class HostGrantsDriftArtifactV1(RootModel[HostGrantsDriftV1]):
    root: HostGrantsDriftV1


# v0.3 separates captured bytes from parsed instruction structure. The closed
# v0.2 models above remain frozen: a legacy baseline cannot assert this proof.
class HostArtifactV3(HostArtifactV2):
    instruction_structure: InstructionStructureEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )


class HostGrantsInventoryV3(HostGrantsInventoryV2):
    host_grants_inventory_schema_version: Literal["0.3"] = "0.3"
    artifacts: list[HostArtifactV3] = Field(default_factory=list)


class HostGrantsNormalizedSnapshotV3(HostGrantsNormalizedSnapshotV2):
    artifacts: list[HostArtifactV3] = Field(default_factory=list)


class HostGrantsBaselineV3(HostGrantsBaselineV2):
    host_grants_schema_version: Literal["0.3"] = "0.3"
    inventory: HostGrantsNormalizedSnapshotV3


class HostGrantsDriftV3(HostGrantsDriftV2):
    host_grants_schema_version: Literal["0.3"] = "0.3"


class HostGrantsInventoryArtifactV3(RootModel[HostGrantsInventoryV3]):
    root: HostGrantsInventoryV3


class HostGrantsBaselineArtifactV3(RootModel[HostGrantsBaselineV3]):
    root: HostGrantsBaselineV3


class HostGrantsDriftArtifactV3(RootModel[HostGrantsDriftV3]):
    root: HostGrantsDriftV3


# v0.4 records workflow recipients and compares effective writes. Older
# snapshots cannot prove that an absent reusable call was inspected.
class HostReusableWorkflowCallV4(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: str
    uses: str
    secrets_inherit: bool


class HostWorkflowPermissionsV4(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: str
    state: Literal["explicit", "repository_default", "unresolved"]
    permissions: dict[str, Literal["read", "write"]]


class HostWorkflowGrantV4(HostWorkflowGrantV2):
    permission_contexts: list[HostWorkflowPermissionsV4]
    effective_write_scopes: list[str]
    reusable_calls: list[HostReusableWorkflowCallV4]


HostGrantV4 = Annotated[
    HostMcpServerGrantV2
    | HostPermissionRuleGrantV2
    | HostPermissionModeGrantV2
    | HostHookGrantV2
    | HostSandboxGrantV2
    | HostAdditionalPathGrantV2
    | HostPluginGrantV2
    | HostProfileGrantV2
    | HostRequirementGrantV2
    | HostWorkflowGrantV4
    | HostInstructionGrantV2,
    Field(discriminator="kind"),
]


class HostGrantsInventoryV4(HostGrantsInventoryV3):
    host_grants_inventory_schema_version: Literal["0.4"] = "0.4"
    grants: list[HostGrantV4] = Field(default_factory=list)


class HostGrantsNormalizedSnapshotV4(HostGrantsNormalizedSnapshotV3):
    grants: list[HostGrantV4] = Field(default_factory=list)


class HostGrantsBaselineV4(HostGrantsBaselineV3):
    host_grants_schema_version: Literal["0.4"] = "0.4"
    inventory: HostGrantsNormalizedSnapshotV4


class HostGrantsDriftV4(HostGrantsDriftV3):
    host_grants_schema_version: Literal["0.4"] = "0.4"


class HostGrantsInventoryArtifactV4(RootModel[HostGrantsInventoryV4]):
    root: HostGrantsInventoryV4


class HostGrantsBaselineArtifactV4(RootModel[HostGrantsBaselineV4]):
    root: HostGrantsBaselineV4


class HostGrantsDriftArtifactV4(RootModel[HostGrantsDriftV4]):
    root: HostGrantsDriftV4


# v0.5 records the in-tree links a read followed (#700, step two). A boundary
# path that is a link is read through to its in-tree target, and the artifact
# names each hop, so a retargeted link reads as a changed artifact.
class HostArtifactV4(HostArtifactV3):
    resolved_through: list[str] = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )


class HostGrantsInventoryV5(HostGrantsInventoryV4):
    host_grants_inventory_schema_version: Literal["0.5"] = "0.5"
    artifacts: list[HostArtifactV4] = Field(default_factory=list)


class HostGrantsNormalizedSnapshotV5(HostGrantsNormalizedSnapshotV4):
    artifacts: list[HostArtifactV4] = Field(default_factory=list)


class HostGrantsBaselineV5(HostGrantsBaselineV4):
    host_grants_schema_version: Literal["0.5"] = "0.5"
    inventory: HostGrantsNormalizedSnapshotV5


class HostGrantsDriftV5(HostGrantsDriftV4):
    host_grants_schema_version: Literal["0.5"] = "0.5"


class HostGrantsInventoryArtifactV5(RootModel[HostGrantsInventoryV5]):
    root: HostGrantsInventoryV5


class HostGrantsBaselineArtifactV5(RootModel[HostGrantsBaselineV5]):
    root: HostGrantsBaselineV5


class HostGrantsDriftArtifactV5(RootModel[HostGrantsDriftV5]):
    root: HostGrantsDriftV5


# v0.6 records the action references a workflow's steps declare (#771). The
# closed v0.5 workflow grant above stays frozen: a v0.4/v0.5 snapshot did not
# read step references, so its silence cannot assert that none changed.
class HostWorkflowStepActionV6(BaseModel):
    """One step's declared action reference, read as text and never fetched.

    ``form`` is ``remote`` for ``owner/repo[/path]@ref``, ``docker`` for
    ``docker://…``, and ``unresolved`` for a value Shipgate does not resolve
    to an action identity; ``unresolved_reason`` then says which. A job whose
    ``steps`` is not a list, or a step that is not a mapping, is listed as
    unresolved too, with no ``uses``, so an absent list still means the steps
    were read and declare nothing. A local
    ``./…`` reference is not listed: composite actions remain unread (#701).
    ``step`` is the step's ``id``, else its ``name``, else ``steps[N]`` — the
    evidence a reviewer uses to find it, not part of the comparison. ``job``
    and ``step`` are published labels: credential-shaped text in either, and
    the userinfo of any ``scheme://…@`` inside it, is redacted (#802).
    """

    model_config = ConfigDict(extra="forbid")

    job: str
    step: str
    uses: str | None
    form: Literal["remote", "docker", "unresolved"]
    unresolved_reason: Literal[
        "expression",
        "unsupported_reference",
        "not_a_string",
        "redacted",
        "steps_not_a_list",
        "step_not_a_mapping",
    ] | None = None


class HostReusableWorkflowSecretV6(BaseModel):
    """One named secret a job passes to the reusable workflow it calls (#693).

    ``destination`` is the callee's secret input name as the caller writes it.
    ``source`` is ``NAME`` from a whole-value ``${{ secrets.NAME }}``, and
    ``form`` is then ``secret``. The name is a reference, never a value: it
    does not establish the secret's privilege, whether the caller has it, or
    what the called workflow does with it. Anything else is ``unresolved``,
    and none of its value is published or digested: a literal value, any
    other expression, a non-string, or a ``secrets`` that is neither
    ``inherit`` nor a mapping (``destination`` is then ``null``). A name the
    credential redactors rewrite is ``redacted`` and records a blocking
    coverage issue, because two values that redact alike must never compare as
    unchanged. Every other unresolved mapping records a non-blocking one naming
    its ``job/destination``: only that value is uncompared, so the rest of the
    file still compares.
    """

    model_config = ConfigDict(extra="forbid")

    destination: str | None
    source: str | None
    form: Literal["secret", "unresolved"]
    unresolved_reason: Literal[
        "literal_value",
        "expression",
        "not_a_string",
        "redacted",
        "secrets_not_a_mapping",
    ] | None = None


class HostReusableWorkflowCallV6(HostReusableWorkflowCallV4):
    # Both present only when set, so a call with no named mapping and an
    # ordinary target keeps its v0.6 shape from #771. The schema version, not
    # the key, separates "read, none declared" from a legacy call.
    uses_redacted: bool = Field(default=False, exclude_if=lambda value: not value)
    secret_mappings: list[HostReusableWorkflowSecretV6] = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )


class HostWorkflowGrantV6(HostWorkflowGrantV4):
    """A GitHub workflow's token permissions, triggers, reusable calls and step references.

    Every job id, trigger and permission scope name is a published label
    (#802): credential-shaped text in it is redacted, one way in every field —
    ``permission_contexts``, ``reusable_calls``, ``step_actions``, ``triggers``,
    and the job and scope names in the ``write_scopes`` and
    ``effective_write_scopes`` entries — and ``config_sha256`` is computed over
    those labels. A single redacted label still compares. When two distinct
    job ids or triggers in the workflow, or two scope names in one
    ``permissions`` mapping that a job's permissions are read from, publish
    alike, the inventory records a blocking coverage issue instead of comparing
    them as one. A top-level mapping no job inherits is read only into
    ``write_scopes``, which is neither compared nor digested.
    """

    reusable_calls: list[HostReusableWorkflowCallV6]
    # Present only when a step declares a listed reference. In a v0.6 grant
    # its absence means the steps were read and declare none; the schema
    # version, not the key, separates that from a legacy grant that never
    # read them.
    step_actions: list[HostWorkflowStepActionV6] = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )


HostGrantV6 = Annotated[
    HostMcpServerGrantV2
    | HostPermissionRuleGrantV2
    | HostPermissionModeGrantV2
    | HostHookGrantV2
    | HostSandboxGrantV2
    | HostAdditionalPathGrantV2
    | HostPluginGrantV2
    | HostProfileGrantV2
    | HostRequirementGrantV2
    | HostWorkflowGrantV6
    | HostInstructionGrantV2,
    Field(discriminator="kind"),
]


class HostGrantsInventoryV6(HostGrantsInventoryV5):
    host_grants_inventory_schema_version: Literal["0.6"] = "0.6"
    grants: list[HostGrantV6] = Field(default_factory=list)


class HostGrantsNormalizedSnapshotV6(HostGrantsNormalizedSnapshotV5):
    grants: list[HostGrantV6] = Field(default_factory=list)


class HostGrantsBaselineV6(HostGrantsBaselineV5):
    host_grants_schema_version: Literal["0.6"] = "0.6"
    inventory: HostGrantsNormalizedSnapshotV6


class HostGrantsDriftV6(HostGrantsDriftV5):
    host_grants_schema_version: Literal["0.6"] = "0.6"


class HostGrantsInventoryArtifactV6(RootModel[HostGrantsInventoryV6]):
    root: HostGrantsInventoryV6


class HostGrantsBaselineArtifactV6(RootModel[HostGrantsBaselineV6]):
    root: HostGrantsBaselineV6


class HostGrantsDriftArtifactV6(RootModel[HostGrantsDriftV6]):
    root: HostGrantsDriftV6


# v0.7 reads how a coding agent is launched inside a job (#823). The v0.6
# workflow grant above stays frozen: a v0.4-v0.6 snapshot never read agent
# launches or checkout refs, so its silence cannot assert that none changed.
class HostWorkflowAgentSettingV7(BaseModel):
    """One permission input or flag an agent launch declares, compared as text (#823).

    ``name`` is the documented input (``claude_args``, ``sandbox``, …) or the
    flag's primary spelling (``--allowedTools`` for ``--allowed-tools`` too).
    ``value`` is the declared text, stripped, as it may be published; a flag
    that takes no value has ``null``. ``claude_args`` is the text the Claude
    actions parse, without the full-line ``#`` comments they drop. What the
    host readers withhold stays withheld: a JSON object (a ``settings`` or
    ``mcp_config`` value, a ``--settings`` or ``--mcp-config`` value, any
    argument word) publishes its key names with ``env`` and ``headers``
    values, ``apiKeyHelper`` and every secret-named value ``<redacted>``, a
    codex ``--config`` override under such a key publishes ``<redacted>``, and
    a URL publishes its scheme and host with ``<redacted-path>`` for its path
    and query (#723). The rest is published through the workflow label
    redaction (#802). A value it rewrites beyond that is credential-shaped: it
    is published redacted with ``unresolved_reason: redacted`` and makes the
    workflow a blocking limit, as a redacted step reference does (#767). A
    value that is not a string (``not_a_string``), or one holding text that
    starts like JSON and does not parse (``unparsed_json``), is
    ``null`` and records a non-blocking coverage issue naming its
    ``job/step``: it is neither published nor compared.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    value: str | None
    unresolved_reason: Literal["not_a_string", "redacted", "unparsed_json"] | None = None


class HostWorkflowAgentRuleV7(BaseModel):
    """One documented widening rule an agent launch meets, and the setting it was read from (#823).

    Decided when the workflow is read, from the declared text, before any of
    it is withheld for publication, so redaction never hides a rule. Only a
    literal value meets one: a value holding ``${{ }}`` meets none.
    ``setting`` is the input (``claude_args``, ``allowed_bots``, ``sandbox``,
    …) or the CLI flag's primary spelling. One rule compares as one whatever
    setting meets it, except ``open_gate``, which is one rule per gate input.
    """

    model_config = ConfigDict(extra="forbid")

    rule: Literal[
        "bypass_permissions",
        "bypass_approvals_and_sandbox",
        "danger_full_access",
        "unsafe_safety_strategy",
        "open_gate",
    ]
    setting: str


class HostWorkflowAgentLaunchV7(BaseModel):
    """A step that launches a known coding agent, read as text and never run (#823).

    ``agent`` is a documented action reference's ``owner/repo`` (the step's
    ``uses:`` at any ref; the Claude base action also as the ``base-action``
    directory of ``anthropics/claude-code-action``) or a known agent CLI a
    literal ``run:`` starts with: ``claude`` with ``-p``/``--print``, or
    ``codex exec``. ``form: read`` lists the documented permission inputs or
    flags the step declares in ``settings``, and the documented widening
    rules they meet in ``widening_rules``, omitted when none.
    ``form: unresolved`` names why the step's settings were not
    read — a ``run:`` holding more than one command or quoting that does not
    balance, a shell expansion, a ``${{ }}`` expression, or ``with:`` that is
    not a mapping — with no
    settings, and records a non-blocking coverage issue. ``job_secrets`` names
    the secrets the step's job references (``${{ secrets.NAME }}``) and the
    workflow-level ``env`` passes: context for the row that names this step,
    never compared. ``job`` and ``step`` are published labels (#802).
    """

    model_config = ConfigDict(extra="forbid")

    job: str
    step: str
    agent: Literal[
        "anthropics/claude-code-action",
        "anthropics/claude-code-base-action",
        "anthropics/claude-code-action/base-action",
        "openai/codex-action",
        "claude",
        "codex",
    ]
    form: Literal["read", "unresolved"]
    unresolved_reason: Literal[
        "compound_command",
        "shell_expansion",
        "expression",
        "inputs_not_a_mapping",
    ] | None = None
    settings: list[HostWorkflowAgentSettingV7] = Field(default_factory=list)
    widening_rules: list[HostWorkflowAgentRuleV7] = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )
    job_secrets: list[str] = Field(default_factory=list, exclude_if=lambda value: not value)


class HostWorkflowCheckoutRefV7(BaseModel):
    """One ``actions/checkout`` step and the ``with.ref`` it declares, as text (#823).

    ``ref`` is ``null`` when the step declares none, or an empty one: the
    checkout's default for the triggering event. A ref the label redaction
    rewrites is published redacted with ``unresolved_reason: redacted`` and
    makes the workflow a blocking limit, as a redacted step reference does
    (#767). A value that is not a string, or ``with:`` that is not a mapping,
    is ``null`` with ``unresolved_reason`` and records a non-blocking coverage
    issue. The ref is never resolved or fetched.
    """

    model_config = ConfigDict(extra="forbid")

    job: str
    step: str
    ref: str | None
    unresolved_reason: Literal["not_a_string", "redacted", "inputs_not_a_mapping"] | None = None


class HostWorkflowGrantV7(HostWorkflowGrantV6):
    """A v0.6 workflow grant plus the agent launches and checkout refs its steps declare.

    Both lists are present only when a step declares one. In a v0.7 grant an
    absent list means the steps were read and declare none; the schema
    version, not the key, separates that from a legacy grant that never read
    them. ``access`` and ``risk`` still describe the workflow's token and
    triggers alone.
    """

    agent_launches: list[HostWorkflowAgentLaunchV7] = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )
    checkout_refs: list[HostWorkflowCheckoutRefV7] = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )


HostGrantV7 = Annotated[
    HostMcpServerGrantV2
    | HostPermissionRuleGrantV2
    | HostPermissionModeGrantV2
    | HostHookGrantV2
    | HostSandboxGrantV2
    | HostAdditionalPathGrantV2
    | HostPluginGrantV2
    | HostProfileGrantV2
    | HostRequirementGrantV2
    | HostWorkflowGrantV7
    | HostInstructionGrantV2,
    Field(discriminator="kind"),
]


class HostGrantsInventoryV7(HostGrantsInventoryV6):
    host_grants_inventory_schema_version: Literal["0.7"] = "0.7"
    grants: list[HostGrantV7] = Field(default_factory=list)


class HostGrantsNormalizedSnapshotV7(HostGrantsNormalizedSnapshotV6):
    grants: list[HostGrantV7] = Field(default_factory=list)


class HostGrantsBaselineV7(HostGrantsBaselineV6):
    host_grants_schema_version: Literal["0.7"] = "0.7"
    inventory: HostGrantsNormalizedSnapshotV7


class HostGrantsDriftV7(HostGrantsDriftV6):
    host_grants_schema_version: Literal["0.7"] = "0.7"


class HostGrantsInventoryArtifactV7(RootModel[HostGrantsInventoryV7]):
    root: HostGrantsInventoryV7


class HostGrantsBaselineArtifactV7(RootModel[HostGrantsBaselineV7]):
    root: HostGrantsBaselineV7


class HostGrantsDriftArtifactV7(RootModel[HostGrantsDriftV7]):
    root: HostGrantsDriftV7


__all__ = [name for name in globals() if name.startswith("Host") or name.startswith("HOST_")]
