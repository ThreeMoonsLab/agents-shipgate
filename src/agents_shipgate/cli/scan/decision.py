from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agents_shipgate.checks.plugin_validation import strict_failure_messages
from agents_shipgate.checks.registry import check_catalog, run_checks
from agents_shipgate.core.capability_policy import build_capability_policy_subjects
from agents_shipgate.core.capability_traces import build_capability_runtime_evidence
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.dynamic_defaults import dynamic_check_defaults
from agents_shipgate.core.findings.identity import dedupe_findings, legacy_name_fingerprint
from agents_shipgate.core.findings.mutations import (
    apply_no_heuristics_filter,
    apply_severity_overrides,
    apply_suppressions,
)
from agents_shipgate.core.findings.remediation import annotate_remediation
from agents_shipgate.core.lenses.action_surface import (
    action_reference_from_scan_reference,
    build_action_surface_facts,
    compute_action_surface_diff,
    evaluate_action_surface_policies,
)
from agents_shipgate.core.policy_evidence import policy_evidence_gap
from agents_shipgate.core.severity_overrides import resolve_severity_overrides
from agents_shipgate.inputs.policy_packs import run_policy_pack_rules
from agents_shipgate.schemas.manifest import AgentsShipgateManifest
from agents_shipgate.schemas.verification import VerificationContext

from .models import _ChecksDecision, _DiffReferences, _LoadedInputs, _ToolsAndAgent
from .patching import _attach_patches, _check_metadata_lookup

logger = logging.getLogger(__name__)


def _run_checks_and_decide(
    *,
    manifest: AgentsShipgateManifest,
    manifest_positions: Any,
    config_path: Path,
    tools_and_agent: _ToolsAndAgent,
    inputs: _LoadedInputs,
    diffs: _DiffReferences,
    plugins_enabled: bool | None,
    suggest_patches: bool,
    no_heuristics: bool,
    verification_context: VerificationContext | None = None,
) -> _ChecksDecision:
    """Phase 5: build internal action-surface facts, run all checks
    (built-in + plugin + policy-pack + action-surface policies),
    resolve severity overrides via the dynamic-default aggregator,
    apply suppressions + optional patches, annotate remediation
    metadata, snapshot ``legacy_fingerprints`` for pre-v0.18 baseline
    compatibility.

    The INTERNAL ``action_surface_diff`` returned here is semantic
    only — provenance enrichment happens later on the PUBLIC diff
    derived from sanitized tools. Mutating ``reason`` here would leak
    ``path:line`` into ``Finding.evidence``, churning fingerprints.
    """
    action_surface_warnings: list[str] = []
    action_surface_facts = build_action_surface_facts(
        manifest,
        agent_id=tools_and_agent.agent.id,
        tools=tools_and_agent.tools,
        warnings=action_surface_warnings,
    )
    capability_facts, capability_policy_subjects = build_capability_policy_subjects(
        manifest,
        agent_id=tools_and_agent.agent.id,
        tools=tools_and_agent.tools,
        action_surface_facts=action_surface_facts,
        artifact_bag=inputs.artifact_bag,
    )
    action_reference = action_reference_from_scan_reference(diffs.diff_reference)
    action_surface_diff = compute_action_surface_diff(
        action_surface_facts,
        action_reference.facts if action_reference else None,
        reference=action_reference,
        # Fail-soft: duplicate action ids in an engine-generated base
        # reference (pre-collision-fix report/baseline) degrade to a
        # source_warning (-> review_required) instead of crashing the scan
        # with a config error. Same principle as the build-time sink above.
        warnings=action_surface_warnings,
    )
    if diffs.diff_reference_error:
        action_surface_diff.enabled = False
        action_surface_diff.notes = [diffs.diff_reference_error]
        # A requested diff that cannot be compared is evidence degradation,
        # not a cosmetic renderer note. Carry it through the existing source-
        # warning channel so release_decision exposes a human-routed
        # regeneration action and cannot silently emit ``passed``.
        action_surface_warnings.append(diffs.diff_reference_error)
    context = ScanContext(
        manifest=manifest,
        agent=tools_and_agent.agent,
        tools=tools_and_agent.tools,
        config_path=config_path.resolve(),
        tool_catalog=tools_and_agent.tool_catalog,
        binding_graph=tools_and_agent.binding_graph,
        framework_artifacts=inputs.artifact_bag,
        action_surface_facts=action_surface_facts,
        capability_facts=capability_facts,
        capability_policy_subjects=capability_policy_subjects,
        manifest_positions=manifest_positions,
        verification=verification_context,
        # M3: expose the --diff-from base report so the Tier B
        # SHIP-VERIFY-* weakening/expansion checks can compare base-vs-head
        # effective policy. None for plain scans (no base) — those checks
        # degrade safely and emit nothing.
        diff_reference=diffs.diff_reference,
        # HEAD-side toolkit scope bounds for the capability-scope check.
        toolkit_bounds=tools_and_agent.toolkit_bounds,
    )
    context.capability_runtime_evidence = build_capability_runtime_evidence(context)
    loaded_plugins: list[dict[str, str | None]] = []
    findings = run_checks(
        context,
        plugins_enabled=plugins_enabled,
        loaded_plugins=loaded_plugins,
        extra_known_check_ids={resolved.rule.id for resolved in inputs.policy_packs.rules},
    )
    action_surface_warnings.extend(
        f"Enabled check plugin failed validation or execution: {message}"
        for message in strict_failure_messages(loaded_plugins)
    )
    findings.extend(run_policy_pack_rules(context, inputs.policy_packs))
    findings.extend(
        evaluate_action_surface_policies(
            manifest,
            action_surface_facts,
            action_surface_diff,
            agent_id=tools_and_agent.agent.id,
            tools=tools_and_agent.tools,
            policy_evidence_gaps=context.policy_evidence_gaps,
        )
    )
    findings = dedupe_findings(findings)
    policy_eligible_findings = []
    for finding in findings:
        support = finding.support
        if support is None or support.policy_eligible:
            policy_eligible_findings.append(finding)
            continue
        source = finding.policy_evidence_source or finding.source
        context.policy_evidence_gaps.append(
            policy_evidence_gap(
                status=support.status,
                subject=finding.tool_id or finding.tool_name or finding.agent_id or finding.check_id,
                policy_id=finding.check_id,
                source_ref=(source.path or source.ref) if source is not None else None,
                support=support,
                manifest_path=(
                    source.pointer
                    if source is not None and source.pointer
                    else f"/checks/{finding.check_id}"
                ),
            )
        )
    findings = policy_eligible_findings
    # v0.17 (M1) + v0.18 (PR #1): centralized aggregator covers every
    # catalog check with ``dynamic_default=True``. See
    # ``core/dynamic_defaults.py`` and ``severity_overrides.py`` for the
    # tier-crossing / floor-enforcement contract.
    catalog = check_catalog(plugins_enabled=plugins_enabled)
    effective_dynamic_defaults = dynamic_check_defaults(
        manifest, inputs.policy_packs, catalog=catalog
    )
    override_resolution = resolve_severity_overrides(
        overrides=manifest.severity_override_entries(),
        acknowledgements=manifest.acknowledge_overrides(),
        catalog=catalog,
        extra_known_check_defaults=effective_dynamic_defaults,
    )
    apply_severity_overrides(findings, override_resolution.override_by_check_id)
    apply_suppressions(findings, manifest.checks.ignore, tools_and_agent.tools)
    heuristics_filter = apply_no_heuristics_filter(
        findings,
        enabled=no_heuristics,
    )
    if suggest_patches:
        _attach_patches(
            findings,
            manifest,
            config_path,
            plugins_enabled=plugins_enabled,
        )
    # v0.7: annotate every finding (regardless of --suggest-patches) with
    # the four remediation fields. When patches are present they're
    # derived from those; otherwise the per-check CheckMetadata seeds
    # the values.
    annotate_remediation(
        findings,
        _check_metadata_lookup(plugins_enabled=plugins_enabled),
    )
    legacy_fingerprints = [legacy_name_fingerprint(finding) for finding in findings]
    logger.debug(
        "checks completed",
        extra={
            "agents_shipgate_finding_count": len(findings),
            "agents_shipgate_suppressed_count": sum(
                1 for finding in findings if finding.suppressed
            ),
        },
    )
    return _ChecksDecision(
        action_surface_facts=action_surface_facts,
        action_surface_diff=action_surface_diff,
        action_surface_warnings=action_surface_warnings,
        findings=findings,
        legacy_fingerprints=legacy_fingerprints,
        override_resolution=override_resolution,
        heuristics_filter=heuristics_filter,
        loaded_plugins=loaded_plugins,
        context=context,
    )
