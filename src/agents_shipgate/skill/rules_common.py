from __future__ import annotations

import re
from collections.abc import Iterable

from agents_shipgate.core.privacy import redact_text, truncate_redacted
from agents_shipgate.schemas.common import SourceReference, parse_confidence, parse_severity
from agents_shipgate.schemas.report import Finding
from agents_shipgate.skill.models import SkillArtifact, TextSegment

COMMAND_PREFIX = r"^\s*(?:(?:sudo|command|time|noglob)\s+)*"

MUTATING_PATTERNS = [
    re.compile(r"\brm\s+-[A-Za-z]*r[fA-Za-z]*\b"),
    re.compile(r"\bfind\b.+\s-delete\b"),
    re.compile(r"\bgit\s+(?:push|clean|reset\s+--hard)\b"),
    re.compile(r"\b(?:mv|cp|chmod|chown)\b.+\s(?:/|~|\.)"),
    re.compile(r"\brequests\.(?:post|put|patch|delete)\b", re.IGNORECASE),
    re.compile(
        COMMAND_PREFIX
        + r"(?:curl|xh|http)\b[^\n]*(?:-X|--request)\s*(?:POST|PUT|PATCH|DELETE)\b",
        re.IGNORECASE,
    ),
    re.compile(
        COMMAND_PREFIX + r"(?:http|https|xh)\s+(?:POST|PUT|PATCH|DELETE)\b",
        re.IGNORECASE,
    ),
]

RELEASE_COMMAND_PATTERNS = [
    re.compile(COMMAND_PREFIX + r"(?:npm|pnpm|bun|cargo|poetry)\s+publish\b", re.IGNORECASE),
    re.compile(COMMAND_PREFIX + r"yarn\s+(?:npm\s+)?publish\b", re.IGNORECASE),
    re.compile(COMMAND_PREFIX + r"twine\s+upload\b", re.IGNORECASE),
    re.compile(COMMAND_PREFIX + r"gh\s+release\s+(?:create|delete|edit|upload)\b", re.IGNORECASE),
    re.compile(
        COMMAND_PREFIX + r"(?:npx\s+)?(?:semantic-release|release-it|np)\b",
        re.IGNORECASE,
    ),
    re.compile(
        COMMAND_PREFIX
        + r"(?:make|just|task|npm|pnpm|yarn|bun)\s+(?:run\s+)?[\w:.-]*(?:deploy|release)[\w:.-]*\b",
        re.IGNORECASE,
    ),
    re.compile(
        COMMAND_PREFIX
        + r"(?:fly|vercel|netlify|wrangler|firebase|sls|serverless|sst|sam|pulumi|terraform|gcloud|aws|az|cap)\b"
        + r"[^\n;|&]*\bdeploy\b",
        re.IGNORECASE,
    ),
    re.compile(
        COMMAND_PREFIX + r"[\w./-]*(?:deploy|release)[\w./-]*(?:\.sh|\.bash)?(?:\s|$)",
        re.IGNORECASE,
    ),
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
        _section_matches(section, name)
        for section in artifact.sections
        for name in normalized
    )


def excerpt(text: str, line: int, *, max_len: int = 180) -> str:
    lines = text.splitlines()
    if line < 1 or line > len(lines):
        return ""
    return bounded_excerpt(lines[line - 1], max_len=max_len)


def bounded_excerpt(text: str, *, max_len: int = 180) -> str:
    """``text`` redacted, then cut to ``max_len`` characters with ``...``.

    Redacting after the cut published the first part of a credential that
    straddled ``max_len``: too short for any rule to recognise by the time the
    report redacted it (#1079).
    """

    value = redact_text(text.strip()) or ""
    if len(value) <= max_len:
        return value
    return f"{truncate_redacted(value, max_len)}..."


def iter_lines(segment: TextSegment):
    yield from enumerate(segment.text.splitlines(), start=segment.start_line)


def mutating_line(line: str) -> bool:
    return any(pattern.search(line) for pattern in MUTATING_PATTERNS) or any(
        pattern.search(line) for pattern in RELEASE_COMMAND_PATTERNS
    )


def has_dry_run(text: str) -> bool:
    return bool(DRY_RUN_RE.search(text))


def has_confirmation(text: str) -> bool:
    return bool(CONFIRM_RE.search(text))


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _section_matches(section: str, name: str) -> bool:
    if section == name:
        return True
    return bool(re.match(rf"^{re.escape(name)}(?:\b|[\s:([{{/.-])", section))
