"""Discovery for MCP servers whose tool surface exists only as code (#431).

``detect`` classified the official MongoDB and Grafana MCP servers as "not an
agent project". Both publish dozens of tools — ``drop-database``,
``delete-many``, ``update_incident`` — and neither commits an export, so every
route discovery had was filename-shaped and found nothing. This module supplies
the missing route: it asks whether a workspace *is* an MCP server written in
TypeScript, Go or Python, and if so which directory holds the registrations.

Two facts have to hold together before the route is offered, and the pairing is
the whole design.

**Declared framework evidence.** A repository that declares no MCP dependency
is not an MCP server just because a class of its own happens to spell a field
``toolName``. #393's lesson is that a proof resting on a spelling is the
fail-open shape, so the dependency — ``@modelcontextprotocol/*`` in a
``package.json``, an MCP module in a ``go.mod``, ``mcp`` or ``fastmcp`` in a
``pyproject.toml`` — is what turns a spelling into provenance. It is also the
cheap half: a workspace with no such dependency is answered without reading a
single source file.

**A registration this reader can stand behind.** Dependency evidence alone says
the repository *uses* MCP, which every client does too. For the lexical idioms
the second fact is a **resolved name**: they match a spelling, and a spelling
is all `.registerTool(` in a client repository ever is. The Python idiom
already carries the second fact in the site itself — it is only emitted after
the decorator has been followed back to a server construction, and a client
does not construct a server — so there the route is offered for a registration
whose *name* is built at run time. That is not a corner: all 40 of
``neo4j-contrib/mcp-neo4j``'s tools are ``name=namespace_prefix + "…"``, and
requiring a readable name would report the repository as "not an agent project"
precisely because its names are dynamic.

Where both hold and the workspace *also* has a parseable MCP export, the export
wins: it is the server's own published contract, published in the shape a
client receives it, and it is ``high`` confidence against ``medium``. The
source route is then withheld and recorded in ``excluded_sources`` — named and
visible, never silently dropped, because a route that vanishes without a reason
is indistinguishable from one nobody implemented.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.common import load_text_file
from agents_shipgate.inputs.mcp_idioms import (
    GO_FRAMEWORK_MODULES,
    MAX_SOURCE_FILE_BYTES,
    SKIP_DIRECTORY_NAMES,
    TYPESCRIPT_FRAMEWORK_PACKAGES,
    PythonServerIndex,
    SourceLanguage,
    declares_python_mcp_framework,
    is_scannable_path,
    language_for_path,
    scan_source,
)
from agents_shipgate.inputs.mcp_server_source import (
    MAX_CACHED_SOURCE_BYTES,
    SOURCE_TYPE,
)

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

#: Python project files read for the language gate. ``setup.py`` is absent on
#: purpose: its dependencies are an argument to a function call, so reading them
#: is either running the file or grepping it, and a grep for ``mcp`` in a Python
#: module is the spelling-shaped proof #393 rules out.
_PYTHON_PROJECT_FILES: frozenset[str] = frozenset({"pyproject.toml"})

#: Dependency tables of a ``pyproject.toml`` that count. PEP 621's own list,
#: PEP 735's dependency groups, and Poetry's table — a server declares its SDK
#: in whichever one its build backend reads, and all three are live in the
#: surveyed servers' ecosystems.
_PYPROJECT_DEPENDENCY_PATHS: tuple[tuple[str, ...], ...] = (
    ("project", "dependencies"),
    ("project", "optional-dependencies"),
    ("dependency-groups",),
    ("tool", "poetry", "dependencies"),
    ("tool", "poetry", "group"),
)


@dataclass(frozen=True)
class McpSourceDiscovery:
    """What discovery concluded about the workspace's own registration sites."""

    #: Workspace-relative directory to point ``tool_sources[].path`` at, or
    #: ``None`` when no route was found.
    path: str | None = None
    #: Languages that contributed a registration this route rests on.
    languages: tuple[SourceLanguage, ...] = ()
    #: Distinct resolved tool names, sorted. Empty is a real answer: a server
    #: can register 40 tools and name none of them where a reader can read it.
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
    #: Workspace-relative files the route has to cover — the ones that carried
    #: a registration, plus the modules whose server construction proved one.
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
    read = scannable[:max_source_files]
    server_index, python_texts = _python_server_index(read)
    names: set[str] = set()
    languages: set[SourceLanguage] = set()
    unresolved_by_file: dict[str, int] = {}
    candidate_files: list[str] = []
    for path, relative in read:
        language = language_for_path(path)
        if language is None:  # pragma: no cover - filtered above
            continue
        text = python_texts.pop(path, None) or _source_text(path)
        if text is None:
            continue
        result = scan_source(
            text, language, module_path=relative, server_index=server_index
        )
        resolved = [site.name for site in result.sites if site.name is not None]
        opaque = sum(1 for site in result.sites if site.name is None)
        if opaque:
            unresolved_by_file[relative] = opaque
        # A site that proves a server counts even when its name does not
        # resolve. For the lexed idioms nothing does — they match a spelling,
        # and a spelling in a repository that merely *uses* MCP is the
        # coincidence the dependency gate exists to reject. The Python idiom
        # has already followed the decorator back to a `FastMCP(...)`
        # construction before it emits anything, and a client does not
        # construct a server. Requiring a *name* on top of that would withhold
        # the route from `neo4j-contrib/mcp-neo4j`, whose 40 registrations are
        # every one of them `name=namespace_prefix + "…"` — the repository
        # would stay "not an agent project" because its tool names are built at
        # run time, which is the state this input exists to end.
        if not resolved and not any(site.proves_server for site in result.sites):
            continue
        languages.add(language)
        names.update(resolved)
        candidate_files.append(relative)
        # The module that constructed the server is part of the route even
        # though it registers nothing: `redis/mcp-redis` builds its server in
        # `src/common/server.py` and decorates in `src/tools/*.py`, and a route
        # covering only the decorators is one on which `scan` cannot repeat the
        # proof `detect` just published.
        candidate_files.extend(result.server_modules)

    if not candidate_files:
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
    covering_export, uncovered = (
        _covering_export(workspace, exported_source_paths, names)
        if names
        # An export cannot be shown to *contain* a surface with no names in it.
        # `names <= covered` is vacuously true for the empty set, so asking the
        # question here would let any readable export displace a route whose
        # whole content is "40 registrations nobody can enumerate" — the one
        # case where the source route says something no export restates.
        else (None, set())
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
        # De-duplicated: one module can prove the binding for every file
        # that imports it, so a plain sort repeated `src/common/server.py`
        # once per decorating module.
        candidate_files=tuple(sorted(set(candidate_files))),
        truncated=truncated,
    )


def _python_server_index(
    scannable: Sequence[tuple[Path, str]]
) -> tuple[PythonServerIndex, dict[Path, str]]:
    """Index every Python module in the walk that constructs an MCP server.

    Built ahead of the scan for the same reason the adapter builds one:
    ``redis/mcp-redis`` constructs the server in ``src/common/server.py`` and
    decorates in ``src/tools/*.py``, so resolving bindings as the walk went
    would prove or refuse the same decorator depending on file order.

    The keys are workspace-relative here and root-relative in the adapter, and
    that is not a divergence: both are the path *inside the tree being read*,
    which is what an import's segments are matched against.

    Returns the texts it read alongside the index, up to
    :data:`MAX_CACHED_SOURCE_BYTES`, so the scan pass reads each Python file
    once. Past the bound a file is read twice rather than held, which changes
    no answer.
    """

    texts: dict[Path, str] = {}
    cached = 0

    def _modules() -> Iterator[tuple[str, str]]:
        nonlocal cached
        for path, relative in scannable:
            if language_for_path(path) != "python":
                continue
            text = _source_text(path)
            if text is None:
                continue
            if cached + len(text) <= MAX_CACHED_SOURCE_BYTES:
                texts[path] = text
                cached += len(text)
            yield relative, text

    return PythonServerIndex.build(_modules()), texts


def _source_text(path: Path) -> str | None:
    """One source file's text, or ``None`` when this walk will not read it.

    The adapter's own reader, not a lenient copy of it. Decoding here with
    ``errors="replace"`` let ``detect`` resolve a registration out of a file
    ``load_mcp_server_source`` then refuses as ``unreadable_file``, so the
    route this promised enumerated fewer tools than it named — the detect/scan
    agreement is the whole point of sharing ``is_scannable_path``, and the read
    has to be shared too.
    """

    try:
        if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
            return None
        return load_text_file(path)
    except (OSError, InputParseError):
        return None


def _framework_evidence(
    workspace: Path, files: Sequence[Path]
) -> _FrameworkEvidence:
    evidence = _FrameworkEvidence()
    for path in files:
        name = path.name
        if name in _PYTHON_PROJECT_FILES or _is_requirements_file(name):
            _record_python_evidence(workspace, path, evidence)
            continue
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


def _is_requirements_file(name: str) -> bool:
    """Whether ``name`` is a pip requirements file.

    The glob rather than the exact name: ``requirements-dev.txt`` and
    ``requirements/base.txt`` are both ordinary, and a repository that pins its
    SDK in one of them has declared it just as plainly.
    """

    lowered = name.lower()
    return lowered.startswith("requirements") and lowered.endswith(".txt")


def _record_python_evidence(
    workspace: Path, path: Path, evidence: _FrameworkEvidence
) -> None:
    """Admit Python when a project file declares ``mcp`` or ``fastmcp``.

    Weaker than its Go and TypeScript counterparts, and knowingly so: a Python
    *client* declares ``mcp`` too. It buys the same thing the others buy — a
    workspace with no such dependency is answered without parsing a single
    module — while the load-bearing half of the pairing moves into the reader,
    which will not emit a Python site at all until it has followed the
    decorated object back to a ``FastMCP(...)`` construction.
    """

    relative = _relative(path, workspace)
    if relative is None:
        return
    if any(part in SKIP_DIRECTORY_NAMES for part in PurePosixPath(relative).parts):
        return
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    requirements = (
        _pyproject_requirements(text)
        if path.name in _PYTHON_PROJECT_FILES
        else _requirements_txt(text)
    )
    dependency = declares_python_mcp_framework(requirements)
    if dependency is None:
        return
    evidence.languages.add("python")
    evidence.reasons.append(f"{relative} depends on {dependency}")


def _pyproject_requirements(text: str) -> list[str]:
    """Every requirement string a ``pyproject.toml`` declares.

    Parsed with ``tomllib`` rather than scanned line by line. The general
    package-token scan in discovery does the latter and drops
    ``mcp[cli]>=1.26.0,<2`` — the requirement two of the five surveyed servers
    declare — because an extras marker is not a bare token.
    """

    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError, RecursionError):
        return []
    requirements: list[str] = []
    for path in _PYPROJECT_DEPENDENCY_PATHS:
        node: object = data
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        requirements.extend(_toml_requirements(node))
    return requirements


def _toml_requirements(node: object, depth: int = 0) -> list[str]:
    """Every string in a dependency table that could name a distribution.

    Four spellings of one fact, and the walker admits **both** halves of a
    table rather than choosing between them: PEP 621 writes a list of
    requirement strings, PEP 735 and Poetry's groups write a table of such
    lists, and Poetry's own table writes the *name as the key* with a
    constraint as the value — sometimes an inline table (``{version = "^2"}``)
    rather than a string.

    Choosing was the bug. A first draft emitted the key only when the value
    yielded nothing, so ``mcp = "^1.6.0"`` produced ``^1.6.0`` and never
    ``mcp`` — every Poetry table in the list above was dead, and the gate
    withheld the route from a Poetry-managed server. Emitting the key too
    costs nothing: a group name like ``dev`` is not a distribution this gate
    looks for, and :func:`normalized_distribution` drops ``^1.6.0`` anyway.
    """

    if depth > 3:  # pragma: no cover - a dependency table is never this deep
        return []
    if isinstance(node, str):
        return [node]
    if isinstance(node, list):
        return [item for item in node if isinstance(item, str)]
    if isinstance(node, dict):
        found: list[str] = []
        for key, value in node.items():
            found.append(str(key))
            found.extend(_toml_requirements(value, depth + 1))
        return found
    return []


def _requirements_txt(text: str) -> list[str]:
    """Requirement lines from a pip requirements file.

    Option lines (``-r``, ``--index-url``, ``-e``) are dropped rather than
    followed: a requirements file that includes another one names a path, and
    resolving it is a second file read for a gate whose whole value is that it
    is cheap.
    """

    return [
        line
        for raw in text.splitlines()
        if (line := raw.strip()) and not line.startswith(("-", "#"))
    ]


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

    candidates = sorted(exported_source_paths)
    if not candidates:
        # No export at all, which is the *common* case here: a server whose
        # tool surface exists only as source is the population this input
        # exists for. `names - covered` would be every name, and the caller
        # renders a non-empty shortfall as "an MCP tool export is also present
        # and does not name them" — a statement about a file that does not
        # exist, in the evidence a human reads when deciding whether to adopt.
        return None, set()
    covered: set[str] = set()
    for candidate in candidates:
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
        (
            f"MCP tool registrations in {'/'.join(sorted(languages))} source "
            f"under {root}/: {len(names)} tool name(s) — {shown}"
        )
        if names
        else (
            f"MCP tool registrations in {'/'.join(sorted(languages))} source "
            f"under {root}/: {unresolved} registration(s), none of which this "
            "reader can name"
        ),
    ]
    lines.extend(sorted(framework.reasons)[:_EVIDENCE_NAME_LIMIT])
    if unresolved and names:
        # Named here because this is what a human reads when deciding whether
        # to adopt, and "61 tools" without "and 3 more this reader cannot name"
        # is the over-claim the whole input is built to avoid. Suppressed when
        # there are no names at all, because the headline above already is the
        # unresolved count and saying it twice reads as two findings.
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
