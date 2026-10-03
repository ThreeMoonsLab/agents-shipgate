"""Static OpenShell policy facts, pinned independently of Shipgate schemas.

These are document facts, never deployed bindings or tool effect declarations.
The authored schema follows NVIDIA/OpenShell v0.1.2 policy schema 1.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OpenShellObject(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class OpenShellPolicyReference(OpenShellObject):
    path: str
    role: Literal["authored", "effective_snapshot"]

    @model_validator(mode="after")
    def local_path(self) -> OpenShellPolicyReference:
        path = PurePosixPath(self.path)
        if (
            not self.path or path.is_absolute() or ".." in path.parts
            or "\\" in self.path or ":" in self.path
            or path.as_posix() != self.path or self.path == "."
        ):
            raise ValueError("policy path must be a normalized repository-relative path")
        return self


class OpenShellSelection(OpenShellObject):
    version: Literal[1]
    runtime_version: Literal["0.1.2"]
    policies: list[OpenShellPolicyReference] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def distinct_paths(self) -> OpenShellSelection:
        paths = [item.path for item in self.policies]
        if len(paths) != len(set(paths)):
            raise ValueError("each policy must be selected once")
        return self


class OpenShellFilesystem(OpenShellObject):
    include_workdir: bool = False
    read_only: list[str] = Field(default_factory=list)
    read_write: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def bounded_paths(self) -> OpenShellFilesystem:
        if len(self.read_only) + len(self.read_write) > 256:
            raise ValueError("too many filesystem paths")
        for path in (*self.read_only, *self.read_write):
            if not path.startswith("/") or ".." in path.split("/") or len(path.encode()) > 4096:
                raise ValueError("invalid filesystem path")
        if "/" in self.read_write:
            raise ValueError("root cannot be writable")
        return self


class OpenShellLandlock(OpenShellObject):
    compatibility: Literal["best_effort", "hard_requirement"] = "best_effort"


class OpenShellProcess(OpenShellObject):
    run_as_user: str = ""
    run_as_group: str = ""

    @model_validator(mode="after")
    def non_root_identity(self) -> OpenShellProcess:
        for value in (self.run_as_user, self.run_as_group):
            if value not in {"", "sandbox"} and not (
                value.isascii() and value.isdecimal() and 1 <= int(value) <= 4294967294
            ):
                raise ValueError("invalid process identity")
        return self


class OpenShellAnyMatcher(OpenShellObject):
    any: list[str] = Field(min_length=1)


OpenShellMatcher = str | OpenShellAnyMatcher


class OpenShellRequestMatcher(OpenShellObject):
    method: str = ""
    path: str = ""
    command: str = ""
    query: dict[str, OpenShellMatcher] = Field(default_factory=dict)
    operation_type: str = ""
    operation_name: str = ""
    fields: list[str] = Field(default_factory=list)
    tool: OpenShellMatcher | None = None
    params: dict[str, OpenShellMatcher] = Field(default_factory=dict)


class OpenShellAllowRule(OpenShellObject):
    allow: OpenShellRequestMatcher


class OpenShellCredentialBinding(OpenShellObject):
    provider: str


class OpenShellMcpOptions(OpenShellObject):
    versions: list[str] = Field(default_factory=lambda: ["2025-11-25"])
    max_body_bytes: int = Field(default=65536, ge=0, le=4294967295)
    strict_tool_names: bool = True
    allow_all_known_mcp_methods: bool = False

    @model_validator(mode="after")
    def supported_versions(self) -> OpenShellMcpOptions:
        if not self.versions or len(set(self.versions)) != len(self.versions) or not set(
            self.versions
        ) <= {"2025-03-26", "2025-06-18", "2025-11-25"}:
            raise ValueError("unsupported MCP versions")
        return self


class OpenShellJsonRpcOptions(OpenShellObject):
    max_body_bytes: int = Field(default=65536, ge=0, le=4294967295)


class OpenShellGraphqlOperation(OpenShellObject):
    operation_type: str = ""
    operation_name: str = ""
    fields: list[str] = Field(default_factory=list)


class OpenShellEndpoint(OpenShellObject):
    host: str = ""
    port: int = Field(default=0, ge=0, le=65535)
    ports: list[int] = Field(default_factory=list)
    path: str = ""
    protocol: Literal["", "rest", "websocket", "graphql", "mcp", "json-rpc", "tcp"] = ""
    tls: Literal["", "skip"] = ""
    enforcement: Literal["audit", "enforce", ""] = "audit"
    access: Literal["", "read-only", "read-write", "full"] = ""
    rules: list[OpenShellAllowRule] = Field(default_factory=list)
    deny_rules: list[OpenShellRequestMatcher] = Field(default_factory=list)
    allowed_ips: list[str] = Field(default_factory=list)
    allow_encoded_slash: bool = False
    websocket_credential_rewrite: bool = False
    request_body_credential_rewrite: bool = False
    allow_uninspected_credentials: bool = False
    persisted_queries: Literal["", "deny", "allow_registered"] = "deny"
    graphql_persisted_queries: dict[str, OpenShellGraphqlOperation] = Field(default_factory=dict)
    graphql_max_body_bytes: int = Field(default=65536, ge=0, le=4294967295)
    credential_signing: str | None = None
    signing_service: str | None = None
    signing_region: str | None = None
    credential_binding: OpenShellCredentialBinding | None = None
    json_rpc: OpenShellJsonRpcOptions | None = None
    mcp: OpenShellMcpOptions | None = None

    @model_validator(mode="after")
    def destination_shape(self) -> OpenShellEndpoint:
        if any(type(port) is not int or not 1 <= port <= 65535 for port in self.ports):
            raise ValueError("invalid endpoint ports")
        if (self.port and self.ports) or not (self.port or self.ports):
            raise ValueError("select port or ports")
        if not self.host and not self.allowed_ips:
            raise ValueError("endpoint needs host or allowed_ips")
        if self.access and self.rules:
            raise ValueError("access and rules are mutually exclusive")
        if self.tls == "skip" and self.protocol not in {"", "tcp"}:
            raise ValueError("TLS skip cannot inspect requests")
        if self.protocol in {"rest", "websocket", "graphql"} and not (self.access or self.rules):
            raise ValueError("inspected endpoint needs request grants")
        if self.protocol in {"mcp", "json-rpc"} and not self.rules and not (
            self.protocol == "mcp" and self.mcp and self.mcp.allow_all_known_mcp_methods
        ):
            raise ValueError("endpoint needs request rules")
        return self


class OpenShellBinary(OpenShellObject):
    path: str


class OpenShellNetworkRule(OpenShellObject):
    name: str = ""
    endpoints: list[OpenShellEndpoint] = Field(default_factory=list)
    binaries: list[OpenShellBinary] = Field(default_factory=list)


class OpenShellPolicy(OpenShellObject):
    version: Literal[1]
    filesystem_policy: OpenShellFilesystem | None = None
    landlock: OpenShellLandlock | None = None
    process: OpenShellProcess | None = None
    network_policies: dict[str, OpenShellNetworkRule] = Field(default_factory=dict)
    # Middleware is retained only as an explicit unsupported coverage signal.
    # Its free-form config may carry credentials and is never published.
    network_middlewares: dict[str, Any] = Field(default_factory=dict)


class OpenShellPolicyFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registration: str
    role: Literal["authored", "effective_snapshot"]
    runtime_version: Literal["0.1.2"]
    policy_schema_version: Literal[1] = 1
    policy: OpenShellPolicy
    defaulted_fields: list[str] = Field(default_factory=list)
    field_paths: list[str] = Field(default_factory=list)
    include_workdir_when_filesystem_omitted: Literal[True] = True
    landlock_when_omitted: Literal["best_effort"] = "best_effort"
    process_omission: Literal["driver_default"] = "driver_default"
    filesystem_baseline: Literal["runtime_dependent_not_resolved"] = "runtime_dependent_not_resolved"
    # An exported file is not evidence that this policy is running now.
    runtime_freshness_verified: Literal[False] = False
