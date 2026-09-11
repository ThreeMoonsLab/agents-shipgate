"""Join the shipped comparison profiles onto the findings they support.

``finding_deltas`` answers a question about *identity*: is this fingerprint in
the base report?  #515 needs a different answer — did *this change* widen,
bound, or leave alone the capability the finding is about — and the evidence
for it already exists, split across two profile surfaces with two private
vocabularies and no join to the findings that gate.

This module is that join and nothing else.  It reads no source, reconstructs no
tree and computes no second diff: every direction it reports is copied verbatim
out of a comparison row that ``compare_operations`` or
``compare_guard_dependencies`` already published.  What it adds is one canonical
statement per finding, and the refusals that statement is allowed to make.

Three rules keep it from overclaiming, and each is the reason a whole class of
row lands on ``unresolved``:

1. Only **predicate-linked** evidence classifies.  A profile row earns that link
   by naming the finding's own fingerprint.  A row joined by canonical tool id
   is evidence about the same capability whose relation to this finding's
   predicate is unproved.
2. Capability-linked evidence may **withdraw** a claim, never establish one.
   ``standing_weakness`` and ``improved_not_resolved`` are negative claims about
   what did not get worse, so any capability-linked movement retracts them.
3. Absence is never agreement.  A finding no profile can compare gets no row at
   all and is counted in a note, and a matched finding whose modeled bounds are
   all unchanged is only ``standing_weakness`` while its dependency coverage is
   still published as ``incomplete``.
"""

from __future__ import annotations

from collections import Counter
from typing import NamedTuple

from agents_shipgate.schemas.finding_attribution import (
    AttributionEffect,
    AttributionLink,
    FindingAttribution,
    FindingAttributionClass,
    FindingAttributionEvidence,
    FindingIdentity,
)
from agents_shipgate.schemas.guard_dependencies import GuardDependencyComparison
from agents_shipgate.schemas.operation_attribution import OperationComparison
from agents_shipgate.schemas.report import Finding
from agents_shipgate.schemas.surfaces import ToolSurfaceDiff

_LIMIT = (
    "Finding attribution names the modeled bound this change moved. Dependency "
    "coverage is incomplete, so an unchanged bound is not proof that the "
    "capability is unchanged and no finding is excluded from the release "
    "decision."
)

# A domain that is neither equal to nor a subset of the base contains at least
# one member the base did not declare, so `changed` is a proved widening.
_DOMAIN_EFFECTS: dict[str, AttributionEffect] = {
    "unchanged": "unchanged",
    "narrowed": "narrowing",
    "widened": "widening",
    "changed": "widening",
    "unresolved": "unresolved",
}
_APPROVAL_EFFECTS: dict[str, AttributionEffect] = {
    "standing_weakness": "unchanged",
    "still_declared": "unchanged",
    "resolved_by_declaration": "narrowing",
    "newly_missing": "widening",
    "unresolved": "unresolved",
}
_ORDER = {
    "widened_by_change": 0,
    "improved_not_resolved": 1,
    "standing_weakness": 2,
    "unresolved": 3,
}
_GUARD_EFFECTS: dict[str, AttributionEffect] = {
    "predicate_unchanged": "unchanged",
    "predicate_narrowed": "narrowing",
    "predicate_widened": "widening",
    "predicate_changed": "widening",
    "unresolved": "unresolved",
}


def _operation_pointer(row) -> str | None:
    """``<document>#<json pointer>`` — a file a reviewer can actually open.

    A bare JSON pointer names a location in a document nobody has been told
    the name of, so the declaring document is carried with it whenever the
    profile recorded one.
    """
    if row is None:
        return None
    document = next(
        (item.path for item in row.operation.inputs if item.role == "openapi_document"),
        None,
    )
    pointer = row.operation.source_pointer
    return f"{document}#{pointer}" if document else pointer


def _guard_pointer(row) -> str | None:
    """The guard's own location, or nothing.

    An unresolved observation can carry a ``guard_path`` with no line, and can
    carry neither. The tool declaration is not a substitute: a pointer printed
    under a ``guard_predicate`` axis has to name the guard, so an unknown
    location stays absent rather than sending a reviewer to the wrong file.
    """
    if row is None or not row.guard_path:
        return None
    return f"{row.guard_path}:{row.guard_line}" if row.guard_line else row.guard_path


def _operation_evidence(
    comparison: OperationComparison, link: AttributionLink
) -> list[FindingAttributionEvidence]:
    base, head = _operation_pointer(comparison.before), _operation_pointer(comparison.after)
    return [
        FindingAttributionEvidence(
            profile="openapi_delete/v1",
            observation_id=comparison.observation_id,
            link=link,
            axis=axis,
            direction=direction,
            effect=table[direction],
            base_pointer=base,
            head_pointer=head,
        )
        for axis, direction, table in (
            ("approval_predicate", comparison.approval_predicate, _APPROVAL_EFFECTS),
            ("declared_target_domain", comparison.declared_target_domain, _DOMAIN_EFFECTS),
        )
    ]


def _guard_evidence(
    comparison: GuardDependencyComparison, link: AttributionLink
) -> list[FindingAttributionEvidence]:
    base, head = _guard_pointer(comparison.before), _guard_pointer(comparison.after)
    rows = [
        FindingAttributionEvidence(
            profile="sdk_boolean_guard/v1",
            observation_id=comparison.observation_id,
            link=link,
            axis="guard_predicate",
            direction=comparison.direction,
            effect=_GUARD_EFFECTS[comparison.direction],
            base_pointer=base,
            head_pointer=head,
        )
    ]
    if comparison.source_behavior is not None:
        direction = comparison.source_behavior.bound_true_domain
        rows.append(
            FindingAttributionEvidence(
                profile="sdk_boolean_guard/v1",
                observation_id=comparison.observation_id,
                link=link,
                axis="bound_true_domain",
                direction=direction,
                effect=_DOMAIN_EFFECTS[direction],
                base_pointer=base,
                head_pointer=head,
            )
        )
    return rows


def _operation_fingerprints(comparison: OperationComparison) -> set[str]:
    return {
        fingerprint
        for side in (comparison.before, comparison.after)
        if side is not None
        for fingerprint in side.finding_fingerprints
    }


def _operation_tool_ids(comparison: OperationComparison) -> set[str]:
    return {
        side.tool_id
        for side in (comparison.before, comparison.after)
        if side is not None and side.tool_id
    }


def _operation_capability_ids(comparison: OperationComparison) -> set[str]:
    return {
        side.capability_id
        for side in (comparison.before, comparison.after)
        if side is not None and side.capability_id
    }


def _guard_tool_ids(comparison: GuardDependencyComparison) -> set[str]:
    ids = {row.tool_id for row in (comparison.before, comparison.after) if row is not None}
    ids.add(comparison.tool_id)
    return {value for value in ids if value}


def _asks_the_question(diff: ToolSurfaceDiff) -> bool:
    """Whether this run compared a head against a base at all.

    Read off the diff itself rather than a caller flag: a reconstructed Git
    operation base is provenance the public report cannot set, and it can carry
    a comparison even where no report or baseline reference was supplied.
    """
    if not diff.operation_comparisons and not diff.guard_comparisons:
        return False
    return diff.base.kind != "none" or any(
        row.evidence_source == "reconstructed_git_base" for row in diff.operation_comparisons
    )


def _identity(diff: ToolSurfaceDiff) -> dict[str, FindingIdentity]:
    deltas = diff.finding_deltas
    identity: dict[str, FindingIdentity] = {}
    for bucket, label in (
        (deltas.new_findings, "new"),
        (deltas.unchanged_findings, "matched"),
        (deltas.accepted_debt, "accepted_debt"),
    ):
        for row in bucket:
            identity[row.fingerprint] = label
    return identity


def _classify(
    identity: FindingIdentity,
    predicate: list[FindingAttributionEvidence],
    capability: list[FindingAttributionEvidence],
    *,
    ambiguous: bool,
) -> tuple[FindingAttributionClass, str]:
    if ambiguous:
        # A profile row names a fingerprint. Where two active findings answer
        # to that fingerprint, no row can say which of them it attributes.
        return "unresolved", (
            "Two or more active findings share this identity, so no comparison "
            "row can say which of them it names."
        )
    if not predicate:
        return "unresolved", (
            "No comparison profile names this finding's fingerprint, so the bound "
            "that made it eligible was not compared."
            + (
                " Evidence on the same capability is carried in this row and "
                "does not attribute this finding."
                if capability
                else ""
            )
        )
    effects = {row.effect for row in predicate}
    capability_effects = {row.effect for row in capability}
    if "unresolved" in effects:
        return "unresolved", (
            "A compared axis of this finding's own bound is unresolved; the "
            "profile refused to state a direction."
        )
    if "widening" in effects:
        return "widened_by_change", (
            "This change removed a bound this finding depends on, or grew the "
            "compared domain of the capability it names."
        )
    if "narrowing" in effects:
        if "widening" in capability_effects:
            return "unresolved", (
                "This finding's own bound was narrowed, but another modeled bound "
                "on the same capability widened; the net direction is unresolved."
            )
        standing = (
            "the finding still stands"
            if identity == "matched"
            else f"a finding of this shape is present at head (identity {identity})"
        )
        return "improved_not_resolved", (
            f"This change added or narrowed a bound this finding depends on and "
            f"{standing}. It is an improvement, not the finding's cause."
        )
    if identity != "matched":
        # `accepted_debt` is not proof of absence from the base either: the
        # baseline bucket is filled before the identity buckets are, so a row
        # there says nothing about whether the base carried the fingerprint.
        return "unresolved", (
            "Every compared bound is unchanged, but this finding is not an "
            f"unchanged base identity ({identity}); what moved its identity was "
            "not modeled."
        )
    if capability_effects - {"unchanged"}:
        return "unresolved", (
            "Every compared bound of this finding is unchanged, but another "
            "modeled bound on the same capability moved or could not be compared."
        )
    return "standing_weakness", (
        "The finding matched the base and every modeled bound it depends on is "
        "unchanged. Dependency coverage is incomplete, so this is not proof the "
        "capability is unchanged."
    )


class FindingAttributionResult(NamedTuple):
    """Rows, the findings no profile could compare, and the limit notes.

    ``unattributed`` is returned as a value rather than only as prose because
    the prose is a diff note, and ``report.md`` renders the first three notes
    only. The one statement that keeps an empty attribution from reading as a
    clean one cannot live where a fourth note is dropped.
    """

    rows: list[FindingAttribution]
    unattributed: int
    notes: list[str]


def attribute_findings(
    diff: ToolSurfaceDiff, findings: list[Finding]
) -> FindingAttributionResult:
    """Return one attribution row per comparable finding, and what was skipped.

    A finding no profile can join gets no row: an empty attribution is the
    absence of evidence, and ``unattributed`` counts those findings so absence
    is never read as agreement.
    """
    if not _asks_the_question(diff):
        # Either no profile is active, or the run has no base and is therefore
        # not asking what a change did. Attributing findings to a change that
        # was never described would print "unresolved" against every finding of
        # every plain `scan`, and the diff notes already say a base is missing.
        return FindingAttributionResult([], 0, [])
    identity = _identity(diff)
    # Built once, not once per (finding, comparison) pair: the evidence rows
    # differ between the two links only by that word, and the join keys are
    # properties of the comparison alone.
    operations = [
        (
            _operation_fingerprints(comparison),
            _operation_tool_ids(comparison),
            _operation_capability_ids(comparison),
            _operation_evidence(comparison, "predicate"),
            _operation_evidence(comparison, "capability"),
        )
        for comparison in diff.operation_comparisons
    ]
    guards = [
        (_guard_tool_ids(comparison), _guard_evidence(comparison, "capability"))
        for comparison in diff.guard_comparisons
    ]
    active = [finding for finding in findings if not finding.suppressed]
    shared = Counter(
        fingerprint
        for finding in active
        if (fingerprint := finding.fingerprint or finding.id)
    )
    unattributed = 0
    rows: list[FindingAttribution] = []
    for finding in active:
        fingerprint = finding.fingerprint or finding.id
        if not fingerprint:
            # No identity to join on is the strongest form of "not compared",
            # so it is counted rather than skipped. Both fields are optional
            # and a plugin check can supply neither.
            unattributed += 1
            continue
        refs = set(finding.capability_refs)
        predicate: list[FindingAttributionEvidence] = []
        capability: list[FindingAttributionEvidence] = []
        for fingerprints, tool_ids, capability_ids, named, same_capability in operations:
            # Copied, so each attribution row owns the evidence it publishes
            # rather than sharing one mutable model with every other finding on
            # the same capability.
            if fingerprint in fingerprints:
                predicate.extend(row.model_copy() for row in named)
            elif (finding.tool_id and finding.tool_id in tool_ids) or (capability_ids & refs):
                capability.extend(row.model_copy() for row in same_capability)
        for tool_ids, evidence in guards:
            # Guard evidence carries no fingerprint, so it is only ever
            # capability-linked, and only through a canonical tool id — a
            # display name is not an identity.
            if finding.tool_id and finding.tool_id in tool_ids:
                capability.extend(row.model_copy() for row in evidence)
        if not predicate and not capability:
            unattributed += 1
            continue
        bucket = identity.get(fingerprint, "unresolved")
        attribution, reason = _classify(
            bucket, predicate, capability, ambiguous=shared[fingerprint] > 1
        )
        rows.append(
            FindingAttribution(
                fingerprint=fingerprint,
                check_id=finding.check_id,
                tool_id=finding.tool_id,
                tool_name=finding.tool_name,
                identity=bucket,
                attribution=attribution,
                reason=reason,
                evidence=[*predicate, *capability],
            )
        )
    # Most-consequential first, then a stable identity tie-break, so a rerun on
    # an unchanged repository prints an unchanged block and the Markdown
    # truncation never hides a widening behind a documentation finding.
    rows.sort(key=lambda row: (_ORDER[row.attribution], row.check_id, row.fingerprint))
    if not rows and not unattributed:
        return FindingAttributionResult([], 0, [])
    notes = [_LIMIT]
    if unattributed:
        notes.append(unattributed_sentence(unattributed))
    return FindingAttributionResult(rows, unattributed, notes)


def unattributed_sentence(count: int) -> str:
    """The one spelling of the count, for every surface that prints it.

    The wording covers both causes: a finding no active profile could speak
    about, and a finding that published no identity for one to join on. Naming
    only the first would send a reader to look at profile coverage for a
    finding no coverage could have reached.
    """
    return (
        f"{count} active finding(s) could not be joined to any comparison "
        "profile and are not attributed to this change in either direction."
    )
