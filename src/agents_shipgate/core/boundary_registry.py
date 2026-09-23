"""Authoritative repository boundary-surface registry.

All local-control consumers use these predicates.  A path is classified once;
the actor passed to ``shipgate check`` never changes the evaluated surface.
"""

from __future__ import annotations

import re
from collections.abc import Collection
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


#: Where a Claude Code plugin keeps its manifest, relative to the plugin root
#: (code.claude.com/docs/en/plugins-reference).
CLAUDE_PLUGIN_MANIFEST = ".claude-plugin/plugin.json"
#: The hook configuration a plugin loads by default, relative to its root.
CLAUDE_PLUGIN_DEFAULT_HOOKS = "hooks/hooks.json"


def is_claude_plugin_manifest_path(path: str) -> bool:
    """A `.claude-plugin/plugin.json`, at the root or under any directory."""

    folded = path.replace("\\", "/").removeprefix("./").casefold()
    return folded == CLAUDE_PLUGIN_MANIFEST or folded.endswith(f"/{CLAUDE_PLUGIN_MANIFEST}")


#: Where a Claude Code marketplace lists its plugins, relative to the
#: marketplace root (code.claude.com/docs/en/plugin-marketplaces).
CLAUDE_PLUGIN_MARKETPLACE = ".claude-plugin/marketplace.json"


def is_claude_plugin_marketplace_path(path: str) -> bool:
    """A `.claude-plugin/marketplace.json`, at the root or under any directory."""

    folded = path.replace("\\", "/").removeprefix("./").casefold()
    return folded == CLAUDE_PLUGIN_MARKETPLACE or folded.endswith(f"/{CLAUDE_PLUGIN_MARKETPLACE}")


_HOOK_DECLARATION_NAME = re.compile(r"(?:^|[-_.])hooks\.json$")


def is_hook_declaration_file_name(path: str) -> bool:
    """A file named like a hook declaration (`hooks.json`, `security-hooks.json`).

    Only such a file is followed from a plugin's `hooks` reference (#714). The
    name is what lets a base tree be materialized from paths alone; a
    reference to any other name is recorded as a limit instead. `hooks.json`
    must start the name or follow a `-`, `_` or `.`, so `webhooks.json` is not
    one.
    """

    name = path.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    return bool(_HOOK_DECLARATION_NAME.search(name))


def is_claude_plugin_reference_path(path: str) -> bool:
    """A file the Claude Code plugin hook reader may open (#714).

    A plugin manifest or marketplace, read to learn which hooks a plugin
    selects, or a hook-named file, read when one selects it. Decided from the
    name alone, so a materialized tree and a change set can both be scoped by
    it without an inventory.
    """

    normalized = path.replace("\\", "/").removeprefix("./")
    return (
        is_claude_plugin_manifest_path(normalized)
        or is_claude_plugin_marketplace_path(normalized)
        or is_hook_declaration_file_name(normalized)
    )


def is_enabled_plugin_hook_source(path: str, enabled_sources: Collection[str]) -> bool:
    """Whether `check` routes a change to this path as a hook the host loads (#809).

    A Claude Code settings layer, `.codex/hooks.json` and
    `.claude/hooks/hooks.json` are registry paths, so a change to one reaches
    protected-surface review from its name. A plugin's hook declaration has no
    registry path: a plugin selects a hook file anywhere in the repository, or
    writes hooks inline in its manifest or marketplace entry. The route here
    takes two facts, and neither is enough alone:

    * the path is one the plugin hook reader opens
      (:func:`is_claude_plugin_reference_path`), which the name decides; and
    * the reader found that it declares hooks of a plugin this repository's
      own project settings enable from an in-repository marketplace, on a
      side of the change (``enabled_sources``, from
      ``HostBoundarySnapshot.enabled_plugin_hook_sources``).

    The second is the evidence the host inventory already publishes as the
    ``project_enabled_plugin`` basis, read from the selection rather than
    back from a grant's `access`/`risk` pair. The name alone would route every
    plugin hook file, including one a plugin only selects, whose loading is
    external state and which #714 made a row rather than an expansion; the
    evidence alone would route a path the reader does not open. The decision
    is the existing protected-surface rule's, and the rows stay the host
    comparison's, so this adds no second decision engine.
    """

    normalized = path.replace("\\", "/").removeprefix("./")
    if not is_claude_plugin_reference_path(normalized):
        return False
    folded = normalized.casefold()
    return any(folded == source.casefold() for source in enabled_sources)


def is_boundary_surface_path(path: str) -> bool:
    """Whether any reader may open this path.

    The one place that answers "would a reader open this file", so a
    materialized base tree and the live reader agree on what the surface is
    (#686, #688). Directory prefixes count: a reader that walks `.claude/`
    needs the directory to exist before it can find `settings.json` in it.

    Plugin manifests, marketplaces and hook-named files are included although
    no adapter names them (#714): the reader opens a manifest or marketplace
    to learn which hook file a plugin selects, and opens that file only when
    one selects it. They are not registry surfaces, so the triggers do not
    route them, and `check` routes one only where a plugin the repository's
    project settings enable loads hooks from it
    (:func:`is_enabled_plugin_hook_source`, #809).
    """

    normalized = path.replace("\\", "/").removeprefix("./")
    if any(adapter.matches(normalized) for adapter in BOUNDARY_ADAPTERS):
        return True
    if is_claude_plugin_reference_path(normalized):
        return True
    folded = normalized.casefold()
    prefix = f"{folded}/"
    return any(
        expected.casefold().startswith(prefix)
        for adapter in BOUNDARY_ADAPTERS
        for expected in adapter.exact_paths
    )
