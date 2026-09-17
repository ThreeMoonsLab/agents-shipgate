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
