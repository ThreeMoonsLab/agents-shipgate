from __future__ import annotations

from agents_shipgate.schemas.common import ProvenanceKind
from agents_shipgate.schemas.report import Finding

PROVENANCE_KIND_ORDER: tuple[ProvenanceKind, ...] = (
    "static_declaration",
    "ast_extraction",
    "keyword_heuristic",
    "regex_heuristic",
    "policy_pack",
    "runtime_trace",
)


def provenance_kind_counts(
    findings: list[Finding],
    *,
    include_suppressed: bool = False,
) -> dict[ProvenanceKind, int]:
    """Count findings by provenance kind in the public enum order.

    This is a reviewer triage helper only. It must not feed release
    gating, severity, fingerprints, baselines, or CI exit behavior.
    """

    counts = {kind: 0 for kind in PROVENANCE_KIND_ORDER}
    for finding in findings:
        if finding.suppressed and not include_suppressed:
            continue
        # Legacy/plugin compatibility: CLI report filtering pre-validates
        # provenance_kind, while renderers coerce older in-memory findings.
        kind = finding.provenance_kind or "static_declaration"
        counts[kind] += 1
    return counts
