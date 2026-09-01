from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from agents_shipgate.core.domain import Scope, Tool, ToolRiskHint, ToolSemanticAssessment
from agents_shipgate.schemas.common import parse_confidence
from agents_shipgate.schemas.semantic import ToolSemanticEvidence
from agents_shipgate.schemas.surfaces import ActionEffect

PermissionClass = Literal[
    "read",
    "write",
    "destructive",
    "external",
    "financial",
    "production",
    "unknown",
]
RiskLevel = Literal["none", "low", "medium", "high", "critical"]

PERMISSION_CLASS_RANK: dict[PermissionClass, int] = {
    "read": 0,
    "write": 1,
    "external": 2,
    "financial": 3,
    "production": 3,
    "destructive": 4,
    "unknown": 5,
}

PERMISSION_EFFECT: dict[PermissionClass, ActionEffect] = {
    "read": "read",
    "write": "write",
    "destructive": "destructive",
    "external": "external_communication",
    "financial": "financial_write",
    "production": "production_operation",
    "unknown": "write",
}

_WRITE_TOKENS = {
    "add",
    "apply",
    "archive",
    "cancel",
    "commit",
    "create",
    "edit",
    "grant",
    "insert",
    "merge",
    "modify",
    "patch",
    "post",
    "publish",
    "push",
    "run",
    "send",
    "set",
    "update",
    "write",
}
_DESTRUCTIVE_TOKENS = {
    "delete",
    "destroy",
    "drop",
    "kill",
    "purge",
    "remove",
    "revoke",
    "terminate",
    "truncate",
    "wipe",
}
_READ_TOKENS = {
    "describe",
    "fetch",
    "find",
    "get",
    "list",
    "lookup",
    "read",
    "search",
    "show",
    "status",
    "view",
}
_EXTERNAL_TOKENS = {"email", "message", "notify", "post", "publish", "send", "sms"}
_FINANCIAL_TOKENS = {
    "billing",
    "charge",
    "invoice",
    "pay",
    "payment",
    "refund",
    "stripe",
    "transfer",
}
_PRODUCTION_TOKENS = {
    "aws",
    "azure",
    "cluster",
    "deploy",
    "gcp",
    "kubernetes",
    "prod",
    "production",
    "terraform",
}
_SECRET_NAME_RE = re.compile(r"(api[_-]?key|auth|credential|password|secret|token)", re.IGNORECASE)


@dataclass(frozen=True)
class CapabilityPermissionProfile:
    classes: tuple[PermissionClass, ...]
    effect: ActionEffect
    risk_level: RiskLevel
    risk_score: int
    side_effect_unknown: bool
    reasons: tuple[str, ...]

    @property
    def strongest_class(self) -> PermissionClass:
        return max(self.classes, key=lambda item: PERMISSION_CLASS_RANK[item])

    @property
    def is_read_only(self) -> bool:
        return self.classes == ("read",) and not self.side_effect_unknown

    @property
    def has_side_effect(self) -> bool:
        return any(item != "read" for item in self.classes)


@dataclass(frozen=True)
class SemanticPermissionClassification:
    """The half of the permission profile that reads only static semantics.

    Split out of :func:`classify_tool_permission` so the frozen capability
    payload (``schemas.capability_payload``) publishes the *same* classes the
    MCP audit surface reports, rather than a second classifier that could drift
    from it. The other half — ``risk_score`` and the ``risk_level`` derived from
    it — needs the ``Tool`` itself and stays where it was.
    """

    classes: tuple[PermissionClass, ...]
    effect: ActionEffect
    side_effect_unknown: bool
    reasons: tuple[str, ...]


def classify_semantic_permission(
    assessment: ToolSemanticAssessment | ToolSemanticEvidence,
) -> SemanticPermissionClassification:
    """Classify a resolved semantic assessment into permission classes.

    Accepts either the in-memory resolver model or its wire projection: the two
    carry the same fields, and capability facts hold the projection.
    """

    classes: set[PermissionClass] = set()
    reasons = [issue.kind for issue in assessment.effect.issues]
    for claim in assessment.effect.claims:
        if claim.source == "mcp_protocol_default":
            reasons.append(claim.source)
            continue
        permission_class = _permission_class_for_effect(claim.value)
        if permission_class is not None:
            classes.add(permission_class)
        reasons.append(claim.source)

    if not classes and assessment.effect.status in {"declared", "structural"}:
        permission_class = _permission_class_for_effect(assessment.conservative_effect)
        if permission_class is not None:
            classes.add(permission_class)

    side_effect_unknown = assessment.effect.status in {
        "protocol_default",
        "inferred",
        "unknown",
        "conflicting",
    } or any(
        issue.kind in {"incomplete_surface", "unattested_surface"}
        for issue in assessment.effect.issues
    )
    if side_effect_unknown:
        classes.add("unknown")

    normalized = _normalize_classes(classes)
    return SemanticPermissionClassification(
        # The rank alone is not a total order — `financial` and `production`
        # share rank 3 — so a rank-only key left their relative order to the
        # stable sort's input, which is set-iteration order and therefore
        # hash-randomized per process. That reached the capability payload's
        # published bytes and its digests. Break every tie by name.
        classes=tuple(sorted(normalized, key=lambda item: (PERMISSION_CLASS_RANK[item], item))),
        effect=assessment.conservative_effect,
        side_effect_unknown=side_effect_unknown,
        reasons=tuple(dict.fromkeys(reasons)),
    )


def classify_tool_permission(tool: Tool) -> CapabilityPermissionProfile:
    """Classify one tool into Shipgate's deterministic permission lattice.

    Compatibility projection over the central semantic resolver. The lattice
    retains its legacy classes and risk score for MCP audit consumers, but it
    no longer implements an independent effect inference path.
    """

    # Local import avoids a domain/lattice/resolver cycle.
    from agents_shipgate.core.semantic_assessment import assess_tool_semantics

    assessment = tool.semantic_assessment or assess_tool_semantics(tool)
    semantic = classify_semantic_permission(assessment)
    score = _risk_score(
        tool,
        set(semantic.classes),
        side_effect_unknown=semantic.side_effect_unknown,
    )
    return CapabilityPermissionProfile(
        classes=semantic.classes,
        effect=semantic.effect,
        risk_level=_risk_level(score),
        risk_score=score,
        side_effect_unknown=semantic.side_effect_unknown,
        reasons=semantic.reasons,
    )


def _permission_class_for_effect(value: str) -> PermissionClass | None:
    return {
        "read": "read",
        "write": "write",
        "destructive": "destructive",
        "external_communication": "external",
        "financial_write": "financial",
        "production_operation": "production",
        # The legacy MCP lattice has no first-class classes for these effects;
        # retain the conservative write upper bound instead of inventing one.
        "privileged_data_access": "write",
        "code_execution": "write",
        "identity_access": "write",
    }.get(value)  # type: ignore[return-value]


def mcp_permission_risk_hints(tool: Tool) -> list[ToolRiskHint]:
    profile = classify_tool_permission(tool)
    hints: list[ToolRiskHint] = []
    for permission_class in profile.classes:
        tag = _risk_tag_for_class(permission_class)
        if tag is None:
            continue
        source = _permission_hint_source(tool, permission_class)
        supporting_scopes = sorted(
            scope
            for scope in tool.auth.scopes
            if permission_class in _classes_from_scopes([scope])
        )
        hints.append(
            ToolRiskHint(
                tag=tag,
                source=source,
                confidence=parse_confidence(
                    _read_only_confidence(tool) if permission_class == "read" else "medium"
                ),
                basis=_permission_hint_basis(source),
                evidence={
                    "permission_class": permission_class,
                    "risk_score": profile.risk_score,
                    "reasons": list(profile.reasons),
                    **({"scopes": supporting_scopes} if source == "auth_scope" else {}),
                },
            )
        )
    if profile.side_effect_unknown:
        hints.append(
            ToolRiskHint(
                tag="unknown_side_effect",
                source="mcp_permission_lattice",
                confidence=parse_confidence("high"),
                basis="protocol_default",
                evidence={"risk_score": profile.risk_score, "reasons": list(profile.reasons)},
            )
        )
    return hints


def _permission_hint_basis(source: str) -> str:
    if source == "mcp_annotation":
        return "protocol_structure"
    if source == "auth_scope":
        return "structural_scope"
    if source == "mcp_config":
        return "protocol_default"
    return "inferred_keyword"


def is_secret_env_name(name: str) -> bool:
    return bool(_SECRET_NAME_RE.search(name or ""))


def _explicit_permission_classes(annotations: dict[str, object]) -> set[PermissionClass]:
    raw = (
        annotations.get("shipgate_permission_classes")
        or annotations.get("permission_classes")
        or annotations.get("permission_class")
        or annotations.get("x-agents-shipgate-permissions")
    )
    values: list[object]
    if isinstance(raw, list):
        values = raw
    elif raw is None:
        values = []
    else:
        values = [raw]
    out: set[PermissionClass] = set()
    for value in values:
        text = str(value).strip().lower()
        if text in PERMISSION_CLASS_RANK:
            out.add(text)  # type: ignore[arg-type]
    return out


def _classes_from_scopes(scopes: list[str]) -> set[PermissionClass]:
    classes: set[PermissionClass] = set()
    for raw in scopes:
        scope = Scope.parse(raw)
        lower = raw.lower()
        tokens = _tokens(lower)
        if scope.is_broad():
            classes.add("unknown")
        if scope.is_read():
            classes.add("read")
        if scope.is_write() or tokens & _WRITE_TOKENS:
            classes.add("write")
        if tokens & _DESTRUCTIVE_TOKENS:
            classes.add("destructive")
        if tokens & _EXTERNAL_TOKENS:
            classes.add("external")
        if tokens & _FINANCIAL_TOKENS:
            classes.add("financial")
        if tokens & _PRODUCTION_TOKENS:
            classes.add("production")
    return classes


def _classes_from_risk_tags(tags: list[str]) -> set[PermissionClass]:
    tag_set = set(tags)
    classes: set[PermissionClass] = set()
    if "read_only" in tag_set:
        classes.add("read")
    if "writes_data" in tag_set or "filesystem_write" in tag_set:
        classes.add("write")
    if "destructive" in tag_set or "irreversible" in tag_set:
        classes.add("destructive")
    if "external_communication" in tag_set or "network_access" in tag_set:
        classes.add("external")
    if "financial_write" in tag_set:
        classes.add("financial")
    if "production_ops" in tag_set:
        classes.add("production")
    if "unknown_side_effect" in tag_set:
        classes.add("unknown")
    return classes


def _classes_from_name(name: str, description: str | None) -> set[PermissionClass]:
    ordered_name_tokens = _ordered_tokens(name)
    tokens = set(_ordered_tokens(f"{name} {description or ''}"))
    classes: set[PermissionClass] = set()
    first = ordered_name_tokens[0] if ordered_name_tokens else ""
    if first in _READ_TOKENS:
        classes.add("read")
    if tokens & _WRITE_TOKENS:
        classes.add("write")
    if tokens & _DESTRUCTIVE_TOKENS:
        classes.update({"write", "destructive"})
    if tokens & _EXTERNAL_TOKENS:
        classes.add("external")
    if tokens & _FINANCIAL_TOKENS:
        classes.add("financial")
    if tokens & _PRODUCTION_TOKENS:
        classes.add("production")
    return classes


def _normalize_classes(classes: set[PermissionClass]) -> set[PermissionClass]:
    if "destructive" in classes:
        classes.add("write")
    if classes - {"read"}:
        classes.discard("read")
    return classes or {"unknown"}


def _permission_hint_source(
    tool: Tool,
    permission_class: PermissionClass,
) -> str:
    """Return the strongest provenance that supports one permission class."""

    annotations = tool.annotations
    if permission_class in _explicit_permission_classes(annotations):
        return "mcp_annotation"
    if permission_class == "read" and annotations.get("readOnlyHint") is True:
        return "mcp_annotation"
    if permission_class in {"write", "destructive"} and annotations.get("destructiveHint") is True:
        return "mcp_annotation"
    if permission_class in _classes_from_scopes(tool.auth.scopes):
        return "auth_scope"
    if permission_class == "unknown" and (
        annotations.get("mcp_wildcard_tools")
        or annotations.get("wildcard_tools")
        or annotations.get("mcp_unknown_schema")
    ):
        return "mcp_config"
    return "keyword"


def _risk_score(
    tool: Tool,
    classes: set[PermissionClass],
    *,
    side_effect_unknown: bool,
) -> int:
    score = max(
        {
            "read": 10,
            "write": 40,
            "external": 70,
            "financial": 90,
            "production": 90,
            "destructive": 95,
            "unknown": 60,
        }[item]
        for item in classes
    )
    approval_mode = str(tool.annotations.get("mcp_approval_mode") or "").lower()
    if approval_mode == "approve" and any(item != "read" for item in classes):
        score += 20
    if tool.annotations.get("wildcard_tools") or tool.annotations.get("mcp_wildcard_tools"):
        score += 20
    if tool.annotations.get("mcp_env_secret_names"):
        score += 15
    if any(Scope.parse(raw).is_broad() for raw in tool.auth.scopes):
        score += 10
    if side_effect_unknown:
        score += 10
    return min(score, 100)


def _risk_level(score: int) -> RiskLevel:
    if score >= 90:
        return "critical"
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    if score > 0:
        return "low"
    return "none"


def _risk_tag_for_class(permission_class: PermissionClass) -> str | None:
    return {
        "read": "read_only",
        "write": "write",
        "destructive": "destructive",
        "external": "external_write",
        "financial": "financial_action",
        "production": "infrastructure_change",
        "unknown": "unknown_side_effect",
    }.get(permission_class)


def _read_only_confidence(tool: Tool) -> str:
    annotations = tool.annotations
    explicit = _explicit_permission_classes(annotations)
    if annotations.get("readOnlyHint") is True or explicit == {"read"}:
        return "high"
    return "medium"


def _tokens(value: str) -> set[str]:
    return set(_ordered_tokens(value))


def _ordered_tokens(value: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return re.findall(r"[a-z0-9]+", spaced.lower())


__all__ = [
    "CapabilityPermissionProfile",
    "SemanticPermissionClassification",
    "PERMISSION_CLASS_RANK",
    "PERMISSION_EFFECT",
    "PermissionClass",
    "RiskLevel",
    "classify_semantic_permission",
    "classify_tool_permission",
    "is_secret_env_name",
    "mcp_permission_risk_hints",
]
