from __future__ import annotations

import copy
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

RULES_VERSION = "0.1"
SENSITIVE_FIELD_INVENTORY_VERSION = "0.1"

REDACTION_MARKER_RE = re.compile(r"\[REDACTED:[a-z0-9_:-]+\]")

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_api_key", re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[opusr]_[A-Za-z0-9_]{20,}\b")),
    (
        "stripe_key",
        re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b"),
    ),
    ("slack_token", re.compile(r"\bxox[boprs]-[A-Za-z0-9-]{10,}\b")),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    ),
    (
        "database_url",
        re.compile(
            r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://"
            r"[^:\s/@]+:[^@\s]+@[^ \t\r\n'\"]+"
        ),
    ),
    (
        "bearer_token",
        re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{12,}\b", re.IGNORECASE),
    ),
)

LABELED_SECRET_PATTERN = re.compile(
    r"(?i)\b(password|secret|token|api[_-]?key)\s*([:=])\s*([A-Za-z0-9_./+=-]{20,})"
)

SENSITIVE_VALUE_KEYS = {
    "apikey",
    "api_key",
    "authorization",
    "bearer",
    "clientsecret",
    "client_secret",
    "credential",
    "credentials",
    "password",
    "passwd",
    "privatekey",
    "private_key",
    "pwd",
    "secret",
    "token",
}

@dataclass
class RedactionStats:
    """Counts redactions by structural JSON path without storing values."""

    path_kinds: dict[str, Counter[str]] = field(
        default_factory=lambda: defaultdict(Counter)
    )

    def record(self, path: str, kind: str, count: int = 1) -> None:
        if count <= 0:
            return
        self.path_kinds[path][kind] += count

    @property
    def occurrence_count(self) -> int:
        return sum(sum(counter.values()) for counter in self.path_kinds.values())

    def merge(self, other: RedactionStats) -> None:
        for path, counter in other.path_kinds.items():
            self.path_kinds[path].update(counter)


def redact_text(
    value: str | None,
    *,
    stats: RedactionStats | None = None,
    path: str = "$",
    force_kind: str | None = None,
) -> str | None:
    if value is None:
        return None
    if REDACTION_MARKER_RE.fullmatch(value.strip()):
        return value
    if force_kind and value:
        if stats is not None:
            stats.record(path, force_kind)
        return _marker(force_kind)

    redacted = value
    for kind, pattern in SECRET_PATTERNS:
        redacted, count = pattern.subn(_marker(kind), redacted)
        if count and stats is not None:
            stats.record(path, kind, count)
    redacted = _redact_labeled_secret(redacted, stats=stats, path=path)
    return redacted


def redact_data(value: Any, *, stats: RedactionStats | None = None, path: str = "$") -> Any:
    if isinstance(value, BaseModel):
        return redact_data(value.model_dump(mode="python"), stats=stats, path=path)
    if isinstance(value, str):
        return redact_text(value, stats=stats, path=path)
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for raw_key, raw_item in value.items():
            key_text = str(raw_key) if isinstance(raw_key, str) else None
            key = (
                redact_text(key_text, stats=stats, path=f"{path}.<key>")
                if key_text is not None
                else raw_key
            )
            child_path = f"{path}.{_path_key(key)}"
            if isinstance(raw_item, str) and _is_sensitive_key(raw_key):
                redacted_item = redact_text(raw_item, stats=stats, path=child_path)
                if redacted_item == raw_item:
                    redacted_item = redact_text(
                        raw_item,
                        stats=stats,
                        path=child_path,
                        force_kind="sensitive_field",
                    )
                redacted[key] = redacted_item
            else:
                redacted[key] = redact_data(raw_item, stats=stats, path=child_path)
        return redacted
    if isinstance(value, list):
        return [redact_data(item, stats=stats, path=f"{path}[]") for item in value]
    if isinstance(value, tuple):
        return tuple(redact_data(item, stats=stats, path=f"{path}[]") for item in value)
    if isinstance(value, set):
        return {redact_data(item, stats=stats, path=f"{path}[]") for item in value}
    return copy.deepcopy(value)


def sanitize_model[T: BaseModel](
    model: T, model_type: type[T], *, stats: RedactionStats, path: str
) -> T:
    payload = redact_data(model.model_dump(mode="python"), stats=stats, path=path)
    return model_type.model_validate(payload)


def sanitize_tools(tools: Iterable[Any], *, stats: RedactionStats) -> list[Any]:
    from agents_shipgate.core.models import Tool

    return [sanitize_model(tool, Tool, stats=stats, path="tools[]") for tool in tools]


def sanitize_findings(findings: Iterable[Any], *, stats: RedactionStats) -> list[Any]:
    from agents_shipgate.core.models import Finding

    return [
        sanitize_model(finding, Finding, stats=stats, path="findings[]")
        for finding in findings
    ]


def sanitize_report(report: Any) -> Any:
    from agents_shipgate.core.models import ReadinessReport

    payload = sanitize_report_payload(report.model_dump(mode="python"))
    return ReadinessReport.model_validate(payload)


def sanitize_report_payload(payload: dict[str, Any]) -> dict[str, Any]:
    stats = RedactionStats()
    return redact_data(payload, stats=stats, path="$")


def sanitize_packet(packet: Any) -> Any:
    from agents_shipgate.packet.models import EvidencePacket

    payload = sanitize_packet_payload(packet.model_dump(mode="python"))
    return EvidencePacket.model_validate(payload)


def sanitize_packet_payload(payload: dict[str, Any]) -> dict[str, Any]:
    stats = RedactionStats()
    return redact_data(payload, stats=stats, path="$")


def build_privacy_audit(
    stats: RedactionStats,
    *,
    output_surfaces: Iterable[str],
    notes: Iterable[str] | None = None,
) -> Any:
    from agents_shipgate.core.models import PrivacyAudit, RedactedPathSummary

    return PrivacyAudit(
        enabled=True,
        rules_version=RULES_VERSION,
        sensitive_field_inventory_version=SENSITIVE_FIELD_INVENTORY_VERSION,
        redacted_occurrence_count=stats.occurrence_count,
        redacted_paths=[
            RedactedPathSummary(
                path=path,
                count=sum(counter.values()),
                kinds=sorted(counter),
            )
            for path, counter in sorted(stats.path_kinds.items())
        ],
        output_surfaces=sorted(dict.fromkeys(output_surfaces)),
        notes=list(notes or ()),
    )


def looks_like_secret_value(value: str) -> bool:
    if len(value) < 20:
        return False
    has_alpha = any(char.isalpha() for char in value)
    has_digit = any(char.isdigit() for char in value)
    has_secret_alphabet = all(char.isalnum() or char in "_./+=-" for char in value)
    return has_alpha and has_digit and has_secret_alphabet


def _redact_labeled_secret(
    value: str,
    *,
    stats: RedactionStats | None,
    path: str,
) -> str:
    def replace(match: re.Match[str]) -> str:
        secret_value = match.group(3)
        if not looks_like_secret_value(secret_value):
            return match.group(0)
        if stats is not None:
            stats.record(path, "labeled_secret_value")
        return f"{match.group(1)}{match.group(2)} {_marker('labeled_secret_value')}"

    return LABELED_SECRET_PATTERN.sub(replace, value)


def _is_sensitive_key(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return _normalized_key(value) in SENSITIVE_VALUE_KEYS


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "", value.lower())


def _path_key(value: object) -> str:
    text = str(value)
    if REDACTION_MARKER_RE.search(text):
        return "<redacted>"
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
        return text
    return "<key>"


def _marker(kind: str) -> str:
    return f"[REDACTED:{kind}]"
