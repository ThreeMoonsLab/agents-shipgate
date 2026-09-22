"""Advisory host comparison evidence; deliberately carries no verdict."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_shipgate.schemas.capability_diff import CapabilityDiffRow
from agents_shipgate.schemas.current_control import CurrentControlWorkspaceIdentity


class HostComparisonLimit(BaseModel):
    """A surface this comparison did not read, and the change did not touch (#721).

    Named rather than dropped: rows exclude it, and the comparison makes no
    claim about it.
    """

    model_config = ConfigDict(extra="forbid")

    host: str
    limit: Literal["unsupported", "parse_failed", "experimental_coverage"]
    source: str
    detail: str


#: The most coverage items one comparison publishes (#812). The list is a
#: prefix of the comparator's order, which puts a file's rows after what no row
#: shows and a compared, unchanged source last, so the cap drops those first;
#: ``omitted_items`` counts the rest.
MAX_COVERAGE_ITEMS = 10

#: The issue kinds a host inventory publishes, as a blocking limit may name them.
CoverageLimitKind = Literal[
    "parse_failed",
    "unreadable",
    "unsupported",
    "unresolved_precedence",
    "dynamic_source_excluded",
    "remote_source_excluded",
]

#: Blocking kinds, most actionable first, as the cap reaches them (#812
#: follow-up). A refused comparison publishes one item per blocking issue, and
#: on a repository carrying many of them the cap used to be filled by whichever
#: sources sorted first alphabetically: twenty-one routine `unsupported` items
#: hid the one `unreadable` source that a reviewer could actually repair.
#:
#: `unreadable` and `parse_failed` name a source in the repository that this
#: entry could not read at all; `unresolved_precedence` names two declarations
#: in it competing for one grant. Those three are the author's to act on. The
#: rest — `unsupported`, `dynamic_source_excluded`, `remote_source_excluded` —
#: are this entry's own boundary on a file that may be exactly as its host
#: documents, so they are listed last and are the first the cap drops. Order
#: alone; no kind is dropped and none is called more severe than another.
COVERAGE_LIMIT_ORDER: tuple[str, ...] = (
    "unreadable",
    "parse_failed",
    "unresolved_precedence",
    "unsupported",
    "dynamic_source_excluded",
    "remote_source_excluded",
)


class HostComparisonCoverageItem(BaseModel):
    """What one comparison established about one source (#812).

    Built only from facts the comparator already computed: the rows, the
    artifact changes, the sources each inventory observed and the blocking
    issues each carries. It is evidence, never a verdict: an item cannot make
    a comparison comparable, remove a row or authorize anything.

    - ``compared``: the source was read, and ``rows`` of the published rows
      come from it, including rows of a source inside the file such as a
      Codex profile (``<file>#profiles.<name>``). With ``0`` rows on ``both``
      sides, the file's bytes were proven identical on both sides. With ``0``
      rows on one side, the file declares no grant this entry compares and its
      artifact did not change, as for a new guidance-only instruction file.
    - ``changed_without_grant_change``: the file changed, it gives no row, and
      the published data shows no grant this entry compares moved: its
      artifact digests the whole file, every side that has it parsed it, and
      nothing but that digest differs, or Git shows its content differs while
      its artifact did not, as for an edited ``env`` value or ``apiKeyHelper``,
      whose values the digest redacts and the comparison never compares. A
      difference a checkout conversion can explain is not shown. It does not say
      which fields changed: reordering or repeating a rule moves the digest
      too. Never a plugin manifest or marketplace, a retargeted link, or a
      Claude Code project settings file while a hook's loading basis changed.
    - ``changed_without_rows``: the file changed and no row is attributed to
      it, but the data does not show that no compared grant moved: a plugin
      manifest or marketplace (its ``hooks`` rows are published under the hook
      files it selects), a retargeted link, a parse or instruction-structure
      change, or project settings while a hook's loading basis changed.
    - ``unchanged_not_proven``: both sides read the file, it gives no row and
      its artifact did not change, but its bytes could be neither proven
      identical nor shown to differ, so a change in a value the artifact
      redacts would not show. A provided diff, a link read, a redacted path, a
      source no artifact publishes on both sides, or working-tree bytes that
      differ only as a checkout conversion such as ``eol=crlf`` or
      ``core.autocrlf`` makes them, cannot be proven. Never read as no change,
      and never as a change.
    - ``blocking_limit``: an incomparable comparison, and this source carries
      a blocking inventory issue of kind ``limit`` on ``side``.

    ``side`` says which inventories published the source, as an artifact or
    as the file of a grant (or carry the limit): ``base`` only, ``head`` only,
    or ``both``. A file is published whenever an inventory reads it: a deleted
    file is ``base``, a new or untracked one ``head``, and so is a hook file
    only the head's plugin configuration selects. A plugin manifest or
    marketplace is published only while it declares hooks, so for one of
    those ``side`` does not say whether the file exists on the other side.
    """

    model_config = ConfigDict(extra="forbid")

    source: str
    hosts: list[str] = Field(min_length=1)
    side: Literal["base", "head", "both"]
    status: Literal[
        "compared",
        "changed_without_grant_change",
        "changed_without_rows",
        "unchanged_not_proven",
        "blocking_limit",
    ]
    rows: int = Field(default=0, ge=0)
    limit: CoverageLimitKind | None = None
    detail: str | None = None
    #: Reserved for naming the scope an item belongs to once a comparison can
    #: be decided per scope (#808). Always ``None`` in this schema version.
    scope: str | None = None

    @model_validator(mode="after")
    def item_shape(self):
        if self.status == "blocking_limit":
            if self.limit is None or self.rows:
                raise ValueError("a blocking limit names its kind and publishes no rows")
        elif self.limit is not None or self.detail is not None:
            raise ValueError("only a blocking limit names a limit kind or detail")
        if (
            self.status
            in {"changed_without_grant_change", "changed_without_rows", "unchanged_not_proven"}
            and self.rows
        ):
            raise ValueError("a source with no row attributed publishes no rows")
        if self.status == "unchanged_not_proven" and self.side != "both":
            raise ValueError("only a source both sides read can be unproven unchanged")
        return self


class HostComparisonCoverage(BaseModel):
    """The capped list of what a comparison established, source by source (#812).

    ``None`` on :class:`HostComparison` means coverage was not recorded — a
    verifier from before schema ``0.20``, a comparison that never read an
    inventory, or a caller that publishes none (`check`). An empty ``items``
    list with ``omitted_items == 0`` means the comparison read no source it
    could name.

    ``items`` is a prefix of the comparator's order, which is the order a
    reviewer can act in: a blocking limit (most actionable kind first, see
    :data:`COVERAGE_LIMIT_ORDER`), then a change no row describes, then a
    source only one side published, then one not proven unchanged, then a
    file's rows, then a source proven unchanged — by source within each. So
    the cap drops the least actionable items, and ``omitted_items`` counts
    exactly those.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[HostComparisonCoverageItem] = Field(
        default_factory=list, max_length=MAX_COVERAGE_ITEMS
    )
    omitted_items: int = Field(default=0, ge=0)
    #: The list's own boundary, stated rather than left to be inferred (#812
    #: follow-up). Every item is a source an inventory read, or was refused
    #: by; a changed file no reader of this entry reads is not an item, and
    #: its absence here is no claim about it. So this list is never a complete
    #: account of what the change touched, however many items it carries.
    #: Enumerating the changed-but-unread residue is #821.
    read_sources_only: Literal[True] = True


class HostComparisonReviewChange(BaseModel):
    """One change exactly as the text prints it, referring to the rows it stands for (#795).

    Presentation, not a second opinion: every value is the one the shared
    renderer prints, and ``row_indexes`` names the published rows it was read
    from — usually one, and two only where the engine itself linked them (an
    allow rule the permission lattice decided another replaced, or the same
    rule text that moved between dispositions). A joined change's two sides can
    never read alike, so a route that redacts a rule's arguments publishes no
    pair; it publishes those rows as their own changes, as its text prints them.

    ``direction`` is the word the text uses, which for a joined change is
    ``widened``, ``narrowed`` or ``moved`` — the classification the rows
    themselves cannot carry, because each row is only an addition or a removal.
    ``change`` is the field-level difference printed in place of
    ``before → after`` when both sides name the same grant, and ``None``
    otherwise.
    """

    model_config = ConfigDict(extra="forbid")

    #: Positions in this comparison's ``rows``, ascending, as it publishes
    #: them. Which of a pair's two rows the ``before`` came from is read off
    #: the rows themselves: one is the removal, the other the addition.
    row_indexes: list[int] = Field(min_length=1, max_length=2)
    severity: str
    direction: str
    subject: str
    before: str
    after: str
    change: str | None = None
    why: str
    #: The engine called at least one of the rows behind this change an
    #: expansion. The count a reader sees is this one, per change, not the
    #: per-row ``expands``: a joined pair is one widening printed once.
    expands: bool = False

    @model_validator(mode="after")
    def change_shape(self):
        if len(set(self.row_indexes)) != len(self.row_indexes):
            raise ValueError("a change names each row it stands for once")
        if any(index < 0 for index in self.row_indexes):
            raise ValueError("a change refers to published rows by position")
        if len(self.row_indexes) == 2 and self.before == self.after:
            raise ValueError("a joined change whose two sides read alike is not published")
        return self


class HostComparisonReviewSummary(BaseModel):
    """The three numbers the text prints, so a reader never recounts them (#795)."""

    model_config = ConfigDict(extra="forbid")

    rows: int = Field(default=0, ge=0)
    changes: int = Field(default=0, ge=0)
    #: Changes the engine called an expansion — `diff`'s "N widening" count.
    widenings: int = Field(default=0, ge=0)


class HostComparisonReview(BaseModel):
    """What the text says about these rows, published as data (#795).

    ``None`` on :class:`HostComparison` means it was not recorded: a verifier
    from before schema ``0.20``, an incomparable comparison, which presents no
    change and asks no question, or a caller that publishes none (`check`,
    whose boundary result carries rows alone).

    It adds no row and decides nothing. The rows stay exactly what they were;
    this says how they are presented, which until now only the text knew, so a
    machine consumer could read `1 widening` from a run whose rows carry two
    ``expands`` flags.
    """

    model_config = ConfigDict(extra="forbid")

    changes: list[HostComparisonReviewChange] = Field(default_factory=list)
    summary: HostComparisonReviewSummary = Field(default_factory=HostComparisonReviewSummary)
    #: The review question the text ends with, verbatim, or ``None`` where it
    #: asks none (no change).
    question: str | None = None
    #: The command the text offers for reading the same comparison again, or
    #: ``None`` where it offers none — a provided diff or a `check` comparison,
    #: which names no base commit. For a commit head it is run after checking
    #: out ``head_commit``. Published whether or not there is a change: a
    #: zero-row result is the one a reviewer is most likely to want to rerun,
    #: and the text prints it there too (#812 follow-up).
    reproduce_command: str | None = None

    @model_validator(mode="after")
    def review_counts(self):
        if self.summary.changes != len(self.changes):
            raise ValueError("the summary counts the changes it publishes")
        if self.summary.widenings != sum(1 for change in self.changes if change.expands):
            raise ValueError("the summary counts the changes the engine called expansions")
        if self.summary.rows != sum(len(change.row_indexes) for change in self.changes):
            raise ValueError("every published row belongs to exactly one change")
        if self.question is None and self.changes:
            raise ValueError("a presented change is asked about")
        return self


class HostComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_identity: CurrentControlWorkspaceIdentity | None = None
    comparison_status: Literal["comparable", "incomparable"]
    incomparable_reasons: list[str] = Field(default_factory=list)
    base_commit: str | None = None
    head_commit: str | None = None
    head_kind: Literal["commit", "worktree", "provided_diff"]
    base_inventory_sha256: str | None = None
    head_inventory_sha256: str | None = None
    paths: list[str] = Field(default_factory=list)
    rows: list[CapabilityDiffRow] = Field(default_factory=list)
    unchanged_limits: list[HostComparisonLimit] = Field(default_factory=list)
    coverage: HostComparisonCoverage | None = None
    review: HostComparisonReview | None = None
    static_analysis_only: Literal[True] = True

    @model_validator(mode="after")
    def comparison_health(self):
        if self.comparison_status == "incomparable" and (
            self.rows or not self.incomparable_reasons
        ):
            raise ValueError("incomparable input needs reasons and cannot publish rows")
        if self.comparison_status == "incomparable" and self.unchanged_limits:
            raise ValueError("incomparable input names no unchanged limits")
        if self.comparison_status == "comparable" and self.incomparable_reasons:
            raise ValueError("comparable input cannot carry incomparable reasons")
        if self.coverage is not None:
            statuses = {item.status for item in self.coverage.items}
            if self.comparison_status == "incomparable" and statuses - {"blocking_limit"}:
                raise ValueError("an incomparable comparison establishes no compared source")
            if self.comparison_status == "comparable" and "blocking_limit" in statuses:
                raise ValueError("a comparable comparison names no blocking limit")
            attributed = sum(item.rows for item in self.coverage.items)
            if attributed > len(self.rows) or (
                not self.coverage.omitted_items and attributed != len(self.rows)
            ):
                raise ValueError("coverage must attribute every published row to its source")
        if self.review is not None:
            if self.comparison_status == "incomparable":
                raise ValueError("an incomparable comparison presents no change")
            positions = [index for change in self.review.changes for index in change.row_indexes]
            if sorted(positions) != list(range(len(self.rows))):
                raise ValueError("the presented changes must stand for every published row once")
        return self
