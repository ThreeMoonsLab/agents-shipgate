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
#: prefix of the comparator's order, which puts a blocking limit first — by
#: kind within those, :data:`COVERAGE_LIMIT_ORDER` below — then a changed input
#: this entry does not read (#821), then what no row shows, then a file's rows,
#: and a compared, unchanged source last, so the cap drops those first;
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
#: in it competing for one grant. The rest — `unsupported`,
#: `dynamic_source_excluded`, `remote_source_excluded` — most often name this
#: entry's own boundary on a file that may be exactly as its host documents, so
#: they are listed last and are the first the cap drops.
#:
#: Most often, not always: `unsupported` also carries an instruction file whose
#: own text would not parse, which
#: :func:`~agents_shipgate.core.host_grants.unresolved_structure_message` still
#: tells the author to repair. So this ranks kinds, not items, and an item
#: behind the count may still be one to repair. Order alone; no kind is dropped
#: and none is called more severe than another.
COVERAGE_LIMIT_ORDER: tuple[str, ...] = (
    "unreadable",
    "parse_failed",
    "unresolved_precedence",
    "unsupported",
    "dynamic_source_excluded",
    "remote_source_excluded",
)

#: The documented rule that named a changed input this entry does not read
#: (#821), as :mod:`agents_shipgate.core.unread_inputs` defines each one. It
#: says what kind of file or member changed and nothing more: never that a
#: host loads it, what it grants, or that the change is a finding.
UnreadCandidateKind = Literal[
    "plugin_mcp_config",
    "plugin_manifest_mcp_servers",
    "plugin_manifest_hooks",
    "plugin_hook_file",
    "unparsed_plugin_manifest",
    "cursor_project_hooks",
    "nested_host_settings",
    "external_plugin_source",
]


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
    - ``changed_not_read``: a path in the comparison's own changed-file set
      that a documented candidate rule names, and that no reader of this entry
      read (#821). ``candidate`` names the rule. The source is the file, or a
      member inside it (``<manifest>#mcpServers``,
      ``<marketplace>#plugins.<name>``) whose text differs between the sides.
      It is named from the path and, for a member, from the member's text;
      nothing is fetched, run or read as a grant, so it never says a host
      loads the file, gives no row and is never a finding. It can accompany a
      refused comparison as well as a comparable one: it is not a source
      either inventory compared.

    ``side`` says which inventories published the source, as an artifact or
    as the file of a grant (or carry the limit): ``base`` only, ``head`` only,
    or ``both``. A file is published whenever an inventory reads it: a deleted
    file is ``base``, a new or untracked one ``head``, and so is a hook file
    only the head's plugin configuration selects. A plugin manifest or
    marketplace is published only while it declares hooks, so for one of
    those ``side`` does not say whether the file exists on the other side.
    For ``changed_not_read``, which nothing published, ``side`` is where the
    file or member exists: ``head`` added, ``base`` removed, ``both`` changed,
    and ``hosts`` is the host the candidate rule attributes the path to (which
    can be ``copilot``), never a host whose reader read it.
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
        "changed_not_read",
    ]
    rows: int = Field(default=0, ge=0)
    limit: CoverageLimitKind | None = None
    #: A blocking limit's published issue message, or for an
    #: ``external_plugin_source`` the source it names, redacted and bounded.
    detail: str | None = None
    #: The candidate rule that named a ``changed_not_read`` item (#821).
    candidate: UnreadCandidateKind | None = None
    #: Reserved for naming the scope an item belongs to once a comparison can
    #: be decided per scope (#808). Always ``None`` in this schema version.
    scope: str | None = None

    @model_validator(mode="after")
    def item_shape(self):
        if self.status == "blocking_limit":
            if self.limit is None or self.rows or self.candidate is not None:
                raise ValueError("a blocking limit names its kind and publishes no rows")
        elif self.status == "changed_not_read":
            if self.candidate is None or self.rows or self.limit is not None:
                raise ValueError(
                    "a changed input this entry does not read names its candidate rule "
                    "and publishes no rows"
                )
        elif self.limit is not None or self.detail is not None or self.candidate is not None:
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
    :data:`COVERAGE_LIMIT_ORDER`), then a changed input this entry does not
    read (#821), then a change no row describes, then a source only one side
    published, then one not proven unchanged, then a file's rows, then a
    source proven unchanged — by source within each. So the cap drops the
    least actionable items, and ``omitted_items`` counts exactly those.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[HostComparisonCoverageItem] = Field(
        default_factory=list, max_length=MAX_COVERAGE_ITEMS
    )
    omitted_items: int = Field(default=0, ge=0)
    #: Whether every item, listed or omitted, is a source an inventory read or
    #: was refused by (#812 follow-up). ``False`` exactly when the list also
    #: names a changed input this entry does not read (``changed_not_read``,
    #: #821). Neither value makes the list a complete account of what the
    #: change touched: the candidate rules are a bounded, documented list, and
    #: a changed file outside them that no reader reads is still not an item,
    #: its absence no claim about it.
    read_sources_only: bool = True
    #: Whether the comparison's changed-file set was matched against the
    #: candidate rules (#821): ``examined``, or ``not_examined`` when the set
    #: could not be listed, or was listed but its sides could not then be
    #: looked at, so no unread input is named and an absent one says nothing.
    #: ``None`` means not recorded: a ``0.20`` verifier, or a comparison built
    #: without its changed files.
    unread_candidates: Literal["examined", "not_examined"] | None = None
    #: Changed paths a candidate rule matched that were not examined, for
    #: either of two causes this one count does not tell apart: past the
    #: discovery bound, or the rule needed a file it could not use (a changed
    #: manifest or marketplace present on a side but not read within its
    #: bound, or a manifest a hook file could be named by that was not read or
    #: did not parse, while no readable one names it). Counted, never listed,
    #: and never counted in ``omitted_items``, which counts items that exist.
    unread_candidates_not_examined: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def coverage_shape(self):
        unread = any(item.status == "changed_not_read" for item in self.items)
        if unread and self.read_sources_only:
            raise ValueError("a list naming a changed input this entry does not read says so")
        if not self.read_sources_only and not unread and not self.omitted_items:
            raise ValueError("a list of read sources only says so")
        if self.unread_candidates != "examined" and (
            not self.read_sources_only or self.unread_candidates_not_examined
        ):
            raise ValueError("only an examined changed-file set names or bounds unread inputs")
        return self


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
            # A changed input this entry does not read is not a compared
            # source, so a refused comparison may name one beside its limits.
            if self.comparison_status == "incomparable" and statuses - {
                "blocking_limit",
                "changed_not_read",
            }:
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
