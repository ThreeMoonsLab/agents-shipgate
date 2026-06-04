from __future__ import annotations

import re
from collections.abc import Iterable

from agents_shipgate.schemas.common import SourceReference, parse_confidence, parse_severity
from agents_shipgate.schemas.report import Finding
from agents_shipgate.skill.models import SkillArtifact, TextSegment

MUTATING_PATTERNS = [
    re.compile(r"\brm\s+-[A-Za-z]*r[fA-Za-z]*\b"),
    re.compile(r"\bfind\b.+\s-delete\b"),
    re.compile(r"\bgit\s+(?:push|clean|reset\s+--hard)\b"),
    re.compile(r"\b(?:mv|cp|chmod|chown)\b.+\s(?:/|~|\.)"),
    re.compile(r"\b(?:deploy|publish|release)\b", re.IGNORECASE),
    re.compile(r"\b(?:POST|PUT|PATCH|DELETE)\b"),
    re.compile(r"\brequests\.(?:post|put|patch|delete)\b"),
]

DRY_RUN_RE = re.compile(r"--dry-run|\bdry_run\b|\bdry-run\b", re.IGNORECASE)
CONFIRM_RE = re.compile(r"\b(confirm|confirmation|ask before|manual approval)\b", re.IGNORECASE)


def skill_finding(
    *,
    artifact: SkillArtifact,
    check_id: str,
    title: str,
    severity: str,
    category: str,
    evidence: dict[str, object],
    confidence: str,
    recommendation: str,
    line: int | None = None,
    path: str | None = None,
    provenance_kind: str = "static_declaration",
) -> Finding:
    source_path = path or artifact.path
    return Finding(
        check_id=check_id,
        title=title,
        severity=parse_severity(severity),
        category=category,
        agent_id="skill-review",
        evidence={"artifact_path": artifact.path, **evidence},
        confidence=parse_confidence(confidence),
        provenance_kind=provenance_kind,  # type: ignore[arg-type]
        source=SourceReference(
            type="skill",
            ref=f"{source_path}:{line}" if line else source_path,
            path=source_path,
            start_line=line,
        ),
        recommendation=recommendation,
    )


def has_section(artifact: SkillArtifact, names: Iterable[str]) -> bool:
    normalized = set()
    for name in names:
        normalized.add(_norm(name))
    return any(
        section == name or section.startswith(f"{name}:")
        for section in artifact.sections
        for name in normalized
    )


def excerpt(text: str, line: int, *, max_len: int = 180) -> str:
    lines = text.splitlines()
    if line < 1 or line > len(lines):
        return ""
    value = lines[line - 1].strip()
    if len(value) <= max_len:
        return value
    return f"{value[:max_len]}..."


def iter_lines(segment: TextSegment):
    yield from enumerate(segment.text.splitlines(), start=segment.start_line)


def mutating_line(line: str) -> bool:
    return any(pattern.search(line) for pattern in MUTATING_PATTERNS)


def has_dry_run(text: str) -> bool:
    return bool(DRY_RUN_RE.search(text))


def has_confirmation(text: str) -> bool:
    return bool(CONFIRM_RE.search(text))


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())
