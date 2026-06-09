from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from agents_shipgate.core.domain import Scope, Tool, ToolRiskHint
from agents_shipgate.core.risk_hints import (
    canonical_risk_tags,
    is_effectively_read_only,
)
from agents_shipgate.schemas.common import parse_confidence
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
_SECRET_NAME_RE = re.compile(
    r"(api[_-]?key|auth|credential|password|secret|token)", re.IGNORECASE
)


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


def classify_tool_permission(tool: Tool) -> CapabilityPermissionProfile:
    """Classify one tool into Shipgate's deterministic permission lattice.

    The classifier is static-only. It never calls a server and never trusts a
    single weak signal as proof of safety. Explicit MCP/Shipgate annotations
    are read first, then scopes, config facts, existing risk hints, and finally
    conservative name/schema cues.
    """

    classes: set[PermissionClass] = set()
    reasons: list[str] = []
    annotations = tool.annotations

    explicit = _explicit_permission_classes(annotations)
    if explicit:
        classes.update(explicit)
        reasons.append("explicit_permission_class")

    if annotations.get("readOnlyHint") is True:
        classes.add("read")
        reasons.append("readOnlyHint")
    if annotations.get("destructiveHint") is True:
        classes.update({"write", "destructive"})
        reasons.append("destructiveHint")
    if annotations.get("openWorldHint") is True:
        classes.add("external")
        reasons.append("openWorldHint")

    scope_classes = _classes_from_scopes(tool.auth.scopes)
    if scope_classes:
        classes.update(scope_classes)
        reasons.append("auth_scopes")

    if annotations.get("mcp_env_secret_names"):
        classes.add("external")
        reasons.append("env_secret_pass_through")
    if annotations.get("mcp_wildcard_tools") or annotations.get("wildcard_tools"):
        classes.add("unknown")
        reasons.append("wildcard_tools")
    if annotations.get("mcp_unknown_schema"):
        classes.add("unknown")
        reasons.append("unknown_schema")

    hint_classes = _classes_from_risk_tags(canonical_risk_tags(tool))
    if hint_classes:
        classes.update(hint_classes)
        reasons.append("risk_hints")

    name_classes = _classes_from_name(tool.name, tool.description)
    if name_classes and not is_effectively_read_only(tool):
        classes.update(name_classes)
        reasons.append("name_description")

    if not classes:
        classes.add("read")
        reasons.append("default_read")

    if classes == {"read"} and is_effectively_read_only(tool):
        reasons.append("effective_read_only")

    normalized = _normalize_classes(classes)
    effect = PERMISSION_EFFECT[
        max(normalized, key=lambda item: PERMISSION_CLASS_RANK[item])
    ]
    side_effect_unknown = "unknown" in normalized
    score = _risk_score(tool, normalized, side_effect_unknown=side_effect_unknown)
    return CapabilityPermissionProfile(
        classes=tuple(sorted(normalized, key=lambda item: PERMISSION_CLASS_RANK[item])),
        effect=effect,
        risk_level=_risk_level(score),
        risk_score=score,
        side_effect_unknown=side_effect_unknown,
        reasons=tuple(dict.fromkeys(reasons)),
    )


def mcp_permission_risk_hints(tool: Tool) -> list[ToolRiskHint]:
    profile = classify_tool_permission(tool)
    hints: list[ToolRiskHint] = []
    for permission_class in profile.classes:
        tag = _risk_tag_for_class(permission_class)
        if tag is None:
            continue
        hints.append(
            ToolRiskHint(
                tag=tag,
                source="mcp_permission_lattice",
                confidence=parse_confidence("high" if permission_class == "read" else "medium"),
                evidence={
                    "permission_class": permission_class,
                    "risk_score": profile.risk_score,
                    "reasons": list(profile.reasons),
                },
            )
        )
    if profile.side_effect_unknown:
        hints.append(
            ToolRiskHint(
                tag="unknown_side_effect",
                source="mcp_permission_lattice",
                confidence=parse_confidence("high"),
                evidence={"risk_score": profile.risk_score, "reasons": list(profile.reasons)},
            )
        )
    return hints


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
    tokens = _tokens(f"{name} {description or ''}")
    classes: set[PermissionClass] = set()
    first = next(iter(_tokens(name)), "")
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
    return classes or {"read"}


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


def _tokens(value: str) -> set[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return set(re.findall(r"[a-z0-9]+", spaced.lower()))


__all__ = [
    "CapabilityPermissionProfile",
    "PERMISSION_CLASS_RANK",
    "PERMISSION_EFFECT",
    "PermissionClass",
    "RiskLevel",
    "classify_tool_permission",
    "is_secret_env_name",
    "mcp_permission_risk_hints",
]
