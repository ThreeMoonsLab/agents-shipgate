from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agents_shipgate.config.schema import AgentsShipgateManifest, PolicyPackConfig
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.errors import ConfigError, InputParseError
from agents_shipgate.core.models import (
    Confidence,
    Finding,
    LoadedPolicyPack,
    Severity,
    SourceReference,
    Tool,
    ToolParameter,
)
from agents_shipgate.core.risk_hints import risk_tags
from agents_shipgate.inputs.common import load_structured_file, resolve_input_path


class PolicyPackParameterMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    names: list[str] = Field(default_factory=list)
    types: list[str] = Field(default_factory=list)
    missing_maximum: bool | None = None
    required: bool | None = None


class PolicyPackMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_tags: list[str] = Field(default_factory=list)
    source_types: list[str] = Field(default_factory=list)
    environment_targets: list[str] = Field(default_factory=list)
    missing_owner: bool | None = None
    missing_auth_scopes: bool | None = None
    missing_approval_policy: bool | None = None
    missing_confirmation_policy: bool | None = None
    missing_idempotency_policy: bool | None = None
    parameters: list[PolicyPackParameterMatch] = Field(default_factory=list)


class PolicyPackRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str | None = None
    description: str | None = None
    category: str = "policy_pack"
    severity: Severity
    confidence: Confidence = "medium"
    recommendation: str
    match: PolicyPackMatch


class PolicyPackFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    name: str | None = None
    version: str | None = None
    rules: list[PolicyPackRule]


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
            data = load_structured_file(path)
            if not isinstance(data, dict):
                raise ConfigError(f"Policy pack must contain a YAML object: {config.path}")
            pack_file = PolicyPackFile.model_validate(data)
            pack_id = config.id or pack_file.id or path.stem
            display_path = _relative_display_path(path, base_dir)
            pack = LoadedPolicyPack(
                id=pack_id,
                name=pack_file.name or pack_id,
                version=pack_file.version,
                path=display_path,
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
        for tool in context.tools:
            evidence = _match_evidence(tool, context, resolved)
            if evidence is None:
                continue
            rule = resolved.rule
            title = rule.title or rule.description or f"Policy pack rule {rule.id} matched"
            findings.append(
                Finding(
                    check_id=rule.id,
                    title=title,
                    severity=rule.severity,
                    category=rule.category,
                    tool_id=tool.id,
                    tool_name=tool.name,
                    agent_id=context.agent.id,
                    evidence=evidence,
                    confidence=rule.confidence,
                    source=SourceReference(type="policy_pack", ref=resolved.pack.path),
                    recommendation=rule.recommendation,
                )
            )
    return findings


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


def _match_evidence(
    tool: Tool,
    context: ScanContext,
    resolved: ResolvedPolicyPackRule,
) -> dict[str, Any] | None:
    rule_match = resolved.rule.match
    evidence: dict[str, Any] = {
        "policy_pack": resolved.pack.id,
        "policy_pack_path": resolved.pack.path,
    }
    tags = risk_tags(tool, min_confidence="medium")
    if rule_match.risk_tags:
        matched_tags = sorted(set(tags).intersection(rule_match.risk_tags))
        if not matched_tags:
            return None
        evidence["risk_tags"] = matched_tags
    if rule_match.source_types:
        if tool.source_type not in rule_match.source_types:
            return None
        evidence["source_type"] = tool.source_type
    if rule_match.environment_targets:
        target = context.manifest.environment.target
        if target not in rule_match.environment_targets:
            return None
        evidence["environment_target"] = target
    if rule_match.missing_owner is not None:
        missing = not bool(tool.owner)
        if missing is not rule_match.missing_owner:
            return None
        evidence["missing_owner"] = missing
    if rule_match.missing_auth_scopes is not None:
        missing = not bool(tool.auth.scopes)
        if missing is not rule_match.missing_auth_scopes:
            return None
        evidence["missing_auth_scopes"] = missing
    if rule_match.missing_approval_policy is not None:
        missing = tool.name not in _approval_tools(context)
        if missing is not rule_match.missing_approval_policy:
            return None
        evidence["missing_approval_policy"] = missing
    if rule_match.missing_confirmation_policy is not None:
        missing = tool.name not in _confirmation_tools(context)
        if missing is not rule_match.missing_confirmation_policy:
            return None
        evidence["missing_confirmation_policy"] = missing
    if rule_match.missing_idempotency_policy is not None:
        missing = not _has_idempotency_evidence(tool, context)
        if missing is not rule_match.missing_idempotency_policy:
            return None
        evidence["missing_idempotency_policy"] = missing
    if rule_match.parameters:
        matched_parameters = _matched_parameters(tool.parameters, rule_match.parameters)
        if len(matched_parameters) != len(rule_match.parameters):
            return None
        evidence["parameters"] = matched_parameters
    return evidence


def _matched_parameters(
    parameters: list[ToolParameter],
    predicates: list[PolicyPackParameterMatch],
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for predicate in predicates:
        matched = next(
            (parameter for parameter in parameters if _parameter_matches(parameter, predicate)),
            None,
        )
        if matched is None:
            continue
        matches.append(
            {
                "name": matched.name,
                "type": matched.type,
                "required": matched.required,
                "maximum": matched.maximum,
            }
        )
    return matches


def _parameter_matches(
    parameter: ToolParameter,
    predicate: PolicyPackParameterMatch,
) -> bool:
    names = set(predicate.names)
    if predicate.name:
        names.add(predicate.name)
    if names and parameter.name not in names:
        return False
    if predicate.types and parameter.type not in predicate.types:
        return False
    if predicate.missing_maximum is not None:
        missing = parameter.maximum is None
        if missing is not predicate.missing_maximum:
            return False
    if predicate.required is not None and parameter.required is not predicate.required:
        return False
    return True


def _approval_tools(context: ScanContext) -> set[str]:
    tools = context.manifest.policies.approval_tools()
    if context.api_artifacts:
        tools |= context.api_artifacts.approval_tools()
    return tools


def _confirmation_tools(context: ScanContext) -> set[str]:
    tools = context.manifest.policies.confirmation_tools()
    if context.api_artifacts:
        tools |= context.api_artifacts.confirmation_tools()
    return tools


def _idempotency_tools(context: ScanContext) -> set[str]:
    tools = context.manifest.policies.idempotency_tools()
    if context.api_artifacts:
        tools |= context.api_artifacts.idempotency_tools()
    return tools


def _has_idempotency_evidence(tool: Tool, context: ScanContext) -> bool:
    if tool.name in _idempotency_tools(context):
        return True
    if tool.annotations.get("idempotentHint") is True:
        return True
    return any(parameter.name == "idempotency_key" for parameter in tool.parameters)


def _relative_display_path(path: Path, base_dir: Path) -> str:
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()
