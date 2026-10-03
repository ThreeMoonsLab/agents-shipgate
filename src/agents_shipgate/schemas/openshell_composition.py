"""Versioned local composition inputs; no deployed or credential-value claims."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, model_validator

from agents_shipgate.schemas.openshell import (
    OpenShellEndpoint,
    OpenShellObject,
    OpenShellPolicyFacts,
    OpenShellPolicyReference,
)


def local(path: str) -> str:
    OpenShellPolicyReference(path=path, role="authored")
    return path


class LocalReference(OpenShellObject):
    path: str

    @model_validator(mode="after")
    def contained(self):
        local(self.path)
        return self


class PolicySelectionContext(OpenShellObject):
    state: Literal["absent", "selected"]
    path: str = ""

    @model_validator(mode="after")
    def selected_path(self):
        if self.state == "selected":
            local(self.path)
        elif self.path:
            raise ValueError("absent selection cannot name an input")
        return self


class ProfileReference(LocalReference):
    scope: Literal["platform", "workspace", "interceptor"]
    workspace: str = ""

    @model_validator(mode="after")
    def scope_identity(self):
        if (self.scope == "workspace") != bool(self.workspace):
            raise ValueError("workspace scope must name its workspace")
        return self


class ProviderAttachment(OpenShellObject):
    name: str = Field(min_length=1, max_length=128)
    profile_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    # A missing base-URL context cannot be treated as the profile's endpoints.
    endpoint_resolution: Literal["profile"]


class LocalComposition(OpenShellObject):
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    workspace: str = Field(min_length=1, max_length=128)
    global_policy: PolicySelectionContext
    saved_policy: PolicySelectionContext
    image_policy: PolicySelectionContext
    catalog_mode: Literal["imported", "interceptor", "combined"]
    profile_catalog: list[ProfileReference] = Field(max_length=32)
    providers: list[ProviderAttachment] = Field(max_length=32)

    @model_validator(mode="after")
    def distinct_attachments(self):
        names = [provider.name for provider in self.providers]
        if len(set(names)) != len(names):
            raise ValueError("provider instance names must be distinct")
        paths = [item.path for item in self.profile_catalog]
        if len(set(paths)) != len(paths):
            raise ValueError("profile catalog paths must be distinct")
        return self


class SnapshotMetadata(OpenShellObject):
    source: str = Field(default="", max_length=1024)
    revision: str = Field(default="", max_length=256)


class PolicyReferenceV2(OpenShellPolicyReference):
    snapshot: SnapshotMetadata | None = None

    @model_validator(mode="after")
    def snapshot_role(self):
        if self.snapshot is not None and self.role != "effective_snapshot":
            raise ValueError("snapshot metadata requires an effective snapshot")
        return self


class OpenShellSelectionV2(OpenShellObject):
    version: Literal[2]
    runtime_version: Literal["0.1.2"]
    policies: list[PolicyReferenceV2] = Field(default_factory=list, max_length=64)
    compositions: list[LocalComposition] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def distinct_inputs(self):
        if not self.policies and not self.compositions:
            raise ValueError("select at least one policy or composition")
        for values in ([item.path for item in self.policies], [item.name for item in self.compositions]):
            if len(set(values)) != len(values):
                raise ValueError("duplicate policy or composition identity")
        return self


class StaticCredential(OpenShellObject):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    env_vars: list[str] = Field(default_factory=list, max_length=32)
    required: bool = False
    auth_style: Literal["", "basic", "bearer", "header", "query", "path"] = ""
    header_name: str = ""
    query_param: str = ""
    path_template: str = ""


class ProfileDiscovery(OpenShellObject):
    credentials: list[str] = Field(default_factory=list, max_length=32)


class OpenShellProviderProfile(OpenShellObject):
    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    resource_version: int = Field(default=0, ge=0)
    annotations: dict[str, str] = Field(default_factory=dict, max_length=32)
    display_name: str = ""
    description: str = ""
    category: Literal["other", "inference", "agent", "source_control", "messaging", "data", "knowledge"] = "other"
    inference_capable: bool = False
    credentials: list[StaticCredential] = Field(default_factory=list, max_length=32)
    discovery: ProfileDiscovery | None = None
    endpoints: list[OpenShellEndpoint] = Field(max_length=128)
    binaries: list[str] = Field(max_length=128)

    @model_validator(mode="after")
    def credential_names(self):
        names = [item.name for item in self.credentials]
        if len(set(names)) != len(names):
            raise ValueError("duplicate credential name")
        if self.discovery and not set(self.discovery.credentials) <= set(names):
            raise ValueError("unknown discovery credential")
        if any(not path.startswith("/") or not re.fullmatch(r"[^\x00-\x1f]+", path) for path in self.binaries):
            raise ValueError("invalid binary path")
        return self


class CompositionContributor(OpenShellObject):
    path: str
    role: Literal["global", "saved", "image", "profile"]
    content_sha256: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    selected: bool
    profile_id: str = ""
    scope: Literal["", "platform", "workspace", "interceptor"] = ""
    workspace: str = ""
    resource_version: int = 0
    provider: str = ""
    rule_key: str = ""


class CompositionProvenance(OpenShellObject):
    name: str
    workspace: str
    selected_policy: Literal["global", "saved", "image"]
    contributors: list[CompositionContributor]
    startup_bound_fields: list[str] = Field(default_factory=lambda: ["filesystem_policy", "landlock"])
    creation_bound_fields: list[str] = Field(default_factory=lambda: ["process"])
    dynamic_fields: list[str] = Field(default_factory=lambda: ["network_policies", "network_middlewares"])


class CredentialEndpointFacts(OpenShellObject):
    host: str
    port: int
    ports: list[int]
    path: str
    tls: str
    allow_uninspected_credentials: bool
    websocket_credential_rewrite: bool
    request_body_credential_rewrite: bool


class CredentialUseFacts(OpenShellObject):
    provider: str
    profile_id: str
    scope: Literal["platform", "workspace", "interceptor"]
    workspace: str
    credentials: list[StaticCredential]
    # These endpoints select credential placement; binaries select network reach.
    endpoints: list[CredentialEndpointFacts]
    credential_values_read: Literal[False] = False
    runtime_authorization_verified: Literal[False] = False


class OpenShellComposedPolicyFacts(OpenShellPolicyFacts):
    role: Literal["composed"] = "composed"
    composition: CompositionProvenance
    credential_use: list[CredentialUseFacts]


class OpenShellSnapshotFactsV2(OpenShellPolicyFacts):
    role: Literal["effective_snapshot"] = "effective_snapshot"
    snapshot: SnapshotMetadata
    startup_bound_fields: list[str] = Field(default_factory=lambda: ["filesystem_policy", "landlock"])
    creation_bound_fields: list[str] = Field(default_factory=lambda: ["process"])
    dynamic_fields: list[str] = Field(default_factory=lambda: ["network_policies", "network_middlewares"])
