"""Discovery for MCP servers whose tool surface exists only as code (#431).

``detect`` classified the official MongoDB and Grafana MCP servers as "not an
agent project". Both publish dozens of tools — ``drop-database``,
``delete-many``, ``update_incident`` — and neither commits an export, so every
route discovery had was filename-shaped and found nothing. This module supplies
the missing route: it asks whether a workspace *is* an MCP server written in
TypeScript or Go, and if so which directory holds the registrations.

Two facts have to hold together before the route is offered, and the pairing is
the whole design.

**Declared framework evidence.** A repository that declares no MCP dependency
is not an MCP server just because a class of its own happens to spell a field
``toolName``. #393's lesson is that a proof resting on a spelling is the
fail-open shape, so the dependency — ``@modelcontextprotocol/*`` in a
``package.json``, an MCP module in a ``go.mod`` — is what turns a spelling into
provenance. It is also the cheap half: a workspace with no such dependency is
answered without reading a single source file.

**A resolved registration.** Dependency evidence alone says the repository
*uses* MCP, which every client does too. At least one tool name has to be
readable at a registration site before this claims a tool surface.

Where both hold and the workspace *also* has a parseable MCP export, the export
wins: it is the server's own published contract, it carries the input schemas
this route deliberately does not read, and it is ``high`` confidence against
``medium``. The source route is then withheld and recorded in
``excluded_sources`` — named and visible, never silently dropped, because a
route that vanishes without a reason is indistinguishable from one nobody
implemented.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.common import load_text_file
from agents_shipgate.inputs.mcp_idioms import (
    GO_FRAMEWORK_MODULES,
    MAX_SOURCE_FILE_BYTES,
    SKIP_DIRECTORY_NAMES,
    TYPESCRIPT_FRAMEWORK_PACKAGES,
    SourceLanguage,
    is_scannable_path,
    language_for_path,
    scan_source,
)
from agents_shipgate.inputs.mcp_server_source import SOURCE_TYPE

#: How many source files discovery reads before it stops. Discovery runs on an
#: unknown workspace with no manifest and has to stay bounded; the scan-time
#: adapter has its own, larger cap. Truncation is reported, never silent.
DEFAULT_MAX_SOURCE_FILES = 1500

#: How many tool names the detection evidence names before it summarises. The
#: evidence line is read by a human deciding whether to adopt, and 110 names is
#: not evidence, it is a wall.
_EVIDENCE_NAME_LIMIT = 5

#: Dependency sections of a ``package.json`` that count. ``devDependencies`` is
#: included on purpose: a server that bundles the SDK declares it there as
#: often as not, and the question here is "was this repository written against
#: MCP", not "does it ship the SDK at runtime".
_PACKAGE_JSON_DEPENDENCY_KEYS = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)


@dataclass(frozen=True)
class McpSourceDiscovery:
    """What discovery concluded about the workspace's own registration sites."""

    #: Workspace-relative directory to point ``tool_sources[].path`` at, or
    #: ``None`` when no route was found.
    path: str | None = None
    #: Languages whose idioms resolved at least one name.
    languages: tuple[SourceLanguage, ...] = ()
    #: Distinct resolved tool names, sorted.
    tool_names: tuple[str, ...] = ()
    #: Registration sites whose name could not be resolved to a literal.
    unresolved_count: int = 0
    #: Human-readable evidence for ``FrameworkDetection.evidence``.
    evidence: tuple[str, ...] = ()
    #: The declared-dependency reasons behind the language gate, on their own.
    #: Scoring reads this rather than indexing into ``evidence``: the rendered
    #: lines are ordered for a human and gain conditional entries at the end,
    #: so "the dependency reason" was a list position rather than a fact.
    framework_evidence: tuple[str, ...] = ()
    #: Workspace-relative files that carried a resolved registration.
    candidate_files: tuple[str, ...] = ()
    #: ``{type, path, reason}`` rows for a route discovery found and withheld.
    excluded: tuple[dict[str, str], ...] = ()
    #: Whether the file cap was reached before the walk finished.
    truncated: bool = False

    @property
    def detected(self) -> bool:
        return self.path is not None


@dataclass
class _FrameworkEvidence:
    """Declared MCP dependencies, per language.

    """

    languages: set[SourceLanguage] = field(default_factory=set)
    reasons: list[str] = field(default_factory=list)


def discover_mcp_server_source(
    workspace: Path,
    *,
    files: Sequence[Path],
    exported_source_paths: Iterable[str] = (),
    max_source_files: int = DEFAULT_MAX_SOURCE_FILES,
) -> McpSourceDiscovery:
    """Decide whether this workspace registers MCP tools in its own source.

    ``exported_source_paths`` are the workspace-relative paths of MCP exports
    discovery already accepted. Their presence is what withholds this route.
    """

    workspace = workspace.resolve()
    framework = _framework_evidence(workspace, files)
    if not framework.languages:
        return McpSourceDiscovery()

    # Paired with the workspace-relative path once, here: a file that is not
    # under the workspace at all (a symlink out of the tree) has no relative
    # form, and inventing one from its basename would put it in the wrong
    # directory for both the skip rules and the route's common ancestor.
    scannable = [
        pair
        for pair in ((path, _relative(path, workspace)) for path in files)
        if pair[1] is not None
        and _language_in_scope(pair[1], framework.languages)
    ]
    # Sorted before the cap, so which files are read is a property of the
    # workspace and not of the walk order — and capped where the flag is set,
    # rather than beside it: reporting `truncated` while reading every file
    # published "this count is a lower bound" for a count that was exact.
    scannable.sort(key=lambda pair: pair[1])
    truncated = len(scannable) > max_source_files
    names: set[str] = set()
    languages: set[SourceLanguage] = set()
    unresolved_by_file: dict[str, int] = {}
    candidate_files: list[str] = []
    unreadable = 0
    for path, relative in scannable[:max_source_files]:
        language = language_for_path(path)
        if language is None:  # pragma: no cover - filtered above
            continue
        try:
            if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
                continue
            # The adapter's own reader, not a lenient copy of it. Decoding here
            # with `errors="replace"` let `detect` resolve a registration out of
            # a file `load_mcp_server_source` then refuses as `unreadable_file`,
            # so the route this promised enumerated fewer tools than it named —
            # the detect/scan agreement is the whole point of sharing
            # `is_scannable_path`, and the read has to be shared too.
            text = load_text_file(path)
        except (OSError, InputParseError):
            unreadable += 1
            continue
        result = scan_source(text, language)
        resolved = [site.name for site in result.sites if site.name is not None]
        opaque = sum(1 for site in result.sites if site.name is None)
        if opaque:
            unresolved_by_file[relative] = opaque
        if not resolved:
            continue
        languages.add(language)
        names.update(resolved)
        candidate_files.append(relative)

    if not names:
        return McpSourceDiscovery(
            unresolved_count=sum(unresolved_by_file.values()), truncated=truncated
        )

    root = _common_directory(candidate_files)
    # Counted over the directory the route actually points at, so the number
    # discovery publishes is the number the adapter will report once the route
    # is configured. A whole-workspace count would name registrations `scan`
    # never reaches.
    unresolved = sum(
        count
        for relative, count in unresolved_by_file.items()
        if root == "." or PurePosixPath(relative).is_relative_to(root)
    )
    evidence = _evidence_lines(
        framework, languages, names, root, truncated, unresolved
    )
    covering_export, uncovered = _covering_export(
        workspace, exported_source_paths, names
    )
    if covering_export is not None:
        return McpSourceDiscovery(
            unresolved_count=unresolved,
            truncated=truncated,
            excluded=(
                {
                    "type": SOURCE_TYPE,
                    "path": root,
                    "reason": (
                        f"An MCP tool export ({covering_export}) already names "
                        f"every one of these {len(names)} registrations, and an "
                        "export is read at high confidence with its input "
                        "schemas; reading them in source would restate it at "
                        "medium."
                    ),
                },
            ),
        )
    if uncovered:
        # An export exists and does not account for the whole surface. It used
        # to withhold this route anyway, which in a workspace holding two
        # servers meant an export for one erased every source-only registration
        # of the other, and for one server meant a partial export erased the
        # remainder. Both routes are suggested instead, and the overlap is
        # named: two sources describing one server are reconciled by a reviewed
        # `tool_identity` binding (#386), never by dropping one of them.
        sample = ", ".join(sorted(uncovered)[:_EVIDENCE_NAME_LIMIT])
        if len(uncovered) > _EVIDENCE_NAME_LIMIT:
            sample += ", …"
        evidence = (
            *evidence,
            f"An MCP tool export is also present and does not name "
            f"{len(uncovered)} of these registrations ({sample}); both routes "
            "are suggested, and a reviewed tool_identity binding is what joins "
            "the two surfaces",
        )

    return McpSourceDiscovery(
        path=root,
        languages=tuple(sorted(languages)),
        tool_names=tuple(sorted(names)),
        unresolved_count=unresolved,
        evidence=evidence,
        framework_evidence=tuple(sorted(framework.reasons)),
        candidate_files=tuple(sorted(candidate_files)),
        truncated=truncated,
    )


def _framework_evidence(
    workspace: Path, files: Sequence[Path]
) -> _FrameworkEvidence:
    evidence = _FrameworkEvidence()
    for path in files:
        name = path.name
        if name not in {"package.json", "go.mod"}:
            continue
        relative = _relative(path, workspace)
        if relative is None:
            continue
        # The same skip set the source walk uses, not a narrower one of its
        # own: a manifest under `coverage/` or `out/` is no more this
        # repository's declaration than one under `node_modules/`, and two
        # lists would let a directory be skipped for registrations while still
        # granting the language gate that admits them.
        parts = PurePosixPath(relative).parts
        if any(part in SKIP_DIRECTORY_NAMES for part in parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if name == "go.mod":
            lowered = text.lower()
            for module in GO_FRAMEWORK_MODULES:
                if module in lowered:
                    evidence.languages.add("go")
                    evidence.reasons.append(f"{relative} requires {module}")
                    break
            continue
        package = _package_dependencies(text)
        for dependency in sorted(package):
            if any(
                dependency.lower().startswith(token)
                for token in TYPESCRIPT_FRAMEWORK_PACKAGES
            ):
                evidence.languages.add("typescript")
                evidence.reasons.append(f"{relative} depends on {dependency}")
                break
    return evidence


def _package_dependencies(text: str) -> set[str]:
    try:
        data = json.loads(text)
    except ValueError:
        return set()
    if not isinstance(data, dict):
        return set()
    names: set[str] = set()
    for key in _PACKAGE_JSON_DEPENDENCY_KEYS:
        section = data.get(key)
        if isinstance(section, dict):
            names.update(str(name) for name in section)
    return names


def _language_in_scope(relative: str, languages: set[SourceLanguage]) -> bool:
    language = language_for_path(relative)
    if language is None or language not in languages:
        return False
    return is_scannable_path(relative)


def _relative(path: Path, workspace: Path) -> str | None:
    try:
        return path.resolve().relative_to(workspace).as_posix()
    except (OSError, ValueError):
        return None


def _common_directory(relative_files: Sequence[str]) -> str:
    """The deepest directory containing every file that registered a tool.

    Not a scope decision — #363 settled that a deepest-common-ancestor is the
    wrong answer for *which project a manifest describes*. This is the narrower
    question of which subtree the adapter has to walk, where the ancestor is
    exactly right: widening it only costs a longer walk, and the adapter skips
    everything that is not source anyway.
    """

    parts_list = [PurePosixPath(name).parent.parts for name in relative_files]
    if not parts_list:
        return "."
    common = parts_list[0]
    for parts in parts_list[1:]:
        limit = min(len(common), len(parts))
        index = 0
        while index < limit and common[index] == parts[index]:
            index += 1
        common = common[:index]
    return PurePosixPath(*common).as_posix() if common else "."


def _covering_export(
    workspace: Path, exported_source_paths: Iterable[str], names: set[str]
) -> tuple[str | None, set[str]]:
    """The export that names *every* registration, and what no export names.

    Location is not the test, and neither is mere existence. A server commits
    its export at the repository root as readily as beside the code, so a
    containment test read two placements of one server's export differently;
    but "any export anywhere wins" is worse in the other direction, because a
    workspace holding two servers has an export for one and source-only
    registrations for the other, and a partial export covers part of a single
    server. Both cases silently deleted real actions.

    So the question asked is the one that matters: does the exported surface
    *contain* the surface read from source? Only then is the source route pure
    restatement, and only then is withholding it lossless.
    """

    covered: set[str] = set()
    for candidate in sorted(exported_source_paths):
        exported = _export_tool_names(workspace, candidate)
        if exported is None:
            continue
        covered |= exported
        if names <= covered:
            return candidate, set()
    return None, names - covered


def _export_tool_names(workspace: Path, relative: str) -> set[str] | None:
    """Tool names an accepted MCP export publishes, read by the real adapter.

    ``None`` when the export declines to name them — a wildcard export claims a
    surface without enumerating it, so it can never be shown to contain
    anything, and the source route is the more informative of the two.
    """

    from agents_shipgate.inputs.mcp import load_mcp_tools
    from agents_shipgate.schemas.manifest import ToolSourceConfig

    try:
        loaded = load_mcp_tools(
            ToolSourceConfig(id="export_probe", type="mcp", path=relative), workspace
        )
    except (InputParseError, OSError):
        return None
    if any(tool.annotations.get("wildcard_tools") is True for tool in loaded.tools):
        return None
    return {tool.name for tool in loaded.tools}


def _evidence_lines(
    framework: _FrameworkEvidence,
    languages: set[SourceLanguage],
    names: set[str],
    root: str,
    truncated: bool,
    unresolved: int,
) -> tuple[str, ...]:
    sample = sorted(names)[:_EVIDENCE_NAME_LIMIT]
    shown = ", ".join(sample)
    if len(names) > len(sample):
        shown += ", …"
    lines = [
        f"MCP tool registrations in {'/'.join(sorted(languages))} source under "
        f"{root}/: {len(names)} tool name(s) — {shown}",
    ]
    lines.extend(sorted(framework.reasons)[:_EVIDENCE_NAME_LIMIT])
    if unresolved:
        # Named here because this is what a human reads when deciding whether
        # to adopt, and "61 tools" without "and 3 more this reader cannot name"
        # is the over-claim the whole input is built to avoid.
        lines.append(
            f"{unresolved} registration(s) name themselves at runtime and are "
            "not enumerated"
        )
    if truncated:
        lines.append(
            "Discovery stopped at the source-file cap, so this count is a "
            "lower bound."
        )
    return tuple(lines)
