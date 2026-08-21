from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

from agents_shipgate.core.artifact_models import (
    GoogleAdkArtifacts,
    GoogleAdkToolset,
)
from agents_shipgate.core.domain import (
    SURFACE_ENUMERATED,
    SURFACE_PARTIAL,
    AgentBindingObservation,
    AuthInfo,
    LoadedToolSource,
    Tool,
    ToolParameter,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.source_warnings import adk_unresolved_tool_warning
from agents_shipgate.inputs.common import (
    load_structured_file,
    load_text_file,
    resolve_input_path,
    stable_tool_id,
)
from agents_shipgate.inputs.mcp import load_mcp_tools
from agents_shipgate.inputs.openapi import load_openapi_tools
from agents_shipgate.inputs.protocol import LoadedAdapterResult
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


def load_google_adk_artifacts(
    manifest: AgentsShipgateManifest,
    base_dir: Path,
) -> tuple[list[LoadedToolSource], GoogleAdkArtifacts | None]:
    source_refs = [
        source for source in manifest.tool_sources if source.type == "google_adk"
    ]
    config = manifest.google_adk
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
    try:
        tree = ast.parse(load_text_file(path), filename=str(path))
    except SyntaxError as exc:
        raise InputParseError(f"Unable to parse Google ADK Python entrypoint {path}: {exc.msg}") from exc
    artifacts.python_entrypoints.append(_display_path(path, base_dir))
    extractor = _PythonAdkExtractor(tree, source_id, source_ref, path.parent, base_dir, artifacts)
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
    )
    artifacts.toolsets.append(toolset)
    if not inventory_path:
        artifacts.warnings.append(
            f"Google ADK McpToolset at {location} has no static MCP tool inventory path."
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

    def bind(self, tool_name: str) -> bool:
        """Add one tool to this agent; return False if it was already bound."""

        if tool_name in self.tool_names:
            return False
        self.tool_names.append(tool_name)
        return True


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
    ) -> None:
        self.tree = tree
        self.source_id = source_id
        self.source_ref = source_ref
        self.entrypoint_dir = entrypoint_dir
        self.base_dir = base_dir
        self.artifacts = artifacts
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
        # Tool names produced by one toolset construction, keyed by the AST
        # call node. A toolset assigned to a variable and shared between
        # agents is loaded once, not once per agent.
        self.toolset_tool_names: dict[int, list[str]] = {}
        self.agent_bindings: dict[str, _AdkAgentBinding] = {}
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
        self.parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        self.agent_names_by_variable = self._agent_names_by_variable()

    def extract(self) -> list[LoadedToolSource]:
        tools: list[Tool] = []
        loaded_sources: list[LoadedToolSource] = []
        warnings_before = len(self.artifacts.warnings)
        self._record_eval_references()
        self._record_star_imports()
        self._record_mutable_tool_bindings()
        for target_name, call in self.agent_call_list:
            agent_name = _kwarg_string(call, "name") or target_name or "adk_agent"
            # The call is only ADK's ``Agent`` while the name still refers to
            # the import it was resolved through.
            self._require_proven_framework_symbol(call)
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
            self._record_agent_callbacks_plugins_subagents(call, agent_name)
            if not isinstance(tools_expr, (ast.List, ast.Tuple)):
                if tools_expr is not None:
                    self._surface_warning(
                        f"Google ADK agent {agent_name!r} uses a dynamic tools expression.",
                        SURFACE_GAP_DYNAMIC_TOOLS,
                    )
                    self.artifacts.toolsets.append(
                        GoogleAdkToolset(
                            kind="dynamic",
                            source_id=self.source_id,
                            source_ref=f"{self.source_ref}:{call.lineno}",
                            agent_name=agent_name,
                            dynamic=True,
                        )
                    )
                continue
            binding = self._binding_for(agent_name, call)
            for item in tools_expr.elts:
                loaded_sources.extend(
                    self._extract_tool_expr(item, tools, agent_name, binding)
                )
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

        root = call.func
        while isinstance(root, ast.Attribute):
            root = root.value
        if not isinstance(root, ast.Name):
            return
        bindings = self.name_bindings.get(root.id, [])
        if len(bindings) != 1 or not isinstance(bindings[0], ast.alias):
            self._note_surface_gap(SURFACE_GAP_SHADOWED_FRAMEWORK_SYMBOL)

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

    def _name_is_proven(self, name: str) -> bool:
        """Whether ``name`` unambiguously refers to what the flat maps say.

        True only when the module binds the name exactly once, at module scope,
        through a statement that is a direct child of the module body. A second
        binding of any kind — a parameter, a class, an import, a later
        assignment, a ``global`` declaration — means the resolution is a guess
        about which one was in effect, and a guess is not a proof.
        """

        bindings = self.name_bindings.get(name, [])
        if len(bindings) != 1:
            return False
        binding = bindings[0]
        if self._scope_of(binding) is not self.tree:
            return False
        return isinstance(self._top_level_statement(binding), _TOP_LEVEL_BINDING_STATEMENTS)

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

        bindings = self.name_bindings.get(name, [])
        if name in _TYPING_ANNOTATION_ALIASES:
            if len(bindings) != 1 or not isinstance(bindings[0], ast.alias):
                return False
            resolved = self.aliases.get(name, "")
            return resolved.rsplit(".", 1)[0] in {"typing", "typing_extensions"}
        return not bindings

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
        for tool in self.canonical_function_tools.values():
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

    def _sub_agent_name(self, variable: str, call: ast.Call) -> str | None:
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
            )
            for binding in self.agent_bindings.values()
            if binding.tool_names
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

    def _extract_tool_expr(
        self,
        expr: ast.AST,
        tools: list[Tool],
        agent_name: str,
        binding: _AdkAgentBinding,
    ) -> list[LoadedToolSource]:
        if isinstance(expr, ast.Name):
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
                self._surface_warning(
                    adk_unresolved_tool_warning(agent_name, expr.id),
                    SURFACE_GAP_UNRESOLVED_REFERENCE,
                )
            return []
        if isinstance(expr, ast.Call):
            call_name = _qualified_name(expr.func, self.aliases)
            if call_name in FUNCTION_TOOL_NAMES | LONG_RUNNING_TOOL_NAMES:
                self._require_proven_framework_symbol(expr)
                func_name = _call_func_name(expr)
                if func_name and func_name in self.functions:
                    self._bind_function_tool(
                        self.functions[func_name],
                        tools,
                        agent_name,
                        binding,
                        call_name in LONG_RUNNING_TOOL_NAMES,
                    )
                else:
                    # A recognised wrapper whose ``func`` this module does not
                    # define: an imported function, an attribute, a lambda, or
                    # no ``func`` at all. The wrapper is a real tool the agent
                    # can call and nothing else records it, so returning
                    # silently here reported a strictly smaller tool surface
                    # than the agent has — and called it proven (PR #400
                    # review).
                    self._surface_warning(
                        f"Google ADK agent {agent_name!r} wraps a tool whose function "
                        f"{func_name or '<unspecified>'!r} is not defined in this module.",
                        SURFACE_GAP_UNRESOLVED_WRAPPER,
                    )
                return []
            if call_name in OPENAPI_TOOLSET_NAMES | MCP_TOOLSET_NAMES:
                return self._extract_toolset_call(expr, agent_name, binding)
        self._surface_warning(
            f"Google ADK agent {agent_name!r} has a tool expression that could not be statically resolved.",
            SURFACE_GAP_UNRESOLVED_EXPRESSION,
        )
        return []

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
        func_name = wrapper.get("func_name")
        if isinstance(func_name, str) and func_name in self.functions:
            self._bind_function_tool(
                self.functions[func_name],
                tools,
                agent_name,
                binding,
                bool(wrapper.get("long_running")),
            )
            return
        self._surface_warning(
            f"Google ADK tool wrapper {wrapper_name!r} has no statically resolvable function.",
            SURFACE_GAP_UNRESOLVED_WRAPPER,
        )

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
        elif long_running != (tool.annotations.get("long_running") is True):
            # The same function wrapped as both FunctionTool and
            # LongRunningFunctionTool is a contradictory declaration about one
            # action. Keep the stricter contract and route it to review rather
            # than letting binding order decide.
            self._surface_warning(
                f"Google ADK function {node.name!r} is bound as both a long-running "
                "and a standard function tool; review its operation contract.",
                SURFACE_GAP_CONFLICTING_CONTRACT,
            )
            if long_running:
                tool.annotations["long_running"] = True
                self.artifacts.long_running_tools.append(
                    self._function_tool_payload(tool, agent_name)
                )
        if binding.bind(tool.name):
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
            return []
        self._require_proven_framework_symbol(call)
        call_name = _qualified_name(call.func, self.aliases)
        if call_name in OPENAPI_TOOLSET_NAMES:
            loaded_sources = self._extract_openapi_toolset(call, agent_name)
        else:
            loaded_sources = self._extract_mcp_toolset(call, agent_name)
        tool_names = [
            tool.name for loaded in loaded_sources for tool in loaded.tools
        ]
        self.toolset_tool_names[id(call)] = tool_names
        self._bind_toolset_tools(tool_names, agent_name, binding)
        return loaded_sources

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
    ) -> Tool:
        parameters = _parameters(node, self.aliases)
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
            source_ref=self.source_ref,
            source_location=f"{self.source_ref}:{node.lineno}",
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
                    node, self.aliases, self._name_is_canonical
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
        )
        self.artifacts.toolsets.append(toolset)
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

    def _extract_mcp_toolset(self, call: ast.Call, agent_name: str) -> list[LoadedToolSource]:
        filter_values = _string_list(_kwarg_literal(call, "tool_filter"))
        inventory_path = _extract_path_argument(call, self.aliases, MCP_INVENTORY_KEYS)
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
        )
        self.artifacts.toolsets.append(toolset)
        if not inventory_path:
            self._surface_warning(
                f"Google ADK McpToolset at {self.source_ref}:{call.lineno} "
                "has no static MCP tool inventory path.",
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
                elements = (
                    keyword.value.elts
                    if isinstance(keyword.value, ast.List | ast.Tuple)
                    else None
                )
                sub_agent_count = len(elements) if elements is not None else None
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
                for item in elements or []:
                    variable = _qualified_name(item, self.aliases)
                    if variable is None:
                        continue
                    resolved = self._sub_agent_name(variable, call)
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


def _simple_target_name(targets: list[ast.expr]) -> str | None:
    if len(targets) != 1:
        return None
    target = targets[0]
    return target.id if isinstance(target, ast.Name) else None


def _call_func_name(call: ast.Call) -> str | None:
    func = _kwarg(call, "func")
    if func is None and call.args:
        func = call.args[0]
    if isinstance(func, ast.Name):
        return func.id
    return None


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

    def load(
        self,
        source: ToolSourceConfig | None,
        base_dir: Path,
        manifest: AgentsShipgateManifest,
    ) -> LoadedAdapterResult:
        loaded_sources, artifacts = load_google_adk_artifacts(manifest, base_dir)
        return LoadedAdapterResult(tool_sources=loaded_sources, artifact=artifacts)
