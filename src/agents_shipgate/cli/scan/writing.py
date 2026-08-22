from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agents_shipgate.ci.release_decision import (
    SUGGESTED_DECLARATIONS_FILENAME,
    SUGGESTED_INVENTORY_FILENAME,
    inventory_manifest_key,
    unresolved_symbol_names,
    unresolved_symbol_sources,
)
from agents_shipgate.cli._artifact_lifecycle import clear_verifier_route_artifacts
from agents_shipgate.core.current_control import (
    SCAN_FORMAT_ARTIFACT_KEYS,
    begin_current_control,
    current_control_lifecycle_owner,
    publish_current_control,
)
from agents_shipgate.core.domain import Tool
from agents_shipgate.core.evidence_actions import yaml_scalar
from agents_shipgate.core.privacy import sanitize_packet
from agents_shipgate.packet.builder import build_packet
from agents_shipgate.schemas.current_control import (
    AgentActionRequiredCurrentControl,
    CurrentControlWorkspaceIdentity,
)
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.report import ReadinessReport

from .declarations import scaffold_for_report
from .models import _OutputPlan, _SanitizedSurfaces
from .output_helpers import _write_packet, _write_reports


def _write_outputs(
    *,
    report: ReadinessReport,
    public_report_payload: Any,
    sanitized: _SanitizedSurfaces,
    plan: _OutputPlan,
    manifest: AgentsShipgateManifest,
    config_path: Path,
    packet_generated_at: str | None,
) -> None:
    """Phase 9: write report (md/json/sarif) + packet (md/json/html/pdf).

    Both writes consume only sanitized values; the raw manifest is
    passed to ``build_packet`` for non-output internal use (packet
    builder reads manifest defaults like ``output.packet.formats`` but
    never serializes raw manifest content into the packet).
    """
    public_report = ReadinessReport.model_validate(public_report_payload)
    # A standalone scan supersedes the report set in this directory.  Verify
    # also calls run_scan internally, but writes its fresh route/identity
    # artifacts only after this phase completes.
    #
    # ``owns_current_control`` is what tells the two apart.  A supporting scan
    # inside verify or preview must not take over the control identity for the
    # PR: the enclosing command already invalidated the pointer and will
    # publish the terminal one itself.
    owns_control = current_control_lifecycle_owner() is None
    if owns_control:
        begin_current_control(
            plan.out_dir,
            operation="scan",
            reason=(
                "A scan is in progress; no decision in this directory is "
                "current until it publishes one."
            ),
        )
    clear_verifier_route_artifacts(plan.out_dir)
    _write_reports(
        public_report,
        plan.generated_paths,
        manifest.output.formats,
        sanitized_payload=public_report_payload,
    )
    _write_suggested_inventory(
        sanitized_tools=sanitized.tools,
        report=public_report,
        generated_paths=plan.generated_paths,
    )
    _write_suggested_declarations(
        report=public_report,
        generated_paths=plan.generated_paths,
    )
    if manifest.output.packet.enabled and plan.packet_format_set:
        assert report.release_decision is not None
        packet = build_packet(
            manifest=manifest,
            agent=public_report.agent,
            project=public_report.project,
            environment=public_report.environment,
            run_id=public_report.run_id,
            tools=sanitized.tools,
            findings=sanitized.findings,
            release_decision=public_report.release_decision,
            api_artifacts=sanitized.api_artifacts,
            anthropic_artifacts=sanitized.anthropic_artifacts,
            source_warnings=sanitized.source_warnings,
            validation_artifacts=sanitized.validation_artifacts,
            tool_surface_diff=public_report.tool_surface_diff,
            action_surface_diff=public_report.action_surface_diff,
            report_payload=public_report_payload,
            capability_runtime_evidence=public_report.capability_runtime_evidence,
            generated_at=packet_generated_at,
            config_ref=config_path.resolve().name,
        )
        _write_packet(
            sanitize_packet(packet),
            plan.generated_paths,
            plan.packet_format_set,
        )
    if owns_control:
        # A scan inventories a workspace; it never establishes merge authority.
        # The pointer says so in the one place a coding agent is required to
        # look, so a verifier route from an earlier run cannot be mistaken for
        # the current permission to finish.  A scan builds no verification
        # plan, so the only identity it can bind is the manifest it read.
        #
        # The binding is restricted to the formats this scan actually wrote.
        # `scan --format markdown` after a verify leaves that verifier's
        # `report.json` in place, and binding it would present the previous
        # run's JSON report as part of the current set.
        publish_current_control(
            plan.out_dir,
            operation="scan",
            control=AgentActionRequiredCurrentControl(
                state="agent_action_required",
                reason=(
                    "A standalone scan produced the current report set. A scan "
                    "does not authorize completion or merge; run "
                    "`agents-shipgate verify` to obtain a merge decision."
                ),
            ),
            workspace_identity=CurrentControlWorkspaceIdentity(
                policy_snapshot_sha256=_manifest_snapshot_sha256(config_path),
            ),
            artifact_keys={
                SCAN_FORMAT_ARTIFACT_KEYS[name]
                for name in plan.generated_paths
                if name in SCAN_FORMAT_ARTIFACT_KEYS
            },
        )


def _manifest_snapshot_sha256(config_path: Path) -> str | None:
    try:
        data = config_path.read_bytes()
    except OSError:
        return None
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _write_suggested_declarations(
    *,
    report: ReadinessReport,
    generated_paths: dict[str, Path],
) -> None:
    """Advisory scaffold for the gaps only a human declaration can close.

    Written next to report.json whenever any evidence gap carries a
    ``declaration_template``. A pure projection of the decision engine's own
    output: it asserts nothing, decides nothing, and every human-owned value
    stays ``<REVIEW_REQUIRED>``. Consumes the sanitized public report so no
    redacted content reaches the scaffold.
    """

    anchor = generated_paths.get("json") or next(iter(generated_paths.values()), None)
    if anchor is None:
        return
    out_path = anchor.parent / SUGGESTED_DECLARATIONS_FILENAME
    scaffold = scaffold_for_report(report)
    if scaffold is None:
        # Nothing is owed any more: a leftover scaffold from an earlier run
        # would keep asking for declarations that are already made.
        _remove_if_present(out_path)
        return
    out_path.write_text(scaffold, encoding="utf-8")


def _remove_if_present(path: Path) -> None:
    """Drop a superseded advisory artifact, tolerating a read-only directory."""

    if not path.is_file():
        return
    try:
        path.unlink()
    except OSError:
        pass


def _inventory_reference_note(bindable: list[tuple[str, str]]) -> str:
    """How to reference this skeleton, for the sources that produced it.

    Only the framework blocks in ``_INVENTORY_MANIFEST_KEYS`` have a
    ``tool_inventories`` key, so the note must not prescribe one — nor the
    ``source_id`` on it — for a source that has none: the reader would go
    looking for a field the schema does not accept. Sources that do have one
    get the exact entry to write, because an inventory referenced without
    ``source_id`` is added beside the extracted tools rather than completing
    them, leaving the gap that asked for the file open (#386).

    An inventory entry completes ONE source, so a skeleton spanning several
    says to split it rather than letting the reader find that out from an
    ambiguity warning two runs later.

    ``bindable`` is the ``(manifest key, source_id)`` pairs the skeleton's
    entries came from — the low-confidence tools' own sources, plus any source
    whose agent named symbols that produced no observation at all (#361).
    """

    if not bindable:
        return (
            "declare it for this source — as a `tool_sources[]` entry of type "
            "`mcp`, or from the matching tool_inventories key where the "
            "framework block has one"
        )
    if len(bindable) == 1:
        key, source_id = bindable[0]
        return (
            f"reference it from `{key}` in shipgate.yaml as "
            f"`- {{path: <saved file>, source_id: {yaml_scalar(source_id)}}}` — "
            "without `source_id` the inventory is an independent source, added "
            "beside the extracted tools instead of completing them, and the gap "
            "that asked for it stays open"
        )
    pairs = ", ".join(
        f"{key} -> source_id: {yaml_scalar(source_id)}" for key, source_id in bindable
    )
    return (
        "split it per source, then reference each file from the matching "
        "tool_inventories key in shipgate.yaml with the `source_id` of the "
        f"source it completes ({pairs}) — without `source_id` an inventory is "
        "an independent source, added beside the extracted tools instead of "
        "completing them, and the gap that asked for it stays open"
    )


def _write_suggested_inventory(
    *,
    sanitized_tools: list[Tool],
    report: ReadinessReport,
    generated_paths: dict[str, Path],
) -> None:
    """v0.26: advisory tool-inventory skeleton for under-enumerated sources.

    Written next to report.json whenever low-confidence tools exist — or an
    agent references tool symbols that produced no observation at all — in
    the MCP-export shape every ``tool_inventories`` manifest key loads
    (``load_mcp_tools`` ignores the top-level ``note``). The
    ``evidence_gaps`` rows in ``release_decision.evidence_coverage``
    reference this file by name. Consumes only sanitized tools so no
    redacted content can leak into the skeleton. Deterministic: observed
    tools by (name, source_type), then unresolved symbols by name, stable
    JSON.
    """
    anchor = generated_paths.get("json") or next(
        iter(generated_paths.values()), None
    )
    if anchor is None:
        return
    low_confidence = sorted(
        (tool for tool in sanitized_tools if tool.extraction_confidence != "high"),
        key=lambda tool: (tool.name, tool.source_type),
    )
    # The repository whose first scan is hardest to move is the one where
    # *every* tool symbol is imported: extraction yields nothing, so there are
    # no low-confidence tools, so no skeleton was written and the names the
    # agent's ``tools=[...]`` list already published were left to be retyped by
    # hand (#361). A symbol is a *name to review*, never an observation: it may
    # name one tool or a toolset exposing many, and its schema is unknown
    # either way — the entry below says so, and nothing here enters the catalog.
    unresolved = unresolved_symbol_names(report)
    if not low_confidence and not unresolved:
        # Nothing is owed any more — the same rule the declaration scaffold
        # follows. A skeleton left behind from the run before the inventory was
        # declared reads as a fresh instruction to declare it again.
        _remove_if_present(anchor.parent / SUGGESTED_INVENTORY_FILENAME)
        return
    entries = []
    for tool in low_confidence:
        entry: dict[str, Any] = {
            "name": tool.name,
            "description": tool.description
            or "TODO: describe what this tool does and what it can touch.",
        }
        if tool.input_schema:
            entry["input_schema"] = tool.input_schema
        entries.append(entry)
    entries.extend(
        {
            "name": symbol,
            "description": (
                "TODO: static analysis could not resolve this symbol, so this "
                "name is the one the agent's tools=[...] list uses — confirm "
                "it is the tool's real name, and split the entry if the "
                "symbol is a toolset exposing several tools."
            ),
        }
        for symbol in unresolved
    )
    bindable = sorted(
        {
            (key, tool.source_id)
            for tool in low_confidence
            if tool.source_id
            and (key := inventory_manifest_key(tool.source_type)) is not None
        }
        | {
            (source.manifest_key, source.source_id)
            for source in unresolved_symbol_sources(report)
        }
    )
    payload = {
        "note": (
            "Skeleton tool inventory generated by agents-shipgate scan for "
            "sources static extraction could not fully enumerate. Review and "
            "complete each entry, save the file in your repo, and "
            f"{_inventory_reference_note(bindable)} (see "
            "release_decision.evidence_coverage.evidence_gaps)."
        ),
        "tools": entries,
    }
    out_path = anchor.parent / SUGGESTED_INVENTORY_FILENAME
    out_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
