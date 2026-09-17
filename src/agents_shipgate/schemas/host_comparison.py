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
#: prefix in the order below, so the cap drops a compared, unchanged source
#: before anything a reviewer must read; ``omitted_items`` counts the rest.
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


class HostComparisonCoverageItem(BaseModel):
    """What one comparison established about one source (#812).

    Built only from facts the comparator already computed: the rows, the
    artifact changes, the sources each inventory observed and the blocking
    issues each carries. It is evidence, never a verdict: an item cannot make
    a comparison comparable, remove a row or authorize anything.

    - ``compared``: the source was read, and ``rows`` of the published rows
      come from it, including rows of a source inside the file such as a
      Codex profile (``<file>#profiles.<name>``). ``0`` means no change in
      what this entry reads — not that every field in the file was understood.
    - ``unread_fields_changed``: a file whose artifact digests the whole file
      changed, every side that has it parsed it, nothing but that digest
      differs, and no grant this entry reads from it changed, so it gives no
      row. Never a plugin manifest or marketplace, a retargeted link, or a
      Claude Code project settings file while a hook's loading basis changed.
    - ``changed_without_rows``: the source's published artifact changed and no
      row is attributed to it, but the data does not show the change was in
      fields no grant reads: a plugin manifest or marketplace ``hooks``
      reference (its rows are published under the hook files it selects), a
      retargeted link, or a parse or instruction-structure change.
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
    status: Literal["compared", "unread_fields_changed", "changed_without_rows", "blocking_limit"]
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
        if self.status in {"unread_fields_changed", "changed_without_rows"} and self.rows:
            raise ValueError("a change with no row attributed publishes no rows")
        return self


class HostComparisonCoverage(BaseModel):
    """The capped list of what a comparison established, source by source (#812).

    ``None`` on :class:`HostComparison` means coverage was not recorded — a
    verifier from before schema ``0.20``, or a comparison that never read an
    inventory. An empty ``items`` list with ``omitted_items == 0`` means the
    comparison read no source it could name.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[HostComparisonCoverageItem] = Field(
        default_factory=list, max_length=MAX_COVERAGE_ITEMS
    )
    omitted_items: int = Field(default=0, ge=0)


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
        return self
