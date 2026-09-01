"""Bounded static extraction for named MCP source-code idioms.

The registry in this module is deliberately closed and versioned.  A manifest
may select one of these recognizers by id; it cannot provide a regular
expression or otherwise teach the scanner a new grammar from the same change
being reviewed.  Recognizers read source text only.  They never import,
compile, or execute the server under review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from agents_shipgate.core.domain import (
    LoadedToolSource,
    SourceSurfaceOmission,
    Tool,
    ToolRiskHint,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.static_inputs import read_static_input_text
from agents_shipgate.inputs.common import (
    manifest_relative_path,
    resolve_input_path,
    stable_tool_id,
    tool_name_warning,
    walk_input_tree,
)
from agents_shipgate.schemas.manifest import ToolSourceConfig
from agents_shipgate.schemas.manifest.tool_sources import (
    GO_ADDTOOL_V1,
    GO_MUSTTOOL_V1,
    MCP_CODE_IDIOM_IDS,
    MCP_CODE_IDIOM_REGISTRY_VERSION,
    TYPESCRIPT_MCP_SDK_V1,
    TYPESCRIPT_STATIC_TOOL_V1,
)

MAX_MCP_CODE_FILES = 5_000
MAX_MCP_CODE_FILE_BYTES = 1 * 1024 * 1024
MAX_MCP_CODE_TOTAL_BYTES = 32 * 1024 * 1024

_TS_SUFFIXES = frozenset({".ts", ".tsx", ".js", ".jsx", ".mts", ".cts"})
_GO_SUFFIXES = frozenset({".go"})
_SKIP_PARTS = frozenset(
    {
        ".git",
        "__fixtures__",
        "fixtures",
        "node_modules",
        "test",
        "tests",
        "testdata",
        "vendor",
    }
)
_MCP_MODULE_RE = re.compile(r"(?:^|[^a-z0-9])mcp(?:[^a-z0-9]|$)", re.IGNORECASE)


@dataclass(frozen=True)
class McpCodeIdiom:
    id: str
    language: Literal["typescript", "go"]
    suffixes: frozenset[str]
    definition_only: bool


MCP_CODE_IDIOMS: dict[str, McpCodeIdiom] = {
    TYPESCRIPT_MCP_SDK_V1: McpCodeIdiom(
        id=TYPESCRIPT_MCP_SDK_V1,
        language="typescript",
        suffixes=_TS_SUFFIXES,
        definition_only=False,
    ),
    TYPESCRIPT_STATIC_TOOL_V1: McpCodeIdiom(
        id=TYPESCRIPT_STATIC_TOOL_V1,
        language="typescript",
        suffixes=_TS_SUFFIXES,
        definition_only=True,
    ),
    GO_MUSTTOOL_V1: McpCodeIdiom(
        id=GO_MUSTTOOL_V1,
        language="go",
        suffixes=_GO_SUFFIXES,
        definition_only=False,
    ),
    GO_ADDTOOL_V1: McpCodeIdiom(
        id=GO_ADDTOOL_V1,
        language="go",
        suffixes=_GO_SUFFIXES,
        definition_only=False,
    ),
}


@dataclass(frozen=True)
class McpCodeObservation:
    name: str
    path: Path
    line: int
    column: int
    description: str | None = None
    operation_type: str | None = None
    annotations: dict[str, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class McpCodeOmission:
    path: Path
    line: int | None
    reason: str
    detail: str


@dataclass(frozen=True)
class McpCodeScan:
    idiom: str
    observations: tuple[McpCodeObservation, ...] = ()
    omissions: tuple[McpCodeOmission, ...] = ()
    files_considered: int = 0


@dataclass(frozen=True)
class _Token:
    kind: Literal["ident", "string", "punct"]
    value: str
    line: int
    column: int


@dataclass(frozen=True)
class _GoMustToolDefinition:
    identifier: str
    path: Path
    name: _Token | None
    site: _Token
    description: str | None
    annotations: dict[str, bool]


def validate_mcp_code_idiom(value: str) -> str:
    """Return a known immutable idiom id or raise a routable parse error."""

    if value not in MCP_CODE_IDIOMS:
        raise InputParseError(
            f"Unknown MCP code idiom {value!r}; expected one of "
            + ", ".join(MCP_CODE_IDIOM_IDS)
        )
    return value


def scan_mcp_code_idiom(
    path: Path,
    idiom_id: str,
    *,
    files: list[Path] | None = None,
) -> McpCodeScan:
    """Scan one file/tree with one named grammar, deterministically and bounded.

    ``files`` is discovery's already-bounded repository inventory.  The
    adapter omits it and walks the identity-bound declared input tree instead.
    """

    idiom = MCP_CODE_IDIOMS[validate_mcp_code_idiom(idiom_id)]
    candidates = _candidate_source_files(path, idiom, files=files)
    omissions: list[McpCodeOmission] = []
    if len(candidates) > MAX_MCP_CODE_FILES:
        omitted = len(candidates) - MAX_MCP_CODE_FILES
        omissions.append(
            McpCodeOmission(
                path=path,
                line=None,
                reason="source_file_cap",
                detail=(
                    f"The {idiom.id} scan stopped at {MAX_MCP_CODE_FILES} source "
                    f"files and left {omitted} file(s) unread."
                ),
            )
        )
        candidates = candidates[:MAX_MCP_CODE_FILES]

    observations: list[McpCodeObservation] = []
    tokenized: list[tuple[Path, list[_Token]]] = []
    bytes_scheduled = 0
    for candidate in candidates:
        try:
            file_bytes = candidate.stat().st_size
        except OSError as exc:
            omissions.append(
                McpCodeOmission(
                    path=candidate,
                    line=None,
                    reason="unreadable_source_file",
                    detail=f"The source file could not be inspected statically: {exc}",
                )
            )
            continue
        if file_bytes > MAX_MCP_CODE_FILE_BYTES:
            omissions.append(
                McpCodeOmission(
                    path=candidate,
                    line=None,
                    reason="source_file_byte_cap",
                    detail=(
                        "The source file was not read because its size exceeds the "
                        f"per-file limit of {MAX_MCP_CODE_FILE_BYTES} bytes."
                    ),
                )
            )
            continue
        if bytes_scheduled + file_bytes > MAX_MCP_CODE_TOTAL_BYTES:
            omissions.append(
                McpCodeOmission(
                    path=path,
                    line=None,
                    reason="source_byte_cap",
                    detail=(
                        "The idiom scan stopped before reading another source file "
                        f"because its aggregate byte limit is {MAX_MCP_CODE_TOTAL_BYTES}."
                    ),
                )
            )
            break
        bytes_scheduled += file_bytes
        try:
            text = read_static_input_text(
                candidate, max_bytes=MAX_MCP_CODE_FILE_BYTES
            )
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            omissions.append(
                McpCodeOmission(
                    path=candidate,
                    line=None,
                    reason="unreadable_source_file",
                    detail=f"The source file could not be read statically: {exc}",
                )
            )
            continue
        tokens = _tokens(text, language=idiom.language)
        if idiom.id == GO_MUSTTOOL_V1:
            tokenized.append((candidate, tokens))
        else:
            found, dropped = _recognize(tokens, candidate, idiom.id)
            observations.extend(found)
            omissions.extend(dropped)

    if idiom.id == GO_MUSTTOOL_V1:
        found, dropped = _go_musttool_registrations(tokenized)
        observations.extend(found)
        omissions.extend(dropped)

    observations.sort(key=lambda row: (row.path.as_posix(), row.line, row.column, row.name))
    omissions.sort(
        key=lambda row: (row.path.as_posix(), row.line or 0, row.reason, row.detail)
    )
    return McpCodeScan(
        idiom=idiom.id,
        observations=tuple(observations),
        omissions=tuple(omissions),
        files_considered=len(candidates),
    )


def load_mcp_code_tools(source: ToolSourceConfig, base_dir: Path) -> LoadedToolSource:
    """Normalize one configured idiom scan into the existing MCP tool model."""

    assert source.path is not None
    assert source.idiom is not None
    root = resolve_input_path(base_dir, source.path)
    scan = scan_mcp_code_idiom(root, source.idiom)
    warnings: list[str] = []
    omissions: list[SourceSurfaceOmission] = []
    for row in scan.omissions:
        display = manifest_relative_path(str(row.path), base_dir)
        location = f"{display}:{row.line}" if row.line is not None else display
        warning = f"MCP code idiom {source.idiom} omitted {location}: {row.detail}"
        warnings.append(warning)
        omissions.append(
            SourceSurfaceOmission(
                subject=location,
                reason=row.reason,
                detail=row.detail,
                warning=warning,
            )
        )

    idiom = MCP_CODE_IDIOMS[source.idiom]
    definition_gap = idiom.definition_only
    if definition_gap:
        detail = (
            f"MCP code idiom {source.idiom} enumerates tool definitions but "
            "does not prove which definitions are registered at runtime"
        )
        warnings.append(detail)
        omissions.append(
            SourceSurfaceOmission(
                subject=source.path,
                reason="definition_only_runtime_binding",
                detail=(
                    "The recognizer enumerates definitions, but runtime registration "
                    "cannot be established from this source shape."
                ),
                warning=detail,
            )
        )
    surface = "partial" if omissions or definition_gap else "enumerated"
    tools: list[Tool] = []
    seen_names: set[str] = set()
    for row in scan.observations:
        if row.name in seen_names:
            warnings.append(
                f"Duplicate MCP tool name {row.name!r} in source {source.id!r}"
            )
        seen_names.add(row.name)
        if warning := tool_name_warning(row.name):
            warnings.append(warning)
        actual_path = manifest_relative_path(str(row.path), base_dir)
        extraction = {
            "method": "mcp_code_idiom",
            "confidence": "medium",
            "registry_version": MCP_CODE_IDIOM_REGISTRY_VERSION,
            "idiom": source.idiom,
            "surface": surface,
            "tool_set_proven": not omissions and not definition_gap,
        }
        surface_gaps = {
            f"{manifest_relative_path(str(item.path), base_dir)}:"
            f"{item.line or 0}:{item.reason}"
            for item in scan.omissions
        }
        if definition_gap:
            surface_gaps.add("definition_only_runtime_binding")
        if surface_gaps:
            extraction["surface_gaps"] = sorted(surface_gaps)
        annotations: dict[str, object] = dict(row.annotations)
        if row.operation_type is not None:
            annotations["operationType"] = row.operation_type
        tools.append(
            Tool(
                id=stable_tool_id(row.name),
                name=row.name,
                description=row.description,
                source_type="mcp",
                source_id=source.id,
                source_ref=source.path,
                source_path=actual_path,
                source_start_line=row.line,
                source_start_column=row.column,
                source_pointer=f"/{source.idiom}/{actual_path}:{row.line}",
                annotations=annotations,
                risk_hints=_operation_risk_hints(row.operation_type),
                extraction_confidence="medium",
                extraction=extraction,
            )
        )
    return LoadedToolSource(
        source_id=source.id,
        source_type="mcp",
        tools=tools,
        warnings=warnings,
        omissions=omissions,
    )


def _operation_risk_hints(operation_type: str | None) -> list[ToolRiskHint]:
    if operation_type is None:
        return []
    tags: tuple[str, ...]
    if operation_type in {"metadata", "read"}:
        tags = ("read_only",)
    elif operation_type == "delete":
        tags = ("destructive", "write")
    elif operation_type in {"create", "update"}:
        tags = ("write",)
    else:
        return []
    return [
        ToolRiskHint(
            tag=tag,
            source="mcp_code_operation_type",
            confidence="medium",
            basis="typed_provider_fact",
            evidence={"operationType": operation_type},
        )
        for tag in tags
    ]


def _candidate_source_files(
    path: Path,
    idiom: McpCodeIdiom,
    *,
    files: list[Path] | None,
) -> list[Path]:
    if path.is_file():
        candidates = [path]
    elif files is None:
        if not path.is_dir():
            raise InputParseError(f"MCP code idiom source must be a file or directory: {path}")
        candidates = [item for item in walk_input_tree(path) if item.is_file()]
    else:
        try:
            root = path.resolve()
        except (OSError, RuntimeError):
            return []
        candidates = []
        for item in files:
            try:
                item.resolve().relative_to(root)
            except (OSError, RuntimeError, ValueError):
                continue
            candidates.append(item)
    return sorted(
        (
            item
            for item in candidates
            if item.suffix.lower() in idiom.suffixes and not _test_or_vendor_path(item)
        ),
        key=lambda item: item.as_posix(),
    )


def _test_or_vendor_path(path: Path) -> bool:
    parts = path.parts
    if any(part.lower() in _SKIP_PARTS for part in parts[:-1]):
        return True
    name = path.name.lower()
    return bool(
        name.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx", "_test.go"))
    )


def is_mcp_code_candidate(path: Path) -> bool:
    return (
        path.suffix.lower() in (_TS_SUFFIXES | _GO_SUFFIXES)
        and not _test_or_vendor_path(path)
    )


def has_mcp_source_import_marker(path: Path, text: str) -> bool:
    """Whether source contains an MCP-named import/module declaration.

    Detection uses this as evidence independent from the call shape. Comments,
    documentation strings, and arbitrary runtime string values do not count.
    Explicit manifest idioms do not need this discovery-only guard.
    """

    language: Literal["typescript", "go"] = (
        "go" if path.suffix.lower() in _GO_SUFFIXES else "typescript"
    )
    tokens = _tokens(text, language=language)
    for index, token in enumerate(tokens):
        if (
            language == "go"
            and token.kind == "ident"
            and token.value == "package"
            and index + 1 < len(tokens)
            and tokens[index + 1].kind == "ident"
            and _mcp_receiver(tokens[index + 1].value)
        ):
            return True
        if token.kind != "ident" or token.value not in {"import", "require"}:
            continue
        end = min(len(tokens), index + 64)
        if index + 1 < len(tokens) and tokens[index + 1].value == "(":
            matched = _matching(tokens, index + 1, "(", ")")
            if matched is not None:
                end = matched
        else:
            semicolon = next(
                (
                    cursor
                    for cursor in range(index + 1, end)
                    if tokens[cursor].value == ";"
                ),
                None,
            )
            if semicolon is not None:
                end = semicolon
        if any(
            candidate.kind == "string" and _mcp_module_literal(candidate.value)
            for candidate in tokens[index + 1 : end]
        ):
            return True
    return False


def _mcp_module_literal(value: str) -> bool:
    folded = value.lower()
    return "modelcontextprotocol" in folded or _MCP_MODULE_RE.search(folded) is not None


def _recognize(
    tokens: list[_Token], path: Path, idiom: str
) -> tuple[list[McpCodeObservation], list[McpCodeOmission]]:
    if idiom == TYPESCRIPT_MCP_SDK_V1:
        return _typescript_sdk(tokens, path)
    if idiom == TYPESCRIPT_STATIC_TOOL_V1:
        return _typescript_static_tools(tokens, path)
    if idiom == GO_MUSTTOOL_V1:
        return _go_musttool(tokens, path)
    return _go_addtool(tokens, path)


def _typescript_sdk(
    tokens: list[_Token], path: Path
) -> tuple[list[McpCodeObservation], list[McpCodeOmission]]:
    observations: list[McpCodeObservation] = []
    omissions: list[McpCodeOmission] = []
    for index in range(2, len(tokens) - 1):
        method = tokens[index]
        if method.kind != "ident" or method.value not in {"tool", "registerTool"}:
            continue
        if tokens[index - 1].value != "." or tokens[index + 1].value != "(":
            continue
        receiver = tokens[index - 2]
        if receiver.kind != "ident" or not _server_receiver(receiver.value):
            continue
        end = _matching(tokens, index + 1, "(", ")")
        if end is None:
            omissions.append(
                McpCodeOmission(
                    path=path,
                    line=method.line,
                    reason="structural_parse_gap",
                    detail=(
                        f"{receiver.value}.{method.value} has no statically balanced "
                        "closing parenthesis, so its registration was not enumerated."
                    ),
                )
            )
            continue
        args = _top_level_args(tokens, index + 2, end)
        first = _sole_string_token(tokens, args[0]) if args else None
        if first is None:
            omissions.append(
                _dynamic_name_omission(path, method, f"{receiver.value}.{method.value}")
            )
            continue
        description: str | None = None
        annotations: dict[str, bool] = {}
        if method.value == "registerTool" and len(args) >= 2:
            description = _named_string(tokens, args[1], "description")
            annotations = _annotation_bools(tokens, args[1])
        elif len(args) >= 2:
            second = _first_token(tokens, args[1])
            if second is not None and second.kind == "string":
                description = second.value
        observations.append(
            McpCodeObservation(
                name=first.value,
                path=path,
                line=first.line,
                column=first.column,
                description=description,
                annotations=annotations,
            )
        )
    return observations, omissions


def _typescript_static_tools(
    tokens: list[_Token], path: Path
) -> tuple[list[McpCodeObservation], list[McpCodeOmission]]:
    observations: list[McpCodeObservation] = []
    omissions: list[McpCodeOmission] = []
    bodies, unbalanced_classes = _class_bodies(tokens)
    omissions.extend(
        McpCodeOmission(
            path=path,
            line=site.line,
            reason="structural_parse_gap",
            detail=(
                "A TypeScript class body could not be balanced statically, so any "
                "tool definition in that class was not enumerated."
            ),
        )
        for site in unbalanced_classes
    )
    for start, end in bodies:
        name_site: _Token | None = None
        name_value: _Token | None = None
        operation: str | None = None
        description: str | None = None
        index = start + 1
        depth = 0
        while index < end:
            token = tokens[index]
            if token.value in {"{", "(", "["}:
                depth += 1
            elif token.value in {"}", ")", "]"}:
                depth = max(0, depth - 1)
            if depth == 0 and token.kind == "ident":
                if token.value == "toolName" and _has_static_prefix(tokens, index, start):
                    value = _property_literal_initializer(tokens, index, end)
                    name_site = token
                    name_value = value
                elif token.value == "operationType" and _has_static_prefix(tokens, index, start):
                    value = _property_literal_initializer(tokens, index, end)
                    if value is not None:
                        operation = value.value
                elif token.value == "description":
                    value = _property_literal_initializer(tokens, index, end)
                    if value is not None:
                        description = value.value
            index += 1
        if name_site is None:
            continue
        if name_value is None:
            omissions.append(_dynamic_name_omission(path, name_site, "static toolName"))
            continue
        observations.append(
            McpCodeObservation(
                name=name_value.value,
                path=path,
                line=name_value.line,
                column=name_value.column,
                description=description,
                operation_type=operation,
            )
        )
    return observations, omissions


def _go_musttool(
    tokens: list[_Token], path: Path
) -> tuple[list[McpCodeObservation], list[McpCodeOmission]]:
    return _go_musttool_registrations([(path, tokens)])


def _go_musttool_registrations(
    tokenized: list[tuple[Path, list[_Token]]],
) -> tuple[list[McpCodeObservation], list[McpCodeOmission]]:
    """Link static ``MustTool`` definitions to ``Identifier.Register(mcp)``.

    Grafana keeps a larger catalog of definitions than it mounts in a given
    server.  Emitting definitions therefore overstates the runtime surface.
    Registry v1 keys definitions by Go package directory and emits one
    observation per statically linked registration.  A registration whose
    receiver cannot be linked is retained as an omission rather than silently
    disappearing from the surface account.
    """

    definitions: dict[tuple[Path, str], list[_GoMustToolDefinition]] = {}
    for path, tokens in tokenized:
        for definition in _go_musttool_definitions(tokens, path):
            definitions.setdefault((path.parent, definition.identifier), []).append(
                definition
            )

    observations: list[McpCodeObservation] = []
    omissions: list[McpCodeOmission] = []
    for path, tokens in tokenized:
        for identifier, site in _go_static_mcp_registrations(tokens):
            if identifier is None:
                omissions.append(
                    McpCodeOmission(
                        path=path,
                        line=site.line,
                        reason="dynamic_tool_registration",
                        detail=(
                            "Register(mcp) uses a non-identifier receiver, so its "
                            "MustTool definition could not be linked statically."
                        ),
                    )
                )
                continue
            matches = definitions.get((path.parent, identifier), [])
            if not matches:
                omissions.append(
                    McpCodeOmission(
                        path=path,
                        line=site.line,
                        reason="unresolved_tool_registration",
                        detail=(
                            f"{identifier}.Register(mcp) has no statically linked "
                            "MCP-qualified MustTool definition in this Go package."
                        ),
                    )
                )
                continue
            if len(matches) != 1:
                omissions.append(
                    McpCodeOmission(
                        path=path,
                        line=site.line,
                        reason="ambiguous_tool_registration",
                        detail=(
                            f"{identifier}.Register(mcp) matches {len(matches)} "
                            "MCP-qualified MustTool definitions in this Go package."
                        ),
                    )
                )
                continue
            definition = matches[0]
            if definition.name is None:
                omissions.append(
                    _dynamic_name_omission(
                        definition.path,
                        definition.site,
                        f"{identifier}.Register(mcp) MustTool definition",
                    )
                )
                continue
            observations.append(
                McpCodeObservation(
                    name=definition.name.value,
                    path=definition.path,
                    line=definition.name.line,
                    column=definition.name.column,
                    description=definition.description,
                    annotations=definition.annotations,
                )
            )
    return observations, omissions


def _go_musttool_definitions(
    tokens: list[_Token], path: Path
) -> list[_GoMustToolDefinition]:
    definitions: list[_GoMustToolDefinition] = []
    for index, token in enumerate(tokens[:-1]):
        if token.kind != "ident" or token.value != "MustTool" or tokens[index + 1].value != "(":
            continue
        if (
            index < 2
            or tokens[index - 1].value != "."
            or tokens[index - 2].kind != "ident"
            or not _mcp_receiver(tokens[index - 2].value)
        ):
            continue
        identifier = _go_var_assignment_identifier(tokens, index - 2)
        if identifier is None:
            # v1 intentionally recognizes the measured declaration form only:
            # `var Identifier = <mcp-qualified>.MustTool(...)`.
            continue
        end = _matching(tokens, index + 1, "(", ")")
        if end is None:
            continue
        args = _top_level_args(tokens, index + 2, end)
        first = _sole_string_token(tokens, args[0]) if args else None
        description = None
        if len(args) > 1:
            second = _first_token(tokens, args[1])
            if second is not None and second.kind == "string":
                description = second.value
        definitions.append(
            _GoMustToolDefinition(
                identifier=identifier,
                path=path,
                name=first,
                site=token,
                description=description,
                annotations=_annotation_bools(tokens, (index + 2, end)),
            )
        )
    return definitions


def _go_var_assignment_identifier(tokens: list[_Token], receiver_index: int) -> str | None:
    # `var Identifier = receiver.MustTool(...)`; keeping the declaration shape
    # closed avoids treating arbitrary field assignments as catalog entries.
    if receiver_index < 3 or tokens[receiver_index - 1].value != "=":
        return None
    identifier = tokens[receiver_index - 2]
    introducer = tokens[receiver_index - 3]
    if (
        identifier.kind == "ident"
        and introducer.kind == "ident"
        and introducer.value == "var"
    ):
        return identifier.value
    return None


def _go_static_mcp_registrations(
    tokens: list[_Token],
) -> list[tuple[str | None, _Token]]:
    registrations: list[tuple[str | None, _Token]] = []
    for index, token in enumerate(tokens[:-1]):
        if token.kind != "ident" or token.value != "Register":
            continue
        if index < 1 or tokens[index - 1].value != "." or tokens[index + 1].value != "(":
            continue
        end = _matching(tokens, index + 1, "(", ")")
        if end is None:
            continue
        args = _top_level_args(tokens, index + 2, end)
        if len(args) != 1 or not _sole_ident_token(tokens, args[0], "mcp"):
            continue
        receiver = tokens[index - 2] if index >= 2 else None
        registrations.append(
            (receiver.value if receiver is not None and receiver.kind == "ident" else None, token)
        )
    return registrations


def _go_addtool(
    tokens: list[_Token], path: Path
) -> tuple[list[McpCodeObservation], list[McpCodeOmission]]:
    observations: list[McpCodeObservation] = []
    omissions: list[McpCodeOmission] = []
    for index, token in enumerate(tokens[:-1]):
        if token.kind != "ident" or token.value != "AddTool" or tokens[index + 1].value != "(":
            continue
        end = _matching(tokens, index + 1, "(", ")")
        if end is None:
            continue
        args = _top_level_args(tokens, index + 2, end)
        qualified_mcp = (
            index >= 2
            and tokens[index - 1].value == "."
            and tokens[index - 2].value == "mcp"
        )
        # The Go SDK exposes both `mcp.AddTool(server, tool, handler)` and
        # `server.AddTool(tool, handler)`. A server variable is often itself
        # named `mcp`, so arity distinguishes the package function from that
        # method receiver before choosing which argument holds the tool.
        is_package_call = qualified_mcp and len(args) >= 3
        tool_arg_index = 1 if is_package_call else 0
        if len(args) <= tool_arg_index:
            continue
        tool_range = args[tool_arg_index]
        if (
            not is_package_call
            and not _contains_mcp_tool_constructor(tokens, tool_range)
        ):
            # `AddTool` is common outside MCP. A method call activates this
            # idiom only when its tool argument is visibly constructed by an
            # MCP package; the package-level `mcp.AddTool` form is anchored by
            # the callee itself.
            continue
        name = _named_string(tokens, tool_range, "Name")
        if name is None:
            name = _call_first_string(tokens, tool_range, "NewTool")
        if name is None:
            omissions.append(_dynamic_name_omission(path, token, "AddTool"))
            continue
        name_token = _named_string_token(tokens, tool_range, "Name") or _call_first_string_token(
            tokens, tool_range, "NewTool"
        )
        assert name_token is not None
        observations.append(
            McpCodeObservation(
                name=name,
                path=path,
                line=name_token.line,
                column=name_token.column,
                description=_named_string(tokens, tool_range, "Description"),
                annotations=_annotation_bools(tokens, tool_range),
            )
        )
    return observations, omissions


def _server_receiver(value: str) -> bool:
    folded = value.replace("_", "").lower()
    return folded == "mcp" or folded.endswith("server")


def _mcp_receiver(value: str) -> bool:
    folded = value.replace("_", "").lower()
    return folded == "mcp" or folded.startswith("mcp")


def _contains_mcp_tool_constructor(
    tokens: list[_Token], bounds: tuple[int, int]
) -> bool:
    start, end = bounds
    return any(
        tokens[index].value == "mcp"
        and tokens[index + 1].value == "."
        and tokens[index + 2].value in {"NewTool", "Tool"}
        for index in range(start, max(start, end - 2))
    )


def _dynamic_name_omission(path: Path, token: _Token, shape: str) -> McpCodeOmission:
    return McpCodeOmission(
        path=path,
        line=token.line,
        reason="dynamic_tool_name",
        detail=(
            f"{shape} does not use a static string literal for the tool name, "
            "so that registration was not enumerated."
        ),
    )


def _class_bodies(
    tokens: list[_Token],
) -> tuple[list[tuple[int, int]], list[_Token]]:
    bodies: list[tuple[int, int]] = []
    unbalanced: list[_Token] = []
    for index, token in enumerate(tokens):
        if token.kind != "ident" or token.value != "class":
            continue
        brace = next((i for i in range(index + 1, len(tokens)) if tokens[i].value == "{"), None)
        if brace is None:
            unbalanced.append(token)
            continue
        end = _matching(tokens, brace, "{", "}")
        if end is not None:
            bodies.append((brace, end))
        else:
            unbalanced.append(token)
    return bodies, unbalanced


def _has_static_prefix(tokens: list[_Token], index: int, start: int) -> bool:
    # A class field's modifiers precede its name on the same source line in
    # the v1 grammar. Looking through a semicolon/newline let an unrelated
    # `static unused; toolName = "x"` donate its modifier to toolName.
    for pos in range(index - 1, start, -1):
        token = tokens[pos]
        if token.line != tokens[index].line or token.value in {";", "{", "}"}:
            return False
        if token.kind == "ident" and token.value == "static":
            return True
    return False


def _property_literal_initializer(
    tokens: list[_Token], index: int, end: int
) -> _Token | None:
    pos = index + 1
    while pos < min(end, index + 12) and tokens[pos].value not in {"=", ";", "{"}:
        pos += 1
    if pos >= end or tokens[pos].value != "=":
        return None
    expression_end = next(
        (
            cursor
            for cursor in range(pos + 1, end)
            if tokens[cursor].value == ";"
        ),
        end,
    )
    return _sole_string_token(tokens, (pos + 1, expression_end))


def _matching(
    tokens: list[_Token], start: int, opening: str, closing: str
) -> int | None:
    depth = 0
    for index in range(start, len(tokens)):
        if tokens[index].value == opening:
            depth += 1
        elif tokens[index].value == closing:
            depth -= 1
            if depth == 0:
                return index
    return None


def _top_level_args(
    tokens: list[_Token], start: int, end: int
) -> list[tuple[int, int]]:
    args: list[tuple[int, int]] = []
    depth = 0
    arg_start = start
    for index in range(start, end):
        value = tokens[index].value
        if value in {"(", "{", "["}:
            depth += 1
        elif value in {")", "}", "]"}:
            depth -= 1
        elif value == "," and depth == 0:
            args.append((arg_start, index))
            arg_start = index + 1
    if arg_start < end:
        args.append((arg_start, end))
    return args


def _first_token(tokens: list[_Token], bounds: tuple[int, int]) -> _Token | None:
    start, end = bounds
    return tokens[start] if start < end else None


def _sole_string_token(
    tokens: list[_Token], bounds: tuple[int, int]
) -> _Token | None:
    start, end = bounds
    if end - start != 1:
        return None
    token = tokens[start]
    return token if token.kind == "string" else None


def _sole_ident_token(
    tokens: list[_Token], bounds: tuple[int, int], value: str
) -> bool:
    start, end = bounds
    return end - start == 1 and tokens[start].kind == "ident" and tokens[start].value == value


def _named_string(
    tokens: list[_Token], bounds: tuple[int, int], name: str
) -> str | None:
    token = _named_string_token(tokens, bounds, name)
    return token.value if token is not None else None


def _named_string_token(
    tokens: list[_Token], bounds: tuple[int, int], name: str
) -> _Token | None:
    start, end = bounds
    outer = next(
        (index for index in range(start, end) if tokens[index].value == "{"),
        None,
    )
    if outer is None:
        return None
    brace_depth = 0
    for index in range(outer, end - 2):
        if tokens[index].value == "{":
            brace_depth += 1
            continue
        if tokens[index].value == "}":
            brace_depth -= 1
            continue
        if (
            brace_depth == 1
            and tokens[index].value == name
            and tokens[index + 1].value == ":"
        ):
            value_end = _field_value_end(tokens, index + 2, end)
            return _sole_string_token(tokens, (index + 2, value_end))
    return None


def _field_value_end(tokens: list[_Token], start: int, end: int) -> int:
    depth = 0
    for index in range(start, end):
        value = tokens[index].value
        if value in {"(", "{", "["}:
            depth += 1
        elif value in {")",
            "}",
            "]",
        }:
            if depth == 0:
                return index
            depth -= 1
        elif value == "," and depth == 0:
            return index
    return end


def _call_first_string(
    tokens: list[_Token], bounds: tuple[int, int], call_name: str
) -> str | None:
    token = _call_first_string_token(tokens, bounds, call_name)
    return token.value if token is not None else None


def _call_first_string_token(
    tokens: list[_Token], bounds: tuple[int, int], call_name: str
) -> _Token | None:
    start, end = bounds
    for index in range(start, end - 1):
        if tokens[index].value == call_name and tokens[index + 1].value == "(":
            close = _matching(tokens, index + 1, "(", ")")
            if close is None or close > end:
                continue
            args = _top_level_args(tokens, index + 2, close)
            if args:
                return _sole_string_token(tokens, args[0])
    return None


def _annotation_bools(
    tokens: list[_Token], bounds: tuple[int, int]
) -> dict[str, bool]:
    start, end = bounds
    aliases = {
        "readOnlyHint": "readOnlyHint",
        "ReadOnlyHint": "readOnlyHint",
        "WithReadOnlyHintAnnotation": "readOnlyHint",
        "destructiveHint": "destructiveHint",
        "DestructiveHint": "destructiveHint",
        "WithDestructiveHintAnnotation": "destructiveHint",
        "idempotentHint": "idempotentHint",
        "IdempotentHint": "idempotentHint",
        "WithIdempotentHintAnnotation": "idempotentHint",
        "openWorldHint": "openWorldHint",
        "OpenWorldHint": "openWorldHint",
        "WithOpenWorldHintAnnotation": "openWorldHint",
    }
    found: dict[str, bool] = {}
    for index in range(start, end - 1):
        canonical = aliases.get(tokens[index].value)
        if canonical is None:
            continue
        pos = index + 1
        if tokens[pos].value in {":", "("}:
            pos += 1
        if pos < end and tokens[pos].kind == "ident" and tokens[pos].value in {"true", "false"}:
            found[canonical] = tokens[pos].value == "true"
    return found


def _tokens(text: str, *, language: Literal["typescript", "go"]) -> list[_Token]:
    """Small comment-aware lexer for the literal-only registry grammars."""

    result: list[_Token] = []
    index = 0
    line = 1
    column = 1
    length = len(text)

    def advance(raw: str) -> None:
        nonlocal line, column
        lines = raw.split("\n")
        if len(lines) == 1:
            column += len(raw)
        else:
            line += len(lines) - 1
            column = len(lines[-1]) + 1

    while index < length:
        char = text[index]
        if char.isspace():
            advance(char)
            index += 1
            continue
        if text.startswith("//", index):
            end = text.find("\n", index + 2)
            end = length if end < 0 else end
            advance(text[index:end])
            index = end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = length if end < 0 else end + 2
            advance(text[index:end])
            index = end
            continue
        if (
            char == "/"
            and language == "typescript"
            and _typescript_regex_can_start(result)
        ):
            end = _typescript_regex_end(text, index)
            if end is not None:
                advance(text[index:end])
                index = end
                continue
        if char in {"'", '"'}:
            start_line, start_column = line, column
            end, value = _quoted_string(text, index, char)
            raw = text[index:end]
            if value is not None:
                result.append(_Token("string", value, start_line, start_column))
            advance(raw)
            index = end
            continue
        if char == "`":
            # Template strings (TS) and raw strings (Go) are not accepted as
            # tool-name literals by v1. Skip their content so examples inside
            # them cannot become registrations.
            end = _backtick_literal_end(text, index, language=language)
            advance(text[index:end])
            index = end
            continue
        if char.isalpha() or char in {"_", "$"}:
            end = index + 1
            while end < length and (text[end].isalnum() or text[end] in {"_", "$"}):
                end += 1
            value = text[index:end]
            result.append(_Token("ident", value, line, column))
            advance(value)
            index = end
            continue
        result.append(_Token("punct", char, line, column))
        advance(char)
        index += 1
    return result


def _typescript_regex_can_start(tokens: list[_Token]) -> bool:
    if not tokens:
        return True
    previous = tokens[-1]
    if previous.kind == "punct":
        return previous.value in {"(", "{", "[", "=", ":", ",", ";", "!", "?"}
    return previous.kind == "ident" and previous.value in {
        "case",
        "delete",
        "return",
        "throw",
        "typeof",
        "void",
        "yield",
    }


def _typescript_regex_end(text: str, start: int) -> int | None:
    """Return the end of a JavaScript regex literal, or ``None`` for division."""

    index = start + 1
    in_class = False
    while index < len(text):
        char = text[index]
        if char in {"\n", "\r"}:
            return None
        if char == "\\":
            index += 2
            continue
        if char == "[":
            in_class = True
        elif char == "]" and in_class:
            in_class = False
        elif char == "/" and not in_class:
            index += 1
            while index < len(text) and text[index].isalpha():
                index += 1
            return index
        index += 1
    return None


def _backtick_literal_end(
    text: str,
    start: int,
    *,
    language: Literal["typescript", "go"],
) -> int:
    if language == "go":
        end = text.find("`", start + 1)
        return len(text) if end < 0 else end + 1

    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == "`":
            return index + 1
        if text.startswith("${", index):
            index = _typescript_template_expression_end(text, index + 2)
            continue
        index += 1
    return len(text)


def _typescript_template_expression_end(text: str, start: int) -> int:
    """Skip one ``${...}``, including nested templates and object literals."""

    depth = 1
    index = start
    while index < len(text):
        if text.startswith("//", index):
            end = text.find("\n", index + 2)
            index = len(text) if end < 0 else end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end < 0 else end + 2
            continue
        char = text[index]
        if char in {"'", '"'}:
            index, _ = _quoted_string(text, index, char)
            continue
        if char == "`":
            index = _backtick_literal_end(text, index, language="typescript")
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return len(text)


def _quoted_string(text: str, start: int, quote: str) -> tuple[int, str | None]:
    chars: list[str] = []
    index = start + 1
    escapes = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}
    while index < len(text):
        char = text[index]
        if char == quote:
            return index + 1, "".join(chars)
        if char in {"\n", "\r"}:
            return index, None
        if char != "\\":
            chars.append(char)
            index += 1
            continue
        index += 1
        if index >= len(text):
            return index, None
        escaped = text[index]
        if escaped in {quote, "\\", "/"}:
            chars.append(escaped)
            index += 1
        elif escaped in escapes:
            chars.append(escapes[escaped])
            index += 1
        elif escaped in {"x", "u"}:
            width = 2 if escaped == "x" else 4
            raw = text[index + 1 : index + 1 + width]
            if len(raw) != width or any(c not in "0123456789abcdefABCDEF" for c in raw):
                return index + 1 + len(raw), None
            chars.append(chr(int(raw, 16)))
            index += 1 + width
        else:
            return index + 1, None
    return len(text), None


__all__ = [
    "GO_ADDTOOL_V1",
    "GO_MUSTTOOL_V1",
    "MCP_CODE_IDIOM_IDS",
    "MCP_CODE_IDIOM_REGISTRY_VERSION",
    "MCP_CODE_IDIOMS",
    "MAX_MCP_CODE_FILE_BYTES",
    "MAX_MCP_CODE_FILES",
    "MAX_MCP_CODE_TOTAL_BYTES",
    "McpCodeObservation",
    "McpCodeOmission",
    "McpCodeScan",
    "TYPESCRIPT_MCP_SDK_V1",
    "TYPESCRIPT_STATIC_TOOL_V1",
    "has_mcp_source_import_marker",
    "load_mcp_code_tools",
    "is_mcp_code_candidate",
    "scan_mcp_code_idiom",
    "validate_mcp_code_idiom",
]
