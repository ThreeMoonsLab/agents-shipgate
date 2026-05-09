"""Loader for Anthropic Messages API tool-use artifacts.

The MVP surface (per the plan) is intentionally narrow:

- ``prompt_files`` — markdown / text system prompts. Concatenated into
  ``AnthropicArtifacts.prompt_text``.
- ``tools`` — JSON files containing a Messages API ``tools`` array
  (``[{name, description, input_schema, ...}, ...]``). Each entry becomes
  a normalized :class:`Tool` with ``source_type='anthropic_api'``.
- ``policy_rules`` — YAML / JSON files merged into
  ``AnthropicArtifacts.policy_rules`` so framework-agnostic policy checks
  pick up Anthropic-side approval / confirmation / idempotency lists.

Anthropic-specific differences from the OpenAI loader:

- Tool definitions are flat ``{name, description, input_schema}`` — there is
  no ``function`` wrapper.
- The schema field is ``input_schema``, not ``parameters``. We read only
  ``input_schema`` and warn if a hand-converted OpenAI shape is detected.
- Tool names are validated against Anthropic's documented regex
  ``^[a-zA-Z0-9_-]{1,64}$`` (stricter than the shared
  :data:`agents_shipgate.inputs.common.CONVENTIONAL_TOOL_NAME_RE`). Violations
  are warnings, not errors — the static linter surfaces the issue without
  blocking the load.
- Server-side Anthropic tools (``web_search_*``, ``code_execution_*``)
  execute on Anthropic infrastructure and have no user-controlled
  ``input_schema``. We skip them with a warning rather than emitting noisy
  schema findings on a managed tool the user cannot fix.
- Client-side Anthropic tools (``bash_*``, ``text_editor_*``, ``computer_*``,
  ``memory_*``) execute inside the user's application code and ARE in scope
  for static release-readiness review. We inventory them as
  ``source_type='anthropic_api'`` tools, attach explicit risk hints (e.g.
  ``bash`` -> destructive + write + code_execution), and let the existing
  framework-agnostic checks (approval policy, auth scopes, owner, etc.)
  fire normally. See https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-reference

Public docs: https://docs.anthropic.com/en/docs/build-with-claude/tool-use
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agents_shipgate.config.schema import AnthropicConfig, ArtifactPathConfig
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.models import (
    AnthropicArtifacts,
    LoadedToolSource,
    Tool,
    ToolRiskHint,
)
from agents_shipgate.inputs.common import (
    iter_tool_items,
    load_structured_file,
    load_structured_file_with_positions,
    load_text_file,
    manifest_relative_path,
    resolve_input_path,
    schema_to_parameters,
    stable_tool_id,
)

PROMPT_SUFFIXES = {".md", ".markdown", ".txt"}

# Per https://docs.anthropic.com/en/docs/build-with-claude/tool-use the tool
# name must match this regex. Surface a warning when it doesn't.
_ANTHROPIC_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# Anthropic server-side tool type prefixes. These tools execute on Anthropic
# infrastructure (sandboxed search, sandboxed code interpreter) and have no
# user-controlled schema; static checks against `input_schema` would fire
# spurious findings on a managed surface the user cannot remediate.
_ANTHROPIC_SERVER_TOOL_PREFIXES: tuple[str, ...] = (
    "web_search_",
    "code_execution_",
)

# Anthropic client-side tool type prefixes mapped to known risk tags. Client
# tools execute inside the user's application code, so static
# release-readiness checks ARE relevant: a `bash` tool needs an approval
# policy, a `computer` tool needs auth scopes, etc. Risk tags are
# pre-populated here because the keyword classifier alone would miss most of
# these names (e.g. "computer", "memory", "str_replace_editor").
_ANTHROPIC_CLIENT_TOOL_RISK_TAGS: dict[str, tuple[str, ...]] = {
    "bash_": ("code_execution", "destructive", "write"),
    "text_editor_": ("destructive", "write"),
    "computer_": ("code_execution", "destructive", "write"),
    "memory_": ("write",),
}


def _classify_anthropic_typed_tool(tool_type: str) -> tuple[str, tuple[str, ...]]:
    """Classify a typed Anthropic tool definition.

    Returns ``(category, risk_tags)`` where ``category`` is one of
    ``"server"``, ``"client"``, or ``"unknown"`` and ``risk_tags`` is the
    pre-populated tag list for client tools (empty for server/unknown).
    """
    for prefix in _ANTHROPIC_SERVER_TOOL_PREFIXES:
        if tool_type.startswith(prefix):
            return "server", ()
    for prefix, tags in _ANTHROPIC_CLIENT_TOOL_RISK_TAGS.items():
        if tool_type.startswith(prefix):
            return "client", tags
    return "unknown", ()


def load_anthropic_artifacts(
    config: AnthropicConfig | None, base_dir: Path
) -> tuple[LoadedToolSource | None, AnthropicArtifacts | None]:
    if config is None:
        return None, None

    artifacts = AnthropicArtifacts()
    tools: list[Tool] = []

    for prompt_path in config.prompt_files:
        prompt = _resolve(base_dir, prompt_path)
        if prompt.suffix.lower() not in PROMPT_SUFFIXES:
            artifacts.warnings.append(
                f"Anthropic prompt file {prompt_path!r} is not markdown/text; "
                "loaded as UTF-8 text."
            )
        text = load_text_file(prompt)
        artifacts.prompt_files.append(_display_path(prompt, base_dir))
        artifacts.prompt_text = "\n\n".join(
            item for item in [artifacts.prompt_text, text] if item
        )

    for tool_ref in config.tools:
        loaded = _load_required_or_optional_with_positions(
            tool_ref, base_dir, artifacts.warnings, "tools"
        )
        if loaded is None:
            continue
        data, positions = loaded
        artifacts.tool_files.append(
            _display_path(_resolve(base_dir, tool_ref.path), base_dir)
        )
        if not isinstance(data, (list, dict)):
            raise InputParseError(
                "Anthropic tools artifact must be an object or list"
            )
        relative_path = manifest_relative_path(tool_ref.path, base_dir)
        for original_index, pointer, item in iter_tool_items(data):
            # Empty pointer ("") is a valid RFC 6901 root-document pointer
            # (singleton object form). Use ``is not None`` to keep the
            # root case from being skipped.
            pos = positions.lookup(pointer) if pointer is not None else None
            tool = _tool_from_anthropic_definition(
                item,
                source_ref=f"{tool_ref.path}#{original_index}",
                warnings=artifacts.warnings,
                skipped_server_tools=artifacts.skipped_server_tools,
                source_path=relative_path,
                source_pointer=pointer,
                source_position=pos,
            )
            if tool:
                tools.append(tool)

    for policy_ref in config.policy_rules:
        data = _load_required_or_optional(
            policy_ref, base_dir, artifacts.warnings, "policy_rules"
        )
        if data is None:
            continue
        if not isinstance(data, dict):
            raise InputParseError(
                f"Anthropic policy_rules file must contain an object: {policy_ref.path}"
            )
        artifacts.policy_rule_files.append(
            _display_path(_resolve(base_dir, policy_ref.path), base_dir)
        )
        _merge_policy_rules(
            artifacts.policy_rules,
            data,
            source_ref=policy_ref.path,
            warnings=artifacts.warnings,
        )

    return (
        LoadedToolSource(
            source_id="anthropic",
            source_type="anthropic_api",
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
        warnings.append(f"Optional Anthropic {kind} artifact {ref.path!r} failed to load.")
        return None


def _load_required_or_optional_with_positions(
    ref: ArtifactPathConfig,
    base_dir: Path,
    warnings: list[str],
    kind: str,
):
    path = _resolve(base_dir, ref.path)
    try:
        return load_structured_file_with_positions(path)
    except InputParseError:
        if not ref.optional:
            raise
        warnings.append(f"Optional Anthropic {kind} artifact {ref.path!r} failed to load.")
        return None


def _merge_policy_rules(
    target: dict[str, Any],
    incoming: dict[str, Any],
    *,
    source_ref: str,
    warnings: list[str],
) -> None:
    for key, value in incoming.items():
        if key not in target:
            target[key] = value
            continue
        warnings.append(
            f"Anthropic policy_rules key {key!r} from {source_ref!r} overlaps an earlier policy file; merging values."
        )
        target[key] = _merge_policy_value(target[key], value)


def _merge_policy_value(existing: Any, incoming: Any) -> Any:
    if isinstance(existing, list) and isinstance(incoming, list):
        return _merge_list_values(existing, incoming)
    if isinstance(existing, dict) and isinstance(incoming, dict):
        return {**existing, **incoming}
    return incoming


def _merge_list_values(existing: list[Any], incoming: list[Any]) -> list[Any]:
    merged: list[Any] = []
    seen: set[str] = set()
    for item in [*existing, *incoming]:
        marker = json.dumps(item, sort_keys=True, default=str)
        if marker in seen:
            continue
        merged.append(item)
        seen.add(marker)
    return merged


def _tool_from_anthropic_definition(
    data: dict[str, Any],
    *,
    source_ref: str,
    warnings: list[str],
    skipped_server_tools: list[dict[str, Any]],
    source_path: str | None = None,
    source_pointer: str | None = None,
    source_position: tuple[int, int] | None = None,
) -> Tool | None:
    if "function" in data and isinstance(data.get("function"), dict):
        warnings.append(
            f"Anthropic tool definition at {source_ref} contains a 'function' wrapper "
            "(OpenAI-style nesting); Anthropic Messages API tools are flat. Skipping."
        )
        return None

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        warnings.append(f"Skipping Anthropic tool without name at {source_ref}.")
        return None

    tool_type = data.get("type")
    typed_client_risk_tags: tuple[str, ...] = ()
    typed_client_kind: str | None = None
    if isinstance(tool_type, str) and tool_type != "custom":
        category, risk_tag_seed = _classify_anthropic_typed_tool(tool_type)
        if category == "server":
            # Anthropic-managed server tool (web_search, code_execution).
            # Schema is sandboxed on Anthropic's side; the user cannot
            # influence behavior, so static checks would be spurious.
            skipped_server_tools.append(
                {"name": name, "type": tool_type, "source_ref": source_ref}
            )
            warnings.append(
                f"Anthropic server-side tool {name!r} ({tool_type!r}) at {source_ref} skipped; "
                "managed Anthropic tools have no user-controlled schema."
            )
            return None
        if category == "unknown":
            # Forward-compat: a typed tool we don't yet classify. We skip
            # with an explicit warning so the user can either declare it
            # under `type: "custom"` (loaded as a normal tool) or wait for
            # adapter support. This avoids silently dropping high-risk
            # tools when Anthropic introduces a new type prefix.
            skipped_server_tools.append(
                {"name": name, "type": tool_type, "source_ref": source_ref}
            )
            warnings.append(
                f"Anthropic tool {name!r} at {source_ref} declares unrecognized "
                f"type {tool_type!r}; skipping. If this is a custom tool, omit "
                "`type` or set it to \"custom\" so the static checks can review it."
            )
            return None
        # Client tool: executes in the user's application code. We inventory
        # it with pre-populated risk tags so framework-agnostic checks
        # (approval, auth scope, owner, idempotency, ...) can fire correctly.
        typed_client_risk_tags = risk_tag_seed
        typed_client_kind = tool_type

    if "input_schema" not in data and "parameters" in data:
        warnings.append(
            f"Anthropic tool {name!r} at {source_ref} uses 'parameters' instead of "
            "'input_schema'; this looks like an OpenAI-converted definition. Skipping."
        )
        return None

    input_schema = data.get("input_schema")
    if input_schema is None:
        input_schema = {}
    if not isinstance(input_schema, dict):
        warnings.append(
            f"Skipping Anthropic tool {name!r} at {source_ref}; input_schema is not an object."
        )
        return None

    description = data.get("description")
    cache_control = data.get("cache_control")

    if not _ANTHROPIC_TOOL_NAME_RE.fullmatch(name):
        warnings.append(
            f"Anthropic tool name {name!r} at {source_ref} violates the documented "
            "regex ^[a-zA-Z0-9_-]{1,64}$; runtime requests will be rejected."
        )

    annotations: dict[str, Any] = {"anthropicTool": True}
    if cache_control is not None:
        annotations["anthropicCacheControl"] = cache_control
    if typed_client_kind is not None:
        annotations["anthropicClientTool"] = True
        annotations["anthropicToolType"] = typed_client_kind

    risk_hints: list[ToolRiskHint] = []
    for tag in typed_client_risk_tags:
        risk_hints.append(
            ToolRiskHint(
                tag=tag,
                source="anthropic_client_tool_type",
                confidence="high",
                evidence={"anthropic_tool_type": typed_client_kind},
            )
        )

    source_start_line: int | None = None
    source_start_column: int | None = None
    if source_position is not None:
        source_start_line, source_start_column = source_position
    # `source_location` stays None: legacy `path:line` strings live in
    # the `run_id` hash and v0.10 Anthropic tools never set it. The
    # structured fields below carry the line/pointer for reviewers.
    return Tool(
        id=stable_tool_id(name),
        name=name,
        description=description if isinstance(description, str) else None,
        source_type="anthropic_api",
        source_id="anthropic",
        source_ref=source_ref,
        source_path=source_path,
        source_start_line=source_start_line,
        source_start_column=source_start_column,
        source_pointer=source_pointer,
        input_schema=input_schema,
        parameters=schema_to_parameters(input_schema),
        annotations=annotations,
        risk_hints=risk_hints,
        extraction_confidence="high",
        extraction={"method": "anthropic_api_artifact", "confidence": "high"},
    )


def _resolve(base_dir: Path, value: str) -> Path:
    return resolve_input_path(base_dir, value)


def _display_path(path: Path, base_dir: Path) -> str:
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
