from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal
from urllib.parse import urlsplit, urlunsplit

from agents_shipgate.core.artifact_models import ConductorArtifacts
from agents_shipgate.core.domain import AuthInfo, LoadedToolSource, Tool
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.common import (
    json_pointer_escape,
    load_structured_file,
    resolve_input_path,
    stable_tool_id,
    tool_name_warning,
)
from agents_shipgate.inputs.coverage import BoundaryCell, SourceCoverage
from agents_shipgate.inputs.protocol import LoadedAdapterResult
from agents_shipgate.schemas.manifest import AgentsShipgateManifest, ToolSourceConfig

MAX_CONDUCTOR_DEPTH = 32
MAX_CONDUCTOR_TASKS = 5000
_EXPRESSION_RE = re.compile(r"\$\{[^{}]+\}")
_CALL_MCP_FIELDS = {"mcpServer", "method", "headers", "arguments"}
_AUTH_HEADER_NAMES = {"authorization", "proxy-authorization", "x-api-key", "api-key"}
_PROVIDER_CAPABILITY_FIELDS = {
    "webSearch",
    "codeInterpreter",
    "fileSearchVectorStoreIds",
    "googleSearchRetrieval",
}
_UNSUPPORTED_CAPABILITY_TYPES = {
    "HTTP",
    "SIMPLE",
    "EVENT",
    "KAFKA_PUBLISH",
    "START_WORKFLOW",
    "AGENT",
    "DYNAMIC",
    "FORK_JOIN_DYNAMIC",
    "INLINE",
}
_RUNTIME_GENERATED_TYPES = {
    "START_WORKFLOW",
    "AGENT",
    "DYNAMIC",
    "FORK_JOIN_DYNAMIC",
}
_KNOWN_NON_CAPABILITY_TYPES = {
    "HUMAN",
    "WAIT",
    "SWITCH",
    "DECISION",
    "DO_WHILE",
    "FORK_JOIN",
    "JOIN",
    "SUB_WORKFLOW",
    "SET_VARIABLE",
    "TERMINATE",
    "NOOP",
    "JSON_JQ_TRANSFORM",
    "LLM_CHAT_COMPLETE",
    "LIST_MCP_TOOLS",
    "CALL_MCP_TOOL",
}


@dataclass(frozen=True)
class _WorkflowRecord:
    source: ToolSourceConfig
    source_path: str
    file_path: Path
    pointer: str
    raw: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.raw["name"])

    @property
    def version(self) -> int:
        raw = self.raw.get("version", 1)
        return raw if type(raw) is int else 1


@dataclass(frozen=True)
class _Checkpoint:
    task_ref: str
    pointer: str
    sequence_pointer: str


def load_conductor_artifacts(
    manifest: AgentsShipgateManifest,
    base_dir: Path,
) -> tuple[list[LoadedToolSource], ConductorArtifacts | None]:
    sources = [source for source in manifest.tool_sources if source.type == "conductor"]
    if not sources:
        return [], None

    records: list[_WorkflowRecord] = []
    artifacts = ConductorArtifacts()
    source_warnings: dict[str, list[str]] = {source.id: [] for source in sources}
    skipped_sources: set[str] = set()

    for source in sources:
        try:
            source_records, workflow_files = _load_source(source, base_dir)
        except InputParseError as exc:
            if not source.optional:
                raise
            warning = f"Optional Conductor source {source.id!r} failed to load: {exc}"
            source_warnings[source.id].append(warning)
            artifacts.warnings.append(warning)
            skipped_sources.add(source.id)
            continue
        records.extend(source_records)
        artifacts.workflow_files.extend(workflow_files)

    index: dict[tuple[str, int], list[_WorkflowRecord]] = {}
    for record in records:
        index.setdefault((record.name, record.version), []).append(record)

    tools_by_source: dict[str, list[Tool]] = {source.id: [] for source in sources}
    extractor = _ConductorExtractor(
        artifacts=artifacts,
        workflow_index=index,
        warnings_by_source=source_warnings,
    )
    for record in records:
        tools_by_source[record.source.id].extend(extractor.extract(record))

    artifacts.workflow_files = list(dict.fromkeys(artifacts.workflow_files))
    artifacts.dynamic_tool_surfaces.sort(key=_surface_sort_key)
    artifacts.unsupported_capabilities.sort(key=_surface_sort_key)
    artifacts.warnings = sorted(dict.fromkeys(artifacts.warnings))
    for source_id, warnings in source_warnings.items():
        source_warnings[source_id] = sorted(dict.fromkeys(warnings))

    loaded = [
        LoadedToolSource(
            source_id=source.id,
            source_type="conductor",
            tools=tools_by_source[source.id],
            warnings=source_warnings[source.id],
        )
        for source in sources
        if source.id not in skipped_sources or source_warnings[source.id]
    ]
    return loaded, artifacts


def is_conductor_workflow_document(data: Any) -> bool:
    """Return whether a JSON document has a valid Conductor workflow root shape."""

    try:
        workflows = _workflow_objects(data, path=None, directory_probe=True)
    except InputParseError:
        return False
    return bool(workflows)


def conductor_agent_task_types(data: Any) -> set[str]:
    """Return supported AI/MCP task markers from a valid workflow document."""

    if not is_conductor_workflow_document(data):
        return set()
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            task_type = value.get("type")
            if task_type in {"CALL_MCP_TOOL", "LIST_MCP_TOOLS", "LLM_CHAT_COMPLETE"}:
                found.add(str(task_type))
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(data)
    return found


def _load_source(
    source: ToolSourceConfig,
    base_dir: Path,
) -> tuple[list[_WorkflowRecord], list[str]]:
    if not source.path:
        raise InputParseError(f"Conductor source {source.id!r} requires path")
    path = resolve_input_path(base_dir, source.path)
    if not path.exists():
        raise InputParseError(f"Input file not found: {path}")

    files = _conductor_files(path, base_dir)
    records: list[_WorkflowRecord] = []
    workflow_files: list[str] = []
    for file_path in files:
        data = load_structured_file(file_path)
        is_directory_child = path.is_dir()
        workflows = _workflow_objects(
            data,
            path=file_path,
            directory_probe=is_directory_child,
        )
        if not workflows:
            continue
        source_path = file_path.resolve().relative_to(base_dir.resolve()).as_posix()
        workflow_files.append(source_path)
        for pointer, raw in workflows:
            records.append(
                _WorkflowRecord(
                    source=source,
                    source_path=source_path,
                    file_path=file_path,
                    pointer=pointer,
                    raw=raw,
                )
            )
    if not records:
        raise InputParseError(f"Conductor source has no workflow definitions: {path}")
    return records, workflow_files


def _conductor_files(path: Path, base_dir: Path) -> list[Path]:
    if path.is_file():
        if path.suffix.lower() != ".json":
            raise InputParseError(f"Conductor source must be a .json file or directory: {path}")
        return [path]
    if not path.is_dir():
        raise InputParseError(f"Conductor source is not a file or directory: {path}")

    base = base_dir.resolve()
    root = path.resolve()
    discovered: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for current, dirnames, filenames in os.walk(path, followlinks=False):
        current_path = Path(current)
        traversable_dirs: list[str] = []
        for name in sorted(dirnames):
            child_dir = current_path / name
            if child_dir.is_symlink():
                resolved_dir = child_dir.resolve()
                try:
                    resolved_dir.relative_to(base)
                    resolved_dir.relative_to(root)
                except ValueError as exc:
                    raise InputParseError(
                        "Conductor directory symlink resolves outside its declared root: "
                        f"{child_dir}"
                    ) from exc
                continue
            traversable_dirs.append(name)
        dirnames[:] = traversable_dirs
        for filename in sorted(filenames):
            child = current_path / filename
            if child.suffix.lower() != ".json":
                continue
            resolved = child.resolve()
            try:
                resolved.relative_to(base)
                resolved.relative_to(root)
            except ValueError as exc:
                raise InputParseError(
                    f"Conductor directory child resolves outside its declared root: {child}"
                ) from exc
            if resolved in seen:
                continue
            seen.add(resolved)
            relative = resolved.relative_to(base).as_posix()
            discovered.append((relative, resolved))
    return [item[1] for item in sorted(discovered, key=lambda item: item[0])]


def _workflow_objects(
    data: Any,
    *,
    path: Path | None,
    directory_probe: bool,
) -> list[tuple[str, dict[str, Any]]]:
    if isinstance(data, dict):
        candidates = [("", data)]
    elif isinstance(data, list):
        candidates = [(f"/{index}", item) for index, item in enumerate(data)]
    else:
        if directory_probe:
            return []
        raise InputParseError(f"Conductor workflow JSON must be an object or array: {path}")

    output: list[tuple[str, dict[str, Any]]] = []
    for pointer, raw in candidates:
        if not _looks_like_workflow(raw):
            if directory_probe and len(candidates) == 1:
                return []
            raise InputParseError(
                f"Invalid Conductor workflow at {path or '<memory>'}#{pointer}: "
                "expected non-blank name and non-empty tasks array"
            )
        assert isinstance(raw, dict)
        _validate_workflow(raw, path=path, pointer=pointer)
        output.append((pointer, raw))
    return output


def _looks_like_workflow(raw: Any) -> bool:
    return (
        isinstance(raw, dict)
        and isinstance(raw.get("name"), str)
        and bool(raw["name"].strip())
        and isinstance(raw.get("tasks"), list)
        and bool(raw["tasks"])
    )


def _validate_workflow(raw: dict[str, Any], *, path: Path | None, pointer: str) -> None:
    schema_version = raw.get("schemaVersion", 2)
    if type(schema_version) is not int or schema_version != 2:
        raise InputParseError(
            f"Unsupported Conductor schemaVersion at {path or '<memory>'}#{pointer}: "
            f"expected 2, got {schema_version!r}"
        )
    version = raw.get("version", 1)
    if type(version) is not int:
        raise InputParseError(
            f"Invalid Conductor workflow version at {path or '<memory>'}#{pointer}: "
            "expected integer"
        )


class _ConductorExtractor:
    def __init__(
        self,
        *,
        artifacts: ConductorArtifacts,
        workflow_index: dict[tuple[str, int], list[_WorkflowRecord]],
        warnings_by_source: dict[str, list[str]],
    ) -> None:
        self.artifacts = artifacts
        self.workflow_index = workflow_index
        self.warnings_by_source = warnings_by_source
        self._visited_inline: set[tuple[str, str]] = set()
        self._seen_task_refs: dict[tuple[str, str], set[str]] = {}

    def extract(self, record: _WorkflowRecord) -> list[Tool]:
        self.artifacts.workflows.append(
            {
                "name": record.name,
                "version": record.version,
                "source_path": record.source_path,
                "pointer": record.pointer,
            }
        )
        duplicate_count = len(self.workflow_index.get((record.name, record.version), []))
        if duplicate_count > 1:
            self._warn(
                record.source.id,
                f"Conductor workflow {record.name!r} version {record.version} is duplicated; "
                "sub-workflow references to it are ambiguous.",
            )
        tasks_pointer = f"{record.pointer}/tasks"
        return self._walk_sequence(
            record=record,
            workflow=record.raw,
            tasks=record.raw["tasks"],
            pointer=tasks_pointer,
            checkpoints=[],
            depth=0,
        )

    def _walk_sequence(
        self,
        *,
        record: _WorkflowRecord,
        workflow: dict[str, Any],
        tasks: list[Any],
        pointer: str,
        checkpoints: list[_Checkpoint],
        depth: int,
    ) -> list[Tool]:
        if depth > MAX_CONDUCTOR_DEPTH:
            raise InputParseError(
                f"Conductor task nesting exceeds {MAX_CONDUCTOR_DEPTH} at "
                f"{record.source_path}#{pointer}"
            )
        tools: list[Tool] = []
        active = list(checkpoints)
        for index, raw_task in enumerate(tasks):
            task_pointer = f"{pointer}/{index}"
            if not isinstance(raw_task, dict):
                raise InputParseError(
                    f"Conductor task must be an object at {record.source_path}#{task_pointer}"
                )
            task_type_raw = raw_task.get("type")
            if not isinstance(task_type_raw, str) or not task_type_raw.strip():
                raise InputParseError(
                    f"Conductor task requires type at {record.source_path}#{task_pointer}"
                )
            self.artifacts.task_count += 1
            if self.artifacts.task_count > MAX_CONDUCTOR_TASKS:
                raise InputParseError(
                    f"Conductor task count exceeds {MAX_CONDUCTOR_TASKS} at "
                    f"{record.source_path}#{task_pointer}"
                )
            task_type = task_type_raw.strip().upper()
            task_ref = _task_ref(raw_task, index)
            seen_refs = self._seen_task_refs.setdefault(
                (record.source_path, record.pointer), set()
            )
            if task_ref in seen_refs:
                self._warn(
                    record.source.id,
                    f"Duplicate Conductor taskReferenceName {task_ref!r} at "
                    f"{record.source_path}#{task_pointer}; provenance uses the JSON pointer.",
                )
            seen_refs.add(task_ref)

            if task_type == "HUMAN":
                checkpoint = _Checkpoint(task_ref, task_pointer, pointer)
                active.append(checkpoint)
                self.artifacts.human_checkpoints.append(
                    _task_fact(record, workflow, task_type, task_ref, task_pointer)
                )
            elif task_type == "CALL_MCP_TOOL":
                tool = self._mcp_call(
                    record, workflow, raw_task, task_ref, task_pointer, pointer, active
                )
                if tool is not None:
                    tools.append(tool)
            elif task_type == "LIST_MCP_TOOLS":
                self.artifacts.mcp_discovery_tasks.append(
                    {
                        **_task_fact(record, workflow, task_type, task_ref, task_pointer),
                        **_endpoint_fact(_input_parameters(raw_task).get("mcpServer")),
                        **_header_fact(_input_parameters(raw_task).get("headers")),
                    }
                )
            elif task_type == "LLM_CHAT_COMPLETE":
                self._llm_task(record, workflow, raw_task, task_ref, task_pointer)
            elif task_type == "SUB_WORKFLOW":
                tools.extend(
                    self._sub_workflow(
                        record, workflow, raw_task, task_ref, task_pointer, depth
                    )
                )
            elif task_type in _UNSUPPORTED_CAPABILITY_TYPES:
                self._unsupported(record, workflow, task_type, task_ref, task_pointer)
                if task_type in _RUNTIME_GENERATED_TYPES:
                    self._dynamic(
                        record,
                        workflow,
                        kind="runtime_generated_task",
                        task_type=task_type,
                        task_ref=task_ref,
                        pointer=task_pointer,
                        dynamic_fields=["task_type_or_target"],
                    )
            elif task_type not in _KNOWN_NON_CAPABILITY_TYPES:
                self._unsupported(record, workflow, task_type, task_ref, task_pointer)

            tools.extend(
                self._walk_nested_containers(
                    record=record,
                    workflow=workflow,
                    task=raw_task,
                    task_pointer=task_pointer,
                    checkpoints=active,
                    depth=depth + 1,
                )
            )
        return tools

    def _walk_nested_containers(
        self,
        *,
        record: _WorkflowRecord,
        workflow: dict[str, Any],
        task: dict[str, Any],
        task_pointer: str,
        checkpoints: list[_Checkpoint],
        depth: int,
    ) -> list[Tool]:
        tools: list[Tool] = []
        for key in ("tasks", "loopOver", "defaultCase"):
            if key not in task:
                continue
            nested = task[key]
            if not isinstance(nested, list):
                raise InputParseError(
                    f"Conductor {key} must be an array at {record.source_path}#{task_pointer}/{key}"
                )
            tools.extend(
                self._walk_sequence(
                    record=record,
                    workflow=workflow,
                    tasks=nested,
                    pointer=f"{task_pointer}/{key}",
                    checkpoints=checkpoints,
                    depth=depth,
                )
            )
        cases = task.get("decisionCases")
        if cases is not None:
            if not isinstance(cases, dict):
                raise InputParseError(
                    f"Conductor decisionCases must be an object at "
                    f"{record.source_path}#{task_pointer}/decisionCases"
                )
            for case_name in sorted(cases, key=str):
                branch = cases[case_name]
                if not isinstance(branch, list):
                    raise InputParseError(
                        f"Conductor decision case must be an array at "
                        f"{record.source_path}#{task_pointer}/decisionCases/"
                        f"{json_pointer_escape(str(case_name))}"
                    )
                tools.extend(
                    self._walk_sequence(
                        record=record,
                        workflow=workflow,
                        tasks=branch,
                        pointer=(
                            f"{task_pointer}/decisionCases/"
                            f"{json_pointer_escape(str(case_name))}"
                        ),
                        checkpoints=checkpoints,
                        depth=depth,
                    )
                )
        fork_tasks = task.get("forkTasks")
        if fork_tasks is not None:
            if not isinstance(fork_tasks, list):
                raise InputParseError(
                    f"Conductor forkTasks must be an array at "
                    f"{record.source_path}#{task_pointer}/forkTasks"
                )
            for branch_index, branch in enumerate(fork_tasks):
                if not isinstance(branch, list):
                    raise InputParseError(
                        f"Conductor fork branch must be an array at "
                        f"{record.source_path}#{task_pointer}/forkTasks/{branch_index}"
                    )
                tools.extend(
                    self._walk_sequence(
                        record=record,
                        workflow=workflow,
                        tasks=branch,
                        pointer=f"{task_pointer}/forkTasks/{branch_index}",
                        checkpoints=checkpoints,
                        depth=depth,
                    )
                )
        return tools

    def _mcp_call(
        self,
        record: _WorkflowRecord,
        workflow: dict[str, Any],
        task: dict[str, Any],
        task_ref: str,
        task_pointer: str,
        sequence_pointer: str,
        checkpoints: list[_Checkpoint],
    ) -> Tool | None:
        inputs = _input_parameters(task)
        method_binding = _binding(inputs.get("method"))
        server_fact = _endpoint_fact(inputs.get("mcpServer"))
        dynamic_fields: list[str] = []
        if method_binding != "literal":
            dynamic_fields.append("method")
        if server_fact["endpoint_binding"] != "literal":
            dynamic_fields.append("mcpServer")
        if dynamic_fields:
            self._dynamic(
                record,
                workflow,
                kind="mcp_call",
                task_type="CALL_MCP_TOOL",
                task_ref=task_ref,
                pointer=task_pointer,
                dynamic_fields=dynamic_fields,
            )

        extra_fields = sorted(str(key) for key in inputs if key not in _CALL_MCP_FIELDS)
        if extra_fields:
            self._warn(
                record.source.id,
                f"Conductor CALL_MCP_TOOL at {record.source_path}#{task_pointer} contains "
                f"unsupported flat input fields {extra_fields!r}; only explicit arguments "
                "are treated as MCP tool arguments.",
            )
        checkpoint_refs = [checkpoint.task_ref for checkpoint in checkpoints]
        relation = "unknown"
        if checkpoints:
            relation = (
                "same_sequence"
                if any(item.sequence_pointer == sequence_pointer for item in checkpoints)
                else "inherited"
            )
        argument_value = inputs.get("arguments")
        argument_keys = (
            sorted(str(key) for key in argument_value)
            if isinstance(argument_value, dict)
            else []
        )
        fact = {
            **_task_fact(record, workflow, "CALL_MCP_TOOL", task_ref, task_pointer),
            "method_binding": method_binding,
            "argument_binding": _structured_binding(argument_value),
            "argument_keys": argument_keys,
            **server_fact,
            **_header_fact(inputs.get("headers")),
            "preceding_checkpoint_refs": checkpoint_refs,
            "checkpoint_relation": relation,
            "semantic_approval": "unknown",
        }
        self.artifacts.mcp_call_tasks.append(fact)
        if method_binding != "literal":
            return None

        method = str(inputs["method"]).strip()
        if warning := tool_name_warning(method):
            self._warn(record.source.id, warning)
        identity = ":".join(
            [
                record.source.id,
                record.source_path,
                record.name,
                str(record.version),
                task_ref,
                task_pointer,
                method,
            ]
        )
        return Tool(
            id=stable_tool_id(identity),
            name=method,
            description=(
                str(task["description"])
                if isinstance(task.get("description"), str)
                else None
            ),
            source_type="conductor_mcp_call",
            source_id=record.source.id,
            source_ref=record.source_path,
            source_path=record.source_path,
            source_pointer=task_pointer,
            input_schema={"type": "object", "properties": {}},
            annotations={
                "framework": "conductor",
                "mcp_server": True,
                "workflow_name": record.name,
                "workflow_version": record.version,
                "task_type": "CALL_MCP_TOOL",
                "task_reference_name": task_ref,
                "schema_partial": True,
                "agent_bindings": [
                    {
                        "agent": record.name,
                        "source_id": record.source.id,
                        "edge_type": "workflow",
                        "source": record.source_path,
                        "source_pointer": task_pointer,
                        "complete": True,
                    }
                ],
                **{
                    key: value
                    for key, value in fact.items()
                    if key
                    in {
                        "argument_binding",
                        "argument_keys",
                        "endpoint_binding",
                        "endpoint_scheme",
                        "endpoint_fingerprint",
                        "header_names",
                        "auth_header_present",
                        "preceding_checkpoint_refs",
                        "checkpoint_relation",
                        "semantic_approval",
                    }
                },
            },
            auth=AuthInfo(source="conductor"),
            extraction_confidence="medium",
            extraction={"method": "conductor_workflow_json", "confidence": "medium"},
        )

    def _llm_task(
        self,
        record: _WorkflowRecord,
        workflow: dict[str, Any],
        task: dict[str, Any],
        task_ref: str,
        task_pointer: str,
    ) -> None:
        inputs = _input_parameters(task)
        fact = _task_fact(
            record, workflow, "LLM_CHAT_COMPLETE", task_ref, task_pointer
        )
        self.artifacts.llm_tasks.append(fact)
        tools = inputs.get("tools")
        if tools is not None:
            dynamic_fields: list[str] = []
            if not isinstance(tools, list):
                dynamic_fields.append("tools")
            else:
                for index, raw_tool in enumerate(tools):
                    if not isinstance(raw_tool, dict):
                        dynamic_fields.append(f"tools[{index}]")
                        continue
                    name_binding = _binding(raw_tool.get("name"))
                    type_binding = _binding(raw_tool.get("type"))
                    if name_binding != "literal":
                        dynamic_fields.append(f"tools[{index}].name")
                    if type_binding != "literal":
                        dynamic_fields.append(f"tools[{index}].type")
                    self.artifacts.llm_advertised_tools.append(
                        {
                            **fact,
                            "tool_index": index,
                            "name_binding": name_binding,
                            "type_binding": type_binding,
                            "name": (
                                str(raw_tool["name"])
                                if name_binding == "literal"
                                else None
                            ),
                            "type": (
                                str(raw_tool["type"])
                                if type_binding == "literal"
                                else None
                            ),
                            "input_schema_present": isinstance(
                                raw_tool.get("inputSchema"), dict
                            ),
                        }
                    )
            if dynamic_fields:
                self._dynamic(
                    record,
                    workflow,
                    kind="llm_tool_advertisement",
                    task_type="LLM_CHAT_COMPLETE",
                    task_ref=task_ref,
                    pointer=task_pointer,
                    dynamic_fields=dynamic_fields,
                )
        for field in sorted(_PROVIDER_CAPABILITY_FIELDS):
            value = inputs.get(field)
            if value in (None, False, [], ""):
                continue
            self._unsupported(
                record,
                workflow,
                "LLM_CHAT_COMPLETE",
                task_ref,
                task_pointer,
                capability=field,
            )

    def _sub_workflow(
        self,
        record: _WorkflowRecord,
        workflow: dict[str, Any],
        task: dict[str, Any],
        task_ref: str,
        task_pointer: str,
        depth: int,
    ) -> list[Tool]:
        params = task.get("subWorkflowParam")
        if not isinstance(params, dict):
            inputs = _input_parameters(task)
            params = inputs.get("subWorkflowParam") if isinstance(inputs, dict) else None
        fact = {
            **_task_fact(record, workflow, "SUB_WORKFLOW", task_ref, task_pointer),
            "resolution": "unresolved",
        }
        self.artifacts.sub_workflows.append(fact)
        if not isinstance(params, dict):
            self._dynamic(
                record,
                workflow,
                kind="sub_workflow",
                task_type="SUB_WORKFLOW",
                task_ref=task_ref,
                pointer=task_pointer,
                dynamic_fields=["subWorkflowParam"],
            )
            return []

        inline = params.get("workflowDefinition")
        if isinstance(inline, dict):
            inline_pointer = f"{task_pointer}/subWorkflowParam/workflowDefinition"
            if not _looks_like_workflow(inline):
                raise InputParseError(
                    f"Invalid inline Conductor workflow at "
                    f"{record.source_path}#{inline_pointer}"
                )
            _validate_workflow(inline, path=record.file_path, pointer=inline_pointer)
            visit_key = (record.source_path, inline_pointer)
            if visit_key in self._visited_inline:
                self._dynamic(
                    record,
                    workflow,
                    kind="sub_workflow_cycle",
                    task_type="SUB_WORKFLOW",
                    task_ref=task_ref,
                    pointer=task_pointer,
                    dynamic_fields=["workflowDefinition"],
                )
                return []
            self._visited_inline.add(visit_key)
            fact["resolution"] = "inline"
            inline_record = _WorkflowRecord(
                source=record.source,
                source_path=record.source_path,
                file_path=record.file_path,
                pointer=inline_pointer,
                raw=inline,
            )
            tools = self._walk_sequence(
                record=inline_record,
                workflow=inline,
                tasks=inline["tasks"],
                pointer=f"{inline_pointer}/tasks",
                checkpoints=[],
                depth=depth + 1,
            )
            self._visited_inline.remove(visit_key)
            return tools
        if inline is not None:
            self._dynamic(
                record,
                workflow,
                kind="sub_workflow",
                task_type="SUB_WORKFLOW",
                task_ref=task_ref,
                pointer=task_pointer,
                dynamic_fields=["workflowDefinition"],
            )
            return []

        name = params.get("name")
        version = params.get("version")
        if _binding(name) == "literal" and type(version) is int:
            matches = self.workflow_index.get((str(name), version), [])
            if len(matches) == 1:
                if self._local_sub_workflow_cycles(record, matches[0]):
                    self._dynamic(
                        record,
                        workflow,
                        kind="sub_workflow_cycle",
                        task_type="SUB_WORKFLOW",
                        task_ref=task_ref,
                        pointer=task_pointer,
                        dynamic_fields=["name_or_version"],
                    )
                    return []
                fact["resolution"] = "local_exact"
                fact["target_name"] = str(name)
                fact["target_version"] = version
                return []
        self._dynamic(
            record,
            workflow,
            kind="sub_workflow",
            task_type="SUB_WORKFLOW",
            task_ref=task_ref,
            pointer=task_pointer,
            dynamic_fields=["name_or_version"],
        )
        return []

    def _local_sub_workflow_cycles(
        self,
        origin: _WorkflowRecord,
        target: _WorkflowRecord,
    ) -> bool:
        origin_key = (origin.name, origin.version)

        def visit(record: _WorkflowRecord, visiting: set[tuple[str, int]]) -> bool:
            key = (record.name, record.version)
            if key == origin_key:
                return True
            if key in visiting:
                return True
            visiting.add(key)
            for task in _iter_tasks(record.raw.get("tasks")):
                if str(task.get("type", "")).strip().upper() != "SUB_WORKFLOW":
                    continue
                params = task.get("subWorkflowParam")
                if not isinstance(params, dict):
                    params = _input_parameters(task).get("subWorkflowParam")
                if not isinstance(params, dict):
                    continue
                name = params.get("name")
                version = params.get("version")
                if _binding(name) != "literal" or type(version) is not int:
                    continue
                matches = self.workflow_index.get((str(name), version), [])
                if len(matches) == 1 and visit(matches[0], visiting):
                    return True
            visiting.remove(key)
            return False

        return visit(target, set())

    def _dynamic(
        self,
        record: _WorkflowRecord,
        workflow: dict[str, Any],
        *,
        kind: str,
        task_type: str,
        task_ref: str,
        pointer: str,
        dynamic_fields: list[str],
    ) -> None:
        surface = {
            **_task_fact(record, workflow, task_type, task_ref, pointer),
            "kind": kind,
            "dynamic_fields": sorted(dict.fromkeys(dynamic_fields)),
        }
        self.artifacts.dynamic_tool_surfaces.append(surface)
        self._warn(
            record.source.id,
            f"Conductor {task_type} at {record.source_path}#{pointer} has a "
            f"dynamic or unresolved capability surface: {surface['dynamic_fields']!r}.",
        )

    def _unsupported(
        self,
        record: _WorkflowRecord,
        workflow: dict[str, Any],
        task_type: str,
        task_ref: str,
        pointer: str,
        *,
        capability: str | None = None,
    ) -> None:
        fact = {
            **_task_fact(record, workflow, task_type, task_ref, pointer),
            "kind": "unsupported_capability",
            "capability": capability or task_type,
        }
        self.artifacts.unsupported_capabilities.append(fact)
        self._warn(
            record.source.id,
            f"Conductor capability {fact['capability']!r} at "
            f"{record.source_path}#{pointer} is recognized but not enumerated by the "
            "MCP-core v1 adapter.",
        )

    def _warn(self, source_id: str, warning: str) -> None:
        self.warnings_by_source[source_id].append(warning)
        self.artifacts.warnings.append(warning)


class ConductorAdapter:
    source_type: ClassVar[str] = "conductor"
    scope: ClassVar[Literal["per_source", "per_scan"]] = "per_scan"
    artifact_class: ClassVar[type | None] = ConductorArtifacts

    coverage: ClassVar[SourceCoverage] = SourceCoverage(
        adapter="conductor",
        label="Conductor OSS workflows",
        reads=(
            "Conductor workflow definition JSON."
        ),
        cells=(
            BoundaryCell(
                shape="export_artifact",
                status="not_applicable",
                reads=(
                    "A workflow definition declares calls, not a tool contract. The "
                    "called server's own export is configured as its own `mcp` "
                    "source."
                ),
            ),
            BoundaryCell(
                shape="literal_registration",
                status="extracted",
                reads=(
                    "A `CALL_MCP_TOOL` task whose `method` is a literal. The call is "
                    "read with its endpoint, arguments, and preceding human "
                    "checkpoints; the tool's schema is not in the workflow, so the "
                    "action is recorded as schema-partial."
                ),
                emits=("conductor_mcp_call",),
                ceiling="medium",
            ),
            BoundaryCell(
                shape="factory",
                status="not_applicable",
                reads="A workflow task is declared, never constructed by the file.",
            ),
            BoundaryCell(
                shape="dynamic_construction",
                variant="expression-backed method",
                status="not_extracted",
                reads=(
                    "A `CALL_MCP_TOOL` whose `method` is a workflow expression "
                    "adds no action: the name that would identify it is not in "
                    "the file. The task is still recorded as a dynamic fact."
                ),
                raises=("SHIP-CONDUCTOR-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE",),
            ),
            BoundaryCell(
                shape="dynamic_construction",
                variant="expression-backed server",
                status="extracted",
                reads=(
                    "A literal `method` with an expression-backed `mcpServer` "
                    "still names the call, so the action enters the catalog at "
                    "`medium`. Only the endpoint it reaches is unresolved — a "
                    "different question from which tool is called."
                ),
                emits=("conductor_mcp_call",),
                ceiling="medium",
                raises=("SHIP-CONDUCTOR-DYNAMIC-TOOL-SURFACE-NOT-ENUMERABLE",),
            ),
        ),
    )

    def load(
        self,
        source: ToolSourceConfig | None,
        base_dir: Path,
        manifest: AgentsShipgateManifest,
    ) -> LoadedAdapterResult:
        loaded_sources, artifacts = load_conductor_artifacts(manifest, base_dir)
        return LoadedAdapterResult(tool_sources=loaded_sources, artifact=artifacts)


def _task_ref(task: dict[str, Any], index: int) -> str:
    value = task.get("taskReferenceName")
    if isinstance(value, str) and value.strip():
        return value.strip()
    name = task.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return f"task-{index}"


def _input_parameters(task: dict[str, Any]) -> dict[str, Any]:
    value = task.get("inputParameters")
    return value if isinstance(value, dict) else {}


def _task_fact(
    record: _WorkflowRecord,
    workflow: dict[str, Any],
    task_type: str,
    task_ref: str,
    pointer: str,
) -> dict[str, Any]:
    version = workflow.get("version", 1)
    return {
        "source_id": record.source.id,
        "source_path": record.source_path,
        "source_pointer": pointer,
        "workflow_name": str(workflow.get("name", record.name)),
        "workflow_version": version if type(version) is int else 1,
        "task_type": task_type,
        "task_reference_name": task_ref,
    }


def _binding(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "missing"
    stripped = value.strip()
    matches = list(_EXPRESSION_RE.finditer(stripped))
    if not matches:
        return "literal"
    if len(matches) == 1 and matches[0].span() == (0, len(stripped)):
        return "direct_reference"
    return "template"


def _structured_binding(value: Any) -> str:
    if value is None:
        return "missing"
    if isinstance(value, str):
        return _binding(value)
    if isinstance(value, (dict, list)):
        return "structured_dynamic" if _contains_expression(value) else "structured"
    return "literal"


def _contains_expression(value: Any) -> bool:
    if isinstance(value, str):
        return bool(_EXPRESSION_RE.search(value))
    if isinstance(value, dict):
        return any(_contains_expression(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_expression(item) for item in value)
    return False


def _iter_tasks(value: Any) -> Iterator[dict[str, Any]]:
    if not isinstance(value, list):
        return
    for task in value:
        if not isinstance(task, dict):
            continue
        yield task
        for key in ("tasks", "loopOver", "defaultCase"):
            yield from _iter_tasks(task.get(key))
        cases = task.get("decisionCases")
        if isinstance(cases, dict):
            for case_name in sorted(cases, key=str):
                yield from _iter_tasks(cases[case_name])
        branches = task.get("forkTasks")
        if isinstance(branches, list):
            for branch in branches:
                yield from _iter_tasks(branch)


def _endpoint_fact(value: Any) -> dict[str, Any]:
    binding = _binding(value)
    fact: dict[str, Any] = {"endpoint_binding": binding}
    if binding != "literal":
        return fact
    text = str(value).strip()
    parsed = urlsplit(text)
    host = parsed.hostname or ""
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        host = f"{host}:{port}"
    normalized = urlunsplit((parsed.scheme.lower(), host, parsed.path, "", ""))
    fact["endpoint_scheme"] = parsed.scheme.lower() or None
    fact["endpoint_fingerprint"] = "sha256:" + hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()
    return fact


def _header_fact(value: Any) -> dict[str, Any]:
    names = sorted(str(key) for key in value) if isinstance(value, dict) else []
    return {
        "header_names": names,
        "auth_header_present": any(name.lower() in _AUTH_HEADER_NAMES for name in names),
    }


def _surface_sort_key(surface: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(surface.get("source_path", "")),
        str(surface.get("source_pointer", "")),
        str(surface.get("kind", "")),
    )
