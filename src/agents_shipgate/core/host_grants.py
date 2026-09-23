"""Deterministic, redacted coding-agent host-grant inventory and drift.

The module parses static files only. It never imports user code, executes a
helper, starts an MCP server, reads a credential value into an artifact, or
uses the network. ``repository`` scope is portable and deterministic;
``local_static`` additionally reads documented on-disk user/managed sources.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import posixpath
import re
import shlex
import stat
import sys
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import yaml
from pydantic import ValidationError

from agents_shipgate.core.boundary_registry import (
    BOUNDARY_ADAPTERS,
    CLAUDE_PLUGIN_DEFAULT_HOOKS,
    CLAUDE_PLUGIN_MARKETPLACE,
    is_claude_plugin_manifest_path,
    is_claude_plugin_marketplace_path,
    is_claude_plugin_reference_path,
    is_explicit_boundary_file_path,
    is_hook_declaration_file_name,
)
from agents_shipgate.core.host_boundary import (
    _is_wildcard_allow,
    _is_write,
    _normalize_workflow_keys,
    _server_map,
    _string_entries,
    _transport_hint,
    _trigger_names,
)
from agents_shipgate.core.host_input_failure import (
    MAX_FAILURE_TEXT,
    HostInputFailure,
    HostInventoryReadError,
    safe_failure_text,
)
from agents_shipgate.core.host_settings import (
    CLAUDE_LIST_SETTINGS,
    claude_setting_values,
    rate_claude_setting,
)
from agents_shipgate.core.instruction_structure import (
    classify_instruction,
    instruction_profile,
    unresolved_reason_is_invalid_syntax,
)
from agents_shipgate.core.jsonc import is_vscode_mcp_path, loads_jsonc
from agents_shipgate.core.permission_lattice import (
    scoped_risk,
    subsumes,
    whole_tool_risk,
)
from agents_shipgate.core.privacy import SENSITIVE_VALUE_KEYS, redact_text
from agents_shipgate.core.trust_roots import (
    IdentityBoundReadSession,
    IdentityReadBudget,
    IdentityReadBudgetExceeded,
    inspect_lexical_path_identity,
)
from agents_shipgate.schemas.host_grants import (
    HOST_GRANTS_BASELINE_SCHEMA_VERSION,
    HOST_GRANTS_DRIFT_SCHEMA_VERSION,
    HOST_GRANTS_INVENTORY_SCHEMA_VERSION,
    HostGrantsBaselineV2,
    HostGrantsBaselineV3,
    HostGrantsBaselineV4,
    HostGrantsBaselineV5,
    HostGrantsBaselineV6,
    HostGrantsBaselineV7,
    HostGrantsDriftV7,
    HostGrantsInventoryV7,
)

HOST_GRANTS_SCHEMA_VERSION = HOST_GRANTS_BASELINE_SCHEMA_VERSION
DEFAULT_BASELINE_FILE = Path(".agents-shipgate/host-grants.json")
INCOMPARABLE_BASELINE_REVIEW = (
    "Review the existing baseline and move, remove, or repair it before "
    "explicitly accepting the current host grants."
)

HostScope = Literal["repository", "local_static"]
MAX_HOST_CONFIG_BYTES = 1024 * 1024
MAX_HOST_BASELINE_BYTES = 16 * 1024 * 1024
MAX_HOST_REPOSITORY_ENTRIES = 100_000
MAX_HOST_STATIC_ENTRIES = 300_000
MAX_HOST_STATIC_TOTAL_BYTES = 64 * 1024 * 1024

_SECRET_KEY_MARKERS = frozenset(SENSITIVE_VALUE_KEYS) | {
    "authorization",
    "cookie",
    "credential",
    "passphrase",
    "private_key",
}
_CREDENTIAL_CONTAINER_KEYS = frozenset({"headers"})
_SECRET_ARG_RE = re.compile(
    r"(?i)(--?(?:api[-_]?key|auth|authorization|cookie|credential|password|secret|token))(=)(.+)"
)
_HEADER_SECRET_RE = re.compile(
    r"(?i)\b(authorization|proxy-authorization|cookie|set-cookie|x-api-key)"
    r"(\s*:\s*)([^\s'\";,\)]+)"
)
_BEARER_SECRET_RE = re.compile(r"(?i)\b(bearer)(\s+)([^\s'\";,\)]+)")
_ASSIGNMENT_SECRET_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|CREDENTIAL)[A-Z0-9_]*)"
    r"(\s*=\s*)([^\s'\";,\)]+)"
)
_SPACE_ARG_SECRET_RE = re.compile(
    r"(?i)(--?(?:api[-_]?key|auth|authorization|cookie|credential|password|secret|token))"
    r"(\s+)([^\s'\";,\)]+)"
)
_URL_RE = re.compile(r"(?:https?|wss?)://[^\s'\"<>]+")


@dataclass
class HostStaticParseCache:
    """Invocation-local cache proving each static source is read/parsed once."""

    max_entries: int = MAX_HOST_STATIC_ENTRIES
    max_total_bytes: int = MAX_HOST_STATIC_TOTAL_BYTES
    _reads: dict[tuple[str, str], tuple[str | None, str | None]] = field(
        default_factory=dict
    )
    _parses: dict[
        tuple[str, str], tuple[Any, str | None, str | None]
    ] = field(default_factory=dict)
    read_counts: dict[str, int] = field(default_factory=dict)
    parse_counts: dict[str, int] = field(default_factory=dict)
    _budget: IdentityReadBudget = field(init=False, repr=False)
    _sessions: dict[str, IdentityBoundReadSession] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _resource_bound_error: str | None = field(default=None, init=False, repr=False)
    _finished: bool = field(default=False, init=False, repr=False)
    _read_failures: dict[tuple[str, str], HostInputFailure] = field(
        default_factory=dict, init=False, repr=False,
    )
    input_failures: dict[str, HostInputFailure] = field(default_factory=dict, init=False)
    terminal_failure: HostInputFailure | None = field(default=None, init=False)

    @property
    def configured_limits(self) -> tuple[tuple[str, int], ...]:
        return (
            ("aggregate_entries", self.max_entries),
            ("aggregate_bytes", self.max_total_bytes),
            ("repository_entries", MAX_HOST_REPOSITORY_ENTRIES),
            ("per_file_bytes", MAX_HOST_CONFIG_BYTES),
        )

    def __post_init__(self) -> None:
        self._budget = IdentityReadBudget(
            max_entries=self.max_entries,
            max_total_bytes=self.max_total_bytes,
        )

    @staticmethod
    def _key(path: Path, containment_root: Path) -> tuple[str, str]:
        return (str(containment_root.absolute()), str(path.absolute()))

    def read(
        self, path: Path, *, containment_root: Path
    ) -> tuple[str | None, str | None]:
        key = self._key(path, containment_root)
        if key not in self._reads:
            display = str(path.absolute())
            self.read_counts[display] = self.read_counts.get(display, 0) + 1
            if self._resource_bound_error is not None:
                self._reads[key] = (None, self._resource_bound_error)
            else:
                try:
                    text, error, failure = _safe_read(
                        path,
                        containment_root=containment_root,
                        reader=self.reader_for(containment_root),
                        limits=self.configured_limits,
                    )
                    self._reads[key] = (text, error)
                    if failure is not None:
                        self._read_failures[key] = failure
                except IdentityReadBudgetExceeded:
                    self.terminal_failure = HostInputFailure(
                        reason="resource_bound_exceeded", phase="source_read",
                        source=str(path), limits=self.configured_limits,
                    )
                    self._read_failures[key] = self.terminal_failure
                    self._resource_bound_error = self.terminal_failure.summary()
                    self._reads[key] = (None, self._resource_bound_error)
                except (OSError, NotImplementedError, ValueError):
                    failure = HostInputFailure(
                        reason="input_unreadable", phase="source_read",
                        source=str(path), limits=self.configured_limits,
                    )
                    self._read_failures[key] = failure
                    self._reads[key] = (None, failure.summary())
        return self._reads[key]

    def read_issue(
        self, *, path: Path, containment_root: Path, source: str,
        host: str, kind: str, message: str,
    ) -> dict[str, Any]:
        """Bind the exact read's facts to its generated inventory issue identity."""
        failure = self._read_failures.get(self._key(path, containment_root))
        if failure is not None:
            failure = replace(failure, source=source)
            message = failure.summary() + " " + failure.recovery()
        issue = _inventory_issue(
            kind=kind, host=host, source=source, message=message, blocking=True,
        )
        if failure is not None:
            self.input_failures[issue["issue_id"]] = failure
        return issue

    @property
    def resource_bound_error(self) -> str | None:
        return self._resource_bound_error

    def finish(self) -> None:
        """Perform one final exact-name/identity pass for every read root."""

        if self._finished:
            return
        if self._resource_bound_error is not None:
            raise IdentityReadBudgetExceeded(self._resource_bound_error)
        for key in sorted(self._sessions):
            try:
                self._sessions[key].finish()
            except IdentityReadBudgetExceeded:
                self.terminal_failure = HostInputFailure(
                    reason="resource_bound_exceeded", phase="snapshot_validation",
                    source=key, limits=self.configured_limits,
                )
                self._resource_bound_error = self.terminal_failure.summary()
                raise
            except (OSError, NotImplementedError, ValueError):
                self.terminal_failure = HostInputFailure(
                    reason="snapshot_validation_failed", phase="snapshot_validation",
                    source=key,
                )
                raise
        self._finished = True

    def reader_for(self, containment_root: Path) -> IdentityBoundReadSession:
        """Return the shared-budget reader for one lexical containment root."""

        key = str(containment_root.absolute())
        session = self._sessions.get(key)
        if session is None:
            session = IdentityBoundReadSession(
                containment_root,
                budget=self._budget,
            )
            self._sessions[key] = session
        return session

    def parse(
        self, path: Path, *, containment_root: Path
    ) -> tuple[Any, str | None, str | None]:
        key = self._key(path, containment_root)
        if key in self._parses:
            return self._parses[key]
        text, read_error = self.read(path, containment_root=containment_root)
        if read_error is not None:
            result = (None, "unreadable", read_error)
            self._parses[key] = result
            return result
        assert text is not None
        display = str(path.absolute())
        self.parse_counts[display] = self.parse_counts.get(display, 0) + 1
        try:
            if path.suffix == ".toml":
                data = tomllib.loads(text)
            elif path.suffix in {".yml", ".yaml"}:
                data = yaml.safe_load(text)
            elif is_vscode_mcp_path(path.as_posix()):
                # VS Code reads `mcp.json` as JSON with comments (#659).
                data = loads_jsonc(text)
            else:
                data = json.loads(text)
        except (tomllib.TOMLDecodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
            result = (
                None,
                "parse_failed",
                f"static parser rejected this {path.suffix.lstrip('.')} file "
                f"({exc.__class__.__name__})",
            )
        else:
            result = (data, None, None)
        self._parses[key] = result
        return result


@dataclass(frozen=True)
class PluginScopeFacts:
    """The plugin reference graph one inventory was read through (#808).

    Private reader facts, never published. They are what bounds a
    plugin-reference limit to a directory: every hook file a plugin can select
    lies under its directory, because a reference that leaves it is refused
    rather than followed (#714), so a limit raised there can only hide grants
    published under that directory — except inline hooks a marketplace entry
    outside it declares for that plugin, which ``inline_roots`` names.
    """

    #: ``{issue id: the plugin directories whose references raised it}``;
    #: ``None`` in the set when the reference named a path outside its plugin,
    #: so no directory bounds what it hides.
    issue_roots: Mapping[str, frozenset[str | None]] = field(default_factory=dict)
    #: ``{inline hook source, as its grants publish it: its plugin directory}``.
    inline_roots: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class HostBoundarySnapshot:
    """Reusable normalized snapshot for audit/check/verify projections."""

    inventory: dict[str, Any]
    cache: HostStaticParseCache
    input_failures: dict[str, HostInputFailure] = field(default_factory=dict)
    #: Issues raised while reading a plugin manifest, a marketplace or a hook
    #: file only a plugin selects (#714). Private reader facts, never
    #: published: `check`, which cannot name a limit, leaves them out of its
    #: completeness, except on a changed file that holds hooks of a plugin
    #: the project settings enable (#809).
    plugin_reference_issue_ids: frozenset[str] = frozenset()
    #: The repository paths of the files that declare hooks of a plugin this
    #: repository's project settings enable (#809): every hook file such a
    #: plugin selects, and a manifest or marketplace whose inline hooks it
    #: loads. The same selection publishes those hooks as
    #: ``project_enabled_plugin``. A private reader fact, never published:
    #: `check` routes a change to one of these files to protected-surface
    #: review, as it routes a settings layer.
    enabled_plugin_hook_sources: frozenset[str] = frozenset()
    #: The hook files such a plugin selects that the reader does not open
    #: (#809): a name other than `hooks.json` or `<name>-hooks.json`, or a
    #: directory the walk skips. The host loads their hooks and the inventory
    #: names each one as a limit, so a change to one is input `check` could
    #: not read, never a change it may allow. Private, never published.
    enabled_plugin_unread_hook_files: frozenset[str] = frozenset()
    #: The directory each of those issues is bounded by (#808): what `diff`
    #: and `verify` may leave uncompared while comparing the rest.
    plugin_scopes: PluginScopeFacts = field(default_factory=PluginScopeFacts)


@dataclass(frozen=True)
class EnabledPluginHookFiles:
    """The files holding hooks of a plugin the project settings enable (#809).

    The two private snapshot facts, gathered across the sides of a change a
    caller could read, so `check` and `verify` decide from the same evidence.
    """

    #: Files the reader opened that declare such hooks
    #: (``HostBoundarySnapshot.enabled_plugin_hook_sources``).
    sources: frozenset[str] = frozenset()
    #: Files such a plugin selects whose hooks the reader did not read
    #: (``HostBoundarySnapshot.enabled_plugin_unread_hook_files``).
    unread: frozenset[str] = frozenset()

    @classmethod
    def of(cls, snapshot: HostBoundarySnapshot) -> EnabledPluginHookFiles:
        return cls(
            sources=snapshot.enabled_plugin_hook_sources,
            unread=snapshot.enabled_plugin_unread_hook_files,
        )

    def union(self, other: EnabledPluginHookFiles) -> EnabledPluginHookFiles:
        return EnabledPluginHookFiles(
            sources=self.sources | other.sources, unread=self.unread | other.unread
        )


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _stable_id(prefix: str, *parts: object) -> str:
    return f"{prefix}_{_sha([str(part) for part in parts])[:24]}"


def _is_secret_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    normalized = re.sub(r"[^a-z0-9_]+", "", key.lower())
    return any(marker in normalized for marker in _SECRET_KEY_MARKERS)


def _sanitize_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<redacted-url>"
    if parsed.scheme not in {"http", "https", "ws", "wss", "sse"}:
        return value
    hostname = parsed.hostname or ""
    netloc = hostname
    try:
        port = parsed.port
    except ValueError:
        port = None
        netloc = "<invalid-host>"
    if port is not None:
        netloc = f"{hostname}:{port}"
    path = "/<redacted-path>" if parsed.path not in {"", "/"} else parsed.path
    return urlunsplit((parsed.scheme, netloc, path, "", ""))


def _sanitize_sensitive_string(value: str) -> str:
    value = _URL_RE.sub(lambda match: _sanitize_url(match.group(0)), value)
    value = _HEADER_SECRET_RE.sub(r"\1\2<redacted>", value)
    value = _BEARER_SECRET_RE.sub(r"\1\2<redacted>", value)
    value = _ASSIGNMENT_SECRET_RE.sub(r"\1\2<redacted>", value)
    value = _SPACE_ARG_SECRET_RE.sub(r"\1\2<redacted>", value)
    match = _SECRET_ARG_RE.fullmatch(value)
    if match:
        return f"{match.group(1)}=<redacted>"
    return value


_PATH_REDACTION_MARKER = re.compile(r"\[REDACTED:[^\]]+\]|<redacted>")


def public_host_path(source: str) -> str:
    """The location a host inventory publishes for one exact source (#590).

    Inside the reader a path is identity; outside it, it is a published string.
    Credential-shaped bytes in a directory or file name must not reach that
    string, so each path component is redacted on its own: a greedy secret
    pattern can never swallow the separator and the file name after it, which
    instruction profiles and precedence ranks read.

    Whenever anything was redacted, the marker carries a short digest of the
    exact source, so two sources that redact alike stay two locations and one
    cannot inherit the other's grants, coverage or recovery evidence. A redacted
    path is a label, not a locator: reads and ids always use the exact source.
    """

    components = source.split("/")
    redacted = [_sanitize_sensitive_string(redact_text(part) or "") for part in components]
    if redacted == components:
        return source
    digest = hashlib.sha256(source.encode("utf-8", "surrogateescape")).hexdigest()[:12]
    marked = [
        _PATH_REDACTION_MARKER.sub(lambda match: f"{match.group(0)}~{digest}", part)
        for part in redacted
    ]
    if marked == redacted:
        # A pattern rewrote a component without leaving a marker to stamp.
        index = next(i for i, (a, b) in enumerate(zip(components, redacted, strict=True)) if a != b)
        marked[index] = f"{marked[index]}~{digest}"
    return "/".join(marked)


def _redact_secret_values(value: Any, *, parent_key: str | None = None) -> Any:
    if parent_key is not None and _is_secret_key(parent_key):
        return "<redacted>"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, inner in value.items():
            key_text = str(key)
            if key_text == "policyHelper":
                result[key_text] = "<excluded-dynamic-helper>"
            elif _is_secret_key(key_text):
                result[key_text] = "<redacted>"
            elif parent_key in _CREDENTIAL_CONTAINER_KEYS:
                result[key_text] = "<redacted>"
            elif key_text in {"env", "headers"} and isinstance(inner, dict):
                result[key_text] = {
                    str(container_key): "<redacted>"
                    for container_key in inner
                }
            else:
                result[key_text] = _redact_secret_values(inner, parent_key=key_text)
        return result
    if isinstance(value, list):
        redacted: list[Any] = []
        redact_next = False
        for item in value:
            if redact_next:
                redacted.append("<redacted>")
                redact_next = False
                continue
            if isinstance(item, str):
                match = _SECRET_ARG_RE.fullmatch(item)
                if match:
                    redacted.append(f"{match.group(1)}={match.group(3) and '<redacted>'}")
                    continue
                if item.lower().lstrip("-").replace("-", "_") in _SECRET_KEY_MARKERS:
                    redact_next = True
            redacted.append(_redact_secret_values(item, parent_key=parent_key))
        return redacted
    if isinstance(value, str):
        return _sanitize_sensitive_string(value)
    return value


def _url_capability_parts(value: Any, *, parent_key: str | None = None) -> list[dict[str, Any]]:
    """The query of each URL, for the change digest only.

    Published fields drop a URL's query because it carries tokens and project
    identifiers. The change digest must still see it: a Supabase server's
    `read_only=true` and `features=…` decide which tools an agent gets, and
    removing one produced no row when only the redacted URL was hashed (#723).
    Nothing returned here is published; it is hashed with the redacted config.

    The path stays out on purpose. A webhook-style path is itself the secret,
    and rotating one must stay quiet
    (`test_drift_all_env_header_value_rotation_quiet_but_key_addition_fires`).
    A digest cannot tell a capability path (`/read` → `/admin`) from a secret
    one, so a path-only change remains unseen; that limit is recorded on #723.

    It walks what `_redact_secret_values` keeps: secret keys, `env` and
    `headers` contribute nothing, and a secret-named query parameter
    contributes its name, never its value. A URL without a query adds nothing,
    so a command server, a bare host or a path-only URL keeps its earlier digest.
    """

    if parent_key is not None and _is_secret_key(parent_key):
        return []
    parts: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, inner in sorted(value.items(), key=lambda item: str(item[0])):
            key_text = str(key)
            if (
                key_text == "policyHelper"
                or _is_secret_key(key_text)
                or parent_key in _CREDENTIAL_CONTAINER_KEYS
                or key_text in {"env", "headers"}
            ):
                continue
            parts.extend(_url_capability_parts(inner, parent_key=key_text))
        return parts
    if isinstance(value, list):
        skip_next = False
        for item in value:
            if skip_next:
                skip_next = False
                continue
            if isinstance(item, str) and (
                _SECRET_ARG_RE.fullmatch(item)
                or item.lower().lstrip("-").replace("-", "_") in _SECRET_KEY_MARKERS
            ):
                skip_next = not _SECRET_ARG_RE.fullmatch(item)
                continue
            parts.extend(_url_capability_parts(item, parent_key=parent_key))
        return parts
    if isinstance(value, str):
        for match in _URL_RE.finditer(value):
            try:
                parsed = urlsplit(match.group(0))
                query = parse_qsl(parsed.query, keep_blank_values=True)
            except ValueError:
                parts.append({"url": "<unparsable-url>", "raw": match.group(0)})
                continue
            if not query:
                continue
            parts.append({
                "url": _sanitize_url(match.group(0)),
                "query": sorted(
                    [name, "<secret>" if _is_secret_key(name) else item]
                    for name, item in query
                ),
            })
    return parts


def redacted_config_sha256(config: Any) -> str:
    redacted = _redact_secret_values(config)
    url_parts = _url_capability_parts(config)
    if not url_parts:
        return _sha(redacted)
    return _sha({"redacted": redacted, "url_capability": url_parts})


def published_setting_value(grant: dict[str, Any]) -> Any:
    """The value a setting grant was read with, as far as its published fields say (#827).

    A `permission_mode` or `sandbox` grant publishes its value as text, and a
    JSON `true` and the string `"True"` both publish `True`. Its digest is of
    the value itself, so a candidate the text allows — a boolean, `null`, a
    number, a list or an object — is the value exactly when it reproduces that
    digest. Anything else is the published text. Nothing is read beyond the
    grant, so a grant from a saved baseline answers the same way.
    """

    text = grant.get("value")
    setting = grant.get("setting")
    if not isinstance(text, str) or not isinstance(setting, str):
        return text
    candidates: list[Any] = [True, False, None]
    try:
        candidates.append(json.loads(text))
    except ValueError:
        pass
    for candidate in candidates:
        if isinstance(candidate, str):
            continue
        rendered = (
            _canonical(candidate) if isinstance(candidate, (dict, list)) else str(candidate)
        )
        if rendered == text and (
            redacted_config_sha256({setting: candidate}) == grant.get("config_sha256")
        ):
            return candidate
    return text


def _display_path(path: Path, *, root: Path, home: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        pass
    try:
        return f"~/{path.resolve().relative_to(home).as_posix()}"
    except ValueError:
        return str(path)


def _issue_source_label(source: str) -> str:
    """The bounded, single-line form of an issue's source, and its identity (#590).

    Built on :func:`public_host_path` and deliberately not re-run through the
    whole-string sanitizers in ``safe_failure_text``: a greedy assignment
    pattern would swallow the digest and the file name after the redacted
    component, and two unreadable sources would print as one.

    Every lossy step leaves a digest of the exact source. Redaction does so in
    :func:`public_host_path`. Replacing control characters and bounding the
    length do so here, at the end. So two sources that display alike never
    share a label, and the issue id can bind the label without merging them.
    """

    public = public_host_path(source)
    label = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", public)
    if label == public and len(label) <= MAX_FAILURE_TEXT:
        return label
    suffix = "~" + hashlib.sha256(source.encode("utf-8", "surrogateescape")).hexdigest()[:12]
    if len(label) + len(suffix) > MAX_FAILURE_TEXT:
        label = label[: MAX_FAILURE_TEXT - len(suffix) - 1] + "…"
    return label + suffix


def _inventory_issue(
    *, kind: str, host: str, source: str, message: str, blocking: bool
) -> dict[str, Any]:
    shown = _issue_source_label(source)
    message = safe_failure_text(message)
    return {
        "issue_id": _stable_id("host_issue", kind, host, shown, message),
        "kind": kind,
        "host": host,
        "source": shown,
        "message": message,
        "blocking": blocking,
    }


def _artifact(
    *, host: str, scope: HostScope, source: str, kind: str, status: str, data: Any = None,
    resolved_through: tuple[str, ...] = (),
) -> dict[str, Any]:
    artifact = {
        "artifact_id": _stable_id("host_artifact", host, scope, source, kind),
        "host": host,
        "scope": scope,
        "path": public_host_path(source),
        "kind": kind,
        "parse_status": status,
        "redacted_sha256": redacted_config_sha256(data) if data is not None else None,
    }
    if resolved_through:
        # The in-tree paths a read followed from a linked boundary path (#700):
        # a retargeted link is then a changed artifact, not a silent substitution.
        artifact["resolved_through"] = [public_host_path(hop) for hop in resolved_through]
    return artifact


def _grant_base(
    *, host: str, scope: HostScope, source: str, kind: str, identity: str,
    config: Any, access: str, risk: str,
) -> dict[str, Any]:
    return {
        "grant_id": _stable_id("host_grant", host, scope, source, kind, identity),
        "host": host,
        "scope": scope,
        "source": public_host_path(source),
        "kind": kind,
        "config_sha256": redacted_config_sha256(config),
        "access": access,
        "risk": risk,
    }


def _safe_read(
    path: Path,
    *,
    containment_root: Path,
    reader: IdentityBoundReadSession | None = None,
    limits: tuple[tuple[str, int], ...] = (),
) -> tuple[str | None, str | None, HostInputFailure | None]:
    lexical_root = Path(os.path.abspath(os.path.normpath(os.fspath(containment_root))))
    lexical_path = Path(os.path.abspath(os.path.normpath(os.fspath(path))))
    try:
        relative = lexical_path.relative_to(lexical_root)
    except ValueError:
        failure = HostInputFailure(
            reason="input_unreadable", phase="source_read", source=str(path),
        )
        return None, failure.summary(), failure
    owns_reader = reader is None
    if reader is None:
        reader = IdentityBoundReadSession(
            lexical_root,
            max_entries=MAX_HOST_STATIC_ENTRIES,
            max_total_bytes=MAX_HOST_CONFIG_BYTES,
        )
    try:
        raw = reader.read_bytes(relative, max_bytes=MAX_HOST_CONFIG_BYTES)
        if owns_reader:
            reader.finish()
    except IdentityReadBudgetExceeded:
        if not owns_reader:
            raise
        failure = HostInputFailure(
            reason="resource_bound_exceeded", phase="source_read",
            source=str(path), limits=limits,
        )
        return None, failure.summary(), failure
    except (OSError, NotImplementedError, ValueError):
        failure = HostInputFailure(
            reason="input_unreadable", phase="source_read",
            source=str(path), limits=limits,
        )
        return None, failure.summary(), failure
    try:
        return raw.decode("utf-8", errors="strict"), None, None
    except UnicodeDecodeError:
        failure = HostInputFailure(
            reason="input_unreadable", phase="utf8_decode", source=str(path),
        )
        return None, failure.summary(), failure


def _claude_permission_shape_error(data: Any) -> str | None:
    """Validate only the known permission containers; unknown settings stay allowed."""

    if not isinstance(data, dict):
        return "settings must be an object"
    if "permissions" not in data:
        return None
    permissions = data["permissions"]
    if not isinstance(permissions, dict):
        return "permissions must be an object"
    for disposition in ("allow", "deny", "ask"):
        if disposition not in permissions:
            continue
        rules = permissions[disposition]
        if not isinstance(rules, list) or any(
            not isinstance(rule, str) or not rule.strip() for rule in rules
        ):
            return f"permissions.{disposition} must be an array of non-empty strings"
    return None


def _load_structured(
    *, path: Path, source: str, host: str, kind: str, scope: HostScope,
    containment_root: Path, cache: HostStaticParseCache,
    artifacts: list[dict[str, Any]], issues: list[dict[str, Any]],
    resolved_through: tuple[str, ...] = (),
) -> Any:
    data, error_kind, error_message = cache.parse(
        path, containment_root=containment_root
    )
    if error_kind is not None:
        assert error_message is not None
        issues.append(cache.read_issue(
            path=path, containment_root=containment_root,
            kind=error_kind,
            host=host,
            source=source,
            message=error_message,
        ))
        artifacts.append(_artifact(
            host=host, scope=scope, source=source, kind=kind, status="failed",
            resolved_through=resolved_through,
        ))
        return None
    if host == "claude-code" and kind in {"config", "hooks"}:
        # A hook file is not a settings file: no host reads `permissions`
        # from it, so only its top-level shape is checked (#714).
        shape_error = (
            _claude_permission_shape_error(data)
            if kind == "config"
            else None if isinstance(data, dict) else "a hook file must be an object"
        )
        if shape_error is not None:
            issues.append(_inventory_issue(
                kind="unsupported", host=host, source=source,
                message=f"Cannot interpret Claude settings: {shape_error}; repair the field and rerun the audit.",
                blocking=True,
            ))
            artifacts.append(_artifact(
                host=host, scope=scope, source=source, kind=kind, status="unsupported",
                resolved_through=resolved_through,
            ))
            return None
    artifacts.append(_artifact(
        host=host, scope=scope, source=source, kind=kind, status="parsed", data=data,
        resolved_through=resolved_through,
    ))
    return data


def _endpoint(server: Any) -> str | None:
    if not isinstance(server, dict):
        return None
    url = server.get("url") or server.get("serverUrl")
    if isinstance(url, str):
        return _sanitize_url(url)
    command = server.get("command")
    if isinstance(command, str):
        first = command.strip().split(maxsplit=1)[0] if command.strip() else ""
        return _sanitize_sensitive_string(Path(first).name or first) or None
    if isinstance(command, list) and command and isinstance(command[0], str):
        return _sanitize_sensitive_string(Path(command[0]).name or command[0])
    return None


#: VS Code's prompted-input reference, e.g. `"API_KEY": "${input:apiKey}"`.
_VSCODE_INPUT_REF = re.compile(r"\$\{input:([^}]+)\}")
#: The documented top level of `.vscode/mcp.json` (#731). Anything else is not
#: modeled, so the file's coverage stays partial rather than silently complete.
_VSCODE_MCP_TOP_LEVEL = frozenset({"servers", "inputs", "sandbox"})


def _vscode_mcp_extras(
    data: Any, *, scope: HostScope, source: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """What `.vscode/mcp.json` says beyond its server set (#731).

    `sandbox` and a server's `sandboxEnabled` decide whether a server runs
    isolated, so they are sandbox grants: turning one off widens what the server
    can reach. `envFile` names environment the file does not contain; it is
    recorded as a non-blocking limit on that server. `inputs` holds prompt
    definitions only, and their values never appear in the file.
    """

    grants: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    if not isinstance(data, dict):
        return grants, issues
    sandbox = data.get("sandbox")
    if isinstance(sandbox, dict):
        for setting, value in sorted(sandbox.items()):
            grants.append(_setting_grant(
                host="vscode", scope=scope, source=source, kind="sandbox",
                setting=f"sandbox.{setting}", value=value, access="unknown", risk="high",
            ))
    for name, server in sorted(_server_map(data).items()):
        if not isinstance(server, dict):
            continue
        if "sandboxEnabled" in server:
            enabled = server["sandboxEnabled"]
            grants.append(_setting_grant(
                host="vscode", scope=scope, source=source, kind="sandbox",
                setting=f"servers.{name}.sandboxEnabled", value=enabled,
                access="admin" if enabled is False else "unknown", risk="high",
            ))
        if "envFile" in server:
            issues.append(_inventory_issue(
                kind="unsupported", host="vscode", source=source,
                message=(
                    f"server {name!r} reads environment from envFile, whose "
                    "contents a static audit does not read"
                ),
                blocking=False,
            ))
    for key in sorted(str(item) for item in data if str(item) not in _VSCODE_MCP_TOP_LEVEL):
        issues.append(_inventory_issue(
            kind="unsupported", host="vscode", source=source,
            message=f"top-level key {key!r} is not part of the modeled .vscode/mcp.json shape",
            blocking=True,
        ))
    return grants, issues


def _mcp_grants(
    data: Any, *, host: str, scope: HostScope, source: str
) -> list[dict[str, Any]]:
    grants: list[dict[str, Any]] = []
    for name, server in sorted(_server_map(data).items()):
        config = server if isinstance(server, dict) else {"value": server}
        digested = config
        if host == "vscode" and isinstance(server, dict):
            # `sandboxEnabled` is its own sandbox grant (#731), so one edit is one
            # row. An `${input:…}` reference's name enters the digest; the value
            # VS Code prompts for never exists in the file.
            digested = {key: value for key, value in config.items() if key != "sandboxEnabled"}
            refs = sorted(set(_VSCODE_INPUT_REF.findall(json.dumps(config, sort_keys=True, default=str))))
            if refs:
                digested = {**digested, "input_refs": refs}
        base = _grant_base(
            host=host, scope=scope, source=source, kind="mcp_server",
            identity=str(name), config=digested, access="external", risk="high",
        )
        env = config.get("env") if isinstance(config.get("env"), dict) else {}
        headers = config.get("headers") if isinstance(config.get("headers"), dict) else {}
        grants.append({
            **base,
            "server": str(name),
            "transport": _transport_hint(config),
            "endpoint": _endpoint(config),
            "env_keys": sorted(str(key) for key in env),
            "header_keys": sorted(str(key) for key in headers),
        })
    return grants


def _permission_rule_grants(
    permissions: Any, *, host: str, scope: HostScope, source: str
) -> list[dict[str, Any]]:
    if not isinstance(permissions, dict):
        return []
    grants: list[dict[str, Any]] = []
    for disposition in ("allow", "ask", "deny"):
        for raw_rule in sorted(_string_entries(permissions.get(disposition))):
            rule = _sanitize_sensitive_string(raw_rule)
            wildcard = disposition == "allow" and _is_wildcard_allow(raw_rule)
            if wildcard:
                # Not every whole-tool grant reaches the same thing. Rating
                # `Read(**)` beside `Bash(*)` put four of eight grants at
                # `critical` on an ordinary repository, and a severity
                # column that cries critical at reading files is one a
                # reviewer stops reading (#657).
                access, risk = whole_tool_risk(raw_rule)
            elif disposition == "allow":
                access, risk = scoped_risk(raw_rule)
            else:
                access, risk = "none", "low"
            grants.append({
                **_grant_base(
                    host=host, scope=scope, source=source, kind="permission_rule",
                    identity=f"{disposition}:{rule}", config={"disposition": disposition, "rule": rule},
                    access=access, risk=risk,
                ),
                "disposition": disposition,
                "rule": rule,
                "wildcard": wildcard,
            })
    return grants


def _setting_grant(
    *, host: str, scope: HostScope, source: str, kind: str, setting: str,
    value: Any, access: str = "unknown", risk: str = "medium",
) -> dict[str, Any]:
    redacted_value = _redact_secret_values(value, parent_key=setting)
    rendered = (
        _canonical(redacted_value)
        if isinstance(redacted_value, (dict, list))
        else str(redacted_value)
    )
    key = "setting"
    if kind == "additional_path":
        key = "path"
    return {
        **_grant_base(
            host=host, scope=scope, source=source, kind=kind,
            identity=f"{setting}:{rendered}",
            config={setting: redacted_value},
            access=access,
            risk=risk,
        ),
        key: setting if kind == "additional_path" else setting,
        **({"value": rendered} if kind in {"permission_mode", "sandbox"} else {}),
    }


#: Why a hook grant is in the inventory (#714). Parsing a hook file proves the
#: file exists; it does not prove a host loads it.
#:
#: * ``host_configuration`` — declared in a file the host documents loading for
#:   this scope (Claude Code settings layers, Codex `hooks.json`).
#: * ``project_enabled_plugin`` — selected by a Claude Code plugin that this
#:   repository's own project settings enable (`enabledPlugins`), from a
#:   marketplace those settings register (`extraKnownMarketplaces`) as a
#:   `directory` or `file` source inside this repository, which lists the
#:   plugin with a source inside it. Claude Code leaves only a plugin from an
#:   external source waiting for a manual install, so this one loads once the
#:   folder is trusted, and its hooks are published as loaded.
#: * ``plugin_selected`` — a hook file or inline object that a Claude Code
#:   plugin manifest or marketplace entry in this repository selects, without
#:   that enablement. Whether the plugin is installed or enabled is external
#:   state, so selection is established and loading is not.
#: * ``declared_only`` — a hook file nothing in this repository selects.
#: * ``unestablished`` — a hook-file grant published with none of the plugin
#:   signatures below. Never produced by this reader; nothing about its
#:   selection is claimed.
HookLoadingBasis = Literal[
    "host_configuration", "project_enabled_plugin", "plugin_selected", "declared_only",
    "unestablished",
]

#: The `access`/`risk` pair the reader publishes for each basis it produces.
#: The pairs differ so the basis survives in a saved baseline without a new
#: field: `medium` is conditional authority, a command that runs only once
#: the plugin is enabled. A project-enabled plugin's hook shares the settings
#: pair because both are hooks the host loads for this project. The two are
#: told apart by `source`: a plugin hook's is a hook file, a manifest or a
#: marketplace entry, never a settings layer.
_HOOK_ACCESS_BY_BASIS: dict[str, tuple[str, str]] = {
    "host_configuration": ("execute", "high"),
    "project_enabled_plugin": ("execute", "high"),
    "plugin_selected": ("execute", "medium"),
    # Nothing establishes what this file can do, because nothing establishes
    # that anything runs it. Not "none": the declaration is kept visible.
    "declared_only": ("unknown", "unknown"),
}

#: The bases whose hooks a host loads for this project. Only these earn an
#: expansion signal (#714).
LOADED_HOOK_BASES: frozenset[str] = frozenset({"host_configuration", "project_enabled_plugin"})


def _hooks_grants(
    data: Any, *, host: str, scope: HostScope, source: str,
    basis: HookLoadingBasis = "host_configuration",
) -> list[dict[str, Any]]:
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return []
    access, risk = _HOOK_ACCESS_BY_BASIS[basis]
    return [
        {
            **_grant_base(
                host=host, scope=scope, source=source, kind="hook",
                identity=str(event), config=config, access=access, risk=risk,
            ),
            "event": str(event),
        }
        for event, config in sorted(hooks.items())
    ]


def hook_loading_basis(grant: dict[str, Any]) -> HookLoadingBasis:
    """The loading basis of one published hook grant (#714).

    Read back from the published fields, so a saved baseline, a drift payload
    and a capability row classify one grant the same way. A Claude Code hook
    whose source is a hook file, a plugin manifest or a marketplace entry,
    rather than a settings layer, is classified only by the exact pair the
    reader publishes for a plugin basis. A grant carrying none of those pairs
    was not recorded with a basis, so it is ``unestablished`` and no
    selection is inferred from its name.

    One pair is shared with history. `1.0.0` recorded every hook file as
    `execute`/`high`, which is the ``project_enabled_plugin`` pair, and no
    published field is free to tell them apart without a schema change. The
    engine reads a basis only from the current side of a change: the wording
    of an added or changed row, and the expansion signal. A removal, the only
    row described from a baseline's grant, names no basis. So nothing a
    `1.0.0` baseline holds is described as an enabled plugin's hook.
    """

    source = str(grant.get("source") or "").replace("\\", "/").split("#", 1)[0]
    pair = (grant.get("access"), grant.get("risk"))
    if grant.get("host") == "claude-code" and is_claude_plugin_reference_path(source):
        for basis in ("project_enabled_plugin", "plugin_selected", "declared_only"):
            if pair == _HOOK_ACCESS_BY_BASIS[basis]:
                return basis  # type: ignore[return-value]
        return "unestablished"
    if grant.get("access") == "unknown":
        return "unestablished"
    return "host_configuration"


def _claude_grants(data: Any, *, scope: HostScope, source: str) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    grants = _permission_rule_grants(data.get("permissions"), host="claude-code", scope=scope, source=source)
    permissions = data.get("permissions") if isinstance(data.get("permissions"), dict) else {}
    # One rating per value, shared with `check` and the rows (#827).
    for item in claude_setting_values(data):
        rating = rate_claude_setting(item.setting, item.value)
        grants.append(_setting_grant(
            host="claude-code", scope=scope, source=source, kind="permission_mode",
            setting=item.setting, value=item.value, access=rating.access, risk=rating.risk,
        ))
    for path in sorted(_string_entries(permissions.get("additionalDirectories")) + _string_entries(data.get("additionalDirectories"))):
        projected_path = _privacy_projected_path(path)
        grant = _grant_base(
            host="claude-code", scope=scope, source=source, kind="additional_path",
            identity=projected_path,
            config={"path": projected_path},
            access="write",
            risk="high",
        )
        grants.append({**grant, "path": projected_path})
    sandbox = data.get("sandbox")
    if isinstance(sandbox, dict):
        for setting, value in sorted(sandbox.items()):
            grants.append(_setting_grant(
                host="claude-code", scope=scope, source=source, kind="sandbox",
                setting=f"sandbox.{setting}", value=value,
                access="admin" if setting in {"enabled", "allowUnsandboxedCommands"} else "unknown",
                risk="high",
            ))
    plugins = data.get("enabledPlugins")
    if isinstance(plugins, dict):
        for name, enabled in sorted(plugins.items()):
            grants.append({
                **_grant_base(
                    host="claude-code", scope=scope, source=source, kind="plugin_or_app",
                    identity=str(name), config={"enabled": enabled}, access="execute", risk="high",
                ),
                "name": str(name), "enabled": bool(enabled),
            })
    # `enabledPlugins` entries install from these, so a marketplace added or
    # re-pointed changes what an enabled plugin runs (#720). Named with a
    # prefix: a marketplace and a plugin never compete for one precedence key.
    marketplaces = data.get("extraKnownMarketplaces")
    if isinstance(marketplaces, dict):
        for name, config in sorted(marketplaces.items()):
            label = f"marketplace:{name}"
            grants.append({
                **_grant_base(
                    host="claude-code", scope=scope, source=source, kind="plugin_or_app",
                    identity=label, config=config, access="external", risk="high",
                ),
                "name": label, "enabled": None,
            })
    grants.extend(_hooks_grants(data, host="claude-code", scope=scope, source=source))
    return grants


def _privacy_projected_path(value: str) -> str:
    """Keep grant identity useful without publishing machine-local paths."""

    expanded = Path(value).expanduser()
    if not expanded.is_absolute():
        return Path(os.path.normpath(value)).as_posix()
    try:
        relative = expanded.relative_to(Path.home())
    except ValueError:
        digest = hashlib.sha256(
            os.path.normcase(os.path.normpath(value)).encode("utf-8")
        ).hexdigest()[:16]
        return f"<external-path:{digest}>"
    return f"~/{relative.as_posix()}"


def _codex_grants(data: Any, *, scope: HostScope, source: str) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    grants: list[dict[str, Any]] = []
    for setting in ("approval_policy", "sandbox_mode", "network_access", "web_search"):
        if setting in data:
            value = data[setting]
            risky = str(value).lower() in {"never", "danger-full-access", "enabled", "true"}
            grants.append(_setting_grant(
                host="codex", scope=scope, source=source,
                kind="sandbox" if "sandbox" in setting or "network" in setting else "permission_mode",
                setting=setting, value=value, access="admin" if risky else "unknown",
                risk="critical" if str(value).lower() == "danger-full-access" else ("high" if risky else "medium"),
            ))
    workspace_write = data.get("sandbox_workspace_write")
    if isinstance(workspace_write, dict):
        for setting, value in sorted(workspace_write.items()):
            grants.append(_setting_grant(
                host="codex", scope=scope, source=source, kind="sandbox",
                setting=f"sandbox_workspace_write.{setting}", value=value,
                access="external" if setting == "network_access" and bool(value) else "unknown",
                risk="high" if setting == "network_access" and bool(value) else "medium",
            ))
    mcp = data.get("mcp_servers")
    if isinstance(mcp, dict):
        grants.extend(_mcp_grants({"mcpServers": mcp}, host="codex", scope=scope, source=source))
    apps = data.get("apps")
    if isinstance(apps, dict):
        for name, config in sorted(apps.items()):
            enabled = config.get("enabled") if isinstance(config, dict) else None
            grants.append({
                **_grant_base(
                    host="codex", scope=scope, source=source, kind="plugin_or_app",
                    identity=str(name), config=config, access="external", risk="high",
                ),
                "name": str(name), "enabled": enabled if isinstance(enabled, bool) else None,
            })
    selected_profile = data.get("profile")
    if isinstance(selected_profile, str) and selected_profile.strip():
        profile_name = selected_profile.strip()
        profiles = data.get("profiles")
        profile_config = (
            profiles.get(profile_name) if isinstance(profiles, dict) else None
        )
        resolved = isinstance(profile_config, dict)
        grants.append(
            {
                **_grant_base(
                    host="codex",
                    scope=scope,
                    source=source,
                    kind="profile",
                    identity=profile_name,
                    config={"profile": profile_name, "resolved": resolved},
                    access="unknown",
                    risk="medium",
                ),
                "profile": profile_name,
                "resolved": resolved,
            }
        )
        if resolved:
            assert isinstance(profile_config, dict)
            grants.extend(
                _codex_grants(
                    {key: value for key, value in profile_config.items() if key != "profile"},
                    scope=scope,
                    source=f"{source}#profiles.{profile_name}",
                )
            )
    return grants


def _flatten_requirements(data: Any, *, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(data, dict):
        flattened: list[tuple[str, Any]] = []
        for key, value in sorted(data.items()):
            name = f"{prefix}.{key}" if prefix else str(key)
            flattened.extend(_flatten_requirements(value, prefix=name))
        return flattened
    return [(prefix or "value", data)]


def _codex_requirement_grants(
    data: Any, *, scope: HostScope, source: str
) -> list[dict[str, Any]]:
    grants: list[dict[str, Any]] = []
    for name, raw_value in _flatten_requirements(data):
        redacted_value = _redact_secret_values(raw_value, parent_key=name)
        rendered = (
            _canonical(redacted_value)
            if isinstance(redacted_value, (dict, list))
            else str(redacted_value)
        )
        grants.append(
            {
                **_grant_base(
                    host="codex",
                    scope=scope,
                    source=source,
                    kind="requirement",
                    identity=name,
                    config={name: redacted_value},
                    access="none",
                    risk="medium",
                ),
                "requirement": name,
                "value": rendered,
            }
        )
    return grants


def _cursor_grants(data: Any, *, scope: HostScope, source: str) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    grants = _permission_rule_grants(data.get("permissions"), host="cursor", scope=scope, source=source)
    for setting in ("approvalMode", "sandbox", "network", "allowWrite"):
        if setting in data:
            value = data[setting]
            grants.append(_setting_grant(
                host="cursor", scope=scope, source=source,
                kind="sandbox" if setting in {"sandbox", "network"} else "permission_mode",
                setting=setting, value=value, access="admin" if bool(value) else "unknown",
                risk="high" if bool(value) else "medium",
            ))
    return grants


#: How much a declared scope level grants, for scopes that publish alike (#802).
_LEVEL_WIDTH = {"read": 1, "write": 2}


def _workflow_permissions(value: Any, job: str, collided: set[str]) -> dict[str, Any]:
    """Normalize one job's effective declaration, retaining unknown defaults.

    ``job`` is the job's published label. Scope names publish through the same
    label rule (#802); every declared scope counts toward a collision, a
    ``none`` one too, because it is what separates two declarations that
    would otherwise publish alike. Scopes that publish alike keep the widest
    level among them, so a collision (itself a blocking limit) never reads a
    declared ``write`` as ``read``.
    """
    state = "explicit"
    permissions: dict[str, str] = {}
    if value is None:
        state = "repository_default"
    elif isinstance(value, str) and value in {"read-all", "write-all"}:
        permissions = {"*": value.removesuffix("-all")}
    elif isinstance(value, dict) and all(
        isinstance(scope, str) and isinstance(level, str) and level in {"read", "write", "none"}
        for scope, level in value.items()
    ):
        shown = _published_labels(value, kind=_SCOPE_LABELS, collided=collided)
        for scope, level in sorted(value.items(), key=lambda item: (shown[item[0]], item[0])):
            label = shown[scope]
            if level != "none" and _LEVEL_WIDTH[level] > _LEVEL_WIDTH.get(permissions.get(label, ""), 0):
                permissions[label] = level
    else:
        state = "unresolved"
    return {"job": job, "state": state, "permissions": permissions}


#: ``owner/repo[/path]@ref``. The reference is compared as declared text and
#: never resolved: a branch, tag and commit SHA are equally opaque here (#771).
_REMOTE_STEP_ACTION_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[^@\s]+)?@[^@\s]+")
_DOCKER_STEP_ACTION_RE = re.compile(r"docker://\S+")
#: ``@algorithm:hex`` ending a reference is an image digest, not registry userinfo.
_TRAILING_DIGEST_RE = re.compile(r"@[A-Za-z0-9_+.-]+:[0-9a-fA-F]{32,}\Z")
#: ``scheme://`` opening a reference, in any letter case.
_REFERENCE_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_UNREADABLE_STEP_REASONS = frozenset({"steps_not_a_list", "step_not_a_mapping"})


def _redact_step_text(value: str) -> str:
    """Step text as it may be published.

    Known token shapes (``ghp_…``, ``AKIA…``, ``xoxb-…``) go through the
    report redactor, then credential assignments and URLs through the host one.
    """

    return _sanitize_sensitive_string(redact_text(value) or "")


def _redact_reference(text: str) -> str:
    """A ``uses:`` value or a name as it may be published; differs from ``text`` when redacted.

    ``docker://user:password@registry/image`` carries registry credentials in
    its userinfo, which neither redactor recognises, so the userinfo is
    replaced whole. A password may itself hold ``/``, ``:`` or ``@`` (a base64
    key file routinely does), so the authority is never taken to end at the
    first ``/``: once a trailing ``@algorithm:hex`` image digest is set aside,
    the userinfo is everything before the *last* ``@``.

    - After any ``scheme://`` (``docker://`` in any letter case, or a scheme
      GitHub would reject), every remaining ``@`` belongs to userinfo.
    - Without a scheme, the last ``@`` opens the ref of ``owner/repo[/path]@ref``,
      so what precedes it is userinfo only when it holds a ``:`` or another
      ``@``. An owner or repository name holds neither; an action path that does
      is refused as redacted rather than risk publishing a password. A marker
      the redactors already wrote is not itself read as userinfo.

    ``docker://image@sha256:<hex>``, a tag, a port and ``owner/repo/path@ref``
    are kept as written.
    """

    display = _redact_step_text(text)
    scheme = _REFERENCE_SCHEME_RE.match(display)
    rest = display[scheme.end():] if scheme else display
    digest = _TRAILING_DIGEST_RE.search(rest)
    body, suffix = (rest[: digest.start()], rest[digest.start():]) if digest else (rest, "")
    userinfo, at, remainder = body.rpartition("@")
    if not at:
        return display
    unmarked = _PATH_REDACTION_MARKER.sub("", userinfo)
    if scheme is None and ":" not in unmarked and "@" not in unmarked:
        return display
    prefix = scheme.group().lower() if scheme else ""
    return f"{prefix}<redacted>@{remainder}{suffix}"


#: A whitespace-delimited token of free text.
_LABEL_TOKEN_RE = re.compile(r"\S+")
#: What a URI scheme may hold (RFC 3986 §3.1): an ASCII letter first, then these.
_SCHEME_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+.-"
_SCHEME_NON_LETTERS = "0123456789+.-"


def _label_scheme_end(token: str) -> int:
    """The index just past the ``://`` of the token's first ``scheme://``, else ``-1``.

    A scheme is an ASCII letter and then letters, digits, ``+``, ``.`` or
    ``-`` running up to ``://``; it may start anywhere in the token, so
    ``(docker://…`` and ``9docker://…`` hold ``docker://``. Each ``://`` is
    tried in order: take the run of scheme characters just before it, and
    its first letter opens the scheme. A ``://`` with no letter before it
    (``9://``, ``+://``) opens none, so the next one is tried.

    Linear in the token (#802 review: a backtracking pattern was quadratic).
    ``/`` ends a run, so the run before one ``://`` never reaches back past
    the previous one, and each segment between them is read once.
    """

    lower = 0
    separator = token.find("://")
    while separator != -1:
        segment = token[lower:separator]
        run = segment[len(segment.rstrip(_SCHEME_CHARS)):]
        if run.lstrip(_SCHEME_NON_LETTERS):
            return separator + 3
        lower = separator + 3
        separator = token.find("://", lower)
    return -1


def _redact_token_userinfo(match: re.Match[str]) -> str:
    token = match.group()
    end = _label_scheme_end(token)
    if end == -1:
        return token
    rest = token[end:]
    digest = _TRAILING_DIGEST_RE.search(rest)
    body, suffix = (rest[: digest.start()], rest[digest.start():]) if digest else (rest, "")
    _, at, remainder = body.rpartition("@")
    return f"{token[:end]}<redacted>@{remainder}{suffix}" if at else token


def _redact_label_userinfo(text: str) -> str:
    """``text`` with the userinfo of every ``scheme://…@`` token replaced (#802).

    The ``scheme://`` rule of :func:`_redact_reference`, applied to each
    whitespace-delimited token of free text rather than to a whole reference:
    in a token holding ``scheme://`` (:func:`_label_scheme_end`), once a
    trailing ``@algorithm:hex`` digest is set aside, everything between the
    first ``scheme://`` and the token's last ``@`` is userinfo, so a password
    holding ``/``, ``:`` or ``@`` is covered. Only such tokens are read, and
    what precedes the scheme in the token is kept. Calling
    :func:`_redact_reference` on a label would replace the words before the
    token too (``Pull docker://ci:pw@gcr.io/img`` → ``<redacted>@gcr.io/img``)
    and read scheme-less prose as userinfo (``Tag v1:beta@2`` → ``<redacted>@2``).

    Linear in ``text``: a label may be as long as the workflow file itself.
    """

    if "://" not in text:
        return text
    return _LABEL_TOKEN_RE.sub(_redact_token_userinfo, text)


def published_workflow_label(text: str) -> str:
    """A workflow label as it may be published (#802).

    A label is text GitHub reads as a name: a job id, a step's ``id`` or
    ``name``, a trigger, a permission scope. Each goes through the redaction
    step text does — known token shapes (``ghp_…``, ``AKIA…``, ``xoxb-…``) and
    credential assignments and URLs — and then the userinfo of any
    ``scheme://…@`` token inside it, so ``Pull docker://ci:<password>@gcr.io/img``
    publishes ``Pull docker://<redacted>@gcr.io/img``. Ordinary names such as
    ``build``, ``deploy-prod``, ``secret-scan``, ``token-refresh``, ``contents``
    or ``pull_request`` are never rewritten.

    Every published job label, prefix and row, and ``check``'s evidence, uses
    this one rule, so no surface names a job another way. Two reads compare
    by what they published, so ``config_sha256`` and every compared fact are
    built from these labels, and :func:`_published_labels` refuses when two
    raw labels in one workflow publish alike.
    """

    return _redact_label_userinfo(_redact_step_text(text))


def _published(text: str, *, label: bool = False) -> tuple[str, bool]:
    """``text`` as it may be published, and whether redaction rewrote it (#767, #802).

    The one rule for workflow text that is both published and compared: a
    step's ``uses:``, a job's reusable-workflow ``uses:``, and the destination
    and source names of a secret it passes. Rewritten text cannot be compared.
    Two distinct values may publish alike, and a digest of either would be a
    digest of the credential, so a caller marks the entry redacted and
    :func:`_uncompared_workflow_text` makes the workflow a blocking limit.

    ``label=True`` publishes a job id, trigger or permission scope name by
    :func:`published_workflow_label` instead. A redacted label still compares:
    one token-shaped job id, alone in its workflow, identifies its job as well
    as the raw text did. What cannot compare is two distinct labels that
    publish alike, which :func:`_published_labels` records.
    """

    display = published_workflow_label(text) if label else _redact_reference(text)
    return display, display != text


#: What a label collision names, in the order a limit lists them (#802).
_JOB_LABELS = "job ids"
_TRIGGER_LABELS = "trigger names"
_SCOPE_LABELS = "permission scope names"
_LABEL_KINDS = (_JOB_LABELS, _TRIGGER_LABELS, _SCOPE_LABELS)


def _published_labels(raws: Any, *, kind: str, collided: set[str]) -> dict[str, str]:
    """Each raw label in one namespace and the label it publishes as (#802).

    A namespace is one workflow's jobs, its triggers, or one ``permissions``
    mapping. When two distinct raw labels in it publish alike, ``kind`` is
    added to ``collided``: they would compare as one job, trigger or scope,
    so :func:`_uncompared_workflow_text` makes the workflow a blocking limit.
    A single redacted label is not a collision and refuses nothing.
    """

    names = [str(raw) for raw in raws]
    shown = {name: _published(name, label=True)[0] for name in names}
    if len(set(shown.values())) < len(names):
        collided.add(kind)
    return shown


def _step_label(step: dict[Any, Any], index: int) -> str:
    """The step's ``id``, else its ``name``, else ``steps[N]`` (zero-based).

    Evidence for finding the step, never part of the comparison, so renaming
    a step or reordering steps that declare the same references stays quiet.
    Published by :func:`published_workflow_label`; two steps whose labels
    publish alike still compare by their references.
    """

    for key in ("id", "name"):
        value = step.get(key)
        if isinstance(value, str) and value.strip():
            return published_workflow_label(value.strip())
    return f"steps[{index}]"


def _unreadable_step(job: str, step: str, reason: str) -> dict[str, Any]:
    """A ``steps`` shape GitHub would reject, recorded rather than read as empty.

    Nothing from the value is published: it may be any text at all.
    """

    return {"job": job, "step": step, "uses": None, "form": "unresolved", "unresolved_reason": reason}


def _step_action(job: str, step: dict[Any, Any], index: int) -> dict[str, Any] | None:
    """One step's declared ``uses:``, or ``None`` when it declares no remote one.

    A local ``./…`` reference is outside this reader: composite actions stay
    unread (#701), so it is neither listed nor counted as inspected. Anything
    Shipgate does not resolve to an action identity is kept, as ``unresolved``
    with the reason, rather than guessed or dropped. A value the credential
    redactor rewrites cannot be both published and compared, so it is
    ``redacted``, and the reader records a blocking coverage issue for it.
    ``job`` is the job's published label (#802).
    """

    if "uses" not in step:
        return None
    value = step["uses"]
    entry: dict[str, Any] = {
        "job": job,
        "step": _step_label(step, index),
        "uses": None,
        "form": "unresolved",
        "unresolved_reason": None,
    }
    if not isinstance(value, str):
        return {**entry, "unresolved_reason": "not_a_string"}
    text = value.strip()
    if text.startswith("./"):
        return None
    display, redacted = _published(text)
    if redacted:
        return {**entry, "uses": display, "unresolved_reason": "redacted"}
    if "${{" in text:
        return {**entry, "uses": text, "unresolved_reason": "expression"}
    if _DOCKER_STEP_ACTION_RE.fullmatch(text):
        return {**entry, "uses": text, "form": "docker"}
    if _REMOTE_STEP_ACTION_RE.fullmatch(text):
        return {**entry, "uses": text, "form": "remote"}
    return {**entry, "uses": text, "unresolved_reason": "unsupported_reference"}


def step_action_key(entry: dict[str, Any]) -> tuple[str, str, str, str]:
    """What a step reference is compared by: its job and the reference, not the step."""

    return (
        str(entry["job"]),
        str(entry["form"]),
        str(entry.get("unresolved_reason") or ""),
        "" if entry.get("uses") is None else str(entry["uses"]),
    )


# --- agent launches (#823) ----------------------------------------------------------
#
# How a coding agent is launched inside a job: a known agent action's permission
# inputs, a literal agent CLI command in a `run:` step, and the ref each
# `actions/checkout` step declares. Every value is compared as declared text; no
# action is fetched, no command is run and no expression is evaluated. A shape
# this reader does not read — a compound shell command, an expansion, a script,
# a composite or unknown action — is never guessed at: where it can be told
# apart it is listed as unresolved and named as a non-blocking limit, and
# otherwise it is one of the unread surfaces the support page names.


@dataclass(frozen=True)
class _AgentAction:
    """What one documented agent action's inputs mean to this reader (#823).

    ``inputs`` are compared as text. ``gates`` are comma-separated user lists
    where a ``*`` entry opens the gate to every user. ``args`` names the input
    that carries agent CLI arguments, read with the family's flag table for
    the documented widening rules. ``modes`` are inputs whose value is itself
    a documented widening.
    """

    family: Literal["claude", "codex"]
    inputs: tuple[str, ...]
    gates: tuple[str, ...] = ()
    args: str | None = None
    modes: tuple[tuple[str, str], ...] = ()


_CLAUDE_BASE_ACTION = _AgentAction(
    family="claude",
    inputs=(
        "allowed_tools", "claude_args", "disallowed_tools", "mcp_config",
        "plugin_marketplaces", "plugins", "settings",
    ),
    args="claude_args",
)

#: The documented agent actions, by ``owner/repo`` (matched case-insensitively,
#: at any ref). The input names are those the actions' own `action.yml`
#: declare; `allowed_tools`, `disallowed_tools` and `mcp_config` are the
#: Claude actions' earlier inputs, still read when a workflow sets them. The
#: base action is published both as its own repository and as the
#: `base-action` directory of `anthropics/claude-code-action`.
_AGENT_ACTIONS: dict[str, _AgentAction] = {
    "anthropics/claude-code-action": _AgentAction(
        family="claude",
        inputs=(
            "additional_permissions", "allowed_bots", "allowed_non_write_users",
            "allowed_tools", "claude_args", "disallowed_tools", "mcp_config",
            "plugin_marketplaces", "plugins", "settings",
        ),
        gates=("allowed_bots", "allowed_non_write_users"),
        args="claude_args",
    ),
    "anthropics/claude-code-base-action": _CLAUDE_BASE_ACTION,
    "anthropics/claude-code-action/base-action": _CLAUDE_BASE_ACTION,
    "openai/codex-action": _AgentAction(
        family="codex",
        inputs=(
            "allow-bot-users", "allow-bots", "allow-users", "codex-args",
            "permission-profile", "safety-strategy", "sandbox",
        ),
        gates=("allow-users",),
        args="codex-args",
        modes=(("sandbox", "danger-full-access"), ("safety-strategy", "unsafe")),
    ),
}

#: Flag spelling -> (primary spelling, arity): ``0`` takes no value, ``1`` one,
#: ``None`` every following word up to the next one starting with ``-``, as the
#: CLI's variadic options read them. From Claude Code's CLI reference.
_CLAUDE_FLAGS: dict[str, tuple[str, int | None]] = {
    "--permission-mode": ("--permission-mode", 1),
    "--dangerously-skip-permissions": ("--dangerously-skip-permissions", 0),
    "--allow-dangerously-skip-permissions": ("--allow-dangerously-skip-permissions", 0),
    "--allowedTools": ("--allowedTools", None),
    "--allowed-tools": ("--allowedTools", None),
    "--disallowedTools": ("--disallowedTools", None),
    "--disallowed-tools": ("--disallowedTools", None),
    "--add-dir": ("--add-dir", None),
    "--mcp-config": ("--mcp-config", None),
    "--settings": ("--settings", 1),
    "--permission-prompt-tool": ("--permission-prompt-tool", 1),
}

#: The same for ``codex exec``, from the Codex CLI's shared option definitions.
_CODEX_FLAGS: dict[str, tuple[str, int | None]] = {
    "--sandbox": ("--sandbox", 1),
    "-s": ("--sandbox", 1),
    "--dangerously-bypass-approvals-and-sandbox": ("--dangerously-bypass-approvals-and-sandbox", 0),
    "--yolo": ("--dangerously-bypass-approvals-and-sandbox", 0),
    "--approve-for-me": ("--approve-for-me", 0),
    "--not-so-yolo": ("--approve-for-me", 0),
    "--dangerously-bypass-hook-trust": ("--dangerously-bypass-hook-trust", 0),
    "--add-dir": ("--add-dir", 1),
    "--config": ("--config", 1),
    "-c": ("--config", 1),
    "--profile": ("--profile", 1),
    "-p": ("--profile", 1),
}

_AGENT_FLAG_TABLES = {"claude": _CLAUDE_FLAGS, "codex": _CODEX_FLAGS}

#: The agent action inputs a documented widening rule is read from.
AGENT_RULE_INPUTS: frozenset[str] = frozenset(
    name
    for spec in _AGENT_ACTIONS.values()
    for name in (*spec.gates, *(mode for mode, _value in spec.modes), *((spec.args,) if spec.args else ()))
)

#: The widening each documented rule names, as a row's ``why`` says it.
AGENT_WIDENING_RULES: dict[str, str] = {
    "bypass_permissions": "skips permission checks (bypassPermissions)",
    "bypass_approvals_and_sandbox": "bypasses approvals and the sandbox",
    "danger_full_access": "runs without a sandbox (danger-full-access)",
    "unsafe_safety_strategy": "runs without privilege restrictions (safety-strategy: unsafe)",
    "open_gate": "accepts runs triggered by any user",
}

#: A literal checkout ref that names pull request code, not the base branch's:
#: the documented pull request and workflow-run head expressions, and
#: ``refs/pull/<n>/head`` or ``/merge``.
_PULL_REQUEST_CODE_REF_RE = re.compile(
    r"\$\{\{\s*(?:github\.event\.pull_request\.(?:head\.(?:sha|ref)|merge_commit_sha)"
    r"|github\.head_ref|github\.event\.workflow_run\.head_(?:sha|branch))\s*\}\}"
    r"|refs/pull/(?:\d+|\$\{\{[^}]*\}\})/(?:head|merge)"
)

#: Triggers that run with the base repository's context on input from people
#: who need not hold write access, as the support page lists them.
UNTRUSTED_INPUT_TRIGGERS = ("issue_comment", "issues", "pull_request_target", "workflow_run")

_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_SHELL_OPERATOR_CHARS = ";&|()<>\n"
_SECRET_REFERENCE_RE = re.compile(r"\bsecrets\.([A-Za-z_][A-Za-z0-9_]*)")
_EXPRESSION_RE = re.compile(r"\$\{\{(.*?)\}\}", re.S)


def pull_request_code_ref(ref: str | None) -> bool:
    """Whether a published checkout ref names pull request code (#823)."""

    return ref is not None and _PULL_REQUEST_CODE_REF_RE.fullmatch(ref.strip()) is not None


def _shell_words(text: str) -> list[str] | None:
    """``text`` split into shell words and operator tokens, or ``None`` when unbalanced.

    Newlines, ``;``, ``&``, ``|``, parentheses and redirections outside quotes
    are operator tokens of their own; a backslash-newline joins two lines, as
    the shell reads it.
    """

    lexer = shlex.shlex(
        re.sub(r"\\\r?\n", "", text), posix=True, punctuation_chars=_SHELL_OPERATOR_CHARS
    )
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return None


def _is_operator(token: str) -> bool:
    return bool(token) and all(char in _SHELL_OPERATOR_CHARS for char in token)


def _has_shell_expansion(text: str) -> bool:
    """A ``$`` or backtick the shell would expand: anywhere outside single quotes."""

    quote: str | None = None
    escaped = False
    for char in text:
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif quote == "'":
            quote = None if char == "'" else quote
        elif char in {"$", "`"}:
            return True
        elif char == quote:
            quote = None
        elif quote is None and char in {"'", '"'}:
            quote = char
    return False


def _has_shell_comment(text: str) -> bool:
    """An unquoted ``#`` that starts a word: the shell reads the rest of the line as a comment.

    Read off the raw text because the word splitter keeps ``#`` as an
    ordinary character, so a quoted ``"#123 review"`` is one word, not a comment.
    """

    quote: str | None = None
    escaped = False
    previous = " "
    for char in text:
        if escaped:
            # An escaped character, a space included, is part of the word.
            escaped = False
            previous = "\\"
            continue
        if char == "\\" and quote != "'":
            escaped = True
        elif quote is not None:
            quote = None if char == quote else quote
        elif char in {"'", '"'}:
            quote = char
        elif char == "#" and (previous.isspace() or previous in _SHELL_OPERATOR_CHARS):
            return True
        previous = char
    return False


def _agent_command(words: list[str]) -> tuple[str, list[str]] | None:
    """The agent CLI one simple command launches headless, and its arguments.

    ``claude`` with ``-p``/``--print`` anywhere in its arguments, or ``codex``
    with ``exec`` (or its alias ``e``) as its first argument, after any leading
    ``NAME=value`` assignments. Any other command — ``claude mcp add``,
    ``codex login``, an ``echo`` that mentions either — launches none.
    """

    index = 0
    while index < len(words) and _ASSIGNMENT_RE.match(words[index]):
        index += 1
    if index >= len(words):
        return None
    command, arguments = words[index], words[index + 1:]
    if command == "claude" and any(word in {"-p", "--print"} for word in arguments):
        return "claude", arguments
    if command == "codex" and arguments[:1] in (["exec"], ["e"]):
        return "codex", arguments[1:]
    return None


def _line_agent(line: str) -> str | None:
    """The agent CLI one line starts headless, read as plain words: a fallback only.

    Used when a ``run:``'s quoting cannot be split into commands, to name the
    launch as unresolved rather than miss it; nothing it reads is published.
    """

    return (_agent_command(line.split()) or (None, None))[0]


def _read_flags(
    words: list[str] | tuple[str, ...], table: dict[str, tuple[str, int | None]]
) -> list[tuple[str, int | None, list[str] | None]]:
    """The documented permission flags among ``words``: primary spelling, arity and value words.

    A flag is read by name wherever it is a word of its own; every other word
    — the prompt, ``--model`` and any undocumented flag — is not compared. A
    flag that takes no value, or is given none, has ``None``.
    """

    flags: list[tuple[str, int | None, list[str] | None]] = []
    index = 0
    while index < len(words):
        word = words[index]
        name, equals, attached = word.partition("=") if word.startswith("--") else (word, "", "")
        spec = table.get(name)
        index += 1
        if spec is None:
            continue
        primary, arity = spec
        if arity == 0:
            flags.append((primary, arity, None))
            continue
        values = [attached] if equals else []
        if arity == 1 and not equals and index < len(words):
            values.append(words[index])
            index += 1
        elif arity is None:
            while index < len(words) and not words[index].startswith("-"):
                values.append(words[index])
                index += 1
        flags.append((primary, arity, values or None))
    return flags


# --- how an agent action splits its argument input (#823) -----------------------------
#
# `claude_args` and `codex-args` are not shell text: no shell reads them. Each
# action splits its input itself, and a widening rule is read from the words
# the action passes on, so these follow the actions' own parsers rather than
# the `run:` tokenizer above.

#: One word of shell-quote's chunker once the Claude actions have made
#: ``()|&;<>`` literal: unquoted non-space characters (a backslash escaping a
#: quote or a blank), a double-quoted run or a single-quoted run, adjacent.
#: An unbalanced quote matches none of them, so shell-quote skips it.
_SHELL_QUOTE_CHUNK_RE = re.compile(r"""(?:(?:\\['" \t]|[^\s'"])+|"(?:\\"|[^"])*?"|'[^']*?')+""")

#: string-argv's pattern, which `openai/codex-action` splits a shell-like
#: `codex-args` with: a word holding quotes keeps them, a quoted string alone
#: is its content, and whitespace, newlines included, separates the rest.
_STRING_ARGV_RE = re.compile(
    r"""([^\s'"]([^\s'"]*(['"])([^\x03]*?)\3)+[^\s'"]*)|[^\s'"]+|(['"])([^\x03]*?)\5"""
)


def _shell_quote_variable(chunk: str, index: int) -> tuple[str, int]:
    """shell-quote's ``parseEnvVar`` with no environment, at the ``$`` at ``index``.

    Returns the value, empty for any name and ``$`` for none, and the index of
    the last character the variable took. Raises ``ValueError`` for the "Bad
    substitution" shell-quote throws, which fails the action.
    """

    index += 1
    char = chunk[index:index + 1]
    if char == "{":
        index += 1
        if chunk[index:index + 1] == "}":
            raise ValueError("bad substitution")
        depth, end = 1, index
        while depth > 0 and end < len(chunk):
            if chunk[end] == "{" and chunk[end - 1] == "$":
                depth += 1
            elif chunk[end] == "}":
                depth -= 1
            end += 1
        if depth != 0:
            raise ValueError("bad substitution")
        name, index = chunk[index:end - 1], end - 1
    elif char and char in "*@#?$!_-":
        # shell-quote steps past the name and then past one more character.
        name, index = char, index + 1
    else:
        match = re.search(r"[^A-Za-z0-9_]", chunk[index:])
        if match is None:
            name, index = chunk[index:], len(chunk)
        else:
            name, index = chunk[index:index + match.start()], index + match.start() - 1
    return ("" if name else "$"), index


def _shell_quote_word(chunk: str, *, comments: bool) -> tuple[str, bool]:
    """One chunk as shell-quote reads it: the word, and whether an unquoted ``#`` ended the input.

    Quotes and backslashes work as in a shell and ``$NAME`` reads as empty.
    With ``comments``, an unquoted ``#`` ends the word and every word after
    it, as it does for the action; without, ``#`` is an ordinary character.
    """

    out: list[str] = []
    quote = ""
    escaped = False
    index = 0
    while index < len(chunk):
        char = chunk[index]
        if escaped:
            out.append(char)
            escaped = False
        elif quote:
            if char == quote:
                quote = ""
            elif quote == "'":
                out.append(char)
            elif char == "\\":
                index += 1
                following = chunk[index:index + 1]
                out.append(following if following and following in "\"\\$" else "\\" + following)
            elif char == "$":
                value, index = _shell_quote_variable(chunk, index)
                out.append(value)
            else:
                out.append(char)
        elif char in {'"', "'"}:
            quote = char
        elif char == "#" and comments:
            return "".join(out), True
        elif char == "\\":
            escaped = True
        elif char == "$":
            value, index = _shell_quote_variable(chunk, index)
            out.append(value)
        else:
            out.append(char)
        index += 1
    return "".join(out), False


@dataclass(frozen=True)
class _ArgumentInput:
    """An agent action's argument input as the action splits it (#823).

    ``text`` is what the action parses. ``spans`` is every word with where it
    sits in ``text``, for publication. ``words`` is what the action passes on,
    or ``None`` when the action refuses the input and the agent does not run.
    """

    text: str
    spans: tuple[tuple[str, int, int], ...]
    words: tuple[str, ...] | None


def _claude_argument_input(value: str) -> _ArgumentInput:
    """``claude_args`` split as ``base-action/src/parse-sdk-options.ts`` splits it.

    Each line whose first non-blank character is ``#`` is dropped, ``()|&;<>``
    are literal, and the rest is read by shell-quote with no environment:
    whitespace, newlines included, separates words; quotes and backslashes work
    as in a shell; ``$NAME`` reads as empty; and an unquoted ``#`` later in the
    input ends it.
    """

    text = "\n".join(
        line for line in value.split("\n") if not line.strip().startswith("#")
    ).strip()
    spans: list[tuple[str, int, int]] = []
    words: list[str] | None = []
    ended = False
    for match in _SHELL_QUOTE_CHUNK_RE.finditer(text):
        chunk = match.group()
        if words is not None and not ended:
            try:
                word, ended = _shell_quote_word(chunk, comments=True)
            except ValueError:
                words = None
            else:
                if word or not ended:
                    words.append(word)
        try:
            shown = _shell_quote_word(chunk, comments=False)[0]
        except ValueError:
            shown = chunk
        spans.append((shown, match.start(), match.end()))
    return _ArgumentInput(text, tuple(spans), None if words is None else tuple(words))


def _codex_argument_input(value: str) -> _ArgumentInput:
    """``codex-args`` read as `openai/codex-action` reads it: a JSON array of strings, or string-argv.

    A value starting with ``[`` that is not a JSON array of strings makes the
    action refuse it. The array form has no spans: it is published whole.
    """

    if value.startswith("["):
        try:
            loaded = json.loads(value)
        except (ValueError, RecursionError):
            return _ArgumentInput(value, (), None)
        if isinstance(loaded, list) and all(isinstance(item, str) for item in loaded):
            return _ArgumentInput(value, (), tuple(loaded))
        return _ArgumentInput(value, (), None)
    spans = tuple(
        (
            next(group for group in (match.group(1), match.group(6), match.group(0)) if group is not None),
            match.start(),
            match.end(),
        )
        for match in _STRING_ARGV_RE.finditer(value)
    )
    return _ArgumentInput(value, spans, tuple(word for word, _start, _end in spans))


def _argument_input(family: str, value: str) -> _ArgumentInput:
    return _claude_argument_input(value) if family == "claude" else _codex_argument_input(value)


# --- what an expression in an input leaves readable (#823 review) ---------------------
#
# GitHub substitutes a `${{ }}` expression into an input before the action reads
# it, and the substituted text may be anything: more words, a quote that closes
# one opened before it, a `#` that ends `claude_args`. So a rule is read only
# from literal text the expression cannot reach.

#: One ``${{ … }}`` expression, or an unterminated ``${{`` to the end of the text.
_EXPRESSION_SPAN_RE = re.compile(r"\$\{\{.*?(?:\}\}|\Z)", re.S)
#: What an expression reads as while literal text around it is split: a
#: private-use character, which no splitter here reads as a blank, a quote or
#: an operator, so the word the expression touches holds it and is set aside.
_EXPRESSION_MARK = "\ue000"


def holds_expression(text: str) -> bool:
    """Whether declared text holds a ``${{ }}`` expression GitHub substitutes before the action reads it."""

    return "${{" in text


def _skipped_quote(text: str, pattern: re.Pattern[str]) -> int | None:
    """The first quote ``pattern`` leaves unmatched in ``text``, else ``None``.

    Both splitters skip a quote no word covers; before an expression, that is a
    quoted run the substituted text may close, so nothing from it on is read.
    """

    covered = 0
    for match in (*pattern.finditer(text), None):
        gap = text[covered:] if match is None else text[covered:match.start()]
        quote = next((index for index, char in enumerate(gap) if char in "'\""), None)
        if quote is not None:
            return covered + quote
        if match is not None:
            covered = match.end()
    return None


def _literal_argument_words(family: str, text: str) -> tuple[str, ...] | None:
    """The words of an argument input no ``${{ }}`` expression in it can reach, as the action splits them.

    Without an expression, every word the action passes on. With one, the
    words the action has finished reading before the first expression: the
    word the expression touches, any quoted run still open at it, and
    everything after it are not read. A JSON array ``codex-args`` gives the
    elements before the one holding an expression. ``None`` when the action
    refuses what is left.
    """

    start = text.find("${{")
    if start == -1:
        return _argument_input(family, text).words
    if family == "codex" and text.startswith("["):
        words = _argument_input(family, _EXPRESSION_SPAN_RE.sub(_EXPRESSION_MARK, text)).words
    else:
        prefix, pattern = text[:start], _STRING_ARGV_RE
        if family == "claude":
            # A line the action drops as a comment is dropped whatever the
            # expression on it holds, and the lines before it are whole.
            *whole, last = prefix.split("\n")
            dropped = last.strip().startswith("#")
            kept = [line for line in whole if not line.strip().startswith("#")]
            prefix = "\n".join([*kept, ""] if dropped else [*kept, last])
            pattern = _SHELL_QUOTE_CHUNK_RE
        quote = _skipped_quote(prefix, pattern)
        words = _argument_input(family, prefix[:quote] + _EXPRESSION_MARK).words
    if words is None:
        return None
    literal: list[str] = []
    for word in words:
        if _EXPRESSION_MARK in word:
            break
        literal.append(word)
    return tuple(literal)


# --- what an agent setting publishes (#823, #802) --------------------------------------

#: A codex ``--config`` override under one of these keys carries values the
#: codex host reader never publishes, as ``.mcp.json`` ``env``/``headers`` do not.
_CONFIG_WITHHELD_KEYS = frozenset({"env", "headers", "http_headers", "env_http_headers"})


def _withheld_json(value: Any) -> str | None:
    """A JSON value as the host readers publish one: key names, secret-bearing values replaced.

    ``env`` and ``headers`` keep their keys with every value ``<redacted>``,
    ``apiKeyHelper`` and every other secret-named key's value is ``<redacted>``,
    and strings go through the host sanitizer (URLs, bearer and header
    values), exactly as `.claude/settings.json` and `.mcp.json` are read.
    Canonical, so reformatting or reordering keys changes nothing.
    """

    try:
        return json.dumps(
            _redact_secret_values(value), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, default=str,
        )
    except (RecursionError, TypeError, ValueError):
        return None


def _withheld_word(word: str) -> str | None:
    """One word as it may be published: a JSON object by :func:`_withheld_json`, else as written.

    ``None`` for a word that starts like a JSON object and does not parse: what
    it holds cannot be told apart, so none of it may be published.
    """

    if not word.lstrip().startswith("{"):
        return word
    try:
        loaded = json.loads(word)
    except (ValueError, RecursionError):
        return None
    return _withheld_json(loaded)


def _withheld_config(text: str) -> str | None:
    """A codex ``--config key=value`` override, its secret-bearing value withheld.

    A key path through ``env``, ``headers`` or a secret-named key publishes
    ``<redacted>`` for its value; a table or array value, parsed as TOML as
    codex parses it, publishes by :func:`_withheld_json`. A value that starts
    like a table or array and does not parse — string-argv keeps the quotes of
    ``--config='k={…}'`` — is ``None``: what it holds cannot be told apart from
    its keys. Anything else is kept.
    """

    key, equals, value = text.partition("=")
    if not equals:
        return text
    segments = [segment.strip().strip("\"'") for segment in key.split(".")]
    if any(segment in _CONFIG_WITHHELD_KEYS or _is_secret_key(segment) for segment in segments):
        return f"{key}=<redacted>"
    try:
        loaded = tomllib.loads(f"value = {value}").get("value")
    except (tomllib.TOMLDecodeError, RecursionError):
        return None if value.strip().strip("\"'").startswith(("{", "[")) else text
    if isinstance(loaded, (dict, list)):
        shown = _withheld_json(loaded)
        return None if shown is None else f"{key}={shown}"
    return text


def _withheld_attached(prefix: str, value: str, withhold: Callable[[str], str | None]) -> str | None:
    """``prefix`` and a value written attached to its flag, the value withheld by ``withhold``."""

    shown = withhold(value)
    return None if shown is None else f"{prefix}{shown}"


def _withheld_words(words: list[str] | tuple[str, ...], *, family: str) -> list[str] | None:
    """Each argument word as it may be published, or ``None`` when one cannot be.

    A value is withheld however it is attached to its flag (#823 review): a
    separate word, ``--name=value`` (``--settings={…}``, ``--mcp-config={…}``,
    ``--config=…``), and codex's ``-c<value>`` and ``-c=<value>``, which clap
    reads as ``-c <value>``.
    """

    shown: list[str] = []
    config = False
    for word in words:
        if config:
            item = _withheld_config(word)
        elif family == "codex" and word.startswith("--config="):
            item = _withheld_attached("--config=", word.removeprefix("--config="), _withheld_config)
        elif family == "codex" and word.startswith("-c") and len(word) > 2:
            prefix = "-c=" if word.startswith("-c=") else "-c"
            item = _withheld_attached(prefix, word.removeprefix(prefix), _withheld_config)
        elif word.startswith("--") and "=" in word:
            name, _, value = word.partition("=")
            item = _withheld_attached(f"{name}=", value, _withheld_word)
        else:
            item = _withheld_word(word)
        if item is None:
            return None
        shown.append(item)
        config = family == "codex" and word in {"-c", "--config"}
    return shown


def _withheld_arguments(family: str, value: str) -> str | None:
    """An argument input as it may be published: the text the action parses, JSON words withheld.

    A word the host readers would not publish is replaced, quoted, by what they
    would; every other character stays as declared. A JSON array
    ``codex-args`` publishes as its array of withheld words.
    """

    parsed = _argument_input(family, value)
    if family == "codex" and value.startswith("["):
        if parsed.words is None:
            # The action refuses it; what it holds is still withheld as JSON.
            try:
                return _withheld_json(json.loads(value))
            except (ValueError, RecursionError):
                return None
        shown = _withheld_words(parsed.words, family=family)
        return None if shown is None else json.dumps(shown, separators=(",", ":"), ensure_ascii=False)
    shown = _withheld_words([word for word, _start, _end in parsed.spans], family=family)
    if shown is None:
        return None
    pieces: list[str] = []
    cursor = 0
    for (word, start, end), published in zip(parsed.spans, shown, strict=True):
        if published != word:
            pieces.extend((parsed.text[cursor:start], shlex.quote(published)))
            cursor = end
    pieces.append(parsed.text[cursor:])
    return "".join(pieces)


def _url_withheld(url: str) -> str:
    """A URL with its path and query withheld as the host sanitizer withholds them.

    One holding userinfo is kept as written, so the label redaction still
    finds the credential in it.
    """

    try:
        netloc = urlsplit(url).netloc
    except ValueError:
        return url
    return url if "@" in netloc else _sanitize_url(url)


#: Private-use characters that stand for one expression each while a value is redacted.
_EXPRESSION_SLOTS = range(0xE000, 0xF900)
_EXPRESSION_SLOT_RE = re.compile("[\ue000-\uf8ff]")


def _published_value(text: str) -> tuple[str, bool]:
    """``text`` as it may be published, and whether credential-shaped text had to be redacted.

    A URL's path and query are withheld the way the host sanitizer withholds
    them from an MCP server URL (#723), and the rest of the text is published
    and compared: a URL path is not a credential. Anything else the #802 label
    redaction rewrites — a token shape, a credential assignment, a bearer or
    header value, a URL's userinfo — is credential-shaped text, and the value
    is published redacted. The caller decides what that refuses.

    Each ``${{ }}`` expression is read as one word while this is decided, so an
    expression inside a URL's userinfo is withheld with the userinfo rather
    than splitting the URL (``https://x:${{ secrets.T }}@host/…`` publishes
    ``https://host/<redacted-path>``), and every expression left in the value is
    published through the same label redaction.
    """

    expressions = _EXPRESSION_SPAN_RE.findall(text)
    if len(expressions) > len(_EXPRESSION_SLOTS) or _EXPRESSION_SLOT_RE.search(text):
        # No free character to stand for each expression: read the text as written.
        expressions = []
    def urls_withheld(value: str) -> str:
        return _URL_RE.sub(lambda match: _url_withheld(match.group(0)), value)

    slots = iter(_EXPRESSION_SLOTS)
    marked = _EXPRESSION_SPAN_RE.sub(lambda _match: chr(next(slots)), text) if expressions else text
    withheld = urls_withheld(marked)
    shown = published_workflow_label(withheld)
    kept = [urls_withheld(expression) for expression in expressions]
    labels = [published_workflow_label(expression) for expression in kept]
    redacted = shown != withheld or labels != kept

    if expressions:
        shown = _EXPRESSION_SLOT_RE.sub(
            lambda match: labels[ord(match.group()) - _EXPRESSION_SLOTS.start], shown
        )
    return shown, redacted


def _setting_text(value: Any) -> str | None:
    """A ``with:`` value as the text GitHub passes, or ``None`` when it is not a scalar."""

    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    return None


def _published_text(name: str, text: str | None) -> dict[str, Any]:
    """A setting's withheld text as it may be published; ``None`` text is ``unparsed_json``."""

    if text is None:
        return {"name": name, "value": None, "unresolved_reason": "unparsed_json"}
    shown, redacted = _published_value(text)
    return {"name": name, "value": shown, "unresolved_reason": "redacted" if redacted else None}


def _published_setting(name: str, value: Any, *, arguments: str | None = None) -> dict[str, Any]:
    """One action input as it may be published: its text, or why it is not.

    ``arguments`` names the family whose action splits this input
    (``claude_args``, ``codex-args``); every other input is one value, a JSON
    object published by :func:`_withheld_json`.
    """

    text = _setting_text(value)
    if text is None:
        return {"name": name, "value": None, "unresolved_reason": "not_a_string"}
    setting = _published_text(
        name, _withheld_word(text) if arguments is None else _withheld_arguments(arguments, text)
    )
    # Read off the declared text: redaction may rewrite the expression away.
    return {**setting, "holds_expression": True} if holds_expression(text) else setting


def _published_flag(family: str, name: str, arity: int | None, values: list[str] | None) -> dict[str, Any]:
    """One CLI flag as it may be published: its value words, each withheld as an input's are."""

    if values is None:
        return {"name": name, "value": None, "unresolved_reason": None}
    if family == "codex" and name == "--config":
        overrides = [_withheld_config(value) for value in values]
        shown = None if None in overrides else [str(value) for value in overrides]
    else:
        shown = _withheld_words(values, family=family)
    if shown is None:
        return _published_text(name, None)
    return _published_text(name, shown[0] if arity == 1 else shlex.join(shown))


def _setting_key(setting: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(setting["name"]),
        str(setting.get("unresolved_reason") or ""),
        "" if setting.get("value") is None else str(setting["value"]),
    )


def _job_secrets(job: dict[Any, Any], workflow_env: Any) -> list[str]:
    """The secret names a job references in ``${{ }}``, and those the workflow ``env`` passes.

    Context for the row that names an agent step in the job, never compared.
    Each name is a published label (#802).
    """

    names: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str):
            for expression in _EXPRESSION_RE.findall(value):
                names.update(_SECRET_REFERENCE_RE.findall(expression))
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(job)
    walk(workflow_env)
    return sorted({published_workflow_label(name) for name in names})


def _action_identity(uses: Any) -> str | None:
    """A step's ``owner/repo`` in lower case when ``uses:`` is ``owner/repo@ref``."""

    if not isinstance(uses, str):
        return None
    text = uses.strip()
    if not _REMOTE_STEP_ACTION_RE.fullmatch(text):
        return None
    return text.rpartition("@")[0].casefold()


def _action_launch(job: str, step_label: str, agent: str, step: dict[Any, Any]) -> dict[str, Any]:
    """A documented agent action's launch: its documented inputs as published, and the rules they meet.

    The rules are read from each input's declared text before any of it is
    withheld for publication, so what redaction hides never hides a rule.
    """

    spec = _AGENT_ACTIONS[agent]
    entry: dict[str, Any] = {
        "job": job, "step": step_label, "agent": agent, "form": "read",
        "unresolved_reason": None, "settings": [],
    }
    inputs = step.get("with")
    if inputs is None:
        return entry
    if not isinstance(inputs, dict):
        return {**entry, "form": "unresolved", "unresolved_reason": "inputs_not_a_mapping"}
    wanted = {name.casefold(): name for name in spec.inputs}
    declared = [
        (wanted[str(key).casefold()], value)
        for key, value in inputs.items()
        if str(key).casefold() in wanted
    ]
    settings = [
        _published_setting(name, value, arguments=spec.family if name == spec.args else None)
        for name, value in declared
    ]
    return _with_rules(
        {**entry, "settings": sorted(settings, key=_setting_key)}, _action_rules(spec, declared)
    )


def _run_launches(job: str, step_label: str, run: str) -> list[dict[str, Any]]:
    """The agent CLIs a ``run:`` step launches: read when literal, else unresolved.

    Read only when the whole ``run:`` is one simple command that starts with
    the agent CLI (after literal ``NAME=value`` assignments), holds no shell
    expansion and no ``${{ }}`` expression: then its documented permission
    flags are its settings. Otherwise an agent CLI found at the start of any
    simple command in it is one ``unresolved`` entry with the reason, and none
    of its text is published.
    """

    run = run.strip()
    words = _shell_words(run)
    base = {"job": job, "step": step_label}
    if words is None:
        # Quoting that does not balance cannot be split into commands, so no
        # word of it is read; a line that starts an agent CLI is still named.
        agents = sorted({
            agent for line in run.splitlines()
            if (agent := _line_agent(line)) is not None
        })
        return [
            {**base, "agent": agent, "form": "unresolved", "unresolved_reason": "compound_command", "settings": []}
            for agent in agents
        ]
    commands: list[list[str]] = [[]]
    for word in words:
        if _is_operator(word):
            commands.append([])
        else:
            commands[-1].append(word)
    launches = [launch for command in commands if (launch := _agent_command(command))]
    if not launches:
        return []
    # A comment is an unquoted `#` at the start of a word, read off the raw
    # text: the splitter keeps `#` literal, so a quoted "#123 review" is one word.
    single = (
        len([command for command in commands if command]) == 1
        and not any(_is_operator(word) for word in words)
        and not _has_shell_comment(run)
    )
    if "${{" in run:
        reason: str | None = "expression"
    elif not single:
        reason = "compound_command"
    elif _has_shell_expansion(run):
        reason = "shell_expansion"
    else:
        reason = None
    if reason is not None:
        agents = sorted({agent for agent, _arguments in launches})
        return [
            {**base, "agent": agent, "form": "unresolved", "unresolved_reason": reason, "settings": []}
            for agent in agents
        ]
    (agent, arguments), = launches
    flags = _read_flags(arguments, _AGENT_FLAG_TABLES[agent])
    settings = [_published_flag(agent, name, arity, values) for name, arity, values in flags]
    return [_with_rules(
        {
            **base, "agent": agent, "form": "read", "unresolved_reason": None,
            "settings": sorted(settings, key=_setting_key),
        },
        _flag_rules(agent, flags),
    )]


def _step_agent_launches(job: str, step: dict[Any, Any], index: int) -> list[dict[str, Any]]:
    """The agent launches one step declares: a known action, or a ``run:`` agent CLI."""

    if "uses" in step:
        agent = _action_identity(step["uses"])
        if agent is not None and agent in _AGENT_ACTIONS:
            return [_action_launch(job, _step_label(step, index), agent, step)]
        return []
    run = step.get("run")
    if isinstance(run, str) and ("claude" in run or "codex" in run):
        return _run_launches(job, _step_label(step, index), run)
    return []


def _checkout_ref(job: str, step: dict[Any, Any], index: int) -> dict[str, Any] | None:
    """An ``actions/checkout`` step and the ``with.ref`` it declares, else ``None``."""

    if _action_identity(step.get("uses")) != "actions/checkout":
        return None
    entry: dict[str, Any] = {
        "job": job, "step": _step_label(step, index), "ref": None, "unresolved_reason": None,
    }
    inputs = step.get("with")
    if inputs is None:
        return entry
    if not isinstance(inputs, dict):
        return {**entry, "unresolved_reason": "inputs_not_a_mapping"}
    if "ref" not in inputs:
        return entry
    text = _setting_text(inputs["ref"])
    if text is None:
        return {**entry, "unresolved_reason": "not_a_string"}
    shown, redacted = _published_value(text)
    # A redacted ref is published redacted, as a step reference is, and makes
    # the workflow a blocking limit (#767): two refs may redact alike.
    return {**entry, "ref": shown or None, "unresolved_reason": "redacted" if redacted else None}


def agent_launch_key(entry: dict[str, Any]) -> tuple[Any, ...]:
    """What an agent launch is compared by: its job, agent, form, settings and rules, not its step.

    The widening rules are part of it: they are read from the declared text,
    so a rule gained where redaction withholds the text is still a change.
    """

    return (
        str(entry["job"]),
        str(entry["agent"]),
        str(entry["form"]),
        str(entry.get("unresolved_reason") or ""),
        tuple(sorted(_setting_key(setting) for setting in entry.get("settings", []))),
        tuple(sorted(
            (str(item["rule"]), str(item["setting"])) for item in entry.get("widening_rules", [])
        )),
    )


def checkout_ref_key(entry: dict[str, Any]) -> tuple[str, str, str]:
    """What a checkout ref is compared by: its job and the declared ref, not its step."""

    return (
        str(entry["job"]),
        str(entry.get("unresolved_reason") or ""),
        "" if entry.get("ref") is None else str(entry["ref"]),
    )


def _claude_action_rules(words: tuple[str, ...]) -> set[str]:
    """The widening rules the words a Claude action passes on meet.

    Read as ``parse-sdk-options.ts`` reads them: a word starting with ``--``
    is always a flag and never another flag's value, so
    ``--dangerously-skip-permissions`` counts wherever it stands, and
    ``--permission-mode`` takes the next word unless that starts with ``--``.
    """

    rules: set[str] = set()
    for index, word in enumerate(words):
        name, equals, attached = word.partition("=")
        if name == "--dangerously-skip-permissions":
            rules.add("bypass_permissions")
        elif name == "--permission-mode":
            following = words[index + 1] if index + 1 < len(words) else ""
            mode = attached if equals else ("" if following.startswith("--") else following)
            if mode == "bypassPermissions":
                rules.add("bypass_permissions")
    return rules


def _flag_rules(
    family: str, flags: list[tuple[str, int | None, list[str] | None]]
) -> set[tuple[str, str]]:
    """The widening rules a CLI's documented flags meet, each with the flag that met it."""

    rules: set[tuple[str, str]] = set()
    for name, _arity, values in flags:
        value = values[0] if values else None
        if family == "claude" and (
            name == "--dangerously-skip-permissions"
            or (name == "--permission-mode" and value == "bypassPermissions")
        ):
            rules.add(("bypass_permissions", name))
        if family == "codex" and name == "--dangerously-bypass-approvals-and-sandbox":
            rules.add(("bypass_approvals_and_sandbox", name))
        if family == "codex" and name == "--sandbox" and value == "danger-full-access":
            rules.add(("danger_full_access", name))
    return rules


def _action_rules(spec: _AgentAction, declared: list[tuple[str, Any]]) -> set[tuple[str, str]]:
    """The documented widening rules an agent action's declared inputs meet, read from the raw text.

    Read here, before anything is withheld for publication, so redaction never
    hides a rule. Only from literal text: GitHub substitutes a ``${{ }}``
    expression into the input before the action reads it, so a rule is read
    only where the substituted text cannot reach — an argument input's words
    before the first expression (:func:`_literal_argument_words`), a user
    gate's entries that hold none — and a mode input holding one meets none.
    Each rule names the input it was read from.
    """

    rules: set[tuple[str, str]] = set()
    for name, value in declared:
        text = _setting_text(value)
        if text is None:
            continue
        if name in spec.gates:
            # The substituted text may add entries; it cannot remove a literal one.
            entries = _EXPRESSION_SPAN_RE.sub(_EXPRESSION_MARK, text).split(",")
            if "*" in {entry.strip() for entry in entries}:
                rules.add(("open_gate", name))
        for mode, widening in spec.modes:
            if name == mode and text == widening:
                rules.add(("danger_full_access" if mode == "sandbox" else "unsafe_safety_strategy", name))
        if name == spec.args:
            words = _literal_argument_words(spec.family, text)
            if words is None:
                continue
            if spec.family == "claude":
                found = _claude_action_rules(words)
            else:
                found = {rule for rule, _flag in _flag_rules("codex", _read_flags(words, _CODEX_FLAGS))}
            rules.update((rule, name) for rule in found)
    return rules


def _with_rules(entry: dict[str, Any], rules: set[tuple[str, str]]) -> dict[str, Any]:
    """``entry`` with the rules it meets, omitted when none, as the schema omits them."""

    if not rules:
        return entry
    return {**entry, "widening_rules": [{"rule": rule, "setting": setting} for rule, setting in sorted(rules)]}


def agent_widening_rules(entry: dict[str, Any]) -> set[tuple[str, str]]:
    """The documented widening rules one published agent launch meets (#823).

    Each is ``(rule, detail)``: ``detail`` names the input for an opened gate
    (``allowed_non_write_users``) and is empty otherwise, so one rule spelled
    two ways, or moved between the CLI and an action, is one rule. Read from
    ``widening_rules``, which the reader decided from the declared text before
    any of it was withheld; an unresolved launch meets none.
    """

    if entry.get("form") != "read":
        return set()
    return {
        (str(item["rule"]), str(item["setting"]) if item["rule"] == "open_gate" else "")
        for item in entry.get("widening_rules", [])
    }


def agent_family(agent: str) -> str:
    """``claude`` or ``codex``: which agent's rules a launch is read by."""

    return _AGENT_ACTIONS[agent].family if agent in _AGENT_ACTIONS else agent


AgentWidening = tuple[str, str, str, dict[str, Any]]
#: A rule one job's launches of one agent family meet: ``(job, family, rule, detail)``.
_RuleKey = tuple[str, str, str, str]

#: The agent action inputs each documented rule is read from; a user gate's
#: rule is read from the gate it names.
_RULE_SETTINGS: dict[str, frozenset[str]] = {
    "bypass_permissions": frozenset({"claude_args"}),
    "bypass_approvals_and_sandbox": frozenset({"codex-args"}),
    "danger_full_access": frozenset({"sandbox", "codex-args"}),
    "unsafe_safety_strategy": frozenset({"safety-strategy"}),
}


def _expression_setting(entry: dict[str, Any], rule: str, detail: str) -> str | None:
    """The input of ``entry`` holding a ``${{ }}`` expression whose substituted text may meet ``rule``."""

    names = frozenset({detail}) if rule == "open_gate" else _RULE_SETTINGS.get(rule, frozenset())
    return next(
        (
            str(setting["name"]) for setting in entry.get("settings", [])
            if setting.get("holds_expression") and setting["name"] in names
        ),
        None,
    )


@dataclass(frozen=True)
class AgentRuleGains:
    """The documented rules a workflow's agent launches meet at ``after`` and not at ``before`` (#823).

    Each widening is ``(job, rule, detail, entry)`` for the first launch at
    ``after`` in that job that meets it. Only ``claimed`` is a widening:

    - ``unread_before``: the job launched that agent at ``before`` only in a
      form this reader does not read, such as a compound ``run:`` that became a
      literal one. That launch may have met the rule already, as a job whose
      permissions were not explicit may already have held a write scope
      (``unknown_before``). A job that also launched the agent in a form that
      was read claims the gain.
    - ``expression_before``: at ``before``, the job's launch of that agent held
      a ``${{ }}`` expression in an input the rule is read from, and GitHub's
      substituted text may already have met it. Each names that input.
    - ``moved``: the rule left another job whose launch that met it left that
      job — the job no longer exists or no longer launches that agent, or the
      same launch now runs here — as when a job is renamed or an agent step
      moves to another job (#823 review). Each names the launch it left, the
      way a step reference moved between jobs adds no scope (#771).
    """

    claimed: list[AgentWidening]
    unread_before: list[AgentWidening]
    expression_before: list[tuple[AgentWidening, str]]
    moved: list[tuple[AgentWidening, dict[str, Any]]]


def agent_rule_gains(before: dict[str, Any] | None, after: dict[str, Any] | None) -> AgentRuleGains:
    """Which documented rules the workflow's agent launches gain, and which of them are claimed.

    Keyed by job, agent family and rule, so moving a launch between steps or
    spellings (``--dangerously-skip-permissions`` and
    ``--permission-mode bypassPermissions`` are one rule) gains nothing.
    """

    def met(grant: dict[str, Any] | None) -> dict[_RuleKey, list[dict[str, Any]]]:
        found: dict[_RuleKey, list[dict[str, Any]]] = {}
        for entry in (grant or {}).get("agent_launches", []):
            for rule, detail in sorted(agent_widening_rules(entry)):
                key = (str(entry["job"]), agent_family(str(entry["agent"])), rule, detail)
                found.setdefault(key, []).append(entry)
        return found

    def launches(grant: dict[str, Any] | None) -> dict[tuple[str, str], list[dict[str, Any]]]:
        found: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for entry in (grant or {}).get("agent_launches", []):
            found.setdefault((str(entry["job"]), agent_family(str(entry["agent"]))), []).append(entry)
        return found

    old, new = met(before), met(after)
    launched_before, launched_after = launches(before), launches(after)
    lost = [key for key in old if key not in new]

    def left(key: _RuleKey, arriving: list[dict[str, Any]]) -> bool:
        # The launch that met the rule in the losing job left it: the job no
        # longer launches that agent, or the same launch now runs elsewhere.
        if (key[0], key[1]) not in launched_after:
            return True
        return any(
            agent_launch_key(entry)[1:] == agent_launch_key(other)[1:]
            for entry in old[key] for other in arriving
        )

    gains = AgentRuleGains(claimed=[], unread_before=[], expression_before=[], moved=[])
    for key, entries in new.items():
        if key in old:
            continue
        job, family, rule, detail = key
        widening: AgentWidening = (job, rule, detail, entries[0])
        source = next((other for other in lost if other[1:] == key[1:] and left(other, entries)), None)
        if source is not None:
            lost.remove(source)
            gains.moved.append((widening, old[source][0]))
            continue
        before_launches = launched_before.get((job, family), [])
        if before_launches and not any(entry.get("form") == "read" for entry in before_launches):
            gains.unread_before.append(widening)
            continue
        setting = next(
            (name for entry in before_launches if (name := _expression_setting(entry, rule, detail))), None
        )
        if setting is not None:
            gains.expression_before.append((widening, setting))
            continue
        gains.claimed.append(widening)
    return gains


def gained_agent_widenings(before: dict[str, Any] | None, after: dict[str, Any] | None) -> list[AgentWidening]:
    """Documented widening rules the workflow's agent launches gain and claim (``AgentRuleGains.claimed``)."""

    return agent_rule_gains(before, after).claimed


#: How an unresolved agent launch or checkout reads in the limit that names it.
_UNRESOLVED_AGENT_PHRASES = {
    "compound_command": "part of a `run:` that holds more than one command, or quoting this audit cannot split",
    "shell_expansion": "a command with a shell expansion this static audit does not evaluate",
    "expression": "a `run:` holding a `${{ }}` expression, which GitHub substitutes before the shell reads it",
    "inputs_not_a_mapping": "a step whose `with:` is not a mapping",
}


def uncompared_agent_launch_texts(grant: dict[str, Any]) -> list[str]:
    """One message per agent launch setting or checkout ref a workflow does not compare (#823).

    Not blocking, like an unread secret value (#693): the launch's job, agent,
    form, reason and widening rules are still compared, so adding, removing or
    re-forming one, or gaining a documented rule, is a row. Only an edit inside
    what is named here is not reported. That includes a setting holding
    credential-shaped text (#823 review): it is compared by its redacted text
    and its rules, so only an edit inside what is redacted is not reported. A
    redacted checkout ref is not named here: :func:`_uncompared_workflow_text`
    makes it a blocking limit, as a redacted step reference is (#767).
    """

    texts: list[str] = []
    for entry in grant.get("agent_launches", []):
        where = f"{entry['job']}/{entry['step']} ({entry['agent']})"
        reason = entry.get("unresolved_reason")
        if reason:
            texts.append(
                f"the agent launch at {where} is "
                f"{_UNRESOLVED_AGENT_PHRASES.get(str(reason), 'in an unsupported form')}; its "
                "settings are neither published nor compared, so an edit to them is not reported"
            )
        for setting in entry.get("settings", []):
            unread = setting.get("unresolved_reason")
            if unread == "redacted":
                texts.append(
                    f"the {setting['name']} value of the agent launch at {where} contains "
                    "credential-shaped text; it is published redacted and compared as published, "
                    "so an edit inside what is redacted that gains no documented widening rule "
                    "is not reported"
                )
                continue
            what = {
                "not_a_string": "is not a string",
                "unparsed_json": (
                    "holds text that starts like JSON, or a codex `--config` table or array, "
                    "and does not parse, so the values it may hold cannot be told apart from "
                    "its key names"
                ),
            }.get(str(unread))
            if what:
                texts.append(
                    f"the {setting['name']} value of the agent launch at {where} {what}; it is "
                    "neither published nor compared, so an edit to it that gains no documented "
                    "widening rule is not reported"
                )
    for entry in grant.get("checkout_refs", []):
        unread = entry.get("unresolved_reason")
        what = {
            "not_a_string": "a ref that is not a string",
            "inputs_not_a_mapping": "a `with:` that is not a mapping",
        }.get(str(unread))
        if what:
            texts.append(
                f"the checkout at {entry['job']}/{entry['step']} declares {what}; it is "
                "neither published nor compared, so an edit to it is not reported"
            )
    # Two steps whose labels publish alike name one limit once.
    return list(dict.fromkeys(texts))


#: A whole ``secrets:`` value of this form names its source (#693). Only the
#: property form is read; ``secrets['NAME']`` and every other expression stay
#: unresolved rather than guessed. The ``secrets`` context name is matched as
#: written: GitHub's expressions reference states case-insensitivity only for
#: string comparison, never for a context name, so ``${{ SECRETS.X }}`` is not
#: assumed to be the same reference.
_SECRET_SOURCE_RE = re.compile(r"\$\{\{\s*secrets\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

#: GitHub's contexts reference calls ``github.token`` "functionally equivalent
#: to the GITHUB_TOKEN secret", so both spellings name one source and moving
#: between them is not a change (#693).
_WORKFLOW_TOKEN_RE = re.compile(r"\$\{\{\s*github\.token\s*\}\}")
_WORKFLOW_TOKEN_SOURCE = "GITHUB_TOKEN"


def _secret_source_name(value: Any) -> str | None:
    """The declared secret a whole ``secrets:`` value names, or ``None``."""

    if not isinstance(value, str):
        return None
    text = value.strip()
    match = _SECRET_SOURCE_RE.fullmatch(text)
    if match is not None:
        return match.group(1)
    return _WORKFLOW_TOKEN_SOURCE if _WORKFLOW_TOKEN_RE.fullmatch(text) else None


def _secret_mapping(destination: object, value: Any) -> dict[str, Any]:
    """One ``destination: value`` a reusable call passes, without its value.

    Only a ``${{ secrets.NAME }}`` source name is published. A literal value
    may be the credential itself and an expression may contain one, so for
    anything else nothing of the value is kept — no text, no digest — and the
    entry says only why it is unresolved.
    """

    shown, destination_redacted = _published(str(destination))
    entry: dict[str, Any] = {
        "destination": shown, "source": None, "form": "unresolved", "unresolved_reason": None,
    }
    name = _secret_source_name(value)
    if name is not None:
        source, source_redacted = _published(name)
        if destination_redacted or source_redacted:
            return {**entry, "source": source, "unresolved_reason": "redacted"}
        return {**entry, "source": source, "form": "secret"}
    if destination_redacted:
        return {**entry, "unresolved_reason": "redacted"}
    if not isinstance(value, str):
        return {**entry, "unresolved_reason": "not_a_string"}
    if "${{" in value:
        return {**entry, "unresolved_reason": "expression"}
    return {**entry, "unresolved_reason": "literal_value"}


def secret_mapping_key(entry: dict[str, Any]) -> tuple[str, str, str, str]:
    """What a named secret mapping is compared by: every published fact.

    The source is folded: GitHub's secrets reference says names "are case
    insensitive when referenced" and are stored uppercase, so ``staging_token``
    and ``STAGING_TOKEN`` are one declared secret and renaming the case is not
    a remap. The destination is the callee's ``workflow_call`` secret id, which
    GitHub does not document as case-insensitive, so it stays as written and a
    case-only edit there reads as a removal plus an addition.
    """

    return (
        "" if entry.get("destination") is None else str(entry["destination"]),
        str(entry["form"]),
        str(entry.get("unresolved_reason") or ""),
        "" if entry.get("source") is None else str(entry["source"]).casefold(),
    )


def _secret_mappings(value: Any) -> list[dict[str, Any]]:
    """The named secrets a reusable call passes, in a canonical order.

    Sorted, so reordering the ``secrets:`` keys changes nothing. A value that
    is neither ``inherit`` nor a mapping is one unresolved entry with no
    destination, so an absent list always means "read, none declared".
    """

    if not isinstance(value, dict):
        return [{
            "destination": None, "source": None, "form": "unresolved",
            "unresolved_reason": "secrets_not_a_mapping",
        }]
    return sorted(
        (_secret_mapping(destination, item) for destination, item in value.items()),
        key=secret_mapping_key,
    )


def _reusable_call(job_name: str, job: dict[Any, Any]) -> dict[str, Any] | None:
    """A job's reusable-workflow call: its target and what secrets it passes.

    ``job_name`` is the job's published label (#802).
    """

    uses = job.get("uses")
    if not (isinstance(uses, str) and uses.strip()):
        return None
    display, redacted = _published(uses.strip())
    secrets = job.get("secrets")
    call: dict[str, Any] = {"job": job_name, "uses": display, "secrets_inherit": secrets == "inherit"}
    if redacted:
        call["uses_redacted"] = True
    if "secrets" in job and secrets != "inherit":
        mappings = _secret_mappings(secrets)
        if mappings:
            call["secret_mappings"] = mappings
    return call


def _joined(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _uncompared_workflow_text(
    grant: dict[str, Any], collided: set[str] | frozenset[str] = frozenset()
) -> str | None:
    """Why part of a workflow grant is published but cannot be compared, or ``None``.

    One rule for every compared workflow reference (#767, #693). A redacted
    step reference, reusable target, secret name or checkout ref (#823) could
    publish the same text as a different one, so comparing the display would
    read a change to the code a job runs, or to where a secret goes, as equal.
    That is a blocking limit: a changed workflow refuses, and an unchanged one
    is named (#721).

    A redacted agent launch setting is not counted here (#823 review). Its
    documented widening rules are read from the declared text before it is
    redacted, so comparing its published text and its rules loses no
    direction, and ordinary prose such as "never print bearer tokens" in a
    system prompt is as credential-shaped to the label redaction as a token
    is. :func:`uncompared_agent_launch_texts` names it, not blocking, as a
    redacted label is compared by what it publishes (#802).

    A job id, trigger or permission scope name is compared by its published
    label (#802). One redacted label is still a distinct label, so it refuses
    nothing; ``collided`` names the kinds of which two distinct raw labels in
    this workflow publish alike, which would merge two jobs, triggers or
    scopes into one, and those refuse the same way.
    """

    calls = grant.get("reusable_calls", [])
    mappings = [entry for call in calls for entry in call.get("secret_mappings", [])]
    redacted = [
        label for label, present in (
            ("a step action reference", any(
                item["unresolved_reason"] == "redacted" for item in grant.get("step_actions", [])
            )),
            ("a reusable workflow reference", any(call.get("uses_redacted") for call in calls)),
            ("a reusable workflow secret name", any(
                entry["unresolved_reason"] == "redacted" for entry in mappings
            )),
            # A checkout ref names the code a job runs, as a step reference does (#823).
            ("a checkout ref", any(
                item.get("unresolved_reason") == "redacted" for item in grant.get("checkout_refs", [])
            )),
        ) if present
    ]
    merged = [kind for kind in _LABEL_KINDS if kind in collided]
    reasons = []
    if len(redacted) == 1:
        reasons.append(
            f"{redacted[0]} contains credential-shaped text; it is published redacted and cannot be compared"
        )
    elif redacted:
        reasons.append(
            f"{_joined(redacted)} contain credential-shaped text; "
            "they are published redacted and cannot be compared"
        )
    if merged:
        reasons.append(
            f"distinct {_joined(merged)} in this workflow publish alike once credential-shaped "
            "text is redacted, so they cannot be compared apart; rename or remove one so "
            "each publishes a distinct label"
        )
    return "; ".join(reasons) or None


#: How an unresolved secret mapping reads in the limit that names it (#693).
_UNRESOLVED_SECRET_PHRASES = {
    "literal_value": "a literal value",
    "expression": "an expression this static audit does not evaluate",
    "not_a_string": "a value that is not a string",
    "secrets_not_a_mapping": "neither `inherit` nor a mapping of secret names",
}


def uncompared_secret_mapping_texts(grant: dict[str, Any]) -> list[str]:
    """One message per secret value a workflow publishes nothing of (#693).

    Not blocking. The destination, the form and the reason are compared, so
    adding an entry, removing one, or moving one between forms is still a row,
    and the rest of the workflow — token permissions, triggers, targets, step
    references, every other host — compares as it always did. Only an edit
    between two values of the *same* unsupported form is invisible, and this
    names where that is, the way an unread `envFile` does, rather than making
    the surface blocking and refusing every row beside it (#789).
    """

    texts: list[str] = []
    for call in grant.get("reusable_calls", []):
        for entry in call.get("secret_mappings", []):
            reason = str(entry.get("unresolved_reason") or "")
            if entry["form"] != "unresolved" or reason == "redacted":
                continue
            where = (
                f"{call['job']}/{entry['destination']}"
                if entry.get("destination") is not None
                else f"{call['job']}/secrets"
            )
            phrase = _UNRESOLVED_SECRET_PHRASES.get(reason, "an unsupported form")
            texts.append(
                f"the secret a reusable workflow call passes at {where} is {phrase}; its "
                "value is neither published nor compared, so an edit between two such "
                "values is not reported"
            )
    return texts


def _workflow_grant(
    data: Any, *, source: str, collided: set[str] | None = None
) -> dict[str, Any] | None:
    """One workflow's grant, every job id, trigger and scope name published once (#802).

    Each label is published by :func:`published_workflow_label` before it is
    used anywhere, so the job in ``permission_contexts``, ``reusable_calls``,
    ``step_actions``, ``agent_launches``, ``checkout_refs``, the
    ``write_scopes`` and ``effective_write_scopes`` prefixes, every row built
    from them, and ``config_sha256`` all hold the same label, and none holds
    the raw text. ``collided`` receives the kinds of label of which two
    distinct raw values publish alike.
    """

    if not isinstance(data, dict):
        return None
    collided = set() if collided is None else collided
    data = _normalize_workflow_keys(data)
    trigger_labels = _published_labels(
        _trigger_names(data.get("on")), kind=_TRIGGER_LABELS, collided=collided
    )
    # Not de-duplicated: two triggers that publish alike stay two entries.
    triggers = sorted(trigger_labels.values())
    write_scopes: list[str] = []
    effective_write_scopes: list[str] = []
    permission_contexts: list[dict[str, Any]] = []
    reusable_calls: list[dict[str, Any]] = []
    step_actions: list[dict[str, Any]] = []
    agent_launches: list[dict[str, Any]] = []
    checkout_refs: list[dict[str, Any]] = []

    def collect(perms: Any, where: str) -> None:
        if perms == "write-all":
            write_scopes.append(f"{where}: write-all")
        elif isinstance(perms, dict):
            for scope_name, value in sorted(perms.items(), key=lambda item: str(item[0])):
                if _is_write(value):
                    write_scopes.append(f"{where}: {published_workflow_label(str(scope_name))}: {value}")

    collect(data.get("permissions"), "<top-level>")
    jobs = data.get("jobs")
    if isinstance(jobs, dict):
        declared = [(str(name), job) for name, job in jobs.items() if isinstance(job, dict)]
        labels = _published_labels(
            (name for name, _ in declared), kind=_JOB_LABELS, collided=collided
        )
        # Ordered by what is published, so a redacted job's place in the
        # lists says nothing more about its raw id than its label does.
        for job_name, job in sorted(declared, key=lambda item: (labels[item[0]], item[0])):
            label = labels[job_name]
            collect(job.get("permissions"), label)
            value = job.get("permissions")
            effective = _workflow_permissions(
                data.get("permissions") if value is None else value, label, collided,
            )
            permission_contexts.append(effective)
            effective_write_scopes.extend(
                f"{label}: " + ("write-all" if scope == "*" else f"{scope}: write")
                for scope, level in effective["permissions"].items() if level == "write"
            )
            call = _reusable_call(label, job)
            if call is not None:
                reusable_calls.append(call)
            if "steps" in job:
                steps = job["steps"]
                if not isinstance(steps, list):
                    step_actions.append(_unreadable_step(label, "steps", "steps_not_a_list"))
                    steps = []
                job_launches: list[dict[str, Any]] = []
                for index, step in enumerate(steps):
                    if not isinstance(step, dict):
                        step_actions.append(
                            _unreadable_step(label, f"steps[{index}]", "step_not_a_mapping")
                        )
                        continue
                    action = _step_action(label, step, index)
                    if action is not None:
                        step_actions.append(action)
                    job_launches.extend(_step_agent_launches(label, step, index))
                    checkout = _checkout_ref(label, step, index)
                    if checkout is not None:
                        checkout_refs.append(checkout)
                if job_launches:
                    # Context for the note on the row, never compared (#823).
                    secrets = _job_secrets(job, data.get("env"))
                    agent_launches.extend(
                        {**launch, "job_secrets": secrets} if secrets else launch
                        for launch in job_launches
                    )
    pull_target = "pull_request_target" in triggers
    write_all = any(entry.endswith(": write-all") for entry in effective_write_scopes)
    unknown = not permission_contexts or any(
        context["state"] != "explicit" for context in permission_contexts
    )
    has_read = any(context["permissions"] for context in permission_contexts)
    inherits_secrets = any(call["secrets_inherit"] for call in reusable_calls)
    projection = {
        "triggers": triggers,
        "pull_request_target": pull_target,
        "write_all": write_all,
        "write_scopes": sorted(write_scopes),
        "permission_contexts": permission_contexts,
        "effective_write_scopes": sorted(effective_write_scopes),
        "reusable_calls": reusable_calls,
    }
    if step_actions:
        # Omitted when empty, as the schema omits it, so a workflow whose steps
        # declare no listed reference keeps its earlier fingerprint.
        projection["step_actions"] = step_actions
    # Omitted when empty for the same reason (#823).
    if agent_launches:
        projection["agent_launches"] = agent_launches
    if checkout_refs:
        projection["checkout_refs"] = checkout_refs
    return {
        **_grant_base(
            host="github", scope="repository", source=source, kind="workflow",
            identity=source, config={key: value for key, value in projection.items() if key != "write_scopes"},
            access="admin" if write_all else (
                "write" if effective_write_scopes or pull_target else (
                    "external" if inherits_secrets else (
                        "unknown" if unknown else ("read" if has_read else "none")
                    )
                )
            ),
            risk="critical" if write_all or pull_target else (
                "high" if effective_write_scopes or inherits_secrets else ("unknown" if unknown else "low")
            ),
        ),
        **projection,
    }


def _instruction_grant(*, host: str, scope: HostScope, source: str, data: str, structure: dict | None = None) -> dict[str, Any]:
    redacted_text = _sanitize_sensitive_string(data)
    return {
        **_grant_base(
            host=host, scope=scope, source=source, kind="instruction_trust_root",
            identity=source,
            config=(structure if structure is not None else {"content_sha256": hashlib.sha256(redacted_text.encode()).hexdigest()}),
            access="execute", risk="medium",
        ),
        "path": public_host_path(source),
    }


def unresolved_structure_message(reason: str) -> str:
    """What an unresolved instruction structure says to the author (#812 follow-up).

    The inventory issue's message is what a reviewer reads in `audit --host`,
    in `unchanged_limits[].detail` and in a refused comparison's coverage
    ``detail``, so it is the sentence this engine stands behind about a file.

    Only a file whose own text would not parse is one an author can repair,
    and this profile refuses far more than that: a documented field written in
    a shape it does not accept, a key it does not list, an anchor it will not
    expand, a duplicate key it will not choose between, a role it does not
    read, a header that parses to something other than a mapping, a value the
    digest cannot encode. On one corpus repository
    forty of forty-three refusals were of that kind, on files whose YAML is
    legal, and every one of them told the author to repair the file. Where the
    limit may be this entry's rather than the file's, the message states what
    could not be established and stops there; correcting the profiles
    themselves is #822.

    The sentence holds on every route that prints it. `audit --host` compares
    nothing, so it says no claim is made here rather than naming a comparison
    that route never runs.
    """

    if unresolved_reason_is_invalid_syntax(reason):
        return (
            f"Instruction structure is unresolved ({reason}); the file's own text "
            "could not be parsed. Repair or review this declared surface."
        )
    return (
        f"Instruction structure is unresolved ({reason}); what this file declares "
        "could not be established, so no claim is made here about it. The "
        "limit may be this entry's rather than the file's, so no repair is prescribed."
    )


def _collect_file(
    *, path: Path, source: str, host: str, scope: HostScope, kind: str,
    containment_root: Path, cache: HostStaticParseCache,
    artifacts: list[dict[str, Any]], grants: list[dict[str, Any]], issues: list[dict[str, Any]],
    resolved_through: tuple[str, ...] = (),
    hook_basis: HookLoadingBasis = "host_configuration",
) -> Any:
    if kind == "instructions":
        text, error = cache.read(path, containment_root=containment_root)
        if error:
            issues.append(cache.read_issue(
                path=path, containment_root=containment_root,
                kind="unreadable", host=host, source=source, message=error,
            ))
            artifacts.append(_artifact(
                host=host, scope=scope, source=source, kind=kind, status="failed",
                resolved_through=resolved_through,
            ))
            return
        assert text is not None
        redacted_text = _sanitize_sensitive_string(text)
        artifact = _artifact(
            host=host, scope=scope, source=source, kind=kind, status="parsed",
            data={"sha256": hashlib.sha256(redacted_text.encode()).hexdigest()},
            resolved_through=resolved_through,
        )
        structure = classify_instruction(source, text)
        if structure is not None:
            artifact["instruction_structure"] = structure.projection()
            if structure.status == "unresolved":
                artifact["parse_status"] = "unsupported"
                issues.append(_inventory_issue(
                    kind="unsupported", host=host, source=source,
                    message=unresolved_structure_message(structure.reason),
                    blocking=True,
                ))
        artifacts.append(artifact)
        if structure is None or structure.status != "guidance":
            grants.append(_instruction_grant(
                host=host, scope=scope, source=source, data=text,
                structure=structure.projection() if structure is not None else None,
            ))
        return text

    data = _load_structured(
        path=path, source=source, host=host, kind=kind, scope=scope,
        containment_root=containment_root, cache=cache,
        artifacts=artifacts, issues=issues, resolved_through=resolved_through,
    )
    if data is None:
        return
    if host == "claude-code" and isinstance(data, dict) and "policyHelper" in data:
        issues.append(
            _inventory_issue(
                kind="unsupported",
                host="claude-code",
                source=source,
                message=(
                    "policyHelper is executable/dynamic policy input; the static "
                    "audit does not run it and cannot establish complete coverage"
                ),
                blocking=True,
            )
        )
    if kind == "mcp":
        grants.extend(_mcp_grants(data, host=host, scope=scope, source=source))
        if host == "vscode":
            vscode_grants, vscode_issues = _vscode_mcp_extras(data, scope=scope, source=source)
            grants.extend(vscode_grants)
            issues.extend(vscode_issues)
    elif kind == "workflow":
        collided: set[str] = set()
        grant = _workflow_grant(data, source=source, collided=collided)
        if grant is not None:
            grants.append(grant)
            uncompared = _uncompared_workflow_text(grant, collided)
            if uncompared is not None:
                # Refuse rather than let a distinct change compare as equal
                # (#767), or two jobs, triggers or scopes compare as one (#802).
                issues.append(_inventory_issue(
                    kind="unsupported", host=host, source=source, message=uncompared, blocking=True,
                ))
            # An unread secret value narrows one entry, not the workflow, so it
            # is named rather than blocking: coverage counts only blocking
            # issues, and every other row on this file still reaches the
            # reviewer (#693).
            issues.extend(
                _inventory_issue(
                    kind="unsupported", host=host, source=source, message=text, blocking=False,
                )
                for text in (
                    *uncompared_secret_mapping_texts(grant),
                    # An agent launch or checkout ref this reader does not
                    # compare narrows that entry, not the workflow (#823).
                    *uncompared_agent_launch_texts(grant),
                )
            )
    elif host == "codex" and kind == "requirements":
        grants.extend(_codex_requirement_grants(data, scope=scope, source=source))
    elif host == "codex" and path.suffix == ".toml":
        grants.extend(_codex_grants(data, scope=scope, source=source))
        if isinstance(data, dict) and isinstance(data.get("profile"), str):
            selected = data["profile"].strip()
            profiles = data.get("profiles")
            if selected and not (
                isinstance(profiles, dict) and isinstance(profiles.get(selected), dict)
            ):
                issues.append(
                    _inventory_issue(
                        kind="unsupported",
                        host="codex",
                        source=source,
                        message=(
                            f"selected profile {selected!r} has no statically "
                            "resolvable [profiles] declaration"
                        ),
                        blocking=True,
                    )
                )
    elif kind == "hooks":
        # A hook file contributes hooks only. Claude Code reads `permissions`,
        # `enabledPlugins` or `sandbox` from settings, never from a hook file,
        # so reading one as settings published authority no host grants (#714).
        grants.extend(_hooks_grants(data, host=host, scope=scope, source=source, basis=hook_basis))
    elif host == "claude-code":
        grants.extend(_claude_grants(data, scope=scope, source=source))
    elif host == "cursor":
        grants.extend(_cursor_grants(data, scope=scope, source=source))
    return data


def _claude_plugin_hook_issue(*, source: str, message: str, blocking: bool) -> dict[str, Any]:
    return _inventory_issue(
        kind="unsupported", host="claude-code", source=source, message=message, blocking=blocking,
    )


@dataclass
class _PluginHookSelection:
    """What the plugin configuration in one repository selects (#714)."""

    #: ``{hook file: the manifest or marketplace entry that selects it}``.
    selected: dict[str, str] = field(default_factory=dict)
    #: The selected hook files that a plugin this repository's project
    #: settings enable selects, published as ``project_enabled_plugin``.
    enabled: set[str] = field(default_factory=set)
    #: The manifests and marketplaces whose inline hooks such a plugin loads,
    #: by file (#809): an inline hook's `source` is the manifest, or
    #: `<marketplace>#plugins.<name>`.
    enabled_inline: set[str] = field(default_factory=set)
    #: The hook files such a plugin selects that the reader refused to open,
    #: by name or by a skipped directory (#809). Each is a named limit.
    enabled_unread: set[str] = field(default_factory=set)
    #: Every issue raised while resolving that selection.
    reference_issue_ids: set[str] = field(default_factory=set)
    #: ``{issue id: the plugin directories whose references raised it}`` (#808).
    #: ``None`` stands for a reference that names a path outside its plugin,
    #: which no directory bounds.
    issue_roots: dict[str, set[str | None]] = field(default_factory=dict)
    #: ``{hook file: every plugin directory that selects it}``, as spelled (#808).
    roots: dict[str, set[str]] = field(default_factory=dict)
    #: ``{inline hook selector: its plugin directory}``: a manifest, or a
    #: marketplace entry ``<marketplace>#plugins.<name>`` (#808).
    inline_roots: dict[str, str] = field(default_factory=dict)


def _plugin_relative_path(reference: str, *, allow_root: bool = False) -> str | None:
    """A `./` path inside a plugin directory, normalized; ``""`` for the root."""

    if not reference.startswith("./") or "\\" in reference:
        return None
    relative = posixpath.normpath(reference[2:] or ".")
    if relative == ".":
        return "" if allow_root else None
    if relative == ".." or relative.startswith("../") or posixpath.isabs(relative):
        return None
    return relative


def _settings_marketplace_path(reference: str) -> str | None:
    """A settings `directory`/`file` marketplace path inside the repository; ``""`` for the root.

    Claude Code resolves a relative local marketplace path against the
    repository's main checkout (plugin-marketplaces, "Configure team
    marketplaces"). An absolute or home-relative path names a machine, not
    this repository, and a backslash, a drive or a path leaving the checkout
    cannot be proved to land inside it (#714).
    """

    if not reference or "\\" in reference or ":" in reference or reference.startswith(("/", "~")):
        return None
    relative = posixpath.normpath(reference)
    if relative == ".":
        return ""
    if relative == ".." or relative.startswith("../"):
        return None
    return relative


#: The project settings layers whose plugin enablement the repository holds,
#: highest precedence first (#714). `.claude/settings.local.json` is usually
#: uncommitted, but Claude Code reads it whenever it is present, so reading
#: it errs toward showing a hook the host would load.
_CLAUDE_PROJECT_SETTINGS_SOURCES = (".claude/settings.local.json", ".claude/settings.json")


def _resolve_claude_plugin_hooks(
    *, candidates: dict[str, tuple[Path, tuple[str, ...]]], root: Path,
    cache: HostStaticParseCache, artifacts: list[dict[str, Any]],
    grants: list[dict[str, Any]], issues: list[dict[str, Any]],
    project_settings: tuple[Any, ...] = (),
    unread_links: frozenset[str] = frozenset(),
) -> _PluginHookSelection:
    """Which hook files Claude Code plugin configuration selects, statically (#714).

    Per the plugins reference, a plugin loads `hooks/hooks.json` relative to
    its root. A `hooks` member adds a `./`-relative path, an array of such
    paths, or an inline object, and custom paths add to the default rather
    than replace it. That member may sit in the plugin's own
    `.claude-plugin/plugin.json` or in its marketplace entry.

    A plugin root is recognised in two ways. One is its manifest. The other
    is a `.claude-plugin/marketplace.json` entry whose `source` is a `./` path,
    or a bare name under `metadata.pluginRoot`. A remote source names nothing
    in the repository. `strict` changes which file is authoritative, not what
    is selected, so both are read and every selection is kept visible.

    Selection is what the repository can prove. Whether the plugin is
    installed or enabled is usually not in the repository, so a selected hook
    is published as ``plugin_selected``, never as loaded.

    The exception is a plugin the repository itself enables (settings
    reference, `enabledPlugins` and `extraKnownMarketplaces`). When a project
    settings layer sets `<plugin>@<marketplace>` to `true`, and a project
    settings layer registers that marketplace as a `directory` or `file`
    source resolving to a `.claude-plugin/marketplace.json` in this
    repository, and that marketplace lists the plugin with a source inside the
    repository, the plugin's selected hooks are ``project_enabled_plugin``.
    Claude Code leaves only a plugin from an external source waiting for a
    manual install (discover-plugins, "Configure team marketplaces"), so this
    one loads once the folder is trusted. A remote or `settings` marketplace
    source, an absolute path, a plugin the marketplace does not list, and a
    value other than `true` establish nothing. A `true` in either layer
    counts: a `false` in `.claude/settings.local.json` is one machine's
    opt-out, not what the committed file enables for everyone else.

    Limits:

    * A manifest reference that cannot be followed blocks, because the hooks
      it names are unread. The issue names the exact file when there is one,
      so an unchanged-limit comparison checks that file's bytes.
    * A marketplace limit never blocks. A marketplace lists plugins for many
      repositories, and a refusal from it would stop comparisons that touch
      no plugin.
    * A reference to a missing file selects nothing a host could load, and a
      reference into a directory the walk skips (`node_modules`, `.venv`) is
      unread by design. Both are named without blocking.

    Matching a reference to a file is case-insensitive when exactly one
    file matches. A host on a case-sensitive filesystem would load nothing
    from a miscased reference, one on macOS or Windows would, and reading it
    errs toward showing the hook rather than hiding it.
    """

    result = _PluginHookSelection()
    by_path = dict(candidates)
    by_folded: dict[str, list[str]] = {}
    for relative in by_path:
        by_folded.setdefault(relative.casefold(), []).append(relative)
    #: ``{hook file: the plugin roots that select it}``, folded, so a basis is
    #: decided once every enabled root is known.
    roots_by_file: dict[str, set[str]] = {}
    #: Inline hook objects, kept until the same is known for their root.
    inline: list[tuple[Any, str, str]] = []
    #: ``{hook file the reader refused: the plugin roots that select it}``.
    unread_roots_by_file: dict[str, set[str]] = {}
    enabled_roots: set[str] = set()
    folded_links = {link.casefold() for link in unread_links}

    def existing(relative: str) -> str | None:
        if relative in by_path:
            return relative
        matches = by_folded.get(relative.casefold(), [])
        return matches[0] if len(matches) == 1 else None

    def issue(*, source: str, message: str, blocking: bool, plugin_root: str | None) -> None:
        """Raise a plugin-reference issue, bounded by ``plugin_root`` (#808).

        ``None`` for a reference that names a path outside its plugin: what
        it would select is not bounded by any directory.
        """

        item = _claude_plugin_hook_issue(source=source, message=message, blocking=blocking)
        issues.append(item)
        result.reference_issue_ids.add(item["issue_id"])
        result.issue_roots.setdefault(item["issue_id"], set()).add(plugin_root)

    def under(plugin_root: str, relative: str) -> str:
        return posixpath.join(plugin_root, relative) if plugin_root else relative

    def select(hook_file: str, *, plugin_root: str, selector: str) -> None:
        result.selected.setdefault(hook_file, selector)
        roots_by_file.setdefault(hook_file, set()).add(plugin_root.casefold())
        result.roots.setdefault(hook_file, set()).add(plugin_root)

    def select_default(plugin_root: str, selector: str) -> None:
        default = existing(under(plugin_root, CLAUDE_PLUGIN_DEFAULT_HOOKS))
        if default is not None:
            select(default, plugin_root=plugin_root, selector=selector)

    def in_repository_marketplace(config: Any) -> str | None:
        source = config.get("source") if isinstance(config, dict) else None
        if not isinstance(source, dict) or not isinstance(source.get("path"), str):
            return None  # `github`, `git`, `url` and `settings` name nothing provable here.
        relative = _settings_marketplace_path(source["path"])
        if relative is None:
            return None
        if source.get("source") == "directory":
            return existing(under(relative, CLAUDE_PLUGIN_MARKETPLACE))
        if source.get("source") == "file" and is_claude_plugin_marketplace_path(relative):
            return existing(relative)
        return None

    # `{marketplace name: plugin entry names}` the project settings enable, and
    # `{marketplace file: the names the project settings register it under}`.
    enabled_ids: set[str] = set()
    registered: dict[str, set[str]] = {}
    for layer in project_settings:
        if not isinstance(layer, dict):
            continue
        plugins = layer.get("enabledPlugins")
        if isinstance(plugins, dict):
            enabled_ids.update(str(name) for name, value in plugins.items() if value is True)
        for key in ("extraKnownMarketplaces", "additionalMarketplaces"):
            marketplaces = layer.get(key)
            if not isinstance(marketplaces, dict):
                continue
            for name, config in marketplaces.items():
                located = in_repository_marketplace(config)
                if located is not None:
                    registered.setdefault(located, set()).add(str(name))
    enabled_plugins: dict[str, set[str]] = {}
    for plugin_id in sorted(enabled_ids):
        plugin, separator, marketplace_name = plugin_id.rpartition("@")
        if separator and plugin:
            enabled_plugins.setdefault(marketplace_name, set()).add(plugin)

    def enabled_entries(marketplace: str, data: Any) -> set[str]:
        """The plugin entry names the project settings enable from this
        marketplace file.

        The marketplace must be registered in the project settings as an
        in-repository source; an unregistered `marketplace.json` enables
        nothing. It is then identified both by the `extraKnownMarketplaces`
        key that registers it and by its own `name`. Claude Code's
        `marketplace.json` schema calls that `name` the identifier users see
        after the `@` (https://code.claude.com/docs/en/plugin-marketplaces),
        and the settings documentation only ever shows a key equal to it, so
        neither name is documented as the one `enabledPlugins` matches.
        Reading both errs toward showing a hook the host would load.
        """

        names = registered.get(marketplace)
        if not names:
            return set()
        own = data.get("name") if isinstance(data, dict) else None
        if isinstance(own, str):
            names = names | {own}
        return {plugin for name in names for plugin in enabled_plugins.get(name, ())}

    def through_unread_link(target: str) -> bool:
        folded = target.casefold()
        return any(folded.startswith(f"{link}/") for link in folded_links)

    def follow(reference: str, *, plugin_root: str, selector: str, blocking: bool) -> None:
        shown = _sanitize_sensitive_string(reference)
        relative = _plugin_relative_path(reference)
        if relative is None:
            issue(
                source=selector.split("#", 1)[0], blocking=blocking, plugin_root=None,
                message=(
                    f"{selector} names hooks at {shown!r}, which is not a `./` path inside the "
                    "plugin directory; the hooks it names were not read"
                ),
            )
            return
        target = under(plugin_root, relative)
        skipped = next(
            (part for part in target.split("/") if part in _WALK_SKIPPED_DIRECTORIES), None
        )
        if skipped is not None:
            unread_roots_by_file.setdefault(target, set()).add(plugin_root.casefold())
            issue(
                source=target, blocking=False, plugin_root=plugin_root,
                message=(
                    f"{selector} selects this hook file inside `{skipped}`, a directory the "
                    "static reader does not walk; its hooks were not read"
                ),
            )
            return
        if not is_hook_declaration_file_name(target):
            unread_roots_by_file.setdefault(target, set()).add(plugin_root.casefold())
            issue(
                source=target, blocking=blocking, plugin_root=plugin_root,
                message=(
                    f"{selector} selects this hook file, whose name is not `hooks.json` or "
                    "`<name>-hooks.json`; the static reader follows only such names, so its "
                    "hooks were not read"
                ),
            )
            return
        found = existing(target)
        if found is None:
            if through_unread_link(target):
                # The walk reports that link as a blocking limit. A second,
                # non-blocking "not in the repository" would contradict it.
                return
            issue(
                source=target, blocking=False, plugin_root=plugin_root,
                message=(
                    f"{selector} selects this hook file, which is not in the repository; "
                    "no hooks were read from it"
                ),
            )
            return
        select(found, plugin_root=plugin_root, selector=selector)

    def follow_hooks(
        declared: Any, *, plugin_root: str, selector: str, blocking: bool
    ) -> None:
        if isinstance(declared, dict):
            inline.append((declared, plugin_root, selector))
            result.inline_roots[selector] = plugin_root
            return
        if isinstance(declared, str):
            references = [declared]
        elif isinstance(declared, list) and all(isinstance(item, str) for item in declared):
            references = list(declared)
        else:
            issue(
                source=selector.split("#", 1)[0], blocking=blocking, plugin_root=plugin_root,
                message=(
                    f"{selector} `hooks` must be a `./` path, an array of such paths or an "
                    "object of hook events; the hooks it names were not read"
                ),
            )
            return
        for reference in sorted(set(references)):
            follow(reference, plugin_root=plugin_root, selector=selector, blocking=blocking)

    for manifest in sorted(relative for relative in by_path if is_claude_plugin_manifest_path(relative)):
        path, resolved_through = by_path[manifest]
        plugin_root = posixpath.dirname(posixpath.dirname(manifest))
        data, error_kind, error_message = cache.parse(path, containment_root=root)
        if error_kind is not None:
            assert error_message is not None
            failure = cache.read_issue(
                path=path, containment_root=root, kind=error_kind, host="claude-code",
                source=manifest,
                message=(
                    f"{error_message}; the hook files this plugin manifest may select were not read"
                ),
            )
            issues.append(failure)
            result.reference_issue_ids.add(failure["issue_id"])
            result.issue_roots.setdefault(failure["issue_id"], set()).add(plugin_root)
            artifacts.append(_artifact(
                host="claude-code", scope="repository", source=manifest, kind="config",
                status="failed", resolved_through=resolved_through,
            ))
            continue
        if not isinstance(data, dict):
            issue(
                source=manifest, blocking=True, plugin_root=plugin_root,
                message=(
                    "Cannot interpret a Claude Code plugin manifest that is not an object; "
                    "the hook files it may select were not read."
                ),
            )
            artifacts.append(_artifact(
                host="claude-code", scope="repository", source=manifest, kind="config",
                status="unsupported", resolved_through=resolved_through,
            ))
            continue
        select_default(plugin_root, manifest)
        if "hooks" not in data:
            # Only the `hooks` member is published, so editing a plugin's
            # version or description is not host-grant drift.
            continue
        artifacts.append(_artifact(
            host="claude-code", scope="repository", source=manifest, kind="config",
            status="parsed", data={"hooks": data["hooks"]}, resolved_through=resolved_through,
        ))
        follow_hooks(data["hooks"], plugin_root=plugin_root, selector=manifest, blocking=True)

    for marketplace in sorted(
        relative for relative in by_path if is_claude_plugin_marketplace_path(relative)
    ):
        path, resolved_through = by_path[marketplace]
        marketplace_root = posixpath.dirname(posixpath.dirname(marketplace))
        data, error_kind, _error_message = cache.parse(path, containment_root=root)
        plugins = data.get("plugins") if isinstance(data, dict) else None
        if error_kind is not None or not isinstance(plugins, list):
            issue(
                source=marketplace, blocking=False, plugin_root=marketplace_root,
                message=(
                    "Cannot read this Claude Code marketplace's `plugins` array; the hooks its "
                    "plugin entries may select were not read"
                ),
            )
            continue
        enabled_here = enabled_entries(marketplace, data)
        metadata = data.get("metadata")
        root_reference = metadata.get("pluginRoot") if isinstance(metadata, dict) else None
        plugin_base = (
            _plugin_relative_path(root_reference, allow_root=True)
            if isinstance(root_reference, str)
            else None
        )
        if isinstance(root_reference, str) and plugin_base is None:
            issue(
                source=marketplace, blocking=False, plugin_root=marketplace_root,
                message=(
                    f"{marketplace} `metadata.pluginRoot` "
                    f"{_sanitize_sensitive_string(root_reference)!r} is not a `./` path inside the "
                    "marketplace directory; the hooks of plugins named by a bare source under it "
                    "were not read"
                ),
            )
        recorded: list[dict[str, Any]] = []
        for index, entry in enumerate(plugins):
            if not isinstance(entry, dict):
                continue
            name = entry.get("name") if isinstance(entry.get("name"), str) else str(index)
            selector = f"{marketplace}#plugins.{name}"
            source = entry.get("source")
            relative: str | None = None
            if isinstance(source, str) and source.startswith("./"):
                relative = _plugin_relative_path(source, allow_root=True)
                if relative is None:
                    issue(
                        source=marketplace, blocking=False, plugin_root=marketplace_root,
                        message=(
                            f"{selector} source {_sanitize_sensitive_string(source)!r} leaves the "
                            "marketplace directory; the hooks that plugin may select were not read"
                        ),
                    )
                    continue
            elif (
                isinstance(source, str) and source and plugin_base is not None
                # A bare name is one directory name: `team/demo` still needs `./`.
                and "/" not in source and ":" not in source
                and _plugin_relative_path(f"./{source}") is not None
            ):
                relative = under(plugin_base, posixpath.normpath(source))
            else:
                continue  # A remote source names nothing in this repository.
            plugin_root = under(marketplace_root, relative) if relative else marketplace_root
            if isinstance(entry.get("name"), str) and entry["name"] in enabled_here:
                enabled_roots.add(plugin_root.casefold())
            select_default(plugin_root, selector)
            if "hooks" not in entry:
                continue
            recorded.append({
                "name": name, "source": source, "strict": entry.get("strict"),
                "hooks": entry["hooks"],
            })
            follow_hooks(entry["hooks"], plugin_root=plugin_root, selector=selector, blocking=False)
        if recorded:
            artifacts.append(_artifact(
                host="claude-code", scope="repository", source=marketplace, kind="config",
                status="parsed", data={"plugins": recorded}, resolved_through=resolved_through,
            ))

    # Every enabled root is known only now: a manifest is read before the
    # marketplace that enables its plugin.
    for declared, plugin_root, selector in inline:
        enabled_here = plugin_root.casefold() in enabled_roots
        if enabled_here:
            result.enabled_inline.add(selector.split("#", 1)[0])
        grants.extend(_hooks_grants(
            {"hooks": declared}, host="claude-code", scope="repository", source=selector,
            basis="project_enabled_plugin" if enabled_here else "plugin_selected",
        ))
    result.enabled = {
        hook_file for hook_file, roots in roots_by_file.items() if roots & enabled_roots
    }
    result.enabled_unread = {
        hook_file for hook_file, roots in unread_roots_by_file.items() if roots & enabled_roots
    }
    return result


#: Directory names the repository walk never enters. One definition, so a
#: plugin reference into one is named as unread rather than missing (#714).
_WALK_SKIPPED_DIRECTORIES = frozenset({
    ".git", ".hg", ".svn", "node_modules", "site-packages", ".venv", "venv",
    # Machine-written tool caches. No host reads configuration from one, so
    # inventorying them buys no coverage — and it made an ordinary
    # concurrent test run collapse the whole repository inventory, because
    # a `.pyc` appearing between the scan and its revalidation is a
    # directory that "changed while it was read" (#598). Excluded for the
    # same reason `.venv` and `node_modules` already are, not to make an
    # unreadable input pass.
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".nox",
})


def _source_kind(path: str) -> str:
    folded = path.casefold()
    if folded.startswith(".github/workflows/"):
        return "workflow"
    if folded.endswith((".mcp.json", "/mcp.json")):
        return "mcp"
    if folded.endswith("hooks.json"):
        return "hooks"
    if folded.endswith("requirements.toml"):
        return "requirements"
    if (
        folded.endswith((".md", ".mdc", "/skill.md"))
        or folded.startswith(".cursor/rules/")
        or folded.startswith(".agents/skills/")
        or folded.startswith("policies/")
        or folded == "shipgate.yaml"
    ):
        return "instructions"
    return "config"


def _audit_hosts(adapter_id: str, path: str, hosts: tuple[str, ...]) -> tuple[str, ...]:
    if path.casefold().startswith(".github/workflows/"):
        return ("github",)
    if adapter_id == "vscode_mcp":
        return ("vscode",)
    return hosts


def _repository_paths(
    root: Path,
    *,
    reader: IdentityBoundReadSession,
    limits: tuple[tuple[str, int], ...] = (),
    include_directory_candidates: bool = False,
    plugin_candidates: dict[str, tuple[Path, tuple[str, ...]]] | None = None,
    plugin_unread_links: set[str] | None = None,
) -> tuple[list[tuple[Path, str, str, str, tuple[str, ...]]], int]:
    """Enumerate repository sources exclusively from the boundary registry.

    Each row is ``(read path, source, host, kind, resolved_through)``. The two
    paths differ only for a boundary path that is an in-tree link (#700, step
    two): the source keeps the link's own spelling, which is what the host
    reads, and the read path is the target the walk already enumerated.
    """

    indexed: dict[tuple[str, str], tuple[Path, str, str, str, tuple[str, ...]]] = {}
    skipped = set(_WALK_SKIPPED_DIRECTORIES)
    candidates: list[tuple[Path, str]] = []
    symlink_directories: list[str] = []
    link_resolutions: dict[str, tuple[Path, str, tuple[str, ...]] | None] = {}
    visited = 0
    pending = [Path()]
    while pending:
        relative_directory = pending.pop()
        directory = root / relative_directory
        try:
            names = reader.directory_entries(
                relative_directory,
                max_entries=MAX_HOST_REPOSITORY_ENTRIES - visited,
            )
        except IdentityReadBudgetExceeded as exc:
            raise HostInventoryReadError(HostInputFailure(
                reason="resource_bound_exceeded", phase="inventory_enumeration",
                source=relative_directory.as_posix(), limits=limits,
            )) from exc
        except (OSError, NotImplementedError, ValueError) as exc:
            raise HostInventoryReadError(HostInputFailure(
                reason="input_unreadable", phase="inventory_enumeration",
                source=relative_directory.as_posix(),
            )) from exc
        visited += len(names)
        child_directories: list[Path] = []
        for name in names:
            candidate = directory / name
            relative = candidate.relative_to(root).as_posix()
            try:
                metadata = candidate.lstat()
                if stat.S_ISLNK(metadata.st_mode):
                    if name not in skipped:
                        candidates.append((candidate, relative))
                        resolution = _resolve_in_tree_link(reader, Path(relative))
                        link_resolutions[relative] = resolution
                        # A link to an in-tree regular file has no descendants
                        # to conceal (#700, owner decision). Everything else,
                        # including a target this session cannot type, still
                        # may hide a recursive match unless it is read through
                        # below.
                        if resolution is None or resolution[1] != "file":
                            symlink_directories.append(relative)
                    continue
                if stat.S_ISDIR(metadata.st_mode):
                    if name not in skipped:
                        child_directories.append(Path(relative))
                        if include_directory_candidates or is_explicit_boundary_file_path(relative):
                            candidates.append((candidate, relative))
                    continue
            except (OSError, ValueError) as exc:
                raise HostInventoryReadError(HostInputFailure(
                    reason="input_unreadable", phase="entry_inspection",
                    source=relative,
                )) from exc
            candidates.append((candidate, relative))
        pending.extend(reversed(child_directories))

    # #700 step two: an in-tree directory link at a boundary location is read
    # through. Every entry beneath its target was already enumerated by the walk
    # above, at its real path, so each one is published again under the link.
    read_through: list[tuple[Path, str, tuple[str, ...]]] = []
    for link, resolution in sorted(link_resolutions.items()):
        if resolution is None or resolution[1] != "directory":
            continue
        target, _kind, hops = resolution
        if not _reads_through_directory_link(
            link, target, skipped=skipped, links=link_resolutions
        ):
            continue
        symlink_directories.remove(link)
        prefix = f"{target.as_posix()}/"
        for path, relative in candidates:
            if relative.startswith(prefix):
                read_through.append(
                    (path, f"{link}/{relative[len(prefix):]}", (*hops[:-1], relative))
                )

    for adapter in BOUNDARY_ADAPTERS:
        for expected in adapter.exact_paths:
            if any(
                expected.casefold().startswith(f"{prefix.casefold()}/")
                for prefix in symlink_directories
            ):
                candidates.append((root / expected, expected))

    entries: list[tuple[Path, str, tuple[str, ...]]] = []
    for path, relative in candidates:
        resolution = link_resolutions.get(relative)
        if resolution is not None and resolution[1] == "file":
            # #700 step two: a linked boundary file is read at its in-tree target.
            entries.append((root / resolution[0], relative, resolution[2]))
        elif resolution is not None and relative not in symlink_directories:
            continue  # A directory link read through above names no source itself.
        else:
            entries.append((path, relative, ()))
    entries.extend(read_through)

    if plugin_candidates is not None:
        # Not adapter surfaces: a plugin manifest, a marketplace and the
        # hook-named files they may select are opened only to decide selection
        # (#714). Taken from the same entries, so links and read-through
        # resolve exactly as above.
        for path, relative, resolved_through in entries:
            if is_claude_plugin_reference_path(relative):
                plugin_candidates[relative] = (path, resolved_through)

    for path, relative, resolved_through in entries:
        for adapter in BOUNDARY_ADAPTERS:
            if not (
                adapter.matches(relative)
                or (
                    relative in symlink_directories
                    and any(
                        _symlink_may_hide_boundary_glob(relative, pattern)
                        for pattern in adapter.globs
                    )
                )
            ):
                continue
            for host in _audit_hosts(adapter.id, relative, adapter.hosts):
                indexed[(host, relative)] = (
                    path,
                    relative,
                    host,
                    _source_kind(relative),
                    resolved_through,
                )
    if plugin_unread_links is not None:
        # Links the walk could not read through but still reports as a
        # source, so reading them raises a blocking limit. A plugin reference
        # beneath one is covered by that limit (#714).
        reported = {relative for _host, relative in indexed}
        plugin_unread_links.update(link for link in symlink_directories if link in reported)
    return [indexed[key] for key in sorted(indexed)], visited


#: How many links one in-tree resolution may pass through.
_MAX_IN_TREE_LINK_HOPS = 8


def _resolve_in_tree_link(
    reader: IdentityBoundReadSession, relative: Path
) -> tuple[Path, str, tuple[str, ...]] | None:
    """Where a link lands inside the tree: ``(target, kind, hops)``, or ``None`` (#700).

    Bound to the identity-bound read rather than to a `stat()` at enumeration.
    Each link's text comes from :meth:`IdentityBoundReadSession.link_target`,
    and each component's kind from
    :meth:`IdentityBoundReadSession.directory_entry_kind`, so :meth:`finish`
    fails the snapshot if a link is re-pointed or a target is swapped.

    ``kind`` is ``"file"`` or ``"directory"``, and ``hops`` are the in-tree
    paths the resolution landed on, ending at the target. An absolute or
    escaping target, a link as an intermediate component, a chain longer than
    the hop bound, or anything the session cannot type is unresolved.
    """

    current = relative
    hops: list[str] = []
    try:
        for _ in range(_MAX_IN_TREE_LINK_HOPS):
            text = reader.link_target(current)
            if not text or os.path.isabs(text) or text.startswith(("\\", "/")):
                return None
            joined = posixpath.normpath(posixpath.join(current.parent.as_posix(), text))
            if joined in {".", ".."} or joined.startswith("../"):
                return None
            target = Path(joined)
            for index in range(1, len(target.parts)):
                if reader.directory_entry_kind(Path(*target.parts[:index])) != "directory":
                    return None
            kind = reader.directory_entry_kind(target)
            hops.append(joined)
            if kind in {"file", "directory"}:
                return target, kind, tuple(hops)
            if kind != "symlink":
                return None
            current = target
    except (OSError, ValueError):
        return None
    return None


def _links_to_in_tree_file(reader: IdentityBoundReadSession, relative: Path) -> bool:
    """Whether a link resolves, inside the tree, to a regular file (#700)."""

    resolution = _resolve_in_tree_link(reader, relative)
    return resolution is not None and resolution[1] == "file"


def _reads_through_directory_link(
    link: str,
    target: Path,
    *,
    skipped: set[str],
    links: dict[str, tuple[Path, str, tuple[str, ...]] | None],
) -> bool:
    """Whether an in-tree directory link is read through (#700, step two).

    Only at a boundary location: a directory a registered adapter names by a
    fixed prefix, such as `.claude/skills`, or an ancestor of an exact path.
    A link that could only hide a `**/` match stays a limit. And only when the
    walk already enumerated everything beneath the target: no skipped name on
    the way, and no link inside it that would need a second resolution, which
    also rules out a link into its own ancestor.
    """

    if any(part in skipped for part in target.parts):
        return False
    prefix = f"{target.as_posix()}/"
    if any(other.startswith(prefix) or f"{other}/".startswith(prefix) for other in links):
        return False
    lowered = link.casefold()
    for adapter in BOUNDARY_ADAPTERS:
        if any(item.casefold().startswith(f"{lowered}/") for item in adapter.exact_paths):
            return True
        for pattern in adapter.globs:
            fixed = pattern.casefold().split("*", 1)[0].rstrip("/")
            if fixed and (
                lowered == fixed
                or lowered.startswith(f"{fixed}/")
                or fixed.startswith(f"{lowered}/")
            ):
                return True
    return False


def _symlink_may_hide_boundary_glob(relative: str, pattern: str) -> bool:
    """Whether a symlink directory can conceal descendants of ``pattern``."""

    path = relative.casefold().strip("/")
    candidate_pattern = pattern.casefold().strip("/")
    if candidate_pattern.startswith("**/"):
        return bool(path)
    fixed_prefix = candidate_pattern.split("*", 1)[0].rstrip("/")
    return bool(fixed_prefix) and (
        path == fixed_prefix
        or path.startswith(f"{fixed_prefix}/")
        or fixed_prefix.startswith(f"{path}/")
    )


def _repository_sources_expected(host: str) -> list[str]:
    expected: set[str] = set()
    for adapter in BOUNDARY_ADAPTERS:
        paths = (*adapter.exact_paths, *adapter.globs)
        for path in paths:
            if host in _audit_hosts(adapter.id, path, adapter.hosts):
                expected.add(path)
    return sorted(expected)


def _local_paths(home: Path) -> list[tuple[Path, str, str, str, Path]]:
    codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex")).expanduser()
    candidates: list[tuple[Path, str, str, str, Path]] = [
        (codex_home / "config.toml", "~/.codex/config.toml", "codex", "config", codex_home),
        (codex_home / "requirements.toml", "~/.codex/requirements.toml", "codex", "requirements", codex_home),
        (home / ".claude/settings.json", "~/.claude/settings.json", "claude-code", "config", home),
        (home / ".cursor/cli-config.json", "~/.cursor/cli-config.json", "cursor", "config", home),
        (home / ".cursor/mcp.json", "~/.cursor/mcp.json", "cursor", "mcp", home),
    ]
    if sys.platform == "darwin":
        managed = Path("/Library/Application Support/ClaudeCode")
        candidates.append((managed / "managed-settings.json", "/Library/Application Support/ClaudeCode/managed-settings.json", "claude-code", "config", managed))
    elif os.name == "nt":
        managed = Path("C:/Program Files/ClaudeCode")
        candidates.append((managed / "managed-settings.json", "C:/Program Files/ClaudeCode/managed-settings.json", "claude-code", "config", managed))
    else:
        managed = Path("/etc/claude-code")
        candidates.append((managed / "managed-settings.json", "/etc/claude-code/managed-settings.json", "claude-code", "config", managed))
    return [item for item in candidates if item[0].exists() or item[0].is_symlink()]


def _collect_claude_project_state(
    *, root: Path, home: Path, cache: HostStaticParseCache,
    artifacts: list[dict[str, Any]], grants: list[dict[str, Any]], issues: list[dict[str, Any]],
) -> None:
    path = home / ".claude.json"
    if not (path.exists() or path.is_symlink()):
        return
    source = "~/.claude.json#current-workspace"
    data, error_kind, error = cache.parse(path, containment_root=home)
    if error_kind is not None:
        assert error is not None
        issues.append(cache.read_issue(
            path=path, containment_root=home,
            kind=error_kind, host="claude-code", source=source,
            message=error,
        ))
        artifacts.append(_artifact(
            host="claude-code", scope="local_static", source=source,
            kind="config", status="failed",
        ))
        return
    if not isinstance(data, dict):
        artifacts.append(_artifact(
            host="claude-code", scope="local_static", source=source,
            kind="config", status="parsed", data={},
        ))
        return
    projects = data.get("projects")
    if not isinstance(projects, dict):
        artifacts.append(_artifact(
            host="claude-code", scope="local_static", source=source,
            kind="config", status="parsed", data={},
        ))
        return
    resolved = str(root.resolve())
    project = projects.get(resolved)
    if not isinstance(project, dict):
        artifacts.append(_artifact(
            host="claude-code", scope="local_static", source=source,
            kind="config", status="parsed", data={},
        ))
        return
    # Only the current workspace projection is retained. Unrelated ~/.claude.json
    # data is neither emitted nor hashed into grant identities.
    artifacts.append(_artifact(
        host="claude-code", scope="local_static", source=source,
        kind="config", status="parsed", data=project,
    ))
    grants.extend(_mcp_grants(project, host="claude-code", scope="local_static", source=source))
    grants.extend(_claude_grants(project, scope="local_static", source=source))
    if "policyHelper" in project:
        issues.append(
            _inventory_issue(
                kind="unsupported",
                host="claude-code",
                source=source,
                message=(
                    "policyHelper is executable/dynamic policy input; the static "
                    "audit does not run it and cannot establish complete coverage"
                ),
                blocking=True,
            )
        )


def _coverage(
    *, scope: HostScope, artifacts: list[dict[str, Any]], issues: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    coverage: list[dict[str, Any]] = []
    for host in ("codex", "claude-code", "cursor", "vscode", "github"):
        host_artifacts = [item for item in artifacts if item["host"] == host]
        host_issues = [item for item in issues if item["host"] == host and item["blocking"]]
        status = "partial" if host_issues else "complete"
        expected = _repository_sources_expected(host)
        if scope == "local_static":
            expected.append("documented local static sources")
        coverage.append({
            "host": host,
            "scope": scope,
            "status": status,
            "sources_expected": expected,
            "sources_observed": sorted(item["path"] for item in host_artifacts),
            "issue_ids": sorted(item["issue_id"] for item in host_issues),
        })
    return coverage


#: Claude Code's settings stack, highest first, as documented at
#: code.claude.com/docs/en/settings § Settings precedence: managed settings,
#: then `--settings` (a static audit never sees a session flag), then project
#: local, shared project and user. A lower number wins.
_CLAUDE_MANAGED_SOURCES = frozenset({
    "/Library/Application Support/ClaudeCode/managed-settings.json",
    "C:/Program Files/ClaudeCode/managed-settings.json",
    "/etc/claude-code/managed-settings.json",
})
_CLAUDE_SETTINGS_RANK: dict[str, int] = {
    **dict.fromkeys(_CLAUDE_MANAGED_SOURCES, 0),
    ".claude/settings.local.json": 1,
    ".claude/settings.json": 2,
    "~/.claude/settings.json": 3,
}
#: One MCP server name defined in several scopes: "Claude Code connects to it
#: once, using the definition from the highest-precedence source", whole
#: entry, local over project (code.claude.com/docs/en/mcp).
_CLAUDE_MCP_RANK: dict[str, int] = {
    "~/.claude.json#current-workspace": 0,
    ".mcp.json": 1,
}
#: Documented to take effect only from managed settings, where they restrict
#: which permission rules or hooks any other source may contribute.
_CLAUDE_MANAGED_ONLY_SETTINGS = {
    "allowManagedPermissionRulesOnly": "permission_rule",
    "allowManagedHooksOnly": "hook",
}
#: Kinds whose values the settings stack does not document how to combine
#: when two layers disagree. Agreement is resolvable; disagreement fails
#: closed and names both layers.
_CLAUDE_UNDOCUMENTED_MERGE_KINDS = frozenset({"sandbox", "plugin_or_app"})


def _claude_precedence_key(grant: dict[str, Any]) -> tuple[str, str] | None:
    """The key two layers compete for, or ``None`` for a kind that merges.

    Permission rules, additional directories and hooks merge across layers
    ("Lists merge instead of overriding"; hook entries "merge across settings
    levels rather than replacing each other"), so every layer's entry stays
    effective and nothing competes.
    """

    kind = grant.get("kind")
    if kind == "permission_mode" and grant.get("setting") in CLAUDE_LIST_SETTINGS:
        # One grant per approved server (#827). Whether Claude Code merges the
        # list across layers is not documented, so each entry is its own key:
        # a server one layer approves is never hidden by another layer's list.
        return (str(kind), f"{grant.get('setting')}:{grant.get('value')}")
    if kind in {"permission_mode", "sandbox"}:
        return (str(kind), str(grant.get("setting")))
    if kind == "plugin_or_app":
        return (str(kind), str(grant.get("name")))
    if kind == "mcp_server":
        return (str(kind), str(grant.get("server")))
    return None


def _claude_setting_ignored_in_source(grant: dict[str, Any]) -> bool:
    """A value the documentation says this file cannot make take effect.

    Such a grant never shadows a lower layer. It is still reported: the
    restriction is recent (a project `bypassPermissions` took effect before
    Claude Code v2.1.257) and a static audit cannot see the installed
    version, so it over-reports rather than hide authority an older client
    would grant.
    """

    if grant.get("kind") != "permission_mode":
        return False
    setting, value, source = grant.get("setting"), grant.get("value"), grant.get("source")
    project_files = {".claude/settings.local.json", ".claude/settings.json"}
    if setting == "defaultMode":
        return value in {"auto", "bypassPermissions"} and source in project_files
    if setting == "skipDangerousModePermissionPrompt":
        return source == ".claude/settings.json"
    return False


def _project_claude_precedence(
    grants: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep the Claude Code grants that take effect across local layers (#657).

    Every layer used to raise a blocking ``unresolved_precedence`` issue as
    soon as two existed, so the ordinary developer setup — user settings plus
    a project's — could never record a local baseline. The projection follows
    the documented stack instead: merged kinds keep every layer, a scalar key
    keeps the highest layer that sets it, a same-named MCP server keeps the
    highest scope's entry, and managed-only restrictions apply. What remains
    names its effective layer in ``source``. Where the documentation does not
    settle a disagreement, or a layer has no documented rank, every candidate
    is kept and a blocking issue names the key and each layer.
    """

    claude = [grant for grant in grants if grant.get("host") == "claude-code"]
    restricted_kinds = {
        _CLAUDE_MANAGED_ONLY_SETTINGS[str(grant.get("setting"))]
        for grant in claude
        if grant.get("kind") == "permission_mode"
        and grant.get("setting") in _CLAUDE_MANAGED_ONLY_SETTINGS
        and grant.get("source") in _CLAUDE_MANAGED_SOURCES
        and grant.get("value") == "True"
    }
    kept: list[dict[str, Any]] = []
    contested: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for grant in grants:
        if grant.get("host") != "claude-code":
            kept.append(grant)
            continue
        managed = grant.get("source") in _CLAUDE_MANAGED_SOURCES
        if (
            grant.get("kind") == "permission_mode"
            and grant.get("setting") in _CLAUDE_MANAGED_ONLY_SETTINGS
            and not managed
        ):
            continue  # Documented as managed-only: no effect from any other file.
        if grant.get("kind") in restricted_kinds and not managed:
            continue  # A managed setting admits only managed rules or hooks.
        key = _claude_precedence_key(grant)
        if key is None:
            kept.append(grant)
        else:
            contested.setdefault(key, []).append(grant)

    issues: list[dict[str, Any]] = []
    for (kind, name), group in sorted(contested.items()):
        sources = sorted({str(grant.get("source")) for grant in group})
        if len(sources) < 2:
            kept.extend(group)
            continue
        rank = _CLAUDE_MCP_RANK if kind == "mcp_server" else _CLAUDE_SETTINGS_RANK
        unranked = [source for source in sources if source not in rank]
        disagreement = kind in _CLAUDE_UNDOCUMENTED_MERGE_KINDS and len({
            str(grant.get("value") if kind == "sandbox" else grant.get("enabled"))
            for grant in group
        }) > 1
        if unranked or disagreement:
            reason = (
                f"{', '.join(unranked)} has no documented place in Claude Code's precedence"
                if unranked
                else "Claude Code's documentation does not say which disagreeing value applies"
            )
            issues.append(
                _inventory_issue(
                    kind="unresolved_precedence",
                    host="claude-code",
                    source=f"claude-code:{kind}:{name}",
                    message=(
                        f"{kind} {name!r} is set in {', '.join(sources)}; {reason}, so its "
                        "effective value is not statically projected."
                    ),
                    blocking=True,
                )
            )
            kept.extend(group)
            continue
        shadowing = [grant for grant in group if not _claude_setting_ignored_in_source(grant)]
        if not shadowing:
            kept.extend(group)
            continue
        winner = min(rank[str(grant.get("source"))] for grant in shadowing)
        # Anything ranked above the winner is a value its own file cannot make
        # take effect; it stays, over-reported, for the reason given above.
        kept.extend(grant for grant in group if rank[str(grant.get("source"))] <= winner)
    return kept, issues


def _local_precedence_issues(
    artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fail closed where several static layers require an effective merge.

    Claude Code is projected by ``_project_claude_precedence``. For the other
    hosts the inventory retains every redacted declaration, but it must not
    claim effective-authority coverage while their layering is undocumented
    or depends on state a static read cannot see (Codex loads a project
    ``.codex/config.toml`` only for a trusted project; Cursor does not say
    whether its user and project CLI files merge).
    """

    grouped: dict[tuple[str, str], list[str]] = {}
    for artifact in artifacts:
        kind = str(artifact.get("kind"))
        if kind not in {"config", "mcp", "requirements"}:
            continue
        key = (str(artifact.get("host")), kind)
        grouped.setdefault(key, []).append(str(artifact.get("path")))
    issues: list[dict[str, Any]] = []
    for (host, kind), sources in sorted(grouped.items()):
        unique_sources = sorted(dict.fromkeys(sources))
        if len(unique_sources) < 2:
            continue
        issues.append(
            _inventory_issue(
                kind="unresolved_precedence",
                host=host,
                source=f"{host}:{kind}",
                message=(
                    f"Multiple {host} {kind} layers were observed "
                    f"({', '.join(unique_sources)}); their runtime-effective "
                    "precedence is not statically projected."
                ),
                blocking=True,
            )
        )
    return issues


def build_host_boundary_snapshot(
    workspace: Path,
    *,
    scope: HostScope = "repository",
    cache: HostStaticParseCache | None = None,
) -> HostBoundarySnapshot:
    """Build the reusable, schema-validated static boundary snapshot."""

    root = workspace.resolve()
    home = Path.home().resolve()
    cache = cache or HostStaticParseCache()
    artifacts: list[dict[str, Any]] = []
    grants: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    inventory_failures: list[HostInputFailure] = []
    plugin_candidates: dict[str, tuple[Path, tuple[str, ...]]] = {}
    plugin_unread_links: set[str] = set()
    try:
        repository_paths, _inventory_entries = _repository_paths(
            root,
            reader=cache.reader_for(root),
            limits=cache.configured_limits,
            plugin_candidates=plugin_candidates,
            plugin_unread_links=plugin_unread_links,
        )
    except HostInventoryReadError as exc:
        repository_paths = []
        inventory_failures.append(exc.failure)
    except IdentityReadBudgetExceeded:
        repository_paths = []
        inventory_failures.append(HostInputFailure(
            reason="resource_bound_exceeded", phase="inventory_enumeration",
            source="<repository>", limits=cache.configured_limits,
        ))
    except (OSError, NotImplementedError, ValueError):
        repository_paths = []
        inventory_failures.append(HostInputFailure(
            reason="input_unreadable", phase="inventory_enumeration",
            source="<repository>",
        ))
    # Parsed once through the cache: the collection below reuses the result
    # and raises any issue these files have, so none is raised here.
    project_settings = tuple(
        cache.parse(path, containment_root=root)[0]
        for path, source, host, _kind, _resolved_through in repository_paths
        if host == "claude-code" and source in _CLAUDE_PROJECT_SETTINGS_SOURCES
    )
    selection = (
        _PluginHookSelection()
        if inventory_failures
        else _resolve_claude_plugin_hooks(
            candidates=plugin_candidates, root=root, cache=cache,
            artifacts=artifacts, grants=grants, issues=issues,
            project_settings=project_settings,
            unread_links=frozenset(plugin_unread_links),
        )
    )
    selected_hooks = selection.selected
    plugin_reference_issue_ids = set(selection.reference_issue_ids)
    issue_roots: dict[str, set[str | None]] = {
        issue_id: set(roots) for issue_id, roots in selection.issue_roots.items()
    }

    def bound_by_selecting_roots(issue_id: str, source: str) -> None:
        # A limit of a hook file only a plugin selects hides that file's
        # hooks, and the file lies under every directory that selects it (#808).
        issue_roots.setdefault(issue_id, set()).update(selection.roots.get(source, ()))

    def note_unusable_selected_hooks(data: Any, *, source: str) -> None:
        if isinstance(data, dict) and not isinstance(data.get("hooks"), dict):
            item = _claude_plugin_hook_issue(
                source=source, blocking=False,
                message=(
                    f"{selected_hooks[source]} selects this hook file, which has no `hooks` "
                    "object of events; no hooks were read from it"
                ),
            )
            issues.append(item)
            plugin_reference_issue_ids.add(item["issue_id"])
            bound_by_selecting_roots(item["issue_id"], source)

    collected_claude_sources: set[str] = set()
    for path, source, host, kind, resolved_through in repository_paths:
        hook_basis: HookLoadingBasis = "host_configuration"
        if host == "claude-code" and kind == "hooks":
            # Claude Code documents no project `.claude/hooks/hooks.json`
            # location; only plugin configuration can select one (#714).
            hook_basis = (
                "project_enabled_plugin" if source in selection.enabled
                else "plugin_selected" if source in selected_hooks
                else "declared_only"
            )
            collected_claude_sources.add(source)
        data = _collect_file(
            path=path, source=source, host=host, scope="repository", kind=kind,
            containment_root=root, cache=cache,
            artifacts=artifacts, grants=grants, issues=issues,
            resolved_through=resolved_through, hook_basis=hook_basis,
        )
        if hook_basis in {"plugin_selected", "project_enabled_plugin"}:
            note_unusable_selected_hooks(data, source=source)
    for source in sorted(set(selected_hooks) - collected_claude_sources):
        path, resolved_through = plugin_candidates[source]
        raised = len(issues)
        data = _collect_file(
            path=path, source=source, host="claude-code", scope="repository", kind="hooks",
            containment_root=root, cache=cache,
            artifacts=artifacts, grants=grants, issues=issues,
            resolved_through=resolved_through,
            hook_basis=(
                "project_enabled_plugin" if source in selection.enabled else "plugin_selected"
            ),
        )
        # Read only because a plugin selects it, so its read limits are
        # plugin-reference limits. A registered hook path above keeps the
        # limits 1.0.0 already gave it.
        plugin_reference_issue_ids.update(item["issue_id"] for item in issues[raised:])
        for item in issues[raised:]:
            bound_by_selecting_roots(item["issue_id"], source)
        note_unusable_selected_hooks(data, source=source)

    excluded = [
        "invocation flags and transient approvals",
        "runtime sandbox enforcement and actual tool behavior",
        "UI and session state",
        "remote server-managed policy",
        "dynamically registered extension MCP servers",
    ]
    if scope == "repository":
        excluded.insert(0, "user and operating-system managed configuration")
    elif scope == "local_static":
        for path, source, host, kind, containment_root in _local_paths(home):
            _collect_file(
                path=path, source=source, host=host, scope="local_static", kind=kind,
                containment_root=containment_root, cache=cache,
                artifacts=artifacts, grants=grants, issues=issues,
            )
        _collect_claude_project_state(
            root=root, home=home, cache=cache,
            artifacts=artifacts, grants=grants, issues=issues
        )
    else:  # pragma: no cover - CLI and typing constrain this; defensive API guard.
        raise ValueError(f"Unsupported host audit scope: {scope!r}")

    if scope == "local_static":
        grants, claude_precedence_issues = _project_claude_precedence(grants)
        issues.extend(claude_precedence_issues)
        issues.extend(_local_precedence_issues(
            [item for item in artifacts if item.get("host") != "claude-code"]
        ))

    try:
        cache.finish()
    except IdentityReadBudgetExceeded:
        inventory_failures.append(cache.terminal_failure or HostInputFailure(
            reason="resource_bound_exceeded", phase="snapshot_validation",
            source="<repository>", limits=cache.configured_limits,
        ))
    except (OSError, NotImplementedError, ValueError):
        inventory_failures.append(cache.terminal_failure or HostInputFailure(
            reason="snapshot_validation_failed", phase="snapshot_validation",
            source="<repository>",
        ))
    if inventory_failures:
        # No parsed projection is trustworthy when the final exact-name pass
        # or complete inventory cannot bind it to the entries that were opened.
        artifacts.clear()
        grants.clear()
        for failure in dict.fromkeys(inventory_failures):
            # Use lexical spelling: a second resolve/read would inspect a
            # different generation and could misattribute the original failure.
            if Path(failure.source).is_absolute():
                try:
                    source = Path(failure.source).relative_to(root).as_posix()
                except ValueError:
                    source = failure.source
                failure = replace(failure, source=source)
            for host in ("codex", "claude-code", "cursor", "vscode", "github"):
                issue = _inventory_issue(
                    kind="unreadable", host=host, source=failure.source,
                    message=failure.summary() + " " + failure.recovery(), blocking=True,
                )
                issues.append(issue)
                cache.input_failures[issue["issue_id"]] = failure

    artifacts.sort(key=lambda item: (item["host"], item["scope"], item["path"], item["kind"]))
    grants.sort(key=lambda item: item["grant_id"])
    # A resource failure may first name a source and then invalidate all hosts.
    # Keep one copy of that same source/host obligation in the inventory too.
    issues = list({item["issue_id"]: item for item in issues}.values())
    issues.sort(key=lambda item: item["issue_id"])
    payload = {
        "host_grants_inventory_schema_version": HOST_GRANTS_INVENTORY_SCHEMA_VERSION,
        "workspace": str(root),
        "scope": scope,
        "artifacts": artifacts,
        "host_coverage": _coverage(scope=scope, artifacts=artifacts, issues=issues),
        "grants": grants,
        "issues": issues,
        "excluded_scopes": sorted(excluded),
        "static_analysis_only": True,
        "runtime_session_verified": False,
    }
    inventory = HostGrantsInventoryV7.model_validate(payload).model_dump(mode="json")
    return HostBoundarySnapshot(
        inventory=inventory, cache=cache, input_failures=dict(cache.input_failures),
        plugin_reference_issue_ids=frozenset(plugin_reference_issue_ids),
        # Nothing parsed is trustworthy once the inventory failed, so no
        # enablement is read from it either; its blocking issues stand.
        enabled_plugin_hook_sources=(
            frozenset()
            if inventory_failures
            else frozenset(selection.enabled | selection.enabled_inline)
        ),
        enabled_plugin_unread_hook_files=(
            frozenset() if inventory_failures else frozenset(selection.enabled_unread)
        ),
        plugin_scopes=PluginScopeFacts(
            issue_roots={
                issue_id: frozenset(roots)
                for issue_id, roots in issue_roots.items()
                if issue_id in plugin_reference_issue_ids
            },
            inline_roots={
                public_host_path(selector): root
                for selector, root in selection.inline_roots.items()
            },
        ),
    )


def host_audit_inventory(
    workspace: Path,
    *,
    scope: HostScope = "repository",
    snapshot: HostBoundarySnapshot | None = None,
    cache: HostStaticParseCache | None = None,
) -> dict[str, Any]:
    """Project a precomputed snapshot, or build one when none was supplied."""

    if snapshot is None:
        snapshot = build_host_boundary_snapshot(workspace, scope=scope, cache=cache)
    inventory = HostGrantsInventoryV7.model_validate(snapshot.inventory)
    if inventory.scope != scope:
        raise ValueError(
            f"Host boundary snapshot scope {inventory.scope!r} does not match {scope!r}"
        )
    if Path(inventory.workspace).resolve() != workspace.resolve():
        raise ValueError("Host boundary snapshot belongs to a different workspace")
    return inventory.model_dump(mode="json")


def inventory_is_complete(inventory: dict[str, Any]) -> bool:
    return not any(item.get("blocking") for item in inventory.get("issues", [])) and all(
        item.get("status") == "complete" for item in inventory.get("host_coverage", [])
    )


def without_host_issues(
    inventory: dict[str, Any], issue_ids: set[str] | frozenset[str]
) -> dict[str, Any]:
    """The inventory without these issues, with host coverage recomputed (#714).

    For a caller that routes none of the sources those issues describe and
    cannot name a limit (`check` and plugin references). Coverage is derived
    from artifacts and blocking issues exactly as the reader derives it, so
    dropping an issue changes nothing else.
    """

    kept = [item for item in inventory.get("issues", []) if item.get("issue_id") not in issue_ids]
    if len(kept) == len(inventory.get("issues", [])):
        return inventory
    return {
        **inventory,
        "issues": kept,
        "host_coverage": _coverage(
            scope=inventory.get("scope", "repository"),
            artifacts=list(inventory.get("artifacts") or []),
            issues=kept,
        ),
    }


def without_host_sources(
    inventory: dict[str, Any],
    *,
    issue_ids: set[str] | frozenset[str],
    withheld: Callable[[str], bool],
) -> dict[str, Any]:
    """The inventory without these issues and every source ``withheld`` names (#808).

    For a comparison that leaves a plugin directory uncompared and compares
    the rest. An artifact, a grant or a non-blocking issue is dropped when
    ``withheld`` names its whole published source, and host coverage is
    recomputed exactly as the reader derives it. A member (``<file>#...``)
    only extends its file's path, so it is under a directory exactly when its
    file is; cutting at the first ``#`` would instead read a sibling such as
    ``plugins/demo#x/...`` as inside ``plugins/demo`` and drop it unnamed. A
    blocking issue is dropped only by id: one the caller has not accounted
    for stays, so what remains must still answer for it.
    """

    def kept(source: object) -> bool:
        return not withheld(str(source))

    issues = [
        item
        for item in inventory.get("issues", [])
        if item.get("issue_id") not in issue_ids
        and (item.get("blocking") or kept(item.get("source")))
    ]
    artifacts = [item for item in inventory.get("artifacts") or [] if kept(item.get("path"))]
    return {
        **inventory,
        "artifacts": artifacts,
        "grants": [item for item in inventory.get("grants") or [] if kept(item.get("source"))],
        "issues": issues,
        "host_coverage": _coverage(
            scope=inventory.get("scope", "repository"), artifacts=artifacts, issues=issues
        ),
    }


def normalized_host_grants(inventory: dict[str, Any]) -> dict[str, Any]:
    return {
        "scope": inventory.get("scope", "repository"),
        "artifacts": sorted(
            list(inventory.get("artifacts") or []),
            key=lambda item: (str(item.get("artifact_id")), _canonical(item)),
        ),
        "grants": sorted(
            list(inventory.get("grants") or []),
            key=lambda item: (str(item.get("grant_id")), _canonical(item)),
        ),
        "host_coverage": sorted(
            list(inventory.get("host_coverage") or []),
            key=lambda item: (str(item.get("host")), _canonical(item)),
        ),
    }


def host_grants_sha256(grants: dict[str, Any]) -> str:
    return _sha(grants)


def build_host_grants_baseline(inventory: dict[str, Any]) -> dict[str, Any]:
    if not inventory_is_complete(inventory):
        raise ValueError(
            "Host-grants inventory is incomplete or experimental; fix its coverage "
            "issues before saving a baseline. A baseline cannot acknowledge missing evidence."
        )
    normalized = normalized_host_grants(inventory)
    payload = {
        "host_grants_schema_version": HOST_GRANTS_BASELINE_SCHEMA_VERSION,
        "scope": inventory["scope"],
        "inventory_sha256": host_grants_sha256(normalized),
        "inventory": normalized,
    }
    return HostGrantsBaselineV7.model_validate(payload).model_dump(mode="json")


def load_host_grants_baseline(path: Path) -> dict[str, Any]:
    baseline, _text = load_host_grants_baseline_with_text(path)
    return baseline


def load_host_grants_baseline_with_text(
    path: Path,
) -> tuple[dict[str, Any], str]:
    """Return validated baseline data and the exact descriptor-bound text."""

    display_path = path
    path = _exact_baseline_read_path(path)
    try:
        text = _read_exact_baseline_text(path, display_path=display_path)
        data = json.loads(text)
    except OSError as exc:
        raise ValueError(
            f"No readable host-grants baseline at {path} ({exc}). A human must "
            "review the current grants before creating or replacing a baseline."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Host-grants baseline {path} is not valid JSON ({exc}). Inspect and "
            "repair or replace it deliberately; do not overwrite it with the "
            "current grants."
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"Host-grants baseline {path} must be a JSON object. Inspect and "
            "repair or replace it deliberately."
        )
    version = data.get("host_grants_schema_version")
    if version == "0.1":
        # A valid-looking v0.1 artifact is intentionally not projected into v0.2:
        # it lacks scope and typed grant identities, so any diff would be lossy.
        if not isinstance(data.get("inventory"), dict):
            raise ValueError(
                f"Host-grants baseline {path} is missing its inventory. Inspect "
                "and repair or replace it deliberately."
            )
        return data, text
    if version not in {"0.2", "0.3", "0.4", "0.5", "0.6", HOST_GRANTS_BASELINE_SCHEMA_VERSION}:
        raise ValueError(
            f"Host-grants baseline {path} has unsupported schema version "
            f"{version!r}. A human must review migration or replacement."
        )
    try:
        model = {"0.2": HostGrantsBaselineV2, "0.3": HostGrantsBaselineV3,
                 "0.4": HostGrantsBaselineV4, "0.5": HostGrantsBaselineV5,
                 "0.6": HostGrantsBaselineV6, "0.7": HostGrantsBaselineV7}[version]
        parsed = model.model_validate(data).model_dump(mode="json")
    except ValidationError:
        return (
            {
                "host_grants_schema_version": f"{version}-invalid",
                "_load_error": f"malformed_v{version}_baseline",
            },
            text,
        )
    stored = parsed["inventory_sha256"]
    recomputed = host_grants_sha256(parsed["inventory"])
    if stored != recomputed:
        raise ValueError(
            f"Host-grants baseline {path} failed its integrity check: stored "
            f"inventory_sha256 {stored!r} does not match {recomputed}. Inspect "
            "the existing evidence and repair or replace it deliberately."
        )
    return parsed, text


def _read_exact_baseline_text(path: Path, *, display_path: Path) -> str:
    """Read the validated baseline through one identity-bound descriptor."""

    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(
            f"No readable host-grants baseline at {display_path} ({exc})."
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise ValueError(
                f"Host-grants baseline {display_path} must be one exact, "
                "singly-linked regular file."
            )
        if opened.st_size > MAX_HOST_BASELINE_BYTES:
            raise ValueError(
                f"Host-grants baseline {display_path} exceeds the "
                f"{MAX_HOST_BASELINE_BYTES}-byte static read limit."
            )
        raw = bytearray()
        while chunk := os.read(
            descriptor,
            min(1024 * 1024, MAX_HOST_BASELINE_BYTES + 1 - len(raw)),
        ):
            raw.extend(chunk)
            if len(raw) > MAX_HOST_BASELINE_BYTES:
                raise ValueError(
                    f"Host-grants baseline {display_path} exceeds the "
                    f"{MAX_HOST_BASELINE_BYTES}-byte static read limit."
                )
        after_read = os.fstat(descriptor)
        text = bytes(raw).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"Host-grants baseline {display_path} is not valid UTF-8 ({exc})."
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    # Recheck the lexical components and final directory entry after the read.
    # Together with O_NOFOLLOW and the descriptor metadata, this detects a
    # symlink/rename swap between validation and use instead of accepting bytes
    # from a different trust-evidence artifact.
    anchor = Path(path.anchor)
    relative = path.relative_to(anchor)
    issue = inspect_lexical_path_identity(anchor, relative)
    try:
        current = path.lstat()
    except OSError as exc:
        raise ValueError(
            f"Host-grants baseline {display_path} changed while it was read."
        ) from exc
    if (
        issue is not None
        or _stable_file_metadata(opened) != _stable_file_metadata(after_read)
        or _stable_file_metadata(opened) != _stable_file_metadata(current)
        or not stat.S_ISREG(current.st_mode)
        or current.st_nlink != 1
    ):
        raise ValueError(
            f"Host-grants baseline {display_path} changed identity while it "
            "was read; retry only after a human verifies the artifact."
        )
    return text


def _stable_file_metadata(metadata: os.stat_result) -> tuple[int, ...]:
    """Return the identity and mutation fields that must remain read-stable."""

    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _exact_baseline_read_path(path: Path) -> Path:
    """Return one exact regular, singly-linked baseline path.

    Baseline bytes are acknowledged trust evidence. Reading through a symlink
    can silently substitute an external artifact, while a hardlink can mutate
    the committed baseline through another name. Normalize lexical ``..``
    components and then inspect the exact path that will be read.
    """

    lexical = Path(os.path.abspath(os.path.normpath(os.fspath(path))))
    anchor = Path(lexical.anchor)
    try:
        relative = lexical.relative_to(anchor)
    except ValueError as exc:
        raise ValueError(
            f"Host-grants baseline {path} has an unsupported path identity; "
            "select one exact regular file."
        ) from exc
    issue = inspect_lexical_path_identity(anchor, relative)
    if issue is not None:
        detail = f": {issue.detail}" if issue.detail else ""
        raise ValueError(
            f"Host-grants baseline {path} must use one exact non-symlink "
            f"filesystem identity ({issue.kind} at {issue.requested}{detail})."
        )
    try:
        metadata = lexical.lstat()
    except FileNotFoundError:
        return lexical
    except OSError as exc:
        raise ValueError(
            f"Could not inspect host-grants baseline {path}: {exc}"
        ) from exc
    if not stat.S_ISREG(metadata.st_mode):
        cause = IsADirectoryError(
            errno.EISDIR,
            "baseline path is not a regular file",
            str(lexical),
        )
        raise ValueError(
            f"Host-grants baseline {path} must be a regular file."
        ) from cause
    if metadata.st_nlink != 1:
        raise ValueError(
            f"Host-grants baseline {path} must not be hardlinked; select one "
            "independently stored reviewed artifact."
        )
    return lexical


def diff_host_grants(baseline: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    base_by_id = {item["grant_id"]: item for item in baseline.get("grants", [])}
    current_by_id = {item["grant_id"]: item for item in current.get("grants", [])}
    changes: list[dict[str, Any]] = []
    for grant_id in sorted(set(base_by_id) | set(current_by_id)):
        before = base_by_id.get(grant_id)
        after = current_by_id.get(grant_id)
        if before != after and not _same_workflow_grant(before, after):
            changes.append({"grant_id": grant_id, "baseline": before, "current": after})
    return changes


def _same_workflow_grant(before: dict | None, after: dict | None) -> bool:
    if any(
        not grant or grant.get("kind") != "workflow" or "permission_contexts" not in grant
        for grant in (before, after)
    ):
        return False
    # Raw declaration placement is retained for inspection, but replacing
    # inherited permissions by an identical explicit map changes no grant.
    # Step references compare as each job's multiset of declared references:
    # a reordered, renamed or re-id'd step that declares the same reference
    # changes no modeled fact, and no execution-order dependency is evaluated.
    # Agent launches and checkout refs compare the same way (#823): as each
    # job's multiset of declared facts, never by step label, and a launch's
    # `job_secrets` is context for the row, never compared.
    ignored = {"write_scopes", "config_sha256", "step_actions", "agent_launches", "checkout_refs"}

    def comparable(grant: dict[str, Any]) -> dict[str, Any]:
        # Both sides are v0.4+ shapes here, and a drift payload refuses a
        # v0.4-v0.6 baseline holding a workflow, so a missing key is "none".
        projection = {key: value for key, value in grant.items() if key not in ignored}
        projection["step_actions"] = sorted(
            step_action_key(item) for item in grant.get("step_actions", [])
        )
        projection["agent_launches"] = sorted(
            agent_launch_key(item) for item in grant.get("agent_launches", [])
        )
        projection["checkout_refs"] = sorted(
            checkout_ref_key(item) for item in grant.get("checkout_refs", [])
        )
        # Named secrets compare as each call's set of facts, whatever order a
        # saved snapshot lists them in (#693).
        projection["reusable_calls"] = [
            {
                **call,
                "secret_mappings": sorted(
                    secret_mapping_key(entry) for entry in call.get("secret_mappings", [])
                ),
            }
            for call in grant.get("reusable_calls", [])
        ]
        return projection

    return comparable(before) == comparable(after)


def _diff_host_artifacts(
    baseline: dict[str, Any], current: dict[str, Any]
) -> list[dict[str, Any]]:
    base_by_id = {item["artifact_id"]: item for item in baseline.get("artifacts", [])}
    current_by_id = {
        item["artifact_id"]: item for item in current.get("artifacts", [])
    }
    changes: list[dict[str, Any]] = []
    for artifact_id in sorted(set(base_by_id) | set(current_by_id)):
        before = base_by_id.get(artifact_id)
        after = current_by_id.get(artifact_id)
        if before != after and not _same_instruction_artifact(before, after):
            changes.append(
                {"artifact_id": artifact_id, "baseline": before, "current": after}
            )
    return changes


def _same_instruction_artifact(before: dict | None, after: dict | None) -> bool:
    present = [item for item in (before, after) if item is not None]
    if not present or any(
        item.get("kind") != "instructions" or item.get("parse_status") != "parsed"
        for item in present
    ):
        return False
    projections = [item.get("instruction_structure") or {} for item in present]
    if any(item.get("status") not in {"guidance", "structured"} for item in projections):
        return False
    if before is None or after is None:
        return projections[0]["status"] == "guidance"
    if projections[0] != projections[1]:
        return False
    return (
        {key: value for key, value in before.items() if key != "redacted_sha256"}
        == {key: value for key, value in after.items() if key != "redacted_sha256"}
    )


def _diff_host_coverage(
    baseline: dict[str, Any], current: dict[str, Any]
) -> list[dict[str, Any]]:
    base_by_host = {item["host"]: item for item in baseline.get("host_coverage", [])}
    current_by_host = {
        item["host"]: item for item in current.get("host_coverage", [])
    }
    changes: list[dict[str, Any]] = []
    guidance_paths = {
        (artifact["host"], artifact["path"])
        for inventory in (baseline, current)
        for artifact in inventory.get("artifacts", [])
        if artifact.get("parse_status") == "parsed"
        and (artifact.get("instruction_structure") or {}).get("status") == "guidance"
    }
    for host in sorted(set(base_by_host) | set(current_by_host)):
        before = base_by_host.get(host)
        after = current_by_host.get(host)
        def comparison(item, host=host):
            if item is None:
                return None
            return {**item, "sources_observed": [
                path for path in item.get("sources_observed", []) if (host, path) not in guidance_paths
            ]}
        if comparison(before) != comparison(after):
            changes.append({"host": host, "baseline": before, "current": after})
    return changes


def host_grant_expansion_signals(changes: list[dict[str, Any]]) -> list[str]:
    widened, narrowed_rules = _permission_direction_signals(changes)
    signals: list[str] = list(widened)
    for change in changes:
        before = change.get("baseline")
        after = change.get("current")
        if after is None:
            if before and before.get("kind") == "permission_rule" and before.get("disposition") in {"deny", "ask"}:
                signals.append(f"{before['disposition']}_rule_removed: {before['host']}:{before['rule']}")
            continue
        kind = after.get("kind")
        prefix = "added" if before is None else "changed"
        if kind == "mcp_server":
            signals.append(f"mcp_server_{prefix}: {after['host']}:{after['server']}")
        elif kind == "permission_rule" and after.get("disposition") == "allow":
            if (after["host"], str(after["rule"])) in narrowed_rules:
                # The narrower half of a replacement. This list is an
                # expansion channel — `preflight` prefixes it with
                # "Expansion signals:" and the drift markdown prints every
                # entry under "## Expansion signals" with a ⚠ — so an
                # `allow_rule_added` here puts the warning on the change
                # that *removed* authority. The narrowing is still visible:
                # it is in `changes` as a removal and an addition (#657).
                continue
            if before is not None and before.get("wildcard") and not after.get("wildcard"):
                # One grant id is one host, source, disposition and rule text,
                # so the rule itself did not change: a baseline saved before
                # #816 rated a one-tool MCP rule a whole-server grant. Reading
                # it narrower now removes nothing and adds nothing; the row
                # stays, as a change without an expansion signal.
                continue
            marker = "wildcard_allow" if after.get("wildcard") else "allow_rule"
            signals.append(f"{marker}_{prefix}: {after['host']}:{after['rule']}")
        elif kind == "hook":
            # An expansion is claimed only for a hook the host loads for this
            # project: one a settings layer declares, or one a plugin selects
            # that this repository's project settings enable from an
            # in-repository marketplace. A hook nothing selects, or one a
            # plugin selects without that enablement, is still a row, never
            # an expansion (#714). Read from the current grant only, so a
            # baseline that recorded such a file as `execute` does not report
            # a widening when it is re-read.
            if hook_loading_basis(after) in LOADED_HOOK_BASES:
                signals.append(f"{kind}_{prefix}: {after['host']}:{after['source']}")
        elif kind in {"permission_mode", "sandbox", "additional_path", "plugin_or_app"}:
            if (
                kind in {"permission_mode", "sandbox"}
                and before is not None
                and before.get("config_sha256") == after.get("config_sha256")
            ):
                # One grant id is one setting and value, and the digest says
                # the value was read the same way: only the rating moved, as
                # when a baseline saved before #827 rated `bypassPermissions`
                # `medium`. The file permits nothing new, so the row stays as
                # a change without an expansion signal, as #816 did for rules.
                continue
            signals.append(f"{kind}_{prefix}: {after['host']}:{after['source']}")
        elif kind == "workflow":
            previous = before or {}
            old_writes = set(previous.get("effective_write_scopes", previous.get("write_scopes", [])))
            new_writes = set(after.get("effective_write_scopes", after.get("write_scopes", [])))
            unknown_before = {
                context["job"] for context in previous.get("permission_contexts", [])
                if context["state"] != "explicit"
            }
            added_writes = {
                entry for entry in new_writes - old_writes
                if f"{entry.split(': ', 1)[0]}: write-all" not in old_writes
                and entry.split(': ', 1)[0] not in unknown_before
            }
            if added_writes or (
                after.get("pull_request_target") and not previous.get("pull_request_target")
            ):
                signals.append(f"workflow_write_{prefix}: {after['source']}")
            def inherited_calls(grant):
                return {
                    (call["job"], call["uses"])
                    for call in grant.get("reusable_calls", []) if call["secrets_inherit"]
                }
            if inherited_calls(after) - inherited_calls(previous):
                signals.append(f"workflow_secrets_inherited_{prefix}: {after['source']}")
            # Only a documented rule gained by a job's agent launches widens
            # (#823); every other agent-launch or checkout edit is a change.
            if gained_agent_widenings(before, after):
                signals.append(f"workflow_agent_widened_{prefix}: {after['source']}")
    return sorted(set(signals))


@dataclass(frozen=True)
class PermissionRuleReplacement:
    """One allow rule the lattice decided another replaced, in one host and source (#657, #816).

    ``direction`` is ``widened`` when the arriving rule covers the one that
    left, and ``narrowed`` when the rule that left covers the arrival. Rules
    are the published rule text, exactly as the grants carry them.
    """

    host: str
    source: str
    before_rule: str
    after_rule: str
    direction: Literal["widened", "narrowed"]


def _permission_direction_signals(
    changes: list[dict[str, Any]],
) -> tuple[list[str], set[tuple[str, str]]]:
    """The expansion signals and narrowed rules of :func:`permission_rule_replacements`."""

    replacements = permission_rule_replacements(changes)
    signals = [
        # Named as well as counted: `allow_rule_changed` says a rule
        # moved, this says which way and by how much. The add signal
        # stays too — it is not wrong, and readers already depend on it.
        f"permission_widened: {item.host}:{item.before_rule} -> {item.after_rule}"
        for item in replacements
        if item.direction == "widened"
    ]
    # A narrowing earns no entry in an expansion list. What it earns
    # is silence there, which is what the caller uses this set for.
    narrowed = {(item.host, item.after_rule) for item in replacements if item.direction == "narrowed"}
    return signals, narrowed


def permission_rule_replacements(
    changes: list[dict[str, Any]],
) -> list[PermissionRuleReplacement]:
    """Name a replaced allow rule as widened or narrowed.

    Grants are keyed by their rule text, so replacing `Bash(npm *)` with
    `Bash(npm test:*)` arrives as one removal and one addition — the same
    shape as replacing it with `Bash(*)`. Set arithmetic cannot tell those
    apart; the lattice can, for the patterns it decides (#657).

    Only pairs within one host, source and disposition are considered, and
    only where exactly one rule left and one arrived: with several on each
    side there is no evidence about which replaced which, and inventing a
    pairing would be inventing the direction too. A pair the lattice cannot
    decide produces nothing, which leaves the existing add/remove signals
    as the whole answer.

    A rule whose identical text only moved to another disposition in the
    same host and source — `deny` to `allow`, say — replaced nothing, so a
    rule that moved into `allow` is set aside before counting. It keeps its
    own `allow_rule_added` and `deny_rule_removed`. Without this, moving
    `Bash(git log *)` out of `deny` in the same edit that narrows
    `Bash(git status *)` to `Bash(git status --short *)` counted as a second
    arrival, and the narrower half was reported as a widening (#816). A rule
    that moved out of `allow` is set aside only when another allow rule also
    left; when it is the only one, it is the rule the arrival replaced.
    Identity is the exact rule text: nothing is paired by likeness.
    """

    removed: dict[tuple[str, str, str], list[str]] = {}
    added: dict[tuple[str, str, str], list[str]] = {}
    for change in changes:
        before, after = change.get("baseline"), change.get("current")
        for grant, sink in ((before, removed), (after, added)):
            if grant and grant.get("kind") == "permission_rule":
                key = (grant["host"], grant.get("source", ""), str(grant.get("disposition")))
                sink.setdefault(key, []).append(str(grant["rule"]))
    moved = {
        (host, source, rule)
        for (host, source, gone_from), gone in removed.items()
        for (other_host, other_source, arrived_in), arrived in added.items()
        if (other_host, other_source) == (host, source) and arrived_in != gone_from
        for rule in set(gone) & set(arrived)
    }
    # A rule present on both sides is unchanged and pairs with nothing.
    replacements: list[PermissionRuleReplacement] = []
    for key, gone in removed.items():
        if key[2] != "allow":
            continue
        host, source = key[0], key[1]
        arrived = added.get(key, [])
        left = [rule for rule in gone if rule not in arrived]
        # A rule that moved out of `allow` is set aside only when another
        # allow rule also left: that one is then what the arrival replaced.
        # When the moved rule is the only one that left, it is. It
        # was granted at the base, so comparing the arrival with it is the
        # same sound base-to-head comparison as any other replacement; setting
        # it aside instead left the arrival unpaired, so tightening
        # `Bash(npm *)` to `Bash(npm test *)` while denying `Bash(npm *)`
        # gained a widening (#816).
        only_gone = [rule for rule in left if (host, source, rule) not in moved] or left
        # A rule that moved *into* `allow` is always set aside: it was denied
        # or asked at the base, so it is never the narrower half of anything.
        only_arrived = [
            rule for rule in arrived if rule not in gone and (host, source, rule) not in moved
        ]
        if len(only_gone) != 1 or len(only_arrived) != 1:
            continue
        before_rule, after_rule = only_gone[0], only_arrived[0]
        if subsumes(after_rule, before_rule) is True:
            direction: Literal["widened", "narrowed"] | None = "widened"
        elif subsumes(before_rule, after_rule) is True:
            direction = "narrowed"
        else:
            direction = None
        if direction is not None:
            replacements.append(
                PermissionRuleReplacement(
                    host=host, source=source, before_rule=before_rule,
                    after_rule=after_rule, direction=direction,
                )
            )
    return replacements


def _incomparable_payload(
    *, inventory: dict[str, Any], baseline_file: str, reasons: list[str]
) -> dict[str, Any]:
    scope = inventory.get("scope", "repository")
    payload = {
        "host_grants_schema_version": HOST_GRANTS_DRIFT_SCHEMA_VERSION,
        "baseline_file": baseline_file,
        "scope": scope,
        "comparison_status": "incomparable",
        "baseline_sha256": None,
        "current_sha256": host_grants_sha256(normalized_host_grants(inventory)),
        "has_drift": None,
        "changes": [],
        "artifact_changes": [],
        "coverage_changes": [],
        "expansion_signals": [],
        "issues": inventory.get("issues", []),
        "incomparable_reasons": sorted(reasons),
        # An incomparable result was built from an existing baseline whose
        # meaning cannot be trusted. Advertising --save-baseline here would
        # replace that evidence with the current grants and silently
        # acknowledge them. Missing baselines are handled before this builder
        # and also route to a human before any first acknowledgement.
        "next_action": None,
    }
    return HostGrantsDriftV7.model_validate(payload).model_dump(mode="json")


#: Baseline versions a drift comparison reads as current. v0.5 only adds
#: ``resolved_through`` on artifacts read through an in-tree link (#700). A
#: v0.4 inventory refused such a link as unreadable, and an incomplete
#: inventory can never be saved, so a v0.4 baseline holds no artifact that v0.5
#: would describe differently. Accepting it keeps every saved baseline usable.
#: v0.6 adds workflow step references (#771) and v0.7 agent launches and
#: checkout refs (#823); the rules below narrow which older baselines that
#: acceptance still covers.
_COMPARABLE_BASELINE_SCHEMA_VERSIONS = frozenset(
    {"0.4", "0.5", "0.6", HOST_GRANTS_BASELINE_SCHEMA_VERSION}
)

#: Baseline versions whose workflow grants never read step action references
#: (#771). Such a grant's missing ``step_actions`` is not evidence that no
#: step declared one, so a baseline holding a workflow grant is incomparable.
#: A baseline with no workflow grant stays comparable: every workflow the
#: current inventory holds is then an added grant, whose references no side
#: claims were compared.
_STEP_ACTIONS_UNREAD_BASELINE_SCHEMA_VERSIONS = frozenset({"0.4", "0.5"})

#: Baseline versions whose workflow grants never read agent launches or
#: checkout refs (#823), by the same rule: silence is not evidence of none.
_AGENT_LAUNCHES_UNREAD_BASELINE_SCHEMA_VERSIONS = frozenset({"0.4", "0.5", "0.6"})


def build_host_drift_payload(
    *, baseline: dict[str, Any], inventory: dict[str, Any], baseline_file: str
) -> dict[str, Any]:
    reasons: list[str] = []
    if baseline.get("host_grants_schema_version") == "0.1":
        reasons.append("baseline_schema_v0.1_lacks_typed_grants_and_scope")
    elif baseline.get("host_grants_schema_version") not in _COMPARABLE_BASELINE_SCHEMA_VERSIONS:
        reasons.append(
            str(baseline.get("_load_error") or "unsupported_baseline_schema")
        )
    elif baseline.get("host_grants_schema_version") in _AGENT_LAUNCHES_UNREAD_BASELINE_SCHEMA_VERSIONS:
        version = baseline.get("host_grants_schema_version")
        workflows = [
            grant for grant in (baseline.get("inventory") or {}).get("grants", [])
            if grant.get("kind") == "workflow"
        ]
        if workflows and version in _STEP_ACTIONS_UNREAD_BASELINE_SCHEMA_VERSIONS:
            reasons.append("baseline_workflow_step_actions_unavailable")
        if workflows:
            reasons.append("baseline_workflow_agent_launches_unavailable")
        if version in _STEP_ACTIONS_UNREAD_BASELINE_SCHEMA_VERSIONS and any(
            grant.get("reusable_calls") for grant in workflows
        ):
            # Such a call's missing ``secret_mappings`` is not evidence that it
            # passed no named secret: those snapshots never read them (#693).
            reasons.append("baseline_reusable_workflow_secret_mappings_unavailable")
    if not inventory_is_complete(inventory):
        reasons.append("current_inventory_incomplete")
    baseline_scope = baseline.get("scope")
    if baseline_scope is not None and baseline_scope != inventory.get("scope"):
        reasons.append(f"scope_mismatch:{baseline_scope}->{inventory.get('scope')}")
    if any(
        artifact.get("kind") == "instructions"
        and instruction_profile(str(artifact.get("path") or "")) is not None
        and not artifact.get("instruction_structure")
        for artifact in (baseline.get("inventory") or {}).get("artifacts", [])
    ):
        reasons.append("baseline_instruction_structure_unavailable")
    if reasons:
        return _incomparable_payload(inventory=inventory, baseline_file=baseline_file, reasons=reasons)

    return _comparable_drift_payload(
        baseline_inventory=baseline["inventory"],
        inventory=inventory,
        baseline_file=baseline_file,
    )


def _comparable_drift_payload(
    *, baseline_inventory: dict[str, Any], inventory: dict[str, Any], baseline_file: str
) -> dict[str, Any]:
    current = normalized_host_grants(inventory)
    changes = diff_host_grants(baseline_inventory, current)
    artifact_changes = _diff_host_artifacts(baseline_inventory, current)
    coverage_changes = _diff_host_coverage(baseline_inventory, current)
    payload = {
        "host_grants_schema_version": HOST_GRANTS_DRIFT_SCHEMA_VERSION,
        "baseline_file": baseline_file,
        "scope": inventory["scope"],
        "comparison_status": "comparable",
        "baseline_sha256": host_grants_sha256(baseline_inventory),
        "current_sha256": host_grants_sha256(current),
        "has_drift": bool(changes or artifact_changes or coverage_changes),
        "changes": changes,
        "artifact_changes": artifact_changes,
        "coverage_changes": coverage_changes,
        "expansion_signals": host_grant_expansion_signals(changes),
        "issues": inventory.get("issues", []),
        "incomparable_reasons": [],
        "next_action": None,
    }
    return HostGrantsDriftV7.model_validate(payload).model_dump(mode="json")


def build_host_comparison_payload(
    *, before: dict[str, Any], after: dict[str, Any], baseline_file: str
) -> dict[str, Any]:
    """Drift between two freshly read inventories whose limits the caller proved unchanged.

    For #721 only: the caller has established that every partial or
    experimental source is byte-identical in both inventories, so what differs
    between them was read on both sides. Nothing here saves or loads a
    baseline. `build_host_grants_baseline` keeps refusing an incomplete
    inventory, because a saved baseline acknowledges evidence and a comparison
    between two commits does not.
    """

    if before.get("scope") != after.get("scope"):
        return _incomparable_payload(
            inventory=after,
            baseline_file=baseline_file,
            reasons=[f"scope_mismatch:{before.get('scope')}->{after.get('scope')}"],
        )
    return _comparable_drift_payload(
        baseline_inventory=normalized_host_grants(before),
        inventory=after,
        baseline_file=baseline_file,
    )


def render_host_audit_markdown(
    inventory: dict[str, Any], *, next_step: str | None = None
) -> str:
    lines = ["# Host Capability Audit", ""]
    lines.append(
        f"Static `{inventory['scope']}` inventory. Runtime session behavior was not verified."
    )
    lines.append("")
    lines.append("## Coverage")
    lines.append("")
    lines.append("| Host | Status | Observed sources |")
    lines.append("|---|---|---:|")
    for item in inventory["host_coverage"]:
        lines.append(f"| {item['host']} | {item['status']} | {len(item['sources_observed'])} |")
    lines.append("")
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for grant in inventory["grants"]:
        by_kind.setdefault(grant["kind"], []).append(grant)
    lines.append(f"## Grants ({len(inventory['grants'])})")
    lines.append("")
    if not inventory["grants"]:
        lines.append("No statically declared grants found in the selected scope.")
    else:
        for kind, grants in sorted(by_kind.items()):
            lines.append(f"- `{kind}`: {len(grants)}")
        wildcard_rules = [
            grant
            for grant in by_kind.get("permission_rule", [])
            if grant.get("disposition") == "allow" and grant.get("wildcard")
        ]
        # A `⚠` on `Read(**)` spends the reader's attention on the grant
        # least worth it, and teaches them the marker means nothing. The
        # warning names the wildcards whose tool class earned a severity;
        # the low-risk ones are still listed above, just not shouted (#657).
        notable = [grant for grant in wildcard_rules if grant.get("risk") != "low"]
        if notable:
            lines.append("")
            quiet = len(wildcard_rules) - len(notable)
            lines.append(
                f"⚠ {len(notable)} wildcard allow rule(s) above low risk; "
                "verification reports "
                "`SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW`."
                + (
                    f" {quiet} further wildcard rule(s) are read-only and listed above."
                    if quiet
                    else ""
                )
            )
    lines.append("")
    if inventory["issues"]:
        lines.append("## Coverage issues")
        lines.append("")
        for issue in inventory["issues"]:
            if issue["blocking"]:
                marker = "blocking"
            elif issue["kind"] in {"unsupported", "unreadable"}:
                # Nothing was declared and nothing was excluded: this surface
                # was read and one part of it could not be compared.
                marker = "not compared"
            else:
                marker = "declared exclusion"
            lines.append(f"- `{issue['host']}` `{issue['source']}` ({marker}): {issue['message']}")
        lines.append("")
    lines.append("## Excluded scopes")
    lines.append("")
    for item in inventory["excluded_scopes"]:
        lines.append(f"- {item}")
    lines.extend([
        "",
        "---",
        next_step
        or "Next: `agents-shipgate verify --preview --json` for release gating.",
    ])
    return "\n".join(lines) + "\n"


def render_host_drift_markdown(payload: dict[str, Any]) -> str:
    lines = ["# Host Grant Drift", ""]
    if payload["comparison_status"] == "incomparable":
        lines.append("**Incomparable** — no trustworthy drift statement can be made.")
        lines.append("")
        for reason in payload["incomparable_reasons"]:
            lines.append(f"- `{reason}`")
        lines.extend(["", f"Next: {INCOMPARABLE_BASELINE_REVIEW}"])
        return "\n".join(lines) + "\n"
    if not payload["has_drift"]:
        lines.append("No drift — current host grants match the acknowledged baseline.")
        return "\n".join(lines) + "\n"
    lines.append(f"**Drift detected** — {len(payload['changes'])} typed grant change(s).")
    lines.append("")
    if payload["expansion_signals"]:
        lines.append("## Expansion signals")
        lines.append("")
        for signal in payload["expansion_signals"]:
            lines.append(f"- ⚠ `{signal}`")
        lines.append("")
    lines.append("After human review, re-record with `shipgate audit --host --save-baseline`.")
    return "\n".join(lines) + "\n"


__all__ = [
    "DEFAULT_BASELINE_FILE",
    "HOST_GRANTS_INVENTORY_SCHEMA_VERSION",
    "HOST_GRANTS_SCHEMA_VERSION",
    "INCOMPARABLE_BASELINE_REVIEW",
    "HostBoundarySnapshot",
    "HostStaticParseCache",
    "PermissionRuleReplacement",
    "PluginScopeFacts",
    "build_host_boundary_snapshot",
    "build_host_drift_payload",
    "build_host_grants_baseline",
    "diff_host_grants",
    "hook_loading_basis",
    "host_audit_inventory",
    "host_grant_expansion_signals",
    "host_grants_sha256",
    "inventory_is_complete",
    "load_host_grants_baseline",
    "load_host_grants_baseline_with_text",
    "normalized_host_grants",
    "permission_rule_replacements",
    "published_setting_value",
    "redacted_config_sha256",
    "render_host_audit_markdown",
    "render_host_drift_markdown",
    "without_host_issues",
    "without_host_sources",
]
