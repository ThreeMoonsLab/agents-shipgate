"""Attribution of one finding to a modeled capability bound the change moved.

This is a *projection* over the comparison rows the source profiles already
publish (``tool_surface_diff.operation_comparisons`` and
``tool_surface_diff.guard_comparisons``); it reads no source and computes no
second diff.  It answers "did this change widen, bound or leave alone the
capability this finding is about?" and nothing else.

It is not a release decision, not a severity, and not permission to drop a
finding: ``dependency_coverage`` stays ``incomplete`` and
``finding_exclusion_eligible`` stays ``False`` on every row, because no
profile has yet proved a complete shared-helper/import/configuration closure
for a capability (#557).  A ``standing_weakness`` row means *no modeled bound
changed*, never *nothing changed*.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: How one compared axis moved the capability the finding is about.
#:
#: ``widening`` is a proved statement, not a guess: a compared domain that is
#: neither equal nor a subset of the base declares at least one member the base
#: did not, so ``changed`` maps here rather than to ``unresolved``.
AttributionEffect = Literal["widening", "narrowing", "unchanged", "unresolved"]

#: ``predicate`` — the profile row names this finding's own fingerprint, so the
#: compared bound is the one that made the finding eligible.
#: ``capability`` — the row was joined only by canonical tool id, so it is a
#: bound on the same capability whose relation to *this* finding's predicate is
#: unproved.  Capability-linked evidence is recorded and can withdraw a
#: negative claim; it can never establish one.
AttributionLink = Literal["predicate", "capability"]

FindingAttributionClass = Literal[
    # A bound the finding depends on was removed, or the capability's compared
    # domain grew.  Under a diff-scoped question this is the change's finding.
    "widened_by_change",
    # A bound was added or narrowed and the finding still stands — a strict
    # improvement that did not perfect the surface (#515's `cal-1` shape).
    "improved_not_resolved",
    # Finding identity matched the base and every joined comparison says the
    # modeled bounds are unchanged.  Dependency coverage is still incomplete.
    "standing_weakness",
    # No comparable predicate evidence, an unresolved or ambiguous comparison,
    # or a capability-linked contradiction.  Never read as safe.
    "unresolved",
]

#: Which fingerprint bucket of ``finding_deltas`` this finding is in.
#: ``matched`` is ``unchanged_findings`` — an identity match, not a safety claim.
#: ``unresolved`` is a finding no bucket named, which happens when there is no
#: base to compare identity against; it is never reported as ``new``.
FindingIdentity = Literal["new", "matched", "accepted_debt", "unresolved"]


class FindingAttributionEvidence(BaseModel):
    """One compared axis of one profile row, and where to read both sides."""

    model_config = ConfigDict(extra="forbid")

    profile: Literal["openapi_delete/v1", "sdk_boolean_guard/v1"]
    observation_id: str
    link: AttributionLink
    axis: Literal[
        "approval_predicate",
        "declared_target_domain",
        "guard_predicate",
        "bound_true_domain",
    ]
    # The profile's own vocabulary, verbatim, so the row can be joined back to
    # the comparison it came from without a second spelling of the direction.
    direction: str
    effect: AttributionEffect
    # The named thing to open on each side; absent when that side has no row.
    base_pointer: str | None = None
    head_pointer: str | None = None


class FindingAttribution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    check_id: str
    tool_id: str | None = None
    tool_name: str | None = None
    identity: FindingIdentity
    attribution: FindingAttributionClass
    reason: str
    evidence: list[FindingAttributionEvidence] = Field(default_factory=list)
    dependency_coverage: Literal["incomplete"] = "incomplete"
    finding_exclusion_eligible: Literal[False] = False
