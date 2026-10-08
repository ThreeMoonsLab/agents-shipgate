"""Which host a repository `.mcp.json` belongs to, from what selects it (#936).

The boundary registry finds `.mcp.json` by its file name at any depth, and
that name alone used to make every one a Claude Code declaration. A
`.mcp.json` inside a Codex plugin was labelled `claude-code` although only
`plugins/codex/firecrawl/.codex-plugin/plugin.json` selects it
(firecrawl/firecrawl-mcp-server#468), and so was one beside a Grok plugin
manifest that no Claude Code file names (xai-org/plugin-marketplace#1205).

The file's servers are read exactly as before. Only the host they are
published under is decided here, from the declarations that select the file:

1. The repository's root `.mcp.json` is Claude Code's project MCP
   configuration.
2. A Claude Code plugin selects the `.mcp.json` at its plugin root: the
   directory of a `.claude-plugin/plugin.json`, or a `./` plugin source a
   `.claude-plugin/marketplace.json` in the repository lists. A manifest's or
   marketplace entry's `mcpServers` `./` path naming a `.mcp.json` selects
   that file too.
3. A Codex plugin selects what its `.codex-plugin/plugin.json` `mcpServers`
   names: a relative path or a list of them, inside the plugin directory,
   and `.mcp.json` at the plugin root when the member names no path. This is
   the rule the Codex plugin reader of `scan` applies
   (:func:`declared_component_paths`).
4. A `.mcp.json` none of those selects, in a directory that holds another
   plugin manifest — a format this entry does not read, such as
   `.grok-plugin/plugin.json`, `.cursor-plugin/plugin.json` or Copilot's
   `.github/plugin/plugin.json`, or a Codex manifest that does not parse or
   names other files — is published under :data:`UNATTRIBUTED_MCP_HOST`: its
   host is not established, and nothing makes it Claude Code's.
5. Any other `.mcp.json` keeps the registry's attribution, Claude Code, as a
   nested copy kept under review (unchanged).

Rules 1 to 3 add up: a file two hosts select is published under both. A
selection is what the repository declares, never that a plugin is installed
or that a host loaded the file, and no marketplace or plugin source outside
the repository is fetched. Matching a reference to a file is
case-insensitive, as the Claude Code plugin hook reader matches one (#714).
"""

from __future__ import annotations

import posixpath
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from agents_shipgate.core.boundary_registry import plugin_manifest_root

#: The file name the boundary registry reads as an MCP configuration at any depth.
MCP_CONFIG_NAME = ".mcp.json"
#: The host an MCP declaration is published under when what selects it is
#: not established (rule 4). Never a host this entry reads.
UNATTRIBUTED_MCP_HOST = "unknown"
#: The plugin manifest formats whose `mcpServers` this entry follows.
FOLLOWED_MCP_MANIFEST_FORMATS = frozenset({"claude-code", "codex"})


def is_mcp_config_path(path: str) -> bool:
    """A `.mcp.json`, at the root or under any directory."""

    return path.replace("\\", "/").rsplit("/", 1)[-1].casefold() == MCP_CONFIG_NAME


def declared_component_paths(value: Any) -> list[str]:
    """The paths a plugin manifest component member names, in order.

    A non-empty string, or the non-empty strings of a list. Anything else —
    an inline object, a number — names no path. Shared with the Codex plugin
    reader of `scan`, which then falls back to the component's default file.
    """

    if isinstance(value, str) and value.strip():
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []


def plugin_reference(root: str, reference: str, *, require_dot_slash: bool) -> str | None:
    """A relative reference inside a plugin directory, as a repository path.

    ``None`` for a reference this entry does not follow: a backslash, an
    absolute path, a URL-like or drive-like value, or one that leaves the
    plugin directory. Claude Code documents `./` paths only, so
    ``require_dot_slash`` refuses any other spelling.
    """

    text = reference.strip()
    if not text or "\\" in text or ":" in text:
        return None
    if text.startswith("./"):
        text = text[2:]
    elif require_dot_slash:
        return None
    if text.startswith("/"):
        return None
    relative = posixpath.normpath(text or ".")
    if relative in {".", ".."} or relative.startswith("../"):
        return None
    return posixpath.join(root, relative) if root else relative


def codex_mcp_targets(manifest: str, data: dict[str, Any]) -> list[str]:
    """The repository paths a Codex plugin manifest's `mcpServers` selects (rule 3)."""

    located = plugin_manifest_root(manifest)
    root = located[0] if located is not None else posixpath.dirname(posixpath.dirname(manifest))
    references = declared_component_paths(data.get("mcpServers")) or [f"./{MCP_CONFIG_NAME}"]
    targets = (plugin_reference(root, item, require_dot_slash=False) for item in references)
    return [target for target in targets if target is not None]


def followed_mcp_member(manifest: str, data: Any) -> list[str] | None:
    """The `.mcp.json` files a manifest's `mcpServers` member names, when that is all it holds.

    ``None`` when the manifest is not a format whose member this entry
    follows, or when the member holds anything this entry does not follow: an
    inline object, a reference it refuses, or a file not named `.mcp.json`.
    An absent member is ``[]``. Used to stop naming the member as unread
    (#821) once every file it names is read.
    """

    located = plugin_manifest_root(manifest)
    if located is None or located[1] not in FOLLOWED_MCP_MANIFEST_FORMATS:
        return None
    if not isinstance(data, dict):
        return None
    if "mcpServers" not in data:
        return []
    value = data["mcpServers"]
    references = declared_component_paths(value)
    if not references or (isinstance(value, list) and len(references) != len(value)):
        return None
    targets: list[str] = []
    for reference in references:
        target = plugin_reference(
            located[0], reference, require_dot_slash=located[1] == "claude-code",
        )
        if target is None or not is_mcp_config_path(target):
            return None
        targets.append(target)
    return targets


@dataclass(frozen=True)
class McpHostSelection:
    """The hosts one `.mcp.json` is published under, and why none was established."""

    hosts: tuple[str, ...]
    #: For :data:`UNATTRIBUTED_MCP_HOST` only: each plugin manifest in the
    #: file's directory and why it attributes the file to no host.
    unattributed_by: tuple[tuple[str, str], ...] = ()


#: Why a manifest beside a `.mcp.json` attributes it to no host (rule 4).
UNREAD_FORMAT = "a plugin manifest format this entry does not read"
NOT_AN_OBJECT = "a Codex plugin manifest that could not be read as a JSON object"
NOT_SELECTED = "a Codex plugin manifest whose mcpServers does not name this file"


def select_mcp_hosts(
    *,
    sources: Iterable[str],
    manifests: Mapping[str, Any],
    claude_roots: Collection[str] = (),
    claude_references: Collection[str] = (),
) -> dict[str, McpHostSelection]:
    """Decide the hosts of every `.mcp.json` in ``sources`` (rules 1-5).

    ``manifests`` maps every plugin manifest in the repository to its parsed
    content when it is a Codex manifest that parsed, and to ``None``
    otherwise. ``claude_roots`` are the plugin directories a Claude Code
    marketplace in the repository lists, and ``claude_references`` the files
    a Claude Code manifest's or marketplace entry's `mcpServers` names.
    """

    located = {
        manifest: where
        for manifest in manifests
        if (where := plugin_manifest_root(manifest)) is not None
    }
    claude = {root.casefold() for root in claude_roots} | {
        root.casefold() for root, fmt in located.values() if fmt == "claude-code"
    }
    referenced_by_claude = {path.casefold() for path in claude_references}
    codex: set[str] = set()
    for manifest, (_root, fmt) in located.items():
        data = manifests[manifest]
        if fmt == "codex" and isinstance(data, dict):
            codex.update(target.casefold() for target in codex_mcp_targets(manifest, data))

    selections: dict[str, McpHostSelection] = {}
    for source in sources:
        if not is_mcp_config_path(source):
            continue
        folded = source.replace("\\", "/").removeprefix("./").casefold()
        directory = posixpath.dirname(folded)
        hosts: set[str] = set()
        if folded == MCP_CONFIG_NAME or directory in claude or folded in referenced_by_claude:
            hosts.add("claude-code")
        if folded in codex:
            hosts.add("codex")
        if hosts:
            selections[source] = McpHostSelection(hosts=tuple(sorted(hosts)))
            continue
        beside = sorted(
            manifest for manifest, (root, _fmt) in located.items() if root.casefold() == directory
        )
        if not beside:
            selections[source] = McpHostSelection(hosts=("claude-code",))
            continue
        reasons = []
        for manifest in beside:
            fmt = located[manifest][1]
            if fmt != "codex":
                reasons.append((manifest, UNREAD_FORMAT))
            elif not isinstance(manifests[manifest], dict):
                reasons.append((manifest, NOT_AN_OBJECT))
            else:
                reasons.append((manifest, NOT_SELECTED))
        selections[source] = McpHostSelection(
            hosts=(UNATTRIBUTED_MCP_HOST,), unattributed_by=tuple(reasons),
        )
    return selections


__all__ = [
    "FOLLOWED_MCP_MANIFEST_FORMATS",
    "MCP_CONFIG_NAME",
    "UNATTRIBUTED_MCP_HOST",
    "McpHostSelection",
    "codex_mcp_targets",
    "declared_component_paths",
    "followed_mcp_member",
    "is_mcp_config_path",
    "plugin_reference",
    "select_mcp_hosts",
]
