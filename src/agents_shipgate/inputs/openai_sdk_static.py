from __future__ import annotations

import ast
from pathlib import Path
from typing import ClassVar, Literal

from agents_shipgate.core.domain import (
    AuthInfo,
    LoadedToolSource,
    Tool,
    ToolkitScopeBound,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.common import resolve_input_path, stable_tool_id
from agents_shipgate.inputs.protocol import LoadedAdapterResult
from agents_shipgate.inputs.python_static import (
    display_path,
    dotted_name,
    function_input_schema,
    function_output_schema,
    function_signature,
    parse_python_file,
)
from agents_shipgate.schemas.manifest import (
    AgentsShipgateManifest,
    ToolSourceConfig,
)

DEFAULT_FUNCTION_TOOL_DECORATORS = frozenset(
    {"function_tool", "agents.function_tool", "openai_agents.function_tool"}
)


def load_openai_sdk_static_tools(
    source: ToolSourceConfig, manifest: AgentsShipgateManifest, base_dir: Path
) -> LoadedToolSource:
    entrypoint = source.path or (manifest.agent.sdk.entrypoint if manifest.agent.sdk else None)
    if not entrypoint:
        return LoadedToolSource(
            source_id=source.id,
            source_type="openai_agents_sdk",
            warnings=["OpenAI Agents SDK source has no entrypoint"],
        )
    path = resolve_input_path(base_dir, entrypoint)
    if not path.exists():
        return LoadedToolSource(
            source_id=source.id,
            source_type="openai_agents_sdk",
            warnings=[f"OpenAI Agents SDK entrypoint not found: {path}"],
        )
    if path.is_dir():
        python_files = sorted(path.glob("*.py"))
        if not python_files:
            raise InputParseError(f"OpenAI Agents SDK source directory has no Python files: {path}")
        tools: list[Tool] = []
        toolkit_bounds: list[ToolkitScopeBound] = []
        for python_file in python_files:
            file_tools, file_bounds = _load_python_file(python_file, source, base_dir)
            tools.extend(file_tools)
            toolkit_bounds.extend(file_bounds)
    elif path.suffix.lower() == ".py":
        tools, toolkit_bounds = _load_python_file(path, source, base_dir)
    else:
        raise InputParseError(
            f"OpenAI Agents SDK source must be a Python file or directory: {path}"
        )
    return LoadedToolSource(
        source_id=source.id,
        source_type="openai_agents_sdk",
        tools=tools,
        toolkit_bounds=toolkit_bounds,
    )


def _load_python_file(
    path: Path,
    source: ToolSourceConfig,
    base_dir: Path,
) -> tuple[list[Tool], list[ToolkitScopeBound]]:
    try:
        tree = parse_python_file(path, label="OpenAI Agents SDK")
    except InputParseError as exc:
        message = str(exc).replace(
            "OpenAI Agents SDK Python entrypoint",
            "OpenAI Agents SDK entrypoint",
        )
        raise InputParseError(message) from exc
    ref = display_path(path, base_dir)
    decorator_names = _function_tool_decorator_names(tree)
    tools = [
        _function_to_tool(node, source, ref, decorator_names)
        for node in ast.walk(tree)
        if _is_function_tool(node, decorator_names)
    ]
    return tools, _detect_toolkit_bounds(tree, ref)


def _function_tool_decorator_names(tree: ast.Module) -> set[str]:
    names = set(DEFAULT_FUNCTION_TOOL_DECORATORS)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"agents", "openai_agents"}:
            for alias in node.names:
                if alias.name == "function_tool":
                    names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"agents", "openai_agents"}:
                    names.add(f"{alias.asname or alias.name}.function_tool")
    return names


def _is_function_tool(node: ast.AST, decorator_names: set[str]) -> bool:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    for decorator in node.decorator_list:
        name = _decorator_name(decorator)
        if name in decorator_names:
            return True
    return False


def _decorator_name(decorator: ast.AST) -> str | None:
    if isinstance(decorator, ast.Call):
        return _decorator_name(decorator.func)
    return dotted_name(decorator)


def _function_to_tool(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    source: ToolSourceConfig,
    source_ref: str,
    decorator_names: set[str],
) -> Tool:
    tool_name = _tool_name(node, decorator_names)
    input_schema, parameters = function_input_schema(node)
    description = _description(node, decorator_names) or ast.get_docstring(node)
    return Tool(
        id=stable_tool_id(tool_name),
        name=tool_name,
        description=description,
        source_type="sdk_function",
        source_id=source.id,
        source_ref=source_ref,
        source_location=f"{source_ref}:{node.lineno}",
        input_schema=input_schema,
        output_schema=function_output_schema(node),
        parameters=parameters,
        function_signature=function_signature(tool_name, parameters, node),
        auth=AuthInfo(source="sdk_static"),
        extraction_confidence="medium",
        extraction={"method": "openai_agents_sdk_ast", "confidence": "medium"},
    )


def _tool_name(node: ast.FunctionDef | ast.AsyncFunctionDef, decorator_names: set[str]) -> str:
    return _decorator_kwarg_string(node, decorator_names, "name_override") or node.name


def _description(
    node: ast.FunctionDef | ast.AsyncFunctionDef, decorator_names: set[str]
) -> str | None:
    return _decorator_kwarg_string(node, decorator_names, "description_override")


def _decorator_kwarg_string(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    decorator_names: set[str],
    kwarg_name: str,
) -> str | None:
    for decorator in node.decorator_list:
        call = decorator if isinstance(decorator, ast.Call) else None
        if not call or _decorator_name(call.func) not in decorator_names:
            continue
        for keyword in call.keywords:
            if keyword.arg != kwarg_name or not isinstance(keyword.value, ast.Constant):
                continue
            value = keyword.value.value
            if isinstance(value, str) and value:
                return value
    return None


# ---------------------------------------------------------------------------
# Agent-toolkit scope bounds
# ---------------------------------------------------------------------------
#
# Some agent toolkits expose their tools through a runtime factory
# (``*toolkit.get_tools()``) the static extractor cannot enumerate, but the
# *constructor* takes a statically-parseable permission allowlist. We capture
# that allowlist so the verifier can diff it base-vs-head and catch a silent
# broadening (the allowlist removed or widened) even when the individual tools
# stay opaque. See ``core.domain.ToolkitScopeBound`` and the
# ``SHIP-VERIFY-CAPABILITY-SCOPE-BROADENED`` check.
#
# The registry maps a constructor's final attribute name → (provider, parser).
# Both the class constructor and the async factory map to the same provider so
# a base→head switch between them still matches on provider.
_TOOLKIT_CONSTRUCTORS: dict[str, str] = {
    "StripeAgentToolkit": "stripe",
    "create_stripe_agent_toolkit": "stripe",
}


def _detect_toolkit_bounds(tree: ast.Module, ref: str) -> list[ToolkitScopeBound]:
    bounds: list[ToolkitScopeBound] = []
    captured: set[int] = set()
    # Assignments first so we can attach the Python binding name (evidence
    # only — matching is by provider, see core.toolkit_scope.policy_key_for).
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        call = _unwrap_toolkit_call(value)
        if call is None:
            continue
        bound = _bound_from_toolkit_call(call, ref, _first_assign_name(targets))
        if bound is not None:
            bounds.append(bound)
            captured.add(id(call))
    # Bare/inline toolkit constructions not bound to a name.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or id(node) in captured:
            continue
        bound = _bound_from_toolkit_call(node, ref, None)
        if bound is not None:
            bounds.append(bound)
    return bounds


def _unwrap_toolkit_call(value: ast.expr) -> ast.Call | None:
    if isinstance(value, ast.Await):
        value = value.value
    return value if isinstance(value, ast.Call) else None


def _first_assign_name(targets: list[ast.expr]) -> str | None:
    for target in targets:
        if isinstance(target, ast.Name):
            return target.id
        if isinstance(target, ast.Attribute):
            return target.attr
    return None


def _bound_from_toolkit_call(
    call: ast.Call, ref: str, binding: str | None
) -> ToolkitScopeBound | None:
    dotted = dotted_name(call.func)
    if not dotted:
        return None
    constructor = dotted.rsplit(".", maxsplit=1)[-1]
    provider = _TOOLKIT_CONSTRUCTORS.get(constructor)
    if provider is None:
        return None
    parsed = _parse_toolkit_configuration(call)
    if parsed is None:
        # ``configuration`` was supplied but is not a literal we can read
        # (e.g. a variable). Skip rather than guess — the dynamic-toolkit
        # path still degrades to insufficient_evidence, never a silent pass.
        return None
    bounded, scopes = parsed
    return ToolkitScopeBound(
        provider=provider,
        constructor=constructor,
        bounded=bounded,
        scopes=sorted(scopes),
        binding=binding,
        source_ref=ref,
        source_line=getattr(call, "lineno", None),
    )


def _parse_toolkit_configuration(
    call: ast.Call,
) -> tuple[bool, list[str]] | None:
    """Read the ``configuration={"actions": {...}}`` allowlist from a call.

    Returns ``(bounded, scopes)``:

    - No ``configuration`` keyword → ``(False, [])`` (unbounded — the full
      toolkit surface is mounted).
    - ``configuration`` is a dict literal → ``(True, scopes)`` with each
      truthy ``resource:verb`` flattened into a scope string.
    - ``configuration`` present but not a dict literal → ``None`` (ambiguous;
      caller skips emitting a bound).
    """
    config = _keyword_value(call, "configuration")
    if config is None:
        return (False, [])
    if not isinstance(config, ast.Dict):
        return None
    actions = _dict_get(config, "actions")
    if actions is None or not isinstance(actions, ast.Dict):
        # Bounded, but no actions allowlist → no tools enabled.
        return (True, [])
    scopes: list[str] = []
    for resource_key, resource_value in zip(actions.keys, actions.values, strict=False):
        resource = _const_str(resource_key)
        if resource is None:
            continue
        if isinstance(resource_value, ast.Dict):
            for verb_key, verb_value in zip(
                resource_value.keys, resource_value.values, strict=False
            ):
                verb = _const_str(verb_key)
                if verb is not None and _is_truthy_const(verb_value):
                    scopes.append(f"{resource}:{verb}")
        elif _is_truthy_const(resource_value):
            scopes.append(f"{resource}:*")
    return (True, scopes)


def _keyword_value(call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _dict_get(node: ast.Dict, name: str) -> ast.expr | None:
    for key, value in zip(node.keys, node.values, strict=False):
        if _const_str(key) == name:
            return value
    return None


def _const_str(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_truthy_const(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and bool(node.value) is True


class OpenAISDKAdapter:
    """``ToolSourceAdapter`` wrapping :func:`load_openai_sdk_static_tools`."""

    source_type: ClassVar[str] = "openai_agents_sdk"
    scope: ClassVar[Literal["per_source", "per_scan"]] = "per_source"
    artifact_class: ClassVar[type | None] = None

    def load(
        self,
        source: ToolSourceConfig | None,
        base_dir: Path,
        manifest: AgentsShipgateManifest,
    ) -> LoadedAdapterResult:
        assert source is not None, "per_source adapter requires a source"
        return LoadedAdapterResult(
            tool_sources=[load_openai_sdk_static_tools(source, manifest, base_dir)]
        )
