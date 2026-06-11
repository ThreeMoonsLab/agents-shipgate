from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents_shipgate.core.capability_lattice import (
    is_secret_env_name,
    mcp_permission_risk_hints,
)
from agents_shipgate.core.domain import AuthInfo, LoadedToolSource, Tool
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.common import (
    load_structured_file_with_positions,
    manifest_relative_path,
    schema_to_parameters,
    stable_tool_id,
)
from agents_shipgate.inputs.mcp import load_mcp_tools
from agents_shipgate.schemas.manifest import ToolSourceConfig

_ENABLED_TOOL_KEYS = (
    "enabled_tools",
    "allowed_tools",
    "tool_allowlist",
    "tools_allowlist",
)
_TOOLS_KEYS = ("tools", "tool_overrides")
_SCHEMA_KEYS = ("inputSchema", "input_schema", "schema")
_OUTPUT_SCHEMA_KEYS = ("outputSchema", "output_schema")
_ENV_REF_RE = re.compile(r"\$[{(]?([A-Za-z_][A-Za-z0-9_]*)[})]?")
_SKIPPED_SCAN_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "dist",
    "node_modules",
    "site-packages",
    "venv",
}


@dataclass(frozen=True)
class NormalizedMcpTool:
    server_name: str
    name: str
    approval_mode: str | None = None
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    annotations: dict[str, Any] = field(default_factory=dict)
    auth: dict[str, Any] = field(default_factory=dict)
    owner: str | None = None
    source_pointer: str | None = None
    source_start_line: int | None = None
    source_start_column: int | None = None
    unknown_schema: bool = False


@dataclass(frozen=True)
class NormalizedMcpServer:
    name: str
    source_id: str
    source_type: str
    source_ref: str
    source_path: str
    source_pointer: str | None = None
    source_start_line: int | None = None
    source_start_column: int | None = None
    default_tools_approval_mode: str | None = None
    transport: str | None = None
    command: str | None = None
    url_present: bool = False
    env_secret_names: tuple[str, ...] = ()
    local_documentation: bool = False
    wildcard: bool = False
    tools: tuple[NormalizedMcpTool, ...] = ()


def load_mcp_manifest_inventory(
    source: ToolSourceConfig,
    base_dir: Path,
) -> LoadedToolSource:
    """Load an explicit MCP inventory through the existing inventory parser."""

    return load_mcp_tools(source, base_dir)


def load_codex_config_mcp_sources(root: Path, base_dir: Path) -> list[LoadedToolSource]:
    """Statically load repo-local Codex MCP declarations as tool sources.

    No command is executed. Server stubs without enumerable tools are represented
    as a synthetic ``{server}.*`` wildcard tool with ``mcp_unknown_schema``.
    """

    sources: list[LoadedToolSource] = []
    root = root.resolve()
    if not root.exists():
        return sources
    for path in _iter_mcp_candidate_files(root):
        rel = _relative(path, root)
        if rel == ".codex/config.toml" or rel.endswith("/.codex/config.toml"):
            source_ref = _relative(path, base_dir)
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError) as exc:
                sources.append(
                    LoadedToolSource(
                        source_id=f"codex_config_mcp:{source_ref}",
                        source_type="codex_config_mcp",
                        warnings=[f"Codex MCP config could not be parsed: {exc}"],
                    )
                )
                continue
            servers = normalize_codex_config_mcp_servers(
                data,
                source_ref=source_ref,
                source_path=manifest_relative_path(str(path.resolve()), base_dir),
            )
            if servers:
                sources.append(_loaded_source_from_servers(source_ref, servers))
        elif path.name == ".mcp.json":
            sources.extend(_load_mcp_json(path, base_dir))
    return sources


def normalize_codex_config_mcp_servers(
    data: dict[str, Any],
    *,
    source_ref: str,
    source_path: str,
) -> list[NormalizedMcpServer]:
    servers: list[NormalizedMcpServer] = []
    servers.extend(
        _servers_from_mapping(
            data.get("mcp_servers"),
            source_ref=source_ref,
            source_path=source_path,
            source_id_prefix="codex_config_mcp",
            source_type="codex_config_mcp",
            pointer_prefix="mcp_servers",
        )
    )
    plugins = data.get("plugins")
    if isinstance(plugins, dict):
        for plugin_name, plugin in sorted(plugins.items()):
            if not isinstance(plugin, dict):
                continue
            servers.extend(
                _servers_from_mapping(
                    plugin.get("mcp_servers"),
                    source_ref=source_ref,
                    source_path=source_path,
                    source_id_prefix=f"codex_plugin_config_mcp:{plugin_name}",
                    source_type="codex_config_mcp",
                    pointer_prefix=f"plugins.{plugin_name}.mcp_servers",
                )
            )
    return servers


def normalize_mcp_json_servers(
    data: dict[str, Any],
    *,
    source_ref: str,
    source_path: str,
) -> list[NormalizedMcpServer]:
    return _servers_from_mapping(
        data.get("mcpServers"),
        source_ref=source_ref,
        source_path=source_path,
        source_id_prefix="mcp_json",
        source_type="codex_config_mcp",
        pointer_prefix="mcpServers",
    )


def tools_from_normalized_mcp_servers(
    servers: list[NormalizedMcpServer] | tuple[NormalizedMcpServer, ...],
) -> list[Tool]:
    tools: list[Tool] = []
    for server in servers:
        for normalized in server.tools:
            tool = _tool_from_normalized(server, normalized)
            tool.risk_hints.extend(mcp_permission_risk_hints(tool))
            tools.append(tool)
    return tools


def _servers_from_mapping(
    raw_servers: Any,
    *,
    source_ref: str,
    source_path: str,
    source_id_prefix: str,
    source_type: str,
    pointer_prefix: str,
) -> list[NormalizedMcpServer]:
    if not isinstance(raw_servers, dict):
        return []
    servers: list[NormalizedMcpServer] = []
    for server_name, raw_server in sorted(raw_servers.items()):
        if not isinstance(raw_server, dict):
            continue
        if raw_server.get("enabled") is False:
            continue
        source_id = f"{source_id_prefix}:{server_name}"
        env_secret_names = tuple(_env_secret_names(raw_server.get("env")))
        transport = _transport(raw_server)
        command = raw_server.get("command") if isinstance(raw_server.get("command"), str) else None
        url_present = isinstance(raw_server.get("url"), str) or transport in {
            "http",
            "sse",
            "streamable_http",
            "streamable-http",
        }
        local_documentation = _looks_like_local_documentation(str(server_name), raw_server)
        tools = _normalized_tools_for_server(
            str(server_name),
            raw_server,
            source_pointer=f"/{pointer_prefix.replace('.', '/')}/{server_name}",
            env_secret_names=env_secret_names,
            transport=transport,
            url_present=url_present,
            local_documentation=local_documentation,
        )
        servers.append(
            NormalizedMcpServer(
                name=str(server_name),
                source_id=source_id,
                source_type=source_type,
                source_ref=source_ref,
                source_path=source_path,
                source_pointer=f"/{pointer_prefix.replace('.', '/')}/{server_name}",
                default_tools_approval_mode=_text_or_none(
                    raw_server.get("default_tools_approval_mode")
                ),
                transport=transport,
                command=command,
                url_present=url_present,
                env_secret_names=env_secret_names,
                local_documentation=local_documentation,
                wildcard=any(tool.name.endswith(".*") for tool in tools),
                tools=tuple(tools),
            )
        )
    return servers


def _normalized_tools_for_server(
    server_name: str,
    server: dict[str, Any],
    *,
    source_pointer: str,
    env_secret_names: tuple[str, ...],
    transport: str | None,
    url_present: bool,
    local_documentation: bool,
) -> list[NormalizedMcpTool]:
    explicit_tools = _tool_config_mapping(server)
    names = set(explicit_tools)
    for key in _ENABLED_TOOL_KEYS:
        value = server.get(key)
        if isinstance(value, list):
            names.update(str(item) for item in value if isinstance(item, str))
    disabled = {
        str(name)
        for name, config in explicit_tools.items()
        if isinstance(config, dict) and config.get("enabled") is False
    }
    names.difference_update(disabled)
    if not names:
        return [
            NormalizedMcpTool(
                server_name=server_name,
                name=f"{server_name}.*",
                approval_mode=_text_or_none(server.get("default_tools_approval_mode")),
                annotations=_base_annotations(
                    server,
                    env_secret_names=env_secret_names,
                    transport=transport,
                    url_present=url_present,
                    local_documentation=local_documentation,
                    unknown_schema=True,
                    wildcard=True,
                ),
                source_pointer=source_pointer,
                unknown_schema=True,
            )
        ]
    tools: list[NormalizedMcpTool] = []
    for name in sorted(names):
        config = explicit_tools.get(name)
        config = config if isinstance(config, dict) else {}
        input_schema = _first_dict(config, _SCHEMA_KEYS)
        output_schema = _first_dict(config, _OUTPUT_SCHEMA_KEYS)
        annotations = _annotations_from_tool_config(config)
        unknown_schema = _unknown_schema(name, input_schema, annotations)
        annotations.update(
            _base_annotations(
                server,
                env_secret_names=env_secret_names,
                transport=transport,
                url_present=url_present,
                local_documentation=local_documentation,
                unknown_schema=unknown_schema,
                wildcard=False,
            )
        )
        tools.append(
            NormalizedMcpTool(
                server_name=server_name,
                name=name,
                approval_mode=_text_or_none(
                    config.get("approval_mode")
                    or server.get("default_tools_approval_mode")
                ),
                input_schema=input_schema,
                output_schema=output_schema,
                annotations=annotations,
                auth=config.get("auth") if isinstance(config.get("auth"), dict) else {},
                owner=_text_or_none(config.get("owner")),
                source_pointer=f"{source_pointer}/tools/{name}",
                unknown_schema=unknown_schema,
            )
        )
    return tools


def _tool_from_normalized(
    server: NormalizedMcpServer,
    normalized: NormalizedMcpTool,
) -> Tool:
    annotations = dict(normalized.annotations)
    if normalized.approval_mode:
        annotations["mcp_approval_mode"] = normalized.approval_mode
    auth = normalized.auth
    scopes = auth.get("scopes") if isinstance(auth, dict) else []
    return Tool(
        id=stable_tool_id(normalized.name),
        name=normalized.name,
        description=f"Codex MCP tool exposed by server {server.name}.",
        source_type=server.source_type,
        source_id=server.source_id,
        source_ref=server.source_ref,
        source_path=server.source_path,
        source_start_line=normalized.source_start_line or server.source_start_line,
        source_start_column=normalized.source_start_column or server.source_start_column,
        source_pointer=normalized.source_pointer or server.source_pointer,
        input_schema=normalized.input_schema,
        output_schema=normalized.output_schema,
        parameters=schema_to_parameters(normalized.input_schema),
        annotations=annotations,
        auth=AuthInfo(
            type=auth.get("type") if isinstance(auth, dict) else None,
            scopes=list(scopes) if isinstance(scopes, list) else [],
            credential_mode=auth.get("credential_mode") if isinstance(auth, dict) else None,
            source="mcp",
        ),
        owner=normalized.owner,
        extraction_confidence="medium" if normalized.unknown_schema else "high",
        extraction={
            "method": "codex_mcp_config",
            "confidence": "medium" if normalized.unknown_schema else "high",
        },
    )


def _loaded_source_from_servers(
    source_ref: str,
    servers: list[NormalizedMcpServer],
) -> LoadedToolSource:
    return LoadedToolSource(
        source_id=f"codex_config_mcp:{source_ref}",
        source_type="codex_config_mcp",
        tools=tools_from_normalized_mcp_servers(servers),
        warnings=[],
    )


def _load_mcp_json(path: Path, base_dir: Path) -> list[LoadedToolSource]:
    try:
        data, positions = load_structured_file_with_positions(path)
    except InputParseError as exc:
        return [
            LoadedToolSource(
                source_id=f"mcp_json:{_relative(path, base_dir)}",
                source_type="codex_config_mcp",
                warnings=[str(exc)],
            )
        ]
    raw_servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(raw_servers, dict):
        return []
    source_ref = _relative(path, base_dir)
    source_path = manifest_relative_path(str(path.resolve()), base_dir)
    servers = normalize_mcp_json_servers(
        data,
        source_ref=source_ref,
        source_path=source_path,
    )
    enriched: list[NormalizedMcpServer] = []
    for server in servers:
        pointer = f"/mcpServers/{_json_pointer_escape(server.name)}"
        pos = positions.lookup(pointer)
        line: int | None = None
        col: int | None = None
        if pos is not None:
            line, col = pos
        enriched.append(
            NormalizedMcpServer(
                **{
                    **server.__dict__,
                    "source_pointer": pointer,
                    "source_start_line": line,
                    "source_start_column": col,
                }
            )
        )
    return [_loaded_source_from_servers(source_ref, enriched)] if enriched else []


def _tool_config_mapping(server: dict[str, Any]) -> dict[str, Any]:
    for key in _TOOLS_KEYS:
        raw = server.get(key)
        if isinstance(raw, dict):
            return {str(name): config for name, config in raw.items()}
    return {}


def _base_annotations(
    server: dict[str, Any],
    *,
    env_secret_names: tuple[str, ...],
    transport: str | None,
    url_present: bool,
    local_documentation: bool,
    unknown_schema: bool,
    wildcard: bool,
) -> dict[str, Any]:
    annotations: dict[str, Any] = {
        "mcp_server": True,
        "codex_mcp_server": True,
        "mcp_transport": transport,
        "mcp_unknown_schema": unknown_schema,
    }
    default_mode = _text_or_none(server.get("default_tools_approval_mode"))
    if default_mode:
        annotations["mcp_default_tools_approval_mode"] = default_mode
    if env_secret_names:
        annotations["mcp_env_secret_names"] = list(env_secret_names)
    if url_present:
        annotations["shipgate_permission_classes"] = ["external"]
    if local_documentation:
        annotations["mcp_local_documentation"] = True
    if wildcard:
        annotations["wildcard_tools"] = True
        annotations["mcp_wildcard_tools"] = True
    return {key: value for key, value in annotations.items() if value is not None}


def _annotations_from_tool_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("annotations")
    annotations = dict(raw) if isinstance(raw, dict) else {}
    for key in ("readOnlyHint", "destructiveHint", "openWorldHint", "idempotentHint"):
        if key in config:
            annotations[key] = config[key]
    if config.get("read_only") is True:
        annotations["readOnlyHint"] = True
    permission_classes = config.get("permission_classes") or config.get("permissions")
    if permission_classes is not None:
        annotations["shipgate_permission_classes"] = permission_classes
    return annotations


def _unknown_schema(
    name: str,
    input_schema: dict[str, Any],
    annotations: dict[str, Any],
) -> bool:
    if input_schema:
        return False
    if annotations.get("readOnlyHint") is True:
        return False
    lowered = name.lower()
    return not lowered.startswith(
        (
            "describe",
            "fetch",
            "find",
            "get",
            "list",
            "lookup",
            "read",
            "search",
            "show",
            "status",
            "view",
        )
    )


def _first_dict(data: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    for name in names:
        value = data.get(name)
        if isinstance(value, dict):
            return value
    return {}


def _env_secret_names(raw_env: Any) -> list[str]:
    if not isinstance(raw_env, dict):
        return []
    names: set[str] = set()
    for key, value in raw_env.items():
        key_text = str(key)
        if is_secret_env_name(key_text):
            names.add(key_text)
        if isinstance(value, str):
            for match in _ENV_REF_RE.findall(value):
                if is_secret_env_name(match):
                    names.add(key_text)
    return sorted(names)


def _transport(server: dict[str, Any]) -> str | None:
    value = server.get("transport") or server.get("type")
    if isinstance(value, str):
        return value
    if isinstance(server.get("url"), str):
        return "http"
    if isinstance(server.get("command"), str):
        return "stdio"
    return None


def _looks_like_local_documentation(server_name: str, server: dict[str, Any]) -> bool:
    if isinstance(server.get("url"), str):
        return False
    if _env_secret_names(server.get("env")):
        return False
    text = " ".join(
        [
            server_name,
            str(server.get("command") or ""),
            " ".join(str(item) for item in server.get("args") or []),
        ]
    )
    tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    return bool(tokens & {"doc", "docs", "documentation", "readme"})


def _iter_mcp_candidate_files(root: Path) -> list[Path]:
    candidates: list[Path] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            children = sorted(current.iterdir(), key=lambda item: item.name)
        except OSError:
            continue
        for child in children:
            if child.is_symlink() and child.is_dir():
                continue
            if child.is_dir():
                if child.name not in _SKIPPED_SCAN_DIRS:
                    stack.append(child)
                continue
            if not child.is_file():
                continue
            rel = _relative(child, root)
            if rel == ".codex/config.toml" or rel.endswith("/.codex/config.toml") or child.name == ".mcp.json":
                candidates.append(child)
    return sorted(candidates)


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _json_pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


__all__ = [
    "NormalizedMcpServer",
    "NormalizedMcpTool",
    "load_codex_config_mcp_sources",
    "load_mcp_manifest_inventory",
    "normalize_codex_config_mcp_servers",
    "normalize_mcp_json_servers",
    "tools_from_normalized_mcp_servers",
]
