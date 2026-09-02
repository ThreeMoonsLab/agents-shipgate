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
- docs/verifier-schema.v0.<minor>.json
                                (from agents_shipgate.schemas.verifier.VerifierArtifact)
- docs/verify-run-schema.v5.json
                                (from agents_shipgate.schemas.verify_run.
                                 VerifyRunArtifact)
- docs/verification-plan-schema.v1.json
- docs/verification-unit-result-schema.v1.json
- docs/verification-artifact-manifest-schema.v1.json
- docs/verification-receipt-schema.v1.json
- docs/current-control-schema.v1.json
- docs/agent-control-schema.v1.json
                                (from agents_shipgate.schemas.verification_identity)
- docs/human-authorization-schema.v1.json
                                (authorization request, signed grant,
                                 evaluation, and trust policy union)
- docs/agent-handoff-schema.v8.json
                                (from agents_shipgate.schemas.agent_handoff.
                                 AgentHandoffArtifact)
- docs/agent-result-schema.v2.json
                                (from agents_shipgate.schemas.agent_result.AgentResultV2)
- docs/agent-boundary-result-schema.v1.json
                                (from agents_shipgate.schemas.agent_boundary.
                                 AgentBoundaryResultV1)
- docs/preflight-schema.v0.3.json
                                (from agents_shipgate.schemas.preflight.
                                 PreflightResultV4)
- docs/org-governance-schema.v0.1.json
                                (from agents_shipgate.schemas.org_governance.
                                 OrgGovernanceStatusV1)
- docs/org-evidence-bundle-schema.v2.json
                                (from agents_shipgate.schemas.org_evidence_bundle.
                                 OrgEvidenceBundleArtifactV1)
- docs/registry-schema.v0.4.json
                                (from agents_shipgate.schemas.registry.
                                 RegistryQueryResultV1)
- docs/host-grants-inventory-schema.v0.2.json
                                (from agents_shipgate.schemas.host_grants.
                                 HostGrantsInventoryArtifactV2)
- docs/host-grants-baseline-schema.v0.2.json
                                (from HostGrantsBaselineArtifactV2)
- docs/host-grants-drift-schema.v0.2.json
                                (from HostGrantsDriftArtifactV2)
- docs/capability-lock-schema.v0.8.json
                                (from agents_shipgate.schemas.capabilities.
                                 CapabilityLockFileArtifactV1)
- docs/capability-lock-diff-schema.v0.9.json
                                (from agents_shipgate.schemas.capabilities.
                                 CapabilityLockDiffArtifactV1)
- docs/capability-payload-schema.v1.json
                                (from agents_shipgate.schemas.capability_payload.
                                 CapabilityPayloadV1)
- docs/examples/capability-payload.v1.state.example.json
- docs/examples/capability-payload.v1.delta.example.json
                                (projected from the shipped
                                 samples/ai_generated_refund_pr fixture, so the
                                 worked examples cannot drift from the schema)
- docs/capability-delta-attestation-schema.v1.json
                                (from agents_shipgate.schemas.capability_attestation.
                                 CapabilityDeltaAttestationV1)
- docs/examples/capability-delta-attestation.v1.example.json
                                (the same fixture, wrapped as an in-toto
                                 statement; needs a `git` binary for the
                                 subject's tree ids)
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
from typing import Any, get_args

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"
# Filenames follow the schema identifiers, so a version bump cannot leave a
# generated file sitting under the previous version's name.
_AGENT_RESULT_SUFFIX = "v3"
_BOUNDARY_SUFFIX = "v2"
_VERIFY_RUN_SUFFIX = "v5"
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
    _postprocess_declaration_review(schema)
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
            # v0.35: the exclusion ledger. Non-Optional in Python (an absent
            # ledger and an empty one must not be confusable), and required on
            # the wire for the same reason — a consumer asking "what did this
            # run decline to look at?" must never read the answer as "the
            # field is missing, so probably nothing".
            "surface_exclusions",
            # v0.43: the privacy-safe manifest declaration projection is a
            # first-class input to base-vs-head declaration review. Omitting
            # it is not equivalent to an empty manifest, so current report
            # bytes must always carry the explicit snapshot envelope.
            "action_declaration_facts",
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
        defs["RedactedPathSummary"]["required"] = sorted(["path", "count", "kinds"])
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
        defs["LoadedPolicyPack"]["required"] = sorted(["id", "name", "path", "rule_count"])
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
                "control_pack",
            ]
        )
    if "HumanAckEntry" in defs:
        defs["HumanAckEntry"]["required"] = sorted(
            ["owner", "reason", "affected_surface", "expires", "source"]
        )
    if "HumanAck" in defs:
        defs["HumanAck"]["required"] = sorted(["required", "satisfied", "acks", "outstanding"])
        # v0.35. Required on the wire for the same reason the top-level field
        # is: an absent count and a zero count must not be confusable. Without
        # these a payload could ship `surface_exclusions: {}` — schema-valid,
        # and erasing exactly the evidence this block exists to carry
        # (PR #404 review). `total`/`gated` are counts, so they are bounded
        # below at zero too; nothing here is expressible as "gated <= total" in
        # JSON Schema, which `validate_semantic_consistency` checks instead.
        defs["SurfaceExclusionLedger"]["required"] = sorted(
            ["entries", "total", "gated", "gap_backed", "truncated"]
        )
        for count_field in ("total", "gated", "gap_backed"):
            defs["SurfaceExclusionLedger"]["properties"][count_field]["minimum"] = 0
        defs["SurfaceExclusion"]["required"] = sorted(
            [
                "stage",
                "subject",
                "reason",
                "source_ref",
                "detail",
                "accounting",
                "accounted_by",
            ]
        )
        defs["BindingSurfaceDiff"]["required"] = sorted(
            [
                "enabled",
                "base_comparison_requested",
                # Emitted on every diff block, nullable in value only. Left out
                # of this list it could be deleted from an otherwise valid
                # report, unlike every other field here (PR #404 review 2).
                "base_report_schema_version",
                "added_reachable_tool_ids",
                "removed_reachable_tool_ids",
                "added_unbound_tool_ids",
                "added_handoffs",
                "removed_handoffs",
                "notes",
            ]
        )
    if "VerifierCapabilityDeltaSummary" in defs:
        defs["VerifierCapabilityDeltaSummary"]["required"] = sorted(
            ["added", "removed", "broadened", "narrowed"]
        )
    if "VerifierReasonCodeCount" in defs:
        defs["VerifierReasonCodeCount"]["required"] = sorted(["reason_code", "count"])
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
        defs["ToolSurfaceScopeFact"]["required"] = sorted(["scope", "kind", "tool_names", "broad"])
    if "ToolSurfaceControlFact" in defs:
        defs["ToolSurfaceControlFact"]["required"] = sorted(["kind", "tool", "source", "reason"])
    if "ToolSurfacePolicyFact" in defs:
        defs["ToolSurfacePolicyFact"]["required"] = sorted(["kind", "key", "value_hash", "summary"])
    if "ToolSurfaceFacts" in defs:
        defs["ToolSurfaceFacts"]["required"] = sorted(["tools", "scopes", "controls", "policies"])
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
        defs["ToolSurfaceFieldChange"]["required"] = sorted(["field", "before", "after"])
    if "ToolSurfaceToolChange" in defs:
        defs["ToolSurfaceToolChange"]["required"] = sorted(
            ["kind", "name", "source_type", "source_id", "changes"]
        )
    if "ToolSurfaceHighRiskEffectChange" in defs:
        defs["ToolSurfaceHighRiskEffectChange"]["required"] = sorted(["kind", "tool", "tag"])
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
        defs["ActionEvidenceFact"]["required"] = sorted(["owner", "runbook", "approval_ticket"])
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
        defs["ActionSurfaceFacts"]["required"] = sorted(["snapshot_version", "actions"])
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
            "required": sorted(["name", "source_type", "risk_tags", "auth_scopes", "confidence"]),
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

    # frameworks.{google_adk,langchain,crewai,conductor} surface counts. These are
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
    frameworks_sub["conductor"] = {
        "type": "object",
        "additionalProperties": True,
        "required": sorted(
            [
                "workflow_file_count",
                "workflow_count",
                "task_count",
                "llm_task_count",
                "mcp_discovery_task_count",
                "mcp_call_task_count",
                "human_checkpoint_count",
                "structurally_checkpointed_mcp_call_count",
                "sub_workflow_task_count",
                "dynamic_tool_surface_count",
                "unsupported_capability_count",
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


def write_policy_pack_schema(*, check_only: bool = False, drift: list[str] | None = None) -> bool:
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
            "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/docs/checks.json"
        ),
        "title": "Agents Shipgate Check Catalog",
        "description": (
            "Machine-readable catalog of built-in checks. Generated from "
            "agents_shipgate.checks.registry.check_catalog(). Do not edit by hand."
        ),
        "checks": [check.model_dump(mode="json") for check in check_catalog(plugins_enabled=False)],
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
    _postprocess_declaration_review(schema)
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
            release_item["required"] = [item for item in required if item != "capability_refs"]
    target = DOCS / f"packet-schema.v{minor}.json"
    return target, _canonical_json(schema)


def write_packet_schema(*, check_only: bool = False, drift: list[str] | None = None) -> bool:
    target, content = build_packet_schema()
    return _emit(target, content, check_only=check_only, drift=drift if drift is not None else [])


def _postprocess_authorization_evaluation(schema: dict[str, Any]) -> None:
    """Publish model-validator invariants in every schema embedding authorization."""

    evaluation = schema.get("$defs", {}).get("AuthorizationEvaluationV1")
    if not isinstance(evaluation, dict):
        return
    authority_fields = [
        "authorization_id",
        "authorization_request_id",
        "trust_policy_id",
        "key_id",
        "provider",
        "principal",
        "operation_id",
        "command",
        "issued_at",
        "expires_at",
    ]
    evaluation["allOf"] = [
        {
            "if": {"properties": {"status": {"const": "accepted"}}},
            "then": {
                "required": authority_fields,
                "properties": {
                    **{field: {"not": {"type": "null"}} for field in authority_fields},
                    "reason_codes": {"maxItems": 0},
                },
            },
        },
        {
            "if": {
                "properties": {"status": {"enum": ["rejected", "not_requested", "not_applicable"]}}
            },
            "then": {"properties": {"command": {"type": "null"}}},
        },
        {
            "if": {"properties": {"status": {"const": "rejected"}}},
            "then": {
                "required": ["reason_codes"],
                "properties": {"reason_codes": {"minItems": 1}},
            },
        },
        {
            "if": {"properties": {"status": {"enum": ["not_requested", "not_applicable"]}}},
            "then": {
                "properties": {
                    **{field: {"type": "null"} for field in authority_fields},
                }
            },
        },
    ]
    properties = evaluation.get("properties", {})
    if isinstance(properties, dict) and isinstance(properties.get("reason_codes"), dict):
        properties["reason_codes"]["uniqueItems"] = True


def _postprocess_declaration_review(schema: dict[str, Any]) -> None:
    """Pin the current declaration-review wire shape in every embedding schema.

    These models deliberately carry defaults so legacy report/packet/verifier
    normalizers can construct the honest disabled-empty projection. Current
    emitted artifacts, however, always serialize every key. Requiring those
    keys prevents a current-version payload from using Python defaults to erase
    review rows or their counts.
    """

    defs = schema.get("$defs", {})
    required_by_definition = {
        "ActionDeclarationSelectorFact": [
            "tool",
            "tool_id",
            "source_type",
            "source_id",
            "provider",
            "operation",
        ],
        "ActionDeclarationFact": [
            "row_id",
            "selector",
            "subject",
            "subject_id",
            "resolution",
            "declared_effect",
            "declared_risk_tags",
            "has_override",
            "override_identity",
            "basis",
            "declaration_hash",
            "manifest_path",
        ],
        "ActionDeclarationFacts": ["snapshot_version", "rows"],
        "DeclarationReviewSummary": [
            "evidence_consistent",
            "unverified",
            "acknowledged_override",
        ],
        "DeclarationReviewRow": [
            "row_id",
            "change_type",
            "bucket",
            "subject",
            "subject_id",
            "declared_effect",
            "declared_risk_tags",
            "observed_readings",
            "reason",
            "manifest_path",
            "acknowledged_overrides",
        ],
        "DeclarationReviewDecision": [
            "enabled",
            "base_comparison_requested",
            "base_kind",
            "changed_count",
            "summary",
            "rows",
            "notes",
        ],
    }
    for name, required in required_by_definition.items():
        definition = defs.get(name)
        if isinstance(definition, dict):
            definition["required"] = sorted(required)

    semantic = defs.get("SemanticCoverageDecision")
    if isinstance(semantic, dict):
        semantic["required"] = sorted(
            set(semantic.get("required", [])) | {"declaration_review"}
        )


def build_verifier_schema() -> tuple[Path, str]:
    """Generate docs/verifier-schema.v0.1.json from VerifierArtifact."""

    from agents_shipgate.schemas.verifier import VerifierArtifact

    schema = VerifierArtifact.model_json_schema()
    _postprocess_authorization_evaluation(schema)
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
    """Generate the current compact local-agent control schema."""

    from agents_shipgate.schemas.agent_result import AgentResultV2

    schema = AgentResultV2.model_json_schema()
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/agent-result-schema.{_AGENT_RESULT_SUFFIX}.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Agents Shipgate Agent Result v2"
    schema["description"] = (
        "JSON Schema for the compact local/MCP control projection. Generated "
        "from agents_shipgate.schemas.agent_result.AgentResultV2. Do not edit by hand."
    )
    target = DOCS / f"agent-result-schema.{_AGENT_RESULT_SUFFIX}.json"
    return target, _canonical_json(schema)


def build_codex_boundary_result_schema() -> tuple[Path, str]:
    """Build the frozen v2 compatibility schema for explicit maintenance."""

    from agents_shipgate.schemas.codex_boundary_result import CodexBoundaryResultV2

    schema = CodexBoundaryResultV2.model_json_schema()
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        "main/docs/codex-boundary-result-schema.v2.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Agents Shipgate Codex Boundary Result v2"
    schema["description"] = (
        "JSON Schema for shipgate check --format codex-boundary-json. "
        "Generated from "
        "agents_shipgate.schemas.codex_boundary_result.CodexBoundaryResultV2. "
        "Do not edit by hand."
    )
    target = DOCS / "codex-boundary-result-schema.v2.json"
    return target, _canonical_json(schema)


def build_agent_boundary_result_schema() -> tuple[Path, str]:
    """Generate the current neutral multi-host boundary result schema."""

    from agents_shipgate.schemas.agent_boundary import AgentBoundaryResultV1

    schema = AgentBoundaryResultV1.model_json_schema()
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/agent-boundary-result-schema.{_BOUNDARY_SUFFIX}.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Agents Shipgate Agent Boundary Result v1"
    schema["description"] = (
        "JSON Schema for shipgate check --format agent-boundary-json. "
        "Generated from agents_shipgate.schemas.agent_boundary. "
        "control.state is authoritative; the result is static and scope-bound."
    )
    target = DOCS / f"agent-boundary-result-schema.{_BOUNDARY_SUFFIX}.json"
    return target, _canonical_json(schema)


def build_verify_run_schema() -> tuple[Path, str]:
    """Generate the current verify-run schema."""

    from agents_shipgate.schemas.verify_run import VerifyRunArtifact

    schema = VerifyRunArtifact.model_json_schema()
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/verify-run-schema.{_VERIFY_RUN_SUFFIX}.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Agents Shipgate Verify Run v5"
    schema["description"] = (
        "JSON Schema for agents-shipgate-reports/verify-run.json. Generated "
        "from agents_shipgate.schemas.verify_run.VerifyRunArtifact. Do not "
        "edit by hand."
    )
    target = DOCS / f"verify-run-schema.{_VERIFY_RUN_SUFFIX}.json"
    return target, _canonical_json(schema)


def _verification_identity_schema(
    *, model, filename: str, title: str, description: str
) -> tuple[Path, str]:
    schema = model.model_json_schema()
    schema["$id"] = (
        f"https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/docs/{filename}"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = title
    schema["description"] = description
    return DOCS / filename, _canonical_json(schema)


def build_verification_plan_schema() -> tuple[Path, str]:
    from agents_shipgate.schemas.verification_identity import VerificationPlan

    return _verification_identity_schema(
        model=VerificationPlan,
        filename="verification-plan-schema.v1.json",
        title="Agents Shipgate Verification Plan v1",
        description="Content-addressed immutable verification request plan.",
    )


def build_verification_unit_result_schema() -> tuple[Path, str]:
    from agents_shipgate.schemas.verification_identity import VerificationUnitResult

    return _verification_identity_schema(
        model=VerificationUnitResult,
        filename="verification-unit-result-schema.v1.json",
        title="Agents Shipgate Verification Unit Result v1",
        description="Decision-free normalized worker output for offline assembly.",
    )


def build_verification_artifact_manifest_schema() -> tuple[Path, str]:
    from agents_shipgate.schemas.verification_identity import VerificationArtifactManifest

    return _verification_identity_schema(
        model=VerificationArtifactManifest,
        filename="verification-artifact-manifest-schema.v1.json",
        title="Agents Shipgate Verification Artifact Manifest v1",
        description="Content-addressed manifest of assembled verification artifacts.",
    )


def build_verification_receipt_schema() -> tuple[Path, str]:
    from agents_shipgate.schemas.verification_identity import VerificationReceipt

    return _verification_identity_schema(
        model=VerificationReceipt,
        filename="verification-receipt-schema.v1.json",
        title="Agents Shipgate Verification Receipt v1",
        description="Terminal closure record written after every artifact is finalized.",
    )


def build_current_control_schema() -> tuple[Path, str]:
    from agents_shipgate.schemas.current_control import CurrentControlPointer

    return _verification_identity_schema(
        model=CurrentControlPointer,
        filename="current-control-schema.v1.json",
        title="Agents Shipgate Current Control Pointer v1",
        description=(
            "JSON Schema for agents-shipgate-reports/current-control.json, the "
            "one atomic entry point naming the control identity that is current "
            "now. Generated from "
            "agents_shipgate.schemas.current_control.CurrentControlPointer. Do "
            "not edit by hand."
        ),
    )


def build_agent_control_envelope_schema() -> tuple[Path, str]:
    """Publish the envelope union, not a flattened model.

    Generated from the ``TypeAdapter`` rather than a single class so the
    per-state variants reach the published document as a discriminated
    ``oneOf``. That is what makes the safety separations enforceable by an
    off-the-shelf draft 2020-12 validator: a flat schema accepted
    ``execution: "failed"`` beside ``control_state: "complete"``, and a
    coding-agent route on a stopping state, because Pydantic model validators
    have no JSON Schema representation.
    """

    from agents_shipgate.schemas.agent_control_envelope import (
        AGENT_CONTROL_ENVELOPE_ADAPTER,
    )

    filename = "agent-control-schema.v1.json"
    schema = AGENT_CONTROL_ENVELOPE_ADAPTER.json_schema()
    schema["$id"] = (
        f"https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/main/docs/{filename}"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Agents Shipgate Agent Control Envelope v1"
    schema["description"] = (
        "JSON Schema for the compact shipgate.agent_control/v1 control envelope "
        "emitted by `verify --format control`, `check --format "
        "agent-control-json`, and `agents-shipgate agent control`. A projection "
        "of the authoritative control state, never a second decision. Generated "
        "from agents_shipgate.schemas.agent_control_envelope. Do not edit by hand."
    )
    return DOCS / filename, _canonical_json(schema)


def build_human_authorization_schema() -> tuple[Path, str]:
    """Generate the signed authorization protocol schema family."""

    from pydantic import TypeAdapter

    from agents_shipgate.schemas.human_authorization import (
        AuthorizationEvaluationV1,
        HumanAuthorizationRequestV1,
        HumanAuthorizationTrustPolicyV1,
        HumanAuthorizationV1,
    )

    schema = TypeAdapter(
        HumanAuthorizationRequestV1
        | HumanAuthorizationV1
        | AuthorizationEvaluationV1
        | HumanAuthorizationTrustPolicyV1
    ).json_schema()
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        "main/docs/human-authorization-schema.v1.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Agents Shipgate Human Authorization Protocol v1"
    schema["description"] = (
        "JSON Schema family for the unsigned request, externally signed grant, "
        "fail-closed evaluation, and external trust policy. Agents Shipgate "
        "does not provide signing or approval authority. Content-address, "
        "cross-object identity, canonical ordering, and time-window relations "
        "also require application-level model validation."
    )
    _postprocess_authorization_evaluation(schema)
    return DOCS / "human-authorization-schema.v1.json", _canonical_json(schema)


def build_agent_handoff_schema() -> tuple[Path, str]:
    """Generate the current agent-handoff schema."""

    from agents_shipgate.schemas.agent_handoff import (
        AGENT_HANDOFF_SCHEMA_VERSION,
        AgentHandoffArtifact,
    )

    schema = AgentHandoffArtifact.model_json_schema()
    _postprocess_authorization_evaluation(schema)
    major = AGENT_HANDOFF_SCHEMA_VERSION.rsplit("/v", 1)[-1]
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/agent-handoff-schema.v{major}.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = f"Agents Shipgate Agent Handoff v{major}"
    schema["description"] = (
        "JSON Schema for agents-shipgate-reports/agent-handoff.json. "
        "Generated from "
        "agents_shipgate.schemas.agent_handoff.AgentHandoffArtifact. It is "
        "a compact projection for coding agents and does not gate releases; "
        "release_decision.decision remains the only gate."
    )
    target = DOCS / f"agent-handoff-schema.v{major}.json"
    return target, _canonical_json(schema)


def build_preflight_schema() -> tuple[Path, str]:
    """Generate the current preflight schema."""

    from agents_shipgate.schemas.preflight import (
        PREFLIGHT_SCHEMA_VERSION,
        PreflightResultV4,
    )

    schema = PreflightResultV4.model_json_schema()
    minor = PREFLIGHT_SCHEMA_VERSION
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/preflight-schema.v{minor}.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = f"Agents Shipgate Preflight Result v{minor}"
    schema["description"] = (
        "JSON Schema for shipgate preflight --json. Generated from "
        "agents_shipgate.schemas.preflight.PreflightResultV4. It is a "
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


#: The one place a published capability object may leave a property out of
#: ``required``, and why. ``DigestSet`` is in-toto's type, not ours: it is
#: ``map<string, string>``, so an algorithm the producer did not compute is
#: absent rather than ``null``. Everywhere else in these schemas absence is
#: spelled as a value, and the assertion below keeps it that way.
_CAPABILITY_OPTIONAL_PROPERTIES: dict[str, frozenset[str]] = {
    "CapabilityDeltaSubjectDigest": frozenset({"gitCommit"}),
}


def _postprocess_capability_payload(schema: dict[str, Any]) -> None:
    """Push every constraint JSON Schema *can* express into the published file.

    Pydantic's ``model_json_schema`` does not emit ``model_validator`` rules, so
    a consumer handed only this file would validate far less than the spec
    promises. Several of those rules are ordinary conditional schemas, and an
    external tool should get them for free rather than be told to reimplement
    them. What genuinely cannot be expressed here — anything requiring a
    recomputation — is named as stage two in the schema description and
    enumerated in the spec page.
    """

    defs = schema["$defs"]

    def not_null(field: str) -> dict[str, Any]:
        return {"properties": {field: {"not": {"type": "null"}}}}

    def is_null(field: str) -> dict[str, Any]:
        return {"properties": {field: {"type": "null"}}}

    def when(field: str, value: Any) -> dict[str, Any]:
        return {"properties": {field: {"const": value}}, "required": [field]}

    if "CapabilityRecordTransitionEntry" not in defs:  # pragma: no cover
        raise AssertionError(
            "the capability payload post-processor was handed a schema that "
            "carries no capability records; it would silently publish none of "
            "the stage-one rules"
        )
    entry = defs["CapabilityRecordTransitionEntry"]
    entry["allOf"] = [
        # A membership change has exactly one side, no changed dimensions, and
        # one honest direction.
        {
            "if": when("transition", "added"),
            "then": {
                "allOf": [
                    is_null("before"),
                    not_null("after"),
                    {"properties": {"changed_dimensions": {"maxItems": 0}}},
                    when("semantic_direction", "added"),
                ]
            },
        },
        {
            "if": when("transition", "removed"),
            "then": {
                "allOf": [
                    not_null("before"),
                    is_null("after"),
                    {"properties": {"changed_dimensions": {"maxItems": 0}}},
                    when("semantic_direction", "removed"),
                ]
            },
        },
        # A paired change carries both sides, at least one moved dimension, and
        # cannot claim a membership direction.
        *(
            {
                "if": when("transition", transition),
                "then": {
                    "allOf": [
                        not_null("before"),
                        not_null("after"),
                        {"properties": {"changed_dimensions": {"minItems": 1}}},
                        {
                            "properties": {
                                "semantic_direction": {
                                    "not": {"enum": ["added", "removed"]}
                                }
                            }
                        },
                    ]
                },
            }
            for transition in ("changed", "reidentified")
        ),
    ]

    subject = defs["CapabilityDeltaSubject"]
    subject["allOf"] = [
        # `transition` follows from the presence pair, and a subject present on
        # neither side is not a row at all.
        {
            "if": {
                "allOf": [when("present_in_base", base), when("present_in_head", head)]
            },
            "then": when("transition", expected),
        }
        for base, head, expected in (
            (False, True, "added"),
            (True, False, "removed"),
            (True, True, "modified"),
        )
    ] + [
        {
            "not": {
                "allOf": [
                    when("present_in_base", False),
                    when("present_in_head", False),
                ]
            }
        },
        # Presence bounds the changes, not only the transition label: a subject
        # base never had cannot carry a capability that changed or went away.
        *(
            {
                "if": when(field, False),
                "then": {
                    "properties": {
                        "changes": {
                            "items": {
                                "properties": {"transition": {"const": allowed}},
                                "required": ["transition"],
                            }
                        }
                    }
                },
            }
            for field, allowed in (
                ("present_in_base", "added"),
                ("present_in_head", "removed"),
            )
        ),
    ]

    # The permission shapes the classifier can actually produce. A consumer
    # reasons about this block — "is this read-only?" — so a combination the
    # lattice never emits is a claim with no meaning, and stage one can say so.
    permission = defs["CapabilityPermissionFacts"]
    permission["allOf"] = [
        {
            "if": when("status", "unavailable"),
            "then": {
                "allOf": [
                    {"properties": {"classes": {"maxItems": 0}}},
                    when("side_effect_unknown", True),
                ]
            },
        },
        {
            "if": when("status", "measured"),
            "then": {"properties": {"classes": {"minItems": 1}}},
        },
        # `read` is the whole profile or not in it at all.
        {
            "if": {
                "properties": {"classes": {"contains": {"const": "read"}}},
                "required": ["classes"],
            },
            "then": {"properties": {"classes": {"maxItems": 1}}},
        },
        # `destructive` always carries `write`.
        {
            "if": {
                "properties": {"classes": {"contains": {"const": "destructive"}}},
                "required": ["classes"],
            },
            "then": {"properties": {"classes": {"contains": {"const": "write"}}}},
        },
        # Unknown side effects and the `unknown` class are the same statement,
        # in both directions.
        {
            "if": when("side_effect_unknown", True),
            "then": {
                "anyOf": [
                    when("status", "unavailable"),
                    {"properties": {"classes": {"contains": {"const": "unknown"}}}},
                ]
            },
        },
        {
            "if": {
                "properties": {"classes": {"contains": {"const": "unknown"}}},
                "required": ["classes"],
            },
            "then": when("side_effect_unknown", True),
        },
    ]

    # A membership change has no second record to compare against.
    for transition in ("added", "removed"):
        entry["allOf"].append(
            {
                "if": when("transition", transition),
                "then": {"properties": {"semantic_changes": {"maxItems": 0}}},
            }
        )

    # Naming a subject outside analysis requires having looked.
    defs["CapabilityAnalysisCoverage"]["allOf"] = [
        {
            "if": {
                "properties": {"status": {"not": {"const": "complete"}}},
                "required": ["status"],
            },
            "then": {"properties": {"subjects_outside_analysis": {"maxItems": 0}}},
        }
    ]

    # Identical rows are the cheap half of "one subject, one row"; the rest is
    # stage two, because uniqueness is on a sub-key.
    for name, field in (
        ("CapabilityStatePayloadV1", "subjects"),
        ("CapabilityDeltaPayloadV1", "subjects"),
        ("CapabilityStateSubject", "capabilities"),
        ("CapabilityDeltaSubject", "changes"),
        ("CapabilityAnalysisCoverage", "subjects_outside_analysis"),
        ("CapabilityCoverageDelta", "newly_outside_analysis"),
        ("CapabilityCoverageDelta", "no_longer_outside_analysis"),
        ("CapabilityRecordTransitionEntry", "semantic_changes"),
        ("CapabilityRecordTransitionEntry", "changed_dimensions"),
        ("CapabilityPermissionFacts", "classes"),
        ("CapabilityRecord", "resource"),
        ("CapabilityRecord", "scope"),
        ("CapabilityRecord", "risk_tags"),
        ("CapabilityAuthorityFacts", "scopes"),
        ("CapabilityAuthorityFacts", "broad_scopes"),
    ):
        # Skip what this document does not carry: the attestation embeds the
        # delta view alone, and reusing this one post-processor is how the two
        # published schemas cannot express different rules for the same object.
        if name in defs:
            defs[name]["properties"][field]["uniqueItems"] = True

    # Every object in the published schema requires every property it declares.
    # A field with a schema default drops out of `required`, and a wire format
    # whose spec says "always present" must not have any.
    for name, definition in defs.items():
        if definition.get("type") != "object" or "properties" not in definition:
            continue
        allowed = _CAPABILITY_OPTIONAL_PROPERTIES.get(name, frozenset())
        missing = sorted(
            set(definition["properties"]) - set(definition.get("required", [])) - allowed
        )
        if missing:  # pragma: no cover - the generator would be shipping a hole
            raise AssertionError(
                f"{name} publishes optional properties {missing}; every field of "
                "the capability payload must be required on the wire"
            )


def build_capability_payload_schema() -> tuple[Path, str]:
    """Generate the frozen shared capability payload schema (#469)."""

    from agents_shipgate.schemas.capability_payload import (
        CAPABILITY_PAYLOAD_SCHEMA_VERSION,
        CapabilityPayloadV1,
    )

    schema = CapabilityPayloadV1.model_json_schema()
    _postprocess_capability_payload(schema)
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        "main/docs/capability-payload-schema.v1.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Agents Shipgate Capability Payload v1"
    schema["description"] = (
        "Frozen JSON Schema for "
        f"{CAPABILITY_PAYLOAD_SCHEMA_VERSION} — the one capability payload "
        "shared by the exported capability delta and the committed capability "
        "state. Generated from "
        "agents_shipgate.schemas.capability_payload.CapabilityPayloadV1. Two "
        "views discriminated on `view`; one subject is one row. "
        "VALIDATION IS TWO STAGES: this file is stage one and is not "
        "sufficient on its own. The rules that require recomputation — "
        "subject-key derivation, summary and transition rollups, changed-"
        "dimension derivation, state-digest verification, cross-row "
        "uniqueness, and the coverage transition — cannot be expressed in "
        "JSON Schema and are listed as stage two in "
        "docs/capability-payload.md; a consumer that runs only stage one does "
        "not have the guarantees the spec states. It is non-gating and is not "
        "part of report.json; release_decision.decision remains the only gate."
    )
    target = DOCS / "capability-payload-schema.v1.json"
    return target, _canonical_json(schema)


# The worked examples are projected from a shipped sample rather than written
# by hand, for the reason #425 recorded: a hand-written example is a claim
# about the format that nothing checks, and it drifts silently. Generating
# them here puts them under the same `--check` drift gate as the schemas.
_PAYLOAD_EXAMPLE_SAMPLE = REPO_ROOT / "samples" / "ai_generated_refund_pr"


def _capability_payload_example_facts() -> tuple[list[Any], list[Any]]:
    """Build the base and head capability facts of the refund-PR sample.

    The sample keeps its head tool surface under ``_head/`` because the fixture
    runner materializes a two-commit history from it. Reproduce that here with a
    temporary copy: static inputs only, no git, no network.
    """

    import shutil
    import tempfile

    from agents_shipgate.cli.capability import build_capability_lock_from_config

    base = build_capability_lock_from_config(
        config=_PAYLOAD_EXAMPLE_SAMPLE / "shipgate.yaml",
        no_plugins=True,
        verbose=False,
    )
    with tempfile.TemporaryDirectory() as tmp:
        head_root = Path(tmp) / "head"
        shutil.copytree(_PAYLOAD_EXAMPLE_SAMPLE, head_root)
        shutil.copyfile(head_root / "_head" / "tools.json", head_root / "tools.json")
        head = build_capability_lock_from_config(
            config=head_root / "shipgate.yaml",
            no_plugins=True,
            verbose=False,
        )
    return base.capabilities, head.capabilities


def build_capability_payload_state_example() -> tuple[Path, str]:
    """Generate the worked state-view example from the shipped sample."""

    from agents_shipgate.core.capability_payload import project_capability_state

    base_facts, _ = _capability_payload_example_facts()
    payload = project_capability_state(base_facts, ref="samples/ai_generated_refund_pr")
    target = DOCS / "examples" / "capability-payload.v1.state.example.json"
    return target, _canonical_json(payload.model_dump(mode="json"))


def build_capability_payload_delta_example() -> tuple[Path, str]:
    """Generate the worked delta-view example from the shipped sample."""

    from agents_shipgate.core.capability_payload import project_capability_delta

    base_facts, head_facts = _capability_payload_example_facts()
    payload = project_capability_delta(
        base_facts,
        head_facts,
        base_ref="samples/ai_generated_refund_pr",
        head_ref="samples/ai_generated_refund_pr/_head",
    )
    target = DOCS / "examples" / "capability-payload.v1.delta.example.json"
    return target, _canonical_json(payload.model_dump(mode="json"))


def build_capability_delta_attestation_schema() -> tuple[Path, str]:
    """Generate the frozen in-toto delta-attestation schema (#470)."""

    from agents_shipgate.schemas.capability_attestation import (
        CAPABILITY_DELTA_ATTESTATION_SCHEMA_VERSION,
        CAPABILITY_DELTA_PREDICATE_TYPE,
        GIT_OBJECT_PATTERN,
        CapabilityDeltaAttestationV1,
    )

    schema = CapabilityDeltaAttestationV1.model_json_schema()
    # The same post-processor the payload schema runs. The attestation embeds
    # the delta view, so running a second, parallel set of stage-one rules over
    # the same objects is exactly the divergence the shared payload exists to
    # prevent.
    _postprocess_capability_payload(schema)
    # Pydantic's validation schema for an optional nullable field admits
    # ``null``, and the producer never emits it: a ``DigestSet`` is
    # ``map<string, string>``, so an uncomputed algorithm is absent. Publishing
    # the nullable form would tell an external validator to accept a document
    # this format does not produce and an in-toto consumer cannot type.
    digest = schema["$defs"]["CapabilityDeltaSubjectDigest"]["properties"]["gitCommit"]
    digest.pop("anyOf", None)
    digest.pop("default", None)
    digest["type"] = "string"
    digest["pattern"] = GIT_OBJECT_PATTERN
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        "main/docs/capability-delta-attestation-schema.v1.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Agents Shipgate Capability Delta Attestation v1"
    schema["description"] = (
        "Frozen JSON Schema for an in-toto Statement carrying "
        f"{CAPABILITY_DELTA_ATTESTATION_SCHEMA_VERSION} — the exported "
        "capability delta, published so a consumer can read what an agent can "
        "do after a change without running Agents Shipgate. predicateType is "
        f"{CAPABILITY_DELTA_PREDICATE_TYPE}, and `predicate.delta` is the "
        "frozen shipgate.capability_payload/v1 delta view unchanged. "
        "VALIDATION IS TWO STAGES: this file is stage one and is not "
        "sufficient on its own. Beyond the payload's own stage-two rules "
        "(docs/capability-payload.md), a consumer must check that the attested "
        "subject's gitTree equals predicate.delta.head.ref, and that both refs "
        "are git object ids. The statement is emitted unsigned; authenticity "
        "is the transport's job. It is non-gating: "
        "release_decision.decision remains the only release gate."
    )
    target = DOCS / "capability-delta-attestation-schema.v1.json"
    return target, _canonical_json(schema)


def _capability_payload_example_trees() -> tuple[str, str]:
    """The git tree object ids of the shipped sample's base and head states.

    Real object ids, not placeholders: the attestation binds its subject to a
    tree, and a worked example whose subject is fabricated is a claim about the
    format that nothing can check. ``git write-tree`` is deterministic — a tree
    id is a function of file names, contents and modes alone, with no author,
    no date and no history — so the committed example is stable across machines
    and re-runs, which is what puts it under the ``--check`` drift gate.
    """

    import shutil
    import subprocess
    import tempfile

    def tree_id(root: Path) -> str:
        git = (
            "git",
            # Neutralize whatever the developer's global config says: a
            # line-ending rewrite or a stray excludes file would change the
            # blob contents, and with them the published example.
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.eol=lf",
            "-c",
            "core.excludesfile=",
            "-c",
            "core.attributesfile=",
            "-C",
            str(root),
        )
        subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
        subprocess.run([*git, "add", "-A"], check=True)
        written = subprocess.run(
            [*git, "write-tree"],
            check=True,
            capture_output=True,
            text=True,
        )
        shutil.rmtree(root / ".git")
        return written.stdout.strip()

    with tempfile.TemporaryDirectory() as tmp:
        base_root = Path(tmp) / "base"
        head_root = Path(tmp) / "head"
        shutil.copytree(_PAYLOAD_EXAMPLE_SAMPLE, base_root)
        shutil.copytree(_PAYLOAD_EXAMPLE_SAMPLE, head_root)
        # ``_head/`` is the fixture runner's staging area for the second
        # commit, not part of either reviewed state.
        shutil.copyfile(head_root / "_head" / "tools.json", head_root / "tools.json")
        shutil.rmtree(base_root / "_head")
        shutil.rmtree(head_root / "_head")
        return tree_id(base_root), tree_id(head_root)


def build_capability_delta_attestation_example() -> tuple[Path, str]:
    """Generate the worked attestation from the shipped sample (#470).

    ``verification.status`` is ``unbound`` and that is the honest value: this
    example is projected from the sample's capability facts, not emitted by a
    ``verify`` run, so there is no receipt to chain to. A ``verify`` emission
    always carries ``bound`` — the run identities it would name mix in the
    engine build and the platform, which is precisely why a committed golden
    cannot be one.
    """

    from agents_shipgate.core.capability_attestation import (
        project_capability_delta_attestation,
    )
    from agents_shipgate.schemas.capability_attestation import attestation_json

    base_facts, head_facts = _capability_payload_example_facts()
    base_tree, head_tree = _capability_payload_example_trees()
    attestation = project_capability_delta_attestation(
        base_facts,
        head_facts,
        subject_name="samples/ai_generated_refund_pr",
        base_tree_sha=base_tree,
        head_tree_sha=head_tree,
        head_commit_sha=None,
    )
    target = DOCS / "examples" / "capability-delta-attestation.v1.example.json"
    return target, _canonical_json(attestation_json(attestation))


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
        HostGrantsInventoryArtifactV2,
    )

    schema = HostGrantsInventoryArtifactV2.model_json_schema()
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


def build_host_grants_baseline_schema() -> tuple[Path, str]:
    """Generate the current host-grants baseline schema."""

    from agents_shipgate.schemas.host_grants import (
        HOST_GRANTS_BASELINE_SCHEMA_VERSION,
        HostGrantsBaselineArtifactV2,
    )

    schema = HostGrantsBaselineArtifactV2.model_json_schema()
    minor = HOST_GRANTS_BASELINE_SCHEMA_VERSION
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/host-grants-baseline-schema.v{minor}.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = f"Agents Shipgate Host Grants Baseline v{minor}"
    schema["description"] = (
        "JSON Schema for a human-acknowledged, scope-bound host-grants baseline."
    )
    target = DOCS / f"host-grants-baseline-schema.v{minor}.json"
    return target, _canonical_json(schema)


def build_host_grants_drift_schema() -> tuple[Path, str]:
    """Generate the current host-grants drift schema."""

    from agents_shipgate.schemas.host_grants import (
        HOST_GRANTS_DRIFT_SCHEMA_VERSION,
        HostGrantsDriftArtifactV2,
    )

    schema = HostGrantsDriftArtifactV2.model_json_schema()
    minor = HOST_GRANTS_DRIFT_SCHEMA_VERSION
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        f"main/docs/host-grants-drift-schema.v{minor}.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = f"Agents Shipgate Host Grants Drift v{minor}"
    schema["description"] = "JSON Schema for scope-aware host-grant drift and incomparability."
    target = DOCS / f"host-grants-drift-schema.v{minor}.json"
    return target, _canonical_json(schema)


# Public ordered list of (name, builder) pairs. Tests and the CLI iterate this
# instead of hardcoding individual calls, so adding a new schema is one edit.
# --- Determinism boundary ---------------------------------------------------


_BOUNDARY_INTRO = """\
Agents Shipgate abstains where it cannot prove something. That is only useful
if the line is published: an `insufficient_evidence` verdict outside the
boundary reads as *"this tool cannot analyse my repository"*, and inside it as
*"this repository declares its tools in a shape nothing static can read"*.
Those are different answers, and only one of them tells you what to do next.

Read this page as a scoping answer. Find the input you use, find the shape your
repository declares tools in, and the row says what the scan can establish about
it and what a verdict may rest on. Every "cannot" here is a property of static
reading, not of your code.

Nothing on this page is written by hand. Each built-in adapter declares what it
reads for each declaration shape and the extraction-confidence ceiling that
shape reaches, beside the code that mints it; the outcome column is computed by
asking the engine's own completeness predicates about those declared facts. CI
regenerates the page and fails on any difference, so a boundary that moves in
the code moves here in the same commit or the build stops.
"""

_BOUNDARY_CEILING_NOTE = """\
`ceiling` is the best `extraction_confidence` the route reaches, and `high` is
the only value that leaves an action able to be pass-eligible. Below it there is
no tolerance to spend: an action that cannot be proven raises an
`incomplete_surface` semantic gap, semantic gaps are zero-tolerance, and one of
them is enough to withhold a verdict. A single action from a `low_confidence`
route is therefore already a scan that cannot reach `passed` — the point is not
how many you have.

A ceiling is a ceiling, not a promise — an individual action can land lower, and
effect, authority, identity, and binding evidence are judged separately and can
withhold a pass from a route marked `proven` here.
"""

_BOUNDARY_REMEDY = """\
There is no universal remedy, and the per-input line below each table says which
one applies. Where an input accepts a **reviewed tool inventory** — an
MCP-export-shaped file listing the tools the source really exposes, declared in
the manifest and merged through review — that file is read as a published
contract and reaches `high`. Four inputs have such a key; the rest do not, and
telling their adopters to write one sends them after a manifest key that does
not exist.

Two rows an inventory never moves. A wildcard inventory (`wildcard: true`) is a
reviewed file that names nothing, so it loads at `high` and still proves no
surface — review is not the ingredient that was missing. And where the table
says `not_extracted`, the scan is telling you it read a declaration and refused
to guess what it produces; that is a statement about the declaration, not a gap
an inventory fills. `not_applicable` means the input has no such declaration
form at all.

Nothing an agent can write for itself moves a row. That is the point of the
boundary rather than an accident of it.
"""


def _boundary_cell_label(cell: object) -> str:
    shape = cell.shape
    variant = cell.variant
    return f"{shape} — {variant}" if variant else str(shape)


def _boundary_anchor(source: Any) -> str:
    """The GitHub heading anchor for one input's section.

    GitHub lowercases, drops every character that is not alphanumeric, a space,
    or a hyphen, then maps spaces to hyphens. Approximating that with a couple
    of `replace` calls produced `#openai-agents-sdk-(python)`, which resolves to
    nothing — the parentheses survive in the link and not in the heading.
    """

    text = "".join(
        char
        for char in source.label.lower()
        if char.isalnum() or char in " -"
    )
    return text.replace(" ", "-")


def _boundary_table_text(value: str) -> str:
    return value.replace("|", "\\|")


def _boundary_remedy_line(source: Any) -> str:
    """The remedy that actually applies to this input.

    Derived from `inventory_manifest_key()` and from whether any route here
    reaches `proven` at all, because the universal-inventory promise was false
    for most rows: there is no inventory key for `sdk_function`,
    `conductor_mcp_call`, or `codex_config_mcp`.
    """

    inventory_key = source.inventory_key
    proven = [
        cell for cell in source.cells if cell.outcome == "proven"
    ]
    if inventory_key:
        return (
            f"**Getting to `proven` here:** declare a reviewed tool inventory at "
            f"`{inventory_key}[]`. It is read as a published contract, so it "
            "reaches `high` — unless it declares `wildcard: true`, which names "
            "nothing."
        )
    if proven:
        labels = " or ".join(f"`{_boundary_cell_label(cell)}`" for cell in proven)
        return (
            f"**Getting to `proven` here:** only via {labels}. The engine "
            "prescribes no `tool_inventories[]` remediation for this input, so "
            "that route is the whole answer."
        )
    return (
        "**Getting to `proven` here:** no route on this input reaches `proven`. "
        "Publish the actions through an input that does, or accept that a "
        "verdict cannot rest on this surface alone."
    )


def build_determinism_boundary_matrix() -> tuple[Path, str]:
    """Generate docs/determinism-boundary.json from the adapter registry."""

    from agents_shipgate.inputs.coverage import build_boundary_matrix

    matrix = build_boundary_matrix()
    payload = {
        "$id": (
            "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
            "main/docs/determinism-boundary.json"
        ),
        "title": "Agents Shipgate Determinism Boundary",
        "description": (
            "What each built-in input can establish, per declaration shape: the "
            "extraction-confidence ceiling and what that ceiling means for a "
            "release verdict. Generated from "
            "agents_shipgate.inputs.coverage.build_boundary_matrix(). Do not "
            "edit by hand."
        ),
        **matrix.model_dump(mode="json"),
    }
    return DOCS / "determinism-boundary.json", _canonical_json(payload)


#: Plain words for each outcome, for the table an adopter actually reads. The
#: tokens stay in the JSON and in the per-input detail; a reader scanning for
#: their framework should not have to learn `set_unproven` first (#478 review).
_BOUNDARY_OUTCOME_WORDS: dict[str, str] = {
    "proven": "✅ proven",
    "set_unproven": "⚠️ set unproven",
    "low_confidence": "⚠️ not proven",
    "not_extracted": "✖ not read",
    "not_applicable": "· n/a",
}

#: The reader's own words for each declaration shape. They recognise
#: `tools=[a, b]` faster than "literal registration", so the column headers ask
#: the question and the token is defined once, in the reference at the bottom.
_BOUNDARY_SHAPE_HEADINGS: dict[str, str] = {
    "export_artifact": "…in a contract file",
    "literal_registration": "…written out in code",
    "factory": "…built by a call",
    "dynamic_construction": "…not named at all",
}


def _boundary_answer(source: Any, matrix: Any) -> list[str]:
    """The plain-language answer for one input.

    Composed from the outcomes the engine already derived, never written per
    adapter — a hand-written paragraph per input is the drift this whole page
    exists to avoid. Three shapes of answer, because there are three shapes of
    repository: the contract file *is* the surface, the code is read but never
    proven, or nothing here reaches a verdict at all.
    """

    proven = [cell for cell in source.cells if cell.outcome == "proven"]
    code_route = matrix.best_outcome(source, "literal_registration")

    # Ordered by what the reader has in their hands. Leading with the contract
    # file told a LangChain adopter "yes, from a contract file" when the thing
    # they actually wrote is Python, and buried the fact that their code *is*
    # read — just never proven.
    if not proven:
        answer = (
            "**Not on its own.** shipgate may still read and check actions here "
            "— the routes below say which — but none of them proves one well "
            "enough for a verdict to rest on it."
        )
    elif code_route == "proven":
        answer = (
            "**Yes, from your source** — when the file resolves completely. A "
            "single unresolved expression anywhere in the module drops "
            "everything that module declares, including tools a resolved "
            "toolset contributed."
        )
    elif code_route in {"low_confidence", "set_unproven"}:
        answer = (
            "**Read, but not proven from your source alone.** shipgate reads "
            "the tools you write out and can say what each one does. What it "
            "cannot do is prove that list is *all* of them — so it will not "
            "pass them on their own, however ordinary they look."
        )
    elif code_route == "not_extracted":
        answer = (
            "**Not from your source.** shipgate read the declaration and "
            "refused to guess what it produces, which is a statement about the "
            "declaration rather than a gap in the scan."
        )
    else:
        answer = (
            "**Yes, from the file you point it at.** That file is the contract "
            "and it is read as written; what it does not name, shipgate does "
            "not invent."
        )

    return [answer, "", _boundary_remedy_line(source)]


def build_determinism_boundary_page() -> tuple[Path, str]:
    """Generate docs/determinism-boundary.md from the adapter registry.

    Ordered for someone who arrived from an `insufficient_evidence` verdict and
    wants to know whether shipgate can read *their* repository. The precise
    matrix is still here in full — it moved below the answer, not out of it.
    """

    from agents_shipgate.inputs.coverage import (
        CELL_OUTCOME_VERDICTS,
        DECLARATION_SHAPE_DEFINITIONS,
        DECLARATION_SHAPE_ORDER,
        build_boundary_matrix,
    )

    matrix = build_boundary_matrix()
    lines: list[str] = [
        "# What Agents Shipgate can prove about your repository",
        "",
        "> Generated by `scripts/generate_schemas.py` from the adapter registry.",
        "> Do not edit by hand — re-run the script to update.",
        "",
        _BOUNDARY_INTRO,
        "## Start here",
        "",
        "1. Find your framework in the table below.",
        (
            "2. Read across to the way **your** repository declares its tools. "
            "Most repositories use more than one."
        ),
        (
            "3. Anything that is not ✅ has a fix, and it is written under that "
            "framework's heading."
        ),
        "",
        (
            "If you landed here from an `insufficient_evidence` verdict, this "
            "page is the reason for it. A ⚠️ or ✖ on the row you use is not a "
            "bug report — it is shipgate telling you which of your declarations "
            "it could not read, and that is a scoping answer you can act on."
        ),
        "",
        "| Your framework | " + " | ".join(
            _BOUNDARY_SHAPE_HEADINGS[shape] for shape in DECLARATION_SHAPE_ORDER
        ) + " |",
        "| --- | " + " | ".join("---" for _ in DECLARATION_SHAPE_ORDER) + " |",
    ]
    for source in matrix.sources:
        cells = [
            _BOUNDARY_OUTCOME_WORDS[matrix.best_outcome(source, shape)]
            for shape in DECLARATION_SHAPE_ORDER
        ]
        lines.append(f"| [{source.label}](#{_boundary_anchor(source)}) | " + " | ".join(cells) + " |")
    lines.extend(
        [
            "",
            "**✅ proven** — a clean verdict can rest on this. "
            "**⚠️ not proven / set unproven** — shipgate read the action but "
            "cannot prove it saw the whole set, so it will not pass it. "
            "**✖ not read** — nothing enters the catalog; shipgate says so "
            "rather than reporting an empty surface. **· n/a** — your framework "
            "has no such declaration.",
            "",
            (
                "Where an input shows more than one answer for a shape, the "
                "table shows the best of them and the framework's own section "
                "lists every route."
            ),
            "",
            "## Your framework",
            "",
        ]
    )
    for source in matrix.sources:
        routes = []
        if "tool_sources" in source.configured_as:
            routes.append(f"a `tool_sources[]` entry of type `{source.adapter}`")
        if "manifest_section" in source.configured_as:
            routes.append(f"the top-level `{source.manifest_section}:` manifest section")
        configured = " or ".join(routes)
        if source.manifest_section_role == "supplements":
            configured += (
                f" The top-level `{source.manifest_section}:` section supplements "
                "what that entry finds and cannot activate this input on its own."
            )
        lines.extend(
            [
                f"### {source.label}",
                "",
                f"*Configured as {configured}.* {source.reads}",
                "",
                "**Can a verdict rest on it?**",
                "",
            ]
        )
        lines.extend(_boundary_answer(source, matrix))
        lines.extend(
            [
                "",
                "<details>",
                f"<summary>Every route this input has, in full ({len(source.cells)})</summary>",
                "",
                (
                    "| Declaration shape | What is read | Emits | Ceiling | "
                    "Outcome | Evidence gaps | Raises |"
                ),
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for cell in source.cells:
            emits = ", ".join(f"`{value}`" for value in cell.emits) or "—"
            ceiling = f"`{cell.ceiling}`" if cell.ceiling else "—"
            gaps = ", ".join(f"`{gap}`" for gap in cell.evidence_gaps) or "—"
            raises = ", ".join(f"`{check}`" for check in cell.raises) or "—"
            lines.append(
                f"| `{_boundary_cell_label(cell)}` "
                f"| {_boundary_table_text(cell.reads)} "
                f"| {emits} | {ceiling} | `{cell.outcome}` | {gaps} | {raises} |"
            )
        lines.extend(["", "</details>", ""])

    lines.extend(["## Reference", "", "### The four declaration shapes", "", ])
    for shape in DECLARATION_SHAPE_ORDER:
        lines.append(
            f"- **{_BOUNDARY_SHAPE_HEADINGS[shape]}** (`{shape}`) — "
            f"{DECLARATION_SHAPE_DEFINITIONS[shape]}"
        )
    lines.extend(["", "### What each outcome means for a verdict", "", _BOUNDARY_CEILING_NOTE])
    for outcome, sentence in CELL_OUTCOME_VERDICTS.items():
        lines.append(
            f"- **{_BOUNDARY_OUTCOME_WORDS[outcome]}** (`{outcome}`) — {sentence}"
        )
    lines.extend(
        [
            "",
            "### Getting a route to ✅",
            "",
            _BOUNDARY_REMEDY,
            "### Which release this describes",
            "",
            (
                f"This page describes **agents-shipgate {matrix.generated_for_version}**, "
                "and its rows move as the adapters do — `schema_version` "
                f"(`{matrix.schema_version}`) versions the shape of the "
                "machine-readable companion, not the routes."
            ),
            "",
            (
                "If you arrived from a link in a stored report, check that "
                "version against the scanner that produced the report before "
                "trusting a row: a boundary is only a specification of the "
                "release it was generated from. Every released version is "
                "tagged, so the matrix your scanner implemented is at "
                "`https://github.com/ThreeMoonsLab/agents-shipgate/blob/v<your-version>/docs/determinism-boundary.md`."
            ),
            "",
            "### Scope",
            "",
            (
                "This page covers the built-in inputs of this distribution. A "
                "third-party adapter registered through the "
                "`agents_shipgate.adapters` entry point may coin any source type "
                "and any confidence, and nothing here speaks for what it proves."
            ),
            "",
            (
                "Repository coverage is not risk coverage. This page says what "
                "the scan can *read*; [`docs/checks.md`](checks.md) says what it "
                "checks once it has read it. The machine-readable companion is "
                "[`determinism-boundary.json`](determinism-boundary.json)."
            ),
            "",
        ]
    )
    return DOCS / "determinism-boundary.md", "\n".join(lines)


def write_determinism_boundary_page(
    *, check_only: bool = False, drift: list[str] | None = None
) -> bool:
    target, content = build_determinism_boundary_page()
    return _emit(target, content, check_only=check_only, drift=drift if drift is not None else [])


BUILDERS: tuple[tuple[str, Callable[[], tuple[Path, str]]], ...] = (
    ("manifest", build_manifest_schema),
    ("checks_catalog", build_checks_catalog),
    ("report", build_report_schema),
    ("policy_pack", build_policy_pack_schema),
    ("packet", build_packet_schema),
    ("verifier", build_verifier_schema),
    ("verify_run", build_verify_run_schema),
    ("verification_plan", build_verification_plan_schema),
    ("verification_unit_result", build_verification_unit_result_schema),
    ("verification_artifact_manifest", build_verification_artifact_manifest_schema),
    ("verification_receipt", build_verification_receipt_schema),
    ("current_control", build_current_control_schema),
    ("agent_control_envelope", build_agent_control_envelope_schema),
    ("human_authorization", build_human_authorization_schema),
    ("agent_handoff", build_agent_handoff_schema),
    ("agent_result", build_agent_result_schema),
    # codex_boundary_result v2 is a frozen compatibility schema and is not
    # regenerated with current package defaults.
    ("agent_boundary_result", build_agent_boundary_result_schema),
    ("preflight", build_preflight_schema),
    ("capability_lock", build_capability_lock_schema),
    ("capability_lock_diff", build_capability_lock_diff_schema),
    ("capability_payload", build_capability_payload_schema),
    ("capability_payload_state_example", build_capability_payload_state_example),
    ("capability_payload_delta_example", build_capability_payload_delta_example),
    ("capability_delta_attestation", build_capability_delta_attestation_schema),
    (
        "capability_delta_attestation_example",
        build_capability_delta_attestation_example,
    ),
    ("attestation", build_attestation_schema),
    ("org_governance", build_org_governance_schema),
    ("org_evidence_bundle", build_org_evidence_bundle_schema),
    ("registry", build_registry_schema),
    ("host_grants_inventory", build_host_grants_inventory_schema),
    ("host_grants_baseline", build_host_grants_baseline_schema),
    ("host_grants_drift", build_host_grants_drift_schema),
    ("governance_benchmark_catalog", build_governance_benchmark_catalog_schema),
    ("governance_benchmark_result", build_governance_benchmark_result_schema),
    ("determinism_boundary_matrix", build_determinism_boundary_matrix),
    ("determinism_boundary_page", build_determinism_boundary_page),
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
    (DOCS / "examples").mkdir(parents=True, exist_ok=True)
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
