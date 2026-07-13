"""Authoritative repository boundary-surface registry.

All local-control consumers use these predicates.  A path is classified once;
the actor passed to ``shipgate check`` never changes the evaluated surface.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents_shipgate.core.globbing import glob_match


@dataclass(frozen=True)
class BoundaryAdapterSpec:
    id: str
    hosts: tuple[str, ...]
    exact_paths: tuple[str, ...] = ()
    globs: tuple[str, ...] = ()
    experimental: bool = False

    def matches(self, path: str) -> bool:
        normalized = path.replace("\\", "/").removeprefix("./").casefold()
        return any(normalized == item.casefold() for item in self.exact_paths) or any(
            glob_match(pattern.casefold(), normalized) for pattern in self.globs
        )


BOUNDARY_ADAPTERS: tuple[BoundaryAdapterSpec, ...] = (
    BoundaryAdapterSpec(
        id="codex",
        hosts=("codex",),
        exact_paths=(
            ".codex/config.toml",
            ".codex/hooks.json",
            ".codex/requirements.toml",
        ),
        globs=(
            "**/.codex/config.toml",
            "**/.codex/hooks.json",
            "**/.codex/requirements.toml",
        ),
    ),
    BoundaryAdapterSpec(
        id="claude_code",
        hosts=("claude-code",),
        exact_paths=(
            ".claude/settings.json",
            ".claude/settings.local.json",
            ".mcp.json",
            "CLAUDE.md",
        ),
        globs=(
            "**/.mcp.json",
            ".claude/commands/*",
            ".claude/commands/**",
            ".claude/skills/*/SKILL.md",
            ".claude/skills/**/SKILL.md",
        ),
    ),
    BoundaryAdapterSpec(
        id="cursor",
        hosts=("cursor",),
        exact_paths=(".cursor/cli.json", ".cursor/mcp.json"),
        globs=(".cursor/rules/*", ".cursor/rules/**"),
    ),
    BoundaryAdapterSpec(
        id="vscode_mcp",
        hosts=("vscode",),
        exact_paths=(".vscode/mcp.json",),
        experimental=True,
    ),
    BoundaryAdapterSpec(
        id="shared",
        hosts=("codex", "claude-code", "cursor"),
        exact_paths=(
            "AGENTS.md",
            "AGENTS.override.md",
            "shipgate.yaml",
            ".shipgate/agent-contract.json",
            "policies/agent-boundary.shipgate.yaml",
            "policies/codex-boundary.shipgate.yaml",
            "policies/host-boundary.shipgate.yaml",
        ),
        globs=(
            ".agents/skills/*/SKILL.md",
            ".agents/skills/**/SKILL.md",
            ".github/workflows/*.yml",
            ".github/workflows/*.yaml",
            "**/.github/workflows/*.yml",
            "**/.github/workflows/*.yaml",
            ".agents-shipgate/baseline*.json",
            ".agents-shipgate/*waiver*.json",
            ".agents-shipgate/state*.json",
            "policies/*.shipgate.yaml",
        ),
    ),
)


def boundary_adapters_for_path(path: str) -> tuple[BoundaryAdapterSpec, ...]:
    return tuple(adapter for adapter in BOUNDARY_ADAPTERS if adapter.matches(path))


def boundary_hosts_for_path(path: str) -> tuple[str, ...]:
    return tuple(
        sorted({host for adapter in boundary_adapters_for_path(path) for host in adapter.hosts})
    )


def is_agent_boundary_path(path: str) -> bool:
    return bool(boundary_adapters_for_path(path))


__all__ = [
    "BOUNDARY_ADAPTERS",
    "BoundaryAdapterSpec",
    "boundary_adapters_for_path",
    "boundary_hosts_for_path",
    "is_agent_boundary_path",
]
