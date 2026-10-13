from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

from agents_shipgate.core.artifact_models import CodexBoundaryArtifacts
from agents_shipgate.inputs.common import (
    manifest_relative_path,
    resolve_input_path,
    walk_input_tree,
)
from agents_shipgate.inputs.coverage import BoundaryCell, SourceCoverage
from agents_shipgate.inputs.mcp_manifest import (
    READ_MCP_CONFIG_FILES,
    UNREAD_HOST_MCP_CONFIG_FILES,
    load_codex_config_mcp_sources,
)
from agents_shipgate.inputs.protocol import LoadedAdapterResult
from agents_shipgate.schemas.manifest import AgentsShipgateManifest, ToolSourceConfig


def _code_list(names: tuple[str, ...]) -> str:
    quoted = [f"`{name}`" for name in names]
    return quoted[0] if len(quoted) == 1 else f"{', '.join(quoted[:-1])} and {quoted[-1]}"


#: What the coverage says this adapter reads, written from the tuples the
#: reader matches files with, so the two cannot drift apart (#1025).
_READS = (
    f"{_code_list(READ_MCP_CONFIG_FILES)}, at any depth under the root a "
    "`tool_sources[]` entry names — including the servers a plugin block of "
    "`.codex/config.toml` declares. Other hosts' MCP config files there "
    f"({_code_list(tuple(path for path, _host in UNREAD_HOST_MCP_CONFIG_FILES))}) "
    "are not read; each one present is named in a source warning."
)


class CodexConfigAdapter:
    source_type: ClassVar[str] = "codex_config"
    scope: ClassVar[Literal["per_source"]] = "per_source"
    artifact_class: ClassVar[type | None] = CodexBoundaryArtifacts

    coverage: ClassVar[SourceCoverage] = SourceCoverage(
        adapter="codex_config",
        label="Codex / MCP host config",
        reads=_READS,
        cells=(
            BoundaryCell(
                shape="export_artifact",
                status="not_applicable",
                reads=(
                    "Host config declares servers. A committed `tools/list` export "
                    "is configured as its own `mcp` source."
                ),
            ),
            BoundaryCell(
                shape="literal_registration",
                variant="tool with a schema",
                status="extracted",
                reads=(
                    "A server entry whose `tools` mapping names a tool and gives "
                    "it an input schema."
                ),
                emits=("codex_config_mcp",),
                ceiling="high",
            ),
            BoundaryCell(
                shape="literal_registration",
                variant="tool without a schema",
                status="extracted",
                reads=(
                    "A server entry naming a tool — in `tools` or an allowlist "
                    "such as `enabled_tools` — with no readable input schema. The "
                    "name is a fact; the surface it accepts is not."
                ),
                emits=("codex_config_mcp",),
                ceiling="medium",
                surface_flags=("mcp_unknown_schema",),
            ),
            BoundaryCell(
                shape="factory",
                status="not_applicable",
                reads="Host config declares servers; it does not construct them.",
            ),
            BoundaryCell(
                shape="dynamic_construction",
                status="extracted",
                reads=(
                    "A server entry that names no tools at all: one synthetic "
                    "`<server>.*` action stands in for a surface only the running "
                    "server can name."
                ),
                emits=("codex_config_mcp",),
                ceiling="medium",
                surface_flags=(
                    "wildcard_tools",
                    "mcp_wildcard_tools",
                    "mcp_unknown_schema",
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
        del manifest
        assert source is not None
        assert source.path is not None
        root = resolve_input_path(base_dir, source.path)
        if root.is_file():
            root = root.parent
        artifact = _collect_codex_boundary_artifacts(root, base_dir)
        mcp_sources = load_codex_config_mcp_sources(root, base_dir)
        return LoadedAdapterResult(
            tool_sources=mcp_sources,
            artifact=artifact,
            warnings=list(artifact.warnings),
        )


def _collect_codex_boundary_artifacts(root: Path, base_dir: Path) -> CodexBoundaryArtifacts:
    artifact = CodexBoundaryArtifacts(
        root_path=manifest_relative_path(str(root.resolve()), base_dir)
    )
    if not root.exists():
        artifact.warnings.append(f"Codex config root does not exist: {root}")
        return artifact
    for path in walk_input_tree(root):
        if not path.is_file():
            continue
        rel = _relative(path, root)
        if rel == ".codex/config.toml" or rel.endswith("/.codex/config.toml"):
            artifact.config_files.append(_relative(path, base_dir))
        elif rel == ".codex/hooks.json" or rel.endswith("/.codex/hooks.json"):
            artifact.hooks_files.append(_relative(path, base_dir))
        elif path.name in {"AGENTS.md", "AGENTS.override.md"}:
            artifact.agent_instruction_files.append(_relative(path, base_dir))
        elif (
            path.name == "SKILL.md"
            and (rel.startswith(".agents/skills/") or "/.agents/skills/" in rel)
        ):
            artifact.skill_files.append(_relative(path, base_dir))
        elif rel in {
            ".github/workflows/agents-shipgate.yml",
            ".github/workflows/agents-shipgate.yaml",
        } or rel.endswith("/.github/workflows/agents-shipgate.yml") or rel.endswith(
            "/.github/workflows/agents-shipgate.yaml"
        ):
            artifact.github_workflow_files.append(_relative(path, base_dir))
    return artifact


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())
