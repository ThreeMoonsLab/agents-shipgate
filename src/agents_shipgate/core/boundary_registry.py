"""Authoritative repository boundary-surface registry.

All local-control consumers use these predicates.  A path is classified once;
the actor passed to ``shipgate check`` never changes the evaluated surface.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents_shipgate.core.globbing import glob_match_ci


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
            glob_match_ci(pattern, normalized) for pattern in self.globs
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
            # Same document shape as `.codex/hooks.json`, which has been read
            # since the Codex adapter landed. A `SessionStart` command is
            # executable code around the agent, and it was invisible (#689).
            ".claude/hooks/hooks.json",
            ".mcp.json",
            "CLAUDE.md",
        ),
        globs=(
            "**/.claude/hooks/hooks.json",
            "**/.mcp.json",
            "**/CLAUDE.md",
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
            "**/AGENTS.md",
            "**/AGENTS.override.md",
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


def is_explicit_boundary_file_path(path: str) -> bool:
    """Named configuration files, excluding wildcard directory containers."""
    normalized = path.replace("\\", "/").removeprefix("./").casefold()
    return any(
        normalized in {item.casefold() for item in adapter.exact_paths}
        or any(
            not any(char in pattern.rsplit("/", 1)[-1] for char in "*?[")
            and glob_match_ci(pattern, normalized)
            for pattern in adapter.globs
        )
        for adapter in BOUNDARY_ADAPTERS
    )


def boundary_adapters_for_path(path: str) -> tuple[BoundaryAdapterSpec, ...]:
    return tuple(adapter for adapter in BOUNDARY_ADAPTERS if adapter.matches(path))


def host_config_adapters_for_path(path: str) -> tuple[BoundaryAdapterSpec, ...]:
    """Host configuration candidates, not generic governance or instructions.

    Discovery answers applicability from filenames only. Matching an adapter
    does not establish that a file parses or that its grants are understood.
    """
    normalized = path.replace("\\", "/").removeprefix("./").casefold()
    if not normalized.endswith((".json", ".toml")):
        return ()
    return tuple(
        adapter for adapter in boundary_adapters_for_path(normalized)
        if adapter.id != "shared"
        and not normalized.startswith((".claude/commands/", ".cursor/rules/"))
    )


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


def is_boundary_surface_path(path: str) -> bool:
    """Whether any adapter reads this path.

    The one place that answers "would a reader open this file", so a
    materialized base tree and the live reader agree on what the surface is
    (#686, #688). Directory prefixes count: a reader that walks `.claude/`
    needs the directory to exist before it can find `settings.json` in it.
    """

    normalized = path.replace("\\", "/").removeprefix("./")
    if any(adapter.matches(normalized) for adapter in BOUNDARY_ADAPTERS):
        return True
    folded = normalized.casefold()
    prefix = f"{folded}/"
    return any(
        expected.casefold().startswith(prefix)
        for adapter in BOUNDARY_ADAPTERS
        for expected in adapter.exact_paths
    )
