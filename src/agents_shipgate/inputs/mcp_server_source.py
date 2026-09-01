"""Read an MCP server's tool surface out of its own source (#431).

The route for a repository that *is* an MCP server and never emits its tool
list. ``tool_sources[].path`` points at the directory (or the single file) that
holds the registrations; this input walks it, applies the built-in idiom
registry in :mod:`agents_shipgate.inputs.mcp_idioms`, and contributes one
action per tool name written as a literal at a registration site.

What it does **not** do is as much of the design as what it does. It runs no
code, evaluates no schema library, infers no type, and reads no annotation the
source declares about itself — the #268 lesson is that an artifact must never
get to say what counts as evidence about itself, and a tool server's own source
is exactly such an artifact. Effect and authority still come from the
declaration questionnaire (#410). The one thing the source is trusted to state
is a name, which is checkable against the registration site that carries it.

Where a repository publishes a committed export, that export stays the better
route: it is the server's own contract, it carries the input schemas this input
deliberately does not read, and it is ``high`` confidence against this input's
``medium``. ``detect`` therefore withholds this route wherever an export
already covers the scope, and an adopter with both configured keeps the
export's evidence untouched.

**Completeness is per file**, following #393: one unresolved registration holds
every tool that file produced at ``partial``, and its siblings elsewhere in the
tree stay ``enumerated``. A name this reader cannot resolve is never dropped —
it becomes a :class:`~agents_shipgate.core.domain.SourceSurfaceOmission` that
the exclusion ledger accounts for by subject.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

from agents_shipgate.core.domain import (
    SURFACE_ENUMERATED,
    SURFACE_PARTIAL,
    LoadedToolSource,
    SourceSurfaceOmission,
    Tool,
    ToolRiskHint,
)
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.common import (
    load_text_file,
    manifest_relative_path,
    resolve_input_path,
    stable_tool_id,
    tool_name_warning,
    walk_input_tree,
)
from agents_shipgate.inputs.coverage import BoundaryCell, SourceCoverage
from agents_shipgate.inputs.mcp_idioms import (
    IDIOM_REGISTRY_VERSION,
    MAX_SOURCE_FILE_BYTES,
    OMISSION_REASONS,
    RegistrationSite,
    is_scannable_path,
    language_for_path,
    scan_source,
)
from agents_shipgate.inputs.protocol import LoadedAdapterResult
from agents_shipgate.schemas.manifest import (
    AgentsShipgateManifest,
    ToolSourceConfig,
)

#: The ``Tool.source_type`` and ``tool_sources[].type`` this input owns.
SOURCE_TYPE = "mcp_server_source"

#: The extraction method recorded on every action this input contributes.
EXTRACTION_METHOD = "mcp_source_registration"

#: Ceiling for a literal at a registration site. A name read from source is
#: weaker evidence than a name read from the server's published contract, and
#: the gap is not closable by reading more carefully: the export also carries
#: the input schema, which this input does not read at all.
EXTRACTION_CONFIDENCE = "medium"

#: How many source files one configured source may be walked for. A cap that
#: silently truncated would be the fail-open shape this input exists to close,
#: so exceeding it is recorded as an omission and holds every action the source
#: contributed at ``partial``: past the cap, nothing is known about what was
#: not read.
MAX_SCANNED_FILES = 4000

#: ``operationType``-style classifications an idiom can read, mapped to the
#: risk-tag vocabulary. Deliberately partial, in one direction only.
#:
#: ``read`` and ``metadata`` are absent on purpose. A heuristic must never
#: *prove* a read-only action (#357/#409), and a tool server asserting its own
#: harmlessness is the one claim an untrusted source has an incentive to make.
#: The escalating classifications carry no such incentive, so reading them is
#: safe — and they are the point: MongoDB writes ``static operationType =
#: "delete"`` beside ``drop-database``, which is precisely the answer a
#: reviewer should have to defend contradicting.
#:
#: These arrive as ``inferred_keyword`` claims at ``low`` confidence, so they
#: are never policy-eligible: they cannot close an effect-evidence gap and
#: cannot make an action pass-eligible on their own. What they can do is
#: challenge a declaration that sits below them.
OPERATION_TYPE_RISK_TAGS: dict[str, str] = {
    "create": "write",
    "update": "write",
    "delete": "destructive",
    "destroy": "destructive",
    "drop": "destructive",
    "write": "write",
    "execute": "code_execution",
}


def load_mcp_server_source(source: ToolSourceConfig, base_dir: Path) -> LoadedToolSource:
    assert source.path is not None
    root = resolve_input_path(base_dir, source.path)
    if not root.exists():
        raise InputParseError(f"Input path not found: {root}")

    warnings: list[str] = []
    omissions: list[SourceSurfaceOmission] = []
    tools: list[Tool] = []
    seen_names: set[str] = set()

    files, capped = _scannable_files(root)
    if not files:
        raise InputParseError(
            f"MCP server source has no TypeScript or Go files to read: {root}. "
            "Point tool_sources[].path at the directory holding the server's "
            "tool registrations, or at one such file."
        )

    def _omit(subject: str, reason: str, warning: str, detail: str) -> None:
        warnings.append(warning)
        omissions.append(
            SourceSurfaceOmission(
                subject=subject, reason=reason, detail=detail, warning=warning
            )
        )

    # Gaps that belong to the *source*, not to one file. A file this reader
    # never opened could register anything, so unlike a single unresolved
    # registration these hold every action the source contributed at
    # ``partial``: the question they leave open is which tools exist.
    source_gaps: list[str] = []

    if capped:
        source_gaps.append("walk_capped")
        _omit(
            subject=manifest_relative_path(source.path, base_dir),
            reason="walk_capped",
            warning=(
                f"MCP server source {source.id!r} stopped at {MAX_SCANNED_FILES} "
                "source files; the rest of the tree was not read"
            ),
            detail=(
                f"Reading stopped at {MAX_SCANNED_FILES} files, so any tool "
                "registered past that point is absent from this catalog. Point "
                "tool_sources[].path at the package that holds the "
                "registrations rather than at the repository root."
            ),
        )

    for path in files:
        language = language_for_path(path)
        assert language is not None  # guaranteed by _scannable_files
        relative = _relative_source_path(path, root, source.path, base_dir)
        unread = _unread_reason(path)
        if unread is None:
            try:
                text = load_text_file(path)
            except InputParseError:
                unread = "unreadable_file"
        if unread is not None:
            # One unreadable file must not fail the whole scan: a repository
            # carrying a generated bundle beside its server would then have no
            # route at all, which is the state this input exists to end.
            source_gaps.append(unread)
            _omit(
                subject=relative,
                reason=unread,
                warning=f"MCP server source {relative} was not read ({unread})",
                detail=OMISSION_REASONS[unread],
            )
            continue
        result = scan_source(text, language)

        file_gaps: list[str] = []
        for anomaly in result.anomalies:
            file_gaps.append(anomaly)
            _omit(
                subject=relative,
                reason=anomaly,
                warning=(
                    f"MCP server source {relative} could not be fully read "
                    f"({anomaly})"
                ),
                detail=OMISSION_REASONS[anomaly],
            )
        for site in result.sites:
            if site.name is not None:
                continue
            reason = site.unresolved_reason or "name_not_literal"
            file_gaps.append(reason)
            _omit(
                subject=f"{relative}:{site.line}",
                reason=reason,
                warning=(
                    f"MCP tool registered at {relative}:{site.line} does not "
                    "name itself with a literal"
                ),
                detail=OMISSION_REASONS[reason],
            )

        surface = (
            SURFACE_PARTIAL if (file_gaps or source_gaps) else SURFACE_ENUMERATED
        )
        gaps = sorted(set(file_gaps))
        for site in result.sites:
            if site.name is None:
                continue
            if site.name in seen_names:
                # One name is one tool. Grafana registers
                # `alerting_manage_silences` twice — a read-only build and a
                # read-write one, exactly one of which is mounted — and two
                # catalog rows sharing an id would be one action counted twice.
                warnings.append(
                    f"MCP tool {site.name!r} is registered more than once in "
                    f"source {source.id!r}; the first registration is used"
                )
                continue
            seen_names.add(site.name)
            if warning := tool_name_warning(site.name):
                warnings.append(warning)
            tools.append(
                _tool_from_site(
                    site,
                    source=source,
                    source_path=relative,
                    surface=surface,
                    surface_gaps=gaps,
                )
            )

    # A source gap found *after* a file was read still holds that file's
    # actions: which tools exist is one question for the whole source, and
    # answering it per file in walk order would leave the answer depending on
    # where in the tree the unread file happened to sit.
    if source_gaps:
        _apply_source_gaps(tools, sorted(set(source_gaps)))

    return LoadedToolSource(
        source_id=source.id,
        source_type=SOURCE_TYPE,
        tools=tools,
        warnings=warnings,
        omissions=omissions,
    )


def _apply_source_gaps(tools: list[Tool], gaps: list[str]) -> None:
    for tool in tools:
        tool.extraction["surface"] = SURFACE_PARTIAL
        merged = sorted(set(tool.extraction.get("surface_gaps", [])) | set(gaps))
        tool.extraction["surface_gaps"] = merged


def _unread_reason(path: Path) -> str | None:
    """Why this file was not opened, or ``None`` when it was."""

    try:
        if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
            return "file_too_large"
    except OSError:
        return "unreadable_file"
    return None


def _tool_from_site(
    site: RegistrationSite,
    *,
    source: ToolSourceConfig,
    source_path: str,
    surface: str,
    surface_gaps: list[str],
) -> Tool:
    assert site.name is not None
    extraction: dict[str, object] = {
        "method": EXTRACTION_METHOD,
        "confidence": EXTRACTION_CONFIDENCE,
        "idiom": site.idiom,
        "registry_version": IDIOM_REGISTRY_VERSION,
        "surface": surface,
    }
    if surface_gaps:
        extraction["surface_gaps"] = surface_gaps
    return Tool(
        id=stable_tool_id(site.name),
        name=site.name,
        description=site.description,
        source_type=SOURCE_TYPE,
        source_id=source.id,
        source_ref=source.path,
        source_path=source_path,
        source_start_line=site.line,
        source_start_column=site.column,
        risk_hints=_operation_type_hints(site),
        extraction_confidence=EXTRACTION_CONFIDENCE,
        extraction=extraction,
    )


def _operation_type_hints(site: RegistrationSite) -> list[ToolRiskHint]:
    if site.operation_type is None:
        return []
    tag = OPERATION_TYPE_RISK_TAGS.get(site.operation_type.strip().lower())
    if tag is None:
        return []
    return [
        ToolRiskHint(
            tag=tag,
            source="mcp_operation_type",
            confidence="low",
            basis="inferred_keyword",
            evidence={
                "operation_type": site.operation_type,
                "idiom": site.idiom,
            },
        )
    ]


def _scannable_files(root: Path) -> tuple[list[Path], bool]:
    """Every source file under ``root`` this input reads, and whether it capped."""

    if root.is_file():
        if language_for_path(root) is None:
            raise InputParseError(
                f"MCP server source is not a TypeScript or Go file: {root}"
            )
        return [root], False
    candidates = [
        path
        for path in walk_input_tree(root)
        if path.is_file() and is_scannable_path(path.relative_to(root))
    ]
    return candidates[:MAX_SCANNED_FILES], len(candidates) > MAX_SCANNED_FILES


def _relative_source_path(
    path: Path, root: Path, configured: str, base_dir: Path
) -> str:
    """The manifest-relative path of the file a tool was read from.

    Not the configured ``path``: that is a directory for every real server, and
    a finding pointing at a directory sends a reviewer to look for a
    registration in 300 files. ``resolve_input_path`` already refused anything
    outside the manifest directory, so the relative form always exists — the
    fallback is for the single-file case, where ``root`` *is* the file.
    """

    try:
        return manifest_relative_path(str(path.relative_to(base_dir.resolve())), base_dir)
    except ValueError:
        return manifest_relative_path(configured, base_dir)


class MCPServerSourceAdapter:
    """``ToolSourceAdapter`` wrapping :func:`load_mcp_server_source`."""

    source_type: ClassVar[str] = SOURCE_TYPE
    scope: ClassVar[Literal["per_source", "per_scan"]] = "per_source"
    artifact_class: ClassVar[type | None] = None

    coverage: ClassVar[SourceCoverage] = SourceCoverage(
        adapter=SOURCE_TYPE,
        label="MCP server source",
        reads=(
            "The TypeScript or Go source of an MCP server that does not commit "
            "an export, through a built-in registry of registration idioms."
        ),
        cells=(
            BoundaryCell(
                shape="export_artifact",
                status="not_applicable",
                reads=(
                    "A committed export is the server's own contract and is read "
                    "by the MCP tool-export input at `high`, which stays the "
                    "better route wherever one exists."
                ),
            ),
            BoundaryCell(
                shape="literal_registration",
                status="extracted",
                reads=(
                    'A tool name written as a string literal at a registration '
                    'site — `static toolName = "…"`, `.registerTool("…"`, '
                    '`MustTool("…"`, `NewTool("…"`, `Tool{Name: "…"}` — plus the '
                    "sibling operation-class literal where the idiom defines one."
                ),
                emits=(SOURCE_TYPE,),
                ceiling=EXTRACTION_CONFIDENCE,
                surface=SURFACE_ENUMERATED,
            ),
            BoundaryCell(
                shape="factory",
                status="not_extracted",
                reads=(
                    "A helper that registers a table of tools contributes "
                    "nothing on its own: resolving what it registers would mean "
                    "evaluating it. Any registration site inside the helper is "
                    "read on its own terms."
                ),
            ),
            BoundaryCell(
                shape="dynamic_construction",
                status="not_extracted",
                reads=(
                    "A registration whose name is a variable, a concatenation, "
                    "or a template substitution names no tool this reader can "
                    "check. It enters no catalog and is recorded as an "
                    "unenumerated subject in the exclusion ledger, which holds "
                    "its file's surface at `partial`."
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
        assert source is not None, "per_source adapter requires a source"
        return LoadedAdapterResult(tool_sources=[load_mcp_server_source(source, base_dir)])
