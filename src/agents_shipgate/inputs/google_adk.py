from __future__ import annotations

import ast
import copy as copy_module
import dataclasses
import hashlib
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

from agents_shipgate.core.artifact_models import (
    GoogleAdkArtifacts,
    GoogleAdkToolset,
    GoogleAdkToolsetConnection,
)
from agents_shipgate.core.domain import (
    ANY_TOOL,
    SURFACE_ENUMERATED,
    SURFACE_PARTIAL,
    AgentBindingObservation,
    AgentRemoteBinding,
    AuthInfo,
    LoadedToolSource,
    RemoteBindingStatus,
    Tool,
    ToolParameter,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.privacy import is_credential_key, redact_url_credentials
from agents_shipgate.core.source_warnings import adk_unresolved_tool_warning
from agents_shipgate.inputs.builder_calls import (
    BuilderCalls,
    ConstructionContext,
    Invocation,
)
from agents_shipgate.inputs.common import (
    load_structured_file,
    load_text_file,
    resolve_input_path,
    stable_tool_id,
)
from agents_shipgate.inputs.coverage import BoundaryCell, SourceCoverage
from agents_shipgate.inputs.list_expressions import (
    Conditions,
    ListExpressions,
    ListMember,
    ListResolution,
    evaluation_site,
    source_text,
    unread_list_reason,
    unread_parts,
)
from agents_shipgate.inputs.mcp import load_mcp_tools
from agents_shipgate.inputs.openapi import load_openapi_tools
from agents_shipgate.inputs.protocol import LoadedAdapterResult
from agents_shipgate.inputs.python_imports import (
    FACTORY_RETURN,
    LOCAL_BINDING,
    MODULE_NOT_FOUND,
    NOT_BOUND,
    OUTSIDE_SCOPE,
    ImportResolver,
    PythonModule,
    Resolution,
    ScopeIndex,
    _module_bindings,
    local_binding_detail,
    reference_spelling,
)
from agents_shipgate.inputs.traces import load_trace_artifacts
from agents_shipgate.schemas.manifest import (
    AgentsShipgateManifest,
    ArtifactPathConfig,
    ToolInventoryConfig,
    ToolSourceConfig,
)

AGENT_CLASS_NAMES = {
    "Agent",
    "LlmAgent",
    # The package root re-exports the agent: ``from google.adk import Agent``.
    "google.adk.Agent",
    "google.adk.agents.Agent",
    "google.adk.agents.LlmAgent",
    "google.adk.agents.llm_agent.Agent",
    "google.adk.agents.llm_agent.LlmAgent",
}
FUNCTION_TOOL_NAMES = {
    "FunctionTool",
    "google.adk.tools.FunctionTool",
    "google.adk.tools.function_tool.FunctionTool",
}
LONG_RUNNING_TOOL_NAMES = {
    "LongRunningFunctionTool",
    "google.adk.tools.LongRunningFunctionTool",
    "google.adk.tools.function_tool.LongRunningFunctionTool",
}
OPENAPI_TOOLSET_NAMES = {
    "OpenAPIToolset",
    "google.adk.tools.openapi_tool.openapi_spec_parser.openapi_toolset.OpenAPIToolset",
}
MCP_TOOLSET_NAMES = {
    "McpToolset",
    "MCPToolset",
    "google.adk.tools.mcp_tool.McpToolset",
    "google.adk.tools.mcp_tool.MCPToolset",
}
#: ADK connection-params constructors, mapped to the transport they mount.
#: Matched on the final dotted segment so an aliased or fully-qualified
#: spelling resolves the same way. A constructor outside this table is read as
#: ``unresolved`` transport rather than guessed at.
MCP_CONNECTION_TRANSPORTS = {
    "StreamableHTTPConnectionParams": "streamable_http",
    "StreamableHTTPServerParams": "streamable_http",
    "SseConnectionParams": "sse",
    "SseServerParams": "sse",
    "StdioConnectionParams": "stdio",
    "StdioServerParameters": "stdio",
}
#: Constructor arguments that carry credential material. ``headers`` is the
#: HTTP transports' spelling; ``env`` is stdio's — on ``StdioServerParameters``,
#: which ``StdioConnectionParams`` holds under ``server_params`` rather than
#: inline, so the nested call has to be resolved before this reads anything
#: (ADK 2.8.0 ``mcp_session_manager.StdioConnectionParams``; PR #540 review).
MCP_CREDENTIAL_ARG_NAMES = ("headers", "env")
#: The argument a stdio connection nests its server parameters under.
MCP_NESTED_PARAMS_ARG = "server_params"

#: Rendered in place of a credential value the reader read and will not
#: publish, and of one it could not read. Both keep the credential axis
#: *comparable*: adding a hardcoded credential beside an existing environment
#: reference changes this list, so it changes the carried summary and hash
#: rather than showing up only as a limitation the hash never saw (PR #540
#: review). Neither uses parentheses, so a one-entry list can never be mistaken
#: for a whole-summary status sentinel.
CREDENTIAL_WITHHELD_MARKER = "<literal credential withheld>"
CREDENTIAL_UNREADABLE_MARKER = "<not statically readable>"
#: ``os.environ`` / ``os.getenv`` spellings, after import-alias resolution.
_ENVIRON_MAPPING_NAMES = {"os.environ", "os.environb"}
_ENVIRON_GETTER_NAMES = {"os.getenv", "os.environ.get", "os.environb.get"}

#: Per-binding limitation codes. Each names what the reader could not
#: establish about *this* binding, so a reviewer never has to infer a silence.
LIMIT_SHADOWED_CONNECTION_CONSTRUCTOR = "shadowed_connection_constructor"
LIMIT_SHADOWED_TOOLSET_CONSTRUCTOR = "shadowed_toolset_constructor"
LIMIT_REBOUND_CONNECTION_REFERENCE = "rebound_connection_reference"
LIMIT_UNRESOLVED_CONNECTION_REFERENCE = "unresolved_connection_reference"
LIMIT_DYNAMIC_CONNECTION_EXPRESSION = "dynamic_connection_expression"
LIMIT_UNRECOGNIZED_CONNECTION_CONSTRUCTOR = "unrecognized_connection_constructor"
LIMIT_DYNAMIC_ENDPOINT_EXPRESSION = "dynamic_endpoint_expression"
LIMIT_ENDPOINT_CREDENTIALS_REDACTED = "endpoint_credentials_redacted"
LIMIT_LITERAL_CREDENTIAL_VALUE = "literal_credential_value"
LIMIT_DYNAMIC_CREDENTIAL_EXPRESSION = "dynamic_credential_expression"
LIMIT_UNRESOLVED_NESTED_PARAMS = "unresolved_nested_server_params"
LIMIT_DYNAMIC_TOOL_FILTER = "dynamic_tool_filter"
LIMIT_CONNECTION_NOT_READ_FROM_CONFIG = "connection_not_read_from_agent_config"

CALLBACK_KEYS = {
    "before_agent_callback",
    "after_agent_callback",
    "before_model_callback",
    "after_model_callback",
    "before_tool_callback",
    "after_tool_callback",
}
OPENAPI_PATH_KEYS = {"spec_path", "path", "spec_file", "openapi_path", "openapi_spec"}
MCP_INVENTORY_KEYS = {"inventory_path", "tool_inventory_path", "mcp_tools_path", "mcp_inventory"}
EVAL_PATH_KEYS = {"eval_set", "eval_sets", "eval_file", "eval_files", "eval_path", "eval_paths"}
# Python constructs that own a name binding. A ``variable = Agent(...)`` is
# reachable only from its own scope outward, so resolving a ``sub_agents``
# element has to respect them.
_SCOPE_NODES = (
    ast.Module,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ClassDef,
)
#: ``Tool.extraction["surface"]`` — whether this adapter *proved* the tool
#: surface it reports, rather than merely produced it.
#:
#: Before #393 the Python AST path hardcoded ``confidence="medium"``, so no ADK
#: repository could reach ``high`` from source however statically analysable it
#: was, and ``insufficient_evidence`` was the framework's default first-run
#: verdict rather than a property of any repository. A condition that holds for
#: every input carries no information: it could not tell a toolkit factory from
#: twelve module-level functions, and the remedy it prescribed — transcribe the
#: twelve tools Shipgate had just extracted correctly into an inventory — added
#: no fact to the system.
#:
#: ``SURFACE_ENUMERATED`` is therefore a claim the extractor has to earn on the
#: parsed module. Every construct that leaves any part of the surface
#: unresolved records a reason code below and holds the whole module at
#: ``medium``; absence of the attestation reads as incomplete everywhere
#: downstream, so a new ambiguity nobody classified fails closed.

#: Module-scoped reasons: one unresolved construct anywhere in the file means
#: this file's tool surface was not proven, so it holds every tool the file
#: produced. Scoping them per agent instead would let a fully-resolved agent in
#: a half-resolved module claim a proof the module cannot support.
SURFACE_GAP_DYNAMIC_TOOLS = "dynamic_tools_expression"
SURFACE_GAP_UNRESOLVED_REFERENCE = "unresolved_tool_reference"
SURFACE_GAP_UNRESOLVED_EXPRESSION = "unresolved_tool_expression"
SURFACE_GAP_UNRESOLVED_WRAPPER = "unresolved_tool_wrapper"
SURFACE_GAP_DYNAMIC_TOOLSET = "dynamic_toolset"
SURFACE_GAP_CONFLICTING_CONTRACT = "conflicting_tool_contract"
#: One agent binds two different definitions under one tool name — a local
#: ``lookup`` and an imported ``other.lookup``. The model sees one name for two
#: callables, so which one runs is not something the source settles (#864).
SURFACE_GAP_DUPLICATE_TOOL_NAME = "duplicate_tool_name"
#: One agent name constructed at more than one call site in a module: the
#: binding graph merges them, so which one runs is not established (#876). Each
#: row names the constructions that list its tool (#872).
SURFACE_GAP_DUPLICATE_AGENT_NAME = "duplicate_agent_name"
#: A class deriving from an ADK agent class: instances are built by calling the
#: subclass, which this reader does not follow, so their tools are unread (#876).
SURFACE_GAP_AGENT_SUBCLASS = "agent_subclass_unread"
SURFACE_GAP_UNRESOLVED_SUB_AGENT = "unresolved_sub_agent"
#: The module reaches an agent's ``tools`` attribute after construction, or
#: builds an agent from unpacked keyword arguments. Reading the ``tools=``
#: literal proves the surface only if that literal is the whole story;
#: ``agent.tools.append(imported)`` and ``Agent(**config)`` both make it not be.
SURFACE_GAP_MUTABLE_TOOL_BINDING = "mutable_tool_binding"
SURFACE_GAP_DYNAMIC_AGENT_KWARGS = "dynamic_agent_kwargs"
#: A ``tools=`` element resolved to a definition the name may not actually
#: refer to. ``self.functions`` is a flat, scope-blind name map, so it happily
#: answers with a function defined inside a factory, a method lifted out of a
#: class body, one of two conditional definitions, or a definition whose name
#: was later rebound. Naming the tool is still useful; claiming its signature
#: was proven is not.
SURFACE_GAP_SHADOWED_DEFINITION = "shadowed_tool_definition"
#: A call this adapter read as ``Agent``/``FunctionTool``/``*Toolset`` is only
#: that framework's constructor while the name still refers to the import.
#: ``from google.adk.tools import FunctionTool`` followed by
#: ``FunctionTool = replacement`` leaves ``_qualified_name`` resolving the stale
#: alias, so a foreign factory was read with Google's semantics (#400 review).
SURFACE_GAP_SHADOWED_FRAMEWORK_SYMBOL = "shadowed_framework_symbol"
#: ``from x import *`` can rebind any name in the module at import time, and
#: the binding table can only record it under ``"*"``. Nothing in the file is
#: provably what it appears to be, so nothing is proven.
SURFACE_GAP_STAR_IMPORT = "star_import_shadowing"
#: Emitted only by the fail-closed backstop in
#: ``_PythonAdkExtractor._resolve_extraction_evidence``:
#: a warning this module raised through neither surface helper. It means a new
#: ambiguity was added without deciding what it says about the surface, so the
#: module declines to claim one.
SURFACE_GAP_UNCLASSIFIED = "unclassified_extractor_warning"
#: Per-tool reasons: the module may be fully enumerated while one function's
#: own callable interface still is not.
SURFACE_GAP_UNTYPED_PARAMETER = "untyped_parameter"
SURFACE_GAP_VARIADIC_PARAMETERS = "variadic_parameters"
SURFACE_GAP_DECORATED_FUNCTION = "decorated_tool_function"
#: An annotation is present but the emitter cannot represent it, so the schema
#: it ships is a guess with better manners than an absent annotation's. See
#: :func:`_annotation_is_faithful`.
SURFACE_GAP_UNREPRESENTABLE_ANNOTATION = "unrepresentable_annotation"
#: An Agent Config lists tool *names*; there is no signature to read, so this
#: path never claims an enumerated surface.
SURFACE_GAP_TOOL_REFERENCE_ONLY = "tool_reference_only"
#: Reasons that are about *this callable's* interface rather than about which
#: tools exist. The distinction is load-bearing at exactly one place: a
#: reviewed tool inventory is a human statement about a tool's own schema, so
#: it can legitimately close these — that is what #386 is for — while no
#: per-tool assertion can establish that a module exposes no *other* tools. A
#: reason absent from this set therefore survives identity merging and keeps
#: the canonical tool below high (#400 review).
TOOL_INTERFACE_SURFACE_GAPS = frozenset(
    {
        SURFACE_GAP_UNTYPED_PARAMETER,
        SURFACE_GAP_VARIADIC_PARAMETERS,
        SURFACE_GAP_DECORATED_FUNCTION,
        SURFACE_GAP_UNREPRESENTABLE_ANNOTATION,
        SURFACE_GAP_TOOL_REFERENCE_ONLY,
    }
)
#: Names Google ADK injects rather than exposing to the model. ADK identifies
#: the injection by the parameter's *type*, with ``tool_context`` as a name
#: fallback; dropping every parameter spelled ``ctx`` or ``context`` deleted
#: ordinary model-visible inputs from the schema (#400 review).
ADK_CONTEXT_TYPE_NAMES = {
    "ToolContext",
    "CallbackContext",
    "ReadonlyContext",
    "google.adk.tools.ToolContext",
    "google.adk.tools.tool_context.ToolContext",
    "google.adk.agents.callback_context.CallbackContext",
    "google.adk.agents.readonly_context.ReadonlyContext",
}
ADK_CONTEXT_PARAMETER_NAME = "tool_context"


def remote_bindings_from_toolsets(
    toolsets: list[GoogleAdkToolset],
) -> list[AgentRemoteBinding]:
    """One :class:`AgentRemoteBinding` per agent that binds an MCP toolset.

    The toolset record is per *construction* — a toolset shared by two agents
    is one record — while a binding is per agent, because that is the unit a
    reviewer attributes and the unit that can change independently: one agent
    dropping a shared toolset is a real capability change for that agent only.

    Deterministic: emitted in the order the toolsets were read, deduped on the
    ``(source_id, agent, slot)`` identity the carriage keys on.
    """

    bindings: list[AgentRemoteBinding] = []
    seen: set[tuple[str, str, str]] = set()
    for toolset in toolsets:
        if toolset.kind != "mcp" or toolset.connection is None:
            continue
        connection = toolset.connection
        agents = toolset.binding_agents or (
            [toolset.agent_name] if toolset.agent_name else []
        )
        slot = toolset.slot or "#1"
        for agent in agents:
            identity = (toolset.source_id, agent, slot)
            if identity in seen:
                continue
            seen.add(identity)
            bindings.append(
                AgentRemoteBinding(
                    agent=agent,
                    source_id=toolset.source_id,
                    slot=slot,
                    provider="google_adk_mcp",
                    transport=connection.transport,
                    transport_status=connection.transport_status,
                    endpoint=connection.endpoint,
                    endpoint_status=connection.endpoint_status,
                    endpoint_env_ref=connection.endpoint_env_ref,
                    credential_refs=list(connection.credential_refs),
                    credential_status=connection.credential_status,
                    tool_filter=list(toolset.filter_values),
                    filter_status=connection.filter_status,
                    inventory_path=toolset.inventory_path,
                    limitations=list(connection.limitations),
                    source_ref=toolset.source_ref,
                )
            )
    return bindings


def _attach_remote_bindings(
    loaded_sources: list[LoadedToolSource],
    artifacts: GoogleAdkArtifacts,
) -> None:
    """Carry each source's remote bindings on its own ``LoadedToolSource``.

    Grouped by ``source_id`` so a workspace with several ADK sources keeps each
    source's bindings with the source that produced them; the aggregation in
    ``cli/scan`` then flattens them exactly the way ``toolkit_bounds`` are.
    """

    by_source: dict[str, list[AgentRemoteBinding]] = {}
    for binding in remote_bindings_from_toolsets(artifacts.toolsets):
        by_source.setdefault(binding.source_id, []).append(binding)
    if not by_source:
        return
    for loaded in loaded_sources:
        if loaded.source_type != "google_adk":
            continue
        bindings = by_source.get(loaded.source_id)
        if bindings:
            loaded.remote_bindings = bindings


def load_google_adk_artifacts(
    manifest: AgentsShipgateManifest | None,
    base_dir: Path,
    *,
    sources: list[ToolSourceConfig] | None = None,
) -> tuple[list[LoadedToolSource], GoogleAdkArtifacts | None]:
    # Discovery supplies source locations only; it makes no manifest claims.
    source_refs = [
        source for source in (sources if sources is not None else
                              manifest.tool_sources if manifest is not None else [])
        if source.type == "google_adk"
    ]
    config = manifest.google_adk if manifest is not None else None
    if not source_refs and (config is None or not config.has_inputs()):
        return [], None

    artifacts = GoogleAdkArtifacts()
    loaded_sources: list[LoadedToolSource] = []
    for source in source_refs:
        try:
            loaded_sources.extend(_load_google_adk_source(source, base_dir, artifacts))
        except InputParseError:
            if not source.optional:
                raise
            warning = f"Optional Google ADK source {source.id!r} failed to load."
            loaded_sources.append(
                LoadedToolSource(
                    source_id=source.id,
                    source_type="google_adk",
                    warnings=[warning],
                )
            )

    if config:
        for entrypoint in config.python_entrypoints:
            loaded_sources.extend(
                _load_python_ref(
                    entrypoint,
                    base_dir,
                    source_id=f"google_adk:{entrypoint.path}",
                    artifacts=artifacts,
                )
            )
        for agent_config in config.agent_configs:
            loaded_sources.extend(
                _load_agent_config_ref(
                    agent_config,
                    base_dir,
                    source_id=f"google_adk:{agent_config.path}",
                    artifacts=artifacts,
                )
            )
        for inventory in config.tool_inventories:
            loaded = _load_inventory_ref(
                inventory,
                base_dir,
                source_id=f"google_adk_inventory:{inventory.path}",
                artifacts=artifacts,
            )
            if loaded:
                loaded_sources.append(loaded)
        _load_eval_refs(config.eval_sets, base_dir, artifacts)
        files, traces = load_trace_artifacts(
            config.trace_samples,
            base_dir,
            artifacts.warnings,
            label="Google ADK",
            source_type="google_adk_trace",
        )
        artifacts.trace_sample_files.extend(files)
        artifacts.trace_samples.extend(traces)

    _attach_remote_bindings(loaded_sources, artifacts)
    return loaded_sources, artifacts


def _load_google_adk_source(
    source: ToolSourceConfig,
    base_dir: Path,
    artifacts: GoogleAdkArtifacts,
) -> list[LoadedToolSource]:
    assert source.path is not None
    ref = ArtifactPathConfig(path=source.path, optional=source.optional)
    path = _resolve_existing_path(ref, base_dir)
    if path.is_dir():
        candidate = path / "agent.py"
        if candidate.exists():
            return _load_python_path(candidate, base_dir, source.id, source.path, artifacts)
        raise InputParseError(f"Google ADK source directory has no agent.py: {path}")
    if path.suffix.lower() == ".py":
        return _load_python_path(path, base_dir, source.id, source.path, artifacts)
    return _load_agent_config_path(path, path.parent, source.id, source.path, artifacts)


def _load_python_ref(
    ref: ArtifactPathConfig,
    base_dir: Path,
    *,
    source_id: str,
    artifacts: GoogleAdkArtifacts,
) -> list[LoadedToolSource]:
    try:
        path = _resolve_existing_path(ref, base_dir)
    except InputParseError:
        if not ref.optional:
            raise
        artifacts.warnings.append(f"Optional Google ADK Python entrypoint {ref.path!r} failed to load.")
        return []
    return _load_python_path(path, base_dir, source_id, ref.path, artifacts)


def _load_agent_config_ref(
    ref: ArtifactPathConfig,
    base_dir: Path,
    *,
    source_id: str,
    artifacts: GoogleAdkArtifacts,
) -> list[LoadedToolSource]:
    try:
        path = _resolve_existing_path(ref, base_dir)
    except InputParseError:
        if not ref.optional:
            raise
        artifacts.warnings.append(f"Optional Google ADK Agent Config {ref.path!r} failed to load.")
        return []
    return _load_agent_config_path(path, path.parent, source_id, ref.path, artifacts)


def _load_inventory_ref(
    ref: ToolInventoryConfig,
    base_dir: Path,
    *,
    source_id: str,
    artifacts: GoogleAdkArtifacts,
) -> LoadedToolSource | None:
    source = ToolSourceConfig(id=source_id, type="mcp", path=ref.path, optional=ref.optional)
    try:
        loaded = load_mcp_tools(source, base_dir)
    except InputParseError:
        if not ref.optional:
            raise
        artifacts.warnings.append(f"Optional Google ADK tool inventory {ref.path!r} failed to load.")
        return None
    artifacts.tool_inventory_files.append(_display_path(resolve_input_path(base_dir, ref.path), base_dir))
    for tool in loaded.tools:
        tool.source_type = "google_adk_inventory"
        tool.annotations["adk_inventory"] = True
    loaded.completes_source_id = ref.source_id
    loaded.is_tool_inventory = True
    return loaded


def _load_eval_refs(
    refs: list[ArtifactPathConfig],
    base_dir: Path,
    artifacts: GoogleAdkArtifacts,
) -> None:
    for ref in refs:
        try:
            path = _resolve_existing_path(ref, base_dir)
            load_structured_file(path)
        except InputParseError:
            if not ref.optional:
                raise
            artifacts.warnings.append(f"Optional Google ADK eval artifact {ref.path!r} failed to load.")
            continue
        _append_unique(artifacts.eval_files, _display_path(path, base_dir))


def _load_python_path(
    path: Path,
    base_dir: Path,
    source_id: str,
    source_ref: str,
    artifacts: GoogleAdkArtifacts,
) -> list[LoadedToolSource]:
    text = load_text_file(path)
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        raise InputParseError(f"Unable to parse Google ADK Python entrypoint {path}: {exc.msg}") from exc
    artifacts.python_entrypoints.append(_display_path(path, base_dir))
    # Repository-local imports are followed inside the directory this read was
    # given, never above it (#864).
    resolver = ImportResolver(base_dir)
    extractor = _PythonAdkExtractor(
        tree,
        source_id,
        source_ref,
        path.parent,
        base_dir,
        artifacts,
        resolver=resolver,
        module=resolver.entry(path, tree, text),
    )
    return extractor.extract()


def _load_agent_config_path(
    path: Path,
    config_base_dir: Path,
    source_id: str,
    source_ref: str,
    artifacts: GoogleAdkArtifacts,
    *,
    seen: set[Path] | None = None,
) -> list[LoadedToolSource]:
    seen = seen or set()
    resolved = path.resolve()
    if resolved in seen:
        artifacts.warnings.append(f"Skipping recursive Google ADK Agent Config {path}")
        return []
    seen.add(resolved)
    data = load_structured_file(path)
    if not isinstance(data, dict):
        raise InputParseError(f"Google ADK Agent Config must contain an object: {path}")

    artifacts.agent_config_files.append(_display_path(path, config_base_dir))
    agent_name = str(data.get("name") or path.stem)
    raw_tools = data.get("tools")
    if isinstance(raw_tools, list):
        tools_data = raw_tools
    else:
        # ``tools`` absent (None) is a genuine zero-tool agent. But ``tools``
        # present in a shape we cannot enumerate — a templated string, an
        # env-var reference, a mapping — must NOT silently collapse to a
        # confident ``tool_count: 0``; that reads as deliberate narrowing and
        # fails open. Record it as a dynamic/unparseable surface (warning +
        # dynamic toolset marker), mirroring the Python entrypoint's
        # dynamic-tools-expression handling, so evidence-coverage and the ADK
        # dynamic-toolset checks treat the surface as unknown.
        tools_data = []
        if raw_tools is not None:
            artifacts.warnings.append(
                f"Google ADK agent {agent_name!r} declares a dynamic or "
                f"unparseable 'tools' value in its Agent Config; its tool "
                f"surface could not be enumerated."
            )
            artifacts.toolsets.append(
                GoogleAdkToolset(
                    kind="dynamic",
                    source_id=source_id,
                    source_ref=source_ref,
                    agent_name=agent_name,
                    dynamic=True,
                )
            )
    artifacts.agents.append(
        {
            "name": agent_name,
            "source_ref": source_ref,
            "instruction_present": bool(data.get("instruction")),
            "instruction_preview": _string_or_none(data.get("instruction")),
            "tool_count": len(tools_data),
        }
    )
    _record_config_callbacks_and_plugins(data, source_ref, agent_name, artifacts)
    _record_config_eval_refs(data, config_base_dir, artifacts)

    tools: list[Tool] = []
    loaded_sources: list[LoadedToolSource] = []
    for index, raw_tool in enumerate(tools_data):
        loaded_sources.extend(
            _tool_from_config_entry(
                raw_tool,
                index=index,
                agent_name=agent_name,
                source_id=source_id,
                source_ref=source_ref,
                config_base_dir=config_base_dir,
                artifacts=artifacts,
                tools=tools,
            )
        )

    for sub_agent in data.get("sub_agents") or []:
        if not isinstance(sub_agent, dict):
            continue
        config_path = sub_agent.get("config_path")
        if not isinstance(config_path, str) or not config_path:
            continue
        artifacts.sub_agents.append(
            {
                "agent_name": agent_name,
                "config_path": config_path,
                "source_ref": source_ref,
            }
        )
        sub_path = resolve_input_path(config_base_dir, config_path)
        loaded_sources.extend(
            _load_agent_config_path(
                sub_path,
                sub_path.parent,
                source_id=source_id,
                source_ref=f"{source_ref}:{config_path}",
                artifacts=artifacts,
                seen=seen,
            )
        )

    return [
        LoadedToolSource(
            source_id=source_id,
            source_type="google_adk",
            tools=tools,
            warnings=[],
        ),
        *loaded_sources,
    ]


def _tool_from_config_entry(
    raw_tool: Any,
    *,
    index: int,
    agent_name: str,
    source_id: str,
    source_ref: str,
    config_base_dir: Path,
    artifacts: GoogleAdkArtifacts,
    tools: list[Tool],
) -> list[LoadedToolSource]:
    name: str | None = None
    args: dict[str, Any] = {}
    if isinstance(raw_tool, str):
        name = raw_tool
    elif isinstance(raw_tool, dict):
        raw_name = raw_tool.get("name") or raw_tool.get("tool")
        if isinstance(raw_name, str):
            name = raw_name
        args = _args_to_dict(raw_tool.get("args"))
        for key, value in raw_tool.items():
            if key not in {"name", "tool", "args"}:
                args.setdefault(key, value)
    if not name:
        artifacts.warnings.append(f"Google ADK Agent Config {source_ref} has a tool without a name.")
        return []

    location = f"{source_ref}#/tools/{index}"
    if _looks_like_openapi_toolset(name):
        return _record_config_openapi_toolset(name, args, agent_name, source_id, location, config_base_dir, artifacts)
    if _looks_like_mcp_toolset(name):
        return _record_config_mcp_toolset(name, args, agent_name, source_id, location, config_base_dir, artifacts)

    tool = Tool(
        id=stable_tool_id(name),
        name=_short_tool_name(name),
        description=_string_or_none(args.get("description")) or f"Google ADK tool reference: {name}",
        source_type="google_adk_config",
        source_id=source_id,
        source_ref=location,
        source_location=location,
        annotations={"adk_tool_reference": name, "agent_name": agent_name},
        auth=AuthInfo(source="google_adk_config"),
        extraction_confidence="low",
        extraction={
            "method": "google_adk_agent_config",
            "confidence": "low",
            "surface": SURFACE_PARTIAL,
            "surface_gaps": [SURFACE_GAP_TOOL_REFERENCE_ONLY],
        },
    )
    tools.append(tool)
    artifacts.function_tools.append(
        {
            "name": tool.name,
            "source_ref": location,
            "agent_name": agent_name,
            "metadata_present": bool(args.get("description") or args.get("parameters")),
        }
    )
    _record_tool_binding(
        artifacts, agent_name=agent_name, tool_name=tool.name, source_ref=location
    )
    return []


def _record_config_openapi_toolset(
    name: str,
    args: dict[str, Any],
    agent_name: str,
    source_id: str,
    location: str,
    config_base_dir: Path,
    artifacts: GoogleAdkArtifacts,
) -> list[LoadedToolSource]:
    spec_path = _first_string_arg(args, OPENAPI_PATH_KEYS)
    toolset = GoogleAdkToolset(
        kind="openapi",
        source_id=source_id,
        source_ref=location,
        agent_name=agent_name,
        name=name,
        resolved=bool(spec_path),
        dynamic=not bool(spec_path),
        binding_agents=[agent_name],
        slot=_config_toolset_slot(artifacts, agent_name, source_id),
    )
    artifacts.toolsets.append(toolset)
    if not spec_path:
        artifacts.warnings.append(
            f"Google ADK OpenAPIToolset at {location} has no static local spec path."
        )
        return []
    loaded = load_openapi_tools(
        ToolSourceConfig(id=f"{source_id}:openapi:{len(artifacts.toolsets)}", type="openapi", path=spec_path),
        config_base_dir,
    )
    for tool in loaded.tools:
        tool.annotations["adk_toolset"] = "OpenAPIToolset"
        tool.annotations["adk_agent_name"] = agent_name
        _record_tool_binding(
            artifacts, agent_name=agent_name, tool_name=tool.name, source_ref=location
        )
    return [loaded]


def _config_toolset_slot(
    artifacts: GoogleAdkArtifacts,
    agent_name: str,
    source_id: str,
) -> str:
    """1-based order of this agent's toolsets in one ADK agent config.

    The array position rather than the file line, for the same reason the
    Python path uses an ordinal: reformatting the config must not read as a
    changed binding. Counted within the *source*, because ``artifacts`` is
    shared across every configured source and two of them may each declare an
    agent of the same name.
    """

    existing = sum(
        1
        for toolset in artifacts.toolsets
        if toolset.source_id == source_id and agent_name in toolset.binding_agents
    )
    return f"#{existing + 1}"


def _config_mcp_connection(
    args: dict[str, Any],
    filtered: bool,
) -> GoogleAdkToolsetConnection:
    """The connection an ADK *agent config* declares, as far as it is read.

    Only the literal endpoint is read here. Credential material in a YAML
    config is written in idioms this reader does not interpret (``${VAR}``
    interpolation, a deployment secret store), so the credential axis reports
    ``not_read`` — a statement about the reader — rather than ``absent``,
    which would claim the binding carries no credential at all.
    """

    limitations = [LIMIT_CONNECTION_NOT_READ_FROM_CONFIG]
    params = args.get("connection_params")
    url = params.get("url") if isinstance(params, dict) else None
    endpoint: str | None = None
    endpoint_status: RemoteBindingStatus = "not_read"
    if isinstance(params, dict):
        if isinstance(url, str) and url:
            endpoint, withheld = redact_url_credentials(url)
            endpoint_status = "literal"
            if withheld:
                limitations.append(LIMIT_ENDPOINT_CREDENTIALS_REDACTED)
        elif url is None:
            endpoint_status = "absent"
        else:
            endpoint_status = "unresolved"
            limitations.append(LIMIT_DYNAMIC_ENDPOINT_EXPRESSION)
    return GoogleAdkToolsetConnection(
        endpoint=endpoint,
        endpoint_status=endpoint_status,
        filter_status="literal" if filtered else "absent",
        limitations=sorted(set(limitations)),
    )


def _record_config_mcp_toolset(
    name: str,
    args: dict[str, Any],
    agent_name: str,
    source_id: str,
    location: str,
    config_base_dir: Path,
    artifacts: GoogleAdkArtifacts,
) -> list[LoadedToolSource]:
    filter_values = _string_list(args.get("tool_filter"))
    inventory_path = _first_string_arg(args, MCP_INVENTORY_KEYS)
    connection = _config_mcp_connection(args, bool(filter_values))
    toolset = GoogleAdkToolset(
        kind="mcp",
        source_id=source_id,
        source_ref=location,
        agent_name=agent_name,
        name=name,
        filtered=bool(filter_values),
        filter_values=filter_values,
        inventory_path=inventory_path,
        resolved=bool(inventory_path),
        dynamic=not bool(inventory_path),
        binding_agents=[agent_name],
        slot=_config_toolset_slot(artifacts, agent_name, source_id),
        connection=connection,
    )
    artifacts.toolsets.append(toolset)
    if not inventory_path:
        artifacts.warnings.append(
            adk_mcp_inventory_warning(
                location,
                agent_name=agent_name,
                endpoint=_endpoint_phrase(connection),
            )
        )
        return []
    loaded = load_mcp_tools(
        ToolSourceConfig(id=f"{source_id}:mcp:{len(artifacts.toolsets)}", type="mcp", path=inventory_path),
        config_base_dir,
    )
    for tool in loaded.tools:
        tool.annotations["adk_toolset"] = "McpToolset"
        tool.annotations["adk_agent_name"] = agent_name
        _record_tool_binding(
            artifacts, agent_name=agent_name, tool_name=tool.name, source_ref=location
        )
    return [loaded]


@dataclass
class _AdkAgentBinding:
    """One agent's ordered tool bindings inside a single ADK Python module.

    An ADK tool object may be bound to any number of agents (the canonical
    multi-agent shape shares one ``FunctionTool`` between a coordinator and
    its sub-agents). The underlying function is one capability, so it must
    enter the catalog exactly once; the many-to-many agent relation is
    carried here and published as ``AgentBindingObservation`` instead.
    """

    agent: str
    source_pointer: str
    tool_names: list[str] = field(default_factory=list)
    #: ``tool_name -> native locator`` for tools bound from a function
    #: definition, so a same-named definition elsewhere stays distinct (#864).
    tool_locators: dict[str, str] = field(default_factory=dict)
    #: ``tool_name -> file:line`` of that definition, for the reader.
    tool_locations: dict[str, str] = field(default_factory=dict)
    #: Names bound to two different definitions: neither is bound (#879).
    duplicated: set[str] = field(default_factory=set)
    #: ``tool_name -> why`` for a binding made on a guess (#879 review).
    tool_issues: dict[str, str] = field(default_factory=dict)
    constructor_issue: str | None = None
    constructor_issues: dict[str, str] = field(default_factory=dict)
    #: Why this agent's tool list is incomplete, when it is.
    issues: list[str] = field(default_factory=list)
    #: Every ``(tool, locator)`` one construction of this name asked to bind,
    #: bound before or not, while ``extract`` reads that construction (#876
    #: review); None between constructions.
    recording: list[tuple[str, str | None]] | None = field(default=None, repr=False)
    #: ``tool_name -> lines`` of the constructions that list it (#872).
    tool_sites: dict[str, list[str]] = field(default_factory=dict)
    handoffs_complete: bool = True
    handoff_sites: dict[str, list[str]] = field(default_factory=dict)
    #: What each tool is bound under, when only under a condition (#909).
    when: Conditions = field(default_factory=Conditions, repr=False)

    def bind(
        self, tool_name: str, locator: str | None = None, location: str | None = None
    ) -> bool:
        """Add one tool to this agent; return False if it was already bound."""

        if self.recording is not None:
            self.recording.append((tool_name, locator))
        if tool_name in self.tool_names or tool_name in self.duplicated:
            return False
        self.tool_names.append(tool_name)
        if locator is not None:
            self.tool_locators[tool_name] = locator
        if location is not None:
            self.tool_locations[tool_name] = location
        return True

    def binds_other_definition(self, tool_name: str, locator: str) -> bool:
        """Whether ``tool_name`` is already bound to a *different* definition."""

        bound = self.tool_locators.get(tool_name)
        return bound is not None and bound != locator

    def unbind_duplicate(self, tool_name: str, reason: str) -> None:
        """Two definitions share ``tool_name``: bind neither, whatever the order.

        Keeping the first one listed let list order decide which definition the
        agent was reported to call (#879 review).
        """

        self.duplicated.add(tool_name)
        self.tool_issues.pop(tool_name, None)
        self.tool_names = [name for name in self.tool_names if name != tool_name]
        self.tool_locators.pop(tool_name, None)
        self.tool_locations.pop(tool_name, None)
        if reason not in self.issues:
            self.issues.append(reason)


def _record_tool_binding(
    artifacts: GoogleAdkArtifacts,
    *,
    agent_name: str,
    tool_name: str,
    source_ref: str,
) -> None:
    """Record one agent -> tool binding edge.

    Bindings are counted separately from tool definitions so a shared tool
    stays one entry in ``function_tools`` while every agent that can call it
    remains visible to reviewers.
    """

    artifacts.tool_bindings.append(
        {
            "agent_name": agent_name,
            "tool_name": tool_name,
            "source_ref": source_ref,
        }
    )


class _PythonAdkExtractor:
    def __init__(
        self,
        tree: ast.Module,
        source_id: str,
        source_ref: str,
        entrypoint_dir: Path,
        base_dir: Path,
        artifacts: GoogleAdkArtifacts,
        *,
        resolver: ImportResolver | None = None,
        module: PythonModule | None = None,
    ) -> None:
        self.tree = tree
        self.scopes = ScopeIndex(tree)
        self.source_id = source_id
        self.source_ref = source_ref
        self.entrypoint_dir = entrypoint_dir
        self.base_dir = base_dir
        self.artifacts = artifacts
        # Follows a tool reference into a sibling module (#864). None when the
        # entrypoint lies outside the directory the read was given, in which
        # case an imported name stays unresolved exactly as before.
        self.resolver = resolver
        self.module = module
        self.aliases = _import_aliases(tree)
        self.functions = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        # Every binding occurrence of every name in the module. The maps above
        # are flat and scope-blind by design — they have to be, to name a tool
        # at all — so this is what says whether their answer is also a *proof*.
        # Read through ``_name_is_proven``.
        self.name_bindings = _name_binding_occurrences(tree)
        self.wrappers = self._wrapper_assignments()
        self.toolset_assignments = self._toolset_assignments()
        # One canonical Tool per function definition, keyed by the def name.
        # Every later binding of the same definition reuses this entry.
        self.canonical_function_tools: dict[str, Tool] = {}
        # The same for definitions reached through a repository-local import,
        # keyed by the defining module and line: two modules may each define
        # ``lookup``, and those are two tools (#864).
        self.imported_function_tools: dict[tuple[str, int], Tool] = {}
        # ``(aliases, name bindings)`` per defining module, for the annotation
        # and shadowing checks that module's own spelling decides.
        self.module_names: dict[str, tuple[dict[str, str], dict[str, list[ast.AST]]]] = {}
        # Scope indexes of the modules a tool factory is read in (#865).
        self.module_scopes: dict[str, ScopeIndex] = {}
        # ``(module, name) -> line`` where a module-level factory tool is changed.
        self.module_changes: dict[tuple[str, str], int | None] = {}
        # ``module -> name -> lines`` of the functions a tool can be made from.
        self.module_function_lines: dict[str, dict[str, list[int]]] = {}
        # Tool names produced by one toolset construction, keyed by the AST
        # call node. A toolset assigned to a variable and shared between
        # agents is loaded once, not once per agent.
        self.toolset_tool_names: dict[int, list[str]] = {}
        # The toolset record produced by one construction, keyed by AST call
        # node, so a second agent binding a shared toolset variable is recorded
        # against the same binding rather than starting a new one.
        self.toolset_records: dict[int, GoogleAdkToolset] = {}
        # Module-level variable a toolset construction was assigned to.
        self.toolset_variable_names: dict[int, str] = {
            id(call): name for name, call in self.toolset_assignments.items()
        }
        # Module-level assignments whose value is a recognized connection-params
        # construction, so ``connection_params=params`` resolves.
        self.connection_assignments = self._connection_assignments()
        # Per-agent counter for inline toolset constructions. A slot is an
        # ordinal within one agent's tool list, never a line number.
        self.inline_slot_counts: dict[str, int] = {}
        self.agent_bindings: dict[str, _AdkAgentBinding] = {}
        #: ``agent name -> {id(call): (line, signature)}`` for every construction
        #: site with a literal tool list; equal signatures bind the same (#876).
        self.agent_sites: dict[str, dict[int, tuple[int, object]]] = {}
        # Reasons this module's tool surface was not proven complete (#393).
        # Empty at the end of ``extract`` is what earns ``SURFACE_ENUMERATED``.
        self.surface_gaps: list[str] = []
        # Warnings this module raised through ``_surface_warning`` or
        # ``_note_warning``. Compared against the real growth of
        # ``artifacts.warnings`` so an unclassified append fails closed.
        self._accounted_warnings = 0
        # Every ``Agent(...)`` assignment in this module, walked once and
        # reused: ``extract`` iterates it, and the sub-agent spelling map
        # below is built from it.
        self.agent_call_list = self._agent_calls()
        # Built on first use: most constructions spell a literal list.
        self._lists: ListExpressions | None = None
        self._builder_calls = BuilderCalls(self.resolver) if self.resolver is not None else None
        self._invocation: Invocation | None = None
        self.parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        self.agent_names_by_variable = self._agent_names_by_variable()

    @property
    def lists(self) -> ListExpressions:
        """Members of a ``tools=`` or ``sub_agents=`` expression (#909)."""

        if self._lists is None:
            self._lists = ListExpressions(
                ref=self.source_ref,
                tree=self.tree,
                scopes=self.scopes,
                bindings=self.module.bindings if self.module is not None else _module_bindings(self.tree)[0],
                module=self.module,
                resolver=self.resolver,
                agent_reads=lambda call, keyword: self.module is not None
                and keyword in {"tools", "sub_agents"} | CALLBACK_KEYS
                and self._is_agent_call(call) and self._agent_constructor_issue(call) is None,
                builder_calls=self._builder_calls,
                module_agent_reads=self._module_agent_reads,
            )
        return self._lists

    def extract(self) -> list[LoadedToolSource]:
        tools: list[Tool] = []
        loaded_sources: list[LoadedToolSource] = []
        warnings_before = len(self.artifacts.warnings)
        self._record_eval_references()
        self._record_star_imports()
        self._record_mutable_tool_bindings()
        construction_sites = [
            (target_name, call, context)
            for target_name, call in self.agent_call_list
            for context in (
                self._builder_calls.contexts(self.module, call)
                if self._builder_calls is not None and self.module is not None
                else (ConstructionContext(),)
            )
        ]
        for target_name, call, context in construction_sites:
            self._invocation = context.invocation
            agent_name = _kwarg_string(call, "name") or target_name or "adk_agent"
            if self._invocation is not None and target_name is None and _kwarg_string(call, "name") is None:
                binding = self._binding_for(agent_name, call)
                message = (
                    f"Google ADK agent at {self.source_ref}:{call.lineno} has no literal or "
                    "assigned identity; its caller-supplied name is not read."
                )
                self._surface_warning(message, SURFACE_GAP_DYNAMIC_TOOLS)
                if message not in binding.issues:
                    binding.issues.append(message)
                binding.handoffs_complete = False
            if any(self.lists.construction_changed(self._invocation, field) for field in ("tools", "sub_agents")):
                binding = self._binding_for(agent_name, call)
                message = f"Google ADK agent {agent_name!r}: the caller's returned agent handle may change its capability lists."
                self._surface_warning(message, SURFACE_GAP_DYNAMIC_TOOLS)
                if message not in binding.issues:
                    binding.issues.append(message)
                binding.handoffs_complete = False
            if self.lists.constructor_changed(call, self._invocation):
                binding = self._binding_for(agent_name, call)
                message = (
                    f"Google ADK agent {agent_name!r}: its builder's constructor or "
                    "imports may change its capability lists."
                )
                self._surface_warning(message, SURFACE_GAP_DYNAMIC_TOOLS)
                if message not in binding.issues:
                    binding.issues.append(message)
                binding.handoffs_complete = False
            if context.limits:
                binding = self._binding_for(agent_name, call)
                for limit in context.limits:
                    message = f"Google ADK agent {agent_name!r}: {limit}"
                    self._surface_warning(message, SURFACE_GAP_DYNAMIC_TOOLS)
                    if message not in binding.issues:
                        binding.issues.append(message)
            # The call is only ADK's ``Agent`` while the name still refers to
            # the import it was resolved through.
            self._require_proven_framework_symbol(call)
            constructor_issue = self._agent_constructor_issue(call)
            if constructor_issue is None and self.lists.constructor_namespace_changed():
                constructor_issue = self.lists.constructor_namespace_issue or "an agent handle may change or hand on a tool carrying the constructor namespace"
            if constructor_issue is not None:
                binding = self._binding_for(agent_name, call)
                message = f"Google ADK agent {agent_name!r}: its constructor identity is not established: {constructor_issue}."
                self._surface_warning(message, SURFACE_GAP_SHADOWED_FRAMEWORK_SYMBOL)
                if message not in binding.issues:
                    binding.issues.append(message)
                binding.constructor_issue = message
                binding.constructor_issues[f"{self.source_ref}:{call.lineno}"] = message
                binding.handoffs_complete = False
            if any(keyword.arg is None for keyword in call.keywords):
                # ``Agent(**config)`` hides every keyword, ``tools`` included.
                # Without ``tools=`` the agent silently records tool_count 0,
                # which would otherwise read as a proven empty surface.
                self._note_surface_gap(SURFACE_GAP_DYNAMIC_AGENT_KWARGS)
            tools_expr = _kwarg(call, "tools")
            tool_count = len(tools_expr.elts) if isinstance(tools_expr, (ast.List, ast.Tuple)) else 0
            self.artifacts.agents.append(
                {
                    "name": agent_name,
                    "source_id": self.source_id,
                    "source_ref": self.source_ref,
                    "instruction_present": bool(_kwarg_string(call, "instruction")),
                    "instruction_preview": _kwarg_string(call, "instruction"),
                    "tool_count": tool_count,
                }
            )
            handoffs_at = len(self.artifacts.sub_agents)
            self._record_agent_callbacks_plugins_subagents(call, agent_name)
            handoffs = self.artifacts.sub_agents[handoffs_at:]
            flowed = self._local_tool_list(tools_expr, call) if isinstance(tools_expr, ast.Name) and self._invocation is None else None
            if flowed is not None:
                # ``tools = [...]`` built in the agent's function (#865): its
                # members read as a literal list would be.
                elements, conditional = flowed
                self.artifacts.agents[-1]["tool_count"] = len(elements) + len(conditional)
                binding = self._binding_for(agent_name, call)
                loaded_sources.extend(
                    self._read_construction(
                        call, agent_name, binding, [ListMember(item, None) for item in elements], tools, handoffs
                    )
                )
                for line in conditional:
                    message = (
                        f"Google ADK agent {agent_name!r} adds a tool to its tools list only under a "
                        f"condition or in a loop at {self.source_ref}:{line}, which is not established."
                    )
                    self._surface_warning(message, SURFACE_GAP_DYNAMIC_TOOLS)
                    if message not in binding.issues:
                        binding.issues.append(message)
                if conditional:
                    # Not the same agent as another construction of its name.
                    self.agent_sites.setdefault(agent_name, {})[(id(call), ())] = (call.lineno, call)
                continue
            if tools_expr is None:
                # No tools, but a construction all the same: two of one name
                # that hand off differently are not one agent (#909 review).
                binding = self._binding_for(agent_name, call)
                loaded_sources.extend(
                    self._read_construction(call, agent_name, binding, [], tools, handoffs)
                )
                continue
            if self._invocation is None and isinstance(tools_expr, ast.List | ast.Tuple) and not any(
                isinstance(item, ast.Starred) for item in tools_expr.elts
            ):
                members = [ListMember(item, None) for item in tools_expr.elts]
                unread = None
            else:
                # ``[*BASE, *([extra] if wanted else [])]``, ``base + extra``,
                # a module list, a filter (#909): every member it can hold.
                listed = self.lists.resolve(tools_expr, invocation=self._invocation)
                members = list(listed.members)
                self.artifacts.agents[-1]["tool_count"] = len({id(member.expr) for member in members})
                unread = listed if listed.unresolved else None
                if _at_risk(listed):
                    # Read, but not proven for ``scan``: a list is checked for
                    # changes only in the module that binds it, and another
                    # module could change it. No warning, so the comparison
                    # reads it.
                    self._note_surface_gap(SURFACE_GAP_DYNAMIC_TOOLS)
            binding = self._binding_for(agent_name, call)
            if unread is not None:
                pointer = f"{self.source_ref}:{call.lineno}"
                message = unread_list_reason("Google ADK", agent_name, pointer, unread)
                self._surface_warning(message, SURFACE_GAP_DYNAMIC_TOOLS)
                if message not in binding.issues:
                    binding.issues.append(message)
                self.artifacts.toolsets.append(
                    GoogleAdkToolset(
                        kind="dynamic",
                        source_id=self.source_id,
                        source_ref=pointer,
                        agent_name=agent_name,
                        dynamic=True,
                    )
                )
            loaded_sources.extend(
                self._read_construction(call, agent_name, binding, members, tools, handoffs)
            )
        for record in self.artifacts.sub_agents:
            binding = self.agent_bindings.get(record.get("agent_name"))
            if binding is not None and binding.constructor_issue is not None and "sub_agent_count" in record:
                record["unresolved_sub_agents"] = list(dict.fromkeys(
                    [*record.get("unresolved_sub_agents", []), *record.get("sub_agents", [])]
                ))
                record["unread"] = binding.constructor_issue
        self._record_duplicate_constructions()
        self._record_agent_subclasses()
        self._resolve_extraction_evidence(warnings_before, loaded_sources)
        return [
            LoadedToolSource(
                source_id=self.source_id,
                source_type="google_adk",
                tools=tools,
                warnings=[],
                binding_observations=self._binding_observations(),
            ),
            *loaded_sources,
        ]

    def _read_construction(
        self,
        call: ast.Call,
        agent_name: str,
        binding: _AdkAgentBinding,
        members: list[ListMember],
        tools: list[Tool],
        handoffs: list[dict[str, Any]],
    ) -> list[LoadedToolSource]:
        """Read one construction's tools, and note what it binds as its site.

        Every construction of one name shares a binding, so a second one's
        tools are recorded as it asks for them, bound before or not. A site is
        comparable only when it was read cleanly — every tool a definition, no
        warning, no new issue, no toolset, no ``**`` and no unresolved or
        dynamic handoff; any other site differs from every site (#876 review).
        """

        warnings_at = len(self.artifacts.warnings)
        state_at = (list(binding.issues), dict(binding.tool_issues), set(binding.duplicated))
        binding.recording = []
        loaded: list[LoadedToolSource] = []
        conditioned: set[tuple[str, str]] = set()
        always: set[str] = set()
        try:
            for member in members:
                at = len(binding.recording)
                loaded.extend(self._extract_member(member, tools, agent_name, binding))
                for tool_name, _ in binding.recording[at:]:
                    if tool_name in binding.tool_names:
                        if member.invocation is not None:
                            locations = binding.tool_sites.setdefault(tool_name, [])
                            for location in member.invocation.locations:
                                if location not in locations:
                                    locations.append(location)
                        binding.when.add(tool_name, member.conditions)
                        if member.conditions:
                            conditioned.add((tool_name, " and ".join(member.conditions)))
                        else:
                            always.add(tool_name)
        finally:
            recorded, binding.recording = binding.recording, None
        for tool_name, _ in recorded or ():
            lines = binding.tool_sites.setdefault(tool_name, [])
            for location in (f"{self.source_ref}:{call.lineno}", *(self._invocation.locations if self._invocation else ())):
                if location not in lines:
                    lines.append(location)
        clean = (
            not loaded
            and all(locator is not None for _, locator in recorded or ())
            and len(self.artifacts.warnings) == warnings_at
            and (list(binding.issues), dict(binding.tool_issues), set(binding.duplicated))
            == state_at
            and not call.args
            and all(keyword.arg is not None for keyword in call.keywords)
            and all(
                handoff.get("sub_agent_count") is not None
                and not handoff.get("unresolved_sub_agents")
                for handoff in handoffs
            )
        )
        signature: object = (
            (
                frozenset(recorded or ()),
                tuple(sorted(name for handoff in handoffs for name in handoff["sub_agents"])),
                frozenset(conditioned),
                frozenset(always),
                tuple(
                    sorted(
                        (name, tuple(alternatives))
                        for handoff in handoffs
                        for name, alternatives in (handoff.get("conditions") or {}).items()
                    )
                ),
            )
            if clean
            else call
        )
        site_key = (id(call), self._invocation.key if self._invocation else ())
        self.agent_sites.setdefault(agent_name, {})[site_key] = (call.lineno, signature)
        return loaded

    def _record_duplicate_constructions(self) -> None:
        """Name an agent whose constructions in this module differ (#876).

        The binding graph merges every construction of one name, so which one
        binds which tool is not established. Constructions that bind exactly
        the same definitions and handoffs, each read cleanly, are one agent
        (#876 review): ``root_agent`` and a builder returning its twin.
        """

        for agent_name, sites in self.agent_sites.items():
            if len(sites) < 2 or len({signature for _, signature in sites.values()}) == 1:
                continue
            lines = ", ".join(str(line) for line in sorted(line for line, _ in sites.values()))
            reason = (
                f"Google ADK agent {agent_name!r} is constructed more than once in "
                f"{self.source_ref} (lines {lines}); which one runs is not established, "
                "and their tools are compared as one agent, each row naming the "
                "constructions that list its tool."
            )
            self._surface_warning(reason, SURFACE_GAP_DUPLICATE_AGENT_NAME)
            self.agent_bindings[agent_name].issues.append(reason)

    def _record_agent_subclasses(self) -> None:
        """Name each class deriving from an ADK agent class this module uses (#876).

        ``extract`` reads every ``Agent(...)`` call; an instance of a subclass
        is not one, so its wiring is a named limit rather than silently absent.
        A class this module never names again — no call, ``partial`` or other
        reference beyond being a deeper subclass's base, and no decorator that
        could build it — is no agent here (#876 review); a module that imports
        it is named by the comparison's census instead, as for an SDK subclass.
        """

        subclasses = adk_agent_subclasses(self.tree, self.aliases, self.name_bindings)
        if not subclasses:
            return
        # A deeper subclass's base names its parent without building it; the
        # deeper class is itself counted by its own uses.
        bases = {
            id(base)
            for node in ast.walk(self.tree)
            if isinstance(node, ast.ClassDef) and node.name in subclasses
            for base in node.bases
        }
        referenced = {
            node.id
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and id(node) not in bases
        }
        decorated = {
            node.name
            for node in ast.walk(self.tree)
            if isinstance(node, ast.ClassDef) and node.decorator_list
        }
        for name, line in sorted(subclasses.items(), key=lambda item: item[1]):
            if name not in referenced and name not in decorated:
                continue
            self._surface_warning(
                f"Google ADK agent class {name!r} at {self.source_ref}:{line} derives "
                "from an agent class; agents built from it are not read.",
                SURFACE_GAP_AGENT_SUBCLASS,
            )

    def _local_tool_list(self, name: ast.Name, agent: ast.Call) -> tuple[list[ast.expr], list[int]] | None:
        """The members of ``tools = [a, b]`` bound once in the agent's function,
        with ``tools.append(c)`` / ``.extend([...])`` / ``.insert(i, c)`` /
        ``tools += [...]`` statements, and the lines of those made under a
        condition or in a loop (#865).

        None — the dynamic tools expression it was — for any other use of the
        list in that function: handed to a call, returned, aliased, changed
        another way, read by a nested function, or a starred member.

        The agent copies the list when it is built, so only additions before
        the statement that builds it count; one in the same compound
        statement is named (#865 review).
        """

        # ``tools += [...]`` binds the name too; read below as an addition.
        found = [
            item
            for item in self.scopes.enclosing_bindings(name, name.id)
            if not isinstance(self.scopes.parents.get(item), ast.AugAssign)
        ]
        if len(found) != 1 or not isinstance(found[0], ast.Name):
            return None
        statement = self.scopes.statement_of(found[0])
        value = getattr(statement, "value", None)
        if not (
            statement is not None
            and (
                (isinstance(statement, ast.Assign) and statement.targets == [found[0]])
                or (isinstance(statement, ast.AnnAssign) and statement.target is found[0])
            )
            and isinstance(value, ast.List | ast.Tuple)
            and _unconditional(statement, self.scopes)
        ):
            return None
        function = self.scopes.parents[statement]
        body = list(getattr(function, "body", []))
        built_at = _top_statement(agent, function, self.scopes.parents)
        if built_at not in body:
            return None
        elements: list[ast.expr] = list(value.elts)
        conditional: list[int] = []
        for node in ast.walk(function):
            if not isinstance(node, ast.Name) or node.id != name.id or node is found[0]:
                continue
            if self._enclosing_scope(node) is not function:
                return None
            parent = self.scopes.parents.get(node)
            if isinstance(parent, ast.keyword) and parent.arg == "tools":
                call = self.scopes.parents.get(parent)
                if isinstance(call, ast.Call) and self._is_agent_call(call):
                    continue
                return None
            added: list[ast.expr] | None = None
            change: ast.stmt | None = None
            if isinstance(parent, ast.AugAssign) and parent.target is node and isinstance(parent.op, ast.Add):
                if isinstance(parent.value, ast.List | ast.Tuple):
                    added, change = list(parent.value.elts), parent
            elif isinstance(parent, ast.Attribute) and parent.value is node and isinstance(node.ctx, ast.Load):
                call = self.scopes.parents.get(parent)
                holder = self.scopes.parents.get(call) if call is not None else None
                if isinstance(call, ast.Call) and call.func is parent and isinstance(holder, ast.Expr) and not call.keywords:
                    if parent.attr == "append" and len(call.args) == 1:
                        added, change = [call.args[0]], holder
                    elif parent.attr == "insert" and len(call.args) == 2:
                        added, change = [call.args[1]], holder
                    elif (
                        parent.attr == "extend"
                        and len(call.args) == 1
                        and isinstance(call.args[0], ast.List | ast.Tuple)
                    ):
                        added, change = list(call.args[0].elts), holder
            if added is None or change is None:
                return None
            top = _top_statement(change, function, self.scopes.parents)
            if top not in body or body.index(top) > body.index(built_at):
                # After the agent is built: not its list any more.
                continue
            if top is change and body.index(top) < body.index(built_at):
                elements += added
            else:
                conditional.append(change.lineno)
        if any(isinstance(item, ast.Starred) for item in elements):
            return None
        return elements, conditional

    def _enclosing_scope(self, node: ast.AST) -> ast.AST | None:
        current = self.scopes.parents.get(node)
        while current is not None and not isinstance(
            current, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef | ast.Module
            | ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
        ):
            current = self.scopes.parents.get(current)
        return current

    def _surface_warning(self, message: str, reason: str) -> None:
        """Report a construct that leaves part of this module's surface unknown."""

        self.artifacts.warnings.append(message)
        self._accounted_warnings += 1
        self._note_surface_gap(reason)

    def _note_warning(self, message: str) -> None:
        """Report a warning that says nothing about the tool surface.

        Eval-artifact references are about test collateral, not about which
        tools an agent can call, so they must not cost the module its
        completeness claim. Everything else goes through ``_surface_warning``.
        """

        self.artifacts.warnings.append(message)
        self._accounted_warnings += 1

    def _note_surface_gap(self, reason: str) -> None:
        if reason not in self.surface_gaps:
            self.surface_gaps.append(reason)

    def _record_star_imports(self) -> None:
        """A ``from x import *`` makes every name in the module unknowable.

        The binding table can only record the alias under ``"*"``, so a local
        ``def known(...)`` looked singly-bound and proven while the star import
        may replace it at run time (#400 review). Nothing here is provable, so
        the module says so once rather than per name.
        """

        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == "*" for alias in node.names
            ):
                self._note_surface_gap(SURFACE_GAP_STAR_IMPORT)
                return

    def _require_proven_framework_symbol(self, call: ast.Call) -> None:
        """Require a recognised framework constructor to really be that import.

        ``_qualified_name`` maps a call back to ``google.adk...`` through the
        import alias table, and that table is spelling-based: after
        ``from google.adk.tools import FunctionTool`` and
        ``FunctionTool = replacement``, a foreign factory was still read with
        Google's semantics — its tools catalogued, its module proven (#400
        review). The name has to be bound exactly once, by that import, for the
        resolution to mean anything.
        """

        if not self._framework_symbol_is_proven(call):
            self._note_surface_gap(SURFACE_GAP_SHADOWED_FRAMEWORK_SYMBOL)

    def _framework_symbol_is_proven(self, call: ast.Call) -> bool:
        """Whether ``call``'s root name is still the import it resolves through."""

        root = call.func
        while isinstance(root, ast.Attribute):
            root = root.value
        if not isinstance(root, ast.Name):
            return True
        bindings = self.name_bindings.get(root.id, [])
        return len(bindings) == 1 and isinstance(bindings[0], ast.alias)

    def _record_mutable_tool_bindings(self) -> None:
        """Notice any reach for an agent's ``tools`` after it is constructed.

        The ``tools=`` literal is only a proof of the surface while nothing
        else touches it. ``root_agent.tools.append(imported_tool)``,
        ``root_agent.tools = [...]``, ``setattr(root_agent, "tools", ...)``,
        and an alias bound with ``bucket = root_agent.tools`` all add tools the
        walk above never sees, and every one of them was silently promoted to
        ``high`` before this guard.

        Any ``.tools`` access at all counts, not only the mutating spellings:
        a read can be aliased and a subscript store hides behind a load. The
        cost of over-reporting is one module that stays at ``medium``, which is
        where it already was; the cost of under-reporting is a proven-surface
        claim over tools nobody enumerated. Dotted module paths such as
        ``google.adk.tools.FunctionTool`` are excluded by their imported root —
        those are packages, not agents.

        Reflective access is checked separately because it carries the
        attribute name as data: ``getattr(root_agent, "tools").append(...)``,
        ``vars(root_agent)["tools"]``, and ``root_agent.__dict__["tools"]``
        contain no ``Attribute`` node named ``tools`` at all and walked
        straight past the first check (PR #400 review).
        """

        for node in ast.walk(self.tree):
            if isinstance(node, ast.Attribute) and node.attr == "tools":
                if not self._is_imported_module_path(node.value):
                    self._note_surface_gap(SURFACE_GAP_MUTABLE_TOOL_BINDING)
                    return
            if _is_reflective_tools_access(node, self.aliases):
                self._note_surface_gap(SURFACE_GAP_MUTABLE_TOOL_BINDING)
                return

    def _is_imported_module_path(self, node: ast.AST) -> bool:
        """Whether ``node`` roots in a name that is *only* ever an import.

        Membership in ``self.aliases`` is not enough. ``from x import agents``
        followed by ``agents = LlmAgent(...)`` leaves the name in the alias map
        while it now refers to an agent, so ``agents.tools.append(...)`` was
        waved through as a package path (PR #400 review). A root that anything
        else in the module rebinds is not a package.
        """

        current = node
        while isinstance(current, ast.Attribute):
            current = current.value
        if not isinstance(current, ast.Name) or current.id not in self.aliases:
            return False
        bindings = self.name_bindings.get(current.id, [])
        return len(bindings) == 1 and isinstance(bindings[0], ast.alias)

    def _name_is_proven(self, name: str, module: PythonModule | None = None) -> bool:
        """Whether ``name`` unambiguously refers to what the flat maps say.

        True only when the module binds the name exactly once, at module scope,
        through a statement that is a direct child of the module body. A second
        binding of any kind — a parameter, a class, an import, a later
        assignment, a ``global`` declaration — means the resolution is a guess
        about which one was in effect, and a guess is not a proof. ``module``
        is where the name is written, when not here: a member of a list
        another module builds (#909).
        """

        if module is None or module is self.module:
            bindings, tree, parents = self.name_bindings.get(name, []), self.tree, self.parents
        else:
            bindings = self._names_of(module)[1].get(name, [])
            tree, parents = module.tree, self._scopes_for(module).parents
        if len(bindings) != 1:
            return False
        scope = parents.get(bindings[0])
        while scope is not None and not isinstance(scope, _SCOPE_NODES):
            scope = parents.get(scope)
        if scope is not tree:
            return False
        top: ast.AST = bindings[0]
        while parents.get(top) is not None and parents.get(top) is not tree:
            top = parents[top]
        return isinstance(top, _TOP_LEVEL_BINDING_STATEMENTS)

    def _top_level_statement(self, node: ast.AST) -> ast.AST | None:
        """The direct child of the module body that contains ``node``."""

        current: ast.AST | None = node
        parent = self.parents.get(node)
        while parent is not None and parent is not self.tree:
            current = parent
            parent = self.parents.get(parent)
        return current if parent is self.tree else None

    def _require_proven_name(self, name: str) -> None:
        if not self._name_is_proven(name):
            self._note_surface_gap(SURFACE_GAP_SHADOWED_DEFINITION)

    def _name_is_canonical(self, name: str) -> bool:
        """Whether an annotation spelling still means the type it looks like.

        A builtin (``str``, ``int``, ``list``, …) is canonical only while the
        module binds nothing of that name: ``from domain import Account as str``
        makes ADK see ``Account`` where the emitter wrote ``{"type": "string"}``
        (#400 review). A ``typing`` alias (``List``, ``Dict``) is canonical only
        when its single binding is an import that resolves into ``typing``.
        """

        return _name_is_canonical_in(name, self.name_bindings, self.aliases)

    def _resolve_extraction_evidence(
        self, warnings_before: int, loaded_sources: list[LoadedToolSource]
    ) -> None:
        """Settle each tool's extraction confidence on what this module proved.

        Runs once, after the whole module is walked, because completeness is a
        property of the file rather than of the agent that happened to be
        visited first: a dynamic tools expression on the last agent invalidates
        the proof for tools bound by the first.

        ``loaded_sources`` carries the tools an OpenAPI or MCP toolset in this
        module contributed. They are settled here too, because a module whose
        *only* tools come from a resolved toolset still has a tool set this
        file could not prove: ``Agent(**config)`` beside a resolved
        ``McpToolset`` recorded ``dynamic_agent_kwargs`` into a loop over
        function tools that was empty, and the gap evaporated (PR #400 review).
        Their own schemas remain trustworthy, so they are only ever lowered,
        never raised — this loop cannot promote a tool it did not extract.

        The unaccounted-warning backstop is the load-bearing part. A future
        ambiguity added with a plain ``artifacts.warnings.append`` would
        otherwise leave ``surface_gaps`` empty and silently promote an
        unresolved module to ``high`` — the fail-open shape a "safe" block-level
        signal clearing a path-wide guard produces. Counting warnings makes the
        default answer "not proven".
        """

        emitted = len(self.artifacts.warnings) - warnings_before
        if emitted != self._accounted_warnings:
            self._note_surface_gap(SURFACE_GAP_UNCLASSIFIED)
        for tool in [
            *self.canonical_function_tools.values(),
            *self.imported_function_tools.values(),
        ]:
            raw_gaps = tool.extraction.get("surface_gaps")
            local_gaps = raw_gaps if isinstance(raw_gaps, list) else []
            self._record_surface_evidence(tool, {*self.surface_gaps, *local_gaps})
        if not self.surface_gaps:
            return
        for loaded in loaded_sources:
            for tool in loaded.tools:
                raw_gaps = tool.extraction.get("surface_gaps")
                local_gaps = raw_gaps if isinstance(raw_gaps, list) else []
                self._record_surface_evidence(
                    tool, {*self.surface_gaps, *local_gaps}, lower_only=True
                )

    def _record_surface_evidence(
        self, tool: Tool, gaps: set[str], *, lower_only: bool = False
    ) -> None:
        """Write one tool's completeness evidence.

        ``tool_set_proven`` is the half that has to survive identity merging. A
        reviewed inventory or identity binding is a human statement about a
        *tool's own schema*, so it legitimately closes the interface reasons —
        that is what #386 is for. Nothing a human can say about one tool
        establishes that a module exposes no *other* tools, so a set-scoped
        reason has to travel with the observation and keep the canonical tool
        below high wherever it is merged (#400 review).
        """

        ordered = sorted(gaps)
        tool.extraction["surface"] = SURFACE_PARTIAL if ordered else SURFACE_ENUMERATED
        tool.extraction["surface_gaps"] = ordered
        tool.extraction["tool_set_proven"] = not (
            gaps - TOOL_INTERFACE_SURFACE_GAPS
        )
        if not ordered:
            if lower_only:
                return
            tool.extraction["confidence"] = "high"
            tool.extraction_confidence = "high"
            return
        if lower_only and tool.extraction_confidence != "high":
            return
        tool.extraction["confidence"] = "medium"
        tool.extraction_confidence = "medium"

    def _agent_names_by_variable(self) -> dict[tuple[ast.AST | None, str], str | None]:
        """Map each ``variable = Agent(...)`` to the agent's declared ``name=``.

        ADK routes a handoff to the sub-agent's ``name=``, but
        ``sub_agents=[salesforce_agent]`` spells the Python variable the agent
        was assigned to. Agent nodes are keyed by the name, so the two
        spellings have to be reconciled or the handoff lands on a phantom node
        owning no tools and the sub-agent's whole surface drops out of the
        root-reachable graph (#385).

        The key carries the enclosing scope because ``_agent_calls`` walks
        nested functions too. Two factories that each build a local ``worker``
        are one flat key apart, and collapsing them made a root reach the
        *other* factory's agent — analyzing tools it cannot call and excluding
        the ones it can, while still reporting ``pass_eligible``. Rebinding one
        name to differently named agents inside a single scope is genuine
        flow-sensitivity that AST position cannot settle, so it maps to
        ``None`` and resolves to nothing rather than to a guess.
        """

        names: dict[tuple[ast.AST | None, str], str | None] = {}
        for target_name, call in self.agent_call_list:
            if not target_name:
                continue
            key = (self._scope_of(call), target_name)
            agent_name = _kwarg_string(call, "name") or target_name
            if key in names and names[key] != agent_name:
                names[key] = None
                continue
            names[key] = agent_name
        return names

    def _scope_of(self, node: ast.AST) -> ast.AST | None:
        """The nearest enclosing scope of ``node``, or None above the module."""

        current = self.parents.get(node)
        while current is not None and not isinstance(current, _SCOPE_NODES):
            current = self.parents.get(current)
        return current

    def _sub_agent_name(self, variable: str, call: ast.AST) -> str | None:
        """Resolve one ``sub_agents`` element to an agent defined in this module.

        Walks scopes innermost-out from the referencing call, so a factory's
        local agent wins over a module-level name and a sibling factory's
        identical local name is never consulted. Returns None for anything
        this module does not define as an agent — an imported name, an
        ambiguous rebinding — which the caller reports as incomplete rather
        than binding to a name it cannot stand behind.
        """

        scope: ast.AST | None = self._scope_of(call)
        while scope is not None:
            if (scope, variable) in self.agent_names_by_variable:
                return self.agent_names_by_variable[(scope, variable)]
            scope = self._scope_of(scope)
        return None

    def _binding_for(self, agent_name: str, call: ast.Call) -> _AdkAgentBinding:
        binding = self.agent_bindings.get(agent_name)
        if binding is None:
            binding = _AdkAgentBinding(
                agent=agent_name,
                source_pointer=f"{self.source_ref}:{call.lineno}",
            )
            self.agent_bindings[agent_name] = binding
        return binding

    def _binding_observations(self) -> list[AgentBindingObservation]:
        """Publish agent wiring as framework-owned binding observations.

        Bindings deliberately do not travel on ``Tool.annotations``: the
        catalog holds one observation per tool definition, so a per-tool
        agent name could only ever name one of N binding agents. Handoffs
        stay with the ``sub_agents`` artifact records that already own them.
        """

        return [
            AgentBindingObservation(
                agent=binding.agent,
                source_id=self.source_id,
                source=self.source_ref,
                source_pointer=binding.source_pointer,
                tool_names=list(binding.tool_names),
                tool_locators=dict(binding.tool_locators),
                tool_issues={**binding.tool_issues,
                             **({name: "; ".join(filter(None, [binding.tool_issues.get(name), binding.constructor_issue]))
                                 for name in binding.tool_names}
                                if binding.constructor_issue is not None else {})},
                tool_sites={
                    name: sorted(lines, key=lambda location: (location.rsplit(":", 1)[0], int(location.rsplit(":", 1)[1])))
                    for name, lines in binding.tool_sites.items()
                }
                if len(self.agent_sites.get(binding.agent, {})) > 1 or any(len(locations) > 1 for locations in binding.tool_sites.values())
                else {},
                tool_conditions=binding.when.only_when(binding.duplicated),
                tools_complete=not binding.issues,
                handoffs_complete=binding.handoffs_complete,
                handoff_sites=dict(binding.handoff_sites),
                issues=list(binding.issues),
                constructor_issues=dict(binding.constructor_issues),
            )
            for binding in self.agent_bindings.values()
            if binding.tool_names or binding.issues or binding.handoff_sites
        ]

    def _agent_calls(self) -> list[tuple[str | None, ast.Call]]:
        calls: list[tuple[str | None, ast.Call]] = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                if self._is_agent_call(node.value):
                    calls.append((_simple_target_name(node.targets), node.value))
            elif isinstance(node, ast.Call) and self._is_agent_call(node):
                if not any(existing is node for _, existing in calls):
                    calls.append((None, node))
        return calls

    def _module_agent_reads(self, module: PythonModule, call: ast.Call, keyword: str | None) -> bool:
        if keyword not in {"tools", "sub_agents"} | CALLBACK_KEYS or self._builder_calls is None:
            return False
        spelling = reference_spelling(call.func)
        if spelling is None:
            return False
        scopes = self._builder_calls.scopes(module)
        local = scopes.enclosing_bindings(
            evaluation_site(scopes, call.func), spelling.split(".", 1)[0]
        )
        if local:
            nodes = local
        else:
            nodes = [binding.node for binding in module.bindings.get(spelling.split(".", 1)[0], [])]
        if len(nodes) != 1 or not isinstance(nodes[0], ast.alias):
            return False
        alias = nodes[0]
        statement = scopes.statement_of(alias)
        if isinstance(statement, ast.ImportFrom) and not statement.level and statement.module:
            imported = f"{statement.module}.{alias.name}"
        elif isinstance(statement, ast.Import):
            imported = alias.name if alias.asname else alias.name.split(".", 1)[0]
        else:
            return False
        suffix = spelling.partition(".")[2]
        qualified = f"{imported}.{suffix}" if suffix else imported
        return (qualified in AGENT_CLASS_NAMES and self.resolver is not None
                and self._builder_calls.constructor_issue(module, call) is None)

    def _agent_constructor_issue(self, call: ast.Call) -> str | None:
        if self.module is not None and self._builder_calls is not None:
            return self._builder_calls.constructor_issue(self.module, call)
        return None if self._framework_symbol_is_proven(call) else "the constructor root is not bound by one import"

    def _is_agent_call(self, call: ast.Call) -> bool:
        return _qualified_name(call.func, self.aliases) in AGENT_CLASS_NAMES

    def _wrapper_assignments(self) -> dict[str, dict[str, Any]]:
        wrappers: dict[str, dict[str, Any]] = {}
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            target_name = _simple_target_name(node.targets)
            if not target_name:
                continue
            call_name = _qualified_name(node.value.func, self.aliases)
            if call_name not in FUNCTION_TOOL_NAMES | LONG_RUNNING_TOOL_NAMES:
                continue
            func_name = _call_func_name(node.value)
            wrappers[target_name] = {
                "func_name": func_name,
                "func_expr": _call_func_expr(node.value),
                "long_running": call_name in LONG_RUNNING_TOOL_NAMES,
                "call": node.value,
            }
        return wrappers

    def _toolset_assignments(self) -> dict[str, ast.Call]:
        toolsets: dict[str, ast.Call] = {}
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            target_name = _simple_target_name(node.targets)
            if not target_name:
                continue
            call_name = _qualified_name(node.value.func, self.aliases)
            if call_name in OPENAPI_TOOLSET_NAMES | MCP_TOOLSET_NAMES:
                toolsets[target_name] = node.value
        return toolsets

    def _connection_assignments(self) -> dict[str, ast.Call]:
        """Module-level ``params = <ConnectionParams>(...)`` assignments."""

        assignments: dict[str, ast.Call] = {}
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            target_name = _simple_target_name(node.targets)
            if not target_name:
                continue
            call_name = _qualified_name(node.value.func, self.aliases) or ""
            if call_name.rsplit(".", 1)[-1] in MCP_CONNECTION_TRANSPORTS:
                assignments[target_name] = node.value
        return assignments

    def _slot_for(self, call: ast.Call, agent_name: str) -> str:
        """Stable discriminator for one toolset inside one agent.

        The module-level variable when there is one — which is also what keeps
        a toolset shared by two agents a single binding — otherwise the
        1-based order of inline constructions in that agent's tool list.
        Neither depends on the source line, so moving or reformatting the call
        cannot register as a change.
        """

        variable = self.toolset_variable_names.get(id(call))
        if variable:
            return variable
        count = self.inline_slot_counts.get(agent_name, 0) + 1
        self.inline_slot_counts[agent_name] = count
        return f"#{count}"

    def _extract_member(
        self,
        member: ListMember,
        tools: list[Tool],
        agent_name: str,
        binding: _AdkAgentBinding,
    ) -> list[LoadedToolSource]:
        """One member of an agent's tools list, read where it is written (#909)."""

        module = member.module
        if module is None:
            return self._extract_tool_expr(member.expr, tools, agent_name, binding)
        spelling = reference_spelling(member.expr)
        if spelling is None or self.resolver is None:
            message = (
                f"Google ADK agent {agent_name!r} binds `{source_text(member.expr)}`, written in "
                f"{module.ref}:{member.expr.lineno}, which this reader does not resolve."
            )
            self._surface_warning(message, SURFACE_GAP_UNRESOLVED_EXPRESSION)
            if message not in binding.issues:
                binding.issues.append(message)
            return []
        local = self._builder_calls.scopes(module).enclosing_bindings(member.expr, spelling.split(".", 1)[0]) if self._builder_calls else []
        if local and self._builder_calls is not None:
            resolution, long_running = self._builder_calls.resolve(module, member.expr), False
            if resolution.definition is not None and resolution.module is not None and resolution.definition not in resolution.module.tree.body:
                resolution = Resolution(reference=spelling, reason=LOCAL_BINDING,
                                        detail=f"the caller-local tool at {module.ref}:{resolution.definition.lineno} has an enclosing closure, which this increment does not follow")
        else:
            resolution, long_running = self._resolve_reference(spelling, module)
        if resolution is not None and resolution.resolved:
            self._bind_resolved(resolution, tools, agent_name, binding, long_running, spelled_in=module)
        else:
            self._unresolved_reference(agent_name, spelling, resolution)
            issue = adk_unresolved_tool_warning(agent_name, spelling)
            if resolution is not None and resolution.detail:
                issue += f" {resolution.detail}."
            if issue not in binding.issues:
                binding.issues.append(issue)
        return []

    def _extract_tool_expr(
        self,
        expr: ast.AST,
        tools: list[Tool],
        agent_name: str,
        binding: _AdkAgentBinding,
    ) -> list[LoadedToolSource]:
        spelling = reference_spelling(expr)
        verdict = self._local_meaning(expr, spelling) if spelling is not None else None
        if isinstance(verdict, tuple):
            resolution, long_running = verdict
            if resolution.resolved:
                self._bind_resolved(resolution, tools, agent_name, binding, long_running)
            else:
                assert spelling is not None
                self._unresolved_reference(agent_name, spelling, resolution)
            return []
        if isinstance(expr, ast.Name) and verdict is None and self.module is not None:
            # No binding in an enclosing function: the module-level binding is
            # the one visible here. The flat maps answer only when their entry
            # *is* that binding; a same-named ``def`` or wrapper nested in some
            # function says nothing about it (#879 review).
            visible = self._module_level_flat(expr.id)
            if visible == "value":
                statement = self.module.bindings[expr.id][0].statement
                value = getattr(statement, "value", None)
                assert isinstance(value, ast.Call)
                return self._extract_tool_expr(value, tools, agent_name, binding)
            if visible is None:
                resolution, long_running = self._resolve_reference(expr.id)
                if resolution is not None and resolution.resolved:
                    self._bind_resolved(resolution, tools, agent_name, binding, long_running)
                    return []
                if not (
                    expr.id in self.wrappers
                    or expr.id in self.toolset_assignments
                    or expr.id in self.functions
                ):
                    self._unresolved_reference(agent_name, expr.id, resolution)
                    return []
                issue = self._name_unproven_guess(expr.id, resolution, agent_name, binding)
                before = set(binding.tool_names)
                loaded = self._extract_flat_name(expr, tools, agent_name, binding)
                self._record_guess(expr.id, issue, before, binding)
                return loaded
        if isinstance(expr, ast.Name):
            return self._extract_flat_name(expr, tools, agent_name, binding)
        if isinstance(expr, ast.Attribute) and self._imported_root(expr):
            return self._extract_imported_attribute(expr, tools, agent_name, binding)
        return self._extract_call_expr(expr, tools, agent_name, binding)

    def _extract_flat_name(
        self,
        expr: ast.Name,
        tools: list[Tool],
        agent_name: str,
        binding: _AdkAgentBinding,
    ) -> list[LoadedToolSource]:
        """A plain name the flat maps describe, or else the import resolver."""

        if expr.id in self.wrappers:
            # The variable's own name has to hold up too, not just the
            # function it wraps: a wrapper reassigned later resolves
            # through a last-write-wins map.
            self._require_proven_name(expr.id)
            self._append_wrapper_tool(expr.id, tools, agent_name, binding)
        elif expr.id in self.toolset_assignments:
            self._require_proven_name(expr.id)
            return self._extract_toolset_call(
                self.toolset_assignments[expr.id], agent_name, binding
            )
        elif expr.id in self.functions:
            self._bind_function_tool(
                self.functions[expr.id], tools, agent_name, binding, False
            )
        else:
            resolution, long_running = self._resolve_reference(expr.id)
            if resolution is not None and resolution.resolved:
                self._bind_resolved(resolution, tools, agent_name, binding, long_running)
            else:
                self._unresolved_reference(agent_name, expr.id, resolution)
        return []

    def _extract_imported_attribute(
        self,
        expr: ast.Attribute,
        tools: list[Tool],
        agent_name: str,
        binding: _AdkAgentBinding,
    ) -> list[LoadedToolSource]:
        # ``memory_bank.remember_firm_finding`` after ``from . import
        # memory_bank``: a module-qualified function, not an arbitrary
        # expression (#864). Resolved, or named as the reference it is.
        spelling = reference_spelling(expr)
        assert spelling is not None
        resolution, long_running = self._resolve_reference(spelling)
        if resolution is not None and resolution.resolved:
            self._bind_resolved(resolution, tools, agent_name, binding, long_running)
        else:
            self._unresolved_reference(agent_name, spelling, resolution)
        return []

    def _extract_call_expr(
        self,
        expr: ast.AST,
        tools: list[Tool],
        agent_name: str,
        binding: _AdkAgentBinding,
    ) -> list[LoadedToolSource]:
        if isinstance(expr, ast.Call):
            call_name = _qualified_name(expr.func, self.aliases)
            if call_name in FUNCTION_TOOL_NAMES | LONG_RUNNING_TOOL_NAMES:
                self._require_proven_framework_symbol(expr)
                func_name = _call_func_name(expr)
                long_running = call_name in LONG_RUNNING_TOOL_NAMES
                if not self._bind_named_function(
                    _call_func_expr(expr), tools, agent_name, binding, long_running
                ):
                    # A recognised wrapper whose ``func`` this module does not
                    # define: an imported function, an attribute, a lambda, or
                    # no ``func`` at all. The wrapper is a real tool the agent
                    # can call and nothing else records it, so returning
                    # silently here reported a strictly smaller tool surface
                    # than the agent has — and called it proven (PR #400
                    # review). An imported function is followed first (#864).
                    self._bind_wrapped_reference(
                        _call_func_expr(expr),
                        tools,
                        agent_name,
                        binding,
                        long_running,
                        f"Google ADK agent {agent_name!r} wraps a tool whose function "
                        f"{func_name or reference_spelling(_call_func_expr(expr)) or '<unspecified>'!r} "
                        "is not defined in this module.",
                    )
                return []
            if call_name in OPENAPI_TOOLSET_NAMES | MCP_TOOLSET_NAMES:
                return self._extract_toolset_call(expr, agent_name, binding)
            spelling = reference_spelling(expr.func)
            made = (
                self._factory_call(expr, self.module, f"{spelling}()")
                if spelling is not None and self.resolver is not None and self.module is not None
                else None
            )
            if made is not None:
                # ``tools=[create_tool()]``: what the factory returns (#865).
                resolution, long_running = made
                if resolution.resolved:
                    self._bind_resolved(resolution, tools, agent_name, binding, long_running)
                    return []
                warning = (
                    f"Google ADK agent {agent_name!r} has a tool expression that could not be statically resolved."
                )
                self._surface_warning(warning, SURFACE_GAP_UNRESOLVED_EXPRESSION)
                self._record_unresolved_reference(warning, agent_name, f"{spelling}()", resolution)
                return []
        self._surface_warning(
            f"Google ADK agent {agent_name!r} has a tool expression that could not be statically resolved.",
            SURFACE_GAP_UNRESOLVED_EXPRESSION,
        )
        return []

    def _local_meaning(
        self, node: ast.AST, spelling: str
    ) -> tuple[Resolution, bool] | str | None:  # None | "flat" | resolution
        """What ``spelling`` means where it is used, when an enclosing function binds it.

        None: no enclosing binding, so the module-level reading applies.
        ``"flat"``: an enclosing binding the flat maps already describe — a
        nested ``def`` that is the only one of its name, a factory's own
        ``toolset = McpToolset(...)`` / ``tool = FunctionTool(...)`` — so the
        usual path applies. A tuple: a resolution to bind or name (#879
        review).
        """

        name = spelling.split(".", 1)[0]
        found = self.scopes.enclosing_bindings(node, name)
        if not found:
            return None
        if len(found) > 1:
            return (
                Resolution(
                    reference=spelling,
                    reason=LOCAL_BINDING,
                    detail=local_binding_detail(self.source_ref, name, found[0], rebound=True),
                ),
                False,
            )
        local = found[0]
        if isinstance(local, ast.alias) and self.resolver is not None and self.module is not None:
            statement = self.scopes.statement_of(local)
            if isinstance(statement, ast.Import | ast.ImportFrom):
                return self._through_wrapper(
                    self.resolver.resolve_local_import(self.module, statement, local, spelling)
                )
        if isinstance(local, ast.FunctionDef | ast.AsyncFunctionDef) and spelling == name:
            if self.functions.get(name) is local:
                return "flat"
        if isinstance(local, ast.Name) and spelling == name:
            statement = self.scopes.statement_of(local)
            value = getattr(statement, "value", None)
            recorded = self.wrappers.get(name, {}).get("call") or self.toolset_assignments.get(name)
            if value is not None and value is recorded:
                return "flat"
            made = self._local_factory(local, statement, spelling)
            if made is not None:
                return made
        return (
            Resolution(
                reference=spelling,
                reason=LOCAL_BINDING,
                detail=local_binding_detail(self.source_ref, name, local),
            ),
            False,
        )

    def _wrapped_function(self, node: ast.Name) -> bool:
        """``FunctionTool(func=fn, require_confirmation=True)`` with ``fn`` a
        factory's function: the wrapper reads it."""

        parent = self.scopes.parents.get(node)
        call = self.scopes.parents.get(parent) if isinstance(parent, ast.keyword) else parent
        return (
            isinstance(call, ast.Call)
            and _qualified_name(call.func, self.aliases) in FUNCTION_TOOL_NAMES | LONG_RUNNING_TOOL_NAMES
            and _call_func_expr(call) is node
        )

    def _local_factory(
        self, local: ast.Name, statement: ast.stmt | None, spelling: str
    ) -> tuple[Resolution, bool] | None:
        """``tool = create_tool()`` in the agent's own function, bound once and
        unconditionally: the tool the factory returns (#865). None when the
        call is not to application code, or the binding is conditional."""

        value = getattr(statement, "value", None)
        if (
            self.resolver is None
            or self.module is None
            or statement is None
            or not isinstance(value, ast.Call)
            or not (
                (isinstance(statement, ast.Assign) and statement.targets == [local])
                or (isinstance(statement, ast.AnnAssign) and statement.target is local)
            )
            or not _unconditional(statement, self.scopes)
        ):
            return None
        made = self._factory_call(value, self.module, spelling)
        if made is None:
            return None
        plain = not any(step.get("returns_tool") for step in made[0].steps)
        function = self.scopes.parents[statement]
        changed = _changed_at(
            function,
            local.id,
            self.scopes.parents,
            bound=local,
            allowed=lambda node: _in_tools_argument(node, self.scopes.parents, self._is_agent_call)
            or _in_local_list(node, self.scopes.parents, self._is_agent_call)
            # Wrapping a function it returns; wrapping a tool again is not a tool.
            or (plain and self._wrapped_function(node)),
        )
        if changed is not None:
            return (
                Resolution(
                    reference=spelling,
                    reason=FACTORY_RETURN,
                    detail=(
                        f"{spelling!r}, the tool {reference_spelling(value.func)!r} returns at "
                        f"{self.source_ref}:{statement.lineno}, is changed or handed on at "
                        f"{self.source_ref}:{changed}"
                    ),
                    steps=made[0].steps,
                ),
                False,
            )
        return made

    def _module_level_flat(self, name: str) -> str | None:
        """Whether the flat maps describe ``name``'s module-level binding.

        ``"flat"``: its single top-level binding is the flat entry. ``"value"``:
        it is a top-level wrapper or toolset assignment the flat map lost to a
        same-named one in a function — read that assignment's call instead.
        None: anything else, which the import resolver answers or names.
        """

        assert self.module is not None
        bindings = self.module.bindings.get(name, [])
        if self._self_wrapper(name) is not None:
            # ``x = FunctionTool(func=x)`` right after ``def x``: the wrapper,
            # which wraps the definition bound just before it.
            return "flat"
        if len(bindings) != 1 or not bindings[0].top_level:
            return None
        node, statement = bindings[0].node, bindings[0].statement
        if node is self.functions.get(name):
            return "flat"
        value = getattr(statement, "value", None)
        recorded = self.wrappers.get(name, {}).get("call") or self.toolset_assignments.get(name)
        if isinstance(node, ast.Name) and value is not None and value is recorded:
            return "flat"
        if (
            isinstance(node, ast.Name)
            and isinstance(value, ast.Call)
            and (name in self.wrappers or name in self.toolset_assignments)
        ):
            return "value"
        return None

    def _bind_named_function(
        self,
        func_expr: ast.AST | None,
        tools: list[Tool],
        agent_name: str,
        binding: _AdkAgentBinding,
        long_running: bool,
    ) -> bool:
        """Bind the function a wrapper's plain ``func`` name means where it is written.

        False when the name is not one this module's flat map can speak to —
        an attribute, a local binding, an imported name — which the caller
        follows through ``_bind_wrapped_reference``.
        """

        if not isinstance(func_expr, ast.Name) or func_expr.id not in self.functions:
            return False
        name = func_expr.id
        if self._visible_function(name, func_expr):
            self._bind_function_tool(self.functions[name], tools, agent_name, binding, long_running)
            return True
        if self.module is None or self._local_meaning(func_expr, name) is not None:
            return False
        resolution, wrapped = self._resolve_reference(name)
        if resolution is not None and resolution.resolved:
            self._bind_resolved(resolution, tools, agent_name, binding, long_running or wrapped)
            return True
        issue = self._name_unproven_guess(name, resolution, agent_name, binding)
        before = set(binding.tool_names)
        self._bind_function_tool(self.functions[name], tools, agent_name, binding, long_running)
        self._record_guess(name, issue, before, binding)
        return True

    def _name_unproven_guess(
        self,
        name: str,
        resolution: Resolution | None,
        agent_name: str,
        binding: _AdkAgentBinding,
    ) -> str:
        """The flat map's same-named definition stands in for what the module binds.

        What the module binds is not established — rebound, only conditional,
        a value — so the definition found elsewhere in the file is named, never
        proven: the shadowed gap keeps ``scan`` at medium, and the returned
        reason, recorded per tool, keeps a comparison from treating it as the
        binding or the agent's list as complete (#879 review).
        """

        why = (
            resolution.detail
            if resolution is not None and resolution.detail
            else f"{name!r} is not bound once at module level"
        )
        # Not an agent issue: ``scan`` would then drop every per-tool finding
        # of the agent for one name (#879 review). The shadowed gap keeps the
        # module at medium; the comparison reads ``tool_issues``.
        self._note_surface_gap(SURFACE_GAP_SHADOWED_DEFINITION)
        return (
            f"Google ADK agent {agent_name!r} lists {name!r}, and {why}; the "
            "same-named definition read for it is not established as the one bound."
        )

    def _record_guess(
        self, name: str, issue: str, before: set[str], binding: _AdkAgentBinding
    ) -> None:
        """Scope a guessed binding's reason to every tool it may really be.

        The tool the guess bound, and every tool name the module's bindings of
        ``name`` could give the agent instead (a ``def``, an imported function,
        ``name = other``, ``name = FunctionTool(func=f)``). Only when one of
        those cannot be named does the reason cover the whole agent (#879
        review): a guess that bound nothing new still leaves a gap.
        """

        names = {bound for bound in binding.tool_names if bound not in before}
        candidates = self._guess_candidates(name)
        names |= candidates if candidates is not None else {ANY_TOOL}
        binding.tool_issues.update({item: issue for item in names})

    def _guess_candidates(self, name: str) -> set[str] | None:
        """The tool names the module's bindings of ``name`` can give the agent.

        Each binding is followed to its definition — an import through the
        resolver, ``x = other`` and ``x = FunctionTool(func=f)`` through the
        name they spell — since an alias's own spelling need not be the tool's
        name. None, meaning any of the agent's tools, when one of them cannot be
        followed or a wildcard import could bind the name too (#879 review).
        """

        if self.module is None or self.resolver is None or self.module.star_import:
            return None
        candidates: set[str] = set()
        for item in self.module.bindings.get(name, []):
            node, statement = item.node, item.statement
            value = getattr(statement, "value", None)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                candidates.add(node.name)
                continue
            if isinstance(node, ast.alias) and isinstance(statement, ast.Import | ast.ImportFrom):
                resolution, _ = self._through_wrapper(
                    self.resolver.resolve_local_import(self.module, statement, node, name)
                )
                if resolution.reason in (MODULE_NOT_FOUND, OUTSIDE_SCOPE):
                    # A module the scope does not hold — ``try: from fast_search
                    # import search`` — gives a function no in-scope tool is;
                    # its name is the one imported.
                    candidates.add(node.name.rsplit(".", 1)[-1])
                    continue
            elif isinstance(node, ast.Name) and isinstance(value, ast.Name):
                resolution, _ = self._resolve_reference(value.id)
            elif (
                isinstance(node, ast.Name)
                and isinstance(value, ast.Call)
                and _qualified_name(value.func, self.aliases)
                in FUNCTION_TOOL_NAMES | LONG_RUNNING_TOOL_NAMES
                and _call_func_name(value) is not None
            ):
                resolution, _ = self._resolve_reference(str(_call_func_name(value)))
            else:
                return None
            if resolution is None or not resolution.resolved or resolution.definition is None:
                return None
            candidates.add(resolution.definition.name)
        return candidates or None

    def _visible_function(self, name: str, node: ast.AST | None) -> bool:
        """Whether ``self.functions[name]`` is the binding of ``name`` visible at ``node``."""

        function = self.functions.get(name)
        if function is None:
            return False
        if self.module is None or node is None:
            return True
        meaning = self._local_meaning(node, name) if isinstance(node, ast.Name) else None
        if meaning == "flat":
            return True
        wrapper = self._self_wrapper(name) if meaning is None else None
        if wrapper is not None:
            # Inside ``x = FunctionTool(func=x)`` the right-hand ``x`` runs
            # before the rebinding: it is the ``def`` bound just before.
            current: ast.AST | None = node
            while current is not None and current is not wrapper:
                current = self.parents.get(current)
            return current is wrapper
        return meaning is None and self._module_definition(name) is function

    def _self_wrapper(self, name: str) -> ast.stmt | None:
        """The ``name = FunctionTool(func=name)`` statement right after ``def name``."""

        if self.module is None:
            return None
        bindings = self.module.bindings.get(name, [])
        if (
            len(bindings) != 2
            or not all(item.top_level for item in bindings)
            or bindings[0].node is not self.functions.get(name)
            or not isinstance(bindings[1].node, ast.Name)
        ):
            return None
        statement = bindings[1].statement
        value = getattr(statement, "value", None)
        if (
            not isinstance(value, ast.Call)
            or value is not self.wrappers.get(name, {}).get("call")
            or _call_func_name(value) != name
        ):
            return None
        return statement

    def _module_definition(
        self, name: str
    ) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        """The single top-level ``def name`` of this module, if it has one."""

        if self.module is None:
            return None
        bindings = self.module.bindings.get(name, [])
        if len(bindings) == 1 and bindings[0].top_level and isinstance(
            bindings[0].node, ast.FunctionDef | ast.AsyncFunctionDef
        ):
            return bindings[0].node
        return None

    def _append_wrapper_tool(
        self,
        wrapper_name: str,
        tools: list[Tool],
        agent_name: str,
        binding: _AdkAgentBinding,
    ) -> None:
        wrapper = self.wrappers[wrapper_name]
        wrapper_call = wrapper.get("call")
        if isinstance(wrapper_call, ast.Call):
            self._require_proven_framework_symbol(wrapper_call)
        func_expr = wrapper.get("func_expr")
        if self._bind_named_function(
            func_expr if isinstance(func_expr, ast.AST) else None,
            tools,
            agent_name,
            binding,
            bool(wrapper.get("long_running")),
        ):
            return
        self._bind_wrapped_reference(
            func_expr if isinstance(func_expr, ast.AST) else None,
            tools,
            agent_name,
            binding,
            bool(wrapper.get("long_running")),
            f"Google ADK tool wrapper {wrapper_name!r} has no statically resolvable function.",
        )

    def _resolve_reference(
        self, spelling: str, module: PythonModule | None = None
    ) -> tuple[Resolution | None, bool]:
        """Follow ``spelling`` through this module's imports (#864).

        The flag is True when the chain went through a
        ``LongRunningFunctionTool(...)`` built in another module. ``module``
        is where the spelling is written, when not here: a member of a list
        another module builds (#909).
        """

        if self.resolver is None or self.module is None:
            return None, False
        resolution, long_running = self._through_wrapper(
            self.resolver.resolve(module or self.module, spelling)
        )
        value, home = resolution.value, resolution.module
        if resolution.resolved or not isinstance(value, ast.Call) or home is None:
            return resolution, long_running
        aliases, _ = self._names_of(home)
        if _qualified_name(value.func, aliases) in (
            FUNCTION_TOOL_NAMES | LONG_RUNNING_TOOL_NAMES | OPENAPI_TOOLSET_NAMES | MCP_TOOLSET_NAMES
        ):
            return resolution, long_running
        # ``tool = create_tool()`` at a module's top level: what the factory
        # returns (#865), unless the module changes the tool afterwards.
        made = self._factory_call(value, home, spelling)
        if made is None:
            return resolution, long_running
        inner, inner_long_running = made
        scopes = self._scopes_for(home)
        statement = scopes.statement_of(value)
        holders = statement.targets if isinstance(statement, ast.Assign) else [getattr(statement, "target", None)]
        holder = holders[0] if len(holders) == 1 and isinstance(holders[0], ast.Name) else None
        changed: int | None = None
        if holder is not None:
            cache_key = (home.ref, holder.id)
            if cache_key not in self.module_changes:
                self.module_changes[cache_key] = _changed_at(
                    home.tree, holder.id, scopes.parents, bound=holder, strict=False
                )
            changed = self.module_changes[cache_key]
        if holder is None or changed is not None:
            return (
                dataclasses.replace(
                    resolution,
                    reason=FACTORY_RETURN,
                    detail=(
                        f"{spelling!r}, the tool {reference_spelling(value.func)!r} returns at "
                        f"{home.ref}:{value.lineno}, is changed at {home.ref}:{changed}"
                    ),
                ),
                False,
            )
        return (
            dataclasses.replace(
                inner,
                steps=(*resolution.steps, *inner.steps),
                caveats=tuple(dict.fromkeys((*resolution.caveats, *inner.caveats))),
            ),
            inner_long_running,
        )

    def _through_wrapper(self, resolution: Resolution) -> tuple[Resolution, bool]:
        """Continue into ``name = FunctionTool(func)`` in the module that built it.

        The chain stops at an assigned value; a recognised function-tool
        wrapper there still wraps one definition, read in its own module's
        spelling. Anything else stays the named stop it already is.
        """

        value, module = resolution.value, resolution.module
        if (
            self.resolver is None
            or module is None
            or module is self.module
            or not isinstance(value, ast.Call)
        ):
            return resolution, False
        aliases, bindings = self._names_of(module)
        call_name = _qualified_name(value.func, aliases)
        func_expr = _call_func_expr(value)
        spelling = reference_spelling(func_expr) if func_expr is not None else None
        if call_name not in FUNCTION_TOOL_NAMES | LONG_RUNNING_TOOL_NAMES or spelling is None:
            return resolution, False
        root = value.func
        while isinstance(root, ast.Attribute):
            root = root.value
        if isinstance(root, ast.Name):
            # As in this module: the constructor is ADK's only while its name
            # is still the import it resolves through.
            found = bindings.get(root.id, [])
            if len(found) != 1 or not isinstance(found[0], ast.alias):
                self._note_surface_gap(SURFACE_GAP_SHADOWED_FRAMEWORK_SYMBOL)
        inner = self.resolver.resolve(module, spelling)
        return (
            dataclasses.replace(
                inner,
                reference=resolution.reference,
                steps=(*resolution.steps, *inner.steps),
                # What runs before this module's wrapper is used still runs
                # before its function is (#879 review).
                caveats=tuple(dict.fromkeys((*resolution.caveats, *inner.caveats))),
            ),
            call_name in LONG_RUNNING_TOOL_NAMES,
        )

    def _scopes_for(self, module: PythonModule) -> ScopeIndex:
        if module is self.module:
            return self.scopes
        scopes = self.module_scopes.get(module.ref)
        if scopes is None:
            scopes = self.module_scopes[module.ref] = ScopeIndex(module.tree)
        return scopes

    def _factory_call(
        self,
        call: ast.Call,
        module: PythonModule,
        reference: str,
        *,
        depth: int = 0,
        seen: frozenset[tuple[str, int]] = frozenset(),
        outer: ast.FunctionDef | ast.AsyncFunctionDef | None = None,
    ) -> tuple[Resolution, bool] | None:
        """The tool ``factory(...)`` returns, read from the factory's body (#865).

        Followed when the call names a function this read can resolve: its one
        unconditional ``return`` is ``FunctionTool(inner)`` (or
        ``LongRunningFunctionTool``), a plain function, a name bound once to
        one of those, or another factory's call, up to
        :data:`MAX_FACTORY_DEPTH` deep. Nothing is executed. None: the call is
        not to application code (a builtin, a third-party package), so the
        caller keeps the answer it had.
        """

        assert self.resolver is not None
        spelling = reference_spelling(call.func)
        if spelling is None:
            return None
        scopes = self._scopes_for(module)
        name = spelling.split(".", 1)[0]
        found = scopes.enclosing_bindings(call.func, name)
        callee: Resolution
        import_node: tuple[ast.Import | ast.ImportFrom, ast.alias] | None = None
        if len(found) > 1:
            callee = Resolution(
                reference=spelling,
                reason=LOCAL_BINDING,
                detail=local_binding_detail(module.ref, name, found[0], rebound=True),
            )
        elif found:
            local = found[0]
            statement = scopes.statement_of(local)
            if isinstance(local, ast.alias) and isinstance(statement, ast.Import | ast.ImportFrom):
                import_node = (statement, local)
                callee = self.resolver.resolve_local_import(module, statement, local, spelling)
            elif (
                isinstance(local, ast.FunctionDef | ast.AsyncFunctionDef)
                and spelling == name
                and _unconditional(local, scopes)
            ):
                callee = Resolution(
                    reference=spelling,
                    module=module,
                    definition=local,
                    steps=(_step(module, local, "definition"),),
                )
            else:
                callee = Resolution(
                    reference=spelling,
                    reason=LOCAL_BINDING,
                    detail=local_binding_detail(module.ref, name, local),
                )
        else:
            callee = self.resolver.resolve(module, spelling)
            bound = module.bindings.get(name, [])
            if len(bound) == 1 and isinstance(bound[0].node, ast.alias):
                statement = bound[0].statement
                if isinstance(statement, ast.Import | ast.ImportFrom):
                    import_node = (statement, bound[0].node)
        where = f"{module.ref}:{call.lineno}"
        if not callee.resolved:
            # Only a function this read reaches is a factory it follows. A
            # third-party package, a class, a name bound twice or through a
            # wildcard keep the answer they had (#865 review) — except
            # application code outside the scope, named with the scope that
            # would read it.
            if callee.reason != MODULE_NOT_FOUND or import_node is None:
                return None
            statement, alias = import_node
            if isinstance(statement, ast.ImportFrom) and not statement.level:
                dotted, names = statement.module or "", [alias.name]
            elif isinstance(statement, ast.Import):
                dotted, names = alias.name, []
            else:
                return None
            if not dotted or not self.resolver.repository_holds(dotted, names):
                return None
            held = "; the repository holds it outside the read scope, which a scope including it would read"
            return (
                dataclasses.replace(
                    callee,
                    reference=reference,
                    detail=f"{reference!r} is the tool {spelling!r} returns at {where}, and {callee.detail}{held}",
                ),
                False,
            )
        factory, home = callee.definition, callee.module
        assert factory is not None and home is not None
        if module is self.module and not found:
            self._require_proven_name(name)
        key = (home.ref, factory.lineno)
        # The factory's body and this call's own arguments are part of what
        # the tool does: its closure (#865 review).
        # The values the call gives the factory's parameters are what the
        # closure holds; a value this read cannot name leaves it unknown.
        values, unnamed = self._call_values(call, factory, module, home, outer)
        # Unnamed: the call as written, and which value is not named — the
        # implementation is then an open question, not a changed tool.
        # An unnamed value may be set anywhere in the calling module: its
        # bytes stand in for it, so an untouched module is no change.
        digest = hashlib.sha256(
            (_code_dump(factory) + (values if values is not None else _dump(call) + module.sha256)).encode()
        ).hexdigest()
        steps = (
            *callee.steps,
            {
                **_step(home, factory, "factory"),
                "factory_ast": digest if unnamed is None else f"unknown:{digest}:{unnamed}",
                "returns_tool": _returns_tool(factory, self._names_of(home)[0]),
            },
        )

        def stop(detail: str) -> tuple[Resolution, bool]:
            return (
                Resolution(
                    reference=reference,
                    reason=FACTORY_RETURN,
                    detail=f"{reference!r} is the tool factory {factory.name!r} ({home.ref}:{factory.lineno}) returns, and {detail}",
                    steps=steps,
                    caveats=callee.caveats,
                ),
                False,
            )

        if key in seen:
            return stop("it calls itself on the way, which is not followed")
        if depth >= MAX_FACTORY_DEPTH:
            return stop(f"it is reached through more than {MAX_FACTORY_DEPTH} factories, which is not followed")
        if factory.decorator_list:
            return stop("it is decorated, which may change what it returns")
        if isinstance(factory, ast.AsyncFunctionDef):
            return stop("it is a coroutine function, whose call returns a coroutine, not a tool")
        returns, yields = _own_returns(factory)
        if yields:
            return stop("it is a generator")
        if len(returns) != 1:
            return stop(f"it returns from {len(returns)} places" if returns else "it never returns")
        returned = returns[0]
        if returned not in factory.body:
            return stop(f"it returns only under a condition, at {home.ref}:{returned.lineno}")
        if returned.value is None:
            return stop("it returns nothing")
        followed = self._factory_returned(
            returned.value, factory, home, returned, reference, depth, seen | {key}, stop
        )
        resolution, long_running = followed
        return (
            dataclasses.replace(
                resolution,
                steps=(*steps, *resolution.steps),
                caveats=tuple(dict.fromkeys((*callee.caveats, *resolution.caveats))),
            ),
            long_running,
        )

    def _function_lines(self, module: PythonModule) -> dict[str, list[int]]:
        """``name -> lines`` of the functions a tool can be minted from in a
        module — not a class's methods — read once per module (#865 review)."""

        lines = self.module_function_lines.get(module.ref)
        if lines is None:
            lines = {}
            stack: list[ast.AST] = [module.tree]
            while stack:
                node = stack.pop()
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, ast.ClassDef):
                        continue
                    if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                        lines.setdefault(child.name, []).append(child.lineno)
                    stack.append(child)
            for found in lines.values():
                found.sort()
            self.module_function_lines[module.ref] = lines
        return lines

    def _call_values(
        self,
        call: ast.Call,
        factory: ast.FunctionDef | ast.AsyncFunctionDef,
        module: PythonModule,
        home: PythonModule,
        outer: ast.FunctionDef | ast.AsyncFunctionDef | None = None,
    ) -> tuple[str | None, str | None]:
        """What each of the factory's parameters holds at this call, as data,
        with the factory's defaults for the rest — so ``make(True)``,
        ``make(readonly=True)`` and ``mk(readonly=True)`` are one call — and,
        when a value is not data this read can name, which one: ``(None,
        "readonly=ro at agent.py:12")`` (#865 review)."""

        arguments = factory.args
        positional = [*arguments.posonlyargs, *arguments.args]
        where = f"{module.ref}:{call.lineno}"
        if any(isinstance(item, ast.Starred) for item in call.args) or any(item.arg is None for item in call.keywords):
            return None, f"'*' or '**' arguments at {where}"
        if len(call.args) > len(positional):
            return None, f"extra positional arguments at {where}"
        given: dict[str, ast.expr] = {item.arg: arg for item, arg in zip(positional, call.args, strict=False)}
        for keyword in call.keywords:
            assert keyword.arg is not None
            given[keyword.arg] = keyword.value
        names = [item.arg for item in [*positional, *arguments.kwonlyargs]]
        if set(given) - set(names):
            return None, f"arguments the factory does not name at {where}"
        defaults: dict[str, ast.expr] = dict(
            zip([item.arg for item in positional[len(positional) - len(arguments.defaults) :]], arguments.defaults, strict=False)
        )
        for item, default in zip(arguments.kwonlyargs, arguments.kw_defaults, strict=False):
            if default is not None:
                defaults[item.arg] = default
        held: dict[str, str] = {}
        unnamed: list[str] = []
        for name in names:
            if name in given:
                data = self._data(given[name], module, call, outer=outer)
                expression = given[name]
            elif name in defaults:
                data = self._data(defaults[name], home, defaults[name])
                expression = defaults[name]
            else:
                return None, f"no value for {name!r} at {where}"
            if data is None:
                unnamed.append(f"{name}={ast.unparse(expression)}")
            else:
                held[name] = data
        if unnamed:
            return None, f"{', '.join(unnamed)} at {where}"
        return json.dumps(sorted(held.items())), None

    def _data(
        self,
        node: ast.expr,
        module: PythonModule,
        at: ast.AST,
        depth: int = 0,
        *,
        outer: ast.FunctionDef | ast.AsyncFunctionDef | None = None,
    ) -> str | None:
        """``node`` as data: a literal; a name or module attribute bound once,
        unconditionally, to one — a list, dict or set only when nothing else
        in its scope uses it, and never a module's, which another module can
        change; a function, by its code; or a parameter of the factory this
        call is made in, which that factory's own call supplies. Else None."""

        if depth > 4:
            return None
        if _written_out(node):
            if isinstance(node, ast.Set):
                return "{" + ",".join(sorted(_dump(item) for item in node.elts)) + "}"
            return _dump(node)
        scopes = self._scopes_for(module)
        if isinstance(node, ast.Name):
            found = scopes.enclosing_bindings(at, node.id)
            if found:
                if len(found) != 1:
                    return None
                if isinstance(found[0], ast.arg) and outer is not None and scopes.parents.get(
                    scopes.parents.get(found[0])
                ) is outer:
                    # ``def make_named(readonly): return make_sql_tool(readonly=readonly)``.
                    return f"<parameter {node.id}>"
                if not isinstance(found[0], ast.Name):
                    return None
                statement = scopes.statement_of(found[0])
                if not (
                    isinstance(statement, ast.Assign | ast.AnnAssign)
                    and statement.value is not None
                    and (statement.targets == [found[0]] if isinstance(statement, ast.Assign) else statement.target is found[0])
                    and _unconditional(statement, scopes)
                ):
                    return None
                if (isinstance(statement.value, ast.Name) or _holds_mutable(statement.value)) and _changed_at(
                    scopes.parents[statement], node.id, scopes.parents, bound=found[0], allowed=lambda use: use is node
                ) is not None:
                    # ``policy = {...}; policy["readonly"] = False``, also
                    # through ``policy = base`` or ``({...},)``.
                    return None
                return self._data(statement.value, module, statement, depth + 1)
        spelling = reference_spelling(node)
        if spelling is None or self.resolver is None:
            return None
        resolution = self.resolver.resolve(module, spelling)
        if resolution.caveats or resolution.module is None:
            return None
        if resolution.definition is not None:
            # ``make(upper)``: a function, by its code.
            return "<function " + _code_dump(resolution.definition) + ">"
        value = resolution.value
        if value is None or _holds_mutable(value):
            # A module's mutable value another module can change.
            return None
        return self._data(value, resolution.module, value, depth + 1)

    def _factory_returned(
        self,
        value: ast.expr,
        factory: ast.FunctionDef | ast.AsyncFunctionDef,
        home: PythonModule,
        returned: ast.Return,
        reference: str,
        depth: int,
        seen: frozenset[tuple[str, int]],
        stop: Callable[[str], tuple[Resolution, bool]],
        *,
        through: str | None = None,
    ) -> tuple[Resolution, bool]:
        """What a factory's ``return`` value is, as a resolution with only the
        steps past the factory itself."""

        scopes = self._scopes_for(home)
        aliases, _ = self._names_of(home)
        if isinstance(value, ast.Call):
            call_name = _qualified_name(value.func, aliases)
            if call_name in FUNCTION_TOOL_NAMES | LONG_RUNNING_TOOL_NAMES:
                func_expr = _call_func_expr(value)
                if func_expr is None:
                    return stop(f"its {call_name} names no function")
                root = value.func
                while isinstance(root, ast.Attribute):
                    root = root.value
                if isinstance(root, ast.Name):
                    # As for any wrapper: ADK's constructor only while the name
                    # is still the import it resolves through.
                    bound = self._names_of(home)[1].get(root.id, [])
                    local = scopes.enclosing_bindings(value, root.id)
                    adk_import = bool(local) and all(
                        isinstance(item, ast.alias)
                        and isinstance(statement := scopes.statement_of(item), ast.ImportFrom)
                        and (statement.module or "").startswith("google.adk")
                        for item in local
                    )
                    if not adk_import and (local or len(bound) != 1 or not isinstance(bound[0], ast.alias)):
                        self._note_surface_gap(SURFACE_GAP_SHADOWED_FRAMEWORK_SYMBOL)
                resolution = self._factory_function(
                    func_expr, factory, home, returned, reference, stop, wrapper=value
                )
                return resolution, resolution.resolved and call_name in LONG_RUNNING_TOOL_NAMES
            followed = self._factory_call(value, home, reference, depth=depth + 1, seen=seen, outer=factory)
            if followed is None:
                return stop(
                    f"it returns what {reference_spelling(value.func) or 'a call'!r} returns at "
                    f"{home.ref}:{value.lineno}, which is not application code this read follows"
                )
            return followed
        if isinstance(value, ast.Name) and through is None:
            found = scopes.enclosing_bindings(value, value.id)
            if len(found) == 1 and isinstance(found[0], ast.Name):
                statement = scopes.statement_of(found[0])
                bound_to = (
                    statement.value
                    if isinstance(statement, ast.Assign)
                    and len(statement.targets) == 1
                    and statement.targets[0] is found[0]
                    else statement.value
                    if isinstance(statement, ast.AnnAssign) and statement.target is found[0]
                    else None
                )
                if bound_to is not None and statement in factory.body and statement.lineno < returned.lineno:
                    # ``tool = FunctionTool(inner)`` then ``return tool``.
                    changed = _changed_at(
                        factory, value.id, scopes.parents, bound=found[0], returned=returned
                    )
                    if changed is not None:
                        return stop(f"it changes the {value.id!r} it returns at {home.ref}:{changed}")
                    return self._factory_returned(
                        bound_to, factory, home, returned, reference, depth, seen, stop, through=value.id
                    )
        if isinstance(value, ast.Name | ast.Attribute):
            # A plain callable: ADK wraps it in a ``FunctionTool`` itself.
            return self._factory_function(value, factory, home, returned, reference, stop), False
        return stop(f"it returns an expression at {home.ref}:{returned.lineno} this read does not follow")

    def _factory_function(
        self,
        expr: ast.expr,
        factory: ast.FunctionDef | ast.AsyncFunctionDef,
        home: PythonModule,
        returned: ast.Return,
        reference: str,
        stop: Callable[[str], tuple[Resolution, bool]],
        *,
        wrapper: ast.Call | None = None,
    ) -> Resolution:
        """The function a factory wraps or returns: a function nested in it,
        defined once, before its ``return`` and never changed or handed on
        (``inner.__name__ = ...`` renames the tool), or one its module binds."""

        assert self.resolver is not None
        spelling = reference_spelling(expr)
        if spelling is None:
            return stop(f"the function it wraps at {home.ref}:{returned.lineno} is not a named function")[0]
        scopes = self._scopes_for(home)
        name = spelling.split(".", 1)[0]
        found = scopes.enclosing_bindings(expr, name)
        if len(found) > 1:
            return stop(local_binding_detail(home.ref, name, found[0], rebound=True))[0]
        if found:
            inner = found[0]
            statement = scopes.statement_of(inner)
            if isinstance(inner, ast.alias) and isinstance(statement, ast.Import | ast.ImportFrom):
                changed = (
                    _changed_at(factory, name, scopes.parents, bound=inner, returned=returned, wrapper=wrapper)
                    if spelling == name
                    else _attribute_changed(factory, spelling, scopes.parents, returned=returned, wrapper=wrapper)
                )
                if changed is not None:
                    return stop(f"{spelling!r} is changed or handed on at {home.ref}:{changed}, which may rename it")[0]
                resolution = self.resolver.resolve_local_import(home, statement, inner, spelling)
                return dataclasses.replace(resolution, reference=reference)
            if not (
                isinstance(inner, ast.FunctionDef | ast.AsyncFunctionDef)
                and spelling == name
                and inner in factory.body
                and inner.lineno < returned.lineno
            ):
                return stop(local_binding_detail(home.ref, name, inner))[0]
            if inner.decorator_list:
                return stop(f"{name!r} ({home.ref}:{inner.lineno}) is decorated, which may replace it")[0]
            same = self._function_lines(home).get(name, [])
            if len(same) > 1:
                # A tool is known by its name in its file (#865 review).
                return stop(
                    f"{home.ref} defines {name!r} more than once (lines "
                    f"{', '.join(str(line) for line in same)}), so which one is the tool is not established"
                )[0]
            changed = _changed_at(
                factory, name, scopes.parents, bound=inner, returned=returned, wrapper=wrapper
            )
            if changed is not None:
                return stop(
                    f"{name!r} is changed or handed on at {home.ref}:{changed}, which may rename it"
                )[0]
            return Resolution(
                reference=reference,
                module=home,
                definition=inner,
                steps=(_step(home, inner, "definition"),),
            )
        # ``search.__name__ = "lookup"`` in the factory renames it too, as
        # does ``impl.search.__name__ = ...``.
        changed = (
            _changed_at(factory, name, scopes.parents, bound=factory, returned=returned, wrapper=wrapper)
            if spelling == name
            else _attribute_changed(factory, spelling, scopes.parents, returned=returned, wrapper=wrapper)
        )
        if changed is not None:
            return stop(f"{spelling!r} is changed or handed on at {home.ref}:{changed}, which may rename it")[0]
        resolution = self.resolver.resolve(home, spelling)
        if resolution.resolved:
            return dataclasses.replace(resolution, reference=reference)
        if resolution.reason in (None, NOT_BOUND):
            return stop(f"{spelling!r} is not bound in {home.ref}")[0]
        return dataclasses.replace(
            resolution,
            reference=reference,
            detail=(
                f"{reference!r} is the tool factory {factory.name!r} ({home.ref}:"
                f"{factory.lineno}) returns, and {resolution.detail}"
            ),
        )

    def _imported_root(self, expr: ast.Attribute) -> bool:
        """Whether a dotted reference starts at a name this module imports."""

        spelling = reference_spelling(expr)
        if spelling is None or self.module is None:
            return False
        bindings = self.module.bindings.get(spelling.split(".", 1)[0], [])
        return any(isinstance(item.node, ast.alias) for item in bindings)

    def _unresolved_reference(
        self, agent_name: str, spelling: str, resolution: Resolution | None
    ) -> None:
        """Name one tool reference that did not reach a definition.

        The warning keeps its decoded wording; the import-resolution reason is
        recorded beside it, keyed by the warning, so a consumer can say *why*
        without the mechanism's sentence changing.
        """

        warning = adk_unresolved_tool_warning(agent_name, spelling)
        self._surface_warning(warning, SURFACE_GAP_UNRESOLVED_REFERENCE)
        self._record_unresolved_reference(warning, agent_name, spelling, resolution)

    def _record_unresolved_reference(
        self,
        warning: str,
        agent_name: str,
        spelling: str,
        resolution: Resolution | None,
    ) -> None:
        if resolution is None or resolution.reason in (None, NOT_BOUND):
            return
        record = {
            "agent_name": agent_name,
            "reference": spelling,
            "warning": warning,
            "reason": resolution.reason,
            "detail": resolution.detail,
            "source_id": self.source_id,
            "source_ref": self.source_ref,
            "import_resolution": resolution.evidence(),
        }
        # A factory called N times inline is one record (#865 review).
        if record not in self.artifacts.unresolved_references:
            self.artifacts.unresolved_references.append(record)

    def _bind_wrapped_reference(
        self,
        func_expr: ast.AST | None,
        tools: list[Tool],
        agent_name: str,
        binding: _AdkAgentBinding,
        long_running: bool,
        warning: str,
    ) -> None:
        """Bind ``FunctionTool(<imported function>)``, or report the wrapper."""

        spelling = reference_spelling(func_expr) if func_expr is not None else None
        local = (
            self._local_meaning(func_expr, spelling)
            if spelling is not None and func_expr is not None
            else None
        )
        if isinstance(local, tuple):
            resolution, wrapped_long_running = local
        else:
            resolution, wrapped_long_running = (
                self._resolve_reference(spelling) if spelling else (None, False)
            )
        if resolution is not None and resolution.resolved:
            self._bind_resolved(
                resolution, tools, agent_name, binding, long_running or wrapped_long_running
            )
            return
        if resolution is not None and resolution.reason not in (None, NOT_BOUND):
            warning = f"{warning[:-1]}; {resolution.detail}."
        self._surface_warning(warning, SURFACE_GAP_UNRESOLVED_WRAPPER)
        if spelling is not None:
            self._record_unresolved_reference(warning, agent_name, spelling, resolution)

    def _bind_resolved(
        self,
        resolution: Resolution,
        tools: list[Tool],
        agent_name: str,
        binding: _AdkAgentBinding,
        long_running: bool,
        *,
        spelled_in: PythonModule | None = None,
    ) -> None:
        """Bind the definition an import chain reached, once per definition."""

        node, module = resolution.definition, resolution.module
        assert node is not None and module is not None
        # A factory's function: its call was proven where it was read (#865).
        made = any(step.get("binding") == "factory" for step in resolution.steps)
        if not made:
            # The spelling has to hold up like a local name where it is
            # written: here, or in the module whose list holds it (#909).
            if not self._name_is_proven(resolution.reference.split(".", 1)[0], spelled_in):
                self._note_surface_gap(SURFACE_GAP_SHADOWED_DEFINITION)
        if any(step.get("module_getattr") for step in resolution.steps):
            # A package ``__getattr__`` could have answered before the
            # submodule did; the definition is named, not proven.
            self._note_surface_gap(SURFACE_GAP_SHADOWED_DEFINITION)
        if module is self.module and not (made and node not in module.tree.body):
            # ``alias = local_function``: the chain came back to this module.
            self._bind_function_tool(node, tools, agent_name, binding, long_running)
            tool = self.canonical_function_tools.get(node.name)
        else:
            key = (module.ref, node.lineno)
            tool = self.imported_function_tools.get(key)
            if tool is None:
                aliases, name_bindings = self._names_of(module)
                if len(name_bindings.get(node.name, [])) != 1:
                    # Parameters and locals elsewhere in that module count, as
                    # they do for a local definition: resolving is not proving.
                    self._note_surface_gap(SURFACE_GAP_SHADOWED_DEFINITION)
                tool = self._function_to_tool(
                    node,
                    agent_name,
                    long_running,
                    source_ref=module.ref,
                    aliases=aliases,
                    name_is_canonical=lambda name: _name_is_canonical_in(
                        name, name_bindings, aliases
                    ),
                )
                if module is not self.module:
                    # Minted from an import: another source reading that module
                    # observes the same definition; the catalog keeps one (#879).
                    tool.extraction["imported_definition"] = True
                self.imported_function_tools[key] = tool
                tools.append(tool)
            else:
                self._reconcile_long_running(tool, node.name, agent_name, long_running)
            self._bind_tool_edge(tool, agent_name, binding)
        if tool is None:
            return
        if resolution.caveats and tool.name not in binding.duplicated:
            # Code that runs before the name is used is not read: the
            # definition is named, never established as the one bound (#879
            # review). ``scan`` holds the module at medium.
            self._note_surface_gap(SURFACE_GAP_SHADOWED_DEFINITION)
            binding.tool_issues[tool.name] = (
                f"Google ADK agent {agent_name!r} binds {tool.name!r} "
                f"({tool.source_location}), but {'; '.join(resolution.caveats)}; the "
                "definition read for it is not established as the one bound."
            )
        evidence = resolution.evidence()
        recorded = tool.extraction.setdefault("import_resolutions", [])
        if evidence not in recorded:
            recorded.append(evidence)
        # Every factory call that makes this tool is part of its
        # implementation: the factory's body and the call's arguments, which
        # its closure holds (#865 review).
        # Per agent: another agent's call to the same factory is its own.
        # ``unknown``: a call passes a value this read cannot name.
        made = [str(step["factory_ast"]) for step in resolution.steps if "factory_ast" in step]
        if made:
            calls = tool.extraction.setdefault("factory_calls", {})
            calls[agent_name] = sorted({*calls.get(agent_name, []), *made})
            if binding.recording is not None:
                # This construction's closure is part of its site: two
                # constructions whose factory values differ are not twins
                # (#865 review).
                binding.recording.append((f"{tool.name}@factory", ",".join(sorted(made))))

    def _names_of(
        self, module: PythonModule
    ) -> tuple[dict[str, str], dict[str, list[ast.AST]]]:
        names = self.module_names.get(module.ref)
        if names is None:
            names = (_import_aliases(module.tree), _name_binding_occurrences(module.tree))
            self.module_names[module.ref] = names
        return names

    def _bind_function_tool(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        tools: list[Tool],
        agent_name: str,
        binding: _AdkAgentBinding,
        long_running: bool,
    ) -> None:
        """Bind one function definition to one agent.

        The first binding creates the canonical catalog observation; later
        bindings of the same definition only add an edge. Emitting a tool per
        binding would model one action as several independent capabilities
        (and collide on tool observation identity).
        """

        self._require_proven_name(node.name)
        tool = self.canonical_function_tools.get(node.name)
        if tool is None:
            tool = self._function_to_tool(node, agent_name, long_running)
            self.canonical_function_tools[node.name] = tool
            tools.append(tool)
        else:
            self._reconcile_long_running(tool, node.name, agent_name, long_running)
        self._bind_tool_edge(tool, agent_name, binding)

    def _reconcile_long_running(
        self, tool: Tool, function_name: str, agent_name: str, long_running: bool
    ) -> None:
        if long_running == (tool.annotations.get("long_running") is True):
            return
        # The same function wrapped as both FunctionTool and
        # LongRunningFunctionTool is a contradictory declaration about one
        # action. Keep the stricter contract and route it to review rather
        # than letting binding order decide.
        self._surface_warning(
            f"Google ADK function {function_name!r} is bound as both a long-running "
            "and a standard function tool; review its operation contract.",
            SURFACE_GAP_CONFLICTING_CONTRACT,
        )
        if long_running:
            tool.annotations["long_running"] = True
            self.artifacts.long_running_tools.append(
                self._function_tool_payload(tool, agent_name)
            )

    def _bind_tool_edge(
        self, tool: Tool, agent_name: str, binding: _AdkAgentBinding
    ) -> None:
        """One agent -> definition edge, keyed by the definition's locator."""

        locator = f"{tool.source_ref}#{tool.name}"
        location = tool.source_location or self.source_ref
        if binding.binds_other_definition(tool.name, locator):
            warning = (
                f"Google ADK agent {agent_name!r} binds two different functions "
                f"named {tool.name!r} ({binding.tool_locations[tool.name]} and "
                f"{location}); the model sees one tool name for both."
            )
            self._surface_warning(warning, SURFACE_GAP_DUPLICATE_TOOL_NAME)
            binding.unbind_duplicate(tool.name, warning)
            self.artifacts.tool_bindings = [
                item
                for item in self.artifacts.tool_bindings
                if not (item.get("agent_name") == agent_name and item.get("tool_name") == tool.name)
            ]
            return
        if binding.bind(tool.name, locator, location):
            _record_tool_binding(
                self.artifacts,
                agent_name=agent_name,
                tool_name=tool.name,
                source_ref=tool.source_location or self.source_ref,
            )

    def _extract_toolset_call(
        self,
        call: ast.Call,
        agent_name: str,
        binding: _AdkAgentBinding,
    ) -> list[LoadedToolSource]:
        """Extract one toolset construction, at most once per call site.

        A toolset bound to a variable and shared between agents is one tool
        surface, so it is loaded once; every sharing agent gets an edge.
        """

        cached = self.toolset_tool_names.get(id(call))
        if cached is not None:
            self._bind_toolset_tools(cached, agent_name, binding)
            # One toolset, several agents. The construction is extracted once,
            # so this is the only place the second agent's attribution can be
            # recorded at all (#538).
            self._record_binding_agent(call, agent_name)
            return []
        self._require_proven_framework_symbol(call)
        call_name = _qualified_name(call.func, self.aliases)
        if call_name in OPENAPI_TOOLSET_NAMES:
            loaded_sources = self._extract_openapi_toolset(call, agent_name)
        else:
            loaded_sources = self._extract_mcp_toolset(call, agent_name)
        self._record_binding_agent(call, agent_name)
        tool_names = [
            tool.name for loaded in loaded_sources for tool in loaded.tools
        ]
        self.toolset_tool_names[id(call)] = tool_names
        self._bind_toolset_tools(tool_names, agent_name, binding)
        return loaded_sources

    def _record_binding_agent(self, call: ast.Call, agent_name: str) -> None:
        """Note that ``agent_name`` binds the toolset built at ``call``."""

        toolset = self.toolset_records.get(id(call))
        if toolset is None:
            return
        if agent_name not in toolset.binding_agents:
            toolset.binding_agents.append(agent_name)

    def _bind_toolset_tools(
        self,
        tool_names: list[str],
        agent_name: str,
        binding: _AdkAgentBinding,
    ) -> None:
        for tool_name in tool_names:
            if binding.bind(tool_name):
                _record_tool_binding(
                    self.artifacts,
                    agent_name=agent_name,
                    tool_name=tool_name,
                    source_ref=self.source_ref,
                )

    def _function_to_tool(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        agent_name: str,
        long_running: bool,
        *,
        source_ref: str | None = None,
        aliases: dict[str, str] | None = None,
        name_is_canonical: Callable[[str], bool] | None = None,
    ) -> Tool:
        """One catalog observation of one function definition.

        ``source_ref``, ``aliases`` and ``name_is_canonical`` describe the
        module that *defines* the function — this entrypoint unless the
        definition was reached through an import (#864), in which case its
        annotations are read in its own module's spelling.
        """

        source_ref = source_ref or self.source_ref
        aliases = self.aliases if aliases is None else aliases
        name_is_canonical = name_is_canonical or self._name_is_canonical
        parameters = _parameters(node, aliases)
        return_type = _annotation_to_string(node.returns)
        signature = f"{node.name}({', '.join(param.name for param in parameters)})"
        if return_type:
            signature = f"{signature} -> {return_type}"
        input_schema = {
            "type": "object",
            "properties": {
                param.name: {"type": _json_schema_type(param.type)}
                for param in parameters
            },
            "required": [param.name for param in parameters if param.required],
        }
        tool = Tool(
            id=stable_tool_id(node.name),
            name=node.name,
            description=ast.get_docstring(node),
            source_type="google_adk_function",
            source_id=self.source_id,
            source_ref=source_ref,
            source_location=f"{source_ref}:{node.lineno}",
            input_schema=input_schema,
            output_schema={"type": _json_schema_type(return_type)} if return_type else {},
            parameters=parameters,
            function_signature=signature,
            annotations={
                # No ``adk_agent_name`` here: this Tool is the canonical
                # observation of one function definition, and the same
                # definition may be bound to several agents. The binding
                # relation travels as AgentBindingObservation instead.
                "adk_agent_source_id": self.source_id,
                "long_running": long_running,
            },
            auth=AuthInfo(source="google_adk_static"),
            # Provisional: ``_resolve_extraction_evidence`` settles both fields
            # once the whole module has been walked. ``medium`` here so a tool
            # is never high-confidence in flight.
            extraction_confidence="medium",
            extraction={
                "method": "google_adk_python_ast",
                "confidence": "medium",
                "surface_gaps": _function_surface_gaps(
                    node, aliases, name_is_canonical
                ),
            },
        )
        payload = self._function_tool_payload(tool, agent_name)
        self.artifacts.function_tools.append(payload)
        if long_running:
            self.artifacts.long_running_tools.append(payload)
        return tool

    def _function_tool_payload(self, tool: Tool, agent_name: str) -> dict[str, Any]:
        """One record per function definition (not per agent binding).

        ``agent_name`` is the first agent observed binding the definition;
        the complete set of binding agents lives in ``tool_bindings``.
        """

        return {
            "name": tool.name,
            "source_ref": tool.source_location,
            "agent_name": agent_name,
            "metadata_present": bool(tool.description and tool.parameters),
        }

    def _extract_openapi_toolset(self, call: ast.Call, agent_name: str) -> list[LoadedToolSource]:
        spec_path = _extract_path_argument(call, self.aliases, OPENAPI_PATH_KEYS)
        toolset = GoogleAdkToolset(
            kind="openapi",
            source_id=self.source_id,
            source_ref=f"{self.source_ref}:{call.lineno}",
            agent_name=agent_name,
            name="OpenAPIToolset",
            resolved=bool(spec_path),
            dynamic=not bool(spec_path),
            slot=self._slot_for(call, agent_name),
        )
        self.artifacts.toolsets.append(toolset)
        self.toolset_records[id(call)] = toolset
        if not spec_path:
            self._surface_warning(
                f"Google ADK OpenAPIToolset at {self.source_ref}:{call.lineno} "
                "has no static local spec path.",
                SURFACE_GAP_DYNAMIC_TOOLSET,
            )
            return []
        loaded = load_openapi_tools(
            ToolSourceConfig(
                id=f"{self.source_id}:openapi:{len(self.artifacts.toolsets)}",
                type="openapi",
                path=spec_path,
            ),
            self.entrypoint_dir,
        )
        for tool in loaded.tools:
            tool.annotations["adk_toolset"] = "OpenAPIToolset"
            # ``adk_agent_source_id`` keeps the binding resolver able to match
            # these tools back to the ADK source; the binding agents
            # themselves come from AgentBindingObservation.
            tool.annotations["adk_agent_source_id"] = self.source_id
        return [loaded]

    def _read_mcp_connection(self, call: ast.Call) -> GoogleAdkToolsetConnection:
        """Read one ``McpToolset``'s ``connection_params`` argument, statically.

        Never imports the module, constructs the object, resolves the endpoint,
        or reads the process environment. Every axis the reader could not
        establish keeps a status and a limitation code rather than a guess.

        A shadowed or rebound constructor keeps its read values and records the
        limitation instead of discarding them. The module-wide
        ``shadowed_framework_symbol`` gap already caps this file's extraction
        confidence, which is the engine's established answer to shadowing;
        blanking the values on top of that would destroy exactly the evidence
        this reader exists to preserve, and the limitation names the doubt.
        """

        limitations: list[str] = []
        expr = _kwarg(call, "connection_params")
        if expr is None:
            return GoogleAdkToolsetConnection(
                transport_status="absent",
                endpoint_status="absent",
                credential_status="absent",
            )
        if isinstance(expr, ast.Name):
            resolved = self.connection_assignments.get(expr.id)
            if resolved is None:
                limitations.append(LIMIT_UNRESOLVED_CONNECTION_REFERENCE)
                return _unresolved_connection(limitations)
            if not self._name_is_proven(expr.id):
                # The flat assignment map answers with one binding of a name
                # the module binds more than once. Naming the endpoint it
                # happens to hold would be a guess about which assignment was
                # in effect.
                self._note_surface_gap(SURFACE_GAP_SHADOWED_DEFINITION)
                limitations.append(LIMIT_REBOUND_CONNECTION_REFERENCE)
                return _unresolved_connection(limitations)
            expr = resolved
        if not isinstance(expr, ast.Call):
            limitations.append(LIMIT_DYNAMIC_CONNECTION_EXPRESSION)
            return _unresolved_connection(limitations)
        constructor_proven = self._framework_symbol_is_proven(expr)
        if not constructor_proven:
            self._note_surface_gap(SURFACE_GAP_SHADOWED_FRAMEWORK_SYMBOL)
            limitations.append(LIMIT_SHADOWED_CONNECTION_CONSTRUCTOR)
        constructor = _qualified_name(expr.func, self.aliases) or ""
        short_name = constructor.rsplit(".", 1)[-1]
        transport = MCP_CONNECTION_TRANSPORTS.get(short_name)
        if transport is None:
            limitations.append(LIMIT_UNRECOGNIZED_CONNECTION_CONSTRUCTOR)
            transport_status: RemoteBindingStatus = "unresolved"
        elif not constructor_proven:
            # The transport is the one axis derived *entirely* from the
            # constructor's identity, which is exactly what a rebinding puts in
            # doubt. The endpoint, the credential references and the filter are
            # read from literal arguments and survive: dropping them would
            # destroy the evidence this reader exists to preserve, and the
            # limitation above already names the doubt.
            transport = None
            transport_status = "unresolved"
        else:
            transport_status = "literal"
        endpoint, endpoint_env_ref, endpoint_status = _read_endpoint(
            expr, self.aliases, limitations
        )
        credential_refs, credential_status = self._read_connection_credentials(
            expr, limitations
        )
        return GoogleAdkToolsetConnection(
            transport=transport,
            transport_status=transport_status,
            constructor=short_name or None,
            endpoint=endpoint,
            endpoint_status=endpoint_status,
            endpoint_env_ref=endpoint_env_ref,
            credential_refs=credential_refs,
            credential_status=credential_status,
            limitations=sorted(set(limitations)),
        )

    def _read_connection_credentials(
        self,
        call: ast.Call,
        limitations: list[str],
    ) -> tuple[list[str], RemoteBindingStatus]:
        """Credential entries of one connection, including its nested params.

        A stdio connection does not carry ``env`` inline: ADK's
        ``StdioConnectionParams`` holds a ``StdioServerParameters`` under
        ``server_params``, and reading only the outer call reported
        ``credential_status: "absent"`` for a binding that plainly had one —
        a false claim of absence, and one that made a changed credential
        reference produce no delta at all (PR #540 review).

        The nested call is resolved the same way the outer one is: inline, or
        through a module-level name the module binds exactly once. Anything
        else reports ``unresolved`` with a limitation, never ``absent``.
        """

        entries, status = _read_credential_refs(call, self.aliases, limitations)
        nested = _kwarg(call, MCP_NESTED_PARAMS_ARG)
        if nested is None:
            return entries, status
        if isinstance(nested, ast.Name):
            resolved = self.connection_assignments.get(nested.id)
            if resolved is None or not self._name_is_proven(nested.id):
                limitations.append(LIMIT_UNRESOLVED_NESTED_PARAMS)
                return _merge_credentials(
                    entries,
                    status,
                    [f"{MCP_NESTED_PARAMS_ARG}={CREDENTIAL_UNREADABLE_MARKER}"],
                    "unresolved",
                )
            nested = resolved
        if not isinstance(nested, ast.Call) or not _is_connection_params_call(
            nested, self.aliases
        ):
            # An unrecognised call is not a server-parameters object this
            # reader knows the shape of. Reading its ``env`` anyway would be a
            # guess, and returning "absent" would be the same false claim one
            # level down from the one this method exists to fix.
            limitations.append(LIMIT_UNRESOLVED_NESTED_PARAMS)
            return _merge_credentials(
                entries,
                status,
                [f"{MCP_NESTED_PARAMS_ARG}={CREDENTIAL_UNREADABLE_MARKER}"],
                "unresolved",
            )
        nested_entries, nested_status = _read_credential_refs(
            nested,
            self.aliases,
            limitations,
            prefix=f"{MCP_NESTED_PARAMS_ARG}.",
        )
        return _merge_credentials(entries, status, nested_entries, nested_status)

    def _extract_mcp_toolset(self, call: ast.Call, agent_name: str) -> list[LoadedToolSource]:
        filter_values = _string_list(_kwarg_literal(call, "tool_filter"))
        inventory_path = _extract_path_argument(call, self.aliases, MCP_INVENTORY_KEYS)
        connection = self._read_mcp_connection(call)
        connection.filter_status = _filter_status(call, filter_values)
        if connection.filter_status == "unresolved":
            connection.limitations = sorted(
                {*connection.limitations, LIMIT_DYNAMIC_TOOL_FILTER}
            )
        if not self._framework_symbol_is_proven(call):
            # ``_extract_toolset_call`` already noted the module-wide gap; the
            # binding carries its own code so a reviewer reading one binding's
            # evidence sees the doubt without cross-referencing the module.
            connection.limitations = sorted(
                {*connection.limitations, LIMIT_SHADOWED_TOOLSET_CONSTRUCTOR}
            )
        toolset = GoogleAdkToolset(
            kind="mcp",
            source_id=self.source_id,
            source_ref=f"{self.source_ref}:{call.lineno}",
            agent_name=agent_name,
            name="McpToolset",
            filtered=bool(filter_values),
            filter_values=filter_values,
            inventory_path=inventory_path,
            resolved=bool(inventory_path),
            dynamic=not bool(inventory_path),
            slot=self._slot_for(call, agent_name),
            connection=connection,
        )
        self.artifacts.toolsets.append(toolset)
        self.toolset_records[id(call)] = toolset
        if not inventory_path:
            self._surface_warning(
                adk_mcp_inventory_warning(
                    f"{self.source_ref}:{call.lineno}",
                    agent_name=agent_name,
                    endpoint=_endpoint_phrase(connection),
                ),
                SURFACE_GAP_DYNAMIC_TOOLSET,
            )
            return []
        loaded = load_mcp_tools(
            ToolSourceConfig(
                id=f"{self.source_id}:mcp:{len(self.artifacts.toolsets)}",
                type="mcp",
                path=inventory_path,
            ),
            self.entrypoint_dir,
        )
        for tool in loaded.tools:
            tool.annotations["adk_toolset"] = "McpToolset"
            tool.annotations["adk_agent_source_id"] = self.source_id
        return [loaded]

    def _record_agent_callbacks_plugins_subagents(self, call: ast.Call, agent_name: str) -> None:
        for keyword in call.keywords:
            if keyword.arg in CALLBACK_KEYS or (keyword.arg or "").endswith("_callback"):
                self.artifacts.callbacks.append(
                    {
                        "agent_name": agent_name,
                        "callback": keyword.arg,
                        "source_ref": f"{self.source_ref}:{call.lineno}",
                    }
                )
            elif keyword.arg == "plugins":
                plugin_count = len(keyword.value.elts) if isinstance(keyword.value, ast.List | ast.Tuple) else None
                self.artifacts.plugins.append(
                    {
                        "agent_name": agent_name,
                        "plugin_count": plugin_count,
                        "source_ref": f"{self.source_ref}:{call.lineno}",
                    }
                )
            elif keyword.arg == "sub_agents":
                value = keyword.value
                if self._invocation is None and isinstance(value, ast.List | ast.Tuple) and not any(
                    isinstance(item, ast.Starred) for item in value.elts
                ):
                    members = [ListMember(item, None) for item in value.elts]
                    complete = True
                    unread = None
                else:
                    # A spread, concatenation, conditional or module list (#909).
                    # Read, not proven for ``scan``: another module could change
                    # the list, and the sub-agents it holds bring their tools.
                    listed = self.lists.resolve(value, invocation=self._invocation)
                    members, complete = list(listed.members), listed.complete
                    unread = unread_parts(listed) if listed.unresolved else None
                    if self._invocation is not None and not complete:
                        self._binding_for(agent_name, call).handoffs_complete = False
                        self._surface_warning(
                            f"Google ADK agent {agent_name!r} has unresolved caller-supplied sub-agents: {unread}.",
                            SURFACE_GAP_DYNAMIC_TOOLS,
                        )
                    if _at_risk(listed):
                        self._note_surface_gap(SURFACE_GAP_DYNAMIC_TOOLS)
                # None, unless every member is read: the graph then names the
                # list as not statically named.
                sub_agent_count = len(members) if complete else None
                # Three outcomes per element, kept apart because they mean
                # different things to the binding graph. Resolved to an agent
                # this module defines: a real handoff target. Named but
                # matching no agent definition — an import, an ambiguous
                # rebinding: recorded so the graph can say a branch of the
                # capability surface was not followed, never bound to the
                # spelling itself (that produced a phantom node whose empty
                # tool set read as proof of no capability). Not nameable at
                # all — an inline construction, a call: left to the count.
                sub_agent_names: list[str] = []
                unresolved_sub_agents: list[str] = []
                when = Conditions()
                for member in members:
                    item = member.expr
                    if member.module is not None:
                        # An agent another module's list names: not one this
                        # module defines, so not matched, as an import is not.
                        spelling = reference_spelling(item)
                        if spelling is not None:
                            unresolved_sub_agents.append(spelling)
                            self._note_surface_gap(SURFACE_GAP_UNRESOLVED_SUB_AGENT)
                            if self._invocation is not None:
                                self._binding_for(agent_name, call).handoffs_complete = False
                                self._surface_warning(
                                    f"Google ADK agent {agent_name!r} has a caller-supplied sub-agent {spelling!r} in {member.module.ref} whose construction surface is not read by this increment.",
                                    SURFACE_GAP_UNRESOLVED_SUB_AGENT,
                                )
                        continue
                    variable = _qualified_name(item, self.aliases)
                    if variable is None:
                        continue
                    # Read from where the member is written: a module list's
                    # names are the module's.
                    resolved = self._sub_agent_name(variable, item)
                    if resolved is None:
                        unresolved_sub_agents.append(variable)
                        # A handoff target this module does not define owns
                        # tools this module never saw, so the file cannot
                        # claim it enumerated the surface reachable from
                        # here. Deliberately no warning: #385 left an
                        # unreached branch ungated, and adding one now would
                        # move repositories between verdicts for a reason
                        # this change is not about.
                        self._note_surface_gap(SURFACE_GAP_UNRESOLVED_SUB_AGENT)
                    else:
                        sub_agent_names.append(resolved)
                        when.add(resolved, member.conditions)
                if self._invocation is not None:
                    binding = self._binding_for(agent_name, call)
                    for name in sub_agent_names:
                        locations = binding.handoff_sites.setdefault(name, [])
                        for location in (f"{self.source_ref}:{call.lineno}", *self._invocation.locations):
                            if location not in locations:
                                locations.append(location)
                self.artifacts.sub_agents.append(
                    {
                        "agent_name": agent_name,
                        "source_id": self.source_id,
                        # Present on every Python-entrypoint record and on no
                        # Agent Config record; the binding graph reads it to
                        # tell the two apart. None when ``sub_agents`` is not
                        # a literal sequence.
                        "sub_agent_count": sub_agent_count,
                        "sub_agents": sub_agent_names,
                        "unresolved_sub_agents": unresolved_sub_agents,
                        # ``name -> [condition, ...]`` for a sub-agent held
                        # only under a condition (#909).
                        "conditions": when.only_when(),
                        # Each part of the list not read, and where it is.
                        **({"unread": unread} if unread else {}),
                        "source_ref": f"{self.source_ref}:{call.lineno}",
                    }
                )

    def _record_eval_references(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign):
                target_names = {
                    target.id.lower()
                    for target in node.targets
                    if isinstance(target, ast.Name)
                }
                if any("eval" in name for name in target_names):
                    self._record_eval_values(_literal_strings(node.value))
            elif isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg in EVAL_PATH_KEYS:
                        self._record_eval_values(_literal_strings(keyword.value))

    def _record_eval_values(self, values: list[str]) -> None:
        for value in values:
            if not _looks_like_local_artifact(value):
                continue
            try:
                path = resolve_input_path(self.entrypoint_dir, value)
            except InputParseError:
                self._note_warning(
                    f"Google ADK eval reference {value!r} resolves outside the entrypoint directory."
                )
                continue
            if not path.exists():
                self._note_warning(
                    f"Google ADK eval reference {value!r} was detected but not found."
                )
                continue
            display = _display_path(path, self.base_dir)
            if display not in self.artifacts.eval_files:
                _append_unique(self.artifacts.eval_files, display)


def _record_config_callbacks_and_plugins(
    data: dict[str, Any],
    source_ref: str,
    agent_name: str,
    artifacts: GoogleAdkArtifacts,
) -> None:
    for key, value in data.items():
        if key in CALLBACK_KEYS or key.endswith("_callback"):
            artifacts.callbacks.append(
                {"agent_name": agent_name, "callback": key, "source_ref": source_ref}
            )
        elif key == "plugins" and isinstance(value, list):
            artifacts.plugins.append(
                {
                    "agent_name": agent_name,
                    "plugin_count": len(value),
                    "source_ref": source_ref,
                }
            )


def _record_config_eval_refs(
    data: dict[str, Any],
    config_base_dir: Path,
    artifacts: GoogleAdkArtifacts,
) -> None:
    for key in EVAL_PATH_KEYS:
        values = data.get(key)
        for value in _config_string_values(values):
            try:
                path = resolve_input_path(config_base_dir, value)
            except InputParseError:
                artifacts.warnings.append(
                    f"Google ADK Agent Config eval reference {value!r} resolves outside the config directory."
                )
                continue
            if not path.exists():
                artifacts.warnings.append(
                    f"Google ADK Agent Config eval reference {value!r} was detected but not found."
                )
                continue
            display = _display_path(path, config_base_dir)
            if display not in artifacts.eval_files:
                _append_unique(artifacts.eval_files, display)


def _resolve_existing_path(ref: ArtifactPathConfig, base_dir: Path) -> Path:
    path = resolve_input_path(base_dir, ref.path)
    if not path.exists():
        raise InputParseError(f"Input file not found: {path}")
    return path


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                local = alias.asname or alias.name
                aliases[local] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".", 1)[0]
                aliases[local] = alias.name
    return aliases


def _qualified_name(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name(node.value, aliases)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return None


def adk_agent_subclasses(
    tree: ast.Module,
    aliases: dict[str, str] | None = None,
    name_bindings: dict[str, list[ast.AST]] | None = None,
) -> dict[str, int]:
    """Classes deriving from a Google ADK agent class, by name, with their line (#876).

    Transitively within the module: ``class Deeper(Helper)`` is one too. A
    base is ADK's only while its root name is bound by nothing but an import,
    as ``_framework_symbol_is_proven`` requires of a call.
    """

    aliases = _import_aliases(tree) if aliases is None else aliases
    name_bindings = _name_binding_occurrences(tree) if name_bindings is None else name_bindings
    classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    found: dict[str, int] = {}
    grown = True
    while grown:
        grown = False
        for node in classes:
            if node.name in found:
                continue
            for base in node.bases:
                expression = base.value if isinstance(base, ast.Subscript) else base
                root = expression
                while isinstance(root, ast.Attribute):
                    root = root.value
                if isinstance(expression, ast.Name) and expression.id in found:
                    derives = True
                else:
                    derives = (
                        isinstance(root, ast.Name)
                        and _qualified_name(expression, aliases) in AGENT_CLASS_NAMES
                        and all(
                            isinstance(binding, ast.alias)
                            for binding in name_bindings.get(root.id, [])
                        )
                    )
                if derives:
                    found[node.name] = node.lineno
                    grown = True
                    break
    return found


def _simple_target_name(targets: list[ast.expr]) -> str | None:
    if len(targets) != 1:
        return None
    target = targets[0]
    return target.id if isinstance(target, ast.Name) else None


def _call_func_name(call: ast.Call) -> str | None:
    func = _call_func_expr(call)
    if isinstance(func, ast.Name):
        return func.id
    return None


def _call_func_expr(call: ast.Call) -> ast.AST | None:
    """The expression a ``FunctionTool(...)`` call wraps, as written."""

    func = _kwarg(call, "func")
    if func is None and call.args:
        func = call.args[0]
    return func


def _name_is_canonical_in(
    name: str, bindings: dict[str, list[ast.AST]], aliases: dict[str, str]
) -> bool:
    """Whether an annotation spelling still means the type it looks like.

    A builtin (``str``, ``int``, ``list``, …) is canonical only while the
    module binds nothing of that name; a ``typing`` alias only when its single
    binding is an import that resolves into ``typing``.
    """

    found = bindings.get(name, [])
    if name in _TYPING_ANNOTATION_ALIASES:
        if len(found) != 1 or not isinstance(found[0], ast.alias):
            return False
        resolved = aliases.get(name, "")
        return resolved.rsplit(".", 1)[0] in {"typing", "typing_extensions"}
    return not found


def _kwarg(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _kwarg_string(call: ast.Call, name: str) -> str | None:
    value = _kwarg_literal(call, name)
    return value if isinstance(value, str) else None


def _kwarg_literal(call: ast.Call, name: str) -> Any:
    value = _kwarg(call, name)
    if value is None:
        return None
    return _literal(value)


def _literal(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return None


def _extract_path_argument(
    call: ast.Call,
    aliases: dict[str, str],
    names: set[str],
) -> str | None:
    for keyword in call.keywords:
        if keyword.arg in names and isinstance(keyword.value, ast.Constant):
            value = keyword.value.value
            if isinstance(value, str):
                return value
        if keyword.arg in {"spec_str", "spec_dict"}:
            path = _path_read_text_argument(keyword.value, aliases)
            if path:
                return path
    for arg in call.args:
        path = _path_read_text_argument(arg, aliases)
        if path:
            return path
    return None


def _literal_strings(node: ast.AST) -> list[str]:
    value = _literal(node)
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, tuple):
        return [item for item in value if isinstance(item, str)]
    return []


def _config_string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            if isinstance(item, str):
                items.append(item)
            elif isinstance(item, dict) and isinstance(item.get("path"), str):
                items.append(item["path"])
        return items
    if isinstance(value, dict) and isinstance(value.get("path"), str):
        return [value["path"]]
    return []


def _path_read_text_argument(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in {"read", "read_text"}:
            target = node.func.value
            if isinstance(target, ast.Call):
                name = _qualified_name(target.func, aliases)
                if name in {"Path", "pathlib.Path", "open"} and target.args:
                    value = _literal(target.args[0])
                    if isinstance(value, str):
                        return value
            elif isinstance(target, ast.Name):
                return None
    return None


def _args_to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, list):
        return {}
    args: dict[str, Any] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str):
            args[name] = item.get("value")
    return args


def _first_string_arg(args: dict[str, Any], names: set[str]) -> str | None:
    for name in names:
        value = args.get(name)
        if isinstance(value, str) and value:
            return value
    return None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, str):
        return [value]
    return []


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _unresolved_connection(limitations: list[str]) -> GoogleAdkToolsetConnection:
    """A connection argument that is present and not statically readable."""

    return GoogleAdkToolsetConnection(
        transport_status="unresolved",
        endpoint_status="unresolved",
        credential_status="unresolved",
        limitations=sorted(set(limitations)),
    )


def _endpoint_phrase(connection: GoogleAdkToolsetConnection | None) -> str | None:
    """How to name this connection's endpoint to a reviewer, or ``None``.

    A literal endpoint names itself. An environment reference names the
    variable, which is a thing a reviewer can look up — never a value, and
    never a claim about what the variable grants.
    """

    if connection is None:
        return None
    if connection.endpoint_status == "literal" and connection.endpoint:
        return connection.endpoint
    if connection.endpoint_status == "environment_reference" and connection.endpoint_env_ref:
        return f"the endpoint in {connection.endpoint_env_ref}"
    return None


def adk_mcp_inventory_warning(
    location: str,
    *,
    agent_name: str | None = None,
    endpoint: str | None = None,
) -> str:
    """Warning for an ADK ``McpToolset`` with no local MCP tool inventory.

    Names the binding the inventory is owed for, not only the file and line:
    the missing input belongs to *this agent's* connection, and a reviewer
    reading a repository with several toolsets could not tell which one from
    a line number alone (#538).
    """

    subject = ""
    if agent_name and endpoint:
        subject = f" (agent {agent_name!r} -> {endpoint})"
    elif agent_name:
        subject = f" (agent {agent_name!r})"
    elif endpoint:
        subject = f" ({endpoint})"
    return (
        f"Google ADK McpToolset at {location}{subject} has no static MCP tool "
        "inventory path."
    )


def _environment_reference(node: ast.AST, aliases: dict[str, str]) -> str | None:
    """The environment variable *name* ``node`` reads, or ``None``.

    Recognizes ``os.environ["X"]``, ``os.environ.get("X")`` and
    ``os.getenv("X")``, in every import spelling the alias table resolves.
    Only the name is ever produced: nothing here reads the process
    environment, and a variable called ``ADMIN_KEY`` establishes no privilege
    level — it is a reference a reviewer can look up, not a claim.
    """

    if isinstance(node, ast.Subscript):
        container = _qualified_name(node.value, aliases)
        if container in _ENVIRON_MAPPING_NAMES:
            key = _literal(node.slice)
            if isinstance(key, str) and key:
                return key
        return None
    if isinstance(node, ast.Call):
        getter = _qualified_name(node.func, aliases)
        if getter in _ENVIRON_GETTER_NAMES and node.args:
            key = _literal(node.args[0])
            if isinstance(key, str) and key:
                return key
    return None


def _dict_items(node: ast.AST) -> list[tuple[str, ast.AST]] | None:
    """``(literal key, value node)`` pairs of a dict literal, else ``None``.

    ``None`` also for a dict that carries ``**spread``: the spread can add or
    replace any key, so the literal pairs are not the whole mapping and
    reporting them as if they were would understate the surface.
    """

    if not isinstance(node, ast.Dict):
        return None
    items: list[tuple[str, ast.AST]] = []
    for key, value in zip(node.keys, node.values, strict=True):
        if key is None:
            return None
        name = _literal(key)
        if not isinstance(name, str):
            return None
        items.append((name, value))
    return items


def _read_credential_refs(
    call: ast.Call,
    aliases: dict[str, str],
    limitations: list[str],
    *,
    prefix: str = "",
) -> tuple[list[str], RemoteBindingStatus]:
    """What the connection's credential-bearing arguments carry.

    Returns ``("<where>=<what>"`` entries, status). ``where`` names the
    argument the entry sits in so a reviewer can open the line; ``what`` is the
    environment variable *name*, or a marker for a value that was read and
    withheld, or one that could not be read at all.

    Every entry is listed, not only the references. A hardcoded credential
    added beside an existing environment reference is a credential being
    *added*, and recording it only as a limitation left the carried summary and
    hash unchanged, so the delta never appeared (PR #540 review). The markers
    carry presence and completeness without carrying a single byte of the
    value.
    """

    refs: list[str] = []
    literal_credential = False
    unresolved = False
    for arg_name in MCP_CREDENTIAL_ARG_NAMES:
        expr = _kwarg(call, arg_name)
        if expr is None:
            continue
        where = f"{prefix}{arg_name}"
        items = _dict_items(expr)
        if items is None:
            unresolved = True
            limitations.append(LIMIT_DYNAMIC_CREDENTIAL_EXPRESSION)
            refs.append(f"{where}={CREDENTIAL_UNREADABLE_MARKER}")
            continue
        for key, value in items:
            env_ref = _environment_reference(value, aliases)
            if env_ref:
                refs.append(f"{where}.{key}={env_ref}")
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                # A literal under a non-credential header (``X-Client:
                # "shipgate"``) is ordinary metadata, not a secret.
                if is_credential_key(key):
                    literal_credential = True
                    limitations.append(LIMIT_LITERAL_CREDENTIAL_VALUE)
                    refs.append(f"{where}.{key}={CREDENTIAL_WITHHELD_MARKER}")
                continue
            unresolved = True
            limitations.append(LIMIT_DYNAMIC_CREDENTIAL_EXPRESSION)
            refs.append(f"{where}.{key}={CREDENTIAL_UNREADABLE_MARKER}")
    entries = sorted(set(refs))
    if not entries:
        return [], "absent"
    if literal_credential and not _has_environment_entry(entries):
        # Present, read, and deliberately withheld. The status is what makes
        # the hardcoded credential visible without publishing it.
        return entries, "redacted"
    if unresolved and not _has_environment_entry(entries):
        return entries, "unresolved"
    return entries, "environment_reference"


def _is_connection_params_call(call: ast.Call, aliases: dict[str, str]) -> bool:
    """Whether ``call`` constructs a recognized ADK connection-params object."""

    name = _qualified_name(call.func, aliases) or ""
    return name.rsplit(".", 1)[-1] in MCP_CONNECTION_TRANSPORTS


def _merge_credentials(
    outer: list[str],
    outer_status: RemoteBindingStatus,
    nested: list[str],
    nested_status: RemoteBindingStatus,
) -> tuple[list[str], RemoteBindingStatus]:
    """Combine an outer connection's credential entries with its nested ones.

    The status is the strongest claim either side supports: an established
    environment reference anywhere wins, then a withheld literal, then
    something unreadable. ``absent`` survives only when *both* sides are.
    """

    entries = sorted(set(outer) | set(nested))
    if not entries:
        return [], "absent"
    statuses = {outer_status, nested_status}
    for candidate in ("environment_reference", "redacted", "unresolved"):
        if candidate in statuses:
            return entries, candidate  # type: ignore[return-value]
    return entries, "absent"


def _has_environment_entry(entries: list[str]) -> bool:
    """Whether any entry names an environment variable rather than a marker."""

    return any(
        not entry.endswith(CREDENTIAL_WITHHELD_MARKER)
        and not entry.endswith(CREDENTIAL_UNREADABLE_MARKER)
        for entry in entries
    )


def _read_endpoint(
    call: ast.Call,
    aliases: dict[str, str],
    limitations: list[str],
) -> tuple[str | None, str | None, RemoteBindingStatus]:
    """``(endpoint, endpoint_env_ref, status)`` for one connection constructor."""

    expr = _kwarg(call, "url")
    if expr is None:
        return None, None, "absent"
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        endpoint, withheld = redact_url_credentials(expr.value)
        if withheld:
            limitations.append(LIMIT_ENDPOINT_CREDENTIALS_REDACTED)
        return endpoint, None, "literal"
    env_ref = _environment_reference(expr, aliases)
    if env_ref:
        return None, env_ref, "environment_reference"
    limitations.append(LIMIT_DYNAMIC_ENDPOINT_EXPRESSION)
    return None, None, "unresolved"


def _filter_status(call: ast.Call, filter_values: list[str]) -> RemoteBindingStatus:
    expr = _kwarg(call, "tool_filter")
    if expr is None:
        return "absent"
    if filter_values:
        return "literal"
    # Present and it produced no literal strings: an empty literal list is a
    # real (empty) filter; anything else — a callable, a name, a comprehension
    # — is not statically readable.
    if isinstance(expr, ast.List | ast.Tuple) and not expr.elts:
        return "literal"
    return "unresolved"


def _looks_like_openapi_toolset(name: str) -> bool:
    lower_name = name.lower()
    return name.split(".")[-1] == "OpenAPIToolset" or (
        "openapi" in lower_name and "toolset" in lower_name
    )


def _looks_like_mcp_toolset(name: str) -> bool:
    lower_name = name.lower()
    return name.split(".")[-1] in {"McpToolset", "MCPToolset"} or (
        "mcp" in lower_name and "toolset" in lower_name
    )


def _looks_like_local_artifact(value: str) -> bool:
    suffix = Path(value).suffix.lower()
    return suffix in {".json", ".jsonl", ".yaml", ".yml"}


def _short_tool_name(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def _is_injected_context_arg(
    arg: ast.arg, aliases: dict[str, str], *, is_receiver: bool
) -> bool:
    """Whether ADK supplies this argument itself instead of the model.

    Google ADK decides this by the parameter's *type* — a ``ToolContext`` and
    friends are filled in by the framework — and falls back to the
    ``tool_context`` name. Dropping every parameter merely *spelled* ``ctx`` or
    ``context`` deleted ordinary model-visible inputs from the schema, so
    ``def known(context: str, record_id: str)`` shipped a one-property schema
    and called it proven (#400 review). Only a statically verifiable injection
    is omitted now; anything else stays a parameter, where an unreadable
    annotation gets caught by the usual checks.
    """

    if is_receiver and arg.arg == "self":
        return True
    if arg.arg == ADK_CONTEXT_PARAMETER_NAME:
        return True
    if arg.annotation is None:
        return False
    return _qualified_name(arg.annotation, aliases) in ADK_CONTEXT_TYPE_NAMES


def _bound_args(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    aliases: dict[str, str],
) -> list[tuple[ast.arg, bool]]:
    """The arguments that become tool parameters, with their requiredness.

    One owner for the "which arguments count" question, so the schema
    (:func:`_parameters`) and the check on whether that schema is faithful
    (:func:`_function_surface_gaps`) can never disagree about which arguments
    they are talking about.
    """

    bound: list[tuple[ast.arg, bool]] = []
    positional_args = [*node.args.posonlyargs, *node.args.args]
    positional_defaults: list[ast.expr | None] = [
        None for _ in range(len(positional_args) - len(node.args.defaults))
    ]
    positional_defaults.extend(node.args.defaults)
    for index, (arg, default) in enumerate(
        zip(positional_args, positional_defaults, strict=True)
    ):
        if _is_injected_context_arg(arg, aliases, is_receiver=index == 0):
            continue
        bound.append((arg, default is None))
    for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True):
        if _is_injected_context_arg(arg, aliases, is_receiver=False):
            continue
        bound.append((arg, default is None))
    return bound


def _parameters(
    node: ast.FunctionDef | ast.AsyncFunctionDef, aliases: dict[str, str]
) -> list[ToolParameter]:
    return [
        _parameter(arg, required=required)
        for arg, required in _bound_args(node, aliases)
    ]


#: Statements that bind a name at module scope in a way the adapter can point
#: at. A binding reached through anything else — an ``if``/``try``/``for`` body,
#: a ``with`` block — is conditional or order-dependent, which is exactly what
#: this check exists to refuse.
#: How many factory calls deep a tool factory's return is followed (#865).
MAX_FACTORY_DEPTH = 4


def _step(module: PythonModule, node: ast.FunctionDef | ast.AsyncFunctionDef, binding: str) -> dict[str, Any]:
    """One hop of a resolution's evidence, in the resolver's shape."""

    return {
        "path": module.ref,
        "line": node.lineno,
        "name": node.name,
        "sha256": module.sha256,
        "binding": binding,
    }


def _unconditional(node: ast.stmt, scopes: ScopeIndex) -> bool:
    """Whether a statement runs whenever its function (or module) body does."""

    parent = scopes.parents.get(node)
    return isinstance(parent, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef) and node in parent.body


def _own_returns(function: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[list[ast.Return], bool]:
    """The ``return`` statements of ``function`` itself, and whether it yields."""

    returns: list[ast.Return] = []
    yields = False
    stack: list[ast.AST] = list(function.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            continue
        if isinstance(node, ast.Return):
            returns.append(node)
        elif isinstance(node, ast.Yield | ast.YieldFrom):
            yields = True
        stack.extend(ast.iter_child_nodes(node))
    return sorted(returns, key=lambda item: (item.lineno, item.col_offset)), yields


def _changed_at(
    function: ast.AST,
    name: str,
    parents: dict[ast.AST, ast.AST],
    *,
    bound: ast.AST,
    returned: ast.Return | None = None,
    wrapper: ast.Call | None = None,
    strict: bool = True,
    allowed: Callable[[ast.Name], bool] | None = None,
) -> int | None:
    """The line where ``name`` — a factory's function or the tool it returns —
    may be changed, or None.

    Any store or deletion through it (``inner.__name__ = ...``,
    ``inner.__dict__[...]``), a rebinding, and ``setattr``/``delattr``/
    ``update_wrapper`` on it always count. ``strict``: so does any other use —
    handing it to a call, a container or another name — except being the
    ``return`` value or the function ``wrapper`` wraps (#865).
    """

    for node in ast.walk(function):
        if not isinstance(node, ast.Name) or node.id != name or node is bound:
            continue
        line = getattr(node, "lineno", 0)
        if not isinstance(node.ctx, ast.Load):
            return line
        parent = parents.get(node)
        if isinstance(parent, ast.Attribute | ast.Subscript) and parent.value is node:
            outer = parent
            while isinstance(parents.get(outer), ast.Attribute | ast.Subscript):
                outer = parents[outer]
            if not isinstance(getattr(outer, "ctx", None), ast.Load):
                return line
        if isinstance(parent, ast.Call) and node in parent.args:
            callee = reference_spelling(parent.func) or ""
            if callee.rsplit(".", 1)[-1] in {"setattr", "delattr", "update_wrapper"}:
                return line
        if not strict or (isinstance(parent, ast.Call) and parent.func is node):
            # Calling it changes nothing about it.
            continue
        if isinstance(parent, ast.Attribute) and parent.value is node:
            # ``tool.name`` read — logged, formatted — leaves it as it is; a
            # method call on it may not.
            outer = parent
            while isinstance(parents.get(outer), ast.Attribute | ast.Subscript) and getattr(parents[outer], "value", None) is outer:
                outer = parents[outer]
            holder = parents.get(outer)
            if isinstance(holder, ast.Call) and holder.func is outer:
                # A method call on it may change it.
                return line
            handed = (
                isinstance(holder, ast.keyword)
                or (isinstance(holder, ast.Call) and outer in holder.args)
                # ``f = tool.func``, ``[tool.func]``: another name for a part of it.
                or isinstance(holder, ast.Assign | ast.AnnAssign | ast.NamedExpr | ast.List | ast.Tuple | ast.Set | ast.Dict)
            )
            if handed and not (
                isinstance(outer, ast.Attribute) and outer.value is node and outer.attr in _DESCRIPTIVE_ATTRIBUTES
            ):
                # ``rename_fn(tool.func)``: a part of it handed on (#865 review).
                return line
            continue
        if isinstance(parent, ast.Return) and parent is returned:
            continue
        if wrapper is not None and parent is wrapper:
            continue
        if isinstance(parent, ast.keyword) and parents.get(parent) is wrapper and wrapper is not None:
            continue
        if allowed is not None and allowed(node):
            continue
        return line
    return None


def _code_dump(function: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """A function's code, without docstrings (description, not behaviour)."""

    copy = copy_module.deepcopy(function)
    for node in ast.walk(copy):
        body = getattr(node, "body", None)
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return _dump(copy)


#: A tool's attributes that are text: handing one on changes nothing.
_DESCRIPTIVE_ATTRIBUTES = frozenset({"name", "description", "__name__", "__doc__", "__qualname__"})


def _returns_tool(function: ast.FunctionDef | ast.AsyncFunctionDef, aliases: dict[str, str]) -> bool:
    """Whether a factory's own ``return`` is (or may be) a tool object rather
    than a plain function: a wrapper call, anything but a name, or a name its
    body binds to a call (#865 review)."""

    returns, _ = _own_returns(function)
    for node in returns:
        value = node.value
        if value is None:
            continue
        if isinstance(value, ast.Call) and _qualified_name(value.func, aliases) in FUNCTION_TOOL_NAMES | LONG_RUNNING_TOOL_NAMES:
            return True
        if not isinstance(value, ast.Name):
            return True
        # ``tool = FunctionTool(inner)`` / ``tool: FunctionTool = ...``: its own
        # bindings only, not a nested function's.
        stack: list[ast.AST] = list(function.body)
        while stack:
            item = stack.pop()
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
                continue
            bound = (
                item.targets
                if isinstance(item, ast.Assign)
                else [item.target]
                if isinstance(item, ast.AnnAssign | ast.NamedExpr)
                else []
            )
            if any(isinstance(target, ast.Name) and target.id == value.id for target in bound) and isinstance(
                getattr(item, "value", None), ast.Call
            ):
                return True
            stack.extend(ast.iter_child_nodes(item))
    return False


def _dump(node: ast.AST) -> str:
    """``ast.dump`` that reads the same on every supported Python."""

    return ast.dump(node, include_attributes=False, **({"show_empty": True} if sys.version_info >= (3, 13) else {}))


def _holds_mutable(node: ast.AST) -> bool:
    """A list, dict or set anywhere in a literal: ``({"readonly": True},)``."""

    return any(isinstance(item, ast.List | ast.Dict | ast.Set) for item in ast.walk(node))


def _written_out(node: ast.AST) -> bool:
    """A value written out in full: a constant, or a container of them."""

    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd | ast.Not):
        return _written_out(node.operand)
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        return all(_written_out(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(key is not None and _written_out(key) for key in node.keys) and all(_written_out(item) for item in node.values)
    return False


def _attribute_changed(
    function: ast.AST,
    spelling: str,
    parents: dict[ast.AST, ast.AST],
    *,
    returned: ast.Return | None,
    wrapper: ast.Call | None,
) -> int | None:
    """``_changed_at`` for a module attribute (``impl.search``): a store
    through it, or handing it to a call other than the wrapper (#865 review)."""

    for node in ast.walk(function):
        if not isinstance(node, ast.Attribute) or reference_spelling(node) != spelling:
            continue
        parent = parents.get(node)
        if (wrapper is not None and (parent is wrapper or (isinstance(parent, ast.keyword) and parents.get(parent) is wrapper))) or (
            isinstance(parent, ast.Return) and parent is returned
        ):
            continue
        outer: ast.AST = node
        while isinstance(parents.get(outer), ast.Attribute | ast.Subscript) and getattr(parents[outer], "value", None) is outer:
            outer = parents[outer]
        if not isinstance(getattr(outer, "ctx", None), ast.Load):
            return node.lineno
        holder = parents.get(outer)
        if isinstance(holder, ast.keyword) or (isinstance(holder, ast.Call) and outer in holder.args):
            return node.lineno
        if isinstance(holder, ast.Assign | ast.AnnAssign | ast.NamedExpr | ast.List | ast.Tuple | ast.Set | ast.Dict):
            # ``f = impl.search``: another name for it (#865 review).
            return node.lineno
    return None


def _in_local_list(
    node: ast.AST, parents: dict[ast.AST, ast.AST], is_agent_call: Callable[[ast.Call], bool]
) -> bool:
    """A member of ``tools = [a, b]`` whose every use is an agent's ``tools=``:
    the list the tools-list reader follows, and nothing else (#865 review)."""

    holder = parents.get(node)
    statement = parents.get(holder) if isinstance(holder, ast.List | ast.Tuple) else None
    if not isinstance(statement, ast.Assign | ast.AnnAssign) or statement.value is not holder:
        return False
    target = statement.targets[0] if isinstance(statement, ast.Assign) and len(statement.targets) == 1 else getattr(statement, "target", None)
    function = parents.get(statement)
    while function is not None and not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef | ast.Module):
        function = parents.get(function)
    if not isinstance(target, ast.Name) or function is None:
        return False
    for use in ast.walk(function):
        if not isinstance(use, ast.Name) or use.id != target.id or use is target:
            continue
        holder = parents.get(use)
        call = parents.get(holder)
        if isinstance(holder, ast.keyword) and holder.arg == "tools" and isinstance(call, ast.Call) and is_agent_call(call):
            continue
        # What the tools-list reader follows: additions to the list.
        if isinstance(holder, ast.AugAssign) and holder.target is use:
            continue
        if (
            isinstance(holder, ast.Attribute)
            and holder.attr in {"append", "extend", "insert"}
            and isinstance(call, ast.Call)
            and call.func is holder
            and isinstance(parents.get(call), ast.Expr)
        ):
            continue
        return False
    return True


def _in_tools_argument(
    node: ast.AST, parents: dict[ast.AST, ast.AST], is_agent_call: Callable[[ast.Call], bool]
) -> bool:
    """Whether ``node`` is a member of an agent call's ``tools=`` list."""

    parent = parents.get(node)
    if isinstance(parent, ast.List | ast.Tuple):
        parent = parents.get(parent)
    if not isinstance(parent, ast.keyword) or parent.arg != "tools":
        return False
    call = parents.get(parent)
    return isinstance(call, ast.Call) and is_agent_call(call)


def _top_statement(node: ast.AST, function: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST | None:
    """The statement directly in ``function``'s body that holds ``node``."""

    current: ast.AST | None = node
    while current is not None and parents.get(current) is not function:
        current = parents.get(current)
    return current


def _at_risk(listed: ListResolution) -> bool:
    """Whether a list's reading depends on a binding another module could change.

    Following a name did, even to an empty list, and so did reading another
    module's list; a literal spread into a literal (``[a, *[b]]``,
    ``[a] + [b]``) did not.
    """

    return bool(listed.unresolved) or listed.followed


_TOP_LEVEL_BINDING_STATEMENTS = (
    ast.Assign,
    ast.AnnAssign,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Import,
    ast.ImportFrom,
)
#: Builtins that name an attribute with a string instead of a dotted access.
#: Matched on the trailing name so ``builtins.setattr`` and an aliased import
#: of the same builtin both count.
_REFLECTIVE_ATTRIBUTE_BUILTINS = {"getattr", "setattr", "delattr"}


def _is_reflective_tools_access(node: ast.AST, aliases: dict[str, str]) -> bool:
    """Whether ``node`` reaches an object's ``tools`` attribute by name.

    Three spellings, all of which produced the same runtime mutation while
    containing no ``Attribute`` node named ``tools``:
    ``getattr(agent, "tools")`` and its ``setattr``/``delattr`` siblings,
    ``vars(agent)["tools"]``, and ``agent.__dict__["tools"]``.

    The builtin is matched through the import alias table as well as its bare
    spelling, so ``from builtins import getattr as read_attr`` is recognised —
    it calls the real builtin, and only the local name changed (#400 review).
    The dictionary forms are matched narrowly, on ``vars(...)`` and
    ``__dict__`` specifically, rather than on any ``["tools"]`` subscript: a
    config dictionary with a ``"tools"`` key is ordinary and is not a mutation.
    """

    if isinstance(node, ast.Subscript):
        key = node.slice
        if not (isinstance(key, ast.Constant) and key.value == "tools"):
            return False
        target = node.value
        if isinstance(target, ast.Attribute) and target.attr == "__dict__":
            return True
        return isinstance(target, ast.Call) and _called_builtin(target, aliases) == "vars"
    if isinstance(node, ast.Call) and len(node.args) >= 2:
        if _called_builtin(node, aliases) not in _REFLECTIVE_ATTRIBUTE_BUILTINS:
            return False
        attribute = node.args[1]
        return isinstance(attribute, ast.Constant) and attribute.value == "tools"
    return False


def _called_builtin(call: ast.Call, aliases: dict[str, str]) -> str | None:
    """The builtin ``call`` invokes, seen through imports and dotted access."""

    qualified = _qualified_name(call.func, aliases)
    if qualified:
        return qualified.rsplit(".", 1)[-1]
    return None


def _name_binding_occurrences(tree: ast.Module) -> dict[str, list[ast.AST]]:
    """Every node in the module that binds a name, keyed by the name.

    Counting binding *occurrences* rather than definitions is the point.
    ``tools=[helper]`` is resolved through a flat ``name -> FunctionDef`` map
    built by walking the whole module, which is what lets the adapter name a
    tool at all — it is not what lets it *prove* one. The same map answers with
    a definition nested inside a factory, a method lifted out of a class body,
    whichever of two conditional definitions the walk saw last, or a definition
    whose name a parameter, class, import, or later assignment shadows.

    Every Python binding form is collected, not just ``def`` and ``=``:
    parameters are ``ast.arg``, classes bind through ``ClassDef.name``,
    ``except E as name`` and ``case X() as name`` have their own shapes, and
    ``global``/``nonlocal`` declare that a name is rebound out of view. Missing
    one of those was how a parameter named after a module function slipped
    through as a proven definition (PR #400 review).
    """

    bindings: dict[str, list[ast.AST]] = {}

    def record(name: str | None, node: ast.AST) -> None:
        if name:
            bindings.setdefault(name, []).append(node)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            record(node.name, node)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
            record(node.id, node)
        elif isinstance(node, ast.arg):
            record(node.arg, node)
        elif isinstance(node, ast.alias):
            record(node.asname or node.name.split(".", 1)[0], node)
        elif isinstance(node, ast.ExceptHandler):
            record(node.name, node)
        elif isinstance(node, ast.MatchAs | ast.MatchStar):
            record(node.name, node)
        elif isinstance(node, ast.MatchMapping):
            record(node.rest, node)
        elif isinstance(node, ast.Global | ast.Nonlocal):
            for name in node.names:
                record(name, node)
    return bindings


#: Annotations :func:`_annotation_json_type` can name a JSON type for. Bare
#: ``list``/``dict`` are included: ``{"type": "array"}`` omits the element
#: schema but does not misstate the value, which is a different thing from a
#: guess.
_SCALAR_ANNOTATION_TYPES = {
    "str": "string",
    "int": "number",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "List": "array",
    "dict": "object",
    "Dict": "object",
}
#: The subset of the above that has to arrive through ``typing``; the rest are
#: builtins, which are canonical exactly while the module binds nothing of that
#: name. See ``_PythonAdkExtractor._name_is_canonical``.
_TYPING_ANNOTATION_ALIASES = {"List", "Dict"}


def _annotation_json_type(
    node: ast.AST | None, name_is_canonical: Callable[[str], bool]
) -> str | None:
    """The JSON type this annotation really denotes, or None if it denotes none.

    Reads the annotation as a tree rather than as the unparsed string, so
    ``set[str]``, ``tuple[int, str]``, ``int | None``, ``Optional[int]``,
    ``Literal[...]``, a string forward reference, and any custom or Pydantic
    class all answer None instead of silently becoming a scalar.

    ``name_is_canonical`` decides whether a spelling still refers to the
    builtin or ``typing`` alias it looks like. Trusting the spelling alone let
    ``from domain import Account as str`` describe a model as a string, on a
    tool marked proven (#400 review).
    """

    if isinstance(node, ast.Name):
        if not name_is_canonical(node.id):
            return None
        return _SCALAR_ANNOTATION_TYPES.get(node.id)
    if isinstance(node, ast.Subscript):
        base = node.value
        if not isinstance(base, ast.Name) or not name_is_canonical(base.id):
            # A dotted base such as ``typing.List`` is left to the emitter
            # comparison below, which already rejects it: the string match in
            # ``_json_schema_type`` misses the module prefix.
            return None
        if base.id in {"list", "List"}:
            return (
                "array"
                if _annotation_json_type(node.slice, name_is_canonical)
                else None
            )
        if base.id in {"dict", "Dict"}:
            if isinstance(node.slice, ast.Tuple) and len(node.slice.elts) == 2:
                key, value = node.slice.elts
                keyed_by_string = (
                    isinstance(key, ast.Name)
                    and key.id == "str"
                    and name_is_canonical("str")
                )
                if keyed_by_string and _annotation_json_type(
                    value, name_is_canonical
                ):
                    return "object"
            return None
    return None


def _annotation_is_faithful(
    node: ast.AST | None, name_is_canonical: Callable[[str], bool]
) -> bool:
    """Whether the schema :func:`_json_schema_type` emits matches the annotation.

    An annotation is not by itself evidence that the emitted schema is right.
    ``_json_schema_type`` reads the *unparsed string* and falls back to
    ``"string"`` for everything it does not recognise, so ``set[str]``,
    ``int | None``, a Pydantic model, and even ``typing.List[str]`` (spelled
    with the module prefix the string match misses) all ship as
    ``{"type": "string"}``.

    Rather than keep a second vocabulary in sync with the emitter's, this asks
    the emitter what it would produce and compares it to what the annotation
    denotes. Any spelling the emitter mishandles is unfaithful by construction,
    including one added later.
    """

    expected = _annotation_json_type(node, name_is_canonical)
    if expected is None:
        return False
    return _json_schema_type(_annotation_to_string(node)) == expected


def _function_surface_gaps(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    aliases: dict[str, str],
    name_is_canonical: Callable[[str], bool],
) -> list[str]:
    """Reasons this one function's callable interface was not fully read.

    Separate from the module-scoped reasons: a file can resolve every tool
    expression it contains and still hold a function whose real signature the
    AST does not give up.

    * A decorator replaces the callable ADK introspects, so the definition's
      parameters are not necessarily the tool's parameters.
    * ``*args`` / ``**kwargs`` are dropped by :func:`_bound_args` — the schema
      would understate an open-ended surface rather than describe it.
    * An unannotated parameter is typed ``string`` by
      :func:`_json_schema_type`'s fallback. That is a guess presented as a
      schema, which is exactly what a high-confidence extraction may not do.
    * An annotation the emitter cannot represent is the same guess with better
      manners — ``set[str]`` and ``int | None`` also ship as ``string``. The
      return annotation is checked too, because ``output_schema`` is built from
      the same fallback; an *absent* return annotation is an honest omission
      (``output_schema`` stays ``{}``) and is not a gap.
    """

    gaps: list[str] = []
    if node.decorator_list:
        gaps.append(SURFACE_GAP_DECORATED_FUNCTION)
    if node.args.vararg is not None or node.args.kwarg is not None:
        gaps.append(SURFACE_GAP_VARIADIC_PARAMETERS)
    annotations = [arg.annotation for arg, _ in _bound_args(node, aliases)]
    if any(annotation is None for annotation in annotations):
        gaps.append(SURFACE_GAP_UNTYPED_PARAMETER)
    declared = [
        annotation for annotation in annotations if annotation is not None
    ]
    if node.returns is not None:
        declared.append(node.returns)
    if not all(
        _annotation_is_faithful(annotation, name_is_canonical)
        for annotation in declared
    ):
        gaps.append(SURFACE_GAP_UNREPRESENTABLE_ANNOTATION)
    return sorted(gaps)


def _parameter(arg: ast.arg, *, required: bool) -> ToolParameter:
    return ToolParameter(
        name=arg.arg,
        type=_annotation_to_string(arg.annotation),
        required=required,
    )


def _annotation_to_string(annotation: ast.AST | None) -> str | None:
    if annotation is None:
        return None
    return ast.unparse(annotation)


def _json_schema_type(annotation: str | None) -> str:
    """The JSON type this adapter emits for an annotation, as a string match.

    ``List[str]`` and ``Dict[str, int]`` are recognised alongside their builtin
    spellings. Without that, ``from typing import List`` emitted ``string`` for
    a list — which :func:`_annotation_is_faithful` correctly refused to certify,
    so the tool was held at ``medium`` for what is really an emitter gap rather
    than anything about the user's code.
    """

    if annotation in {"int", "float"}:
        return "number"
    if annotation == "bool":
        return "boolean"
    text = annotation or ""
    if annotation in {"list", "List"} or text.startswith(("list[", "List[")):
        return "array"
    if annotation in {"dict", "Dict"} or text.startswith(("dict[", "Dict[")):
        return "object"
    return "string"


def _display_path(path: Path, base_dir: Path) -> str:
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


class GoogleADKAdapter:
    """``ToolSourceAdapter`` wrapping :func:`load_google_adk_artifacts`.

    Framework-scoped. The dispatcher invokes ``load()`` once per scan
    when either a ``tool_sources`` entry of type ``google_adk`` or the
    top-level ``manifest.google_adk`` section is configured.
    """

    source_type: ClassVar[str] = "google_adk"
    scope: ClassVar[Literal["per_source", "per_scan"]] = "per_scan"
    artifact_class: ClassVar[type | None] = GoogleAdkArtifacts

    coverage: ClassVar[SourceCoverage] = SourceCoverage(
        adapter="google_adk",
        label="Google ADK",
        reads=(
            "ADK Python modules parsed with `ast`, agent config files, and any "
            "reviewed inventory `google_adk.tool_inventories[]` declares. "
            "Completeness is settled once per module, after the whole file is "
            "walked."
        ),
        manifest_section="google_adk",
        cells=(
            BoundaryCell(
                shape="export_artifact",
                variant="reviewed inventory",
                status="extracted",
                reads=(
                    "A reviewed tool inventory in MCP export form, read as a "
                    "published contract."
                ),
                emits=("google_adk_inventory",),
                ceiling="high",
            ),
            BoundaryCell(
                shape="export_artifact",
                variant="resolved toolset",
                status="extracted",
                reads=(
                    "`McpToolset(...)` / `OpenAPIToolset(...)` whose arguments name a "
                    "committed export or spec in the workspace. Those actions are "
                    "read by the MCP and OpenAPI inputs, then lowered to this "
                    "module's ceiling if anything else in the module was unresolved."
                ),
                emits=("mcp", "openapi"),
                ceiling="high",
            ),
            BoundaryCell(
                shape="export_artifact",
                variant="wildcard inventory",
                status="extracted",
                reads=(
                    "An inventory that declares `wildcard: true` instead of "
                    "listing tools. It is a reviewed file and still names "
                    "nothing, so it loads at `high` and proves no surface — a "
                    "reviewed statement that says nothing is not evidence."
                ),
                emits=("google_adk_inventory",),
                ceiling="high",
                surface_flags=("wildcard_tools",),
            ),
            BoundaryCell(
                shape="literal_registration",
                variant="Python module",
                status="extracted",
                reads=(
                    "A module-level `def` bound by `Agent(tools=[...])` — defined in "
                    "the entrypoint, or reached through a repository-local import "
                    "inside the read directory — in a module where every tool "
                    "expression, agent keyword, and imported symbol resolved. This is "
                    "the only source-code route in any input that reaches `high`."
                ),
                emits=("google_adk_function",),
                ceiling="high",
                surface="enumerated",
            ),
            BoundaryCell(
                shape="literal_registration",
                variant="agent config",
                status="extracted",
                reads=(
                    "A tool named in an agent config file. The name is read; nothing "
                    "local defines the schema, so the action is a reference only."
                ),
                emits=("google_adk_config",),
                ceiling="low",
                surface="partial",
            ),
            BoundaryCell(
                shape="factory",
                variant="module function",
                status="extracted",
                reads=(
                    "A toolset or wrapper call whose arguments do not name a file in "
                    "the workspace records `dynamic_toolset`. The actions already "
                    "read stay in the catalog; the whole module drops to `medium`, "
                    "because a tool set this file could not prove is a fact about "
                    "the file, not about the tool visited first. An unenumerable "
                    "`McpToolset` still has its *connection* read: the literal "
                    "endpoint, the `os.environ` names its credentials are read "
                    "from, the transport and the literal `tool_filter` are "
                    "preserved and compared base-vs-head, so a changed endpoint or "
                    "credential reference is named to a reviewer even while the "
                    "tools behind it stay unproven."
                ),
                emits=("google_adk_function",),
                ceiling="medium",
                surface="partial",
                raises=("SHIP-ADK-DYNAMIC-TOOLSET-NOT-ENUMERABLE",),
            ),
            BoundaryCell(
                shape="factory",
                variant="resolved toolset actions in the same module",
                status="extracted",
                reads=(
                    "The actions a resolved `McpToolset` / `OpenAPIToolset` "
                    "contributed are lowered with the rest of the module. Their "
                    "own schemas stay trustworthy so they are only ever "
                    "lowered, never raised — but a module that cannot prove its "
                    "tool set does not prove theirs either."
                ),
                emits=("mcp", "openapi"),
                ceiling="medium",
            ),
            BoundaryCell(
                shape="dynamic_construction",
                variant="module function",
                status="extracted",
                reads=(
                    "An unresolved tools expression, `Agent(**config)`, a rebound "
                    "tool variable, a star-import shadow, or any extractor warning "
                    "the module could not classify records a surface gap and caps "
                    "the module at `medium`."
                ),
                emits=("google_adk_function",),
                ceiling="medium",
                surface="partial",
            ),
            BoundaryCell(
                shape="dynamic_construction",
                variant="resolved toolset actions in the same module",
                status="extracted",
                reads=(
                    "The actions a resolved `McpToolset` / `OpenAPIToolset` "
                    "contributed are lowered with the rest of the module. Their "
                    "own schemas stay trustworthy so they are only ever "
                    "lowered, never raised — but a module that cannot prove its "
                    "tool set does not prove theirs either."
                ),
                emits=("mcp", "openapi"),
                ceiling="medium",
            ),
        ),
    )

    def load(
        self,
        source: ToolSourceConfig | None,
        base_dir: Path,
        manifest: AgentsShipgateManifest,
    ) -> LoadedAdapterResult:
        loaded_sources, artifacts = load_google_adk_artifacts(manifest, base_dir)
        return LoadedAdapterResult(tool_sources=loaded_sources, artifact=artifacts)
