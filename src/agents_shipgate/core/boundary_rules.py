"""Shared conservative review rules for current and legacy boundary projections."""

from dataclasses import dataclass


@dataclass(frozen=True)
class GenericBoundaryRule:
    id: str
    check_id: str
    title: str
    action: str
    risk_level: str
    recommendation: str


GENERIC_BOUNDARY_RULES = {
    "PROTECTED-SURFACE-UNCLASSIFIED": GenericBoundaryRule(
        id="BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED",
        check_id="SHIP-AGENT-BOUNDARY-PROTECTED-SURFACE-UNCLASSIFIED",
        title="Protected coding-agent surface lacks a safe static classification",
        action="require_review",
        risk_level="medium",
        recommendation="Have a human review the protected boundary change.",
    ),
    "EXPERIMENTAL-SURFACE-CHANGED": GenericBoundaryRule(
        id="BOUNDARY-EXPERIMENTAL-SURFACE-CHANGED",
        check_id="SHIP-AGENT-BOUNDARY-EXPERIMENTAL-SURFACE-CHANGED",
        title="Experimental coding-agent boundary surface changed",
        action="require_review",
        risk_level="high",
        recommendation="Have a human review the experimental boundary surface.",
    ),
    "STATIC-REQUIREMENTS-CHANGED": GenericBoundaryRule(
        id="BOUNDARY-STATIC-REQUIREMENTS-CHANGED",
        check_id="SHIP-AGENT-BOUNDARY-STATIC-REQUIREMENTS-CHANGED",
        title="Static host requirements changed",
        action="require_review",
        risk_level="high",
        recommendation="Have a human review the static host requirements change.",
    ),
    "INPUT-INCOMPLETE": GenericBoundaryRule(
        id="BOUNDARY-INPUT-INCOMPLETE",
        check_id="SHIP-AGENT-BOUNDARY-INPUT-INCOMPLETE",
        title="Boundary input is incomplete",
        action="require_review",
        risk_level="medium",
        recommendation="Provide a complete, coherent boundary diff and rerun the check.",
    ),
}

