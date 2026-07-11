from __future__ import annotations

from .agent_summary import (
    _build_first_recommended_action,
    _top_active_finding,
    build_agent_summary,
)
from .constants import FINGERPRINT_EXCLUDED_EVIDENCE_KEYS, SEVERITY_ORDER
from .identity import (
    _canonicalize_for_fingerprint,
    _collision_discriminator,
    assign_finding_ids,
    dedupe_findings,
    finding_fingerprint,
    legacy_name_fingerprint,
    legacy_policy_routing_fingerprint,
)
from .mutations import (
    NO_HEURISTICS_SUPPRESSION_REASON,
    _matching_suppression,
    _severity_override_for_check,
    apply_no_heuristics_filter,
    apply_severity_overrides,
    apply_suppressions,
)
from .provenance import PROVENANCE_KIND_ORDER, provenance_kind_counts
from .remediation import (
    _REMEDIATION_FALLBACK,
    _derive_from_patches,
    annotate_remediation,
    derive_agent_action,
)
from .report_builder import build_report
from .reviewer_summary import (
    _action_surface_changes,
    _baseline_integrity_issue_count,
    _capability_misalignment_count,
    _evidence_matrix_gap_count,
    _pick_first_recommended_surface,
    _privacy_redaction_count,
    _reviewer_headline,
    _severity_override_counts,
    _tool_surface_changes,
    build_reviewer_summary,
)
from .summaries import (
    _has_mixed_evidence,
    _risk_tag_confidence,
    recommended_actions,
    summarize_findings,
    summarize_tool_surface,
    tool_inventory,
)
from .verifier_blocks import (
    build_capability_change,
    build_human_ack,
    build_protected_surface_changes,
    build_verifier_summary,
)

__all__ = [
    "FINGERPRINT_EXCLUDED_EVIDENCE_KEYS",
    "NO_HEURISTICS_SUPPRESSION_REASON",
    "PROVENANCE_KIND_ORDER",
    "SEVERITY_ORDER",
    "_REMEDIATION_FALLBACK",
    "_action_surface_changes",
    "_baseline_integrity_issue_count",
    "_build_first_recommended_action",
    "_canonicalize_for_fingerprint",
    "_capability_misalignment_count",
    "_collision_discriminator",
    "_derive_from_patches",
    "_evidence_matrix_gap_count",
    "_has_mixed_evidence",
    "_matching_suppression",
    "_pick_first_recommended_surface",
    "_privacy_redaction_count",
    "_reviewer_headline",
    "_risk_tag_confidence",
    "_severity_override_counts",
    "_severity_override_for_check",
    "_tool_surface_changes",
    "_top_active_finding",
    "annotate_remediation",
    "apply_no_heuristics_filter",
    "apply_severity_overrides",
    "apply_suppressions",
    "assign_finding_ids",
    "build_agent_summary",
    "build_capability_change",
    "build_human_ack",
    "build_protected_surface_changes",
    "build_report",
    "build_reviewer_summary",
    "build_verifier_summary",
    "dedupe_findings",
    "derive_agent_action",
    "finding_fingerprint",
    "legacy_policy_routing_fingerprint",
    "legacy_name_fingerprint",
    "provenance_kind_counts",
    "recommended_actions",
    "summarize_findings",
    "summarize_tool_surface",
    "tool_inventory",
]
