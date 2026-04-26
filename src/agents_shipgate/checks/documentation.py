from __future__ import annotations

import re

from agents_shipgate.checks.base import tool_finding
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.risk_hints import is_high_risk_tool, is_write_tool

SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bghp_[A-Za-z0-9_]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]
LABELED_SECRET_PATTERN = re.compile(
    r"(?i)\b(password|secret|token|api[_-]?key)\s*[:=]\s*([A-Za-z0-9_./+=-]{20,})"
)

INJECTION_PATTERNS = [
    re.compile(r"(?i)ignore (all )?(previous|prior) instructions"),
    re.compile(r"(?i)(ignore|override|replace).{0,40}(system prompt|developer message|instructions)"),
    re.compile(r"(?i)(system prompt|developer message).{0,40}(ignore|override|replace)"),
    re.compile(r"(?i)you are now (a|the) (system|developer|admin|root)"),
]


def run(context: ScanContext):
    findings = []
    for tool in context.tools:
        description = tool.description or ""
        if not description.strip() or len(description.strip()) < 20:
            findings.append(
                tool_finding(
                    tool=tool,
                    check_id="SHIP-DOC-MISSING-DESCRIPTION",
                    title=f"{tool.name} has a missing or too-short description",
                    severity="medium",
                    category="documentation",
                    evidence={"description_length": len(description.strip())},
                    confidence="high",
                    recommendation=f"Add a clear tool description for {tool.name} so the agent and reviewers can understand intended use.",
                    context=context,
                )
            )
        secret_matches = _secret_like_matches(description)
        if secret_matches:
            severity = _heuristic_security_severity(tool, secret_matches)
            findings.append(
                tool_finding(
                    tool=tool,
                    check_id="SHIP-DOC-SECRET-IN-DESCRIPTION",
                    title=f"{tool.name} description appears to contain a secret",
                    severity=severity,
                    category="security",
                    evidence={"matched": secret_matches},
                    confidence="high" if severity == "high" else "medium",
                    recommendation=f"Remove secret-like values from the {tool.name} description and rotate any exposed credentials.",
                    context=context,
                )
            )
        injection_matches = _injection_like_matches(description)
        if injection_matches:
            severity = _heuristic_security_severity(tool, injection_matches)
            findings.append(
                tool_finding(
                    tool=tool,
                    check_id="SHIP-DOC-INJECTION-RISK",
                    title=f"{tool.name} description contains instruction-like text",
                    severity=severity,
                    category="security",
                    evidence={"matched": injection_matches},
                    confidence="medium",
                    recommendation=f"Rewrite the {tool.name} description as capability metadata, not instructions to the model.",
                    context=context,
                )
            )
    return findings


def _secret_like_matches(description: str) -> list[str]:
    matches: list[str] = []
    for pattern in SECRET_PATTERNS:
        if pattern.search(description):
            matches.append(pattern.pattern)
    match = LABELED_SECRET_PATTERN.search(description)
    if not match:
        return matches
    value = match.group(2)
    if _looks_like_secret_value(value):
        matches.append("labeled_secret_value")
    return matches


def _looks_like_secret_value(value: str) -> bool:
    if len(value) < 20:
        return False
    has_alpha = any(char.isalpha() for char in value)
    has_digit = any(char.isdigit() for char in value)
    has_secret_alphabet = all(char.isalnum() or char in "_./+=-" for char in value)
    return has_alpha and has_digit and has_secret_alphabet


def _injection_like_matches(description: str) -> list[str]:
    return [
        pattern.pattern
        for pattern in INJECTION_PATTERNS
        if pattern.search(description)
    ]


def _heuristic_security_severity(tool, matches: list[str]) -> str:
    if len(matches) > 1 and (is_write_tool(tool) or is_high_risk_tool(tool)):
        return "high"
    return "medium"
