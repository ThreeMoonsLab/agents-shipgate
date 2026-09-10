"""Join a source operation to the predicate that actually emitted a finding.

No serialized report can create ReconstructedOperationBase. Only the verifier's
fresh archived scan supplies it; this distinction is never a public Boolean.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass

from agents_shipgate.config.loader import load_manifest_text
from agents_shipgate.core.capability_policy import subject_requires_approval_review
from agents_shipgate.core.control_packs import resolve_control_pack
from agents_shipgate.core.errors import AgentsShipgateError
from agents_shipgate.core.static_inputs import active_static_input_snapshot, read_static_input_text
from agents_shipgate.core.tool_identity import ToolSelectorIndex
from agents_shipgate.inputs.common import MAX_INPUT_FILE_BYTES
from agents_shipgate.inputs.openapi_operation_contract import unambiguous_document
from agents_shipgate.schemas.operation_attribution import (
    OperationAttribution,
    OperationComparison,
    OperationInput,
    OperationPredicate,
)


@dataclass(frozen=True)
class ReconstructedOperationBase:
    tree: str
    rows: tuple[OperationAttribution, ...]


def build_operation_attributions(*, context, sources, findings, config_path):
    operations = [row for source in sources for row in source.operation_evidence]
    if not operations:
        return []
    members = defaultdict(list)
    for subject in context.capability_policy_subjects:
        for observation in subject.tool.observation_ids:
            members[observation].append(subject)
    manifest_input = None
    try:
        text = read_static_input_text(config_path, max_bytes=MAX_INPUT_FILE_BYTES)
        parsed = load_manifest_text(text, source=config_path)
        # Compare every declaration, ignoring only invocation/presentation fields.
        excluded = {"ci", "output"}
        if unambiguous_document(text) and parsed.model_dump(
            exclude=excluded
        ) == context.manifest.model_dump(exclude=excluded):
            manifest_input = OperationInput(
                path=config_path.name,
                sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                role="manifest",
            )
            snapshot = active_static_input_snapshot()
            if snapshot is not None and snapshot.contains(config_path):
                snapshot.mark_dependency_input(config_path)
    except (AgentsShipgateError, OSError, UnicodeError):
        pass
    pack = resolve_control_pack(context.manifest)
    selectors = ToolSelectorIndex.build(context.tools)
    result = []
    for operation in operations:
        operation = operation.model_copy(deep=True)
        row = OperationAttribution(
            operation=operation, status="unresolved", reason=operation.reason
        )
        result.append(row)
        candidates = members[operation.observation_id]
        if len(candidates) != 1 or len(candidates[0].tool.observation_ids) != 1:
            row.status, row.reason = "ambiguous", "canonical_subject_not_unique"
            continue
        subject = candidates[0]
        row.tool_id, row.capability_id = subject.tool.id, subject.fact.id
        if manifest_input is not None:
            operation.inputs.append(manifest_input)
        if operation.status != "observed":
            continue
        if (
            manifest_input is None
            or len(context.manifest.tool_sources) != 1
            or len(sources) != 1
            or any(source.type != "openapi" for source in context.manifest.tool_sources)
            or any(
                getattr(context.manifest, family) is not None
                for family in (
                    "openai_api",
                    "anthropic",
                    "google_adk",
                    "langchain",
                    "crewai",
                    "codex_plugins",
                    "n8n",
                    "validation",
                )
            )
            or getattr(context.manifest.agent.sdk, "entrypoint", None)
        ):
            row.reason = "policy_dependency_not_closed_by_profile"
            continue
        assessment = subject.tool.semantic_assessment
        if (
            assessment is None
            or assessment.effect.issues
            or assessment.authority.issues
            or assessment.identity.issues
            or assessment.binding.issues
        ):
            row.reason = "semantic_evidence_unresolved_or_conflicting"
            continue
        row.binding_claim_ids = [claim.claim_id for claim in assessment.binding.claims]
        row.declared_binding_path = list(assessment.binding.reachable_path)
        claims = [
            claim
            for claim in assessment.effect.claims
            if claim.policy_eligible
            and claim.value == "destructive"
            and claim.source == "openapi_method"
            and claim.source_pointer == operation.source_pointer
        ]
        if len(claims) != 1 or subject.tool.name != operation.tool_name:
            row.reason = "structural_effect_claim_or_operation_identity_unresolved"
            continue
        missing = subject_requires_approval_review(subject, pack=pack)
        matched = [
            finding
            for finding in findings
            if finding.check_id == row.check_id and finding.capability_refs == [subject.fact.id]
        ]
        if (missing and len(matched) != 1) or (not missing and matched):
            row.reason = "finding_predicate_join_unresolved"
            continue
        if missing:
            evidence = matched[0].capability_policy_evidence
            if (
                evidence is None
                or evidence.capability_id != subject.fact.id
                or evidence.matched_predicates.get("missing_approval_policy") is not True
            ):
                row.reason = "finding_predicate_join_unresolved"
                continue
        row.finding_fingerprints = [finding.fingerprint for finding in matched]
        row.control_pack_identity = json.dumps(
            pack.run_identity(), sort_keys=True, separators=(",", ":")
        )
        row.missing_approval = missing
        approval_pointers = ["/policies/require_approval_for_tools"]
        for index, declaration in enumerate(context.manifest.action_surface.actions):
            resolution = selectors.resolve(declaration)
            if (
                resolution.resolved
                and resolution.matches[0].id == subject.tool.id
                and declaration.approval is not None
            ):
                approval_pointers.append(f"/action_surface/actions/{index}/approval")
        row.predicates = [
            OperationPredicate(
                predicate="effect",
                observed="destructive",
                claim_ids=[claims[0].claim_id],
                source_pointer=operation.source_pointer,
            ),
            OperationPredicate(
                predicate="approval_obligation",
                observed=str("approval.required" in pack.obligations_for("destructive")).lower(),
                source_pointer="/policies/control_pack",
            ),
            OperationPredicate(
                predicate="missing_approval_policy",
                observed=str(not subject.effective_approval_required).lower(),
                source_pointer="/policies/require_approval_for_tools",
                contributing_pointers=approval_pointers,
            ),
        ]
        row.status, row.reason = (
            "observed",
            "declared_operation_joined_to_existing_approval_predicate",
        )
    return sorted(
        result, key=lambda row: (row.operation.observation_id, row.operation.source_pointer)
    )


def compare_operations(current, base: ReconstructedOperationBase | None):
    before, after = defaultdict(list), defaultdict(list)
    for row in base.rows if base else ():
        before[row.operation.observation_id].append(row)
    for row in current:
        after[row.operation.observation_id].append(row)
    result = []
    for observation in sorted(before.keys() | after.keys()):
        old, new = before[observation], after[observation]
        row = OperationComparison(
            observation_id=observation,
            before=old[0] if len(old) == 1 else None,
            after=new[0] if len(new) == 1 else None,
            reason="reconstructed_base_unavailable",
        )
        result.append(row)
        if base is None:
            continue
        row.evidence_source, row.base_tree = "reconstructed_git_base", base.tree
        if (
            len(old) > 1
            or len(new) > 1
            or any(
                item.status != "observed" or item.operation.status != "observed"
                for item in [*old, *new]
            )
        ):
            row.reason = "unresolved_or_ambiguous_operation_evidence"
            continue
        if not old or not new:
            row.reason = "operation_absence_not_proven_by_reader_census"
            continue
        left, right = old[0], new[0]
        if [(item.role, item.path) for item in left.operation.inputs] != [
            (item.role, item.path) for item in right.operation.inputs
        ]:
            row.reason = "operation_dependency_identity_changed"
            continue
        if (
            left.tool_id,
            left.capability_id,
            left.operation.source_id,
            left.operation.tool_name,
            left.operation.reader_profile,
            left.operation.source_pointer,
        ) != (
            right.tool_id,
            right.capability_id,
            right.operation.source_id,
            right.operation.tool_name,
            right.operation.reader_profile,
            right.operation.source_pointer,
        ):
            row.reason = "operation_or_capability_identity_changed"
            continue
        if (
            left.operation.security_contract != right.operation.security_contract
            or left.control_pack_identity != right.control_pack_identity
        ):
            row.reason = "security_or_obligation_configuration_changed"
            continue
        if (
            left.binding_claim_ids != right.binding_claim_ids
            or left.declared_binding_path != right.declared_binding_path
        ):
            row.reason = "declared_binding_evidence_changed_reachability_unresolved"
            continue
        previous, following = (
            set(left.operation.declared_targets),
            set(right.operation.declared_targets),
        )
        row.declared_target_domain = (
            "unchanged"
            if previous == following
            else "narrowed"
            if following < previous
            else "widened"
            if previous < following
            else "changed"
        )
        row.approval_predicate = (
            "standing_weakness"
            if left.missing_approval and right.missing_approval
            else "still_declared"
            if not left.missing_approval and not right.missing_approval
            else "resolved_by_declaration"
            if left.missing_approval
            else "newly_missing"
        )
        row.reason = "declared_target_domain_and_approval_predicate_compared_separately"
    return result


def invalidate_redacted_operations(original, public):
    for old, new in zip(original, public, strict=True):
        if old != new:
            new.status, new.reason = "redacted", "operation_predicate_evidence_redacted"
            new.predicates = []
            new.missing_approval = None
            new.control_pack_identity = None
            new.operation.status = "redacted"
            new.operation.declared_targets = []
            new.operation.security_contract = None
            new.operation.inputs = []
