"""Bounded local reproduction of OpenShell v0.1.2 selection/composition."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from agents_shipgate.core.openshell import OpenShellReadError, load_document, policy_field_paths
from agents_shipgate.schemas.openshell import OpenShellPolicy
from agents_shipgate.schemas.openshell_composition import (
    CredentialEndpointFacts,
    LocalComposition,
    OpenShellProviderProfile,
)


def parse_profile(text: str) -> OpenShellProviderProfile:
    try:
        return OpenShellProviderProfile.model_validate(load_document(text))
    except ValidationError as exc:
        raise OpenShellReadError("unsupported", "Provider profile contains unsupported fields, types or constraints") from exc


def digest(model) -> str:
    raw = json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def compose_local(spec: LocalComposition, read: Callable[[str, str, Callable], Any],
                  parse_policy: Callable) -> tuple[OpenShellPolicy, dict, list]:
    policies = {}
    contributors = []
    for role in ("global", "saved", "image"):
        context = getattr(spec, role + "_policy")
        if context.state == "absent":
            continue
        policy = read(context.path, "openshell_policy", parse_policy)
        if policy is None:
            raise OpenShellReadError("unsupported", "A selected composition policy could not be read")
        if any(key.startswith("_provider_") for key in policy.network_policies):
            raise OpenShellReadError("unsupported", "Authored composition inputs contain reserved provider rule names")
        policies[role] = policy
        contributors.append({"path": context.path, "role": role, "content_sha256": digest(policy), "selected": False})
    # Even a global override retains the saved/image inputs for restoration.
    underlying = next((role for role in ("saved", "image") if role in policies), None)
    if underlying is None:
        raise OpenShellReadError("unsupported", "Saved/image selection is absent; driver default policy is unresolved")
    selected = "global" if "global" in policies else underlying
    for item in contributors:
        item["selected"] = item["role"] == selected
    entries = []
    identities = set()
    imported_ids, interceptor_ids = set(), set()
    for reference in spec.profile_catalog:
        profile = read(reference.path, "openshell_profile", parse_profile)
        if profile is None:
            raise OpenShellReadError("unsupported", "A selected profile catalog input could not be read")
        identity = (reference.scope, reference.workspace, profile.id)
        if identity in identities:
            raise OpenShellReadError("unsupported", "Profile catalog contains a duplicate scope and ID")
        identities.add(identity)
        (interceptor_ids if reference.scope == "interceptor" else imported_ids).add(profile.id)
        entries.append((reference, profile))
    if imported_ids & interceptor_ids:
        raise OpenShellReadError("unsupported", "Interceptor and imported profile IDs collide")
    if ((spec.catalog_mode == "imported" and interceptor_ids)
            or (spec.catalog_mode == "interceptor" and imported_ids)):
        raise OpenShellReadError("unsupported", "Profile catalog source context contradicts its declared mode")
    effective = policies[selected].model_copy(deep=True)
    credential_use = []
    derived_bytes = len(json.dumps(effective.model_dump(mode="json")).encode())
    used = set()
    for attachment in spec.providers:
        candidates = [(ref, profile) for ref, profile in entries
                      if profile.id == attachment.profile_id and (
                          ref.scope != "workspace" or ref.workspace == spec.workspace)]
        candidates.sort(key=lambda item: 0 if item[0].scope == "workspace" else 1)
        if not candidates:
            raise OpenShellReadError("unsupported", "An attached provider has no profile in the selected resolution scope")
        reference, profile = candidates[0]
        if not profile.endpoints or not profile.binaries:
            raise OpenShellReadError("unsupported", "Endpointless or binary-free provider composition requires unresolved runtime context")
        derived_bytes += len(json.dumps(profile.model_dump(mode="json")).encode())
        if derived_bytes > 1024 * 1024:
            raise OpenShellReadError("unsupported", "Composition exceeds the 1 MiB derived-input bound")
        used.add((reference.scope, reference.workspace, profile.id))
        name = "".join(char.lower() if char.isascii() and (char.isalnum() or char == "_") else "_"
                       for char in attachment.name).strip("_") or "unnamed"
        preferred = "_provider_" + name
        key, suffix = preferred, 2
        while key in effective.network_policies:
            key = preferred + "_" + str(suffix)
            suffix += 1
        if selected != "global":
            from agents_shipgate.schemas.openshell import OpenShellBinary, OpenShellNetworkRule

            effective.network_policies[key] = OpenShellNetworkRule(name=key,
                endpoints=[item.model_copy(deep=True) for item in profile.endpoints],
                binaries=[OpenShellBinary(path=path) for path in profile.binaries])
        contributors.append({"path": reference.path, "role": "profile", "content_sha256": digest(profile),
            "selected": selected != "global", "profile_id": profile.id, "scope": reference.scope,
            "workspace": reference.workspace, "resource_version": profile.resource_version,
            "provider": attachment.name, "rule_key": key if selected != "global" else ""})
        if profile.credentials:
            fields = CredentialEndpointFacts.model_fields
            credential_use.append({"provider": attachment.name, "profile_id": profile.id,
                "scope": reference.scope, "workspace": reference.workspace,
                "credentials": [item.model_dump(mode="json") for item in profile.credentials],
                "endpoints": [{key: value for key, value in item.model_dump(mode="json").items()
                               if key in fields} for item in profile.endpoints]})
    for reference, profile in entries:
        if (reference.scope, reference.workspace, profile.id) not in used:
            contributors.append({"path": reference.path, "role": "profile", "content_sha256": digest(profile),
                "selected": False, "profile_id": profile.id, "scope": reference.scope,
                "workspace": reference.workspace, "resource_version": profile.resource_version})
    provenance = {"name": spec.name, "workspace": spec.workspace,
                  "selected_policy": selected, "contributors": contributors}
    try:
        effective = OpenShellPolicy.model_validate(effective.model_dump(mode="json"))
    except ValidationError as exc:
        raise OpenShellReadError("unsupported", "Composed policy exceeds the supported static constraints") from exc
    return effective, provenance, sorted(credential_use, key=lambda item: item["provider"])


def composed_fields(policy):
    # Generated policy spelling is derived; the authored sources remain in provenance.
    fields, _ = policy_field_paths(policy)
    return fields
