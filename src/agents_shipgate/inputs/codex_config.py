from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

from agents_shipgate.core.artifact_models import CodexBoundaryArtifacts
from agents_shipgate.inputs.common import (
    manifest_relative_path,
    resolve_input_path,
    walk_input_tree,
)
from agents_shipgate.inputs.mcp_manifest import load_codex_config_mcp_sources
from agents_shipgate.inputs.protocol import LoadedAdapterResult
from agents_shipgate.schemas.manifest import AgentsShipgateManifest, ToolSourceConfig


class CodexConfigAdapter:
    source_type: ClassVar[str] = "codex_config"
    scope: ClassVar[Literal["per_source"]] = "per_source"
    artifact_class: ClassVar[type | None] = CodexBoundaryArtifacts

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
