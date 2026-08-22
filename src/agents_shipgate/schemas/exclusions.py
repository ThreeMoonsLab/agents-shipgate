"""The exclusion ledger — one typed record per narrowing decision.

Every stage of the pipeline narrows the surface it hands to the next one: the
trigger decides a diff is irrelevant, the discovery walk stops at its file cap,
scope resolution picks one directory, the binding graph drops a tool the root
cannot reach, an adapter fails to parse a region, a source type cannot prove
its surface is complete. Each of those removes a subject from analysis, and
until #403 each recorded the removal a different way — or not at all.

The failure that motivated this is worth stating precisely, because it is the
shape the product exists to catch. ``github/github-mcp-server#3076`` adds
``delete_repository`` (``destructiveHint: true``) to a 117-tool MCP server. The
scan computed ``unbound_tools: 1`` and reported ``gap_count: 0``,
``pass_eligible: true``: it *knew* one tool had left the analysed surface, and
the release decision could not see that it knew. The checks that would have
blocked the tool are correct; the tool never reached them.

So the record is not another diagnostic. It is the join between "a stage
narrowed" and "the decision can read it":

    observed == analysed ∪ excluded          (conservation)
    excluded ≠ ∅  ⟹  every exclusion is accounted for

``accounted for`` is deliberately two-valued rather than "everything blocks".
A tool source is often a *catalog* — an OpenAPI spec declaring 63 operations of
which an agent wires 5 — and catalog membership is not evidence of capability
(``samples/large_multi_framework_agent``). Gating on those would make declaring
a spec self-blocking, which is why #385 drew the boundary where it did. What
was missing was not a blanket block but the *record*: an exclusion may be
``not_claimed`` only when nothing in the repository claims the subject as
capability, and that claim is now written down and checked rather than assumed
at each call site.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: The pipeline stage that removed the subject from the analysed surface. One
#: value per narrowing site; adding a stage means teaching that site to emit,
#: not inventing a second representation for the same event class.
ExclusionStage = Literal[
    # The trigger decided a change set did not warrant a scan.
    "trigger",
    # The discovery walk stopped before it had read the workspace.
    "discovery",
    # Manifest-scope resolution attributed the workspace to one directory.
    "scope_resolution",
    # The binding graph could not connect a catalog tool to the root agent.
    "binding",
    # An adapter matched an input but could not read all of it.
    "adapter_parse",
    # A tool's own surface enumeration could not be established as complete.
    "surface_completeness",
]

#: How the exclusion reaches the decision it affects. Every value is a claim
#: something else can be checked against — that is what makes "accounted for"
#: mean more than "written down".
#:
#: ``evidence_gap``
#:     A row in ``release_decision.evidence_coverage.evidence_gaps[]`` names
#:     this subject, so the verdict is computed with the narrowing in view.
#:     The conservation check asserts the row exists — a record claiming this
#:     without a matching gap is the exact ``unbound_tools: 1 / gap_count: 0``
#:     state the ledger exists to make unrepresentable.
#: ``route_blocked``
#:     The stage declined to publish a verdict over the exclusion, and its
#:     ``next_action`` repairs it. Used by stages that run before any release
#:     decision exists: a trigger that classified nothing withholds the skip,
#:     a discovery walk stopped at its cap withholds the classification.
#: ``not_claimed``
#:     Nothing in the repository claims the subject is capability of the agent
#:     under review, and this change did not introduce it. Reported for the
#:     reviewer, deliberately not gated — see the module docstring for why a
#:     blanket block would be wrong here.
ExclusionAccounting = Literal["evidence_gap", "route_blocked", "not_claimed"]

#: Bound on ``SurfaceExclusionLedger.entries``. A change set with thousands of
#: unclassified paths, or a catalog with thousands of unwired operations, must
#: not turn a report into a copy of its own inputs. ``total`` stays exact and
#: ``truncated`` says the list is a prefix, so no consumer has to guess.
MAX_LEDGER_ENTRIES = 200


class SurfaceExclusion(BaseModel):
    """One subject a stage removed from the analysed surface."""

    model_config = ConfigDict(extra="forbid")

    stage: ExclusionStage
    #: What was removed — a tool id, a changed path, a workspace directory.
    #: Stable enough to join against the surface it was removed from.
    subject: str
    #: Stable token for *why*, scoped to the stage. Machine-readable; the
    #: prose lives in ``detail``.
    reason: str
    #: Where the subject came from: a tool source id, a file path, a manifest
    #: pointer. ``None`` when the stage has no narrower pointer than itself.
    source_ref: str | None = None
    #: One sentence a reviewer can act on.
    detail: str
    accounting: ExclusionAccounting


class SurfaceExclusionLedger(BaseModel):
    """The exclusions of one run, plus the counts a consumer gates on.

    Kept as an envelope rather than a bare list so ``truncated`` has somewhere
    to live: a change set with ten thousand unclassified paths must not turn
    the report into a copy of the diff, and a consumer reading a bounded list
    has to be able to tell "these are all of them" from "these are the first
    ``len(entries)``".
    """

    model_config = ConfigDict(extra="forbid")

    entries: list[SurfaceExclusion] = Field(default_factory=list)
    #: Total exclusions observed, including any beyond ``entries``.
    total: int = 0
    #: Exclusions the run acted on — ``evidence_gap`` or ``route_blocked``.
    #: ``total - gated`` is what was recorded and deliberately not acted on.
    gated: int = 0
    #: True when ``entries`` is a bounded prefix of the observed exclusions.
    truncated: bool = False

    @property
    def stages(self) -> list[str]:
        """Distinct stages present, in ledger order."""

        return list(dict.fromkeys(entry.stage for entry in self.entries))

    @classmethod
    def from_entries(
        cls,
        entries: Sequence[SurfaceExclusion],
        *,
        limit: int = MAX_LEDGER_ENTRIES,
    ) -> SurfaceExclusionLedger:
        """Bound the list, keep the counts exact.

        The one constructor every emitter uses, so ``total``/``gated`` can
        never disagree with ``entries`` — a ledger that under-counts its own
        exclusions is the failure it exists to prevent, wearing a new hat.
        """

        # Gated rows sort first, so truncation drops the exclusions the
        # decision did not act on rather than the ones it did. A cap that can
        # discard the row proving a subject was gated would let the
        # conservation check pass on a ledger that no longer shows why.
        rows = sorted(
            entries,
            key=lambda row: (
                0 if row.accounting != "not_claimed" else 1,
                row.stage,
                row.subject,
                row.reason,
            ),
        )
        return cls(
            entries=rows[:limit],
            total=len(rows),
            gated=sum(1 for row in rows if row.accounting != "not_claimed"),
            truncated=len(rows) > limit,
        )


__all__ = [
    "MAX_LEDGER_ENTRIES",
    "ExclusionAccounting",
    "ExclusionStage",
    "SurfaceExclusion",
    "SurfaceExclusionLedger",
]
