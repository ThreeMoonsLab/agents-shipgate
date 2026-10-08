from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from agents_shipgate.schemas.instruction_structure import InstructionStructureEvidence
from agents_shipgate.schemas.openshell import OpenShellPolicyFacts
from agents_shipgate.schemas.openshell_composition import (
    OpenShellComposedPolicyFacts,
    OpenShellSnapshotFactsV2,
)

HOST_GRANTS_INVENTORY_SCHEMA_VERSION = "0.9"
HOST_GRANTS_BASELINE_SCHEMA_VERSION = "0.9"
HOST_GRANTS_DRIFT_SCHEMA_VERSION = "0.9"

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


# v0.7 publishes what changed in a hook and in an MCP server's launch (#819).
# A hook row used to read `PostToolUse → PostToolUse` whether its matcher, its
# command or its timeout changed, and an MCP row could not show a version pin
# moving to `@latest`: the grants carried none of it, and only `config_sha256`
# saw the edit. These members display what `config_sha256` already binds. No
# command or argument text is published: a command is its executable's name
# and a digest, and an MCP server's arguments are a package specification and
# a digest. No comparison and no inventory digest reads them, so a `0.6` grant
# and its `0.7` reading of the same configuration compare as the same grant.
class HostHookCommandV7(BaseModel):
    """A hook command as its grant publishes it: the executable's name and a digest of the whole command.

    ``executable`` is the last path segment of the command's first
    whitespace-separated word, when it is a plain token
    (``[A-Za-z0-9._+-]``, at most 80 characters) no redaction rule rewrites
    and the word is no shell reserved word and holds no ``://``, and
    ``<not-shown>`` otherwise, so no part of a URL is named. It
    is a label, not a claim about what a host runs. ``sha256`` is the digest of the whole command as
    ``config_sha256``'s input holds it, so it moves only when that digest
    does; a value that input redacts moves neither. The command's text is
    never published.
    """

    model_config = ConfigDict(extra="forbid")

    executable: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class HostHookHandlerV7(BaseModel):
    """One hook handler under an event: its group's matcher, its command and its timeout.

    ``matcher`` is ``None`` when its group declares none, which the host reads
    as every tool or source, and ``<not-shown>`` when it is not a string or is
    longer than 1,024 characters as ``config_sha256``'s input holds it;
    otherwise it passes through the published-label redaction and is cut at
    120 characters. ``command`` is ``None`` for a handler with
    no command string, such as a ``prompt`` handler, whose prompt is not
    published. ``timeout`` is the declared number or boolean; an integer of
    more than 80 digits is published as its digits cut with ``…``, a string
    as written when it is a plain token, and any other value, a non-finite
    float among them, as ``<not-shown>``. Other handler settings are not
    published; a change confined to them is a row whose text says it is not
    shown.
    """

    model_config = ConfigDict(extra="forbid")

    matcher: str | None = None
    command: HostHookCommandV7 | None = None
    # `bool` first: pydantic's lax `int` would otherwise read `true` as `1`.
    timeout: bool | int | float | str | None = None
    # #826: literal declaration facts only; false is not proof of no approval.
    # Present only on a Claude Code PreToolUse handler, the one the reader
    # examines: absent means not examined, never "read, no allow".
    inline_allow: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    decision_limit: Literal["script_or_command_behavior_not_read"] | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )


#: Why a hook handler's script bytes were not established (#702): the
#: reference grammar's reasons (``core.hook_script_reference.ReferenceLimit``),
#: the event's shape and handler bound, an unselected hook, and the byte
#: reader's limits on an established path.
HostHookScriptLimitV7 = Literal[
    "unsupported_host",
    "not_command_handler",
    "unsupported_command_shape",
    "platform_command_override",
    "unsupported_shell",
    "unsupported_exec_form",
    "unsupported_shell_command",
    "dynamic_command_argument",
    "unexpanded_path_placeholder",
    "unsupported_path_placeholder",
    "plugin_root_not_established",
    "unsupported_or_escaping_path",
    "dynamic_or_conditional_path",
    "working_directory_not_established",
    "interpreter_wrapper",
    "path_lookup",
    "external_executable",
    "unsupported_hook_shape",
    "handler_bound_exceeded",
    "hook_selection_not_established",
    "redacted_dependency_path",
    "escaping_path",
    "missing_input",
    "symlink_input",
    "non_regular_input",
    "oversized_input",
    "unsafe_or_unreadable_input",
    "unreadable_input",
]


class HostHookScriptInputV7(BaseModel):
    """A direct executable reference and its byte reading, never script semantics."""

    model_config = ConfigDict(extra="forbid")

    handler: int = Field(ge=0)
    path: str | None = None
    basis: Literal["project_root_placeholder", "plugin_root_placeholder", "absolute_workspace_path"] | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    size_bytes: int | None = Field(default=None, ge=0)
    limit: HostHookScriptLimitV7 | None = None


class HostHookComparisonV7(HostHookGrantV2):
    # None is historical absence, not evidence that no dependency was selected.
    script_inputs: list[HostHookScriptInputV7] | None = Field(default=None, exclude_if=lambda value: value is None)


class HostHookGrantV7(HostHookComparisonV7):
    #: Every handler the event declares, in file order, at most a bounded
    #: number; ``omitted_handlers`` counts the rest. ``None`` when the event's
    #: value is not a list of matcher groups each holding a ``hooks`` list of
    #: objects whose ``command``, when present, is a string, the shape this
    #: reader establishes: the detail is then not shown rather than guessed. Always present in a ``0.7`` inventory grant,
    #: so its absence marks a grant a saved baseline holds or an earlier
    #: schema read.
    handlers: list[HostHookHandlerV7] | None
    omitted_handlers: int = Field(default=0, ge=0)


class HostMcpLaunchSourceV7(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pin: Literal["pinned", "mutable"]
    package: str | None = Field(default=None, max_length=200)


class HostMcpServerGrantV7(HostMcpServerGrantV2):
    #: The one argument published as written: the first that is a package
    #: specification of a strict shape (npm ``name@version`` or
    #: ``@scope/name@version``, PyPI ``name==version``, or an OCI image
    #: reference with a path and a tag or digest), that no redaction rule
    #: rewrites and that follows no flag but a package runner's own (``-y``,
    #: ``--from``, ``--rm`` …). ``None`` when no argument is one.
    package: str | None
    #: The digest of the declared ``args`` as ``config_sha256``'s input holds
    #: them, the package replaced by a marker and its position digested beside
    #: them, so every other argument is compared and none is published.
    #: ``None`` when no ``args`` is declared.
    #: Both members are always present in a ``0.7`` inventory grant; a saved
    #: baseline holds neither.
    args_sha256: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    #: Optional display fact from bounded, redacted launcher arguments (#825).
    #: Omitted from saved baselines and grant/digest equality, like package.
    launch_source: HostMcpLaunchSourceV7 | None = None



# v0.7 reads how a coding agent is launched inside a job (#823). The v0.6
# workflow grant above stays frozen: a v0.4-v0.6 snapshot never read agent
# launches or checkout refs, so its silence cannot assert that none changed.
class HostWorkflowAgentSettingV7(BaseModel):
    """One permission input or flag an agent launch declares, compared as text (#823).

    ``name`` is the documented input (``claude_args``, ``sandbox``, …) or the
    flag's primary spelling (``--allowedTools`` for ``--allowed-tools`` too).
    ``value`` is the declared text, stripped, as it may be published; a flag
    that takes no value has ``null``.

    ``claude_args`` and ``codex-args`` are read only when they are a plain
    list of words: letters, digits and ``_ . / : = , % + - ( )``, separated by
    blanks or newlines, with no ``--settings`` or ``--mcp-config`` flag. Every
    parser involved splits such text the same way, so it is published as
    those words, one space apart. Any other value — holding a quote, a
    ``${{ }}`` expression, ``$``, a backtick, a comment, a shell operator,
    JSON or another character — is ``unread_arguments``: ``value`` is
    ``<withheld:…>``, a short digest, so an edit to it is still a change
    while none of its text is published; no documented widening rule is read
    from it; and it records a non-blocking coverage issue naming its
    ``job/step`` (#823 review cycle 4). A codex ``--config`` override keeps
    its key; its value is ``<redacted>`` under ``env``, ``headers`` or a
    secret-named key, as the host readers redact such values, published as
    written for ``sandbox_mode``, ``default_permissions``,
    ``approval_policy`` and ``model``, and ``<withheld:…>`` otherwise.

    Every other input is one value. A JSON object (a ``settings`` or
    ``mcp_config`` value) publishes its shape and none of its free text: key
    names, numbers, booleans and ``null``, with each string replaced by
    ``<withheld:…>``, a short digest of what the host readers digest for it,
    so an edit to it is still a change. ``env`` and ``headers`` values,
    ``apiKeyHelper`` and every secret-named value are ``<redacted>``, as the
    host readers redact them. The strings a host reader publishes are kept:
    a ``permissions.allow``/``ask``/``deny`` rule and a documented Claude
    Code setting's value such as ``defaultMode``, and an MCP server's command
    name and its URL's scheme and host, each followed by the digest when it
    drops something the digest reads (a command's arguments, a URL's query).
    So an MCP server's arguments and a hook's command publish nothing, as
    `.mcp.json` and `.claude/settings.json` do not (#823 review). A
    ``settings`` or ``mcp_config`` value that neither starts like a JSON
    object nor is a plain file path (path characters, and a ``${{ }}``
    expression only as a plain context reference) is ``<withheld:…>``, a
    digest and none of its text (#823 review cycle 5). A URL in
    other text publishes its scheme and host with ``<redacted-path>`` for its
    path and query (#723). Other text — a prompt, a flag's value — is
    published through the workflow label redaction (#802). A value it
    rewrites is credential-shaped — a token, but also prose such as "never
    print bearer tokens" — and is published redacted with
    ``unresolved_reason: redacted``: it is compared as published, beside the
    rules read from its declared text, and records a non-blocking coverage
    issue naming its ``job/step``, because an edit inside what is redacted is
    not reported. A value that is not a string (``not_a_string``), or one
    holding text that starts like JSON and does not parse (``unparsed_json``),
    is ``null`` and records a non-blocking coverage issue naming its
    ``job/step``: it is neither published nor compared.

    ``holds_expression`` is ``true`` when an input other than an argument
    input holds a ``${{ }}`` expression, which GitHub substitutes before the
    action reads the input, and is omitted otherwise. A documented widening
    rule is then read only from the entries of a user gate that hold none,
    and from no mode or settings input, and a rule the launch gains in the
    same job afterwards is not claimed, because the substituted text may
    already have met it.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    value: str | None
    unresolved_reason: Literal[
        "not_a_string", "redacted", "unparsed_json", "unread_arguments",
    ] | None = None
    holds_expression: bool = Field(default=False, exclude_if=lambda value: not value)


class HostWorkflowAgentRuleV7(BaseModel):
    """One documented widening rule an agent launch meets, and the setting it was read from (#823).

    Decided when the workflow is read, from the declared text, before any of
    it is withheld for publication, so redaction never hides a rule. Only
    text this reader reads exactly meets one: ``claude_args`` or
    ``codex-args`` only when it is a plain list of words (never when it holds
    a ``${{ }}`` expression), the entries of a user gate that hold no
    expression, and a mode or ``settings`` input that holds none. Claude Code
    settings written as JSON in the ``settings`` input meet
    ``bypass_permissions`` when their ``defaultMode`` is
    ``bypassPermissions``, read as the settings reader reads it; a path to a
    settings file is not read. ``setting`` is the input (``claude_args``,
    ``allowed_bots``, ``sandbox``, ``permission-profile``, …) or the CLI
    flag's primary spelling. One rule compares as one whatever setting meets
    it, except ``open_gate``, which is one rule per gate input.
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
    directory of ``anthropics/claude-code-action``), or a known agent CLI a
    ``run:`` launches when the whole ``run:`` is one line of plain words
    (letters, digits and ``_ . / : = , % + -``, separated by spaces or tabs),
    run by ``bash``, ``sh`` or the runner's default shell, whose program,
    after any ``NAME=value`` assignments, has the file name ``claude`` and
    passes ``-p``/``--print``, or ``codex`` followed by ``exec`` (``e``).
    ``form: read`` lists the documented permission inputs or flags the step
    declares in ``settings``, and the documented widening rules they meet in
    ``widening_rules``, omitted when none. ``form: unresolved`` is an agent
    action whose ``with:`` is not a mapping (``inputs_not_a_mapping``), with
    no settings, and records a non-blocking coverage issue. Any other
    ``run:`` that mentions an agent CLI is not a launch: it is listed in
    ``unread_agent_runs``. ``job_secrets`` names the secrets the step's job
    references (``${{ secrets.NAME }}``) and the workflow-level ``env``
    passes: context for the row that names this step, never compared.
    ``job`` and ``step`` are published labels (#802).
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
    unresolved_reason: Literal["inputs_not_a_mapping"] | None = None
    settings: list[HostWorkflowAgentSettingV7] = Field(default_factory=list)
    widening_rules: list[HostWorkflowAgentRuleV7] = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )
    job_secrets: list[str] = Field(default_factory=list, exclude_if=lambda value: not value)


class HostWorkflowUnreadAgentRunV7(BaseModel):
    """A ``run:`` step that mentions a known agent CLI and is not read as an agent launch (#823 review cycle 4).

    Any ``run:`` holding ``claude`` or ``codex`` as a word of its own that is
    not an agent launch this reader reads — more than one line or command, a
    quote, an expansion, a redirection, a comment, a continuation, a
    ``${{ }}`` expression, another program such as ``npx`` or ``timeout``, a
    subcommand that is not a headless launch, or a declared ``shell:`` other
    than ``bash`` or ``sh`` run on the script alone (so ``bash -c '…' {0}``
    too) — once for each agent CLI it mentions. It is a
    named, non-blocking limit and nothing more: none of the step's text is
    published, it is never compared, so adding, removing or editing it gives
    no row, and it never says that the step starts, or does not start, an
    agent. ``job`` and ``step`` are published labels (#802).
    """

    model_config = ConfigDict(extra="forbid")

    job: str
    step: str
    agent: Literal["claude", "codex"]


class HostWorkflowCheckoutRefV7(BaseModel):
    """One ``actions/checkout`` step and the ``with.ref`` it declares, as text (#823).

    ``ref`` is ``null`` when the step declares none, or an empty one: the
    checkout's default for the triggering event. A ref the label redaction
    rewrites is published redacted with ``unresolved_reason: redacted`` and
    makes the workflow a blocking limit, as a redacted step reference does
    (#767): a ref names the code the job runs, as a step reference does. A
    value that is not a string, or ``with:`` that is not a mapping,
    is ``null`` with ``unresolved_reason`` and records a non-blocking coverage
    issue. The ref is never resolved or fetched.
    """

    model_config = ConfigDict(extra="forbid")

    job: str
    step: str
    ref: str | None
    unresolved_reason: Literal["not_a_string", "redacted", "inputs_not_a_mapping"] | None = None


class HostWorkflowGrantV7(HostWorkflowGrantV6):
    """A v0.6 workflow grant plus the agent launches, unread agent steps and checkout refs its steps declare.

    Each list is present only when a step declares one. In a v0.7 grant an
    absent list means the steps were read and declare none; the schema
    version, not the key, separates that from a legacy grant that never read
    them. ``unread_agent_runs`` is a named limit and is never compared.
    ``access`` and ``risk`` still describe the workflow's token and triggers
    alone.
    """

    agent_launches: list[HostWorkflowAgentLaunchV7] = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )
    unread_agent_runs: list[HostWorkflowUnreadAgentRunV7] = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )
    checkout_refs: list[HostWorkflowCheckoutRefV7] = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )


HostGrantV7 = Annotated[
    HostMcpServerGrantV7
    | HostPermissionRuleGrantV2
    | HostPermissionModeGrantV2
    | HostHookGrantV7
    | HostSandboxGrantV2
    | HostAdditionalPathGrantV2
    | HostPluginGrantV2
    | HostProfileGrantV2
    | HostRequirementGrantV2
    | HostWorkflowGrantV7
    | HostInstructionGrantV2,
    Field(discriminator="kind"),
]


HostBaselineGrantV7 = Annotated[
    HostMcpServerGrantV2
    | HostPermissionRuleGrantV2
    | HostPermissionModeGrantV2
    | HostHookComparisonV7
    | HostSandboxGrantV2
    | HostAdditionalPathGrantV2
    | HostPluginGrantV2
    | HostProfileGrantV2
    | HostRequirementGrantV2
    | HostWorkflowGrantV7
    | HostInstructionGrantV2,
    Field(discriminator="kind"),
]


class HostArtifactV7(HostArtifactV4):
    kind: Literal["config", "mcp", "hooks", "workflow", "instructions", "requirements", "hook_script"]


class HostArtifactChangeV7(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str
    baseline: HostArtifactV7 | None = None
    current: HostArtifactV7 | None = None


class HostGrantsInventoryV7(HostGrantsInventoryV6):
    host_grants_inventory_schema_version: Literal["0.7"] = "0.7"
    grants: list[HostGrantV7] = Field(default_factory=list)
    artifacts: list[HostArtifactV7] = Field(default_factory=list)


class HostGrantsNormalizedSnapshotV7(HostGrantsNormalizedSnapshotV6):
    # Saved baselines keep workflow evidence but omit display-only hook/MCP fields.
    grants: list[HostBaselineGrantV7] = Field(default_factory=list)
    artifacts: list[HostArtifactV7] = Field(default_factory=list)


class HostGrantsBaselineV7(HostGrantsBaselineV6):
    host_grants_schema_version: Literal["0.7"] = "0.7"
    inventory: HostGrantsNormalizedSnapshotV7


class HostGrantsDriftV7(HostGrantsDriftV6):
    host_grants_schema_version: Literal["0.7"] = "0.7"
    artifact_changes: list[HostArtifactChangeV7] = Field(default_factory=list)


class HostGrantsInventoryArtifactV7(RootModel[HostGrantsInventoryV7]):
    root: HostGrantsInventoryV7


class HostGrantsBaselineArtifactV7(RootModel[HostGrantsBaselineV7]):
    root: HostGrantsBaselineV7


class HostGrantsDriftArtifactV7(RootModel[HostGrantsDriftV7]):
    root: HostGrantsDriftV7


HostNameV8 = Literal["codex", "claude-code", "cursor", "vscode", "github", "openshell"]


class HostOpenShellPolicyGrantV8(HostGrantBaseV2):
    host: Literal["openshell"] = "openshell"
    kind: Literal["openshell_policy"] = "openshell_policy"
    facts: OpenShellPolicyFacts


HostGrantV8 = Annotated[HostGrantV7 | HostOpenShellPolicyGrantV8, Field(discriminator="kind")]
HostBaselineGrantV8 = Annotated[
    HostBaselineGrantV7 | HostOpenShellPolicyGrantV8, Field(discriminator="kind")
]


class HostArtifactV8(HostArtifactV7):
    host: HostNameV8
    kind: Literal[
        "config", "mcp", "hooks", "workflow", "instructions", "requirements", "hook_script",
        "openshell_selection", "openshell_policy",
    ]


class HostCoverageV8(HostCoverageV2):
    host: HostNameV8


class HostInventoryIssueV8(HostInventoryIssueV2):
    host: HostNameV8


class HostCoverageChangeV8(HostCoverageChangeV2):
    host: HostNameV8


class HostArtifactChangeV8(HostArtifactChangeV7):
    baseline: HostArtifactV8 | None = None
    current: HostArtifactV8 | None = None


class HostGrantsInventoryV8(HostGrantsInventoryV7):
    host_grants_inventory_schema_version: Literal["0.8"] = "0.8"
    grants: list[HostGrantV8] = Field(default_factory=list)
    artifacts: list[HostArtifactV8] = Field(default_factory=list)
    host_coverage: list[HostCoverageV8] = Field(default_factory=list)
    issues: list[HostInventoryIssueV8] = Field(default_factory=list)


class HostGrantsNormalizedSnapshotV8(HostGrantsNormalizedSnapshotV7):
    grants: list[HostBaselineGrantV8] = Field(default_factory=list)
    artifacts: list[HostArtifactV8] = Field(default_factory=list)
    host_coverage: list[HostCoverageV8] = Field(default_factory=list)


class HostGrantsBaselineV8(HostGrantsBaselineV7):
    host_grants_schema_version: Literal["0.8"] = "0.8"
    inventory: HostGrantsNormalizedSnapshotV8


class HostGrantsDriftV8(HostGrantsDriftV7):
    host_grants_schema_version: Literal["0.8"] = "0.8"
    artifact_changes: list[HostArtifactChangeV8] = Field(default_factory=list)
    coverage_changes: list[HostCoverageChangeV8] = Field(default_factory=list)
    issues: list[HostInventoryIssueV8] = Field(default_factory=list)


class HostGrantsInventoryArtifactV8(RootModel[HostGrantsInventoryV8]):
    root: HostGrantsInventoryV8


class HostGrantsBaselineArtifactV8(RootModel[HostGrantsBaselineV8]):
    root: HostGrantsBaselineV8


class HostGrantsDriftArtifactV8(RootModel[HostGrantsDriftV8]):
    root: HostGrantsDriftV8


class HostOpenShellPolicyGrantV9(HostOpenShellPolicyGrantV8):
    facts: OpenShellPolicyFacts | OpenShellComposedPolicyFacts | OpenShellSnapshotFactsV2


class HostPermissionRuleGrantV9(HostPermissionRuleGrantV2):
    """A permission rule plus, for a Claude Code ``!`` deny or ask rule, what it follows (#974).

    Claude Code reads a deny or ask pattern that starts with ``!`` as a
    carve-out from the relative-path rules of the same tool listed before it in
    the same source (https://code.claude.com/docs/en/permissions#read-and-edit).
    ``carves_from`` is those earlier rules, sorted: present (possibly empty) on
    every such rule and absent on every other, so a grant that lacks it was read
    by a version that did not record its position. Whether the patterns
    overlap is not decided.
    """

    carves_from: list[str] | None = Field(default=None, exclude_if=lambda value: value is None)
# v0.9 reads a same-repository reusable workflow's own restrictions (#921).
# Both members are present only on a call whose target is a workflow in the
# same repository and whose job declares a `write` scope, so every other call
# keeps its v0.7 shape and digest.
class HostReusableWorkflowCallV9(HostReusableWorkflowCallV6):
    """One job's reusable-workflow call; its job's ``permissions`` are a ceiling (#921).

    ``callee_permissions`` says whether the called workflow's own declarations
    were read: ``read``, or why not — ``not_read`` (not among the workflows
    read on this side, or a same-repository spelling GitHub does not run),
    ``limited`` (a blocking limit on that file), ``cycle`` or ``too_deep``
    (past GitHub's ten levels of workflows, or this audit's bound on how many
    it follows). ``callee_write_scopes`` is present only when ``read``: the
    ceiling's write scopes some called job may hold (``*`` for all); the
    caller's ``effective_write_scopes`` for the job hold exactly those. When
    the called workflow was not read the ceiling is assumed to reach it whole.
    """

    callee_permissions: Literal["read", "not_read", "limited", "cycle", "too_deep"] | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    callee_write_scopes: list[str] | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )


class HostWorkflowGrantV9(HostWorkflowGrantV7):
    """A v0.7 workflow grant whose same-repository reusable calls read their callee (#921).

    ``access`` describes the job tokens alone: ``pull_request_target`` raises
    ``risk``, and makes ``access`` ``write`` only where a job declares no
    permissions (#920).
    """

    reusable_calls: list[HostReusableWorkflowCallV9]


#: The hosts a ``0.9`` inventory publishes a source under (#936): each host
#: this entry reads, and ``unknown`` for an MCP configuration no declaration
#: this entry reads selects while another host's plugin manifest sits beside
#: it. ``unknown`` names no host: it says which host loads the servers is not
#: established, so they are not Claude Code's by the file name. Extended in
#: place: ``0.9`` has not shipped in a tagged release.
HostNameV9 = Literal["codex", "claude-code", "cursor", "vscode", "github", "openshell", "unknown"]
McpHostNameV9 = Literal["codex", "claude-code", "cursor", "vscode", "github", "unknown"]


class HostMcpLaunchSourceV9(HostMcpLaunchSourceV7):
    """#825's pin, and where the launcher finds the source when the declaration leaves it open (#933).

    ``resolution`` is ``local_project_or_registry`` for an ``npx`` package
    named with no version specifier: npm runs the local project's dependency
    of that name when there is one and otherwise installs it from the
    registry into its cache, and the declaration does not establish which. It
    is absent for every other source. ``pin`` keeps #825's meaning, a fact of
    the declaration. Display-only, like the rest of ``launch_source``.
    Extended in place: ``0.9`` has not shipped in a tagged release.
    """

    resolution: Literal["local_project_or_registry"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class HostMcpServerGrantV9(HostMcpServerGrantV7):
    """An MCP server, published under the host whose declaration selects its file (#936)."""

    host: McpHostNameV9
    launch_source: HostMcpLaunchSourceV9 | None = None


class HostMcpServerBaselineGrantV9(HostMcpServerGrantV2):
    host: McpHostNameV9


class HostInventoryIssueV9(HostInventoryIssueV8):
    host: HostNameV9


class HostHookCommandShapeV9(BaseModel):
    """What a hook's inline command is made of, without any of its text (#934).

    Read by a bounded, static reading of the command as ``config_sha256``'s
    input holds it, never run. ``commands`` lists, in the order they are
    written, the first 12 distinct programs named at a command position, each
    a plain token (``[A-Za-z0-9._+-]``, at most 80 characters) no redaction
    rule rewrites, the last ``/`` segment of the word; ``commands_more`` counts
    the distinct programs past those, and ``unnamed`` the command positions
    whose word is not such a token (a variable, a substitution, a quoted or
    glob word), both left out when 0. ``statements`` counts the simple
    commands, ``pipes`` the ``|`` and ``|&`` operators, ``substitutions`` the
    ``$( … )`` command substitutions, ``control_flow`` the ``if``, ``elif``,
    ``while``, ``until`` and ``for`` keywords and ``quoted`` the quoted
    strings, each across the whole command. ``redirects`` lists, in order, at
    most 8 redirects to a file as ``> target``, ``>> target`` or ``< target``,
    the target only when it is ``/dev/null`` or a repository-relative path of
    letters, digits, ``.``, ``_`` and ``-`` with no ``..`` segment that no
    redaction rule rewrites, ``<not-shown>`` otherwise; ``redirects_more``
    counts those past them. ``script`` is a script path published as a
    handler's ``args`` publish one: a command word shaped as a relative script
    path, or the first such argument of an interpreter. A command that runs
    ``bash -c '…'`` with a literal script is read inside that script too.

    No argument, quoted string, variable or absolute path is published. It
    describes the command's structure and claims nothing about what it does,
    whether a host runs it, or in which direction a change moves authority.
    """

    model_config = ConfigDict(extra="forbid")

    commands: list[str]
    commands_more: int | None = Field(default=None, exclude_if=lambda value: value is None)
    unnamed: int | None = Field(default=None, exclude_if=lambda value: value is None)
    statements: int
    pipes: int
    substitutions: int
    control_flow: int
    quoted: int
    redirects: list[str] | None = Field(default=None, exclude_if=lambda value: value is None)
    redirects_more: int | None = Field(default=None, exclude_if=lambda value: value is None)
    script: str | None = Field(default=None, max_length=200, exclude_if=lambda value: value is None)


class HostHookCommandV9(HostHookCommandV7):
    """A hook command plus the shape of the inline command, or why it has none (#934).

    Exactly one of ``shape`` and ``shape_limit`` is present. ``shape_limit``
    says why the command is not described and the digest is all there is to
    compare: ``too_long`` (more than 8,192 characters as ``config_sha256``'s
    input holds it), ``unsupported_shell`` (the handler's ``shell`` setting is
    neither ``bash`` nor ``sh``) or ``unsupported_syntax`` (a form the bounded
    reading refuses whole: a here-document, a backquote, ``$(( … ))``,
    ``case``, a function definition, an unterminated quote or group, or a
    command past its word or nesting bound). A row then says so and that the
    config has to be opened to read the change. Left out of grant equality,
    the inventory digests and saved baselines, like the rest of ``handlers``.
    """

    shape: HostHookCommandShapeV9 | None = Field(default=None, exclude_if=lambda value: value is None)
    shape_limit: Literal["too_long", "unsupported_shell", "unsupported_syntax"] | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )


class HostHookArgsV9(BaseModel):
    """A hook handler's ``args``, without their text (#972).

    ``script`` is the first argument shaped as a relative script path
    (segments of letters, digits, ``.``, ``_`` and ``-``, optionally led by
    ``./``, ``${CLAUDE_PROJECT_DIR}/`` or ``${CLAUDE_PLUGIN_ROOT}/``, ending
    in a script extension such as ``.py``, ``.sh`` or ``.mjs``) when no
    redaction rule rewrites it, alone or after the three arguments before it,
    and ``None`` otherwise. It is a label, not a claim about what runs.
    ``sha256`` is the digest of the declared ``args`` as ``config_sha256``'s
    input holds them, with the published script replaced by a marker and its
    position digested beside them, as an MCP server's ``args_sha256`` is, so
    it moves only when that digest does. No other argument text is published.
    """

    model_config = ConfigDict(extra="forbid")

    script: str | None = Field(max_length=200)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


#: A published hook handler setting: as a timeout is published (#971, #972).
HookSettingValueV9 = bool | int | float | str | None


class HostHookHandlerV9(HostHookHandlerV7):
    """A hook handler plus its ``args``, its documented settings and, on a Claude Code tool event, its matcher's reach.

    ``command`` also carries the shape of an inline command, or why it has
    none (:class:`HostHookCommandV9`, #934). ``args`` is present only on a handler that declares them
    (:class:`HostHookArgsV9`). ``type``, ``async``, ``asyncRewake``,
    ``shell`` and ``once``, the documented boolean and enumerated handler
    settings (https://code.claude.com/docs/en/hooks#common-fields), are each
    present only when the handler declares a value other than ``null``, and
    published as a timeout is: a boolean or finite number as declared, a
    string as written when it is a plain token, and anything else as
    ``<not-shown>`` (#971, #972). Other handler settings, such as ``if``,
    ``statusMessage``, ``prompt``, ``model``, ``url`` and ``headers``, are not
    published; a change confined to them is a row whose text says it is not
    shown.

    ``matcher_reach`` is present on every handler of a Claude Code
    ``PreToolUse``, ``PostToolUse``, ``PostToolUseFailure``,
    ``PermissionRequest`` or ``PermissionDenied`` hook, the events whose
    matcher filters the tool name
    (https://code.claude.com/docs/en/hooks#matcher-patterns), and absent on
    every other, so absence means not examined. ``no_tool_name``: every string
    the declared matcher can match holds a character no built-in or MCP tool
    name holds, so the handler runs for no tool call, as with ``Bash(git
    push*)``, which Claude Code reads as a regular expression over the tool
    name. ``possible``: any other matcher, including one this reader does not
    decide. Read from the declared matcher, not the published one, and left
    out of grant equality, the inventory digests and saved baselines like the
    rest of ``handlers``.
    """

    # `async` is a Python keyword: the field is published under its
    # documented name.
    model_config = ConfigDict(extra="forbid", serialize_by_alias=True)

    command: HostHookCommandV9 | None = None
    matcher_reach: Literal["possible", "no_tool_name"] | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    args: HostHookArgsV9 | None = Field(default=None, exclude_if=lambda value: value is None)
    # `bool` first in each: pydantic's lax `int` would otherwise read `true` as `1`.
    type: HookSettingValueV9 = Field(default=None, exclude_if=lambda value: value is None)
    async_: HookSettingValueV9 = Field(
        default=None, alias="async", exclude_if=lambda value: value is None,
    )
    asyncRewake: HookSettingValueV9 = Field(default=None, exclude_if=lambda value: value is None)
    shell: HookSettingValueV9 = Field(default=None, exclude_if=lambda value: value is None)
    once: HookSettingValueV9 = Field(default=None, exclude_if=lambda value: value is None)


class HostHookGrantV9(HostHookGrantV7):
    handlers: list[HostHookHandlerV9] | None


HostGrantV9 = Annotated[
    HostMcpServerGrantV9
    | HostPermissionRuleGrantV9
    | HostPermissionModeGrantV2
    | HostHookGrantV9
    | HostSandboxGrantV2
    | HostAdditionalPathGrantV2
    | HostPluginGrantV2
    | HostProfileGrantV2
    | HostRequirementGrantV2
    | HostWorkflowGrantV9
    | HostInstructionGrantV2
    | HostOpenShellPolicyGrantV9,
    Field(discriminator="kind"),
]
HostBaselineGrantV9 = Annotated[
    HostMcpServerBaselineGrantV9
    | HostPermissionRuleGrantV9
    | HostPermissionModeGrantV2
    | HostHookComparisonV7
    | HostSandboxGrantV2
    | HostAdditionalPathGrantV2
    | HostPluginGrantV2
    | HostProfileGrantV2
    | HostRequirementGrantV2
    | HostWorkflowGrantV9
    | HostInstructionGrantV2
    | HostOpenShellPolicyGrantV9,
    Field(discriminator="kind"),
]


class HostArtifactV9(HostArtifactV8):
    host: HostNameV9
    kind: Literal["config", "mcp", "hooks", "workflow", "instructions", "requirements", "hook_script",
                  "openshell_selection", "openshell_policy", "openshell_profile"]

    @model_validator(mode="after")
    def unknown_host_is_mcp_only(self):
        if self.host == "unknown" and self.kind != "mcp":
            raise ValueError("only an MCP configuration is published with host unknown (#936)")
        return self


class HostArtifactChangeV9(HostArtifactChangeV8):
    baseline: HostArtifactV9 | None = None
    current: HostArtifactV9 | None = None


class HostGrantsInventoryV9(HostGrantsInventoryV8):
    host_grants_inventory_schema_version: Literal["0.9"] = "0.9"
    grants: list[HostGrantV9] = Field(default_factory=list)
    artifacts: list[HostArtifactV9] = Field(default_factory=list)
    issues: list[HostInventoryIssueV9] = Field(default_factory=list)


class HostGrantsNormalizedSnapshotV9(HostGrantsNormalizedSnapshotV8):
    grants: list[HostBaselineGrantV9] = Field(default_factory=list)
    artifacts: list[HostArtifactV9] = Field(default_factory=list)


class HostGrantsBaselineV9(HostGrantsBaselineV8):
    host_grants_schema_version: Literal["0.9"] = "0.9"
    inventory: HostGrantsNormalizedSnapshotV9


class HostGrantsDriftV9(HostGrantsDriftV8):
    host_grants_schema_version: Literal["0.9"] = "0.9"
    artifact_changes: list[HostArtifactChangeV9] = Field(default_factory=list)
    issues: list[HostInventoryIssueV9] = Field(default_factory=list)


class HostGrantsInventoryArtifactV9(RootModel[HostGrantsInventoryV9]):
    root: HostGrantsInventoryV9


class HostGrantsBaselineArtifactV9(RootModel[HostGrantsBaselineV9]):
    root: HostGrantsBaselineV9


class HostGrantsDriftArtifactV9(RootModel[HostGrantsDriftV9]):
    root: HostGrantsDriftV9


__all__ = [name for name in globals() if name.startswith("Host") or name.startswith("HOST_")]
