from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal
from urllib.parse import unquote

from agents_shipgate.core.domain import (
    AuthAlternative,
    AuthInfo,
    AuthorityMode,
    AuthSchemeRequirement,
    LoadedToolSource,
    SourceSurfaceOmission,
    Tool,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.static_inputs import active_static_input_snapshot, read_static_input_text
from agents_shipgate.inputs import common as input_common
from agents_shipgate.inputs.common import (
    HTTP_METHODS,
    PositionIndex,
    json_pointer_escape,
    load_structured_text_with_positions,
    manifest_relative_path,
    resolve_input_path,
    schema_to_parameters,
    stable_tool_id,
    strip_untrusted_binding_annotations,
    tool_name_warning,
)
from agents_shipgate.inputs.coverage import BoundaryCell, SourceCoverage
from agents_shipgate.inputs.openapi_operation_contract import (
    operation_evidence,
    unambiguous_document,
)
from agents_shipgate.inputs.protocol import LoadedAdapterResult
from agents_shipgate.schemas.manifest import (
    AgentsShipgateManifest,
    ToolSourceConfig,
)

MAX_SCHEMA_RESOLVE_DEPTH = 32
MAX_SCHEMA_RESOLVE_NODES = 5000

#: ``SourceSurfaceOmission.reason`` for a path item whose ``$ref`` leaves this
#: document, names nothing in it, names something that is not an object, or
#: loops: the operations it points to are never read.
UNRESOLVED_PATH_ITEM_REF = "unresolved_path_item_ref"
#: ``SourceSurfaceOmission.reason`` for an operation a referenced path item
#: declares and a key of the same name beside the ``$ref`` replaces. OpenAPI
#: leaves that conflict undefined; the operation written beside the ``$ref`` is
#: the one read.
SHADOWED_PATH_ITEM_OPERATION = "shadowed_path_item_operation"

_MISSING = object()


def load_openapi_tools(source: ToolSourceConfig, base_dir: Path) -> LoadedToolSource:
    assert source.path is not None
    path = resolve_input_path(base_dir, source.path)
    if not path.exists():
        raise InputParseError(f"Input file not found: {path}")
    try:
        text = read_static_input_text(path, max_bytes=input_common.MAX_INPUT_FILE_BYTES)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise InputParseError(f"Unable to read input file {path}: {exc}") from exc
    document, positions = load_structured_text_with_positions(text, source=path)
    if not isinstance(document, dict):
        raise InputParseError(f"OpenAPI file must contain an object: {path}")
    if "openapi" not in document:
        raise InputParseError(f"OpenAPI file missing 'openapi' version: {path}")

    paths = document.get("paths")
    if not isinstance(paths, dict):
        raise InputParseError(f"OpenAPI file missing paths object: {path}")

    tools: list[Tool] = []
    warnings: list[str] = []
    omissions: list[SourceSurfaceOmission] = []
    seen_names: set[str] = set()
    operation_rows = []
    # The DELETE proof profile's uniqueness check parses the document again in
    # pure Python. Only a DELETE operation consults it, so it is computed on
    # the first one rather than for every document read.
    document_unambiguous: bool | None = None
    document_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    source_path = manifest_relative_path(source.path, base_dir)
    # Each schema $ref this document cannot resolve, once, with its tools.
    unresolved_schema_refs: dict[str, list[str]] = {}
    unresolved_carriers: dict[str, list[str]] = {}

    def _omit(subject: str, reason: str, warning: str, detail: str) -> None:
        """Record an operation that never reached the catalog, beside its warning."""

        warnings.append(warning)
        omissions.append(
            SourceSurfaceOmission(subject=subject, reason=reason, detail=detail, warning=warning)
        )

    for api_path, raw_path_item in paths.items():
        if not isinstance(raw_path_item, dict):
            raise InputParseError(f"OpenAPI path item {api_path} must be an object")
        path_item = _read_path_item(document, str(api_path), raw_path_item)
        if path_item.unresolved_ref is not None:
            ref = path_item.unresolved_ref
            external = not ref.startswith("#")
            _omit(
                f"/paths/{json_pointer_escape(str(api_path))}",
                UNRESOLVED_PATH_ITEM_REF,
                f"OpenAPI path item {str(api_path)!r} in {source_path!r} points to "
                f"$ref {ref!r}, "
                + (
                    "outside this document"
                    if external
                    else "which this document does not resolve to a path item"
                )
                + "; the operations it declares were not read.",
                "The path item's operations live in another file, which is not read; "
                "point this source at a bundled document that inlines them."
                if external
                else "The path item's $ref names nothing usable in this document, so "
                "its operations were not read; correct the reference.",
            )
        for method_key, shadowed_pointer in path_item.shadowed:
            _omit(
                shadowed_pointer,
                SHADOWED_PATH_ITEM_OPERATION,
                f"OpenAPI path item {str(api_path)!r} in {source_path!r} declares "
                f"{method_key!r} both beside its $ref and at {shadowed_pointer!r}; "
                "only the operation beside the $ref was read.",
                "Two operations claim the same method on this path; remove one so "
                "the document says which is served.",
            )
        path_parameters = path_item.fields.get("parameters") or []
        for method, operation in path_item.fields.items():
            method_lower = str(method).lower()
            if method_lower not in HTTP_METHODS:
                continue
            if method != method_lower:
                pointer = f"{path_item.pointers[method]}/{json_pointer_escape(str(method))}"
                raise InputParseError(
                    f"OpenAPI operation key {method!r} at {pointer} in {source_path!r} "
                    f"is noncanonical; use lowercase {method_lower!r}."
                )
            if not isinstance(operation, dict):
                raise InputParseError(
                    f"OpenAPI operation {method_lower.upper()} {api_path} must be an object"
                )
            try:
                tool = _operation_to_tool(
                    document=document,
                    source=source,
                    source_ref=source.path,
                    source_path=source_path,
                    api_path=str(api_path),
                    pointer_base=path_item.pointers[method],
                    method=method_lower,
                    method_key=str(method),
                    operation=operation,
                    path_parameters=path_parameters,
                    path_parameters_at=f"{path_item.pointers.get('parameters', '')}/parameters",
                    positions=positions,
                    warnings=warnings,
                    unresolved_carriers=unresolved_carriers,
                )
            except RecursionError as exc:
                raise InputParseError(
                    f"OpenAPI operation {method_lower.upper()} {api_path} exceeds the nesting limit"
                ) from exc
            except MemoryError:
                raise
            except Exception as exc:  # noqa: BLE001 - source parse boundary.
                raise InputParseError(
                    "Unable to parse OpenAPI operation "
                    f"{method_lower.upper()} {api_path}: {type(exc).__name__}: {exc}"
                ) from exc
            if tool.name in seen_names:
                warnings.append(
                    f"Duplicate OpenAPI tool name {tool.name!r} in source {source.id!r}"
                )
            seen_names.add(tool.name)
            if warning := tool_name_warning(tool.name):
                warnings.append(warning)
            for ref in _unresolved_refs(document, tool.input_schema) + _unresolved_refs(
                document, tool.output_schema
            ):
                carriers = unresolved_schema_refs.setdefault(ref, [])
                if tool.name not in carriers:
                    carriers.append(tool.name)
            tools.append(tool)
            if method_lower == "delete":
                if document_unambiguous is None:
                    document_unambiguous = unambiguous_document(text)
                operation_rows.append(operation_evidence(
                    document=document, document_digest=document_digest,
                    # The path item as written: a ``$ref`` in it is a
                    # dependency the proof profile does not model, and refuses.
                    source=source, tool=tool, path_item=raw_path_item,
                    method_key=method, operation=operation,
                    document_unambiguous=document_unambiguous,
                ))

    for warning, operations in unresolved_carriers.items():
        others = len(operations) - 1
        scope = f"operation {operations[0]!r}"
        if others:
            scope += f" and {others} other operation{'s' if others > 1 else ''}"
        warnings.append(f"{warning} ({scope})")

    for ref, carriers in unresolved_schema_refs.items():
        others = len(carriers) - 1
        warnings.append(
            f"Unresolved OpenAPI $ref {ref!r} in tool {carriers[0]!r}"
            + (f" and {others} other tool{'s' if others > 1 else ''}" if others else "")
            + "; external or missing refs are left as metadata."
        )

    snapshot = active_static_input_snapshot()
    if operation_rows and snapshot is not None and snapshot.contains(path):
        snapshot.mark_dependency_input(path)
    return LoadedToolSource(
        source_id=source.id,
        source_type="openapi",
        tools=tools,
        warnings=warnings,
        omissions=omissions,
        operation_evidence=operation_rows,
    )


@dataclass
class _PathItem:
    """One ``paths`` entry with its ``$ref`` chain followed inside the document."""

    #: Path Item fields, the ones written beside a ``$ref`` first.
    fields: dict[Any, Any] = field(default_factory=dict)
    #: For each field, the JSON pointer of the object that declares it.
    pointers: dict[Any, str] = field(default_factory=dict)
    #: The ``$ref`` that could not be followed, when one could not.
    unresolved_ref: str | None = None
    #: ``(method key, pointer)`` of each referenced operation that a field of
    #: the same name, read earlier in the chain, replaced.
    shadowed: list[tuple[str, str]] = field(default_factory=list)


def _read_path_item(document: dict[str, Any], api_path: str, raw: dict[str, Any]) -> _PathItem:
    """Read a Path Item, following its ``$ref`` within this document.

    A Path Item may carry a ``$ref`` to another Path Item (OpenAPI 3.1 adds
    ``components.pathItems`` to hold them) beside fields of its own. The fields
    written beside the ``$ref`` are read first, then each referenced object's in
    turn, and every field keeps the pointer of the object that declares it, so
    an operation's ``source_pointer`` and line name where it is written. Where
    two declare the same field the specification leaves the result undefined:
    the earlier one is kept, and a replaced operation is returned in
    ``shadowed`` rather than dropped unseen.

    A reference this cannot follow — one that leaves the document, names
    nothing in it, names something that is not an object, or loops — is
    returned in ``unresolved_ref``. No file is opened: an external reference is
    reported, never read.
    """

    item = _PathItem()
    current: dict[Any, Any] = raw
    pointer = f"/paths/{json_pointer_escape(api_path)}"
    # By object, not by spelling: two pointers can name one object.
    visited = {id(raw)}
    while True:
        for key, value in current.items():
            if key == "$ref":
                continue
            if key in item.fields:
                if str(key).lower() in HTTP_METHODS:
                    item.shadowed.append(
                        (str(key), f"{pointer}/{json_pointer_escape(str(key))}")
                    )
                continue
            item.fields[key] = value
            item.pointers[key] = pointer
        if "$ref" not in current:
            return item
        ref = current["$ref"]
        resolved_pointer = _local_ref_pointer(ref) if isinstance(ref, str) else None
        target = _pointer_target(document, resolved_pointer) if resolved_pointer is not None else _MISSING
        if not isinstance(target, dict) or id(target) in visited:
            item.unresolved_ref = str(ref)
            return item
        visited.add(id(target))
        current = target
        assert resolved_pointer is not None
        pointer = resolved_pointer


def _operation_to_tool(
    *,
    document: dict[str, Any],
    source: ToolSourceConfig,
    source_ref: str | None,
    source_path: str | None,
    api_path: str,
    pointer_base: str,
    method: str,
    method_key: str,
    operation: dict[str, Any],
    path_parameters: list[Any],
    path_parameters_at: str,
    positions: PositionIndex,
    warnings: list[str],
    unresolved_carriers: dict[str, list[str]],
) -> Tool:
    operation_id = operation.get("operationId") or _operation_name(method, api_path)
    # A referenced Path Item can supply the operation and path parameters
    # from different objects. Use each field's original pointer.
    carrier_base = f"{pointer_base}/{json_pointer_escape(method_key)}"
    carrier_warnings: list[str] = []
    request_at = f"{carrier_base}/requestBody"
    request_body, request_at = _resolve_carrier(
        document, operation.get("requestBody"), request_at, ("content",), carrier_warnings
    )
    request_schema = _schema_from_content(document, request_body, request_at, carrier_warnings)
    parameter_schema = _parameters_to_schema(
        document,
        (
            (path_parameters_at, path_parameters),
            (f"{carrier_base}/parameters", operation.get("parameters") or []),
        ),
        carrier_warnings,
    )
    input_schema = _merge_object_schemas(parameter_schema, request_schema)
    output_schema = _extract_response_schema(document, operation, carrier_base, carrier_warnings)
    for warning in dict.fromkeys(carrier_warnings):
        unresolved_carriers.setdefault(warning, []).append(str(operation_id))
    confidence = "medium" if carrier_warnings else "high"
    description = "\n".join(
        part for part in [operation.get("summary"), operation.get("description")] if part
    )
    annotations = _extract_annotations(
        operation,
        operation_label=str(operation_id),
        warnings=warnings,
    )
    annotations["httpMethod"] = method.upper()
    annotations["path"] = api_path
    auth = _extract_security(document, operation)

    pointer = f"{pointer_base}/{method}"
    pos = positions.lookup(pointer)
    source_start_line: int | None = None
    source_start_column: int | None = None
    if pos is not None:
        source_start_line, source_start_column = pos

    # Note: `source_location` intentionally stays None for OpenAPI tools.
    # The legacy `path:line` string participates in `run_id` (see
    # `cli/scan/run_identity.py:_run_id`), and v0.10 OpenAPI tools never set it.
    # Reviewers get the line via the structured `source_start_line` /
    # `source_pointer` fields; SARIF prefers those over the legacy string.
    return Tool(
        id=stable_tool_id(str(operation_id)),
        name=str(operation_id),
        description=description or None,
        source_type="openapi",
        source_id=source.id,
        source_ref=f"{source_ref}#{pointer}",
        source_path=source_path,
        source_start_line=source_start_line,
        source_start_column=source_start_column,
        source_pointer=pointer,
        input_schema=input_schema,
        output_schema=output_schema,
        parameters=schema_to_parameters(input_schema),
        annotations=annotations,
        auth=auth,
        extraction_confidence=confidence,
        extraction={"method": "openapi", "confidence": confidence},
    )


def _schema_from_content(
    document: dict[str, Any], carrier: dict[str, Any], pointer: str, warnings: list[str]
) -> dict[str, Any]:
    content = carrier.get("content")
    if not isinstance(content, dict) or not content:
        return {}
    key = "application/json" if "application/json" in content else next(iter(content))
    media_at = f"{pointer}/content/{json_pointer_escape(str(key))}"
    media, _ = _resolve_carrier(document, content[key], media_at, (), warnings)
    schema = media.get("schema") or {}
    if not isinstance(schema, dict):
        return {}
    return _resolve_schema(document, schema)


def _extract_response_schema(
    document: dict[str, Any], operation: dict[str, Any], pointer: str, warnings: list[str]
) -> dict[str, Any]:
    responses = operation.get("responses") or {}
    if not isinstance(responses, dict):
        return {}
    key = _success_response_key(responses)
    response_at = f"{pointer}/responses/{json_pointer_escape(str(key))}"
    response, response_at = _resolve_carrier(
        document, responses.get(key), response_at,
        ("description", "content", "summary", "headers", "links"), warnings,
    )
    return _schema_from_content(document, response, response_at, warnings)


def _success_response_key(responses: dict[Any, Any]) -> Any:
    """The key of the selected response, so the schema can name its source.

    Codes are compared as text because YAML reads an unquoted ``200:`` as an
    integer, where JSON and a quoted key give a string. The ``2XX`` range
    stands in when no code is listed, and the first response when neither is.
    """

    codes: list[tuple[int, Any]] = []
    success_range: Any = None
    for key in responses:
        code = str(key).strip()
        if len(code) == 3 and code.isascii() and code.isdigit() and code.startswith("2"):
            codes.append((int(code), key))
        elif code.upper() == "2XX" and success_range is None:
            success_range = key
    if codes:
        return min(codes, key=lambda item: item[0])[1]
    if success_range is not None:
        return success_range
    return next(iter(responses), None)


def _parameters_to_schema(
    document: dict[str, Any],
    parameter_lists: tuple[tuple[str, list[Any]], ...],
    warnings: list[str],
) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for pointer, parameters in parameter_lists:
        if not isinstance(parameters, list):
            continue
        for index, raw in enumerate(parameters):
            parameter_at = f"{pointer}/{index}"
            parameter, parameter_at = _resolve_carrier(document, raw, parameter_at, ("name",), warnings)
            name = parameter.get("name")
            if not name:
                continue
            if "schema" in parameter:
                schema = parameter["schema"]
                properties[str(name)] = _resolve_schema(document, schema) if isinstance(schema, dict) else {}
            else:
                properties[str(name)] = _schema_from_content(document, parameter, parameter_at, warnings)
            if parameter.get("required") is True:
                required.append(str(name))
    if not properties:
        return {}
    return {"type": "object", "properties": properties, "required": required}


def _merge_object_schemas(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    if not left:
        return right or {}
    if not right:
        return left
    merged = {"type": "object", "properties": {}, "required": []}
    for schema in [left, right]:
        if schema.get("type") == "object" or "properties" in schema:
            merged["properties"].update(schema.get("properties") or {})
            merged["required"].extend(schema.get("required") or [])
        else:
            merged["properties"]["body"] = schema
            merged["required"].append("body")
    merged["required"] = sorted(set(merged["required"]))
    return merged


def _extract_security(document: dict[str, Any], operation: dict[str, Any]) -> AuthInfo:
    if "security" in operation:
        security = operation["security"]
        explicit = True
        inherited = False
    elif "security" in document:
        security = document["security"]
        explicit = True
        inherited = True
    else:
        security = []
        explicit = False
        inherited = False
    schemes = (document.get("components") or {}).get("securitySchemes") or {}
    scopes: list[str] = []
    auth_type: str | None = None
    alternatives: list[AuthAlternative] = []
    invalid_annotations: list[str] = []
    if isinstance(security, list):
        for entry_index, entry in enumerate(security):
            if not isinstance(entry, dict):
                invalid_annotations.append(f"security[{entry_index}] must be an object")
                continue
            requirements: list[AuthSchemeRequirement] = []
            for scheme_name, scheme_scopes in entry.items():
                scheme = schemes.get(scheme_name) if isinstance(schemes, dict) else None
                scheme_type: str | None = None
                if isinstance(scheme, dict):
                    raw_type = scheme.get("type")
                    if isinstance(raw_type, str) and raw_type.strip():
                        scheme_type = raw_type.strip()
                    elif raw_type is not None:
                        invalid_annotations.append(
                            f"security scheme {scheme_name!r} has invalid type"
                        )
                    auth_type = auth_type or scheme_type
                normalized_scopes: list[str] = []
                if not isinstance(scheme_scopes, list):
                    invalid_annotations.append(
                        f"security scopes for {scheme_name!r} must be a list"
                    )
                else:
                    for raw_scope in scheme_scopes:
                        if not isinstance(raw_scope, str) or not raw_scope.strip():
                            invalid_annotations.append(
                                f"security scopes for {scheme_name!r} must be non-blank strings"
                            )
                            continue
                        normalized_scopes.append(raw_scope.strip())
                    normalized_scopes = sorted(set(normalized_scopes))
                scopes.extend(normalized_scopes)
                requirements.append(
                    AuthSchemeRequirement(
                        name=str(scheme_name),
                        type=scheme_type,
                        scopes=normalized_scopes,
                    )
                )
            alternatives.append(
                AuthAlternative(
                    schemes=sorted(requirements, key=lambda item: item.name),
                    anonymous=not entry,
                )
            )
    else:
        invalid_annotations.append("effective OpenAPI security must be a list")

    mode: AuthorityMode = "unknown"
    if isinstance(security, list) and not security:
        mode = "none"
    elif len(alternatives) == 1:
        alternative = alternatives[0]
        if alternative.anonymous:
            mode = "none"
        elif all(item.type is not None and item.scopes for item in alternative.schemes):
            mode = "scoped"
        elif all(item.type is not None for item in alternative.schemes):
            mode = "unscoped"
    return AuthInfo(
        type=auth_type,
        scopes=sorted(set(scopes)),
        source="openapi",
        mode=mode,
        explicit=explicit,
        inherited=inherited,
        alternatives=alternatives,
        invalid_annotations=invalid_annotations,
    )


def _extract_annotations(
    operation: dict[str, Any],
    *,
    operation_label: str,
    warnings: list[str],
) -> dict[str, Any]:
    annotations: dict[str, Any] = {}
    for source_key, output_key in {
        "x-readOnlyHint": "readOnlyHint",
        "x-destructiveHint": "destructiveHint",
        "x-idempotentHint": "idempotentHint",
        "x-openWorldHint": "openWorldHint",
        "x-retryPolicy": "retryPolicy",
    }.items():
        if source_key in operation:
            annotations[output_key] = operation[source_key]
    agents_shipgate = operation.get("x-agents-shipgate")
    if isinstance(agents_shipgate, dict):
        safe_annotations, rejected_binding_keys = strip_untrusted_binding_annotations(
            agents_shipgate
        )
        annotations.update(safe_annotations)
        if rejected_binding_keys:
            warnings.append(
                f"OpenAPI operation {operation_label!r} contains reserved binding "
                "annotations that were ignored: "
                f"{', '.join(rejected_binding_keys)}"
            )
    return annotations


def _resolve_schema(
    document: dict[str, Any],
    schema: dict[str, Any],
    seen_refs: set[str] | None = None,
    depth: int = 0,
    budget: list[int] | None = None,
) -> dict[str, Any]:
    if depth > MAX_SCHEMA_RESOLVE_DEPTH:
        return {"x-agents-shipgate-resolution-truncated": "max_depth"}
    if budget is None:
        budget = [0]
    budget[0] += 1
    if budget[0] > MAX_SCHEMA_RESOLVE_NODES:
        return {"x-agents-shipgate-resolution-truncated": "max_nodes"}
    seen_refs = set(seen_refs or set())
    ref = schema.get("$ref")
    if isinstance(ref, str):
        if ref in seen_refs:
            return {"$ref": ref, "x-agents-shipgate-recursive-ref": True}
        seen_refs.add(ref)
    resolved = _resolve_ref(document, schema)
    if not isinstance(resolved, dict):
        return {}
    if resolved is not schema and set(resolved) == {"$ref"}:
        # A schema that is only a reference to another: follow the alias,
        # cycles included, rather than leave a resolvable ``$ref`` in place of
        # its schema. A target with keywords beside its ``$ref`` is kept whole.
        return _resolve_schema(document, resolved, seen_refs, depth + 1, budget)
    resolved = deepcopy(resolved)

    def child(value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return _resolve_schema(document, value, seen_refs.copy(), depth + 1, budget)

    if "properties" in resolved and isinstance(resolved["properties"], dict):
        resolved["properties"] = {
            name: child(prop) for name, prop in resolved["properties"].items()
        }
    if "items" in resolved and isinstance(resolved["items"], dict):
        resolved["items"] = child(resolved["items"])
    for combinator in ("oneOf", "anyOf", "allOf"):
        if combinator in resolved and isinstance(resolved[combinator], list):
            resolved[combinator] = [child(item) for item in resolved[combinator]]
    # Read after the keywords above so a schema that uses only those resolves,
    # and is truncated, exactly as before these were read.
    if isinstance(resolved.get("patternProperties"), dict):
        resolved["patternProperties"] = {
            pattern: child(prop) for pattern, prop in resolved["patternProperties"].items()
        }
    for keyword in ("additionalProperties", "not"):
        if isinstance(resolved.get(keyword), dict):
            resolved[keyword] = child(resolved[keyword])
    # ``items`` as a list is the pre-2020-12 tuple form of ``prefixItems``.
    for keyword in ("prefixItems", "items"):
        if isinstance(resolved.get(keyword), list):
            resolved[keyword] = [child(item) for item in resolved[keyword]]
    return resolved


def _local_ref_pointer(ref: str) -> str | None:
    """Decode a local URI fragment in JSON Pointer form."""

    if not ref.startswith("#") or re.search(r"%(?![0-9a-fA-F]{2})", ref):
        return None
    try:
        pointer = unquote(ref[1:], errors="strict")
    except UnicodeDecodeError:
        return None
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        return None
    return pointer


def _pointer_target(document: dict[str, Any], pointer: str) -> Any:
    current: Any = document
    for part in pointer[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif (
            isinstance(current, list)
            and len(part) <= len(str(len(current)))
            and re.fullmatch(r"0|[1-9][0-9]*", part)
            and int(part) < len(current)
        ):
            current = current[int(part)]
        else:
            return _MISSING
    return current


def _local_ref_target(document: dict[str, Any], ref: str) -> Any:
    pointer = _local_ref_pointer(ref)
    return _MISSING if pointer is None else _pointer_target(document, pointer)


def _resolve_ref(document: dict[str, Any], value: dict[str, Any]) -> Any:
    ref = value.get("$ref")
    if not isinstance(ref, str):
        return value
    target = _local_ref_target(document, ref)
    return value if target is _MISSING else target


def _resolve_carrier(
    document: dict[str, Any],
    value: Any,
    pointer: str,
    expected_fields: tuple[str, ...],
    warnings: list[str],
) -> tuple[dict[str, Any], str]:
    """Read a local Reference Object and give the pointer of its resolved object."""

    if not isinstance(value, dict):
        return {}, pointer
    if "$ref" not in value:
        return value, pointer
    visited = {id(value)}
    current = value
    ref = current["$ref"]
    for _ in range(MAX_SCHEMA_RESOLVE_DEPTH):
        ref = current["$ref"]
        if not isinstance(ref, str):
            why = "the reference value must be a string"
            break
        if not ref.startswith("#"):
            why = "references outside this document are unread"
            break
        target_pointer = _local_ref_pointer(ref)
        if target_pointer is None:
            why = "the local fragment is not a supported JSON Pointer"
            break
        target = _pointer_target(document, target_pointer)
        if target is _MISSING:
            why = "the pointer names no value"
            break
        if not isinstance(target, dict):
            why = "the pointer does not name an object"
            break
        if "$ref" not in target:
            if not expected_fields or any(field in target for field in expected_fields):
                return target, target_pointer
            why = "the object does not have the expected carrier fields"
            break
        if id(target) in visited:
            why = "the references form a cycle"
            break
        visited.add(id(target))
        current = target
    else:
        why = f"the chain exceeds {MAX_SCHEMA_RESOLVE_DEPTH} references"
    warnings.append(f"Unresolved OpenAPI $ref at {pointer}/$ref. Reference {ref!r}: {why}.")
    return {}, pointer


def _unresolved_refs(document: dict[str, Any], value: Any) -> list[str]:
    """The ``$ref`` values in a resolved schema this document cannot resolve.

    Only a reference that leaves the document or names nothing in it is
    unresolved. That excludes the ``x-agents-shipgate-recursive-ref`` marker the
    resolver writes where a schema refers back to itself, whose ``$ref`` it has
    already resolved once, and a local reference left in place under a keyword
    the resolver does not descend into. The marker key itself is not trusted:
    the document can spell it too, so the reference is tested either way.
    """

    refs: list[str] = []
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str) and _local_ref_target(document, ref) is _MISSING:
            refs.append(ref)
        for child in value.values():
            refs.extend(_unresolved_refs(document, child))
    elif isinstance(value, list):
        for child in value:
            refs.extend(_unresolved_refs(document, child))
    return refs


def _operation_name(method: str, path: str) -> str:
    safe_path = path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
    safe_path = safe_path.replace("-", "_") or "root"
    return f"{method}_{safe_path}"


class OpenAPIAdapter:
    """``ToolSourceAdapter`` wrapping :func:`load_openapi_tools`."""

    source_type: ClassVar[str] = "openapi"
    scope: ClassVar[Literal["per_source", "per_scan"]] = "per_source"
    artifact_class: ClassVar[type | None] = None

    coverage: ClassVar[SourceCoverage] = SourceCoverage(
        adapter="openapi",
        label="OpenAPI document",
        reads=(
            "An OpenAPI 3.x document; every operation becomes one action."
        ),
        cells=(
            BoundaryCell(
                shape="export_artifact",
                status="extracted",
                reads=(
                    "Each `paths` operation with its parameters, request body, and "
                    "declared security, including the operations of a path item "
                    "whose `$ref` names another path item in the same document. A "
                    "path item whose `$ref` leaves the document or does not resolve "
                    "is recorded as an omission with a source warning, since its "
                    "operations are not read; a schema `$ref` the document does not "
                    "resolve is reported as a source warning against an action that "
                    "is still read."
                ),
                emits=("openapi",),
                ceiling="high",
            ),
            BoundaryCell(
                shape="literal_registration",
                status="not_applicable",
                reads="An OpenAPI document is a contract file, not source.",
            ),
            BoundaryCell(
                shape="factory",
                status="not_applicable",
                reads="Nothing in an OpenAPI document constructs operations.",
            ),
            BoundaryCell(
                shape="dynamic_construction",
                status="not_applicable",
                reads=(
                    "An operation is present in the document or it is not; there is "
                    "no expression form to be unable to resolve."
                ),
            ),
        ),
    )

    def load(
        self,
        source: ToolSourceConfig | None,
        base_dir: Path,
        manifest: AgentsShipgateManifest,
    ) -> LoadedAdapterResult:
        assert source is not None, "per_source adapter requires a source"
        return LoadedAdapterResult(tool_sources=[load_openapi_tools(source, base_dir)])
