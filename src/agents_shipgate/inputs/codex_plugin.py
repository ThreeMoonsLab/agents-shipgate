from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Literal

from agents_shipgate.core.artifact_models import CodexPluginArtifacts
from agents_shipgate.core.domain import (
    AgentBindingObservation,
    LoadedToolSource,
    Tool,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.common import (
    PositionIndex,
    json_pointer_escape,
    list_input_directory,
    load_structured_file,
    load_structured_file_with_positions,
    load_text_file,
    manifest_relative_path,
    resolve_input_path,
)
from agents_shipgate.inputs.coverage import BoundaryCell, SourceCoverage
from agents_shipgate.inputs.mcp import load_mcp_tools
from agents_shipgate.inputs.protocol import LoadedAdapterResult
from agents_shipgate.schemas.codex_plugin import (
    CodexPluginAppSummary,
    CodexPluginComponentPathIssue,
    CodexPluginHookStub,
    CodexPluginMarketplaceSummary,
    CodexPluginMcpServerStub,
    CodexPluginSkillSummary,
    CodexPluginSourceLocation,
    CodexPluginSummary,
)
from agents_shipgate.schemas.manifest import (
    AgentsShipgateManifest,
    CodexPluginMcpInventoryConfig,
    ToolSourceConfig,
)

COMMAND_KEYS = {"command", "cmd", "run", "shell", "script"}
PLUGIN_MANIFEST = ".codex-plugin/plugin.json"
_CODEX_PLUGIN_COMPONENT_KEYS = {"apps", "hooks", "mcpServers", "skills"}
_CODEX_PLUGIN_METADATA_KEYS = {
    "author",
    "description",
    "homepage",
    "interface",
    "keywords",
    "license",
    "name",
    "repository",
    "version",
}
_KNOWN_CODEX_PLUGIN_KEYS = _CODEX_PLUGIN_COMPONENT_KEYS | _CODEX_PLUGIN_METADATA_KEYS


def _attributed(
    loaded: list[LoadedToolSource], source: ToolSourceConfig
) -> list[LoadedToolSource]:
    """Record which configured row produced these results, before anything joins on it.

    This adapter is per-scan, so the dispatcher cannot pass it the config object
    and attributes pass-2 results by the ``(adapter source type, minted source
    id)`` pair instead. Neither half of that pair survives here: an MCP
    inventory is minted as ``codex_plugin:<plugin>/<server>:inventory`` with
    source type ``codex_plugin_mcp_inventory``, so its tools carried no
    configured provenance at all and every source-wide declaration —
    ``authority`` since #410, ``binding`` since #432 — silently applied to
    nothing. ``binding`` is what made it visible, because it says out loud that
    the source contributed nothing when it plainly did.

    The loop above holds the configured row, which is the frame that knows;
    matching ids downstream is the join #410 already established cannot be
    trusted.
    """

    for entry in loaded:
        entry.configured_source_id = source.id
    return loaded


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
                loaded_sources.extend(_attributed(package_sources, source))
            elif source.mode == "marketplace":
                marketplace_sources = _load_marketplace_source(
                    source=source,
                    base_dir=base_dir,
                    artifacts=artifacts,
                    inventories=inventories,
                    seen_roots=seen_roots,
                    seen_names=seen_names,
                )
                loaded_sources.extend(_attributed(marketplace_sources, source))
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
                    configured_source_id=source.id,
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
    if artifacts.warnings or artifacts.component_path_issues or any(
        marketplace.skipped_entries for marketplace in artifacts.marketplaces
    ):
        # A package-root observation is a closed-world statement. If any part
        # of the configured Codex plugin source was skipped or degraded, keep
        # the binding graph incomplete instead of treating an observed skill
        # as proof that the entire callable surface is empty.
        for loaded_source in loaded_sources:
            if loaded_source.source_type == "codex_plugin":
                loaded_source.binding_observations = []
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


def resolve_local_codex_marketplace_roots(
    *,
    marketplace_path: Path,
    base_dir: Path,
) -> tuple[Path, ...]:
    """Resolve the contained local package roots declared by a marketplace.

    Stop at the package boundary: plugin contents remain the normal loader's
    responsibility so a malformed declared plugin still routes to ``verify``.
    """

    resolved_marketplace = resolve_input_path(base_dir, str(marketplace_path))
    data = load_structured_file(resolved_marketplace)
    if not isinstance(data, dict):
        raise InputParseError(
            f"Codex marketplace file must contain an object: {resolved_marketplace}"
        )
    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        raise InputParseError(
            "Codex marketplace file must contain a plugins array: "
            f"{resolved_marketplace}"
        )

    summary = CodexPluginMarketplaceSummary(
        source_id="coverage",
        name=data.get("name") if isinstance(data.get("name"), str) else None,
        path=manifest_relative_path(str(resolved_marketplace), base_dir),
        plugin_count=0,
    )
    roots: list[Path] = []
    for index, entry in enumerate(plugins):
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        root = _resolve_marketplace_plugin_root(
            entry=entry,
            base_dir=base_dir,
            marketplace=summary,
            pointer=f"/plugins/{index}",
        )
        if root is not None:
            roots.append(root.resolve())
    return tuple(dict.fromkeys(roots))


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
    unknown_keys = sorted(set(data) - _KNOWN_CODEX_PLUGIN_KEYS)
    if unknown_keys:
        artifacts.warnings.append(
            "Codex plugin manifest contains unrecognized top-level keys "
            f"{unknown_keys!r}; callable-surface completeness cannot be proven."
        )

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

    skill_start = len(artifacts.skills)
    app_start = len(artifacts.apps)
    mcp_start = len(artifacts.mcp_server_stubs)
    hook_start = len(artifacts.hook_stubs)
    issue_start = len(artifacts.component_path_issues)
    warning_start = len(artifacts.warnings)

    loaded_sources: list[LoadedToolSource] = []
    _load_skills(data, root, base_dir, name, artifacts)
    _load_apps(data, root, base_dir, name, artifacts)
    loaded_sources.extend(_load_mcp_servers(data, root, base_dir, name, artifacts, inventories))
    _load_hooks(data, root, base_dir, name, artifacts)
    if _is_complete_skill_only_package(
        data=data,
        plugin=plugin,
        skills=artifacts.skills[skill_start:],
        apps=artifacts.apps[app_start:],
        mcp_servers=artifacts.mcp_server_stubs[mcp_start:],
        hooks=artifacts.hook_stubs[hook_start:],
        component_path_issues=artifacts.component_path_issues[issue_start:],
        warnings=artifacts.warnings[warning_start:],
        has_declared_inventory=any(
            inventory_plugin == name for inventory_plugin, _ in inventories
        ),
    ):
        # Compatibility projection: the current binding schema names every
        # graph root an "agent". A skill-only plugin is instead a package root
        # whose fully parsed component graph proves that it exposes no
        # callable tools or handoffs. No reviewed agent_bindings declaration
        # is needed for this structural zero-capability fact.
        loaded_sources.append(
            LoadedToolSource(
                source_id=source_id,
                source_type="codex_plugin",
                binding_observations=[
                    AgentBindingObservation(
                        agent=f"codex-plugin:{name}",
                        source_id=source_id,
                        source=plugin.manifest_path,
                        source_pointer="",
                        tools_complete=True,
                        handoffs_complete=True,
                    )
                ],
            )
        )
    return loaded_sources


def _is_complete_skill_only_package(
    *,
    data: dict[str, Any],
    plugin: CodexPluginSummary,
    skills: list[CodexPluginSkillSummary],
    apps: list[CodexPluginAppSummary],
    mcp_servers: list[CodexPluginMcpServerStub],
    hooks: list[CodexPluginHookStub],
    component_path_issues: list[CodexPluginComponentPathIssue],
    warnings: list[str],
    has_declared_inventory: bool,
) -> bool:
    unknown_keys = set(data) - _KNOWN_CODEX_PLUGIN_KEYS
    non_skill_component_keys = (
        _CODEX_PLUGIN_COMPONENT_KEYS - {"skills"}
    ) & set(data)
    return bool(skills) and not any(
        (
            unknown_keys,
            non_skill_component_keys,
            plugin.missing_fields,
            plugin.name_mismatch,
            plugin.duplicate_root,
            plugin.duplicate_name,
            apps,
            mcp_servers,
            hooks,
            component_path_issues,
            warnings,
            has_declared_inventory,
            any(skill.missing_fields or skill.duplicate for skill in skills),
        )
    )


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
    resolved_manifest = resolve_input_path(base_dir, str(manifest_path))
    if not resolved_manifest.is_file():
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
    manifest_path = root / PLUGIN_MANIFEST
    try:
        resolved_manifest = resolve_input_path(base_dir, str(manifest_path))
    except InputParseError as exc:
        marketplace.skipped_entries.append(
            {"plugin": entry.get("name"), "reason": str(exc)}
        )
        return None
    if not resolved_manifest.is_file():
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
    skill_files: list[Path] = []
    for child in list_input_directory(path):
        if not child.is_dir():
            continue
        for candidate in list_input_directory(child):
            if candidate.name == "SKILL.md" and candidate.is_file():
                skill_files.append(candidate)
    return sorted(skill_files)


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


class CodexPluginAdapter:
    """``ToolSourceAdapter`` wrapping :func:`load_codex_plugin_artifacts`.

    Framework-scoped. The dispatcher invokes ``load()`` once per scan
    when either a ``tool_sources`` entry of type ``codex_plugin`` or
    the top-level ``manifest.codex_plugins`` section is configured.
    """

    source_type: ClassVar[str] = "codex_plugin"
    scope: ClassVar[Literal["per_source", "per_scan"]] = "per_scan"
    artifact_class: ClassVar[type | None] = CodexPluginArtifacts

    coverage: ClassVar[SourceCoverage] = SourceCoverage(
        adapter="codex_plugin",
        label="Codex plugin package / marketplace",
        reads=(
            "Plugin manifests, marketplace indexes, and the skills, apps, hooks, "
            "MCP server stubs, and MCP inventories a plugin ships."
        ),
        cells=(
            BoundaryCell(
                shape="export_artifact",
                status="extracted",
                reads=(
                    "An MCP tool inventory shipped inside the plugin and declared by "
                    "`codex_plugins.mcp_tool_inventories[]`, read through the same "
                    "loader as a standalone export."
                ),
                emits=("codex_plugin_mcp_inventory",),
                ceiling="high",
            ),
            BoundaryCell(
                shape="literal_registration",
                status="not_extracted",
                reads=(
                    "A plugin's MCP server stub names a server without a tool "
                    "contract. It is recorded as a plugin component and a host "
                    "boundary fact; no action enters the catalog, and a plugin whose "
                    "whole component graph parsed with no callable tool says so "
                    "structurally rather than by silence."
                ),
            ),
            BoundaryCell(
                shape="factory",
                status="not_applicable",
                reads="A plugin manifest declares components; it does not construct them.",
            ),
            BoundaryCell(
                shape="dynamic_construction",
                status="not_extracted",
                reads=(
                    "A marketplace entry that is unreadable or unnamed is recorded "
                    "as a skipped entry against its index position, so the count of "
                    "what was refused is published rather than absorbed."
                ),
            ),
        ),
    )

    def load(
        self,
        source: ToolSourceConfig | None,
        base_dir: Path,
        manifest: AgentsShipgateManifest,
    ) -> LoadedAdapterResult:
        loaded_sources, artifacts = load_codex_plugin_artifacts(manifest, base_dir)
        return LoadedAdapterResult(tool_sources=loaded_sources, artifact=artifacts)
