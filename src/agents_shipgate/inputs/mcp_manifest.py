from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from agents_shipgate.core.capability_lattice import (
    is_secret_env_name,
    mcp_permission_risk_hints,
)
from agents_shipgate.core.domain import LoadedToolSource, SourceSurfaceOmission, Tool
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.source_warnings import (
    duplicate_mcp_server_declaration_warning,
)
from agents_shipgate.inputs.common import (
    load_structured_file_with_positions,
    load_text_file,
    manifest_relative_path,
    schema_to_parameters,
    stable_tool_id,
    strip_untrusted_binding_annotations,
)
from agents_shipgate.inputs.mcp import _mcp_auth_info, load_mcp_tools
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


#: ``SourceSurfaceOmission.reason`` for a server declaration that did not
#: enter the catalog as a surface of its own. Rendered by
#: ``core.surface_exclusions.EXCLUSION_REASON_PHRASES``, which is where the
#: bundled MCP loader's other reason tokens live.
DUPLICATE_SERVER_DECLARATION = "duplicate_server_declaration"

#: Annotation keys whose ``True`` is a claim that *raises* risk. A merge across
#: disagreeing declarations keeps only what they all say, which is right for
#: every reassuring claim — ``readOnlyHint`` asserted by one file and not the
#: other is not asserted about the server — and exactly wrong for these: a
#: destructive tool declared destructive in one file only would have arrived
#: with the claim dropped. Any declaration making one of these makes it.
_RISK_MONOTONE_ANNOTATIONS: tuple[str, ...] = (
    "destructiveHint",
    "long_running",
    "mcp_unknown_schema",
    "mcp_wildcard_tools",
    "openWorldHint",
    "wildcard_tools",
)

#: Annotation keys carrying a *set* of risk-raising claims, unioned rather than
#: intersected. Every spelling ``capability_lattice`` reads is listed: a rule
#: scoped to the one spelling this reader emits would pass vacuously for the
#: three an adopter may write into a tool's own ``annotations`` block.
_RISK_UNION_ANNOTATIONS: tuple[str, ...] = (
    "mcp_env_secret_names",
    "permission_class",
    "permission_classes",
    "shipgate_permission_classes",
    "x-agents-shipgate-permissions",
)

#: The approval mode that means no human approves the call. It is the riskiest
#: value the field takes, so it survives a disagreement the way a monotone
#: annotation does — including onto a tool whose own file said nothing about
#: approval, because the file that did was describing the same server.
_AUTO_APPROVAL_MODE = "approve"

_MISSING = object()


@dataclass(frozen=True)
class NormalizedMcpTool:
    server_name: str
    name: str
    approval_mode: str | None = None
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    annotations: dict[str, Any] = field(default_factory=dict)
    auth: Any = None
    auth_explicit: bool = False
    owner: str | None = None
    source_pointer: str | None = None
    source_start_line: int | None = None
    source_start_column: int | None = None
    unknown_schema: bool = False
    rejected_binding_annotation_keys: tuple[str, ...] = ()
    #: The file this tool was declared in, when it is not the one the server
    #: it belongs to is reported from. Set only by
    #: :func:`_merge_server_declarations`: reconciling a server declared in two
    #: files gives it the union of their tools, and a tool only the second file
    #: declares is not in the first. Sending a reader to a file that does not
    #: contain the tool is the failure this exists to prevent.
    source_ref: str | None = None
    source_path: str | None = None


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
    #: Canonical digest of the raw mapping this server was normalized from.
    #:
    #: Whether two declarations of one server name are *the same declaration*
    #: is decided here rather than by comparing the normalized fields, because
    #: the normalized fields are only what reaches the catalog. Two ``github``
    #: entries agreeing on every tool and disagreeing on ``args`` would compare
    #: equal on everything this reader carries forward, and they are not the
    #: same declaration. The digest is over everything that was read.
    declaration_digest: str = ""


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

    One result per *server*, not per file. The id a server is minted under
    carries no path — ``mcp audit`` pins that moving a ``.mcp.json`` is not a
    capability change — so a file-level result holding tools stamped per server
    reported every tool as belonging to a source other than the one it was read
    from, and a ``codex_config`` row over any config naming a server aborted the
    scan. Servers are collected across the whole tree first because the same
    server may be declared in several files, which is one capability declared
    twice and has to be reconciled before the catalog sees it
    (:func:`_loaded_sources_from_servers`).
    """

    sources: list[LoadedToolSource] = []
    servers: list[NormalizedMcpServer] = []
    root = root.resolve()
    if not root.exists():
        return sources
    for path in _iter_mcp_candidate_files(root):
        rel = _relative(path, root)
        if rel == ".codex/config.toml" or rel.endswith("/.codex/config.toml"):
            source_ref = _relative(path, base_dir)
            try:
                data = tomllib.loads(load_text_file(path))
            except (InputParseError, tomllib.TOMLDecodeError) as exc:
                sources.append(
                    LoadedToolSource(
                        source_id=f"codex_config_mcp:{source_ref}",
                        source_type="codex_config_mcp",
                        warnings=[f"Codex MCP config could not be parsed: {exc}"],
                    )
                )
                continue
            servers.extend(
                normalize_codex_config_mcp_servers(
                    data,
                    source_ref=source_ref,
                    source_path=manifest_relative_path(str(path.resolve()), base_dir),
                )
            )
        elif path.name == ".mcp.json":
            file_servers, unreadable = _load_mcp_json(path, base_dir)
            if unreadable is not None:
                sources.append(unreadable)
            servers.extend(file_servers)
    sources.extend(_loaded_sources_from_servers(servers))
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
                declaration_digest=_declaration_digest(raw_server),
            )
        )
    return servers


def _declaration_digest(raw_server: dict[str, Any]) -> str:
    """A stable digest of one server declaration exactly as it was written.

    ``default=str`` so a TOML date or any other non-JSON scalar digests as
    itself instead of raising: this must be total over whatever a config file
    contains, and a value two files spell differently has to compare unequal
    rather than abort the read.
    """

    encoded = json.dumps(raw_server, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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
        annotations, rejected_binding_keys = _annotations_from_tool_config(config)
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
                    config.get("approval_mode") or server.get("default_tools_approval_mode")
                ),
                input_schema=input_schema,
                output_schema=output_schema,
                annotations=annotations,
                auth=config.get("auth"),
                auth_explicit="auth" in config,
                owner=_text_or_none(config.get("owner")),
                source_pointer=f"{source_pointer}/tools/{name}",
                unknown_schema=unknown_schema,
                rejected_binding_annotation_keys=tuple(rejected_binding_keys),
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
    return Tool(
        id=stable_tool_id(normalized.name),
        name=normalized.name,
        description=f"Codex MCP tool exposed by server {server.name}.",
        source_type=server.source_type,
        source_id=server.source_id,
        source_ref=normalized.source_ref or server.source_ref,
        source_path=normalized.source_path or server.source_path,
        source_start_line=normalized.source_start_line or server.source_start_line,
        source_start_column=normalized.source_start_column or server.source_start_column,
        source_pointer=normalized.source_pointer or server.source_pointer,
        input_schema=normalized.input_schema,
        output_schema=normalized.output_schema,
        parameters=schema_to_parameters(normalized.input_schema),
        annotations=annotations,
        auth=_mcp_auth_info(normalized.auth, explicit=normalized.auth_explicit),
        owner=normalized.owner,
        extraction_confidence="medium" if normalized.unknown_schema else "high",
        extraction={
            "method": "codex_mcp_config",
            "confidence": "medium" if normalized.unknown_schema else "high",
        },
    )


def _loaded_sources_from_servers(
    servers: Sequence[NormalizedMcpServer],
) -> list[LoadedToolSource]:
    """One tool source per declared server, reconciled across the files.

    Two rules meet here and both are deliberate. An MCP capability is
    identified by ``(server, tool)`` and by nothing else, so that moving a
    ``.mcp.json`` is not a capability change; and one identity may be observed
    only once. A workspace whose packages each carry a ``.mcp.json`` naming
    ``github`` therefore is not two surfaces colliding — it is one server
    declared twice, and the reader owes the catalog a single declaration.

    Reconciliation is deliberately not "pick one". The declarations may
    disagree — different tool sets, schemas, approval modes — and taking the
    first file walked would let the other one widen the real surface
    invisibly. :func:`_merge_server_declarations` keeps the widest surface the
    group allows and records what did not enter the catalog on its own.
    """

    grouped: dict[tuple[str, str], list[NormalizedMcpServer]] = {}
    for server in servers:
        grouped.setdefault((server.source_type, server.source_id), []).append(server)
    sources: list[LoadedToolSource] = []
    for group in grouped.values():
        merged, warnings, omissions = _merge_server_declarations(group)
        sources.append(
            LoadedToolSource(
                source_id=merged.source_id,
                source_type=merged.source_type,
                tools=tools_from_normalized_mcp_servers([merged]),
                warnings=[*_rejected_annotation_warnings(merged), *warnings],
                omissions=omissions,
            )
        )
    return sources


def _rejected_annotation_warnings(server: NormalizedMcpServer) -> list[str]:
    return [
        (
            f"Codex MCP tool {tool.name!r} on server {server.name!r} contains "
            "reserved binding annotations that were ignored: "
            f"{', '.join(tool.rejected_binding_annotation_keys)}"
        )
        for tool in server.tools
        if tool.rejected_binding_annotation_keys
    ]


@dataclass(frozen=True)
class _ServerOverlay:
    """Server-level facts as they apply to every tool of a merged server.

    ``_base_annotations`` folded each declaration's server-level facts into
    that declaration's own tools. Under one name they describe one server, so a
    tool declared in only one of the files still inherits the group's reading:
    a URL in any declaration makes the server external for all of its tools,
    secrets named anywhere are named for all of them, and a reassuring claim
    only some declarations make — a local documentation server, a transport —
    is not a claim about the server at all.
    """

    transport: str | None
    local_documentation: bool
    env_secret_names: tuple[str, ...]
    external: bool
    auto_approved: bool


def _merge_server_declarations(
    group: Sequence[NormalizedMcpServer],
) -> tuple[NormalizedMcpServer, list[str], list[SourceSurfaceOmission]]:
    """Reconcile every declaration of one server into one.

    Identical declarations are silent. Nothing is dropped — every field the
    later files declare is already in the first — so there is no disagreement
    to report and no surface to account for, and saying otherwise would put a
    source warning, which is a gating input, on the ordinary monorepo layout
    where each package vendors the same ``.mcp.json``. Sameness is decided on
    the raw declaration (:attr:`NormalizedMcpServer.declaration_digest`), not
    on the fields that reach the catalog, so two files agreeing on every tool
    and disagreeing on the command that serves them are *not* identical.

    A disagreement is merged conservatively and reported. The surviving
    declaration is the first file walked, which fixes provenance; nothing about
    the *capability* is taken from it alone.
    """

    primary = group[0]
    if all(
        server.declaration_digest == primary.declaration_digest
        for server in group[1:]
    ):
        return primary, [], []

    overlay = _server_overlay(group)
    declarations_by_tool: dict[str, list[tuple[NormalizedMcpServer, NormalizedMcpTool]]] = {}
    for server in group:
        for tool in server.tools:
            declarations_by_tool.setdefault(tool.name, []).append((server, tool))
    tools = tuple(
        _merge_tool_declarations(declarations, overlay=overlay)
        for _, declarations in sorted(declarations_by_tool.items())
    )
    merged = replace(
        primary,
        default_tools_approval_mode=_merged_approval_mode(
            [server.default_tools_approval_mode for server in group]
        ),
        transport=overlay.transport,
        command=_unanimous(server.command for server in group),
        url_present=overlay.external,
        env_secret_names=overlay.env_secret_names,
        local_documentation=overlay.local_documentation,
        # Recomputed, not carried: the union of tools is what decides whether
        # any part of this server's surface was left unenumerated.
        wildcard=any(tool.name.endswith(".*") for tool in tools),
        tools=tools,
    )
    warning = duplicate_mcp_server_declaration_warning(
        primary.name,
        primary.source_ref,
        [server.source_ref for server in group[1:]],
    )
    omissions = [
        SourceSurfaceOmission(
            subject=_declaration_subject(server),
            reason=DUPLICATE_SERVER_DECLARATION,
            detail=(
                f"MCP server {server.name!r} is declared here and in "
                f"{primary.source_ref!r}. The declarations disagree, so this "
                "one did not enter the catalog as a surface of its own: its "
                "tools were merged into the other declaration and judged "
                "there, under the widest reading of the two."
            ),
            warning=warning,
        )
        for server in group[1:]
    ]
    return merged, [warning], omissions


def _server_overlay(group: Sequence[NormalizedMcpServer]) -> _ServerOverlay:
    return _ServerOverlay(
        transport=_unanimous(server.transport for server in group),
        local_documentation=all(server.local_documentation for server in group),
        env_secret_names=tuple(
            sorted({name for server in group for name in server.env_secret_names})
        ),
        external=any(server.url_present for server in group),
        auto_approved=any(
            _is_auto_approval(server.default_tools_approval_mode) for server in group
        ),
    )


def _merge_tool_declarations(
    declared: Sequence[tuple[NormalizedMcpServer, NormalizedMcpTool]],
    *,
    overlay: _ServerOverlay,
) -> NormalizedMcpTool:
    """One tool of a server whose declarations disagree.

    Runs for a tool only one file declares as well as for one several declare:
    the server-level overlay applies to both, and a single declaration merged
    against itself is itself.
    """

    primary_server = declared[0][0]
    declarations = [tool for _, tool in declared]
    primary = declarations[0]
    input_agrees = all(
        declaration.input_schema == primary.input_schema
        for declaration in declarations
    )
    output_agrees = all(
        declaration.output_schema == primary.output_schema
        for declaration in declarations
    )
    # Two files describing one tool with different schemas do not tell us which
    # one the agent will call, so the honest statement is that the interface is
    # not statically known — which is also what caps the tool's extraction
    # confidence in ``_tool_from_normalized``. Keeping one of the two schemas
    # and calling it the surface is the fail-open reading.
    unknown_schema = (
        any(declaration.unknown_schema for declaration in declarations)
        or not input_agrees
    )
    annotations = _merged_annotations(
        [dict(declaration.annotations) for declaration in declarations]
    )
    if unknown_schema:
        annotations["mcp_unknown_schema"] = True
    _apply_server_overlay(annotations, overlay)
    auth, auth_explicit = _merged_auth(declarations)
    approval_mode = _merged_approval_mode(
        [declaration.approval_mode for declaration in declarations]
    )
    return replace(
        primary,
        approval_mode=(
            _AUTO_APPROVAL_MODE if overlay.auto_approved else approval_mode
        ),
        input_schema=primary.input_schema if input_agrees else {},
        output_schema=primary.output_schema if output_agrees else {},
        annotations=annotations,
        auth=auth,
        auth_explicit=auth_explicit,
        owner=_unanimous(declaration.owner for declaration in declarations),
        unknown_schema=unknown_schema,
        rejected_binding_annotation_keys=tuple(
            sorted(
                {
                    key
                    for declaration in declarations
                    for key in declaration.rejected_binding_annotation_keys
                }
            )
        ),
        source_ref=primary_server.source_ref,
        source_path=primary_server.source_path,
        # Pinned for the same reason as the path: ``_tool_from_normalized``
        # falls back to the *server's* position, and the merged server keeps
        # the first file's. A line number from one file beside a path naming
        # another is worse than no line number.
        source_start_line=primary.source_start_line or primary_server.source_start_line,
        source_start_column=(
            primary.source_start_column or primary_server.source_start_column
        ),
    )


def _merged_annotations(
    annotation_sets: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Only what every declaration says, plus every risk claim any of them makes."""

    first, *rest = annotation_sets
    merged = {
        key: value
        for key, value in first.items()
        if all(other.get(key, _MISSING) == value for other in rest)
    }
    for key in _RISK_MONOTONE_ANNOTATIONS:
        if any(annotations.get(key) is True for annotations in annotation_sets):
            merged[key] = True
    for key in _RISK_UNION_ANNOTATIONS:
        values = sorted(
            {
                str(item)
                for annotations in annotation_sets
                for item in _as_sequence(annotations.get(key, _MISSING))
            }
        )
        if values:
            merged[key] = values
    if any(
        _is_auto_approval(annotations.get("mcp_default_tools_approval_mode"))
        for annotations in annotation_sets
    ):
        merged["mcp_default_tools_approval_mode"] = _AUTO_APPROVAL_MODE
    return merged


def _apply_server_overlay(
    annotations: dict[str, Any], overlay: _ServerOverlay
) -> None:
    if overlay.transport is None:
        annotations.pop("mcp_transport", None)
    else:
        annotations["mcp_transport"] = overlay.transport
    if not overlay.local_documentation:
        annotations.pop("mcp_local_documentation", None)
    if overlay.env_secret_names:
        annotations["mcp_env_secret_names"] = list(overlay.env_secret_names)
    if overlay.external:
        classes = _as_sequence(
            annotations.get("shipgate_permission_classes", _MISSING)
        )
        annotations["shipgate_permission_classes"] = sorted(
            {str(item) for item in classes} | {"external"}
        )
    if overlay.auto_approved:
        annotations["mcp_default_tools_approval_mode"] = _AUTO_APPROVAL_MODE


def _merged_auth(
    declarations: Sequence[NormalizedMcpTool],
) -> tuple[Any, bool]:
    """The authority claim the declarations jointly support.

    Scopes are unioned: a scope named by one declaration is one this server may
    hold, and dropping it would hide breadth. Everything else survives only
    unanimously — an auth type or a ``required`` flag two files disagree about
    is not something either of them established.

    A declaration the auth parser would *refuse* is returned untouched rather
    than merged, in both places it can appear: an ``auth`` that is not an
    object at all, and a ``scopes`` that is not a list. Rebuilding it as a
    well-formed mapping would launder an invalid declaration into a valid one
    through the merge, and the invalid-annotation record is the only thing that
    reports it.
    """

    explicit = any(declaration.auth_explicit for declaration in declarations)
    raws = [declaration.auth for declaration in declarations]
    if all(raw == raws[0] for raw in raws[1:]):
        return raws[0], explicit
    unreadable = next(
        (
            raw
            for declaration, raw in zip(declarations, raws, strict=True)
            if declaration.auth_explicit and not isinstance(raw, dict)
        ),
        _MISSING,
    )
    if unreadable is not _MISSING:
        return unreadable, explicit
    mappings = [raw for raw in raws if isinstance(raw, dict)]
    if not mappings:
        return raws[0], explicit
    merged = {
        key: value
        for key, value in mappings[0].items()
        if key != "scopes"
        and all(other.get(key, _MISSING) == value for other in mappings[1:])
    }
    invalid_scopes = next(
        (
            mapping["scopes"]
            for mapping in mappings
            if "scopes" in mapping and not isinstance(mapping["scopes"], list)
        ),
        _MISSING,
    )
    scopes = sorted(
        {
            scope
            for mapping in mappings
            for scope in mapping.get("scopes", [])
            if isinstance(mapping.get("scopes"), list) and isinstance(scope, str)
        }
    )
    if invalid_scopes is not _MISSING:
        merged["scopes"] = invalid_scopes
    elif scopes:
        merged["scopes"] = scopes
    return merged, explicit


def _merged_approval_mode(modes: Sequence[str | None]) -> str | None:
    if any(_is_auto_approval(mode) for mode in modes):
        return _AUTO_APPROVAL_MODE
    return _unanimous(modes)


def _is_auto_approval(mode: Any) -> bool:
    return isinstance(mode, str) and mode.strip().lower() == _AUTO_APPROVAL_MODE


def _unanimous(values: Iterable[Any]) -> Any:
    collected = list(values)
    first = collected[0]
    return first if all(value == first for value in collected[1:]) else None


def _as_sequence(value: Any) -> list[Any]:
    if value is _MISSING or value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def _declaration_subject(server: NormalizedMcpServer) -> str:
    pointer = server.source_pointer or f"/mcpServers/{server.name}"
    return f"{server.source_ref}#{pointer}"


def _load_mcp_json(
    path: Path, base_dir: Path
) -> tuple[list[NormalizedMcpServer], LoadedToolSource | None]:
    """Servers read from one ``.mcp.json``, or a warning-only source.

    The second element is the file-level result for a file that could not be
    read at all. It carries no tools, so it never collides with the per-server
    sources, and it keeps naming the file — which is the only thing there is to
    say about a file whose servers could not be read.
    """

    source_ref = _relative(path, base_dir)
    try:
        data, positions = load_structured_file_with_positions(path)
    except InputParseError as exc:
        return [], LoadedToolSource(
            source_id=f"mcp_json:{source_ref}",
            source_type="codex_config_mcp",
            warnings=[str(exc)],
        )
    raw_servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(raw_servers, dict):
        return [], LoadedToolSource(
            source_id=f"mcp_json:{source_ref}",
            source_type="codex_config_mcp",
            warnings=[
                f"Invalid MCP config {source_ref}: expected top-level "
                "`mcpServers` to be an object."
            ],
        )
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
    return enriched, None


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


def _annotations_from_tool_config(
    config: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    raw = config.get("annotations")
    base = dict(raw) if isinstance(raw, dict) else {}
    annotations, rejected = strip_untrusted_binding_annotations(base)
    for key in ("readOnlyHint", "destructiveHint", "openWorldHint", "idempotentHint"):
        if key in config:
            annotations[key] = config[key]
    if config.get("read_only") is True:
        annotations["readOnlyHint"] = True
    permission_classes = config.get("permission_classes") or config.get("permissions")
    if permission_classes is not None:
        annotations["shipgate_permission_classes"] = permission_classes
    return annotations, rejected


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
            if (
                rel == ".codex/config.toml"
                or rel.endswith("/.codex/config.toml")
                or child.name == ".mcp.json"
            ):
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
    "DUPLICATE_SERVER_DECLARATION",
    "NormalizedMcpServer",
    "NormalizedMcpTool",
    "load_codex_config_mcp_sources",
    "load_mcp_manifest_inventory",
    "normalize_codex_config_mcp_servers",
    "normalize_mcp_json_servers",
    "tools_from_normalized_mcp_servers",
]
