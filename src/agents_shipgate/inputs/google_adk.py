from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

from agents_shipgate.core.artifact_models import (
    GoogleAdkArtifacts,
    GoogleAdkToolset,
)
from agents_shipgate.core.domain import (
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
    ref: ArtifactPathConfig,
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
        extraction={"method": "google_adk_agent_config", "confidence": "low"},
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
        # Every ``Agent(...)`` assignment in this module, walked once and
        # reused: ``extract`` iterates it, and the sub-agent spelling map
        # below is built from it.
        self.agent_call_list = self._agent_calls()
        # ADK routes a handoff to the sub-agent's ``name=``, but
        # ``sub_agents=[salesforce_agent]`` spells the Python variable the
        # agent was assigned to. Agent nodes are keyed by the name, so the
        # two spellings must be reconciled here — otherwise the handoff
        # lands on a phantom node that owns no tools and every tool the
        # sub-agent holds drops out of the root-reachable surface (#385).
        self.agent_names_by_variable = {
            target_name: _kwarg_string(call, "name") or target_name
            for target_name, call in self.agent_call_list
            if target_name
        }

    def extract(self) -> list[LoadedToolSource]:
        tools: list[Tool] = []
        loaded_sources: list[LoadedToolSource] = []
        self._record_eval_references()
        for target_name, call in self.agent_call_list:
            agent_name = _kwarg_string(call, "name") or target_name or "adk_agent"
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
                    self.artifacts.warnings.append(
                        f"Google ADK agent {agent_name!r} uses a dynamic tools expression."
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
                self._append_wrapper_tool(expr.id, tools, agent_name, binding)
            elif expr.id in self.toolset_assignments:
                return self._extract_toolset_call(
                    self.toolset_assignments[expr.id], agent_name, binding
                )
            elif expr.id in self.functions:
                self._bind_function_tool(
                    self.functions[expr.id], tools, agent_name, binding, False
                )
            else:
                self.artifacts.warnings.append(
                    adk_unresolved_tool_warning(agent_name, expr.id)
                )
            return []
        if isinstance(expr, ast.Call):
            call_name = _qualified_name(expr.func, self.aliases)
            if call_name in FUNCTION_TOOL_NAMES | LONG_RUNNING_TOOL_NAMES:
                func_name = _call_func_name(expr)
                if func_name and func_name in self.functions:
                    self._bind_function_tool(
                        self.functions[func_name],
                        tools,
                        agent_name,
                        binding,
                        call_name in LONG_RUNNING_TOOL_NAMES,
                    )
                return []
            if call_name in OPENAPI_TOOLSET_NAMES | MCP_TOOLSET_NAMES:
                return self._extract_toolset_call(expr, agent_name, binding)
        self.artifacts.warnings.append(
            f"Google ADK agent {agent_name!r} has a tool expression that could not be statically resolved."
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
        self.artifacts.warnings.append(
            f"Google ADK tool wrapper {wrapper_name!r} has no statically resolvable function."
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
            self.artifacts.warnings.append(
                f"Google ADK function {node.name!r} is bound as both a long-running "
                "and a standard function tool; review its operation contract."
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
        parameters = _parameters(node)
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
            extraction_confidence="medium",
            extraction={"method": "google_adk_python_ast", "confidence": "medium"},
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
            self.artifacts.warnings.append(
                f"Google ADK OpenAPIToolset at {self.source_ref}:{call.lineno} "
                "has no static local spec path."
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
            self.artifacts.warnings.append(
                f"Google ADK McpToolset at {self.source_ref}:{call.lineno} "
                "has no static MCP tool inventory path."
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
                # Resolve each element to the agent's declared ``name=`` when
                # this module assigns it; an element naming an agent defined
                # elsewhere (or built inline) stays unresolved and is counted
                # as such by the binding graph.
                sub_agent_names = (
                    [
                        self.agent_names_by_variable.get(name, name)
                        for item in elements
                        if (name := _qualified_name(item, self.aliases)) is not None
                    ]
                    if elements is not None
                    else []
                )
                self.artifacts.sub_agents.append(
                    {
                        "agent_name": agent_name,
                        "source_id": self.source_id,
                        "sub_agent_count": sub_agent_count,
                        "sub_agents": sub_agent_names,
                        # False when ``sub_agents`` is not a literal sequence,
                        # or when any element could not be named. The binding
                        # graph turns that into a partial-evidence gap: a
                        # handoff we cannot name is a branch of the capability
                        # surface we did not follow, and it must say so rather
                        # than report the named subset as the whole.
                        "sub_agents_complete": (
                            elements is not None
                            and len(sub_agent_names) == len(elements)
                        ),
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
                self.artifacts.warnings.append(
                    f"Google ADK eval reference {value!r} resolves outside the entrypoint directory."
                )
                continue
            if not path.exists():
                self.artifacts.warnings.append(
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


def _parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ToolParameter]:
    parameters: list[ToolParameter] = []
    positional_args = [*node.args.posonlyargs, *node.args.args]
    positional_defaults: list[ast.expr | None] = [
        None for _ in range(len(positional_args) - len(node.args.defaults))
    ]
    positional_defaults.extend(node.args.defaults)
    for arg, default in zip(positional_args, positional_defaults, strict=True):
        if arg.arg in {"self", "ctx", "context", "tool_context"}:
            continue
        parameters.append(_parameter(arg, required=default is None))
    for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True):
        if arg.arg in {"self", "ctx", "context", "tool_context"}:
            continue
        parameters.append(_parameter(arg, required=default is None))
    return parameters


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
    if annotation in {"int", "float"}:
        return "number"
    if annotation == "bool":
        return "boolean"
    if annotation in {"list", "List"} or (annotation or "").startswith("list["):
        return "array"
    if annotation in {"dict", "Dict"} or (annotation or "").startswith("dict["):
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
