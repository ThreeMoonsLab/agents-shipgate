from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_shipgate.config.schema import (
    ArtifactPathConfig,
    NamedArtifactPathConfig,
    OpenAIApiConfig,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.models import (
    ApiResponseFormat,
    LoadedToolSource,
    OpenAIApiArtifacts,
    Tool,
)
from agents_shipgate.inputs.common import (
    load_structured_file,
    load_text_file,
    resolve_input_path,
    schema_to_parameters,
    stable_tool_id,
    tool_name_warning,
)

PROMPT_SUFFIXES = {".md", ".markdown", ".txt"}


def load_openai_api_artifacts(
    config: OpenAIApiConfig | None, base_dir: Path
) -> tuple[LoadedToolSource | None, OpenAIApiArtifacts | None]:
    if config is None:
        return None, None

    artifacts = OpenAIApiArtifacts()
    tools: list[Tool] = []

    for prompt_path in config.prompt_files:
        prompt = _resolve(base_dir, prompt_path)
        if prompt.suffix.lower() not in PROMPT_SUFFIXES:
            artifacts.warnings.append(
                f"OpenAI API prompt file {prompt_path!r} is not markdown/text; "
                "loaded as UTF-8 text."
            )
        text = load_text_file(prompt)
        artifacts.prompt_files.append(_display_path(prompt, base_dir))
        artifacts.prompt_text = "\n\n".join(
            item for item in [artifacts.prompt_text, text] if item
        )

    for tool_ref in config.tools:
        data = _load_required_or_optional(tool_ref, base_dir, artifacts.warnings, "tools")
        if data is None:
            continue
        artifacts.tool_files.append(
            _display_path(_resolve(base_dir, tool_ref.path), base_dir)
        )
        for index, item in enumerate(_tool_items(data)):
            tool = _tool_from_openai_function(
                item,
                source_ref=f"{tool_ref.path}#{index}",
                fallback_name=None,
                warnings=artifacts.warnings,
            )
            if tool:
                tools.append(tool)

    for schema_ref in config.function_schemas:
        data = _load_required_or_optional(
            schema_ref, base_dir, artifacts.warnings, "function_schemas"
        )
        if data is None:
            continue
        artifacts.tool_files.append(
            _display_path(_resolve(base_dir, schema_ref.path), base_dir)
        )
        tool = _tool_from_openai_function(
            data,
            source_ref=schema_ref.path,
            fallback_name=schema_ref.name,
            warnings=artifacts.warnings,
        )
        if tool:
            tools.append(tool)

    for response_ref in config.response_formats:
        data = _load_required_or_optional(
            response_ref, base_dir, artifacts.warnings, "response_formats"
        )
        if data is None:
            continue
        artifacts.response_formats.append(
            _response_format_from_artifact(data, response_ref)
        )

    if config.api_model_config:
        data = _load_required_or_optional(
            config.api_model_config, base_dir, artifacts.warnings, "model_config"
        )
        if data is not None:
            if not isinstance(data, dict):
                raise InputParseError(
                    "OpenAI API model_config must contain an object: "
                    f"{config.api_model_config.path}"
                )
            artifacts.model_config_path = config.api_model_config.path
            artifacts.model_settings = data

    for test_ref in config.test_cases:
        data = _load_required_or_optional(
            test_ref, base_dir, artifacts.warnings, "test_cases"
        )
        if data is None:
            continue
        artifacts.test_case_files.append(
            _display_path(_resolve(base_dir, test_ref.path), base_dir)
        )
        artifacts.test_cases.extend(_case_items(data, test_ref.path))

    for trace_ref in config.trace_samples:
        data = _load_trace_sample(trace_ref, base_dir, artifacts.warnings)
        if data is None:
            continue
        artifacts.trace_sample_files.append(
            _display_path(_resolve(base_dir, trace_ref.path), base_dir)
        )
        artifacts.trace_samples.extend(data)
        unsupported = sum(1 for item in data if not isinstance(item.get("tool_name"), str))
        if unsupported:
            artifacts.warnings.append(
                f"OpenAI API trace sample {trace_ref.path!r} contains {unsupported} "
                "entries without tool_name."
            )

    for policy_ref in config.policy_rules:
        data = _load_required_or_optional(
            policy_ref, base_dir, artifacts.warnings, "policy_rules"
        )
        if data is None:
            continue
        if not isinstance(data, dict):
            raise InputParseError(
                f"OpenAI API policy_rules file must contain an object: {policy_ref.path}"
            )
        artifacts.policy_rule_files.append(
            _display_path(_resolve(base_dir, policy_ref.path), base_dir)
        )
        artifacts.policy_rules.update(data)

    return (
        LoadedToolSource(
            source_id="openai_api",
            source_type="openai_api",
            tools=tools,
            warnings=artifacts.warnings,
        ),
        artifacts,
    )


def _load_required_or_optional(
    ref: ArtifactPathConfig,
    base_dir: Path,
    warnings: list[str],
    kind: str,
) -> Any | None:
    path = _resolve(base_dir, ref.path)
    try:
        return load_structured_file(path)
    except InputParseError:
        if not ref.optional:
            raise
        warnings.append(f"Optional OpenAI API {kind} artifact {ref.path!r} failed to load.")
        return None


def _load_trace_sample(
    ref: ArtifactPathConfig,
    base_dir: Path,
    warnings: list[str],
) -> list[dict[str, Any]] | None:
    path = _resolve(base_dir, ref.path)
    try:
        if path.suffix.lower() == ".jsonl":
            items: list[dict[str, Any]] = []
            for line_number, line in enumerate(
                load_text_file(path).splitlines(), start=1
            ):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise InputParseError(
                        f"Unable to parse JSONL trace {path}:{line_number}: {exc}"
                    ) from exc
                if isinstance(item, dict):
                    items.append(item)
                else:
                    warnings.append(
                        f"OpenAI API trace sample {ref.path!r} line {line_number} is not an object."
                    )
            return items
        data = load_structured_file(path)
        return _trace_items(data, ref.path, warnings)
    except InputParseError:
        if not ref.optional:
            raise
        warnings.append(
            f"Optional OpenAI API trace artifact {ref.path!r} failed to load."
        )
        return None


def _tool_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        tools = data.get("tools")
        if isinstance(tools, list):
            return [item for item in tools if isinstance(item, dict)]
        return [data]
    raise InputParseError("OpenAI API tools artifact must be an object or list")


def _tool_from_openai_function(
    data: dict[str, Any],
    *,
    source_ref: str,
    fallback_name: str | None,
    warnings: list[str],
) -> Tool | None:
    function = data.get("function") if isinstance(data.get("function"), dict) else data
    if not isinstance(function, dict):
        warnings.append(f"Skipping unsupported OpenAI API function object in {source_ref}.")
        return None

    name = function.get("name") or fallback_name
    if not isinstance(name, str) or not name.strip():
        warnings.append(f"Skipping OpenAI API function without name in {source_ref}.")
        return None

    parameters = function.get("parameters")
    if parameters is None and _looks_like_json_schema(function):
        parameters = function
    if parameters is None:
        parameters = {}
    if not isinstance(parameters, dict):
        warnings.append(f"Skipping OpenAI API function {name!r}; parameters is not an object.")
        return None

    description = function.get("description")
    strict = function.get("strict")
    annotations: dict[str, Any] = {
        "openaiApiFunction": True,
        "openaiStrict": strict,
    }
    warning = tool_name_warning(name)
    if warning:
        warnings.append(warning)

    return Tool(
        id=stable_tool_id(name),
        name=name,
        description=description if isinstance(description, str) else None,
        source_type="openai_api",
        source_id="openai_api",
        source_ref=source_ref,
        input_schema=parameters,
        parameters=schema_to_parameters(parameters),
        annotations={
            key: value for key, value in annotations.items() if value is not None
        },
        extraction_confidence="high",
        extraction={"method": "openai_api_artifact", "confidence": "high"},
    )


def _response_format_from_artifact(
    data: Any, ref: NamedArtifactPathConfig
) -> ApiResponseFormat:
    if not isinstance(data, dict):
        raise InputParseError(f"OpenAI API response format must be an object: {ref.path}")
    wrapper = data.get("json_schema") if isinstance(data.get("json_schema"), dict) else None
    if data.get("type") == "json_schema" and wrapper:
        schema = wrapper.get("schema")
        strict = wrapper.get("strict")
        name = wrapper.get("name")
    elif wrapper and "schema" in wrapper:
        schema = wrapper.get("schema")
        strict = wrapper.get("strict")
        name = wrapper.get("name")
    elif "schema" in data and isinstance(data.get("schema"), dict):
        schema = data.get("schema")
        strict = data.get("strict")
        name = data.get("name")
    else:
        schema = data
        strict = data.get("strict")
        name = data.get("name")
    if not isinstance(schema, dict):
        raise InputParseError(f"OpenAI API response schema is invalid: {ref.path}")
    return ApiResponseFormat(
        path=ref.path,
        name=name if isinstance(name, str) else ref.name,
        strict=strict if isinstance(strict, bool) else None,
        json_schema=schema,
        downstream_critical_fields=ref.downstream_critical_fields,
    )


def _case_items(data: Any, source_ref: str) -> list[dict[str, Any]]:
    if isinstance(data, dict) and isinstance(data.get("test_cases"), list):
        data = data["test_cases"]
    if not isinstance(data, list):
        raise InputParseError(f"OpenAI API test cases must be a list: {source_ref}")
    return [item for item in data if isinstance(item, dict)]


def _trace_items(
    data: Any, source_ref: str, warnings: list[str] | None = None
) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("trace_samples", "events", "tool_calls"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        raise InputParseError(
            f"OpenAI API trace samples must be a list or JSONL: {source_ref}"
        )
    items = [item for item in data if isinstance(item, dict)]
    skipped = len(data) - len(items)
    if skipped and warnings is not None:
        warnings.append(
            f"OpenAI API trace sample {source_ref!r} contains {skipped} unsupported entries."
        )
    return items


def _looks_like_json_schema(value: dict[str, Any]) -> bool:
    return any(key in value for key in ("type", "properties", "required", "additionalProperties"))


def _resolve(base_dir: Path, value: str) -> Path:
    return resolve_input_path(base_dir, value)


def _display_path(path: Path, base_dir: Path) -> str:
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
