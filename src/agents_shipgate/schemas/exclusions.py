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
#: ``unverified``
#:     The base comparison that would decide between the two rows above could
#:     not be performed, so the run cannot say whether this change introduced
#:     the subject. A gap naming the unavailable comparison stands in for the
#:     per-subject one — the honest state, and the fail-closed one: a
#:     ``not_claimed`` record here would assert a comparison nobody ran.
#: ``not_claimed``
#:     Nothing in the repository claims the subject is capability of the agent
#:     under review, and this change did not introduce it. Reported for the
#:     reviewer, deliberately not gated — see the module docstring for why a
#:     blanket block would be wrong here.
ExclusionAccounting = Literal[
    "evidence_gap", "route_blocked", "unverified", "not_claimed"
]

#: Bound on ``SurfaceExclusionLedger.entries``. A change set with thousands of
#: unclassified paths, or a catalog with thousands of unwired operations, must
#: not turn a report into a copy of its own inputs. ``total`` stays exact and
#: ``truncated`` says the list is a prefix, so no consumer has to guess.
MAX_LEDGER_ENTRIES = 200


#: Ledger order. ``evidence_gap`` first so the cap can keep every row the
#: conservation check joins against, then the other acted-on rows, then the
#: merely-recorded ones; ties broken deterministically.
_ACCOUNTING_RANK = {"evidence_gap": 0, "route_blocked": 1, "unverified": 2}


def _ledger_sort_key(row: SurfaceExclusion) -> tuple[int, str, str, str]:
    return (
        _ACCOUNTING_RANK.get(row.accounting, 3),
        row.stage,
        row.subject,
        row.reason,
    )


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

        # Sorting gated rows first is not enough on its own: a plain
        # ``rows[:limit]`` still drops them once there are more than ``limit``,
        # which is precisely the row the conservation check reads (PR #404
        # review). So the cap applies to the *rest*, and an ``evidence_gap``
        # row is never discarded — it is bounded already by the evidence-gap
        # list it joins against, which the report carries in full anyway.
        rows = sorted(entries, key=_ledger_sort_key)
        gap_backed = [row for row in rows if row.accounting == "evidence_gap"]
        rest = rows[len(gap_backed):]
        budget = max(0, limit - len(gap_backed))
        kept = gap_backed + rest[:budget]
        return cls(
            entries=kept,
            total=len(rows),
            gated=sum(1 for row in rows if row.accounting != "not_claimed"),
            truncated=len(kept) < len(rows),
        )


__all__ = [
    "MAX_LEDGER_ENTRIES",
    "ExclusionAccounting",
    "ExclusionStage",
    "SurfaceExclusion",
    "SurfaceExclusionLedger",
]
