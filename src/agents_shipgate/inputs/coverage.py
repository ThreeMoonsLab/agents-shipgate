"""The determinism boundary: what each built-in input can prove, per shape.

``insufficient_evidence`` outside the boundary reads as *"this tool cannot
read my repository"* unless the boundary is a published specification. This
module is that specification's source: every built-in adapter declares, beside
the code that mints its confidences, what it reads for each declaration shape
and the extraction-confidence ceiling that shape reaches. The generator in
``scripts/generate_schemas.py`` projects it into ``docs/determinism-boundary``
(``.md`` and ``.json``), and CI's ``--check`` run fails on drift.

Two properties keep the published page from becoming a false claim.

**It is enumerated fail-closed.** :func:`build_boundary_matrix` walks the
adapter registry rather than a list kept beside it, so an adapter registered
without a :class:`SourceCoverage` raises instead of being omitted; and every
source type the engine's own ceiling vocabularies name
(``AST_ONLY_SOURCE_TYPES``, ``MCP_SOURCE_TYPES``) must appear in some cell, so
a new source type wired into a ceiling without a coverage row raises too. A
report that silently drops a route is worse than no report.

**The consequence column is derived, not written.** A cell declares only what
the adapter does — the source type it emits, the ceiling it reaches, and the
``extraction["surface"]`` evidence it writes. What that means for a verdict is
computed by asking the engine's own predicates
(:func:`~agents_shipgate.core.semantic_assessment.extraction_is_complete`,
:func:`~agents_shipgate.core.semantic_assessment.surface_is_complete`) about a
probe tool built from those declared facts. Restating the rule here would be
the route-table mistake #433 names: a table hand-maintained beside the function
that owns the routes drifts, and a drifted published boundary is a lie with a
URL.

Third-party adapters are outside the published boundary by construction. They
may coin any source type and any confidence, and nothing in this repository can
speak for what they prove, so :func:`build_boundary_matrix` reads a fresh
built-in registry rather than the process-global one a scan may have added them
to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate.core.domain import SURFACE_ENUMERATED, Tool
from agents_shipgate.core.semantic_assessment import (
    AST_ONLY_SOURCE_TYPES,
    MCP_SOURCE_TYPES,
    extraction_is_complete,
    surface_is_complete,
)
from agents_shipgate.core.surface_exclusions import exclusion_phrase
from agents_shipgate.schemas.common import Confidence
from agents_shipgate.schemas.manifest.tool_sources import (
    BUILTIN_PER_SCAN_ONLY_TOOL_SOURCE_TYPES,
    BUILTIN_TOOL_SOURCE_TYPES,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agents_shipgate.inputs.protocol import AdapterRegistry

#: The version of the published matrix's machine-readable shape. Bumped only
#: when a consumer would have to change to keep reading it.
BOUNDARY_SCHEMA_VERSION = "shipgate.determinism_boundary/v1"

#: How a repository names the tools it exposes. The reader's question is "which
#: of these does my repository do?", so the four are cut by *what the declaration
#: names*, not by which framework it belongs to — a wildcard MCP export and a
#: ``tools=[*build()]`` comprehension are one row's worth of answer.
DeclarationShape = Literal[
    "export_artifact",
    "literal_registration",
    "factory",
    "dynamic_construction",
]

DECLARATION_SHAPE_ORDER: tuple[DeclarationShape, ...] = (
    "export_artifact",
    "literal_registration",
    "factory",
    "dynamic_construction",
)

DECLARATION_SHAPE_DEFINITIONS: dict[DeclarationShape, str] = {
    "export_artifact": (
        "The surface arrives as a contract file that names every action and "
        "its schema — an MCP `tools/list` export, an OpenAPI document, a "
        "reviewed tool inventory."
    ),
    "literal_registration": (
        "Each action is written out where the parser can read it: a decorated "
        "function, a tool class, a literal list of tool objects, a config "
        "entry naming the tool."
    ),
    "factory": (
        "A call constructs the action set. The call is visible; what it "
        "returns is not, because resolving it would mean running it."
    ),
    "dynamic_construction": (
        "The declaration does not name the actions: a wildcard, a "
        "comprehension, `**config`, a name rebound in a loop, a symbol that "
        "resolves to nothing readable."
    ),
}

#: What a cell says happened to this shape.
CellStatus = Literal["extracted", "not_extracted", "not_applicable"]

#: The ``extraction["surface"]`` evidence an adapter writes for a shape.
#: ``None`` means the adapter writes no answer at all, which every consumer
#: reads as incomplete — absence is not ``partial`` with extra steps.
SurfaceEvidence = Literal["enumerated", "partial"]

#: What a route amounts to, derived from the engine's answer about it. Five
#: values, so the long sentence explaining each is written once on the page and
#: every cell points at it rather than restating it in its own words.
CellOutcome = Literal[
    "proven",
    "set_unproven",
    "low_confidence",
    "not_extracted",
    "not_applicable",
]

#: Worst-to-best, for a summary that has to pick one answer for a shape with
#: several routes. A summary that showed the *worst* would tell a reader with a
#: reviewed inventory that their repository cannot be proven.
CELL_OUTCOME_RANK: dict[CellOutcome, int] = {
    "not_applicable": 0,
    "not_extracted": 1,
    "low_confidence": 2,
    "set_unproven": 3,
    "proven": 4,
}


class BoundaryCoverageError(RuntimeError):
    """A built-in route has no published coverage, or claims one it cannot reach.

    Raised during generation, never at scan time: the boundary page is an
    artifact of the build, and a build that cannot describe a route must fail
    rather than publish a matrix missing it.
    """


class BoundaryCell(BaseModel):
    """One (input, declaration shape) route, as the adapter implements it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    shape: DeclarationShape
    status: CellStatus
    #: Names this route when one shape has more than one answer — an ADK tool
    #: is registered literally in Python *and* literally in agent config, and
    #: the two reach different ceilings. Collapsing them would have to publish
    #: the lower one for both, which is false about half the repositories that
    #: read it.
    variant: str | None = None
    #: What the adapter reads for this shape, in the terms of someone looking
    #: at their own repository. One sentence.
    reads: str
    #: The ``Tool.source_type`` values this route contributes to the catalog.
    #: Empty unless ``status`` is ``extracted``.
    emits: tuple[str, ...] = ()
    #: The best ``extraction_confidence`` this route reaches. ``None`` unless
    #: ``status`` is ``extracted``.
    ceiling: Confidence | None = None
    #: The ``extraction["surface"]`` value the adapter writes for this route.
    surface: SurfaceEvidence | None = None
    #: ``Tool.annotations`` keys this route sets to ``True`` that bear on
    #: surface completeness (``wildcard_tools``, ``mcp_unknown_schema``, …).
    surface_flags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_status_agreement(self) -> BoundaryCell:
        if self.status == "extracted":
            if not self.emits or self.ceiling is None:
                raise ValueError(
                    f"{self.shape}: an extracted route must name the source "
                    "type(s) it emits and the ceiling it reaches"
                )
        elif self.emits or self.ceiling is not None:
            raise ValueError(
                f"{self.shape}: a {self.status} route puts nothing in the "
                "catalog, so it cannot name an emitted source type or a ceiling"
            )
        if self.surface is not None and self.status != "extracted":
            raise ValueError(
                f"{self.shape}: surface evidence is written onto a tool, so a "
                f"{self.status} route cannot declare it"
            )
        for source_type in self.emits:
            if self.surface is not None and source_type not in AST_ONLY_SOURCE_TYPES:
                raise ValueError(
                    f"{self.shape}: {source_type!r} reads a published contract, "
                    "so no adapter writes extraction['surface'] for it"
                )
            if (
                source_type in AST_ONLY_SOURCE_TYPES
                and self.ceiling == "high"
                and self.surface != SURFACE_ENUMERATED
            ):
                raise ValueError(
                    f"{self.shape}: {source_type!r} is read out of source code, "
                    "so the engine caps it below high until the adapter proves "
                    f"the surface ({SURFACE_ENUMERATED!r})"
                )
        return self

    def probe(self, source_type: str) -> Tool:
        """A tool carrying exactly the facts this route produces.

        Built so the engine — not this module — answers what the route means.
        Only the fields the two completeness predicates read are populated;
        anything else would invite the reader to think the probe is a scan.
        """

        extraction: dict[str, object] = {"confidence": self.ceiling}
        if self.surface is not None:
            extraction["surface"] = self.surface
        return Tool(
            id=f"boundary:{source_type}:{self.shape}",
            name=f"{source_type}:{self.shape}",
            source_type=source_type,
            annotations={flag: True for flag in self.surface_flags},
            extraction_confidence=self.ceiling or "low",
            extraction=extraction,
        )


class SourceCoverage(BaseModel):
    """Every declaration shape one built-in input can meet.

    Declared as a ``coverage`` class attribute on the adapter itself. That is
    the point: the ceiling is minted a few hundred lines away, and a table in
    another file is one refactor from describing an adapter that no longer
    behaves that way.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: The adapter's ``source_type`` — its key in the registry.
    adapter: str
    #: Display name for the input, in the adopter's words.
    label: str
    #: What the adapter reads at all. One sentence.
    reads: str
    cells: tuple[BoundaryCell, ...]

    @model_validator(mode="after")
    def validate_every_shape_answered(self) -> SourceCoverage:
        by_shape: dict[str, list[BoundaryCell]] = {}
        for cell in self.cells:
            by_shape.setdefault(cell.shape, []).append(cell)
        missing = sorted(set(DECLARATION_SHAPE_ORDER) - set(by_shape))
        if missing:
            raise ValueError(
                f"{self.adapter}: coverage answers no declaration shape "
                f"{', '.join(missing)}. Every shape gets an answer, including "
                "'this input has no such declaration' — a blank cell is "
                "indistinguishable from a route nobody checked."
            )
        for shape, cells in by_shape.items():
            if len(cells) == 1:
                continue
            variants = [cell.variant for cell in cells]
            if None in variants or len(set(variants)) != len(variants):
                raise ValueError(
                    f"{self.adapter}: {shape} has {len(cells)} routes, so each "
                    "needs a distinct `variant` naming which one it is"
                )
        return self


#: What each outcome means for a release verdict. A closed mapping, keyed by an
#: outcome that is itself derived from the engine's predicates, so no sentence
#: here can describe a state the predicates did not reach.
CELL_OUTCOME_VERDICTS: dict[CellOutcome, str] = {
    "not_applicable": "No such declaration exists for this input.",
    "not_extracted": (
        "Nothing enters the catalog, so no check runs on it and no verdict "
        "covers it. The scan records that it read and refused this, rather "
        "than reporting an empty surface."
    ),
    "proven": (
        "Extraction evidence is complete: an action from this route can be "
        "pass-eligible. Effect, authority, identity, and binding evidence are "
        "judged separately and can still withhold a pass."
    ),
    "set_unproven": (
        "The action's own contract was read, but the set it belongs to was not "
        "established, so it raises `incomplete_surface` and can never be "
        "pass-eligible. The exclusion ledger records the unread remainder — "
        "the action is analysed; what stands beside it is not."
    ),
    "low_confidence": (
        "Every action from this route raises `low_confidence_tool` and "
        "`incomplete_surface`, so none of them can be pass-eligible, and the "
        "exclusion ledger records what was not established. Once low-confidence "
        "actions reach half the analysed surface, the verdict is "
        "`insufficient_evidence`. A reviewed tool inventory is the route out."
    ),
}


class ResolvedCell(BaseModel):
    """A published cell: what was declared, plus what the engine makes of it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    shape: DeclarationShape
    status: CellStatus
    variant: str | None
    reads: str
    emits: tuple[str, ...]
    ceiling: Confidence | None
    surface: SurfaceEvidence | None
    surface_flags: tuple[str, ...]
    #: Derived — ``extraction_is_complete`` on this route's tools.
    extraction_complete: bool
    #: Derived — ``surface_is_complete`` on this route's tools.
    surface_complete: bool
    #: Derived — whether extraction evidence alone leaves the action able to be
    #: pass-eligible. Effect, authority, identity, and binding evidence are
    #: judged separately and can still withhold it.
    extraction_permits_pass: bool
    #: Derived — the evidence-gap kinds every action on this route carries.
    evidence_gaps: tuple[str, ...]
    #: Derived — the exclusion-ledger reason the unread remainder is recorded
    #: under, and the phrase the ledger renders for it.
    exclusion_reason: str | None
    exclusion_detail: str | None
    #: Derived — which of the five outcomes this route reaches.
    outcome: CellOutcome
    #: Derived — what that outcome means for a verdict, in one sentence.
    verdict: str


class ResolvedSource(BaseModel):
    """One input's published row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    adapter: str
    label: str
    reads: str
    #: Derived — how a manifest asks for this input.
    configured_as: Literal["tool_sources", "manifest_section"]
    cells: tuple[ResolvedCell, ...]


class BoundaryMatrix(BaseModel):
    """The whole published boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = BOUNDARY_SCHEMA_VERSION
    declaration_shapes: dict[str, str] = Field(
        default_factory=lambda: dict(DECLARATION_SHAPE_DEFINITIONS)
    )
    outcomes: dict[str, str] = Field(
        default_factory=lambda: dict(CELL_OUTCOME_VERDICTS)
    )
    sources: tuple[ResolvedSource, ...]

    def best_outcome(self, source: ResolvedSource, shape: str) -> CellOutcome:
        """The best outcome ``source`` reaches for ``shape``."""

        candidates = [cell.outcome for cell in source.cells if cell.shape == shape]
        if not candidates:  # pragma: no cover - SourceCoverage forbids it
            raise BoundaryCoverageError(
                f"{source.adapter} publishes no route for {shape!r}"
            )
        return max(candidates, key=lambda outcome: CELL_OUTCOME_RANK[outcome])


def _cell_outcome(
    *,
    status: CellStatus,
    extraction_complete: bool,
    surface_complete: bool,
) -> CellOutcome:
    """Which of the five outcomes the engine's answer amounts to."""

    if status == "not_applicable":
        return "not_applicable"
    if status == "not_extracted":
        return "not_extracted"
    if extraction_complete and surface_complete:
        return "proven"
    if extraction_complete:
        return "set_unproven"
    return "low_confidence"


def _resolve_cell(cell: BoundaryCell) -> ResolvedCell:
    """Ask the engine what this route's declared facts amount to."""

    if cell.status == "extracted":
        probes = [cell.probe(source_type) for source_type in cell.emits]
        extraction_answers = {extraction_is_complete(probe) for probe in probes}
        surface_answers = {surface_is_complete(probe) for probe in probes}
        if len(extraction_answers) > 1 or len(surface_answers) > 1:
            raise BoundaryCoverageError(
                f"cell {cell.shape!r} emits source types the engine treats "
                f"differently ({', '.join(cell.emits)}); split it so each "
                "published answer is one answer"
            )
        extraction_complete = extraction_answers.pop()
        surface_complete = surface_answers.pop()
    else:
        extraction_complete = False
        surface_complete = False

    gaps: list[str] = []
    exclusion_reason: str | None = None
    if cell.status == "extracted":
        if not extraction_complete:
            gaps.append("low_confidence_tool")
        if not (extraction_complete and surface_complete):
            gaps.append("incomplete_surface")
            # The ledger row follows the *gap*, not the completeness bit:
            # ``_surface_completeness_exclusions`` builds one row per
            # ``incomplete_surface`` evidence gap, and that gap fires on low
            # confidence as readily as on an unproven surface. Deriving it from
            # ``surface_complete`` alone published "no exclusion" for every
            # medium-confidence contract input (n8n, Conductor), which the
            # ledger contradicts on the same run.
            exclusion_reason = "surface_not_enumerated"

    outcome = _cell_outcome(
        status=cell.status,
        extraction_complete=extraction_complete,
        surface_complete=surface_complete,
    )
    return ResolvedCell(
        shape=cell.shape,
        status=cell.status,
        variant=cell.variant,
        reads=cell.reads,
        emits=cell.emits,
        ceiling=cell.ceiling,
        surface=cell.surface,
        surface_flags=cell.surface_flags,
        extraction_complete=extraction_complete,
        surface_complete=surface_complete,
        extraction_permits_pass=extraction_complete and surface_complete,
        evidence_gaps=tuple(gaps),
        exclusion_reason=exclusion_reason,
        exclusion_detail=(
            exclusion_phrase(exclusion_reason) if exclusion_reason else None
        ),
        outcome=outcome,
        verdict=CELL_OUTCOME_VERDICTS[outcome],
    )


def _configured_as(adapter: str) -> Literal["tool_sources", "manifest_section"]:
    if adapter in BUILTIN_TOOL_SOURCE_TYPES:
        return "tool_sources"
    if adapter in BUILTIN_PER_SCAN_ONLY_TOOL_SOURCE_TYPES:
        return "manifest_section"
    raise BoundaryCoverageError(
        f"adapter {adapter!r} is registered but appears in neither "
        "BUILTIN_TOOL_SOURCE_TYPES nor "
        "BUILTIN_PER_SCAN_ONLY_TOOL_SOURCE_TYPES, so the page cannot say how "
        "a manifest asks for it"
    )


def build_boundary_matrix(registry: AdapterRegistry | None = None) -> BoundaryMatrix:
    """Project the registered built-in adapters into the published boundary.

    ``registry`` defaults to a fresh :class:`AdapterRegistry`, which populates
    the built-ins and nothing else — deliberately not the process-global
    ``REGISTRY``, which may already carry third-party adapters discovered by an
    earlier scan in the same process. The published boundary describes what
    this distribution proves; a page whose contents depended on the generating
    machine's installed plugins would not be a specification.
    """

    from agents_shipgate.inputs.protocol import AdapterRegistry

    registry = registry if registry is not None else AdapterRegistry()

    sources: list[ResolvedSource] = []
    covered_source_types: set[str] = set()
    for adapter in registry:
        coverage = getattr(adapter, "coverage", None)
        if not isinstance(coverage, SourceCoverage):
            raise BoundaryCoverageError(
                f"adapter {adapter.source_type!r} declares no coverage, so the "
                "determinism boundary cannot say what it proves. Add a "
                "`coverage: ClassVar[SourceCoverage]` answering every "
                "declaration shape."
            )
        if coverage.adapter != adapter.source_type:
            raise BoundaryCoverageError(
                f"adapter {adapter.source_type!r} declares coverage for "
                f"{coverage.adapter!r}"
            )
        cells = tuple(
            _resolve_cell(cell)
            for cell in sorted(
                coverage.cells, key=lambda c: DECLARATION_SHAPE_ORDER.index(c.shape)
            )
        )
        covered_source_types.add(adapter.source_type)
        for cell in cells:
            covered_source_types.update(cell.emits)
        sources.append(
            ResolvedSource(
                adapter=coverage.adapter,
                label=coverage.label,
                reads=coverage.reads,
                configured_as=_configured_as(coverage.adapter),
                cells=cells,
            )
        )

    # Fail closed the other way too. The registry proves every *adapter* is
    # described; these two vocabularies are where the engine names individual
    # source types, and a token added to either without a cell is a route the
    # page would silently omit while the engine gates on it. An adapter's own
    # key counts as covered by its row: ``google_adk`` is in the AST-only set
    # defensively and no tool is ever minted with it, so demanding a cell that
    # emits it would ask the page to publish a route that does not exist.
    for label, vocabulary in (
        ("AST_ONLY_SOURCE_TYPES", AST_ONLY_SOURCE_TYPES),
        ("MCP_SOURCE_TYPES", MCP_SOURCE_TYPES),
    ):
        missing = sorted(vocabulary - covered_source_types)
        if missing:
            raise BoundaryCoverageError(
                f"{label} names source types no adapter's coverage emits: "
                f"{', '.join(missing)}. Add the route to the adapter that "
                "produces it, or remove it from the vocabulary."
            )

    return BoundaryMatrix(sources=tuple(sources))


__all__ = [
    "BOUNDARY_SCHEMA_VERSION",
    "DECLARATION_SHAPE_DEFINITIONS",
    "DECLARATION_SHAPE_ORDER",
    "BoundaryCell",
    "BoundaryCoverageError",
    "BoundaryMatrix",
    "CELL_OUTCOME_RANK",
    "CELL_OUTCOME_VERDICTS",
    "CellOutcome",
    "CellStatus",
    "DeclarationShape",
    "ResolvedCell",
    "ResolvedSource",
    "SourceCoverage",
    "SurfaceEvidence",
    "build_boundary_matrix",
]
