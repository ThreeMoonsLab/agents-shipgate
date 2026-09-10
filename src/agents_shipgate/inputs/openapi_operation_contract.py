"""A finite declared DELETE request domain, captured before Tool normalization."""

from __future__ import annotations

import itertools
import json
import math
import re
from typing import Any
from urllib.parse import urlsplit

import yaml

from agents_shipgate.core.tool_identity import source_observation_id
from agents_shipgate.schemas.operation_attribution import DeclaredOperation, OperationInput

MAX_TARGET_BYTES = 16 * 1024


def unambiguous_document(text: str) -> bool:
    """Reject duplicate keys, merges, aliases and non-string mapping keys.

    This is a proof-profile refusal, not a change to the supported source loader.
    In particular, a last-key-wins loader result cannot prove uniqueness.
    """
    try:
        root = yaml.compose(text)
        todo, seen = [root], set()
        while todo:
            node = todo.pop()
            if node is None or id(node) in seen or len(seen) >= 5000:
                return False
            seen.add(id(node))
            if isinstance(node, yaml.MappingNode):
                keys = set()
                for key, value in node.value:
                    if not isinstance(key, yaml.ScalarNode) or key.tag != "tag:yaml.org,2002:str":
                        return False
                    if key.value in keys:
                        return False
                    keys.add(key.value)
                    todo.append(value)
            elif isinstance(node, yaml.SequenceNode):
                todo.extend(node.value)
        return True
    except (yaml.YAMLError, RecursionError, ValueError):
        return False


def operation_evidence(
    *, document, document_digest, source, tool, path_item, operation, document_unambiguous
):
    row = DeclaredOperation(
        source_id=source.id,
        observation_id=source_observation_id(tool, source.id),
        tool_name=tool.name,
        method=tool.annotations["httpMethod"],
        path_template=tool.annotations["path"],
        source_pointer=tool.source_pointer,
        status="unresolved",
        reason="unsupported_declared_operation_profile",
        inputs=[
            OperationInput(path=tool.source_path, sha256=document_digest, role="openapi_document")
        ],
    )
    try:
        if not document_unambiguous:
            raise ValueError("ambiguous_document")
        if document.get("openapi") != "3.0.3" or row.method != "DELETE":
            raise ValueError("unsupported_version_or_method")
        if not isinstance(operation.get("operationId"), str) or not operation["operationId"]:
            raise ValueError("literal_operation_id_required")
        if set(operation) - {
            "operationId",
            "summary",
            "description",
            "tags",
            "externalDocs",
            "responses",
            "deprecated",
            "security",
            "parameters",
            "servers",
        }:
            raise ValueError("unmodeled_operation_dependency")
        if "$ref" in path_item or any(key in operation for key in ("requestBody", "callbacks")):
            raise ValueError("unmodeled_operation_dependency")
        servers = operation.get("servers", path_item.get("servers", document.get("servers")))
        if not isinstance(servers, list) or len(servers) != 1 or not isinstance(servers[0], dict):
            raise ValueError("one_literal_server_required")
        server = servers[0]
        if set(server) - {"url", "description"} or not isinstance(server.get("url"), str):
            raise ValueError("server_configuration_unresolved")
        if len(server["url"]) > 2048 or len(row.path_template) > 1024:
            raise ValueError("declared_target_byte_limit")
        url = urlsplit(server["url"])
        if (
            url.scheme != "https"
            or not url.netloc
            or url.username
            or url.password
            or url.query
            or url.fragment
            or "{" in server["url"]
            or "}" in server["url"]
        ):
            raise ValueError("server_configuration_unresolved")
        if not re.fullmatch(
            r"https://[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[A-Za-z0-9_/-]*)?", server["url"]
        ):
            raise ValueError("server_encoding_unresolved")
        # Avoid path encoding, traversal and serialization equivalence guesses.
        template = row.path_template
        if not re.fullmatch(r"/(?:[A-Za-z0-9_/-]|\{[A-Za-z_][A-Za-z0-9_]*\})*", template):
            raise ValueError("path_template_unresolved")
        names = re.findall(r"\{([^}]+)\}", template)
        if len(set(names)) != len(names) or len(names) > 4:
            raise ValueError("path_parameter_identity_unresolved")
        parameters: dict[tuple[str, str], dict[str, Any]] = {}
        for entries in (path_item.get("parameters", []), operation.get("parameters", [])):
            if not isinstance(entries, list):
                raise ValueError("parameters_unresolved")
            local = set()
            for param in entries:
                if not isinstance(param, dict) or set(param) - {
                    "in",
                    "name",
                    "required",
                    "schema",
                    "description",
                    "style",
                    "explode",
                }:
                    raise ValueError("parameter_configuration_unresolved")
                if (
                    param.get("in") != "path"
                    or param.get("name") not in names
                    or param.get("required") is not True
                ):
                    raise ValueError("non_path_or_optional_parameter")
                key = (param["in"], param["name"])
                if key in local:
                    raise ValueError("duplicate_parameter")
                local.add(key)
                if (
                    param.get("style", "simple") != "simple"
                    or param.get("explode", False) is not False
                ):
                    raise ValueError("parameter_serialization_unresolved")
                parameters[key] = param
        if {key[1] for key in parameters} != set(names):
            raise ValueError("path_parameter_missing")
        domains = []
        for name in names:
            schema = parameters[("path", name)].get("schema")
            if (
                not isinstance(schema, dict)
                or set(schema) - {"type", "enum", "description"}
                or schema.get("type") != "string"
            ):
                raise ValueError("finite_string_enum_required")
            values = schema.get("enum")
            if (
                not isinstance(values, list)
                or not 1 <= len(values) <= 32
                or any(
                    not isinstance(value, str)
                    or len(value) > 128
                    or not re.fullmatch(r"[A-Za-z0-9_-]+", value)
                    for value in values
                )
            ):
                raise ValueError("finite_string_enum_required")
            domains.append(sorted(set(values)))
        if math.prod(map(len, domains)) > 256:
            raise ValueError("declared_target_limit")
        maximum_length = (
            len(server["url"]) + len(template) + sum(max(map(len, domain)) for domain in domains)
        )
        if math.prod(map(len, domains)) * maximum_length > MAX_TARGET_BYTES:
            raise ValueError("declared_target_byte_limit")
        security = operation.get("security", document.get("security"))
        if not isinstance(security, list):
            raise ValueError("explicit_security_required")
        schemes = document.get("components", {}).get("securitySchemes", {})
        used = {}
        for alternative in security:
            if not isinstance(alternative, dict):
                raise ValueError("security_unresolved")
            for name, scopes in alternative.items():
                scheme = schemes.get(name)
                if scopes != [] or not isinstance(scheme, dict):
                    raise ValueError("security_unresolved")
                if scheme.get("type") == "http":
                    valid = scheme.get("scheme") in {"bearer", "basic"} and not set(scheme) - {
                        "type",
                        "scheme",
                        "bearerFormat",
                        "description",
                    }
                else:
                    valid = (
                        scheme.get("type") == "apiKey"
                        and scheme.get("in") == "header"
                        and isinstance(scheme.get("name"), str)
                        and not set(scheme) - {"type", "in", "name", "description"}
                    )
                if not valid:
                    raise ValueError("security_unresolved")
                used[name] = scheme
        targets = []
        for values in itertools.product(*domains):
            path = template
            for name, value in zip(names, values, strict=True):
                path = path.replace("{" + name + "}", value)
            targets.append(server["url"].rstrip("/") + path)
        row.server = server["url"]
        row.declared_targets = sorted(set(targets))
        row.security_contract = json.dumps(
            {"alternatives": security, "schemes": used}, sort_keys=True, separators=(",", ":")
        )
        row.status = "observed"
        row.reason = "finite_declared_delete_domain_runtime_unverified"
    except (ValueError, TypeError, AttributeError) as exc:
        row.reason = (
            str(exc) if isinstance(exc, ValueError) else "malformed_operation_configuration"
        )
    return row
