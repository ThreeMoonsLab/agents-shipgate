from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_shipgate.config.schema import (
    AgentsShipgateManifest,
    CodexPluginMcpInventoryConfig,
    ToolSourceConfig,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.core.models import (
    CodexPluginAppSummary,
    CodexPluginArtifacts,
    CodexPluginComponentPathIssue,
    CodexPluginHookStub,
    CodexPluginMarketplaceSummary,
    CodexPluginMcpServerStub,
    CodexPluginSkillSummary,
    CodexPluginSourceLocation,
    CodexPluginSummary,
    LoadedToolSource,
    Tool,
)
from agents_shipgate.inputs.common import (
    PositionIndex,
    json_pointer_escape,
    load_structured_file_with_positions,
    load_text_file,
    manifest_relative_path,
    resolve_input_path,
)
from agents_shipgate.inputs.mcp import load_mcp_tools

COMMAND_KEYS = {"command", "cmd", "run", "shell", "script"}
PLUGIN_MANIFEST = ".codex-plugin/plugin.json"


def load_codex_plugin_artifacts(
    manifest: AgentsShipgateManifest,
    base_dir: Path,
) -> tuple[list[LoadedToolSource], CodexPluginArtifacts | None]:
    sources = [source for source in manifest.tool_sources if source.type == "codex_plugin"]
    if not sources:
        return [], None

    artifacts = CodexPluginArtifacts()
    loaded_sources: list[LoadedToolSource] = []
    seen_roots: dict[Path, CodexPluginSummary] = {}
    seen_names: dict[str, CodexPluginSummary] = {}
    inventories = {
        (entry.plugin, entry.server): entry
        for entry in (manifest.codex_plugins.mcp_tool_inventories if manifest.codex_plugins else [])
    }

    for source in sources:
        try:
            if (source.mode or "package") == "package":
                package_sources = _load_package_source(
                    source=source,
                    base_dir=base_dir,
                    artifacts=artifacts,
                    inventories=inventories,
                    seen_roots=seen_roots,
                    seen_names=seen_names,
                )
                loaded_sources.extend(package_sources)
            elif source.mode == "marketplace":
                marketplace_sources = _load_marketplace_source(
                    source=source,
                    base_dir=base_dir,
                    artifacts=artifacts,
                    inventories=inventories,
                    seen_roots=seen_roots,
                    seen_names=seen_names,
                )
                loaded_sources.extend(marketplace_sources)
            else:
                raise InputParseError(
                    f"Codex plugin source {source.id!r} has invalid mode "
                    f"{source.mode!r}; expected 'package' or 'marketplace'"
                )
        except InputParseError:
            if not source.optional:
                raise
            warning = f"Optional Codex plugin source {source.id!r} failed to load."
            artifacts.warnings.append(warning)
            loaded_sources.append(
                LoadedToolSource(
                    source_id=source.id,
                    source_type="codex_plugin",
                    warnings=[warning],
                )
            )

    artifacts.plugin_count = len(artifacts.plugins)
    artifacts.marketplace_count = len(artifacts.marketplaces)
    artifacts.skill_count = len(artifacts.skills)
    artifacts.app_count = len(artifacts.apps)
    artifacts.mcp_server_stub_count = len(artifacts.mcp_server_stubs)
    artifacts.hook_stub_count = len(artifacts.hook_stubs)
    artifacts.mcp_inventory_file_count = len(artifacts.mcp_inventory_files)
    artifacts.warnings = sorted(dict.fromkeys(artifacts.warnings))
    return loaded_sources, artifacts


def _load_package_source(
    *,
    source: ToolSourceConfig,
    base_dir: Path,
    artifacts: CodexPluginArtifacts,
    inventories: dict[tuple[str, str], CodexPluginMcpInventoryConfig],
    seen_roots: dict[Path, CodexPluginSummary],
    seen_names: dict[str, CodexPluginSummary],
) -> list[LoadedToolSource]:
    assert source.path is not None
    root, manifest_path = _resolve_package_root(base_dir, source.path, artifacts)
    return _load_plugin_package(
        source=source,
        base_dir=base_dir,
        root=root,
        manifest_path=manifest_path,
        marketplace_name=None,
        artifacts=artifacts,
        inventories=inventories,
        seen_roots=seen_roots,
        seen_names=seen_names,
    )


def _load_marketplace_source(
    *,
    source: ToolSourceConfig,
    base_dir: Path,
    artifacts: CodexPluginArtifacts,
    inventories: dict[tuple[str, str], CodexPluginMcpInventoryConfig],
    seen_roots: dict[Path, CodexPluginSummary],
    seen_names: dict[str, CodexPluginSummary],
) -> list[LoadedToolSource]:
    assert source.path is not None
    marketplace_path = resolve_input_path(base_dir, source.path)
    data, positions = load_structured_file_with_positions(marketplace_path)
    if not isinstance(data, dict):
        raise InputParseError(f"Codex marketplace file must contain an object: {marketplace_path}")
    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        raise InputParseError(f"Codex marketplace file must contain a plugins array: {marketplace_path}")
    marketplace_name = data.get("name") if isinstance(data.get("name"), str) else None
    summary = CodexPluginMarketplaceSummary(
        source_id=source.id,
        name=marketplace_name,
        path=manifest_relative_path(source.path, base_dir),
        plugin_count=0,
    )
    artifacts.marketplaces.append(summary)

    loaded: list[LoadedToolSource] = []
    for index, entry in enumerate(plugins):
        pointer = f"/plugins/{index}"
        if not isinstance(entry, dict):
            summary.skipped_entries.append({"index": index, "reason": "entry is not an object"})
            continue
        plugin_name = entry.get("name")
        if not isinstance(plugin_name, str) or not plugin_name.strip():
            summary.skipped_entries.append({"index": index, "reason": "entry has no name"})
            continue
        missing_policy = _missing_marketplace_policy(entry)
        if missing_policy:
            summary.missing_policy_entries.append(
                {
                    "plugin": plugin_name,
                    "missing": missing_policy,
                    "source_ref": f"{source.path}#{pointer}",
                }
            )
        root = _resolve_marketplace_plugin_root(
            entry=entry,
            base_dir=base_dir,
            marketplace=summary,
            pointer=pointer,
        )
        if root is None:
            continue
        summary.plugin_count += 1
        loaded.extend(
            _load_plugin_package(
                source=source,
                base_dir=base_dir,
                root=root,
                manifest_path=root / PLUGIN_MANIFEST,
                marketplace_name=marketplace_name or source.id,
                artifacts=artifacts,
                inventories=inventories,
                seen_roots=seen_roots,
                seen_names=seen_names,
            )
        )
    return loaded


def _load_plugin_package(
    *,
    source: ToolSourceConfig,
    base_dir: Path,
    root: Path,
    manifest_path: Path,
    marketplace_name: str | None,
    artifacts: CodexPluginArtifacts,
    inventories: dict[tuple[str, str], CodexPluginMcpInventoryConfig],
    seen_roots: dict[Path, CodexPluginSummary],
    seen_names: dict[str, CodexPluginSummary],
) -> list[LoadedToolSource]:
    root_resolved = root.resolve()
    if root_resolved in seen_roots:
        existing = seen_roots[root_resolved]
        existing.duplicate_root = True
        artifacts.warnings.append(
            f"Duplicate Codex plugin root {manifest_relative_path(str(root_resolved), base_dir)!r}; "
            f"kept source {existing.source_id!r}."
        )
        return []
    data, positions = load_structured_file_with_positions(manifest_path)
    if not isinstance(data, dict):
        raise InputParseError(f"Codex plugin manifest must contain an object: {manifest_path}")

    name = data.get("name") if isinstance(data.get("name"), str) else root.name
    source_id = f"codex_plugin:{source.id}/{name}"
    missing_fields = [
        field
        for field in ("name", "version", "description")
        if not isinstance(data.get(field), str) or not str(data.get(field)).strip()
    ]
    root_name = root.name
    plugin = CodexPluginSummary(
        source_id=source_id,
        name=name,
        root_path=manifest_relative_path(str(root_resolved), base_dir),
        manifest_path=manifest_relative_path(str(manifest_path), base_dir),
        version=data.get("version") if isinstance(data.get("version"), str) else None,
        description=(
            data.get("description") if isinstance(data.get("description"), str) else None
        ),
        marketplace=marketplace_name,
        missing_fields=missing_fields,
        name_mismatch=("name" not in missing_fields and name != root_name),
        location=_location(
            source_ref=manifest_relative_path(str(manifest_path), base_dir),
            source_path=manifest_relative_path(str(manifest_path), base_dir),
            pointer="",
            positions=positions,
        ),
    )
    if name in seen_names and seen_names[name].root_path != plugin.root_path:
        plugin.duplicate_name = True
        seen_names[name].duplicate_name = True
        artifacts.warnings.append(
            f"Codex plugin name {name!r} appears at multiple roots; kept both packages."
        )
    seen_names.setdefault(name, plugin)
    seen_roots[root_resolved] = plugin
    artifacts.plugins.append(plugin)

    loaded_sources: list[LoadedToolSource] = []
    _load_skills(data, root, base_dir, name, artifacts)
    _load_apps(data, root, base_dir, name, artifacts)
    loaded_sources.extend(_load_mcp_servers(data, root, base_dir, name, artifacts, inventories))
    _load_hooks(data, root, base_dir, name, artifacts)
    return loaded_sources


def _resolve_package_root(
    base_dir: Path,
    source_path: str,
    artifacts: CodexPluginArtifacts,
) -> tuple[Path, Path]:
    path = resolve_input_path(base_dir, source_path)
    if path.is_dir():
        root = path
        manifest_path = root / PLUGIN_MANIFEST
    elif path.name == "plugin.json" and path.parent.name == ".codex-plugin":
        root = path.parent.parent
        manifest_path = path
        artifacts.warnings.append(
            "Codex plugin source path points at .codex-plugin/plugin.json; "
            "prefer the plugin root directory."
        )
    else:
        raise InputParseError(
            f"Codex plugin source must be a plugin root directory or {PLUGIN_MANIFEST}: {path}"
        )
    if not manifest_path.exists():
        raise InputParseError(f"Codex plugin manifest not found: {manifest_path}")
    return root, manifest_path


def _resolve_marketplace_plugin_root(
    *,
    entry: dict[str, Any],
    base_dir: Path,
    marketplace: CodexPluginMarketplaceSummary,
    pointer: str,
) -> Path | None:
    source = entry.get("source")
    if not isinstance(source, dict):
        marketplace.skipped_entries.append(
            {"plugin": entry.get("name"), "reason": "missing source object"}
        )
        return None
    if source.get("source") != "local":
        marketplace.skipped_entries.append(
            {
                "plugin": entry.get("name"),
                "reason": "only local marketplace sources are statically supported",
                "source_ref": f"{marketplace.path}#{pointer}/source/source",
            }
        )
        return None
    path = source.get("path")
    if not isinstance(path, str) or not path.strip():
        marketplace.skipped_entries.append(
            {"plugin": entry.get("name"), "reason": "missing local source.path"}
        )
        return None
    try:
        root = resolve_input_path(base_dir, path)
    except InputParseError as exc:
        marketplace.skipped_entries.append(
            {"plugin": entry.get("name"), "reason": str(exc)}
        )
        return None
    if not (root / PLUGIN_MANIFEST).exists():
        marketplace.skipped_entries.append(
            {
                "plugin": entry.get("name"),
                "reason": f"plugin manifest not found at {path}/{PLUGIN_MANIFEST}",
            }
        )
        return None
    return root


def _missing_marketplace_policy(entry: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    policy = entry.get("policy")
    if not isinstance(policy, dict):
        missing.extend(["policy.installation", "policy.authentication"])
    else:
        if (
            not isinstance(policy.get("installation"), str)
            or not policy.get("installation")
        ):
            missing.append("policy.installation")
        if (
            not isinstance(policy.get("authentication"), str)
            or not policy.get("authentication")
        ):
            missing.append("policy.authentication")
    if not isinstance(entry.get("category"), str) or not entry.get("category"):
        missing.append("category")
    return missing


def _load_skills(
    data: dict[str, Any],
    root: Path,
    base_dir: Path,
    plugin_name: str,
    artifacts: CodexPluginArtifacts,
) -> None:
    paths = _component_paths(data, root, "skills", default="skills")
    skill_files: list[Path] = []
    for path in paths:
        resolved = _resolve_component_path(
            root=root,
            base_dir=base_dir,
            raw_path=path,
            plugin=plugin_name,
            component="skills",
            artifacts=artifacts,
        )
        if resolved is None:
            continue
        if resolved.is_dir():
            skill_files.extend(_skill_files(resolved))
        elif resolved.name == "SKILL.md":
            skill_files.append(resolved)
        else:
            artifacts.component_path_issues.append(
                CodexPluginComponentPathIssue(
                    plugin=plugin_name,
                    component="skills",
                    path=path,
                    reason="skills path is neither a directory nor SKILL.md",
                )
            )
    seen_skill_names: dict[str, CodexPluginSkillSummary] = {}
    for skill_file in sorted(dict.fromkeys(skill_files)):
        text = load_text_file(skill_file)
        metadata = _skill_frontmatter(text)
        missing = [
            field
            for field in ("name", "description")
            if not isinstance(metadata.get(field), str) or not metadata.get(field, "").strip()
        ]
        skill = CodexPluginSkillSummary(
            plugin=plugin_name,
            name=metadata.get("name") if isinstance(metadata.get("name"), str) else None,
            description=(
                metadata.get("description")
                if isinstance(metadata.get("description"), str)
                else None
            ),
            path=manifest_relative_path(str(skill_file), base_dir),
            missing_fields=missing,
            location=CodexPluginSourceLocation(
                source_ref=manifest_relative_path(str(skill_file), base_dir),
                source_path=manifest_relative_path(str(skill_file), base_dir),
            ),
        )
        if skill.name:
            existing = seen_skill_names.get(skill.name)
            if existing is not None:
                existing.duplicate = True
                skill.duplicate = True
            seen_skill_names.setdefault(skill.name, skill)
        artifacts.skills.append(skill)


def _load_apps(
    data: dict[str, Any],
    root: Path,
    base_dir: Path,
    plugin_name: str,
    artifacts: CodexPluginArtifacts,
) -> None:
    for path in _component_paths(data, root, "apps", default=".app.json"):
        resolved = _resolve_component_path(
            root=root,
            base_dir=base_dir,
            raw_path=path,
            plugin=plugin_name,
            component="apps",
            artifacts=artifacts,
        )
        if resolved is None:
            continue
        app_data, positions = load_structured_file_with_positions(resolved)
        apps = app_data.get("apps") if isinstance(app_data, dict) else None
        if not isinstance(apps, dict):
            artifacts.warnings.append(f"Codex plugin app file has no apps object: {path}")
            continue
        for app_name, raw_app in sorted(apps.items(), key=lambda item: str(item[0])):
            if not isinstance(raw_app, dict):
                artifacts.warnings.append(
                    f"Skipping non-object Codex app entry {app_name!r} in {path}"
                )
                continue
            pointer = f"/apps/{json_pointer_escape(str(app_name))}"
            artifacts.apps.append(
                CodexPluginAppSummary(
                    plugin=plugin_name,
                    name=str(app_name),
                    connector_id=raw_app.get("id") if isinstance(raw_app.get("id"), str) else None,
                    path=manifest_relative_path(str(resolved), base_dir),
                    location=_location(
                        source_ref=f"{manifest_relative_path(str(resolved), base_dir)}#{pointer}",
                        source_path=manifest_relative_path(str(resolved), base_dir),
                        pointer=pointer,
                        positions=positions,
                    ),
                )
            )


def _load_mcp_servers(
    data: dict[str, Any],
    root: Path,
    base_dir: Path,
    plugin_name: str,
    artifacts: CodexPluginArtifacts,
    inventories: dict[tuple[str, str], CodexPluginMcpInventoryConfig],
) -> list[LoadedToolSource]:
    loaded_sources: list[LoadedToolSource] = []
    for path in _component_paths(data, root, "mcpServers", default=".mcp.json"):
        resolved = _resolve_component_path(
            root=root,
            base_dir=base_dir,
            raw_path=path,
            plugin=plugin_name,
            component="mcpServers",
            artifacts=artifacts,
        )
        if resolved is None:
            continue
        mcp_data, positions = load_structured_file_with_positions(resolved)
        servers = mcp_data.get("mcpServers") if isinstance(mcp_data, dict) else None
        if not isinstance(servers, dict):
            artifacts.warnings.append(f"Codex plugin MCP file has no mcpServers object: {path}")
            continue
        for server_name, raw_server in sorted(servers.items(), key=lambda item: str(item[0])):
            if not isinstance(raw_server, dict):
                artifacts.warnings.append(
                    f"Skipping non-object Codex MCP server {server_name!r} in {path}"
                )
                continue
            inventory = inventories.get((plugin_name, str(server_name)))
            loaded_inventory, inventory_path = _load_mcp_inventory(
                inventory=inventory,
                base_dir=base_dir,
                plugin_name=plugin_name,
                server_name=str(server_name),
                artifacts=artifacts,
            )
            if loaded_inventory is not None:
                loaded_sources.append(loaded_inventory)
            pointer = f"/mcpServers/{json_pointer_escape(str(server_name))}"
            artifacts.mcp_server_stubs.append(
                CodexPluginMcpServerStub(
                    plugin=plugin_name,
                    server=str(server_name),
                    path=manifest_relative_path(str(resolved), base_dir),
                    command=(
                        raw_server.get("command")
                        if isinstance(raw_server.get("command"), str)
                        else None
                    ),
                    inventory_path=inventory_path,
                    inventory_loaded=loaded_inventory is not None,
                    location=_location(
                        source_ref=f"{manifest_relative_path(str(resolved), base_dir)}#{pointer}",
                        source_path=manifest_relative_path(str(resolved), base_dir),
                        pointer=pointer,
                        positions=positions,
                    ),
                )
            )
    return loaded_sources


def _load_mcp_inventory(
    *,
    inventory: CodexPluginMcpInventoryConfig | None,
    base_dir: Path,
    plugin_name: str,
    server_name: str,
    artifacts: CodexPluginArtifacts,
) -> tuple[LoadedToolSource | None, str | None]:
    if inventory is None:
        return None, None
    source_id = f"codex_plugin:{plugin_name}/{server_name}:inventory"
    source = ToolSourceConfig(
        id=source_id,
        type="mcp",
        path=inventory.path,
        optional=inventory.optional,
    )
    try:
        loaded = load_mcp_tools(source, base_dir)
    except InputParseError:
        if not inventory.optional:
            raise
        artifacts.warnings.append(
            f"Optional Codex plugin MCP inventory {inventory.path!r} failed to load."
        )
        return None, None
    inventory_path = manifest_relative_path(inventory.path, base_dir)
    artifacts.mcp_inventory_files.append(inventory_path)
    tools: list[Tool] = []
    for original in loaded.tools:
        tool = original.model_copy(deep=True)
        tool.source_type = "codex_plugin_mcp_inventory"
        tool.source_id = source_id
        tool.annotations["codex_plugin"] = plugin_name
        tool.annotations["codex_plugin_mcp_server"] = server_name
        tools.append(tool)
    return (
        LoadedToolSource(
            source_id=source_id,
            source_type="codex_plugin_mcp_inventory",
            tools=tools,
            warnings=loaded.warnings,
        ),
        inventory_path,
    )


def _load_hooks(
    data: dict[str, Any],
    root: Path,
    base_dir: Path,
    plugin_name: str,
    artifacts: CodexPluginArtifacts,
) -> None:
    for path in _component_paths(data, root, "hooks"):
        resolved = _resolve_component_path(
            root=root,
            base_dir=base_dir,
            raw_path=path,
            plugin=plugin_name,
            component="hooks",
            artifacts=artifacts,
        )
        if resolved is None:
            continue
        hook_data, positions = load_structured_file_with_positions(resolved)
        for pointer, key, command in _iter_hook_commands(hook_data):
            artifacts.hook_stubs.append(
                CodexPluginHookStub(
                    plugin=plugin_name,
                    name=key,
                    command=command,
                    path=manifest_relative_path(str(resolved), base_dir),
                    location=_location(
                        source_ref=f"{manifest_relative_path(str(resolved), base_dir)}#{pointer}",
                        source_path=manifest_relative_path(str(resolved), base_dir),
                        pointer=pointer,
                        positions=positions,
                    ),
                )
            )


def _component_paths(
    data: dict[str, Any],
    root: Path,
    key: str,
    *,
    default: str | None = None,
) -> list[str]:
    value = data.get(key)
    paths: list[str] = []
    if isinstance(value, str) and value.strip():
        paths.append(value)
    elif isinstance(value, list):
        paths.extend(item for item in value if isinstance(item, str) and item.strip())
    if not paths and default and (root / default).exists():
        paths.append(default)
    return paths


def _resolve_component_path(
    *,
    root: Path,
    base_dir: Path,
    raw_path: str,
    plugin: str,
    component: str,
    artifacts: CodexPluginArtifacts,
) -> Path | None:
    try:
        resolved = _resolve_plugin_path(root, raw_path)
    except InputParseError as exc:
        artifacts.component_path_issues.append(
            CodexPluginComponentPathIssue(
                plugin=plugin,
                component=component,
                path=raw_path,
                reason=str(exc),
            )
        )
        return None
    if not resolved.exists():
        artifacts.component_path_issues.append(
            CodexPluginComponentPathIssue(
                plugin=plugin,
                component=component,
                path=raw_path,
                reason="missing",
            )
        )
        return None
    try:
        resolved.relative_to(base_dir.resolve())
    except ValueError:
        artifacts.component_path_issues.append(
            CodexPluginComponentPathIssue(
                plugin=plugin,
                component=component,
                path=raw_path,
                reason="outside_manifest_dir",
            )
        )
        return None
    return resolved


def _resolve_plugin_path(root: Path, raw_path: str) -> Path:
    raw = Path(raw_path)
    candidate = raw if raw.is_absolute() else root / raw_path
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise InputParseError(
            f"Codex plugin component path {raw_path!r} resolves outside plugin root"
        ) from exc
    return resolved


def _skill_files(path: Path) -> list[Path]:
    if path.name == "SKILL.md":
        return [path]
    return sorted(child for child in path.glob("*/SKILL.md") if child.is_file())


def _skill_frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _iter_hook_commands(data: Any, pointer: str = "") -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    if isinstance(data, dict):
        for key, value in data.items():
            child_pointer = f"{pointer}/{json_pointer_escape(str(key))}"
            if key in COMMAND_KEYS and isinstance(value, str) and value.strip():
                found.append((child_pointer, str(key), value))
            found.extend(_iter_hook_commands(value, child_pointer))
    elif isinstance(data, list):
        for index, item in enumerate(data):
            found.extend(_iter_hook_commands(item, f"{pointer}/{index}"))
    return found


def _location(
    *,
    source_ref: str,
    source_path: str,
    pointer: str,
    positions: PositionIndex,
) -> CodexPluginSourceLocation:
    pos = positions.lookup(pointer)
    start_line: int | None = None
    start_column: int | None = None
    if pos is not None:
        start_line, start_column = pos
    return CodexPluginSourceLocation(
        source_ref=source_ref,
        source_path=source_path,
        source_pointer=pointer,
        source_start_line=start_line,
        source_start_column=start_column,
    )
