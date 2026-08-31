"""n8n adapter — top-level orchestrator + auxiliary loaders.

Internal module. Owns:

- ``N8nAdapter`` — the ``ToolSourceAdapter`` Protocol implementation
  registered in ``inputs.protocol.REGISTRY``.
- ``load_n8n_artifacts`` — the public per-scan entry point invoked by
  the dispatcher's pass-2 loop. Walks the manifest's ``n8n`` config,
  loads workflows / inventories / credential stubs / variable stubs /
  data-table schemas / execution samples / eval sets, and returns
  ``(loaded_sources, artifacts)``.
- Auxiliary loaders for the supplementary artifact paths:
  ``_load_inventory_ref`` (MCP-shaped tool inventories),
  ``_load_credential_stubs``, ``_load_structured_refs``,
  ``_artifact_paths``, ``_credential_entries``.

Workflow extraction itself lives in ``_workflows.py``; this module
just orchestrates the loading order and aggregates ``LoadedToolSource``
entries.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Literal

from agents_shipgate.core.artifact_models import N8nArtifacts
from agents_shipgate.core.domain import LoadedToolSource
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.common import (
    load_structured_file,
    resolve_input_path,
    walk_input_tree,
)
from agents_shipgate.inputs.coverage import BoundaryCell, SourceCoverage
from agents_shipgate.inputs.mcp import load_mcp_tools
from agents_shipgate.inputs.n8n._common import (
    _append_unique,
    _display_path,
    _skip_path,
    _string_or_none,
)
from agents_shipgate.inputs.n8n._workflows import _load_workflow_ref
from agents_shipgate.inputs.protocol import LoadedAdapterResult
from agents_shipgate.schemas.manifest import (
    AgentsShipgateManifest,
    ArtifactPathConfig,
    ToolInventoryConfig,
    ToolSourceConfig,
)


def load_n8n_artifacts(
    manifest: AgentsShipgateManifest,
    base_dir: Path,
) -> tuple[list[LoadedToolSource], N8nArtifacts | None]:
    config = manifest.n8n
    if config is None or not config.has_inputs():
        return [], None

    artifacts = N8nArtifacts()
    loaded_sources: list[LoadedToolSource] = []
    _load_credential_stubs(config.credential_stubs, base_dir, artifacts)
    _load_structured_refs(
        config.variable_stubs,
        base_dir,
        artifacts.variable_stub_files,
        artifacts.warnings,
        label="n8n variable stub",
    )
    _load_structured_refs(
        config.data_table_schemas,
        base_dir,
        artifacts.data_table_schema_files,
        artifacts.warnings,
        label="n8n data-table schema",
    )
    _load_structured_refs(
        config.execution_samples,
        base_dir,
        artifacts.execution_sample_files,
        artifacts.warnings,
        label="n8n execution sample",
    )
    _load_structured_refs(
        config.eval_sets,
        base_dir,
        artifacts.eval_files,
        artifacts.warnings,
        label="n8n eval set",
    )
    for inventory in config.tool_inventories:
        loaded = _load_inventory_ref(inventory, base_dir, artifacts)
        if loaded:
            loaded_sources.append(loaded)

    for workflow_ref in config.workflows:
        loaded_sources.extend(_load_workflow_ref(workflow_ref, base_dir, artifacts))

    return loaded_sources, artifacts


# --- Auxiliary loaders ------------------------------------------------------


def _load_inventory_ref(
    ref: ToolInventoryConfig,
    base_dir: Path,
    artifacts: N8nArtifacts,
) -> LoadedToolSource | None:
    source = ToolSourceConfig(
        id=f"n8n_inventory:{ref.path}",
        type="mcp",
        path=ref.path,
        optional=ref.optional,
    )
    try:
        loaded = load_mcp_tools(source, base_dir)
    except InputParseError:
        if not ref.optional:
            raise
        artifacts.warnings.append(f"Optional n8n tool inventory {ref.path!r} failed to load.")
        return None
    artifacts.tool_inventory_files.append(
        _display_path(resolve_input_path(base_dir, ref.path), base_dir)
    )
    for tool in loaded.tools:
        tool.source_type = "n8n_inventory"
        tool.annotations["n8n_inventory"] = True
    return LoadedToolSource(
        source_id=loaded.source_id,
        source_type="n8n_inventory",
        tools=loaded.tools,
        warnings=loaded.warnings,
        completes_source_id=ref.source_id,
        is_tool_inventory=True,
    )


def _load_credential_stubs(
    refs: list[ArtifactPathConfig],
    base_dir: Path,
    artifacts: N8nArtifacts,
) -> None:
    for path in _artifact_paths(refs, base_dir, artifacts.warnings, label="n8n credential stub"):
        data = load_structured_file(path)
        _append_unique(artifacts.credential_stub_files, _display_path(path, base_dir))
        for entry in _credential_entries(data):
            artifacts.credential_stubs.append(entry)


def _load_structured_refs(
    refs: list[ArtifactPathConfig],
    base_dir: Path,
    target: list[str],
    warnings: list[str],
    *,
    label: str,
) -> None:
    for path in _artifact_paths(refs, base_dir, warnings, label=label):
        load_structured_file(path)
        _append_unique(target, _display_path(path, base_dir))


def _artifact_paths(
    refs: list[ArtifactPathConfig],
    base_dir: Path,
    warnings: list[str],
    *,
    label: str,
) -> list[Path]:
    paths: list[Path] = []
    for ref in refs:
        try:
            path = resolve_input_path(base_dir, ref.path)
        except InputParseError:
            if not ref.optional:
                raise
            warnings.append(f"Optional {label} {ref.path!r} failed to load.")
            continue
        if not path.exists():
            if not ref.optional:
                raise InputParseError(f"Input file not found: {path}")
            warnings.append(f"Optional {label} {ref.path!r} failed to load.")
            continue
        if path.is_dir():
            paths.extend(
                sorted(
                    (
                        item
                        for item in walk_input_tree(path)
                        if item.is_file()
                        and item.suffix.lower() in {".json", ".yaml", ".yml"}
                        and not _skip_path(item, path)
                    ),
                    key=lambda item: _display_path(item, base_dir),
                )
            )
        else:
            paths.append(path)
    return paths


def _credential_entries(data: Any) -> list[dict[str, Any]]:
    raw_entries: list[Any]
    if isinstance(data, list):
        raw_entries = data
    elif isinstance(data, dict) and isinstance(data.get("credentials"), list):
        raw_entries = data["credentials"]
    elif isinstance(data, dict):
        raw_entries = [data]
    else:
        raw_entries = []
    entries: list[dict[str, Any]] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        name = _string_or_none(raw.get("name"))
        scopes = raw.get("scopes") or raw.get("oauthScopes") or []
        entries.append(
            {
                "id": _string_or_none(raw.get("id")),
                "type": _string_or_none(raw.get("type")),
                "name_present": bool(name),
                "scopes": [str(scope) for scope in scopes] if isinstance(scopes, list) else [],
            }
        )
    return entries


# --- N8nAdapter Protocol implementation --------------------------------------


class N8nAdapter:
    """``ToolSourceAdapter`` wrapping :func:`load_n8n_artifacts`.

    Manifest-only: ``source_type = "n8n"`` is NOT in
    ``ToolSourceConfig.type``'s Literal. Always invoked once per scan
    via the dispatcher's pass 2.
    """

    source_type: ClassVar[str] = "n8n"
    scope: ClassVar[Literal["per_source", "per_scan"]] = "per_scan"
    artifact_class: ClassVar[type | None] = N8nArtifacts

    coverage: ClassVar[SourceCoverage] = SourceCoverage(
        adapter="n8n",
        label="n8n workflows",
        reads=(
            "n8n workflow JSON exports, stubs, and reviewed inventories declared "
            "under `manifest.n8n`."
        ),
        manifest_section="n8n",
        cells=(
            BoundaryCell(
                shape="export_artifact",
                variant="reviewed inventory",
                status="extracted",
                reads=(
                    "A reviewed tool inventory in MCP export form, read as a "
                    "published contract."
                ),
                emits=("n8n_inventory",),
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
                emits=("n8n_inventory",),
                ceiling="high",
                surface_flags=("wildcard_tools",),
            ),
            BoundaryCell(
                shape="literal_registration",
                status="extracted",
                reads=(
                    "A tool node present in the workflow JSON — workflow, code, "
                    "HTTP, MCP client, or another AI tool node — read for its "
                    "parameters and credentials. The export names the node; the "
                    "called tool's own contract is not in the file."
                ),
                emits=(
                    "n8n_workflow_tool",
                    "n8n_code_tool",
                    "n8n_http_tool",
                    "n8n_mcp_client_tool",
                    "n8n_ai_tool",
                    "mcp",
                ),
                ceiling="medium",
            ),
            BoundaryCell(
                shape="factory",
                status="not_applicable",
                reads=(
                    "A workflow JSON has no construction step: a node is in the file "
                    "or it is not."
                ),
            ),
            BoundaryCell(
                shape="dynamic_construction",
                variant="expression-backed tool name",
                status="extracted",
                reads=(
                    "A tool node whose `toolName` (or workflow target) is an "
                    "n8n expression. The node still enters the catalog under "
                    "the expression text at `medium`, and the unresolved name "
                    "is recorded as a dynamic fact — the action is not hidden, "
                    "its identity is."
                ),
                emits=("n8n_workflow_tool",),
                ceiling="medium",
            ),
            BoundaryCell(
                shape="dynamic_construction",
                variant="MCP client wildcard",
                status="extracted",
                reads=(
                    "An MCP client node whose tool selection is `all`, `all_except`, "
                    "or unreadable: one `<node>.*` action stands in for a selection "
                    "the workflow does not enumerate."
                ),
                emits=("n8n_mcp_client_tool",),
                ceiling="medium",
                surface_flags=("wildcard_tools",),
            ),
        ),
    )

    def load(
        self,
        source: ToolSourceConfig | None,
        base_dir: Path,
        manifest: AgentsShipgateManifest,
    ) -> LoadedAdapterResult:
        loaded_sources, artifacts = load_n8n_artifacts(manifest, base_dir)
        return LoadedAdapterResult(tool_sources=loaded_sources, artifact=artifacts)
