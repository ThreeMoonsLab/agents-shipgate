"""Regenerate the JSON-Schema and check-catalog files under docs/.

Run from the repo root:

    python scripts/generate_schemas.py            # write
    python scripts/generate_schemas.py --check    # verify no drift; exit 1 on diff

Writes / verifies:
- docs/manifest-v0.1.json       (from agents_shipgate.schemas.manifest)
- docs/checks.json              (from agents_shipgate.checks.registry.check_catalog)
- docs/report-schema.v0.<minor>.json
                                (from agents_shipgate.schemas.report.ReadinessReport;
                                 minor derived from report_schema_version default)
- docs/policy-pack-schema.v0.1.json
                                (from agents_shipgate.schemas.policy_pack.
                                 PolicyPackArtifactV1)
- docs/packet-schema.v0.<minor>.json
                                (from agents_shipgate.schemas.packet.EvidencePacket)
- docs/verifier-schema.v0.1.json
                                (from agents_shipgate.schemas.verifier.VerifierArtifact)
- docs/verify-run-schema.v1.json
                                (from agents_shipgate.schemas.verify_run.
                                 VerifyRunArtifact)
- docs/agent-handoff-schema.v1.json
                                (from agents_shipgate.schemas.agent_handoff.
                                 AgentHandoffArtifact)
- docs/agent-result-schema.v1.json
                                (legacy local-agent protocol schema from
                                 agents_shipgate.schemas.agent_result_v1.
                                 AgentResultV1)
- docs/preflight-schema.v0.2.json
                                (from agents_shipgate.schemas.preflight.
                                 PreflightResultV2)
- docs/org-governance-schema.v0.1.json
                                (from agents_shipgate.schemas.org_governance.
                                 OrgGovernanceStatusV1)
- docs/org-evidence-bundle-schema.v1.json
                                (from agents_shipgate.schemas.org_evidence_bundle.
                                 OrgEvidenceBundleArtifactV1)
- docs/registry-schema.v0.3.json
                                (from agents_shipgate.schemas.registry.
                                 RegistryQueryResultV1)
- docs/host-grants-inventory-schema.v0.1.json
                                (from agents_shipgate.schemas.host_grants.
                                 HostGrantsInventoryArtifactV1)
- docs/capability-lock-schema.v0.3.json
                                (from agents_shipgate.schemas.capabilities.
                                 CapabilityLockFileArtifactV1)
- docs/capability-lock-diff-schema.v0.4.json
                                (from agents_shipgate.schemas.capabilities.
                                 CapabilityLockDiffArtifactV1)
- docs/capability-intent-schema.v0.1.json
- docs/capability-observation-schema.v0.1.json
- docs/capability-reconciliation-schema.v0.1.json
                                (experimental local reconciliation contracts
                                 from agents_shipgate.schemas.
                                 capability_reconciliation)
- docs/governance-benchmark-catalog-schema.v0.2.json
                                (from agents_shipgate.schemas.governance_benchmark.
                                 GovernanceBenchmarkCatalogArtifactV1)
- docs/governance-benchmark-result-schema.v0.2.json
                                (from agents_shipgate.schemas.governance_benchmark.
                                 GovernanceBenchmarkResultArtifactV1)

``--check`` mode is the M4 trust-hardening gate: it generates each schema in
memory (running the same post-processing as ``write``) and compares it to the
committed file. Drift exits non-zero with a unified diff preview, so a Pydantic
model edit that forgets to regenerate fails CI fast with an actionable message.

Tests should import ``build_*_schema`` directly — they return ``(Path, str)``
tuples without touching disk, so unit tests stay subprocess-free.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import get_args

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"
SRC = REPO_ROOT / "src"

# Allow `python scripts/generate_schemas.py` from a checkout without install.
sys.path.insert(0, str(SRC))

from agents_shipgate.schemas.common import AgentAction, ProvenanceKind  # noqa: E402

# --- Shared helpers ---------------------------------------------------------

# Canonical JSON form for every schema we emit. Matches the v0.x convention
# already on disk: 2-space indent, sorted keys, trailing newline. Tests and
# the --check path both consume this exact form, so any future field reorder
# in Pydantic stays diffable as one logical change.
def _canonical_json(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


_DIFF_PREVIEW_LINES = 40


def _emit(target: Path, content: str, *, check_only: bool, drift: list[str]) -> bool:
    """Write ``content`` to ``target`` (write mode) or compare (check mode).

    In check mode, on mismatch, appends a short unified-diff preview to
    ``drift`` and returns False; the caller aggregates and exits 1. In write
    mode, always writes and returns True.
    """
    try:
        relative = target.relative_to(REPO_ROOT)
    except ValueError:
        # Target outside the repo (e.g., test fixture with monkeypatched DOCS).
        # Fall back to the bare path so error messages stay readable.
        relative = target
    if check_only:
        if not target.exists():
            drift.append(f"{relative}: missing (run scripts/generate_schemas.py)")
            return False
        existing = target.read_text(encoding="utf-8")
        if existing == content:
            return True
        diff_lines = list(
            difflib.unified_diff(
                existing.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"{relative} (committed)",
                tofile=f"{relative} (generated)",
                n=2,
            )
        )
        preview = "".join(diff_lines[:_DIFF_PREVIEW_LINES])
        suffix = (
            f"\n... ({len(diff_lines) - _DIFF_PREVIEW_LINES} more diff lines truncated)\n"
            if len(diff_lines) > _DIFF_PREVIEW_LINES
            else ""
        )
        drift.append(f"{relative}: drift detected\n{preview}{suffix}")
        return False
    target.write_text(content, encoding="utf-8")
    print(f"Wrote {relative}")
    return True


def build_manifest_schema() -> tuple[Path, str]:
    from agents_shipgate.schemas.manifest import AgentsShipgateManifest

    schema = AgentsShipgateManifest.model_json_schema()
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        "main/docs/manifest-v0.1.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Agents Shipgate Manifest v0.1"
    schema["description"] = (
        "JSON Schema for shipgate.yaml. Generated from "
        "agents_shipgate.schemas.manifest.AgentsShipgateManifest. Do not edit by hand."
    )

    # v0.17 (M1): `checks.severity_overrides` accepts both the legacy
    # scalar form (``SHIP-XYZ: medium``) and the rich form
    # (``SHIP-XYZ: { severity, reason, expires }``). At the Python level
    # the field type is ``dict[str, SeverityOverrideEntry]`` after a
    # ``mode="before"`` validator coerces scalars. The Pydantic
    # autogenerated schema only sees the post-coercion type, so we
    # surface the accepted-input union explicitly here. Without this
    # override the JSON Schema would reject every legacy scalar manifest.
    defs = schema.get("$defs", {})
    if "ChecksConfig" in defs:
        checks_props = defs["ChecksConfig"].setdefault("properties", {})
        if "severity_overrides" in checks_props and "SeverityOverrideEntry" in defs:
            checks_props["severity_overrides"] = {
                "type": "object",
                "additionalProperties": {
                    "anyOf": [
                        {
                            "type": "string",
                            "enum": ["info", "low", "medium", "high", "critical"],
                        },
                        {"$ref": "#/$defs/SeverityOverrideEntry"},
                    ]
                },
                "title": "Severity Overrides",
                "description": (
                    "Per-check severity overrides. Accepts either a "
                    "severity scalar (legacy form) or a "
                    "SeverityOverrideEntry object with optional reason "
                    "and expires."
                ),
            }

    target = DOCS / "manifest-v0.1.json"
    return target, _canonical_json(schema)


def write_manifest_schema(*, check_only: bool = False, drift: list[str] | None = None) -> bool:
    target, content = build_manifest_schema()
    return _emit(target, content, check_only=check_only, drift=drift if drift is not None else [])


def build_report_schema() -> tuple[Path, str]:
    """Generate docs/report-schema.v0.<minor>.json from the Pydantic
    ReadinessReport model.

    The minor version is derived from ``ReadinessReport.report_schema_version``
    so a schema bump is one-step: change the default in models.py and rerun
    this script. CI's clean-tree assertion catches any field drift.

    Post-processing preserves v0.5's stable public contract (additive only):
    - ``schema_version`` and ``report_schema_version`` keep their version
      constants (Pydantic emits them as plain strings with defaults).
    - ``required`` keeps the v0.5 list of fields that consumers depend on,
      regardless of whether the Pydantic model marks them as having
      defaults. Optional v0.6 additions (``manifest_dir``, per-finding
      ``patches``) stay optional.
    """
    from agents_shipgate.schemas.report import ReadinessReport

    schema = ReadinessReport.model_json_schema()
    minor = ReadinessReport.model_fields["report_schema_version"].default
    title = f"Agents Shipgate Readiness Report v{minor}"
    schema_id = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/report-schema.v{minor}.json"
    )
    schema["$id"] = schema_id
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = title
    schema["description"] = (
        "JSON Schema for the Agents Shipgate Tool-Use Readiness Report. "
        "Generated from agents_shipgate.schemas.report.ReadinessReport with "
        "post-processing to preserve the v0.5 public contract. "
        "Do not edit by hand."
    )
    # Preserve v0.5's stable required list, plus v0.8/v0.9/v0.10 additions.
    # Optional intermediate additions (manifest_dir, per-finding patches)
    # are not added here, so they stay optional for additive consumers.
    # `release_decision` is required at v0.8, v0.9 capability diff fields and
    # v0.10 tool-surface diff fields are required for every emitted report.
    # Marking them required
    # at the schema level catches drift early.
    schema["required"] = sorted(
        [
            "schema_version",
            "report_schema_version",
            "run_id",
            "project",
            "agent",
            "environment",
            "summary",
            "release_decision",
            "capability_facts",
            "declared_intentions",
            "misalignments",
            "release_consequence",
            "suggested_scenarios",
            "tool_surface",
            "tool_surface_facts",
            "tool_surface_diff",
            "action_surface_facts",
            "action_surface_diff",
            "frameworks",
            "codex_plugin_surface",
            "findings",
            "recommended_actions",
            "generated_reports",
            "loaded_policy_packs",
            "loaded_plugins",
            # v0.20: third-party adapter provenance (parallels
            # loaded_plugins[]). Optional in Python via
            # ``Field(default_factory=list)`` for test-helper minimal
            # reports; emitted scans always populate it (empty list
            # when --no-plugins is set or no third-party adapters are
            # installed). Required + non-nullable on the wire.
            "loaded_adapters",
            "tool_inventory",
            "source_warnings",
            # v0.12: agent_summary is the deterministic top-level
            # summary block. The Pydantic model marks it Optional so
            # older test helpers can construct minimal reports — but
            # every emitted report runs build_report() which always
            # populates it. Mark required at the schema level so a
            # payload missing the field fails validation.
            "agent_summary",
            # v0.17 (M1): policy_audit is the top-of-report audit envelope
            # for severity overrides applied during scan. Optional in
            # Python for back-compat with older fixtures; emitted scans
            # always populate it (empty envelope when no overrides), so
            # we mark it required + non-nullable on the wire.
            "policy_audit",
            "privacy_audit",
            # v0.21: heuristics_filter is the top-of-report audit envelope
            # for the --no-heuristics CLI flag. Optional in Python for
            # back-compat with older fixtures; emitted scans always
            # populate it (empty envelope with enabled=false when the
            # flag is not set), so we mark it required + non-nullable
            # on the wire. Consumers can read
            # ``heuristics_filter.filtered_finding_count`` without a
            # null check.
            "heuristics_filter",
            # v0.20: reviewer_summary parallels agent_summary for the
            # audit/lens dimensions. Optional in Python for back-compat
            # with test helpers; emitted scans always populate it via
            # build_report() → build_reviewer_summary().
            "reviewer_summary",
            # v0.22 (verifier cycle, P2/M3): the five new verifier blocks.
            # Optional in Python for back-compat with test helpers;
            # emitted scans always populate them (deterministic
            # empty/default instances in Phase A) via
            # _build_final_report() → build_*. Required + non-nullable on
            # the wire so consumers can read them without an existence
            # check. ``protected_surface_changes`` is a plain list (always
            # present, may be empty); the other four are single blocks
            # tightened to a direct $ref below.
            "capability_change",
            "protected_surface_changes",
            "effective_policy",
            "human_ack",
            "verifier_summary",
        ]
    )
    # Preserve version constants. Pydantic emits these as plain strings
    # with `default`, but consumers may validate `const` against the
    # actual report shape.
    properties = schema.setdefault("properties", {})
    properties["schema_version"] = {"const": "0.1"}
    properties["report_schema_version"] = {"const": minor}
    # v0.8: tighten release_decision to a direct $ref. The Pydantic
    # model declares `release_decision: ReleaseDecision | None = None`
    # so older test fixtures and SARIF-only callers can construct
    # minimal reports — but every emitted report has it populated.
    # Without this override the schema would emit
    # `anyOf: [ReleaseDecision, null]`, which would let `null` pass
    # validation and silently violate the v0.8 contract.
    properties["release_decision"] = {"$ref": "#/$defs/ReleaseDecision"}
    properties["release_consequence"] = {"$ref": "#/$defs/ReleaseConsequence"}
    # v0.12: tighten agent_summary the same way as release_decision —
    # Optional in Python for back-compat, required + non-nullable on
    # the wire. Without this override the schema emits
    # `anyOf: [AgentSummary, null]`, which would let a payload silently
    # ship with `agent_summary: null` and violate the v0.12 contract.
    properties["agent_summary"] = {"$ref": "#/$defs/AgentSummary"}
    # v0.17 (M1): same tightening for policy_audit. Pydantic emits
    # `anyOf: [PolicyAudit, null]` for the Optional Python field; on
    # the wire every emitted report carries a real PolicyAudit
    # envelope (may be empty), never null. The const + non-nullable
    # form lets consumers read ``policy_audit.severity_overrides_applied``
    # without a null check.
    properties["policy_audit"] = {"$ref": "#/$defs/PolicyAudit"}
    # v0.18: same tightening for privacy_audit. Emitted scans always
    # carry the default-on privacy envelope after public output redaction.
    properties["privacy_audit"] = {"$ref": "#/$defs/PrivacyAudit"}
    # v0.21: same tightening for heuristics_filter. Emitted scans always
    # carry the envelope (with enabled=false when --no-heuristics was
    # not set), so the wire shape is HeuristicsFilter, never null. The
    # const + non-nullable form lets consumers read
    # ``heuristics_filter.filtered_finding_count`` without a null check.
    properties["heuristics_filter"] = {"$ref": "#/$defs/HeuristicsFilter"}
    # v0.20: same tightening for reviewer_summary. Parallel to v0.12
    # agent_summary — Python-Optional for test helpers, required +
    # non-nullable on the wire so consumers can read
    # ``reviewer_summary.first_recommended_surface`` without a null
    # check on the block itself.
    properties["reviewer_summary"] = {"$ref": "#/$defs/ReviewerSummary"}
    # v0.22 (verifier cycle, P2/M3): same tightening for the four
    # single-block verifier fields. Pydantic emits `anyOf: [Block, null]`
    # for the Optional Python fields; on the wire every emitted report
    # carries a real block (deterministic empty/default in Phase A),
    # never null. The const + non-nullable form lets consumers read e.g.
    # ``verifier_summary.verdict`` without a null check.
    # ``protected_surface_changes`` is a plain list field, so it keeps the
    # Pydantic array schema and only needs the required-key pin above.
    properties["capability_change"] = {"$ref": "#/$defs/CapabilityChangeBlock"}
    properties["effective_policy"] = {"$ref": "#/$defs/EffectivePolicy"}
    properties["human_ack"] = {"$ref": "#/$defs/HumanAck"}
    properties["verifier_summary"] = {"$ref": "#/$defs/VerifierSummary"}

    # Preserve nested v0.5 required lists. Pydantic auto-generation marks
    # only fields without defaults as required, but consumers depend on
    # several optional-with-default fields being present in every report.
    # Optional v0.6 additions (Finding.patches) intentionally stay
    # optional — additive only.
    defs = schema.setdefault("$defs", {})
    if "PrivacyAudit" in defs:
        defs["PrivacyAudit"]["required"] = sorted(
            [
                "enabled",
                "rules_version",
                "sensitive_field_inventory_version",
                "redacted_occurrence_count",
                "redacted_paths",
                "output_surfaces",
                "notes",
            ]
        )
    if "RedactedPathSummary" in defs:
        defs["RedactedPathSummary"]["required"] = sorted(
            ["path", "count", "kinds"]
        )
    # v0.21: pin every HeuristicsFilter field as required. Pydantic
    # auto-required is empty here because every field has a default
    # (enabled=False, lists/dicts default-factory). On the wire every
    # emitted scan carries all four fields — `apply_no_heuristics_filter`
    # always populates them — so consumers can rely on the full shape
    # without conditional key checks.
    if "HeuristicsFilter" in defs:
        defs["HeuristicsFilter"]["required"] = sorted(
            [
                "enabled",
                "excluded_provenance_kinds",
                "filtered_finding_count",
                "filtered_by_kind",
            ]
        )
    if "Finding" in defs:
        defs["Finding"]["required"] = sorted(
            [
                "id",
                "fingerprint",
                "check_id",
                "title",
                "severity",
                "category",
                "evidence",
                "confidence",
                "recommendation",
                "blocks_release",
                "suppressed",
                "baseline_status",
                "capability_refs",
                # v0.12: deterministic projection field. Optional in
                # Python (so test helpers can construct minimal Findings)
                # but required + non-nullable on the wire — every
                # emitted report runs annotate_remediation which sets it.
                "agent_action",
                # v0.15: per-finding rule provenance. Optional in
                # Python (None default for legacy v0.12-v0.14 reports
                # loaded via explain-finding and minimal test
                # constructions) but required + non-nullable on the
                # wire — emitted reports always carry a real value
                # via tool_finding/agent_finding's required kwarg.
                "provenance_kind",
            ]
        )
        # v0.12: tighten agent_action to the inline enum shape (no
        # null). Pydantic emits `anyOf: [{enum, type: string}, null]`
        # for the Optional model field; on the wire we promise the
        # field is always a real enum value. AgentAction is a Literal,
        # not a BaseModel, so we cannot $ref it — inline the enum.
        finding_props = defs["Finding"].setdefault("properties", {})

        if "agent_action" in finding_props:
            finding_props["agent_action"] = {
                "type": "string",
                "enum": list(get_args(AgentAction)),
            }
        # v0.15: same tightening for provenance_kind — Python-Optional,
        # wire-required, inline enum (Literal can't be $ref'd).
        if "provenance_kind" in finding_props:
            finding_props["provenance_kind"] = {
                "type": "string",
                "enum": list(get_args(ProvenanceKind)),
            }
    # v0.12: tighten the AgentSummary block. Pydantic auto-detects
    # required only for fields without defaults (verdict, headline);
    # but every field below is populated by `build_agent_summary` on
    # every emitted report, so all of them belong in the required
    # list. `first_recommended_action` is required as a key
    # (always present) but nullable (None on `passed` verdict with no
    # auto-apply path).
    if "AgentSummary" in defs:
        defs["AgentSummary"]["required"] = sorted(
            [
                "verdict",
                "headline",
                "blocker_count",
                "review_item_count",
                "auto_appliable_patches",
                "needs_human_review",
                "first_recommended_action",
            ]
        )
    # v0.12: AgentSummaryAction must require `kind`, `command`, and
    # `why` whenever it appears (i.e. when first_recommended_action
    # is non-null). `command` is required as a KEY but nullable as a
    # VALUE — `kind: "info"` actions carry `command: null` while
    # `kind: "command"` actions carry the actual CLI string. Pydantic
    # auto-required only includes `why` because the other two have
    # defaults.
    if "AgentSummaryAction" in defs:
        defs["AgentSummaryAction"]["required"] = sorted(
            [
                "kind",
                "command",
                "why",
            ]
        )
    # v0.20: tighten the ReviewerSummary block. Pydantic auto-required
    # only includes `verdict` and `headline` (the two fields without
    # defaults); every other field is populated by
    # ``build_reviewer_summary`` on every emitted report, so all of
    # them belong in the required list. ``first_recommended_surface``
    # is required as a key (always present) but nullable as a value
    # (``None`` on a fully clean scan).
    if "ReviewerSummary" in defs:
        defs["ReviewerSummary"]["required"] = sorted(
            [
                "verdict",
                "headline",
                "tool_surface_changes",
                "capability_misalignments",
                "action_surface_changes",
                "evidence_matrix_gaps",
                "severity_overrides_applied",
                "severity_overrides_tier_crossed",
                "privacy_redactions",
                "baseline_integrity_issues",
                "first_recommended_surface",
            ]
        )
    # v0.20: ReviewerSurfacePointer must require every field whenever
    # it appears (i.e. when first_recommended_surface is non-null).
    # ``kind`` / ``name`` are Literal-typed enums so the schema will
    # also enforce the closed value set.
    if "ReviewerSurfacePointer" in defs:
        defs["ReviewerSurfacePointer"]["required"] = sorted(
            [
                "kind",
                "name",
                "path",
                "why",
            ]
        )
    if "LoadedPolicyPack" in defs:
        defs["LoadedPolicyPack"]["required"] = sorted(
            ["id", "name", "path", "rule_count"]
        )
    # v0.22 (verifier cycle, P2/M3): pin the verifier-block contracts.
    # Every field of every block is populated on emitted scans (the
    # Phase A default builders set them all), so all fields belong in the
    # required list even though Pydantic auto-required only includes the
    # no-default fields. Nullable-but-required-as-key fields (the scope
    # before/after on a capability member, ack expiry/source, the
    # effective-policy ci_mode) keep the key present with a null value.
    if "CapabilityChangeMember" in defs:
        defs["CapabilityChangeMember"]["required"] = sorted(
            [
                "id",
                "direction",
                "subject_kind",
                "tool",
                "action",
                "scope",
                "before_scope",
                "after_scope",
                "risk_tags",
                "release_impact",
                "provenance_kind",
                "confidence",
                "rationale",
                "related_finding_ids",
            ]
        )
    if "CapabilityChangeBlock" in defs:
        defs["CapabilityChangeBlock"]["required"] = sorted(
            ["enabled", "added", "removed", "broadened", "narrowed"]
        )
    if "ProtectedSurfaceChange" in defs:
        defs["ProtectedSurfaceChange"]["required"] = sorted(
            ["path", "kind", "glob", "related_finding_ids"]
        )
    if "EffectivePolicy" in defs:
        defs["EffectivePolicy"]["required"] = sorted(
            [
                "ci_mode",
                "fail_on",
                "suppressed_check_ids",
                "waiver_scopes",
                "severity_overrides",
                "baseline_integrity_mode",
                "baseline_fingerprints",
                "ci_gate_present",
            ]
        )
    if "HumanAckEntry" in defs:
        defs["HumanAckEntry"]["required"] = sorted(
            ["owner", "reason", "affected_surface", "expires", "source"]
        )
    if "HumanAck" in defs:
        defs["HumanAck"]["required"] = sorted(
            ["required", "satisfied", "acks", "outstanding"]
        )
    if "VerifierCapabilityDeltaSummary" in defs:
        defs["VerifierCapabilityDeltaSummary"]["required"] = sorted(
            ["added", "removed", "broadened", "narrowed"]
        )
    if "VerifierReasonCodeCount" in defs:
        defs["VerifierReasonCodeCount"]["required"] = sorted(
            ["reason_code", "count"]
        )
    if "VerifierSummary" in defs:
        defs["VerifierSummary"]["required"] = sorted(
            [
                "verdict",
                "by_severity",
                "by_reason_code",
                "capability_delta_summary",
                "protected_surface_touched",
                "policy_weakened",
                "human_ack_required",
                "human_ack_satisfied",
                "top_reason_codes",
            ]
        )

    # v0.8 release_decision: pin required keys so consumers can rely on
    # the full block being present (Pydantic only marks fields without
    # defaults as required, but our consumers depend on the whole shape).
    # v0.17 adds contribution_rules — a deterministic per-finding audit
    # of how each finding contributed to the decision. Required + always
    # present (defaults to []) so consumers never need an existence check.
    if "ReleaseDecision" in defs:
        defs["ReleaseDecision"]["required"] = sorted(
            [
                "decision",
                "reason",
                "blockers",
                "review_items",
                "evidence_coverage",
                "baseline_delta",
                "fail_policy",
                "contribution_rules",
            ]
        )
    if "ContributionRule" in defs:
        # v0.17: pin the full audit-row contract. `fingerprint` is
        # nullable but required-as-key (every emitted row carries the
        # field; the value may be null for findings without a computed
        # fingerprint). All other fields are required and non-nullable
        # on the wire — build_release_decision emits one
        # ContributionRule per report finding.
        defs["ContributionRule"]["required"] = sorted(
            [
                "finding_id",
                "fingerprint",
                "check_id",
                "category",
                "rule",
                "rationale",
            ]
        )
    if "ReleaseDecisionItem" in defs:
        # Pin the full v0.8 contract documented in STABILITY.md. `id`,
        # `fingerprint`, and `baseline_status` are nullable in the model
        # but every emitted item carries them — requiring the key to be
        # present (value may be null) lets agent/CI consumers rely on
        # the documented shape without conditional key checks.
        defs["ReleaseDecisionItem"]["required"] = sorted(
            [
                "id",
                "fingerprint",
                "check_id",
                "severity",
                "title",
                "baseline_status",
                "blocks_release",
                "capability_refs",
            ]
        )
    if "EvidenceCoverageDecision" in defs:
        defs["EvidenceCoverageDecision"]["required"] = sorted(
            [
                "level",
                "human_review_recommended",
                "source_warning_count",
                "low_confidence_tool_count",
            ]
        )
    if "BaselineDelta" in defs:
        defs["BaselineDelta"]["required"] = sorted(
            ["enabled", "matched_count", "new_count", "resolved_count"]
        )
    if "FailPolicy" in defs:
        defs["FailPolicy"]["required"] = sorted(
            [
                "ci_mode",
                "fail_on",
                "new_findings_only",
                "would_fail_ci",
                "exit_code",
            ]
        )
    if "CapabilityFact" in defs:
        defs["CapabilityFact"]["required"] = sorted(
            [
                "id",
                "tool_name",
                "source_type",
                "source_ref",
                "capability",
                "risk_tags",
                "auth_scopes",
                "owner",
                "included_reason",
                "control_status",
                "related_findings",
            ]
        )
    if "DeclaredIntention" in defs:
        defs["DeclaredIntention"]["required"] = sorted(
            ["id", "kind", "text", "source", "intent_tags"]
        )
    if "Misalignment" in defs:
        defs["Misalignment"]["required"] = sorted(
            [
                "id",
                "kind",
                "severity",
                "tool_name",
                "capability_refs",
                "intention_refs",
                "finding_refs",
                "policy_requirement",
                "gap",
                "release_implication",
            ]
        )
    if "ReleaseConsequence" in defs:
        defs["ReleaseConsequence"]["required"] = sorted(
            [
                "decision",
                "summary",
                "blocker_misalignment_count",
                "review_misalignment_count",
                "fail_policy",
            ]
        )
    if "SuggestedScenario" in defs:
        defs["SuggestedScenario"]["required"] = sorted(
            [
                "id",
                "scenario_type",
                "title",
                "given",
                "expected_control",
                "source_misalignments",
                "source_findings",
            ]
        )
    if "ToolSurfaceHashes" in defs:
        defs["ToolSurfaceHashes"]["required"] = sorted(
            [
                "source_ref",
                "description",
                "input_schema",
                "output_schema",
                "parameters",
                "annotations",
            ]
        )
    if "ToolSurfaceToolFact" in defs:
        defs["ToolSurfaceToolFact"]["required"] = sorted(
            [
                "name",
                "source_type",
                "source_id",
                "source_ref",
                "risk_tags",
                "auth_scopes",
                "owner",
                "extraction_confidence",
                "has_description",
                "hashes",
            ]
        )
    if "ToolSurfaceScopeFact" in defs:
        defs["ToolSurfaceScopeFact"]["required"] = sorted(
            ["scope", "kind", "tool_names", "broad"]
        )
    if "ToolSurfaceControlFact" in defs:
        defs["ToolSurfaceControlFact"]["required"] = sorted(
            ["kind", "tool", "source", "reason"]
        )
    if "ToolSurfacePolicyFact" in defs:
        defs["ToolSurfacePolicyFact"]["required"] = sorted(
            ["kind", "key", "value_hash", "summary"]
        )
    if "ToolSurfaceFacts" in defs:
        defs["ToolSurfaceFacts"]["required"] = sorted(
            ["tools", "scopes", "controls", "policies"]
        )
    if "ToolSurfaceDiffBase" in defs:
        defs["ToolSurfaceDiffBase"]["required"] = sorted(
            [
                "kind",
                "path",
                "report_schema_version",
                "baseline_schema_version",
            ]
        )
    if "ToolSurfaceDiffSummary" in defs:
        defs["ToolSurfaceDiffSummary"]["required"] = sorted(
            [
                "tools_added",
                "tools_removed",
                "tools_changed",
                "new_scopes",
                "removed_scopes",
                "new_high_risk_effects",
                "removed_high_risk_effects",
                "controls_added",
                "controls_removed",
                "metadata_changes",
                "policy_drift_items",
                "new_findings",
                "resolved_findings",
                "unchanged_findings",
                "accepted_debt",
            ]
        )
    if "ToolSurfaceFieldChange" in defs:
        defs["ToolSurfaceFieldChange"]["required"] = sorted(
            ["field", "before", "after"]
        )
    if "ToolSurfaceToolChange" in defs:
        defs["ToolSurfaceToolChange"]["required"] = sorted(
            ["kind", "name", "source_type", "source_id", "changes"]
        )
    if "ToolSurfaceHighRiskEffectChange" in defs:
        defs["ToolSurfaceHighRiskEffectChange"]["required"] = sorted(
            ["kind", "tool", "tag"]
        )
    if "ToolSurfaceScopeChange" in defs:
        defs["ToolSurfaceScopeChange"]["required"] = sorted(
            ["kind", "scope", "scope_kind", "tool_names", "broad"]
        )
    if "ToolSurfaceControlChange" in defs:
        defs["ToolSurfaceControlChange"]["required"] = sorted(
            ["kind", "control", "tool", "source", "reason"]
        )
    if "ToolSurfaceMetadataChange" in defs:
        defs["ToolSurfaceMetadataChange"]["required"] = sorted(
            ["kind", "tool", "metadata", "before", "after"]
        )
    if "ToolSurfacePolicyDrift" in defs:
        defs["ToolSurfacePolicyDrift"]["required"] = sorted(
            [
                "kind",
                "policy_kind",
                "key",
                "before_hash",
                "after_hash",
                "before_summary",
                "after_summary",
            ]
        )
    if "ToolSurfaceFindingDeltaItem" in defs:
        defs["ToolSurfaceFindingDeltaItem"]["required"] = sorted(
            [
                "fingerprint",
                "check_id",
                "severity",
                "title",
                "tool_name",
                "baseline_status",
            ]
        )
    if "ToolSurfaceFindingDeltas" in defs:
        defs["ToolSurfaceFindingDeltas"]["required"] = sorted(
            [
                "new_findings",
                "resolved_findings",
                "unchanged_findings",
                "accepted_debt",
            ]
        )
    if "ToolSurfaceDiff" in defs:
        defs["ToolSurfaceDiff"]["required"] = sorted(
            [
                "enabled",
                "base",
                "summary",
                "tools",
                "high_risk_effects",
                "scopes",
                "controls",
                "metadata_changes",
                "policy_drift",
                "finding_deltas",
                "notes",
            ]
        )
    if "ActionApprovalFact" in defs:
        defs["ActionApprovalFact"]["required"] = sorted(["required", "threshold"])
    if "ActionSafeguardsFact" in defs:
        defs["ActionSafeguardsFact"]["required"] = sorted(
            ["idempotency", "audit_log", "rollback", "dry_run"]
        )
    if "ActionEvidenceFact" in defs:
        defs["ActionEvidenceFact"]["required"] = sorted(
            ["owner", "runbook", "approval_ticket"]
        )
    if "ActionSurfaceHashes" in defs:
        defs["ActionSurfaceHashes"]["required"] = sorted(
            ["identity_hash", "schema_hash", "policy_hash", "risk_hash"]
        )
    if "ActionFact" in defs:
        defs["ActionFact"]["required"] = sorted(
            [
                "action_id",
                "agent_id",
                "tool_id",
                "tool_name",
                "provider",
                "source_type",
                "source_id",
                "operation",
                "effect",
                "risk_tags",
                "required_scopes",
                "approval_policy",
                "safeguards",
                "evidence",
                "input_fields",
                "required_input_fields",
                "input_schema_hash",
                "hashes",
            ]
        )
    if "ActionSurfaceFacts" in defs:
        defs["ActionSurfaceFacts"]["required"] = sorted(
            ["snapshot_version", "actions"]
        )
    if "ActionSurfaceDiffSummary" in defs:
        defs["ActionSurfaceDiffSummary"]["required"] = sorted(
            [
                "actions_added",
                "actions_removed",
                "actions_modified",
                "scope_expansions",
                "effect_escalations",
                "risk_tags_added",
                "approvals_removed",
                "safeguards_removed",
                "input_schema_expansions",
                "blocking_findings",
            ]
        )
    if "ActionSurfaceChange" in defs:
        defs["ActionSurfaceChange"]["required"] = sorted(
            [
                "type",
                "action_id",
                "agent_id",
                "tool_name",
                "operation",
                "severity",
                "reason",
                "before",
                "after",
                "added",
                "removed",
            ]
        )
    if "ActionSurfaceDiff" in defs:
        defs["ActionSurfaceDiff"]["required"] = sorted(
            ["enabled", "base", "summary", "added", "removed", "modified", "notes"]
        )

    # tool_inventory[] and loaded_plugins[] are typed as
    # ``list[dict[str, Any]]`` on the model, so Pydantic emits item
    # schemas without per-item required lists. v0.5 documented these
    # required keys; preserve them.
    if "tool_inventory" in properties and properties["tool_inventory"].get("type") == "array":
        properties["tool_inventory"]["items"] = {
            "type": "object",
            "additionalProperties": True,
            "required": sorted(
                ["name", "source_type", "risk_tags", "auth_scopes", "confidence"]
            ),
        }
    if "loaded_plugins" in properties and properties["loaded_plugins"].get("type") == "array":
        properties["loaded_plugins"]["items"] = {
            "type": "object",
            "additionalProperties": True,
            # v0.17 (M5): plugin validation provenance is required on
            # every emitted loaded_plugins entry. ``validation_status``
            # is one of ``valid | load_failed | bad_signature |
            # bad_metadata | id_collision | bad_floor`` and the two
            # error lists are always present (empty for clean plugins).
            # The v0.7 frozen schema preserves the original 5-field
            # required list — those frozen-schema tests pin the
            # pre-M5 shape; this required list is the current contract.
            "required": sorted(
                [
                    "name",
                    "value",
                    "distribution",
                    "version",
                    "check_id",
                    "validation_status",
                    "validation_errors",
                    "runtime_errors",
                ]
            ),
        }
    # v0.20: adapter validation provenance — parallel shape to
    # loaded_plugins[] but the ID key is ``source_type`` (the dispatcher
    # key) rather than ``check_id``. ``validation_status`` is one of
    # ``valid | load_failed | bad_protocol | bad_scope |
    # source_type_collision``; the two error lists are always present
    # (empty for clean adapters).
    if "loaded_adapters" in properties and properties["loaded_adapters"].get("type") == "array":
        properties["loaded_adapters"]["items"] = {
            "type": "object",
            "additionalProperties": True,
            "required": sorted(
                [
                    "name",
                    "value",
                    "distribution",
                    "version",
                    "source_type",
                    "validation_status",
                    "validation_errors",
                    "runtime_errors",
                ]
            ),
        }

    # frameworks.{google_adk,langchain,crewai} surface counts. These are
    # also list[dict[str, Any]]-shaped at the model level; v0.5 enumerated
    # the per-framework count keys that consumers check.
    frameworks_property = properties.setdefault(
        "frameworks", {"type": "object", "additionalProperties": True}
    )
    frameworks_property.setdefault("type", "object")
    frameworks_property["additionalProperties"] = True
    frameworks_sub = frameworks_property.setdefault("properties", {})
    frameworks_sub["google_adk"] = {
        "type": "object",
        "additionalProperties": True,
        "required": sorted(
            [
                "python_entrypoint_count",
                "agent_config_count",
                "agent_count",
                "function_tool_count",
                "long_running_tool_count",
                "toolset_count",
                "dynamic_toolset_count",
                "callback_count",
                "plugin_count",
                "sub_agent_count",
                "eval_file_count",
                "trace_sample_count",
                "tool_inventory_file_count",
                "warnings",
            ]
        ),
    }
    frameworks_sub["langchain"] = {
        "type": "object",
        "additionalProperties": True,
        "required": sorted(
            [
                "python_entrypoint_count",
                "function_tool_count",
                "structured_tool_count",
                "tool_node_count",
                "agent_tool_binding_count",
                "dynamic_tool_surface_count",
                "tool_inventory_file_count",
                "warnings",
            ]
        ),
    }
    frameworks_sub["crewai"] = {
        "type": "object",
        "additionalProperties": True,
        "required": sorted(
            [
                "python_entrypoint_count",
                "agent_count",
                "crew_count",
                "function_tool_count",
                "class_tool_count",
                "prebuilt_tool_count",
                "dynamic_tool_surface_count",
                "tool_inventory_file_count",
                "warnings",
            ]
        ),
    }

    target = DOCS / f"report-schema.v{minor}.json"
    return target, _canonical_json(schema)


def write_report_schema(*, check_only: bool = False, drift: list[str] | None = None) -> bool:
    target, content = build_report_schema()
    return _emit(target, content, check_only=check_only, drift=drift if drift is not None else [])


def build_policy_pack_schema() -> tuple[Path, str]:
    """Generate docs/policy-pack-schema.v0.1.json from PolicyPackArtifactV1."""

    from agents_shipgate.schemas.policy_pack import (
        POLICY_PACK_SCHEMA_VERSION,
        PolicyPackArtifactV1,
    )

    schema = PolicyPackArtifactV1.model_json_schema()
    minor = POLICY_PACK_SCHEMA_VERSION
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/policy-pack-schema.v{minor}.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = f"Agents Shipgate Policy Pack v{minor}"
    schema["description"] = (
        "JSON Schema for local Agents Shipgate policy-pack YAML files. "
        "Generated from agents_shipgate.schemas.policy_pack.PolicyPackArtifactV1. "
        "Do not edit by hand."
    )
    target = DOCS / f"policy-pack-schema.v{minor}.json"
    return target, _canonical_json(schema)


def write_policy_pack_schema(
    *, check_only: bool = False, drift: list[str] | None = None
) -> bool:
    target, content = build_policy_pack_schema()
    return _emit(
        target,
        content,
        check_only=check_only,
        drift=drift if drift is not None else [],
    )


def build_checks_catalog() -> tuple[Path, str]:
    from agents_shipgate.checks.registry import check_catalog

    # Plugins are explicitly disabled here. The committed docs/checks.json
    # is the built-in catalog only — if a developer has
    # ``AGENTS_SHIPGATE_ENABLE_PLUGINS=1`` set in their shell and any
    # third-party check plugin installed, the default ``check_catalog()``
    # would augment the result with that plugin's metadata, and
    # ``--check`` would then either falsely flag drift or, worse,
    # silently overwrite the committed catalog with a plugin-augmented
    # one. Force plugins off so the artifact is deterministic regardless
    # of the host environment.
    payload = {
        "$id": (
            "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
            "main/docs/checks.json"
        ),
        "title": "Agents Shipgate Check Catalog",
        "description": (
            "Machine-readable catalog of built-in checks. Generated from "
            "agents_shipgate.checks.registry.check_catalog(). Do not edit by hand."
        ),
        "checks": [
            check.model_dump(mode="json")
            for check in check_catalog(plugins_enabled=False)
        ],
    }
    target = DOCS / "checks.json"
    return target, _canonical_json(payload)


def write_checks_catalog(*, check_only: bool = False, drift: list[str] | None = None) -> bool:
    target, content = build_checks_catalog()
    return _emit(target, content, check_only=check_only, drift=drift if drift is not None else [])


def build_packet_schema() -> tuple[Path, str]:
    """Generate docs/packet-schema.v0.<minor>.json from EvidencePacket.

    Versioned independently from the report schema; bumping requires a
    single change to ``EvidencePacket.packet_schema_version`` and a
    rerun of this script. CI's clean-tree assertion catches drift.
    """

    from agents_shipgate.schemas.packet import EvidencePacket

    schema = EvidencePacket.model_json_schema()
    minor = str(EvidencePacket.model_fields["packet_schema_version"].default)
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/packet-schema.v{minor}.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = f"Agents Shipgate Release Evidence Packet v{minor}"
    schema["description"] = (
        "JSON Schema for packet.json. Generated from "
        "agents_shipgate.schemas.packet.EvidencePacket. Do not edit by hand."
    )
    # EvidencePacket intentionally reuses report ReleaseDecisionItem models for
    # in-memory building, but packet v0.6 serialization strips v0.24's
    # report-only capability_refs field. Keep the generated packet schema
    # aligned with the packet wire contract unless packet_schema_version bumps.
    release_item = schema.get("$defs", {}).get("ReleaseDecisionItem")
    if isinstance(release_item, dict):
        properties = release_item.get("properties")
        if isinstance(properties, dict):
            properties.pop("capability_refs", None)
        required = release_item.get("required")
        if isinstance(required, list):
            release_item["required"] = [
                item for item in required if item != "capability_refs"
            ]
    target = DOCS / f"packet-schema.v{minor}.json"
    return target, _canonical_json(schema)


def write_packet_schema(*, check_only: bool = False, drift: list[str] | None = None) -> bool:
    target, content = build_packet_schema()
    return _emit(target, content, check_only=check_only, drift=drift if drift is not None else [])


def build_verifier_schema() -> tuple[Path, str]:
    """Generate docs/verifier-schema.v0.1.json from VerifierArtifact."""

    from agents_shipgate.schemas.verifier import VerifierArtifact

    schema = VerifierArtifact.model_json_schema()
    minor = str(VerifierArtifact.model_fields["verifier_schema_version"].default)
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/verifier-schema.v{minor}.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = f"Agents Shipgate Verifier Artifact v{minor}"
    schema["description"] = (
        "JSON Schema for verifier.json. Generated from "
        "agents_shipgate.schemas.verifier.VerifierArtifact. Do not edit by hand."
    )
    target = DOCS / f"verifier-schema.v{minor}.json"
    return target, _canonical_json(schema)


def write_verifier_schema(*, check_only: bool = False, drift: list[str] | None = None) -> bool:
    target, content = build_verifier_schema()
    return _emit(target, content, check_only=check_only, drift=drift if drift is not None else [])


def build_agent_result_schema() -> tuple[Path, str]:
    """Generate the legacy local-agent protocol schema."""

    from agents_shipgate.schemas.agent_result_v1 import AgentResultV1

    schema = AgentResultV1.model_json_schema()
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        "main/docs/agent-result-schema.v1.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Agents Shipgate Legacy Agent Result v1"
    schema["description"] = (
        "Legacy JSON Schema retained for existing local-agent protocol and "
        "MCP consumers. It is not emitted by agents-shipgate verify and is "
        "not the Codex boundary result contract. Generated from "
        "agents_shipgate.schemas.agent_result_v1.AgentResultV1. Do not edit by hand."
    )
    target = DOCS / "agent-result-schema.v1.json"
    return target, _canonical_json(schema)


def build_codex_boundary_result_schema() -> tuple[Path, str]:
    """Generate docs/codex-boundary-result-schema.v1.json."""

    from agents_shipgate.schemas.codex_boundary_result import CodexBoundaryResultV1

    schema = CodexBoundaryResultV1.model_json_schema()
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        "main/docs/codex-boundary-result-schema.v1.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Agents Shipgate Codex Boundary Result v1"
    schema["description"] = (
        "JSON Schema for shipgate check --format codex-boundary-json. "
        "Generated from "
        "agents_shipgate.schemas.codex_boundary_result.CodexBoundaryResultV1. "
        "Do not edit by hand."
    )
    target = DOCS / "codex-boundary-result-schema.v1.json"
    return target, _canonical_json(schema)


def build_verify_run_schema() -> tuple[Path, str]:
    """Generate docs/verify-run-schema.v1.json."""

    from agents_shipgate.schemas.verify_run import VerifyRunArtifact

    schema = VerifyRunArtifact.model_json_schema()
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        "main/docs/verify-run-schema.v1.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Agents Shipgate Verify Run v1"
    schema["description"] = (
        "JSON Schema for agents-shipgate-reports/verify-run.json. Generated "
        "from agents_shipgate.schemas.verify_run.VerifyRunArtifact. Do not "
        "edit by hand."
    )
    target = DOCS / "verify-run-schema.v1.json"
    return target, _canonical_json(schema)


def build_agent_handoff_schema() -> tuple[Path, str]:
    """Generate docs/agent-handoff-schema.v1.json."""

    from agents_shipgate.schemas.agent_handoff import AgentHandoffArtifact

    schema = AgentHandoffArtifact.model_json_schema()
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        "main/docs/agent-handoff-schema.v1.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Agents Shipgate Agent Handoff v1"
    schema["description"] = (
        "JSON Schema for agents-shipgate-reports/agent-handoff.json. "
        "Generated from "
        "agents_shipgate.schemas.agent_handoff.AgentHandoffArtifact. It is "
        "a compact projection for coding agents and does not gate releases; "
        "release_decision.decision remains the only gate."
    )
    target = DOCS / "agent-handoff-schema.v1.json"
    return target, _canonical_json(schema)


def build_preflight_schema() -> tuple[Path, str]:
    """Generate docs/preflight-schema.v0.2.json from PreflightResultV2."""

    from agents_shipgate.schemas.preflight import (
        PREFLIGHT_SCHEMA_VERSION,
        PreflightResultV2,
    )

    schema = PreflightResultV2.model_json_schema()
    minor = PREFLIGHT_SCHEMA_VERSION
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/preflight-schema.v{minor}.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = f"Agents Shipgate Preflight Result v{minor}"
    schema["description"] = (
        "JSON Schema for shipgate preflight --json. Generated from "
        "agents_shipgate.schemas.preflight.PreflightResultV2. It is a "
        "proactive routing/projection surface, not a release gate; "
        "release_decision.decision remains the only gate."
    )
    target = DOCS / f"preflight-schema.v{minor}.json"
    return target, _canonical_json(schema)


def build_capability_lock_schema() -> tuple[Path, str]:
    """Generate the stable capability-lock schema."""

    from agents_shipgate.schemas.capabilities import (
        CAPABILITY_LOCK_SCHEMA_VERSION,
        CapabilityLockFileArtifactV1,
    )

    schema = CapabilityLockFileArtifactV1.model_json_schema()
    minor = CAPABILITY_LOCK_SCHEMA_VERSION
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/capability-lock-schema.v{minor}.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = f"Agents Shipgate Capability Lock v{minor}"
    schema["description"] = (
        "Stable JSON Schema for static capability lock artifacts. Generated "
        "from agents_shipgate.schemas.capabilities. It is non-gating and is "
        "not part of report.json; release_decision.decision remains the only "
        "gate."
    )
    target = DOCS / f"capability-lock-schema.v{minor}.json"
    return target, _canonical_json(schema)


def build_capability_lock_diff_schema() -> tuple[Path, str]:
    """Generate the stable capability-lock diff schema."""

    from agents_shipgate.schemas.capabilities import (
        CAPABILITY_LOCK_DIFF_SCHEMA_VERSION,
        CapabilityLockDiffArtifactV1,
    )

    schema = CapabilityLockDiffArtifactV1.model_json_schema()
    minor = CAPABILITY_LOCK_DIFF_SCHEMA_VERSION
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/capability-lock-diff-schema.v{minor}.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = f"Agents Shipgate Capability Lock Diff v{minor}"
    schema["description"] = (
        "Stable JSON Schema for semantic capability lock diff artifacts. "
        "Generated from agents_shipgate.schemas.capabilities. It is "
        "non-gating and is not part of report.json; "
        "release_decision.decision remains the only gate."
    )
    target = DOCS / f"capability-lock-diff-schema.v{minor}.json"
    return target, _canonical_json(schema)


def write_capability_lock_schema(
    *, check_only: bool = False, drift: list[str] | None = None
) -> bool:
    target, content = build_capability_lock_schema()
    return _emit(
        target,
        content,
        check_only=check_only,
        drift=drift if drift is not None else [],
    )


def write_capability_lock_diff_schema(
    *, check_only: bool = False, drift: list[str] | None = None
) -> bool:
    target, content = build_capability_lock_diff_schema()
    return _emit(
        target,
        content,
        check_only=check_only,
        drift=drift if drift is not None else [],
    )


def build_capability_intent_schema() -> tuple[Path, str]:
    """Generate the experimental human-owned capability-intent schema."""

    from agents_shipgate.schemas.capability_reconciliation import (
        CAPABILITY_INTENT_SCHEMA_VERSION,
        CapabilityIntentPolicyArtifactV1,
    )

    minor = CAPABILITY_INTENT_SCHEMA_VERSION
    schema = CapabilityIntentPolicyArtifactV1.model_json_schema()
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/capability-intent-schema.v{minor}.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = f"Agents Shipgate Experimental Capability Intent v{minor}"
    schema["description"] = (
        "Experimental JSON Schema for a reviewed, human-owned capability-intent "
        "sidecar. It is separate from untrusted observations and is not a release gate."
    )
    target = DOCS / f"capability-intent-schema.v{minor}.json"
    return target, _canonical_json(schema)


def build_capability_observation_schema() -> tuple[Path, str]:
    """Generate the experimental vendor-neutral observation schema."""

    from agents_shipgate.schemas.capability_reconciliation import (
        CAPABILITY_OBSERVATION_SCHEMA_VERSION,
        CapabilityObservationArtifactV1,
    )

    minor = CAPABILITY_OBSERVATION_SCHEMA_VERSION
    schema = CapabilityObservationArtifactV1.model_json_schema()
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/capability-observation-schema.v{minor}.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = f"Agents Shipgate Experimental Capability Observations v{minor}"
    schema["description"] = (
        "Experimental vendor-neutral JSON Schema for untrusted local sandbox grants "
        "and sampled eval observations. It performs no live collection and does not gate."
    )
    target = DOCS / f"capability-observation-schema.v{minor}.json"
    return target, _canonical_json(schema)


def build_capability_reconciliation_schema() -> tuple[Path, str]:
    """Generate the experimental non-gating reconciliation-result schema."""

    from agents_shipgate.schemas.capability_reconciliation import (
        CAPABILITY_RECONCILIATION_SCHEMA_VERSION,
        CapabilityReconciliationArtifactV1,
    )

    minor = CAPABILITY_RECONCILIATION_SCHEMA_VERSION
    schema = CapabilityReconciliationArtifactV1.model_json_schema()
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/capability-reconciliation-schema.v{minor}.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = f"Agents Shipgate Experimental Capability Reconciliation v{minor}"
    schema["description"] = (
        "Experimental JSON Schema for deterministic four-set capability reconciliation. "
        "Candidate changes are human-only and release_decision.decision remains the sole gate."
    )
    target = DOCS / f"capability-reconciliation-schema.v{minor}.json"
    return target, _canonical_json(schema)


def build_governance_benchmark_catalog_schema() -> tuple[Path, str]:
    """Generate the stable governance-benchmark catalog schema."""

    from agents_shipgate.schemas.governance_benchmark import (
        GOVERNANCE_BENCHMARK_CATALOG_SCHEMA_VERSION,
        GovernanceBenchmarkCatalogArtifactV1,
    )

    schema = GovernanceBenchmarkCatalogArtifactV1.model_json_schema()
    minor = GOVERNANCE_BENCHMARK_CATALOG_SCHEMA_VERSION
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/governance-benchmark-catalog-schema.v{minor}.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = f"Agents Shipgate Governance Benchmark Catalog v{minor}"
    schema["description"] = (
        "Stable JSON Schema for the AgentPR governance benchmark catalog. "
        "Generated from agents_shipgate.schemas.governance_benchmark. "
        "It is an eval substrate and does not gate releases."
    )
    target = DOCS / f"governance-benchmark-catalog-schema.v{minor}.json"
    return target, _canonical_json(schema)


def build_governance_benchmark_result_schema() -> tuple[Path, str]:
    """Generate the stable governance-benchmark result schema."""

    from agents_shipgate.schemas.governance_benchmark import (
        GOVERNANCE_BENCHMARK_RESULT_SCHEMA_VERSION,
        GovernanceBenchmarkResultArtifactV1,
    )

    schema = GovernanceBenchmarkResultArtifactV1.model_json_schema()
    minor = GOVERNANCE_BENCHMARK_RESULT_SCHEMA_VERSION
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/governance-benchmark-result-schema.v{minor}.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = f"Agents Shipgate Governance Benchmark Result v{minor}"
    schema["description"] = (
        "Stable JSON Schema for governance benchmark result artifacts. "
        "Generated from agents_shipgate.schemas.governance_benchmark. "
        "It is an eval substrate and does not gate releases."
    )
    target = DOCS / f"governance-benchmark-result-schema.v{minor}.json"
    return target, _canonical_json(schema)


def build_attestation_schema() -> tuple[Path, str]:
    """Generate the release attestation schema."""

    from agents_shipgate.schemas.attestation import (
        ATTESTATION_SCHEMA_VERSION,
        ReleaseAttestationArtifactV1,
    )

    schema = ReleaseAttestationArtifactV1.model_json_schema()
    minor = ATTESTATION_SCHEMA_VERSION
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/attestation-schema.v{minor}.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = f"Agents Shipgate Release Attestation v{minor}"
    schema["description"] = (
        "JSON Schema for deterministic, local release attestations emitted by "
        "agents-shipgate attest. It binds verifier/report artifacts plus "
        "static capability lock/diff hashes when available. It does not gate; "
        "release_decision.decision remains the only gate."
    )
    target = DOCS / f"attestation-schema.v{minor}.json"
    return target, _canonical_json(schema)


def build_org_governance_schema() -> tuple[Path, str]:
    """Generate the organization governance status schema."""

    from agents_shipgate.schemas.org_governance import (
        ORG_GOVERNANCE_SCHEMA_VERSION,
        OrgGovernanceStatusV1,
    )

    schema = OrgGovernanceStatusV1.model_json_schema()
    minor = ORG_GOVERNANCE_SCHEMA_VERSION
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/org-governance-schema.v{minor}.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = f"Agents Shipgate Organization Governance Status v{minor}"
    schema["description"] = (
        "JSON Schema for agents-shipgate org status --json. This is an "
        "organization governance projection over local artifacts, not a "
        "release verdict; release_decision.decision remains the only gate."
    )
    target = DOCS / f"org-governance-schema.v{minor}.json"
    return target, _canonical_json(schema)


def build_registry_schema() -> tuple[Path, str]:
    """Generate the local attestation registry schema."""

    from agents_shipgate.schemas.registry import (
        REGISTRY_SCHEMA_VERSION,
        RegistryQueryResultV1,
    )

    schema = RegistryQueryResultV1.model_json_schema()
    minor = REGISTRY_SCHEMA_VERSION
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/registry-schema.v{minor}.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = f"Agents Shipgate Local Attestation Registry v{minor}"
    schema["description"] = (
        "JSON Schema for agents-shipgate registry query --json. Rows are "
        "local, append-only projections of deterministic attestations. The "
        "registry does not produce release verdicts."
    )
    target = DOCS / f"registry-schema.v{minor}.json"
    return target, _canonical_json(schema)


def build_org_evidence_bundle_schema() -> tuple[Path, str]:
    """Generate the organization evidence bundle schema."""

    from agents_shipgate.schemas.org_evidence_bundle import (
        ORG_EVIDENCE_BUNDLE_SCHEMA_VERSION,
        OrgEvidenceBundleArtifactV1,
    )

    schema = OrgEvidenceBundleArtifactV1.model_json_schema()
    version = ORG_EVIDENCE_BUNDLE_SCHEMA_VERSION.rsplit("/", maxsplit=1)[-1]
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/org-evidence-bundle-schema.{version}.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Agents Shipgate Organization Evidence Bundle v1"
    schema["description"] = (
        "JSON Schema for agents-shipgate org bundle --json. This bundle "
        "aggregates local deterministic artifacts for fleet reporting and "
        "does not produce a release verdict."
    )
    target = DOCS / f"org-evidence-bundle-schema.{version}.json"
    return target, _canonical_json(schema)


def build_host_grants_inventory_schema() -> tuple[Path, str]:
    """Generate the host-grants inventory schema."""

    from agents_shipgate.schemas.host_grants import (
        HOST_GRANTS_INVENTORY_SCHEMA_VERSION,
        HostGrantsInventoryArtifactV1,
    )

    schema = HostGrantsInventoryArtifactV1.model_json_schema()
    minor = HOST_GRANTS_INVENTORY_SCHEMA_VERSION
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/host-grants-inventory-schema.v{minor}.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = f"Agents Shipgate Host Grants Inventory v{minor}"
    schema["description"] = (
        "JSON Schema for shipgate audit --host --json. The inventory "
        "summarizes local coding-agent host grants and does not gate releases."
    )
    target = DOCS / f"host-grants-inventory-schema.v{minor}.json"
    return target, _canonical_json(schema)


# Public ordered list of (name, builder) pairs. Tests and the CLI iterate this
# instead of hardcoding individual calls, so adding a new schema is one edit.
BUILDERS: tuple[tuple[str, Callable[[], tuple[Path, str]]], ...] = (
    ("manifest", build_manifest_schema),
    ("checks_catalog", build_checks_catalog),
    ("report", build_report_schema),
    ("policy_pack", build_policy_pack_schema),
    ("packet", build_packet_schema),
    ("verifier", build_verifier_schema),
    ("verify_run", build_verify_run_schema),
    ("agent_handoff", build_agent_handoff_schema),
    ("agent_result", build_agent_result_schema),
    ("codex_boundary_result", build_codex_boundary_result_schema),
    ("preflight", build_preflight_schema),
    ("capability_lock", build_capability_lock_schema),
    ("capability_lock_diff", build_capability_lock_diff_schema),
    ("capability_intent", build_capability_intent_schema),
    ("capability_observation", build_capability_observation_schema),
    ("capability_reconciliation", build_capability_reconciliation_schema),
    ("attestation", build_attestation_schema),
    ("org_governance", build_org_governance_schema),
    ("org_evidence_bundle", build_org_evidence_bundle_schema),
    ("registry", build_registry_schema),
    ("host_grants_inventory", build_host_grants_inventory_schema),
    ("governance_benchmark_catalog", build_governance_benchmark_catalog_schema),
    ("governance_benchmark_result", build_governance_benchmark_result_schema),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="generate_schemas",
        description=(
            "Regenerate docs/*.json schemas (default) or verify they match the "
            "current Pydantic models (--check)."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify committed schemas match the generators; exit 1 on drift.",
    )
    args = parser.parse_args(argv)

    DOCS.mkdir(parents=True, exist_ok=True)
    drift: list[str] = []
    for _name, builder in BUILDERS:
        target, content = builder()
        _emit(target, content, check_only=args.check, drift=drift)

    if args.check and drift:
        sys.stderr.write("\n".join(drift))
        sys.stderr.write(
            "\n\nSchema drift detected in "
            f"{len(drift)} file(s). To resolve:\n"
            "  python scripts/generate_schemas.py\n"
            "  git add docs/ && git commit\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
