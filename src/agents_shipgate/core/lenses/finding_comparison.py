"""Evidence diagnostics for the legacy fingerprint diff, never release scope.

The four shipped finding buckets compare identity. Neither those buckets nor
matching predicate support establish a dependency closure or change causality.
Keep that limit visible while retaining the base evidence needed to inspect a
match whose support moved (#515).
"""

from __future__ import annotations

from collections import defaultdict

from agents_shipgate.schemas.report import Finding

_LIMIT = (
    "Finding comparison is fingerprint-based, not attribution to this change. "
    "Shared-helper, binding and configuration dependency coverage is not established; "
    "matching findings do not prove unchanged authority."
)


def _groups(findings: list[Finding] | tuple[Finding, ...]) -> dict[str, list[Finding]]:
    groups: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        if not finding.suppressed and (key := finding.fingerprint or finding.id):
            groups[key].append(finding)
    return groups


def _locations(finding: Finding) -> str:
    paths = sorted({source.path or source.ref for source in (finding.source, finding.policy_evidence_source)
                    if source is not None and (source.path or source.ref)})
    return ", ".join(paths) or "source location unavailable"


def finding_comparison_notes(
    findings: list[Finding], base_evidence: tuple[Finding, ...] | None,
) -> list[str]:
    notes = [_LIMIT]
    if base_evidence is None:
        return [*notes, "Base finding evidence is unavailable; fingerprint matches cannot compare supporting evidence. Regenerate the base report from its source workspace to inspect that evidence."]
    before, after = _groups(base_evidence), _groups(findings)
    changed: list[str] = []
    ambiguous = 0
    missing_support = 0
    for key in sorted(before.keys() & after.keys()):
        if len(before[key]) != 1 or len(after[key]) != 1:
            ambiguous += 1
            continue
        old, new = before[key][0], after[key][0]
        axes = []
        if old.support is None or new.support is None:
            missing_support += 1
        # Compare the carried content too: a supplied digest alone is not
        # proof that two serialized support objects express the same thing.
        if old.support != new.support:
            axes.append("predicate support")
        if (old.source, old.policy_evidence_source) != (new.source, new.policy_evidence_source):
            axes.append("source references")
        if (old.tool_id, old.agent_id, old.capability_refs) != (new.tool_id, new.agent_id, new.capability_refs):
            axes.append("capability subjects")
        if (old.severity, old.confidence, old.blocks_release) != (new.severity, new.confidence, new.blocks_release):
            axes.append("release contribution")
        if axes:
            subject = new.tool_name or old.tool_name or new.title
            changed.append(
                f"{subject} ({new.check_id}) — changed evidence: {', '.join(axes)}; "
                f"base: {_locations(old)}; head: {_locations(new)}. "
                "Inspect both reports; this comparison does not establish widening or improvement."
            )
    if changed or ambiguous or missing_support:
        notes.append(
            f"Evidence comparison: {len(changed)} matched finding(s) have changed evidence; "
            f"{missing_support} match(es) have predicate support unavailable on one or both sides; "
            f"{ambiguous} ambiguous finding identities were not compared. "
            "All matched rows remain in the legacy unchanged_findings identity bucket."
        )
    return [*notes, *changed]
