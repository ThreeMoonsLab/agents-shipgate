"""Regenerate the JSON-Schema and check-catalog files under docs/.

Run from the repo root:

    python scripts/generate_schemas.py

Writes:
- docs/manifest-v0.1.json       (from agents_shipgate.config.schema)
- docs/checks.json              (from agents-shipgate list-checks --json)
- docs/report-schema.v0.<minor>.json
                                (from agents_shipgate.core.models.ReadinessReport;
                                 minor derived from report_schema_version default)

CI calls this script and asserts the working tree is clean afterward, so
out-of-date generated files fail the build — drift protection for any
field changes on Finding (e.g., patches) or ReadinessReport
(e.g., manifest_dir).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"
SRC = REPO_ROOT / "src"

# Allow `python scripts/generate_schemas.py` from a checkout without install.
sys.path.insert(0, str(SRC))


def write_manifest_schema() -> None:
    from agents_shipgate.config.schema import AgentsShipgateManifest

    schema = AgentsShipgateManifest.model_json_schema()
    schema["$id"] = (
        "https://raw.githubusercontent.com/ThreeMoonsLab/agents-shipgate/"
        "main/docs/manifest-v0.1.json"
    )
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Agents Shipgate Manifest v0.1"
    schema["description"] = (
        "JSON Schema for shipgate.yaml. Generated from "
        "agents_shipgate.config.schema.AgentsShipgateManifest. Do not edit by hand."
    )
    target = DOCS / "manifest-v0.1.json"
    target.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {target.relative_to(REPO_ROOT)}")


def write_report_schema() -> None:
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
    from agents_shipgate.core.models import ReadinessReport

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
        "Generated from agents_shipgate.core.models.ReadinessReport with "
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
            "frameworks",
            "findings",
            "recommended_actions",
            "generated_reports",
            "loaded_policy_packs",
            "loaded_plugins",
            "tool_inventory",
            "source_warnings",
            # v0.12: agent_summary is the deterministic top-level
            # summary block. The Pydantic model marks it Optional so
            # older test helpers can construct minimal reports — but
            # every emitted report runs build_report() which always
            # populates it. Mark required at the schema level so a
            # payload missing the field fails validation.
            "agent_summary",
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

    # Preserve nested v0.5 required lists. Pydantic auto-generation marks
    # only fields without defaults as required, but consumers depend on
    # several optional-with-default fields being present in every report.
    # Optional v0.6 additions (Finding.patches) intentionally stay
    # optional — additive only.
    defs = schema.setdefault("$defs", {})
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
                "suppressed",
                "baseline_status",
                # v0.12: deterministic projection field. Optional in
                # Python (so test helpers can construct minimal Findings)
                # but required + non-nullable on the wire — every
                # emitted report runs annotate_remediation which sets it.
                "agent_action",
            ]
        )
        # v0.12: tighten agent_action to the inline enum shape (no
        # null). Pydantic emits `anyOf: [{enum, type: string}, null]`
        # for the Optional model field; on the wire we promise the
        # field is always a real enum value. AgentAction is a Literal,
        # not a BaseModel, so we cannot $ref it — inline the enum.
        finding_props = defs["Finding"].setdefault("properties", {})
        from typing import get_args as _get_args

        from agents_shipgate.core.models import AgentAction as _AgentAction

        if "agent_action" in finding_props:
            finding_props["agent_action"] = {
                "type": "string",
                "enum": list(_get_args(_AgentAction)),
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
    if "LoadedPolicyPack" in defs:
        defs["LoadedPolicyPack"]["required"] = sorted(
            ["id", "name", "path", "rule_count"]
        )

    # v0.8 release_decision: pin required keys so consumers can rely on
    # the full block being present (Pydantic only marks fields without
    # defaults as required, but our consumers depend on the whole shape).
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
            ]
        )
    if "ReleaseDecisionItem" in defs:
        # Pin the full v0.8 contract documented in STABILITY.md. `id`,
        # `fingerprint`, and `baseline_status` are nullable in the model
        # but every emitted item carries them — requiring the key to be
        # present (value may be null) lets agent/CI consumers rely on
        # the documented shape without conditional key checks.
        defs["ReleaseDecisionItem"]["required"] = sorted(
            ["id", "fingerprint", "check_id", "severity", "title", "baseline_status"]
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
            "required": sorted(
                ["name", "value", "distribution", "version", "check_id"]
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
    target.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {target.relative_to(REPO_ROOT)}")


def write_checks_catalog() -> None:
    from agents_shipgate.checks.registry import check_catalog

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
        "checks": [check.model_dump(mode="json") for check in check_catalog()],
    }
    target = DOCS / "checks.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {target.relative_to(REPO_ROOT)}")


def write_packet_schema() -> None:
    """Generate docs/packet-schema.v0.<minor>.json from EvidencePacket.

    Versioned independently from the report schema; bumping requires a
    single change to ``EvidencePacket.packet_schema_version`` and a
    rerun of this script. CI's clean-tree assertion catches drift.
    """

    from agents_shipgate.packet.models import EvidencePacket

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
        "agents_shipgate.packet.models.EvidencePacket. Do not edit by hand."
    )
    target = DOCS / f"packet-schema.v{minor}.json"
    target.write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Wrote {target.relative_to(REPO_ROOT)}")


def main() -> int:
    DOCS.mkdir(parents=True, exist_ok=True)
    write_manifest_schema()
    write_checks_catalog()
    write_report_schema()
    write_packet_schema()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
