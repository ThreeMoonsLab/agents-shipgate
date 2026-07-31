from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from agents_shipgate.core.capability_policy import match_policy_pack_subject
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.errors import ConfigError, InputParseError
from agents_shipgate.core.policy_evidence import finding_support, policy_evidence_gap
from agents_shipgate.core.static_inputs import read_static_input_bytes
from agents_shipgate.inputs.common import load_structured_file, resolve_input_path
from agents_shipgate.schemas.common import SourceReference
from agents_shipgate.schemas.manifest import AgentsShipgateManifest, PolicyPackConfig
from agents_shipgate.schemas.policy_pack import (
    PolicyPackFile,
    PolicyPackRule,
)
from agents_shipgate.schemas.report import (
    Finding,
    LoadedPolicyPack,
    PolicyApprovalRouting,
    PolicyRoutingMetadata,
)


@dataclass(frozen=True)
class ResolvedPolicyPackRule:
    pack: LoadedPolicyPack
    rule: PolicyPackRule


@dataclass(frozen=True)
class LoadedPolicyPacks:
    rules: list[ResolvedPolicyPackRule]
    loaded: list[LoadedPolicyPack]
    warnings: list[str]


def load_policy_packs(
    manifest: AgentsShipgateManifest,
    base_dir: Path,
    *,
    cli_policy_packs: list[Path] | None = None,
) -> LoadedPolicyPacks:
    configs = [*manifest.checks.policy_packs]
    configs.extend(
        PolicyPackConfig(path=str(path), id=None, optional=False)
        for path in cli_policy_packs or []
    )
    loaded: list[LoadedPolicyPack] = []
    resolved_rules: list[ResolvedPolicyPackRule] = []
    warnings: list[str] = []
    for config in configs:
        try:
            path = resolve_input_path(base_dir, config.path)
            sha256, sha256_status = _verify_pack_pin(path, config)
            data = load_structured_file(path)
            if not isinstance(data, dict):
                raise ConfigError(f"Policy pack must contain a YAML object: {config.path}")
            pack_file = PolicyPackFile.model_validate(data)
            _validate_policy_pack_team_refs(manifest, pack_file, config.path)
            pack_id = config.id or pack_file.id or path.stem
            display_path = _relative_display_path(path, base_dir)
            pack = LoadedPolicyPack(
                id=pack_id,
                name=pack_file.name or pack_id,
                version=pack_file.version,
                path=display_path,
                source=config.source,
                sha256=sha256,
                sha256_status=sha256_status,
                owner=pack_file.owner,
                rule_count=len(pack_file.rules),
            )
            loaded.append(pack)
            resolved_rules.extend(
                ResolvedPolicyPackRule(pack=pack, rule=rule) for rule in pack_file.rules
            )
        except (ConfigError, InputParseError, ValidationError) as exc:
            if config.optional:
                warnings.append(f"Optional policy pack {config.path!r} failed to load: {exc}")
                continue
            if isinstance(exc, ConfigError):
                raise
            raise ConfigError(f"Invalid policy pack {config.path!r}: {exc}") from exc
    _validate_rule_ids(resolved_rules)
    return LoadedPolicyPacks(rules=resolved_rules, loaded=loaded, warnings=warnings)


def run_policy_pack_rules(
    context: ScanContext,
    policy_packs: LoadedPolicyPacks,
) -> list[Finding]:
    findings: list[Finding] = []
    for resolved in policy_packs.rules:
        for subject in context.capability_policy_subjects:
            match = match_policy_pack_subject(
                subject,
                resolved.rule.match,
                environment_target=context.manifest.environment.target,
                base_evidence={
                    "policy_pack": resolved.pack.id,
                    "policy_pack_path": resolved.pack.path,
                    "policy_pack_source": resolved.pack.source,
                    "policy_pack_sha256": resolved.pack.sha256,
                    "policy_pack_sha256_status": resolved.pack.sha256_status,
                },
            )
            if match.status == "not_matched":
                continue
            rule = resolved.rule
            support = finding_support(
                match.support.predicates,
                requested_confidence=rule.confidence,
                status=match.status,
            )
            if match.status != "matched" or not support.policy_eligible:
                context.policy_evidence_gaps.append(
                    policy_evidence_gap(
                        status=match.status,
                        subject=f"{subject.tool.name} [{subject.tool.id}]",
                        policy_id=rule.id,
                        source_ref=resolved.pack.path,
                        support=support,
                        manifest_path=f"{resolved.pack.path}#rules/{rule.id}/match",
                    )
                )
                continue
            title = rule.title or rule.description or f"Policy pack rule {rule.id} matched"
            findings.append(
                Finding(
                    check_id=rule.id,
                    title=title,
                    severity=rule.severity,
                    category=rule.category,
                    tool_id=subject.tool.id,
                    tool_name=subject.tool.name,
                    agent_id=context.agent.id,
                    evidence=match.evidence,
                    confidence=support.confidence,
                    provenance_kind="policy_pack",
                    source=SourceReference(type="policy_pack", ref=resolved.pack.path),
                    capability_refs=[subject.fact.id],
                    capability_policy_evidence=match.capability_policy_evidence,
                    policy_routing=_policy_routing_metadata(resolved),
                    support=support,
                    recommendation=rule.recommendation,
                    blocks_release=rule.block and support.blocking_eligible,
                )
            )
    return findings


def _policy_routing_metadata(
    resolved: ResolvedPolicyPackRule,
) -> PolicyRoutingMetadata:
    approval = resolved.rule.approval
    return PolicyRoutingMetadata(
        owner=resolved.rule.owner or resolved.pack.owner,
        reviewers=list(resolved.rule.reviewers),
        approval=PolicyApprovalRouting(
            required=approval.required if approval is not None else False,
            teams=list(approval.teams) if approval is not None else [],
            min_approvals=approval.min_approvals if approval is not None else None,
            enforced=False,
        ),
    )


def _verify_pack_pin(
    path: Path, config: PolicyPackConfig
) -> tuple[str | None, str]:
    """v0.2: enforce the optional sha256 content pin on shared packs."""
    pinned = (config.sha256 or "").strip().lower()
    if not pinned:
        return None, "unpinned"
    try:
        digest = hashlib.sha256(read_static_input_bytes(path)).hexdigest()
    except (OSError, ValueError) as exc:
        raise ConfigError(
            f"Could not hash pinned policy pack {config.path!r}: {exc}"
        ) from exc
    if digest != pinned:
        raise ConfigError(
            f"Policy pack {config.path!r} does not match its pinned sha256. "
            f"Expected {pinned}, got {digest}. The pack content changed since "
            "it was pinned; re-review the pack and update the pin, or restore "
            "the pinned content."
        )
    return digest, "verified"


def _validate_policy_pack_team_refs(
    manifest: AgentsShipgateManifest,
    pack_file: PolicyPackFile,
    pack_path: str,
) -> None:
    org = getattr(manifest, "organization", None)
    if org is None or not org.teams:
        return
    known = set(org.teams)

    def check(ref: str | None, *, rule_id: str, field: str) -> None:
        if ref is not None and ref not in known:
            raise ConfigError(
                f"Policy pack {pack_path!r} rule {rule_id!r} references "
                f"unknown organization team {ref!r} in {field}; known teams: "
                f"{', '.join(sorted(known))}."
            )

    for rule in pack_file.rules:
        check(rule.owner, rule_id=rule.id, field="owner")
        for reviewer in rule.reviewers:
            check(reviewer, rule_id=rule.id, field="reviewers")
        if rule.approval is not None:
            for team in rule.approval.teams:
                check(team, rule_id=rule.id, field="approval.teams")


def _validate_rule_ids(rules: list[ResolvedPolicyPackRule]) -> None:
    seen: dict[str, str] = {}
    for resolved in rules:
        rule_id = resolved.rule.id
        if rule_id.startswith("SHIP-"):
            raise ConfigError(
                f"Policy pack rule id {rule_id!r} is reserved for built-in checks; "
                "use a non-SHIP namespace such as ORG-*."
            )
        previous = seen.get(rule_id)
        if previous:
            raise ConfigError(
                f"Duplicate policy pack rule id {rule_id!r} in {resolved.pack.path}; "
                f"already declared in {previous}."
            )
        seen[rule_id] = resolved.pack.path


def _relative_display_path(path: Path, base_dir: Path) -> str:
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()
