"""Changed inputs a host comparison does not read, named instead of left silent (#821).

A host comparison reads the files its readers recognise. A change to any
other file gives no row, and until this module it gave no word either: a pull
request that added a Cursor plugin's `mcp.json`, removed a guard from
`.cursor/hooks.json` or moved a marketplace plugin's pinned `sha` printed "No
static host-grant changes detected", exactly as a docs-only change does. On a
23-PR public corpus, none of the nine comparable zero-row results that changed
such a file named it.

This names the changed paths a bounded, documented rule set recognises as
plausibly agent configuration, and that no reader of this entry read. It
starts from the comparison's own changed-file set, never from a walk of the
repository, so an unchanged candidate is never named. It reads only what a
rule needs — whether a plugin manifest sits beside a file, and the text of a
plugin manifest's or marketplace's members — within fixed bounds, and never
beyond the repository. It fetches nothing, runs nothing and interprets
nothing: a name is not a grant, a row, a loading claim or a finding.

The rules, tried in this order; a path takes the first that names it:

1. **A plugin manifest** — `.claude-plugin/plugin.json`,
   `.codex-plugin/plugin.json`, `.cursor-plugin/plugin.json` or
   `.github/plugin/plugin.json` (Copilot), at any depth. Its `mcpServers`
   member, and except for Claude Code, whose `hooks` member is read (#714),
   its `hooks` member, are named when their text differs between the sides
   (``plugin_manifest_mcp_servers``, ``plugin_manifest_hooks``). A manifest
   that does not parse as a JSON object on a side it exists on is named whole
   (``unparsed_plugin_manifest``).
2. **A Claude Code marketplace** — `.claude-plugin/marketplace.json`, at any
   depth. Each `plugins[]` entry whose `source` is an object (`github`,
   `git`, `url` and the like) is compared as text by entry name, and one
   that was added, removed or changed is named with the source it now names
   (``external_plugin_source``). The source is never fetched.
3. **Cursor project hooks** — `.cursor/hooks.json`, at any depth
   (``cursor_project_hooks``).
4. **Host settings below the repository root** — `<dir>/.claude/settings.json`,
   `<dir>/.claude/settings.local.json`, `<dir>/.cursor/cli.json`,
   `<dir>/.cursor/mcp.json` or `<dir>/.vscode/mcp.json`, such as a dotfiles
   package's `claude/.claude/settings.json`. Whether it is a nested project's
   or a user-scope package's is not established (``nested_host_settings``).
5. **A plugin's MCP configuration** — `mcp.json` in a directory that holds a
   plugin manifest from rule 1 on the side it exists on
   (``plugin_mcp_config``).
6. **A hook file a plugin manifest names** — a file named like a hook
   declaration (`hooks.json`, `<name>-hooks.json`) that the `hooks` member of a
   Codex, Cursor or Copilot manifest in one of its eight nearest ancestor
   directories names by a relative path inside that plugin
   (``plugin_hook_file``).

A whole file (rules 3-6) is named only when no inventory of this comparison
published it, as an artifact, the file of a grant, or a blocking issue: a file
a reader read is already an item of its own. A member (rules 1-2) is named
whatever else read the file, because no reader reads that member. A path
under a directory the host readers never walk (`node_modules`, `.venv` and
the like) is not considered.

Bounds: at most :data:`MAX_UNREAD_CANDIDATES` candidate paths are examined,
in path order, and each file read is at most the host reader's own
per-file bound. A candidate is counted as not examined, rather than guessed
at, when it is past that bound, or when its rule needed a file it could not
use: a changed manifest or marketplace present on a side but not read within
its bound, or a manifest a hook file could be named by that was not read or
did not parse as a JSON object, while no readable one names it. The one count
covers both causes.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from collections.abc import Callable, Mapping, Sequence, Set
from dataclasses import dataclass, field
from typing import Any

from agents_shipgate.core.boundary_registry import (
    is_claude_plugin_marketplace_path,
    is_hook_declaration_file_name,
)
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.host_grants import (
    _WALK_SKIPPED_DIRECTORIES,
    _sanitize_sensitive_string,
    public_host_path,
)
from agents_shipgate.core.privacy import redact_text

#: Plugin manifests, relative to the plugin root, and the host each is for.
PLUGIN_MANIFESTS: tuple[tuple[str, str], ...] = (
    (".claude-plugin/plugin.json", "claude-code"),
    (".codex-plugin/plugin.json", "codex"),
    (".cursor-plugin/plugin.json", "cursor"),
    (".github/plugin/plugin.json", "copilot"),
)

#: Hosts whose plugin manifest's `hooks` member no reader of this entry reads.
#: A Claude Code manifest's is read, and the hooks it selects are rows (#714).
UNREAD_HOOKS_MEMBER_HOSTS = frozenset({"codex", "cursor", "copilot"})

#: Host settings files that are read at the repository root only.
NESTED_HOST_SETTINGS: tuple[tuple[str, str], ...] = (
    (".claude/settings.json", "claude-code"),
    (".claude/settings.local.json", "claude-code"),
    (".cursor/cli.json", "cursor"),
    (".cursor/mcp.json", "cursor"),
    (".vscode/mcp.json", "vscode"),
)

CURSOR_PROJECT_HOOKS = ".cursor/hooks.json"
PLUGIN_MCP_CONFIG = "mcp.json"

#: The most candidate paths one comparison examines (#821).
MAX_UNREAD_CANDIDATES = 32
#: How many ancestor directories of a hook file are searched for the plugin
#: manifest that names it.
MAX_MANIFEST_ANCESTORS = 8
#: The longest external-source description a coverage item publishes.
MAX_SOURCE_DETAIL_CHARS = 200
#: The longest marketplace entry name a coverage item's `source` carries,
#: before the digest that follows a name publishing changed.
MAX_ENTRY_NAME_CHARS = 100

_SIDES = ("base", "head")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
#: A lone surrogate: JSON's `\ud800` decodes to one, and no UTF-8 sink takes it.
_SURROGATE = re.compile(r"[\ud800-\udfff]")


@dataclass(frozen=True)
class ChangedInputs:
    """The comparison's changed paths and a bounded look at each side (#821).

    ``paths`` is every path the change touches — both names of a rename — or
    ``None`` when the set could not be listed. ``present(side, paths)`` answers
    which of ``paths`` exist on ``side`` (``"base"`` or ``"head"``) as a tree
    entry of any kind, and ``read(side, paths)`` returns the bytes of each that
    is a regular file within the read bound; a path missing from that answer
    was not read. Neither follows a link or leaves the repository.
    """

    paths: tuple[str, ...] | None
    present: Callable[[str, Sequence[str]], Set[str]] = lambda _side, _paths: set()
    read: Callable[[str, Sequence[str]], Mapping[str, bytes]] = lambda _side, _paths: {}


@dataclass
class UnreadDiscovery:
    """What discovery found: coverage facts, and how many candidates it did not examine."""

    examined: bool
    facts: list[dict[str, Any]] = field(default_factory=list)
    not_examined: int = 0


@dataclass(frozen=True)
class _Candidate:
    path: str
    rule: str
    #: The host of a manifest candidate.
    host: str | None = None


def _named(folded: str, suffix: str) -> bool:
    return folded == suffix or folded.endswith(f"/{suffix}")


def _join(root: str, relative: str) -> str:
    return f"{root}/{relative}" if root else relative


def _manifest_host(path: str) -> tuple[str, str] | None:
    """``(plugin root, host)`` when ``path`` is a plugin manifest."""

    folded = path.casefold()
    for suffix, host in PLUGIN_MANIFESTS:
        if _named(folded, suffix):
            return path[: len(path) - len(suffix)].rstrip("/"), host
    return None


def _classify(path: str) -> _Candidate | None:
    """The first rule that names ``path``, from its spelling alone."""

    parts = path.split("/")
    if not path or any(part in _WALK_SKIPPED_DIRECTORIES for part in parts[:-1]):
        return None
    folded = path.casefold()
    manifest = _manifest_host(path)
    if manifest is not None:
        return _Candidate(path, "manifest", manifest[1])
    if is_claude_plugin_marketplace_path(path):
        return _Candidate(path, "marketplace", "claude-code")
    if _named(folded, CURSOR_PROJECT_HOOKS):
        return _Candidate(path, "cursor_project_hooks", "cursor")
    for suffix, host in NESTED_HOST_SETTINGS:
        if folded != suffix and folded.endswith(f"/{suffix}"):
            return _Candidate(path, "nested_host_settings", host)
    if parts[-1].casefold() == PLUGIN_MCP_CONFIG:
        return _Candidate(path, "plugin_mcp_config")
    if is_hook_declaration_file_name(path):
        return _Candidate(path, "plugin_hook_file")
    return None


def _ancestors(path: str) -> list[str]:
    directories: list[str] = []
    current = posixpath.dirname(path)
    while len(directories) < MAX_MANIFEST_ANCESTORS:
        directories.append(current)
        if not current:
            break
        current = posixpath.dirname(current)
    return directories


def _hook_manifests(path: str) -> list[tuple[str, str, str]]:
    """``(manifest, plugin root, host)`` a hook file's `hooks` reference could come from."""

    return [
        (_join(root, suffix), root, host)
        for root in _ancestors(path)
        for suffix, host in PLUGIN_MANIFESTS
        if host in UNREAD_HOOKS_MEMBER_HOSTS
    ]


def _parsed(raw: bytes | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
        # `json.loads` accepts nesting that `json.dumps` then refuses with a
        # `RecursionError` (#821 review). A document that deep is read as one
        # that did not parse, whose members cannot be compared, never a crash.
        _canonical(value)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hook_references(manifest: dict[str, Any], root: str) -> set[str]:
    """The files a manifest's `hooks` names, folded, as paths in the repository."""

    value = manifest.get("hooks")
    references = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    paths: set[str] = set()
    for reference in references:
        if not isinstance(reference, str) or not reference.strip() or "\\" in reference:
            continue
        relative = posixpath.normpath(reference.strip())
        if relative.startswith("/") or relative == ".." or relative.startswith("../"):
            continue
        if relative != ".":
            paths.add(_join(root, relative).casefold())
    return paths


def _side(present_on: Sequence[str]) -> str | None:
    if len(present_on) == 2:
        return "both"
    return present_on[0] if present_on else None


def _published_text(value: str, limit: int = MAX_SOURCE_DETAIL_CHARS) -> str:
    """Repository text as a coverage item may publish it: redacted, one line, bounded.

    A lone surrogate is published as its `\\uXXXX` escape (#821 review cycle
    2): printed or written as itself, it made `diff` and `verify` fail on
    output.
    """

    text = _CONTROL.sub(" ", _sanitize_sensitive_string(redact_text(value) or ""))
    text = _SURROGATE.sub(lambda match: f"\\u{ord(match.group()):04x}", text)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _published_entry_name(name: str) -> str:
    """A marketplace entry name as a coverage item's `source` carries it (#821 review).

    `public_host_path` redacts a path piece by piece between `/`s, which is
    right for a path and wrong for a name: a name holding a URL with userinfo
    would pass through whole, and one of 5,000 characters would too. So the
    name is published as `detail` is, redacted as a whole, on one line and
    bounded. Whenever that changed it, a short digest of the exact name
    follows, as `public_host_path` stamps one, so two entries that publish
    alike stay two items. A name that needs none of it is published as written.
    The digest encodes with ``surrogatepass``, the one error handler that takes
    every lone surrogate a JSON name can hold (#821 review cycle 2).
    """

    text = _published_text(name, MAX_ENTRY_NAME_CHARS)
    if text == name:
        return name
    digest = hashlib.sha256(name.encode("utf-8", "surrogatepass")).hexdigest()[:12]
    return f"{text}~{digest}"


def external_source_text(source: dict[str, Any]) -> str:
    """What an external plugin source names, as text: its kind, where, and its pin (#821)."""

    def text(key: str) -> str | None:
        value = source.get(key)
        return value if isinstance(value, str) and value.strip() else None

    parts = [text("source") or "external"]
    location = text("repo") or text("url") or text("package")
    if location:
        parts.append(location)
    if text("path"):
        parts.append(f"path {text('path')}")
    pin = text("sha") or text("ref") or text("version")
    if pin:
        return _published_text(f"{' '.join(parts)} at {pin}")
    # Redacted before the suffix is added: a URL's sanitizer would otherwise
    # take the comma as part of the URL and redact it with the path.
    suffix = ", no ref pinned"
    return _published_text(" ".join(parts), MAX_SOURCE_DETAIL_CHARS - len(suffix)) + suffix


def _external_sources(marketplace: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Each entry name's object sources, in entry order (#821)."""

    plugins = marketplace.get("plugins")
    entries: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(plugins, list):
        return entries
    for index, entry in enumerate(plugins):
        if not isinstance(entry, dict) or not isinstance(entry.get("source"), dict):
            continue
        name = entry["name"] if isinstance(entry.get("name"), str) else str(index)
        entries.setdefault(name, []).append(entry["source"])
    return entries


def discover_unread_inputs(
    changed: ChangedInputs, *, read_by_entry: Callable[[str], bool]
) -> UnreadDiscovery:
    """The changed inputs no reader of this entry read, by the rules above (#821).

    ``read_by_entry(path)`` answers whether an inventory of this comparison
    already published the whole file. Returns coverage facts in the shape the
    comparator groups, with ``hosts`` as a set.
    """

    if changed.paths is None:
        return UnreadDiscovery(examined=False)
    candidates: list[_Candidate] = []
    for path in sorted(set(changed.paths)):
        candidate = _classify(path)
        if candidate is None:
            continue
        if candidate.rule not in {"manifest", "marketplace"} and read_by_entry(path):
            continue
        candidates.append(candidate)
    result = UnreadDiscovery(examined=True)
    result.not_examined = max(0, len(candidates) - MAX_UNREAD_CANDIDATES)
    candidates = candidates[:MAX_UNREAD_CANDIDATES]
    if not candidates:
        return result

    # One presence question and one read per side for every candidate. Only a
    # manifest or marketplace is ever read: a changed one for its members, and
    # a hook file's possible manifests for their `hooks`. A plugin's `mcp.json`
    # needs only to know that a manifest sits beside it.
    wanted: set[str] = set()
    readable: set[str] = set()
    for candidate in candidates:
        wanted.add(candidate.path)
        if candidate.rule in {"manifest", "marketplace"}:
            readable.add(candidate.path)
        elif candidate.rule == "plugin_mcp_config":
            root = posixpath.dirname(candidate.path)
            wanted.update(_join(root, suffix) for suffix, _host in PLUGIN_MANIFESTS)
        elif candidate.rule == "plugin_hook_file":
            manifests = {manifest for manifest, _root, _host in _hook_manifests(candidate.path)}
            wanted |= manifests
            readable |= manifests
    ordered = sorted(wanted)
    try:
        present = {side: set(changed.present(side, ordered)) & wanted for side in _SIDES}
        to_read = {side: sorted(present[side] & readable) for side in _SIDES}
        contents = {
            side: dict(changed.read(side, to_read[side])) if to_read[side] else {}
            for side in _SIDES
        }
    except (ConfigError, OSError, ValueError):
        # The paths were listed but the sides could not be looked at: no
        # candidate is named, and the list says so rather than implying none.
        return UnreadDiscovery(examined=False)

    def fact(source: str, side: str, hosts: set[str], candidate: str, detail: str | None = None):
        result.facts.append(
            {
                "source": public_host_path(source),
                "hosts": set(hosts),
                "side": side,
                "status": "changed_not_read",
                "rows": 0,
                "candidate": candidate,
                "detail": detail,
            }
        )

    for candidate in candidates:
        path = candidate.path
        sides = [side for side in _SIDES if path in present[side]]
        side = _side(sides)
        if side is None:
            continue
        if candidate.rule in {"cursor_project_hooks", "nested_host_settings"}:
            fact(path, side, {candidate.host or ""}, candidate.rule)
        elif candidate.rule == "plugin_mcp_config":
            root = posixpath.dirname(path)
            hosts = {
                host
                for suffix, host in PLUGIN_MANIFESTS
                for present_side in sides
                if _join(root, suffix) in present[present_side]
            }
            if hosts:
                fact(path, side, hosts, candidate.rule)
        elif candidate.rule == "plugin_hook_file":
            hosts: set[str] = set()
            unread = False
            for present_side in sides:
                for manifest, root, host in _hook_manifests(path):
                    if manifest not in present[present_side]:
                        continue
                    data = _parsed(contents[present_side].get(manifest))
                    if data is None:
                        # Not read within the bound, or not a JSON object:
                        # whether it names this file cannot be established.
                        unread = True
                        continue
                    if path.casefold() in _hook_references(data, root):
                        hosts.add(host)
            if hosts:
                fact(path, side, hosts, candidate.rule)
            elif unread:
                result.not_examined += 1
        else:
            _member_facts(candidate, sides, contents, fact, result, read_by_entry)
    return result


def _member_facts(
    candidate: _Candidate,
    sides: list[str],
    contents: dict[str, dict[str, bytes]],
    fact: Callable[..., None],
    result: UnreadDiscovery,
    read_by_entry: Callable[[str], bool],
) -> None:
    """Rules 1 and 2: the members of a changed manifest or marketplace whose text differs."""

    path = candidate.path
    if any(path not in contents[side] for side in sides):
        # Present but not read within the bound: nothing is guessed.
        result.not_examined += 1
        return
    parsed = {side: _parsed(contents[side][path]) for side in sides}
    if any(value is None for value in parsed.values()):
        if not read_by_entry(path):
            fact(path, _side(sides) or "both", {candidate.host or ""}, "unparsed_plugin_manifest")
        return
    if candidate.rule == "manifest":
        members = ["mcpServers"]
        if candidate.host in UNREAD_HOOKS_MEMBER_HOSTS:
            members.append("hooks")
        for member in members:
            texts = {
                side: _canonical(data[member])
                for side, data in parsed.items()
                if data is not None and member in data
            }
            # A side without the file, or without the member, has no text:
            # adding or removing either is a difference, as is an edit.
            if texts.get("base") == texts.get("head"):
                continue
            kind = "plugin_manifest_mcp_servers" if member == "mcpServers" else "plugin_manifest_hooks"
            fact(f"{path}#{member}", _side(sorted(texts)) or "both", {candidate.host or ""}, kind)
        return
    entries = {side: _external_sources(data) for side, data in parsed.items() if data is not None}
    for name in sorted({name for sources in entries.values() for name in sources}):
        texts = {
            side: [_canonical(source) for source in sources[name]]
            for side, sources in entries.items()
            if name in sources
        }
        if texts.get("base") == texts.get("head"):
            continue
        side = _side(sorted(texts)) or "both"
        described = entries["head" if "head" in texts else "base"][name][0]
        fact(
            f"{path}#plugins.{_published_entry_name(name)}",
            side,
            {"claude-code"},
            "external_plugin_source",
            detail=external_source_text(described),
        )


__all__ = [
    "MAX_UNREAD_CANDIDATES",
    "ChangedInputs",
    "UnreadDiscovery",
    "discover_unread_inputs",
    "external_source_text",
]
