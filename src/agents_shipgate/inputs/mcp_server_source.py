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

The Python idiom (#484) also reads the decorated function's **signature**,
because for FastMCP that is where the input schema comes from — it is written
in the same file, at the same site, and checking it costs no inference. That
does not lift the ceiling: a signature is what the author wrote, not what the
server publishes, and the ``medium`` bound is about the route, not about how
much of it was read.

Reading a signature is two questions, and #539 is what happens when either is
answered by looking at the *spelling*. Which parameters the caller supplies is
decided by the framework's own injection rule, which resolves the annotation to
a class — so the reader resolves it too, against the module's bindings, and
publishes the parameter with the question named wherever that does not settle
it. What type a parameter has is whatever the annotation denotes — read from
its tree, never from a string match that answers ``string`` for everything it
does not recognise. Where neither can be established the parameter stays
visible and the tool's ``surface_gaps`` say which of the two is open: this
input's failure modes are a real input erased and an invented caller
requirement, and both are worse than an admitted gap.

Where a repository publishes a committed export, that export stays the better
route: it is the server's own contract published in the shape a client
receives it, and it is ``high`` confidence against this input's ``medium``.
``detect`` therefore withholds this route wherever an export already covers
the scope, and an adopter with both configured keeps the export's evidence
untouched.

**Completeness is per file**, following #393: one unresolved registration holds
every tool that file produced at ``partial``, and its siblings elsewhere in the
tree stay ``enumerated``. A name this reader cannot resolve is never dropped —
it becomes a :class:`~agents_shipgate.core.domain.SourceSurfaceOmission` that
the exclusion ledger accounts for by subject.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar, Literal

from agents_shipgate.core.domain import (
    SURFACE_ENUMERATED,
    SURFACE_PARTIAL,
    LoadedToolSource,
    SourceSurfaceOmission,
    Tool,
    ToolParameter,
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
    PythonServerIndex,
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

#: How many bytes of Python source the index pass keeps for the scan pass.
#:
#: A cost bound and never a decision: past it a file is simply read twice
#: instead of once, so no answer moves and nothing is recorded. That is what
#: makes it different from every other cap in this module — exceeding
#: :data:`MAX_SCANNED_FILES` or :data:`MAX_SOURCE_FILE_BYTES` leaves a hole in
#: the enumeration and has to be reported; exceeding this one leaves a hole in
#: nothing.
#:
#: Sized against the measurement: all of ``awslabs/mcp``'s non-test Python is
#: 12.8 MB, so the largest server in the survey is read once. The bound exists
#: for the shape the file cap alone does not stop — 4,000 files of 8 MB each
#: would be 32 GB held to save a second read.
MAX_CACHED_SOURCE_BYTES = 64 * 1024 * 1024

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
            "MCP server source has no TypeScript, Go or Python files to read: "
            f"{root}. "
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

    server_index, python_texts = _python_server_index(files, root)

    for path in files:
        language = language_for_path(path)
        assert language is not None  # guaranteed by _scannable_files
        relative = _relative_source_path(path, source.path, base_dir)
        unread = _unread_reason(path)
        text = python_texts.pop(path, None)
        if unread is None and text is None:
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
        result = scan_source(
            text,
            language,
            module_path=_root_relative(path, root),
            server_index=server_index,
        )

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


def _python_server_index(
    files: list[Path], root: Path
) -> tuple[PythonServerIndex, dict[Path, str]]:
    """Index every module in the walk that constructs an MCP server.

    A pass over the Python files ahead of the one that scans them, and the
    ordering is the whole point: ``redis/mcp-redis`` applies all 53 of its
    decorators in ``src/tools/*.py`` and constructs the server they register on
    in ``src/common/server.py``, so a reader that resolved bindings as it went
    would prove or refuse the same decorator depending on walk order.

    The texts come back with the index so the scan pass does not read them a
    second time — on ``awslabs/mcp`` that is 2,500 files' worth of I/O — up to
    :data:`MAX_CACHED_SOURCE_BYTES`, past which a file is read twice rather
    than held. Only Python files are held either way: the TypeScript and Go
    halves of a mixed repository are streamed as before, one read each.
    """

    texts: dict[Path, str] = {}
    cached = 0

    def _modules() -> Iterator[tuple[str, str]]:
        # A generator, so ``build`` sees one module at a time and keeps only
        # each one's exported names. Materialising the list would hold every
        # file's text alive at once, which is the cost the bound above exists
        # to refuse.
        nonlocal cached
        for path in files:
            if (
                language_for_path(path) != "python"
                or _unread_reason(path) is not None
            ):
                continue
            relative = _root_relative(path, root)
            if relative is None:
                continue
            try:
                text = load_text_file(path)
            except InputParseError:
                # Reported as an omission by the read loop, which is the pass
                # that owns the file's surface. Recording it here too would put
                # one file's problem into the ledger twice.
                continue
            if cached + len(text) <= MAX_CACHED_SOURCE_BYTES:
                texts[path] = text
                cached += len(text)
            yield relative, text

    return PythonServerIndex.build(_modules()), texts


def _root_relative(path: Path, root: Path) -> str | None:
    """``path`` relative to the walked root, as an import-resolvable key.

    Deliberately *not* the manifest-relative path the omissions and tools
    carry. Module resolution matches path segments against a dotted import, so
    the segments have to be the ones inside the source tree; prefixing them
    with the manifest's own directory layout would make ``from .server import
    mcp`` resolve against a package that does not exist.
    """

    if root.is_file():
        return path.name if path == root else None
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


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
    # A signature this reader could not fully resolve is this tool's own gap,
    # not its file's: the file still enumerated every registration it holds.
    # It joins the file's reasons in one list because ``surface`` is one claim
    # per tool, and a tool whose interface is partly unread has not had its
    # surface established whatever the rest of the file managed.
    gaps = sorted(set(surface_gaps) | set(_signature_gaps(site)))
    extraction: dict[str, object] = {
        "method": EXTRACTION_METHOD,
        "confidence": EXTRACTION_CONFIDENCE,
        "idiom": site.idiom,
        "registry_version": IDIOM_REGISTRY_VERSION,
        "surface": SURFACE_PARTIAL if gaps else surface,
    }
    if gaps:
        extraction["surface_gaps"] = gaps
    parameters = _signature_parameters(site)
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
        input_schema=_signature_input_schema(parameters, site),
        output_schema=(
            {"type": site.returns_json_type}
            if site.returns_json_type is not None
            else {}
        ),
        parameters=parameters,
        function_signature=_signature_text(site, parameters),
        risk_hints=_operation_type_hints(site),
        extraction_confidence=EXTRACTION_CONFIDENCE,
        extraction=extraction,
    )


#: ``Tool.extraction["surface_gaps"]`` reasons this input records about **one
#: tool's own interface**, as opposed to which tools the file registers.
#:
#: The framework decides whether it injects a parameter by resolving the
#: annotation to a class and checking identity against its own request context.
#: Where the module does not settle that — a relative import pointing outside
#: the walk, a name two statements bind, a base class this reader cannot
#: place — the honest answer is neither "the caller supplies it" nor "the
#: framework does". The parameter stays in the inventory, visible, and the
#: tool says which question it could not answer (#539).
SURFACE_GAP_UNRESOLVED_CONTEXT = "unresolved_context_identity"

#: A parameter carrying no annotation at all. The type it publishes would be
#: the emitter's fallback rather than anything the source said.
SURFACE_GAP_UNTYPED_PARAMETER = "untyped_parameter"

#: An annotation is present and this reader cannot represent what it denotes —
#: a Pydantic model, a ``Literal``, a container whose elements it cannot name.
#: The same guess as an absent annotation with better manners, which is why
#: it is a gap rather than a schema.
#:
#: Both spellings are the vocabulary ``google_adk`` established for the same
#: two facts, and ``tests/test_mcp_server_source.py`` pins them equal: a
#: reviewer reading ``Unresolved: unrepresentable_annotation`` must not have to
#: know which adapter wrote it.
SURFACE_GAP_UNREPRESENTABLE_ANNOTATION = "unrepresentable_annotation"


def _signature_parameters(site: RegistrationSite) -> list[ToolParameter]:
    """The registered tool's parameters, for an idiom that reads a signature.

    One exclusion, and only where the reader **established** it: the server
    strips its own request context from the schema it publishes, so a catalog
    that kept it would publish a required argument no caller can supply.

    Matching that on the annotation's spelling is what #539 reproduced in both
    directions — an application model named ``Context`` disappeared from a tool
    presented as taking no arguments, and an aliased framework context became a
    required string. The name-based ``SKIPPED_TOOL_PARAMETERS`` the other
    Python adapters share is wrong here for the same reason and one step
    further out: it holds ``config``, ``context`` and ``runtime``, which are
    ordinary user-supplied inputs to an MCP tool. ``self`` is not special
    either — a decorated method registers the plain function, and the server
    puts ``self`` in the schema, so this reader says what the server says.
    """

    if site.parameters is None:
        return []
    return [
        ToolParameter(
            name=parameter.name,
            # The type the annotation *denotes*, never the emitter's fallback.
            # ``None`` says the type was not read, which is the honest answer
            # and a different one from ``string``.
            type=parameter.json_type,
            required=parameter.required,
        )
        for parameter in site.parameters
        if parameter.injection != "framework_injected"
    ]


def _signature_gaps(site: RegistrationSite) -> list[str]:
    """Reasons this tool's own signature was not fully read.

    Separate from the file's reasons, which answer "which tools exist": a file
    can enumerate every registration it holds and still carry one function
    whose interface the source does not give up.
    """

    if site.parameters is None:
        return []
    gaps: set[str] = set()
    for parameter in site.parameters:
        if parameter.injection == "framework_injected":
            # Not published, so nothing about its type is claimed either.
            continue
        if parameter.injection == "unresolved":
            gaps.add(SURFACE_GAP_UNRESOLVED_CONTEXT)
        if parameter.annotation is None:
            gaps.add(SURFACE_GAP_UNTYPED_PARAMETER)
        elif parameter.json_type is None:
            gaps.add(SURFACE_GAP_UNREPRESENTABLE_ANNOTATION)
    # ``output_schema`` is built from the return annotation by the same rule,
    # so an unreadable one is the same gap. An *absent* return annotation is an
    # honest omission — the schema stays ``{}`` — and is not one.
    if site.returns is not None and site.returns_json_type is None:
        gaps.add(SURFACE_GAP_UNREPRESENTABLE_ANNOTATION)
    return sorted(gaps)


def _signature_input_schema(
    parameters: list[ToolParameter], site: RegistrationSite
) -> dict[str, object]:
    """The JSON Schema the signature implies, or ``{}`` when there is no signature.

    An empty schema and a schema with no properties are different claims: the
    first says this idiom reads no signature at all, the second says the tool
    takes no arguments. A lexical idiom must publish the first — inventing
    "this tool takes nothing" for a TypeScript registration would be a schema
    assertion nobody made.
    """

    if site.parameters is None:
        return {}
    return {
        "type": "object",
        "properties": {
            # An empty property schema is the JSON Schema for "any value", and
            # it is what this reader knows about a type it could not read. The
            # tool's ``surface_gaps`` say which question was left open; the
            # schema does not answer it with a guess.
            parameter.name: (
                {"type": parameter.type} if parameter.type is not None else {}
            )
            for parameter in parameters
        },
        "required": [
            parameter.name for parameter in parameters if parameter.required
        ],
    }


def _signature_text(
    site: RegistrationSite, parameters: list[ToolParameter]
) -> str | None:
    if site.parameters is None:
        return None
    rendered = f"{site.name}({', '.join(parameter.name for parameter in parameters)})"
    return f"{rendered} -> {site.returns}" if site.returns else rendered


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
                "MCP server source is not a TypeScript, Go or Python file: "
                f"{root}"
            )
        return [root], False
    candidates = [
        path
        for path in walk_input_tree(root)
        if path.is_file() and is_scannable_path(path.relative_to(root))
    ]
    return candidates[:MAX_SCANNED_FILES], len(candidates) > MAX_SCANNED_FILES


def _relative_source_path(path: Path, configured: str, base_dir: Path) -> str:
    """The manifest-relative path of the file a tool was read from.

    Not the configured ``path``: that is a directory for every real server, and
    a finding pointing at a directory sends a reviewer to look for a
    registration in 300 files.

    ``resolve_input_path`` already refuses a source path outside the manifest
    directory, so the relative form exists for every file this walk reaches.
    The fallback covers the case that refusal cannot: a path that escapes
    through a symlink after resolution, where naming the configured source is
    better than raising in the middle of a catalog.
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
            "The TypeScript, Go or Python source of an MCP server that does "
            "not commit an export, through a built-in registry of "
            "registration idioms."
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
                variant="literal name at a registration site",
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
                shape="literal_registration",
                status="extracted",
                variant="FastMCP decorator",
                reads=(
                    "A `@mcp.tool` decorator on a Python function, where `mcp` "
                    "is followed back to a `FastMCP(...)` construction — in the "
                    "same module or in one this walk also read. The name is the "
                    "`name=` literal or, where the framework's own default "
                    "applies, the function's; the description is the "
                    "`description=` literal or the docstring; the input schema "
                    "is the annotated signature, minus the request context the "
                    "framework injects. A parameter whose annotation this "
                    "reader cannot resolve to a type, or place as injected or "
                    "caller-supplied, stays in the schema with that question "
                    "named and holds the tool at `partial`."
                ),
                emits=(SOURCE_TYPE,),
                ceiling=EXTRACTION_CONFIDENCE,
                surface=SURFACE_ENUMERATED,
            ),
            BoundaryCell(
                shape="dynamic_construction",
                status="not_extracted",
                variant="decorator on an object this reader cannot follow",
                reads=(
                    "A `.tool` decorator whose object does not resolve to a "
                    "`FastMCP(...)` construction — `@self.mcp.tool` on a server "
                    "handed in as an argument is the measured shape — registers "
                    "something this reader can neither name nor confirm is a "
                    "tool at all. It enters no catalog and is recorded as an "
                    "unenumerated subject in the exclusion ledger."
                ),
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
                variant="name built at runtime",
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
