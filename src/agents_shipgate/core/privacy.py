from __future__ import annotations

import base64
import binascii
import copy
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote_plus

from pydantic import BaseModel

#: Bumped whenever a rule below redacts text an earlier version published.
#: ``0.2`` (#1026): ``Basic`` credentials, PEM private-key blocks, Google
#: ``AIza`` API keys, unlabeled AWS secret access keys, ``authorization`` and
#: ``auth`` labels, and the extra fields of an ``extra="allow"`` model.
RULES_VERSION = "0.2"
SENSITIVE_FIELD_INVENTORY_VERSION = "0.1"

REDACTION_MARKER_RE = re.compile(r"\[REDACTED:[a-z0-9_:-]+\]")
_MARKER_KIND_RE = re.compile(r"\[REDACTED:([a-z0-9_:-]+)\]")
BEARER_TOKEN_RE = re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{12,}\b", re.IGNORECASE)

#: The authentication schemes that may lead an ``Authorization`` header's
#: value. The host-grant readers' header redaction (``core.host_grants``)
#: builds on the same alternation: without it the scheme word alone was taken
#: as the value and the credential after it was published.
AUTH_SCHEME_PATTERN = r"(?:bearer|basic|digest|token|negotiate)"

class CheckedPattern:
    """A compiled expression whose every match must also pass ``accept``.

    Some secret shapes cannot be told from prose by a regular expression
    alone: ``Basic ReadOnly`` has the shape of a ``Basic`` credential, and
    ``getCustomerOrderHistoryForAccountV2Async`` the shape of an AWS secret
    key (#1026 review). A second, exact test of each match decides. The class
    offers the four members the rules' callers use — ``search``,
    ``finditer``, ``subn`` and ``pattern`` — so it stands where an
    ``re.Pattern`` stands in :data:`SECRET_PATTERNS`.
    """

    __slots__ = ("_accept", "_compiled", "pattern")

    def __init__(
        self,
        compiled: re.Pattern[str],
        accept: Callable[[re.Match[str]], bool],
    ) -> None:
        self._compiled = compiled
        self._accept = accept
        self.pattern = compiled.pattern

    def finditer(self, string: str) -> Iterator[re.Match[str]]:
        return (match for match in self._compiled.finditer(string) if self._accept(match))

    def search(self, string: str) -> re.Match[str] | None:
        return next(self.finditer(string), None)

    def subn(self, repl: str, string: str) -> tuple[str, int]:
        count = 0

        def replace(match: re.Match[str]) -> str:
            nonlocal count
            if not self._accept(match):
                return match.group(0)
            count += 1
            return repl

        return self._compiled.sub(replace, string), count


SecretPattern = re.Pattern[str] | CheckedPattern


def _is_basic_credential(match: re.Match[str]) -> bool:
    """Whether the token after ``Basic`` decodes to ``user:password``.

    Every HTTP Basic credential is the base64 of ``user:password``, so it is
    at least eight characters and decodes to printable ASCII holding a colon
    after a non-empty user. ``ReadOnly``, ``DataSets`` or ``MetaData`` decode
    to bytes that are not text at all; no word of a 160,000-word English list,
    in any of its casings, passes.
    """

    token = match.group(1)
    if len(token) < 8:
        return False
    try:
        decoded = base64.b64decode(token, validate=True)
    except (binascii.Error, ValueError):
        return False
    if not decoded or any(byte < 0x20 or byte > 0x7E for byte in decoded):
        return False
    user, colon, _password = decoded.partition(b":")
    return bool(colon and user)


#: ``Basic <base64(user:password)>``, with or without the ``Authorization:``
#: label before it, which is kept. Only a token that decodes to ``user:…`` is
#: a credential (:func:`_is_basic_credential`), so prose such as ``Provides
#: Basic ReadOnly access`` is neither reported nor redacted. A labeled
#: credential of any other shape is still redacted, by
#: :data:`CREDENTIAL_LABEL_PATTERN`.
BASIC_AUTH_RE = CheckedPattern(
    re.compile(r"\b(?i:basic)\s+([A-Za-z0-9+/]{4,}={0,2})(?![A-Za-z0-9+/=_-])"),
    _is_basic_credential,
)

#: A PEM private-key block: through its ``-----END … PRIVATE KEY-----`` line,
#: or, when the text holds no end line (a truncated or wrapped key), through
#: the end of the text, since everything after the header may be key material.
PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN[A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----"
    r"(?:.*?-----END[A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----|.*)",
    re.DOTALL,
)

#: A Google API key: ``AIza`` and 35 URL-safe characters.
GOOGLE_API_KEY_RE = re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{35}(?![A-Za-z0-9])")

_LOWER_RUN_RE = re.compile(r"[a-z]+")


def _is_random_token(match: re.Match[str]) -> bool:
    """Whether a 40-character run reads as random rather than as words.

    A camelCase identifier (``getCustomerOrderHistoryForAccountV2Async``) or a
    relative path (``Sources/PaymentKit/Handlers/RefundTools2``) splits into
    words of two or more lower-case letters, upper-case runs, digit runs and
    ``/``. A random key almost never does: it holds a ``+`` or a lone
    lower-case letter between other characters (AWS's documented example key
    opens with ``wJ``); about one random key in 1,400 reads as words.
    """

    token = match.group(0)
    return "+" in token or any(len(run) < 2 for run in _LOWER_RUN_RE.findall(token))


#: An AWS secret access key in free text, with no label to find it by: a run
#: of exactly 40 base64 characters holding an upper-case letter, a lower-case
#: letter and a digit, not starting with ``/``, that does not read as words
#: (:func:`_is_random_token`). A Git commit id (lower-case hex), a ``sha256``
#: digest, a JSON pointer, a path or a camelCase name does not take that
#: shape. It has no prefix a cheap precheck can find, so it is read in every
#: string.
AWS_SECRET_ACCESS_KEY_RE = CheckedPattern(
    re.compile(
        r"(?<![A-Za-z0-9/+])(?!/)"
        r"(?=[A-Za-z0-9/+]{0,39}[A-Z])(?=[A-Za-z0-9/+]{0,39}[a-z])(?=[A-Za-z0-9/+]{0,39}[0-9])"
        r"[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+=])"
    ),
    _is_random_token,
)

SECRET_PATTERNS: tuple[tuple[str, SecretPattern], ...] = (
    ("openai_api_key", re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}")),
    (
        "aws_access_key",
        re.compile(r"\b(?:AKIA|ASIA|AROA|AGPA|ANPA|AIDA|AIPA)[0-9A-Z]{16}\b"),
    ),
    (
        "github_token",
        re.compile(r"\b(?:gh[opusr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    ),
    (
        "stripe_key",
        re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b"),
    ),
    ("stripe_webhook_secret", re.compile(r"\bwhsec_[A-Za-z0-9]{16,}\b")),
    ("slack_token", re.compile(r"\bxox[aboprs]-[A-Za-z0-9-]{10,}\b")),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    ),
    (
        "database_url",
        re.compile(
            r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|rediss|"
            r"mssql|sqlserver|clickhouse)://"
            r"[^:\s/@]+:[^@\s]+@[^ \t\r\n'\"]+"
        ),
    ),
    ("bearer_token", BEARER_TOKEN_RE),
    ("basic_auth", BASIC_AUTH_RE),
    ("private_key", PRIVATE_KEY_RE),
    ("google_api_key", GOOGLE_API_KEY_RE),
)

#: Shapes that are redacted but never *detected*. ``SECRET_PATTERNS`` also
#: decides findings (``SHIP-DOC-SECRET-IN-DESCRIPTION``,
#: ``SHIP-N8N-SECRET-IN-WORKFLOW-PARAMETER``, skill ``SEC-SECRET-001``), and a
#: context-free shape would raise one, and an evidence gap with it, for any
#: 40-character token that happens to look random. Over-redaction is the
#: accepted direction for output; a false finding is not.
REDACTION_ONLY_SECRET_PATTERNS: tuple[tuple[str, SecretPattern], ...] = (
    ("aws_secret_access_key", AWS_SECRET_ACCESS_KEY_RE),
)

# Marker kinds this module can actually emit. The redact_text passthrough
# (idempotency over already-redacted values) must only honor these: a
# scanned input that merely *looks* like a marker — e.g.
# "[REDACTED:my-lowercase-secret-123]" — would otherwise bypass forced
# sensitive-key redaction and smuggle its payload into the report.
KNOWN_MARKER_KINDS: frozenset[str] = frozenset(
    name for name, _ in (*SECRET_PATTERNS, *REDACTION_ONLY_SECRET_PATTERNS)
) | {"labeled_secret_value", "sensitive_field"}


def _is_known_marker(value: str) -> bool:
    match = _MARKER_KIND_RE.fullmatch(value)
    return match is not None and match.group(1) in KNOWN_MARKER_KINDS


# Fast, cheap substring markers for JSON logging's defensive precheck.
# Keep this next to SECRET_PATTERNS so adding a new prefix-detectable
# secret pattern also updates the logging precheck surface.
SECRET_PRECHECK_MARKERS: tuple[str, ...] = (
    "akia",
    "agpa",
    "aida",
    "aiza",
    "aipa",
    "anpa",
    "aroa",
    "asia",
    "clickhouse://",
    "eyj",
    "ghp_",
    "gho_",
    "ghr_",
    "ghs_",
    "ghu_",
    "github_pat_",
    "mssql://",
    "mongodb",
    "mysql://",
    "postgres://",
    "postgresql://",
    "private key",
    "redis://",
    "rediss://",
    "rk_live_",
    "rk_test_",
    "sk-",
    "sk_live_",
    "sk_test_",
    "sqlserver://",
    "pk_live_",
    "pk_test_",
    "whsec_",
    "xox",
)

# The logging precheck intentionally excludes generic ``bearer`` and ``basic``
# prose to avoid serializing every ordinary log payload; it searches the two
# exact expressions instead. Full output redaction still needs to consider
# both before applying the exact length- and alphabet-constrained regular
# expressions. Each marker is the bare word, because the expressions accept
# any whitespace after it (``bearer\t…`` used to bypass a ``"bearer "``
# marker).
REDACTION_SECRET_PRECHECK_MARKERS: tuple[str, ...] = (
    *SECRET_PRECHECK_MARKERS,
    "basic",
    "bearer",
)

LABELED_SECRET_PRECHECK_MARKERS: tuple[str, ...] = (
    "api-key",
    "api_key",
    "apikey",
    "auth",
    "password",
    "secret",
    "token",
)

REDACTION_SECRET_PRECHECK_RE = re.compile(
    "|".join(re.escape(marker) for marker in REDACTION_SECRET_PRECHECK_MARKERS)
)
LABELED_SECRET_PRECHECK_RE = re.compile(
    "|".join(re.escape(marker) for marker in LABELED_SECRET_PRECHECK_MARKERS)
)

LABELED_SECRET_PATTERN = re.compile(
    r"(?i)(['\"]?)(password|secret|token|api[_-]?key)\1"
    r"(\s*[:=]\s*)(['\"]?)([A-Za-z0-9_./+=-]{20,})\4"
)

#: ``Authorization: <scheme> <credential>``, ``authorization=<credential>`` and
#: ``auth: <credential>`` in free text. The label, the separator and the scheme
#: word are kept and the credential is redacted: any credential of eight or
#: more characters after a scheme word; after an ``authorization`` label alone,
#: a token of twelve or more characters holding a letter and a digit; and one
#: that looks like a secret (:func:`looks_like_secret_value`) otherwise.
#: Redaction only, like :data:`REDACTION_ONLY_SECRET_PATTERNS`:
#: ``auth: oauth2_client_credentials`` names a mode as often as it carries a
#: credential, and a finding must not guess which.
CREDENTIAL_LABEL_PATTERN = re.compile(
    r"(?i)(['\"]?)\b(proxy-authorization|authorization|auth)\1(\s*[:=]\s*)(['\"]?)"
    rf"({AUTH_SCHEME_PATTERN}\s+)?([A-Za-z0-9_./+=~-]+)\4"
)

#: ``Authorization: Digest username="…", realm="…", response="…"``: the
#: value is a list of ``key="…"`` pairs, not one token, so everything after
#: the scheme word to the end of the line is the credential.
DIGEST_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b((?:proxy-)?authorization['\"]?\s*[:=]\s*['\"]?digest\s+)([^\r\n]+)"
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


#: Field-name endings that make a key credential-bearing whatever its prefix.
#: ``access_token`` and ``x-api-key`` are ordinary spellings that no exact
#: vocabulary can enumerate, and a URL query or a request header is exactly
#: where they appear (PR #540 review). Matched against the same normalized
#: form :func:`is_sensitive_key` uses, so ``X-Access-Token`` and
#: ``access%5Ftoken`` both land on ``accesstoken``.
#:
#: Deliberately *not* a bare ``key``: ``sort_key`` and ``partition_key`` are
#: ordinary parameters, and claiming a credential where there is none is its
#: own false statement.
CREDENTIAL_KEY_SUFFIXES: tuple[str, ...] = (
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "passwd",
    "password",
    "pwd",
    "secret",
    "signature",
    "token",
)


def is_credential_key(value: object) -> bool:
    """Whether a *field name* names credential material.

    Wider than :func:`is_sensitive_key`, and used where a reader has to decide
    before writing an artifact — a URL query parameter, a connection header.
    The exact vocabulary answers first; the suffix rule catches the compound
    spellings it cannot enumerate. Over-matching here costs a redacted value
    that did not need it, which is the safe direction; under-matching publishes
    a credential.
    """
    if not isinstance(value, str):
        return False
    normalized = _normalized_key(value)
    if normalized in SENSITIVE_VALUE_KEYS:
        return True
    return normalized.endswith(CREDENTIAL_KEY_SUFFIXES)


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
    stripped = value.strip()
    if stripped.startswith("[REDACTED:") and _is_known_marker(stripped):
        return value

    redacted = value
    lowered = value.lower()
    if REDACTION_SECRET_PRECHECK_RE.search(lowered) is not None:
        for kind, pattern in SECRET_PATTERNS:
            redacted, count = pattern.subn(_marker(kind), redacted)
            if count and stats is not None:
                stats.record(path, kind, count)
    if LABELED_SECRET_PRECHECK_RE.search(lowered) is not None:
        redacted = _redact_labeled_secret(redacted, stats=stats, path=path)
        if "auth" in lowered:
            redacted = _redact_credential_label(redacted, stats=stats, path=path)
    for kind, pattern in REDACTION_ONLY_SECRET_PATTERNS:
        redacted, count = pattern.subn(_marker(kind), redacted)
        if count and stats is not None:
            stats.record(path, kind, count)
    if force_kind and redacted == value and value:
        if stats is not None:
            stats.record(path, force_kind)
        return _marker(force_kind)
    return redacted


def redact_data(
    value: Any,
    *,
    stats: RedactionStats | None = None,
    path: str = "$",
    force_kind: str | None = None,
) -> Any:
    return _redact_value(
        value,
        stats=stats,
        path=path,
        force_kind=force_kind,
        preserve_models=False,
    )


def _redact_value(
    value: Any,
    *,
    stats: RedactionStats | None,
    path: str,
    force_kind: str | None,
    preserve_models: bool,
) -> Any:
    if isinstance(value, BaseModel):
        if not preserve_models:
            return _redact_value(
                value.model_dump(mode="python"),
                stats=stats,
                path=path,
                force_kind=force_kind,
                preserve_models=False,
            )
        updates: dict[str, Any] = {}
        for field_name in type(value).model_fields:
            child_force_kind = (
                "sensitive_field" if is_sensitive_key(field_name) else force_kind
            )
            updates[field_name] = _redact_value(
                getattr(value, field_name),
                stats=stats,
                path=f"{path}.{_path_key(field_name)}",
                force_kind=child_force_kind,
                preserve_models=True,
            )
        copied = value.model_copy(update=updates, deep=True)
        if value.model_extra:
            # An ``extra="allow"`` model (``Finding``, ``Tool``,
            # ``SourceReference``) carries whatever a check plugin or a reader
            # attached beyond its declared fields, and every writer publishes
            # it. Walk it as ``redact_data`` walks a dict, names included, so
            # the copy a caller holds is as sanitised as the file on disk and
            # the privacy audit counts what was withheld (#1026).
            extra = _redact_value(
                dict(value.model_extra),
                stats=stats,
                path=path,
                force_kind=force_kind,
                preserve_models=True,
            )
            object.__setattr__(copied, "__pydantic_extra__", extra)
        return copied
    if isinstance(value, str):
        return redact_text(value, stats=stats, path=path, force_kind=force_kind)
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
            child_force_kind = (
                "sensitive_field" if is_sensitive_key(raw_key) else force_kind
            )
            redacted[key] = _redact_value(
                raw_item,
                stats=stats,
                path=child_path,
                force_kind=child_force_kind,
                preserve_models=preserve_models,
            )
        return redacted
    if isinstance(value, list):
        return [
            _redact_value(
                item,
                stats=stats,
                path=f"{path}[]",
                force_kind=force_kind,
                preserve_models=preserve_models,
            )
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _redact_value(
                item,
                stats=stats,
                path=f"{path}[]",
                force_kind=force_kind,
                preserve_models=preserve_models,
            )
            for item in value
        )
    if isinstance(value, set):
        return {
            _redact_value(
                item,
                stats=stats,
                path=f"{path}[]",
                force_kind=force_kind,
                preserve_models=preserve_models,
            )
            for item in value
        }
    return copy.deepcopy(value)


def sanitize_model[T: BaseModel](
    model: T, model_type: type[T], *, stats: RedactionStats, path: str
) -> T:
    redacted = _redact_value(
        model,
        stats=stats,
        path=path,
        force_kind=None,
        preserve_models=True,
    )
    if isinstance(redacted, model_type):
        return redacted
    return model_type.model_validate(redacted)


def sanitize_tools(tools: Iterable[Any], *, stats: RedactionStats) -> list[Any]:
    from agents_shipgate.core.domain import Tool

    return [sanitize_model(tool, Tool, stats=stats, path="tools[]") for tool in tools]


def sanitize_findings(findings: Iterable[Any], *, stats: RedactionStats) -> list[Any]:
    from agents_shipgate.schemas.report import Finding

    return [
        sanitize_model(finding, Finding, stats=stats, path="findings[]")
        for finding in findings
    ]


def sanitize_report(report: Any) -> Any:
    from agents_shipgate.schemas.report import ReadinessReport

    payload = sanitize_report_payload(report.model_dump(mode="python"))
    return ReadinessReport.model_validate(payload)


def sanitize_report_payload(payload: dict[str, Any]) -> dict[str, Any]:
    stats = RedactionStats()
    return redact_data(payload, stats=stats, path="$")


def sanitize_packet(packet: Any) -> Any:
    from agents_shipgate.schemas.packet import EvidencePacket

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
    from agents_shipgate.schemas.report import PrivacyAudit, RedactedPathSummary

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
    return (has_alpha or has_digit) and has_secret_alphabet


def redact_url_credentials(url: str) -> tuple[str, bool]:
    """Strip credential material a URL carries in userinfo or its query.

    ``https://user:pass@host/mcp?api_key=abc`` is a literal secret in source
    that the generic patterns do not catch: ``SECRET_PATTERNS``'s
    ``database_url`` rule only covers database schemes, and
    ``LABELED_SECRET_PATTERN`` only fires on values long enough to look like a
    key. A remote endpoint is published verbatim to reviewers, so it is
    normalized here — at the reader, before the value reaches any artifact —
    rather than relying on a downstream pattern to notice.

    Returns the normalized URL and whether anything was withheld. Callers
    record the second value as a limitation: a change confined to redacted
    bytes cannot be named, and that has to be visible rather than silent.

    Both markers use ``sensitive_field``, one of ``KNOWN_MARKER_KINDS``, so the
    later output-redaction pass recognizes an already-redacted value instead of
    re-processing it.
    """
    marker = _marker("sensitive_field")
    redacted = url
    changed = False
    scheme, sep, rest = redacted.partition("://")
    if sep:
        # The authority ends at the first ``/``, ``?`` or ``#`` — all three,
        # because a URL with a query and no path puts the query inside what a
        # ``/`` split would call the authority, and an ``@`` in a query value
        # (``?to=a@b.com``) would then be read as userinfo and the rest of the
        # URL thrown away.
        cut = min(
            (index for index in (rest.find(c) for c in "/?#") if index != -1),
            default=len(rest),
        )
        authority, tail = rest[:cut], rest[cut:]
        if "@" in authority:
            _, _, host = authority.rpartition("@")
            authority = f"{marker}@{host}"
            changed = True
        redacted = f"{scheme}{sep}{authority}{tail}"
    head, question, query = redacted.partition("?")
    if question:
        parts: list[str] = []
        for pair in query.split("&"):
            key, equals, _value = pair.partition("=")
            # Classify the *decoded* name. ``api%5Fkey`` is ``api_key`` to
            # every server that reads it, and matching the raw spelling let it
            # through untouched (PR #540 review).
            if equals and is_credential_key(unquote_plus(key)):
                parts.append(f"{key}={marker}")
                changed = True
            else:
                parts.append(pair)
        redacted = f"{head}{question}{'&'.join(parts)}"
    return redacted, changed


def _redact_labeled_secret(
    value: str,
    *,
    stats: RedactionStats | None,
    path: str,
) -> str:
    def replace(match: re.Match[str]) -> str:
        secret_value = match.group(5)
        if not looks_like_secret_value(secret_value):
            return match.group(0)
        if stats is not None:
            stats.record(path, "labeled_secret_value")
        return (
            f"{match.group(1)}{match.group(2)}{match.group(1)}"
            f"{match.group(3)}{match.group(4)}"
            f"{_marker('labeled_secret_value')}{match.group(4)}"
        )

    return LABELED_SECRET_PATTERN.sub(replace, value)


def _redact_credential_label(
    value: str,
    *,
    stats: RedactionStats | None,
    path: str,
) -> str:
    def replace_digest(match: re.Match[str]) -> str:
        if _is_known_marker(match.group(2).strip()):
            return match.group(0)
        if stats is not None:
            stats.record(path, "labeled_secret_value")
        return f"{match.group(1)}{_marker('labeled_secret_value')}"

    def replace(match: re.Match[str]) -> str:
        label, scheme, credential = match.group(2), match.group(5), match.group(6)
        if scheme:
            if len(credential) < 8:
                return match.group(0)
        elif not (
            looks_like_secret_value(credential)
            or (label.lower() != "auth" and _looks_like_short_token(credential))
        ):
            return match.group(0)
        if stats is not None:
            stats.record(path, "labeled_secret_value")
        return (
            f"{match.group(1)}{label}{match.group(1)}"
            f"{match.group(3)}{match.group(4)}{scheme or ''}"
            f"{_marker('labeled_secret_value')}{match.group(4)}"
        )

    value = DIGEST_CREDENTIAL_PATTERN.sub(replace_digest, value)
    return CREDENTIAL_LABEL_PATTERN.sub(replace, value)


def _looks_like_short_token(value: str) -> bool:
    """A credential an ``Authorization`` label carries without a scheme word."""

    return (
        len(value) >= 12
        and any(char.isalpha() for char in value)
        and any(char.isdigit() for char in value)
    )


def is_sensitive_key(value: object) -> bool:
    """Whether a key names credential material under the shared vocabulary.

    Public so a reader that has to decide *before* writing an artifact — a
    connection header, a URL query parameter — asks the same question the
    output-redaction pass asks, instead of keeping a second list that drifts.
    """
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
