from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Literal

from agents_shipgate.core.domain import (
    AuthInfo,
    LoadedToolSource,
    SourceSurfaceOmission,
    Tool,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.common import (
    load_structured_file_with_positions,
    manifest_relative_path,
    resolve_input_path,
    schema_to_parameters,
    stable_tool_id,
    strip_untrusted_binding_annotations,
    tool_name_warning,
    walk_input_tree,
)
from agents_shipgate.inputs.coverage import BoundaryCell, SourceCoverage
from agents_shipgate.inputs.protocol import LoadedAdapterResult
from agents_shipgate.schemas.manifest import (
    AgentsShipgateManifest,
    ToolSourceConfig,
)
from agents_shipgate.schemas.manifest.tool_sources import MCP_TOOL_SNAPSHOT_V1

MAX_MCP_SNAPSHOT_FILES = 5_000
MAX_MCP_SNAPSHOT_FILE_BYTES = 1 * 1024 * 1024
MAX_MCP_SNAPSHOT_TOTAL_BYTES = 32 * 1024 * 1024


def load_mcp_tools(source: ToolSourceConfig, base_dir: Path) -> LoadedToolSource:
    if source.idiom == MCP_TOOL_SNAPSHOT_V1:
        return _load_mcp_tool_snapshots(source, base_dir)
    if source.idiom is not None:
        # Lazy to keep the long-established JSON loader independent of the
        # optional source-code grammar registry (and its directory walk).
        from agents_shipgate.inputs.mcp_code import load_mcp_code_tools

        return load_mcp_code_tools(source, base_dir)
    assert source.path is not None
    path = resolve_input_path(base_dir, source.path)
    source_ref = source.path
    source_path = manifest_relative_path(source.path, base_dir)
    data, positions = load_structured_file_with_positions(path)
    warnings: list[str] = []

    pointer_prefix: str
    if isinstance(data, list):
        raw_tools = data
        pointer_prefix = ""
    elif isinstance(data, dict):
        raw_tools = data.get("tools")
        pointer_prefix = "/tools"
        if data.get("wildcard") is True or raw_tools == "*":
            if isinstance(raw_tools, list) and raw_tools:
                raise InputParseError(
                    "MCP source declares wildcard tool exposure and an explicit tools "
                    f"array: {path}. Use wildcard exposure or explicit tools, not both."
                )
            wildcard_warnings = ["MCP source declares wildcard tool exposure"]
            # Pick the pointer that actually triggered the wildcard
            # branch so reviewers jump to the offending line — `wildcard:
            # true` and `tools: '*'` are different signals on different
            # lines.
            wildcard_pointer = "/wildcard" if data.get("wildcard") is True else "/tools"
            wildcard_pos = positions.lookup(wildcard_pointer)
            wildcard_start_line: int | None = None
            wildcard_start_column: int | None = None
            if wildcard_pos is not None:
                wildcard_start_line, wildcard_start_column = wildcard_pos
            wildcard = Tool(
                id=stable_tool_id(f"{source.id}.*"),
                name=f"{source.id}.*",
                description="Wildcard MCP tool exposure.",
                source_type="mcp",
                source_id=source.id,
                source_ref=source_ref,
                source_path=source_path,
                source_start_line=wildcard_start_line,
                source_start_column=wildcard_start_column,
                source_pointer=wildcard_pointer,
                annotations={"wildcard_tools": True},
                extraction_confidence="high",
                extraction={"method": "mcp_json", "confidence": "high"},
            )
            return LoadedToolSource(
                source_id=source.id,
                source_type="mcp",
                tools=[wildcard],
                warnings=wildcard_warnings,
            )
    else:
        raise InputParseError(f"MCP tools file must be an object or array: {path}")

    if not isinstance(raw_tools, list):
        raise InputParseError(f"MCP tools file must contain a tools array: {path}")

    tools: list[Tool] = []
    omissions: list[SourceSurfaceOmission] = []
    seen_names: set[str] = set()

    def _omit(index: int, reason: str, warning: str, detail: str) -> None:
        """Record a dropped entry as a fact, not only as prose.

        These two branches are real omissions: the entry is read, refused, and
        never reaches the catalog. Saying so in a typed record is what lets the
        exclusion ledger account for it without mapping every warning — most of
        which are about tools that *did* load — to a missing subject.
        """

        warnings.append(warning)
        omissions.append(
            SourceSurfaceOmission(
                subject=f"{pointer_prefix or '/tools'}/{index}",
                reason=reason,
                detail=detail,
                warning=warning,
            )
        )

    for index, raw in enumerate(raw_tools):
        if not isinstance(raw, dict):
            _omit(
                index,
                "unreadable_entry",
                "Skipping non-object MCP tool entry",
                "The entry is not an object, so no tool could be read from it.",
            )
            continue
        name = raw.get("name")
        if not name:
            _omit(
                index,
                "unnamed_entry",
                "Skipping MCP tool without name",
                "The entry declares no name, so it cannot enter the catalog.",
            )
            continue
        name_text = str(name)
        if name_text in seen_names:
            warnings.append(f"Duplicate MCP tool name {name_text!r} in source {source.id!r}")
        seen_names.add(name_text)
        if warning := tool_name_warning(name_text):
            warnings.append(warning)
        input_schema = _first_present(raw, ["inputSchema", "input_schema"]) or {}
        output_schema = _first_present(raw, ["outputSchema", "output_schema"]) or {}
        raw_annotations = raw.get("annotations") or {}
        annotations: dict[str, Any] = {}
        if isinstance(raw_annotations, dict):
            annotations, rejected_binding_keys = strip_untrusted_binding_annotations(
                raw_annotations
            )
            if rejected_binding_keys:
                warnings.append(
                    f"MCP tool {name_text!r} contains reserved binding annotations "
                    f"that were ignored: {', '.join(rejected_binding_keys)}"
                )
        raw_auth = raw.get("auth")
        auth_explicit = "auth" in raw
        pointer = f"{pointer_prefix}/{index}"
        pos = positions.lookup(pointer)
        source_start_line: int | None = None
        source_start_column: int | None = None
        if pos is not None:
            source_start_line, source_start_column = pos
        # `source_location` stays None: the legacy `path:line` string is
        # part of the `run_id` hash and v0.10 MCP tools never set it.
        # Reviewers get the line through the structured fields below.
        tool = Tool(
            id=stable_tool_id(str(name)),
            name=name_text,
            description=raw.get("description"),
            source_type="mcp",
            source_id=source.id,
            source_ref=source_ref,
            source_path=source_path,
            source_start_line=source_start_line,
            source_start_column=source_start_column,
            source_pointer=pointer,
            input_schema=input_schema if isinstance(input_schema, dict) else {},
            output_schema=output_schema if isinstance(output_schema, dict) else {},
            parameters=schema_to_parameters(input_schema),
            annotations=annotations,
            auth=_mcp_auth_info(raw_auth, explicit=auth_explicit),
            owner=raw.get("owner"),
            extraction_confidence="high",
            extraction={"method": "mcp_json", "confidence": "high"},
        )
        tools.append(tool)

    return LoadedToolSource(
        source_id=source.id,
        source_type="mcp",
        tools=tools,
        warnings=warnings,
        omissions=omissions,
    )


def _load_mcp_tool_snapshots(
    source: ToolSourceConfig, base_dir: Path
) -> LoadedToolSource:
    """Aggregate GitHub-style checked-in per-tool ``*.snap`` JSON objects."""

    assert source.path is not None
    root = resolve_input_path(base_dir, source.path)
    if not root.is_dir():
        raise InputParseError(
            f"MCP tool snapshot idiom requires a directory: {root}"
        )
    candidates = sorted(
        (
            path
            for path in walk_input_tree(root)
            if path.is_file() and path.suffix.lower() == ".snap"
        ),
        key=lambda path: path.as_posix(),
    )
    if not candidates:
        raise InputParseError(
            f"MCP tool snapshot directory contains no .snap files: {root}"
        )

    warnings: list[str] = []
    omissions: list[SourceSurfaceOmission] = []
    if len(candidates) > MAX_MCP_SNAPSHOT_FILES:
        omitted = len(candidates) - MAX_MCP_SNAPSHOT_FILES
        warning = (
            f"MCP snapshot scan stopped at {MAX_MCP_SNAPSHOT_FILES} files and "
            f"left {omitted} file(s) unread"
        )
        warnings.append(warning)
        omissions.append(
            SourceSurfaceOmission(
                subject=source.path,
                reason="source_file_cap",
                detail=warning,
                warning=warning,
            )
        )
        candidates = candidates[:MAX_MCP_SNAPSHOT_FILES]

    rows: list[tuple[Path, dict[str, Any], Any]] = []
    scheduled_bytes = 0
    for path in candidates:
        display = manifest_relative_path(str(path), base_dir)
        try:
            size = path.stat().st_size
        except OSError as exc:
            warning = f"MCP snapshot {display} could not be inspected: {exc}"
            warnings.append(warning)
            omissions.append(
                SourceSurfaceOmission(
                    subject=display,
                    reason="unreadable_source_file",
                    detail=warning,
                    warning=warning,
                )
            )
            continue
        if size > MAX_MCP_SNAPSHOT_FILE_BYTES:
            warning = (
                f"MCP snapshot {display} exceeds the "
                f"{MAX_MCP_SNAPSHOT_FILE_BYTES}-byte per-file limit"
            )
            warnings.append(warning)
            omissions.append(
                SourceSurfaceOmission(
                    subject=display,
                    reason="source_file_byte_cap",
                    detail=warning,
                    warning=warning,
                )
            )
            continue
        if scheduled_bytes + size > MAX_MCP_SNAPSHOT_TOTAL_BYTES:
            warning = (
                "MCP snapshot scan stopped at its aggregate "
                f"{MAX_MCP_SNAPSHOT_TOTAL_BYTES}-byte limit"
            )
            warnings.append(warning)
            omissions.append(
                SourceSurfaceOmission(
                    subject=source.path,
                    reason="source_byte_cap",
                    detail=warning,
                    warning=warning,
                )
            )
            break
        scheduled_bytes += size
        try:
            raw, positions = load_structured_file_with_positions(path)
        except InputParseError as exc:
            warning = f"MCP snapshot {display} is not readable JSON: {exc}"
            warnings.append(warning)
            omissions.append(
                SourceSurfaceOmission(
                    subject=display,
                    reason="unreadable_entry",
                    detail=warning,
                    warning=warning,
                )
            )
            continue
        if not isinstance(raw, dict):
            warning = f"MCP snapshot {display} is not one tool object"
            warnings.append(warning)
            omissions.append(
                SourceSurfaceOmission(
                    subject=display,
                    reason="unreadable_entry",
                    detail=warning,
                    warning=warning,
                )
            )
            continue
        rows.append((path, raw, positions))

    valid_rows: list[tuple[Path, dict[str, Any], Any, str]] = []
    for path, raw, positions in rows:
        display = manifest_relative_path(str(path), base_dir)
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            warning = f"Skipping MCP snapshot {display} without a string name"
            warnings.append(warning)
            omissions.append(
                SourceSurfaceOmission(
                    subject=display,
                    reason="unnamed_entry",
                    detail=warning,
                    warning=warning,
                )
            )
            continue
        valid_rows.append((path, raw, positions, name))

    tools: list[Tool] = []
    seen_names: set[str] = set()
    surface = "partial" if omissions else "enumerated"
    tool_set_proven = not omissions
    for path, raw, positions, name in valid_rows:
        display = manifest_relative_path(str(path), base_dir)
        if name in seen_names:
            warnings.append(f"Duplicate MCP tool name {name!r} in source {source.id!r}")
        seen_names.add(name)
        if warning := tool_name_warning(name):
            warnings.append(warning)
        raw_annotations = raw.get("annotations") or {}
        annotations: dict[str, Any] = {}
        if isinstance(raw_annotations, dict):
            annotations, rejected = strip_untrusted_binding_annotations(raw_annotations)
            if rejected:
                warnings.append(
                    f"MCP tool {name!r} contains reserved binding annotations "
                    f"that were ignored: {', '.join(rejected)}"
                )
        input_schema = _first_present(raw, ["inputSchema", "input_schema"]) or {}
        output_schema = _first_present(raw, ["outputSchema", "output_schema"]) or {}
        position = positions.lookup("/name")
        line, column = position if position is not None else (None, None)
        tools.append(
            Tool(
                id=stable_tool_id(name),
                name=name,
                description=raw.get("description"),
                source_type="mcp",
                source_id=source.id,
                source_ref=source.path,
                source_path=display,
                source_start_line=line,
                source_start_column=column,
                source_pointer=f"/snapshots/{path.relative_to(root).as_posix()}",
                input_schema=input_schema if isinstance(input_schema, dict) else {},
                output_schema=output_schema if isinstance(output_schema, dict) else {},
                parameters=schema_to_parameters(input_schema),
                annotations=annotations,
                auth=_mcp_auth_info(raw.get("auth"), explicit="auth" in raw),
                owner=raw.get("owner"),
                extraction_confidence="high",
                extraction={
                    "method": "mcp_tool_snapshot",
                    "confidence": "high",
                    "idiom": MCP_TOOL_SNAPSHOT_V1,
                    "surface": surface,
                    "tool_set_proven": tool_set_proven,
                },
            )
        )
    return LoadedToolSource(
        source_id=source.id,
        source_type="mcp",
        tools=tools,
        warnings=warnings,
        omissions=omissions,
    )


def _first_present(raw: dict[str, Any], names: list[str]) -> Any:
    for name in names:
        if name in raw:
            return raw[name]
    return None


def _mcp_auth_info(raw_auth: Any, *, explicit: bool) -> AuthInfo:
    invalid: list[str] = []
    if not explicit:
        return AuthInfo(source="mcp")
    if not isinstance(raw_auth, dict):
        return AuthInfo(
            source="mcp",
            explicit=True,
            invalid_annotations=["auth must be an object"],
        )

    raw_type = raw_auth.get("type")
    auth_type = raw_type.strip() if isinstance(raw_type, str) else None
    if raw_type is not None and not auth_type:
        invalid.append("auth.type must be a non-blank string")

    raw_scopes = raw_auth.get("scopes", [])
    scopes: list[str] = []
    if not isinstance(raw_scopes, list):
        invalid.append("auth.scopes must be a list of non-blank strings")
    else:
        for raw_scope in raw_scopes:
            if not isinstance(raw_scope, str) or not raw_scope.strip():
                invalid.append("auth.scopes must contain non-blank strings")
                continue
            scopes.append(raw_scope.strip())

    required = raw_auth.get("required")
    if "required" in raw_auth and type(required) is not bool:
        invalid.append("auth.required must be an exact boolean")

    raw_credential_mode = raw_auth.get("credential_mode")
    credential_mode = (
        raw_credential_mode.strip()
        if isinstance(raw_credential_mode, str)
        else None
    )
    if raw_credential_mode is not None and not credential_mode:
        invalid.append("auth.credential_mode must be a non-blank string")

    raw_mode = raw_auth.get("mode")
    valid_modes = {"none", "scoped", "unscoped", "ambient"}
    if raw_mode is not None and (not isinstance(raw_mode, str) or raw_mode not in valid_modes):
        invalid.append("auth.mode must be one of none, scoped, unscoped, ambient")
        mode = "unknown"
    elif isinstance(raw_mode, str):
        mode = raw_mode
    elif required is False:
        mode = "none"
    elif auth_type and scopes:
        mode = "scoped"
    elif auth_type:
        mode = "unscoped"
    else:
        mode = "unknown"

    if mode == "none" and (required is True or auth_type or scopes):
        invalid.append("auth.mode none conflicts with required credentials")
    elif required is False and mode != "none":
        invalid.append("auth.required false conflicts with authenticated mode")
    elif mode == "scoped" and (not auth_type or not scopes):
        invalid.append("auth.mode scoped requires auth.type and concrete scopes")
    elif mode == "unscoped" and (not auth_type or scopes):
        invalid.append("auth.mode unscoped requires auth.type and empty scopes")
    elif mode == "ambient" and scopes:
        invalid.append("auth.mode ambient requires empty scopes")

    if invalid:
        mode = "unknown"
    return AuthInfo(
        type=auth_type,
        scopes=scopes,
        credential_mode=credential_mode,
        source="mcp",
        mode=mode,
        explicit=True,
        invalid_annotations=invalid,
    )


class MCPAdapter:
    """``ToolSourceAdapter`` wrapping :func:`load_mcp_tools`."""

    source_type: ClassVar[str] = "mcp"
    scope: ClassVar[Literal["per_source", "per_scan"]] = "per_source"
    artifact_class: ClassVar[type | None] = None

    coverage: ClassVar[SourceCoverage] = SourceCoverage(
        adapter="mcp",
        label="MCP tool export",
        reads=(
            "A committed MCP `tools/list` response, or any file carrying the "
            "same `tools` array."
        ),
        cells=(
            BoundaryCell(
                shape="export_artifact",
                status="extracted",
                reads=(
                    "A `tools` array naming each tool with its input schema: the "
                    "server's own published contract, read as written."
                ),
                emits=("mcp",),
                ceiling="high",
            ),
            BoundaryCell(
                shape="literal_registration",
                status="not_applicable",
                reads=(
                    "An export is a contract file, not source. A server declared "
                    "in a host config is read by the Codex / MCP host config input."
                ),
            ),
            BoundaryCell(
                shape="factory",
                status="not_applicable",
                reads="An export is the result of construction, never the construction.",
            ),
            BoundaryCell(
                shape="dynamic_construction",
                status="extracted",
                reads=(
                    "`wildcard: true` or `tools: \"*\"`: one synthetic `<source>.*` "
                    "action stands in for a surface the file declines to name."
                ),
                emits=("mcp",),
                ceiling="high",
                surface_flags=("wildcard_tools",),
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
        return LoadedAdapterResult(tool_sources=[load_mcp_tools(source, base_dir)])
