"""Bounded, offline parsing of explicitly selected OpenShell documents."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError
from yaml.events import AliasEvent, CollectionEndEvent, CollectionStartEvent, ScalarEvent

from agents_shipgate.schemas.openshell import OpenShellPolicy, OpenShellSelection

MAX_OPENSHELL_BYTES = 1024 * 1024
MAX_OPENSHELL_EVENTS = 100000
MAX_OPENSHELL_DEPTH = 48
MAX_OPENSHELL_REFERENCES = 64


@dataclass
class OpenShellCollectionBudget:
    references: int = 0
    artifact_ids: set[str] = field(default_factory=set)

    def reserve(self) -> bool:
        self.references += 1
        return self.references <= MAX_OPENSHELL_REFERENCES


@dataclass
class OpenShellReadError(ValueError):
    kind: str
    message: str


class _PolicyLoader(yaml.SafeLoader):
    # YAML 1.2 booleans; YAML 1.1 dates and on/off coercions are inappropriate
    # for string-valued MCP revisions, methods, host names and matchers.
    yaml_implicit_resolvers = {
        key: [item for item in resolvers if item[0] not in {
            "tag:yaml.org,2002:bool", "tag:yaml.org,2002:timestamp",
        }]
        for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[str, Any]:
        mapping: dict[str, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key == "<<" or key in mapping:
                raise OpenShellReadError("parse_failed", "duplicate, merge or non-string mapping key")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


_PolicyLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|false|True|False|TRUE|FALSE)$"), list("tTfF")
)


def load_document(text: str) -> dict[str, Any]:
    """Reject expansion and depth hazards before constructing any YAML nodes.

    JSON is a YAML subset, so duplicate handling and bounds are identical.
    Anchors/aliases and explicit tags are a deliberate unsupported subset.
    """
    if len(text.encode("utf-8")) > MAX_OPENSHELL_BYTES:
        raise OpenShellReadError("unsupported", "OpenShell document exceeds the 1 MiB static read limit")
    depth = 0
    try:
        for count, event in enumerate(yaml.parse(text), 1):
            if count > MAX_OPENSHELL_EVENTS:
                raise OpenShellReadError("unsupported", "OpenShell document exceeds the event limit")
            if isinstance(event, AliasEvent) or getattr(event, "anchor", None):
                raise OpenShellReadError("unsupported", "YAML anchors and aliases are not read")
            if getattr(event, "tag", None):
                raise OpenShellReadError("unsupported", "explicit YAML tags are not read")
            if isinstance(event, CollectionStartEvent):
                depth += 1
                if depth > MAX_OPENSHELL_DEPTH:
                    raise OpenShellReadError("unsupported", "OpenShell document exceeds the depth limit")
            elif isinstance(event, CollectionEndEvent):
                depth -= 1
            elif isinstance(event, ScalarEvent) and any(ord(char) < 32 for char in event.value):
                raise OpenShellReadError("unsupported", "control characters in policy values are not read")
        data = yaml.load(text, Loader=_PolicyLoader)
    except (yaml.YAMLError, RecursionError, UnicodeError) as exc:
        raise OpenShellReadError("parse_failed", "OpenShell static parser rejected the document") from exc
    if not isinstance(data, dict):
        raise OpenShellReadError("parse_failed", "OpenShell document must be an object")
    _reject_nulls(data)
    return data


def _reject_nulls(value: Any) -> None:
    if value is None:
        raise OpenShellReadError("unsupported", "explicit null values are not read")
    if isinstance(value, dict):
        for key, child in value.items():
            _reject_nulls(key)
            _reject_nulls(child)
    elif isinstance(value, list):
        for child in value:
            _reject_nulls(child)
    elif isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeError as exc:
            raise OpenShellReadError("unsupported", "invalid Unicode policy value") from exc


def parse_selection(text: str) -> OpenShellSelection:
    # The registration is JSON, not an upstream OpenShell format.
    data = load_document(text)
    try:
        json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise OpenShellReadError("parse_failed", "OpenShell registration must be JSON") from exc
    return _validate(OpenShellSelection, data)


def parse_policy(text: str) -> OpenShellPolicy:
    data = load_document(text)
    model = _validate(OpenShellPolicy, data)
    if model.network_middlewares:
        raise OpenShellReadError("unsupported", "network_middlewares are outside static policy coverage")
    return model


def _validate(model: type[BaseModel], data: dict[str, Any]) -> Any:
    if type(data.get("version")) is not int:
        raise OpenShellReadError("unsupported", "document version must be an integer")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        # Never include input values, dictionary keys or parser excerpts.
        raise OpenShellReadError(
            "unsupported", "unsupported version, field, type or policy constraint; see OpenShell coverage documentation"
        ) from exc


def policy_field_paths(model: BaseModel, prefix: str = "") -> tuple[list[str], list[str]]:
    """JSON pointers distinguish an omitted default from an authored value."""
    fields: list[str] = []
    defaults: list[str] = []
    for name in type(model).model_fields:
        path = f"{prefix}/{name}"
        fields.append(path)
        if name not in model.model_fields_set:
            defaults.append(path)
        value = getattr(model, name)
        children: list[tuple[str, BaseModel]] = []
        if isinstance(value, BaseModel):
            children.append((path, value))
        elif isinstance(value, dict):
            children.extend(
                (f"{path}/{key.replace('~', '~0').replace('/', '~1')}", child)
                for key, child in value.items() if isinstance(child, BaseModel)
            )
        elif isinstance(value, list):
            children.extend((f"{path}/{index}", child) for index, child in enumerate(value)
                            if isinstance(child, BaseModel))
        for child_path, child in children:
            child_fields, child_defaults = policy_field_paths(child, child_path)
            fields.extend(child_fields)
            defaults.extend(child_defaults)
    return sorted(fields), sorted(defaults)
