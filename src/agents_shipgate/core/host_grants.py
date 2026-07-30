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
import re
import stat
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import yaml
from pydantic import ValidationError

from agents_shipgate.core.boundary_registry import BOUNDARY_ADAPTERS
from agents_shipgate.core.host_boundary import (
    _is_wildcard_allow,
    _is_write,
    _normalize_workflow_keys,
    _server_map,
    _string_entries,
    _transport_hint,
    _trigger_names,
)
from agents_shipgate.core.privacy import SENSITIVE_VALUE_KEYS
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
    HostGrantsDriftV2,
    HostGrantsInventoryV2,
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
                    self._reads[key] = _safe_read(
                        path,
                        containment_root=containment_root,
                        reader=self.reader_for(containment_root),
                    )
                except IdentityReadBudgetExceeded as exc:
                    self._resource_bound_error = (
                        "static host-boundary inventory exceeded its aggregate "
                        f"resource bound ({exc})"
                    )
                    self._reads[key] = (None, self._resource_bound_error)
                except (OSError, NotImplementedError, ValueError):
                    self._reads[key] = (
                        None,
                        "symbolic links or filesystem aliases in configuration "
                        "paths are not followed",
                    )
        return self._reads[key]

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
            except IdentityReadBudgetExceeded as exc:
                self._resource_bound_error = (
                    "static host-boundary inventory exceeded its aggregate "
                    f"resource bound ({exc})"
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
class HostBoundarySnapshot:
    """Reusable normalized snapshot for audit/check/verify projections."""

    inventory: dict[str, Any]
    cache: HostStaticParseCache


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


def redacted_config_sha256(config: Any) -> str:
    return _sha(_redact_secret_values(config))


def _display_path(path: Path, *, root: Path, home: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        pass
    try:
        return f"~/{path.resolve().relative_to(home).as_posix()}"
    except ValueError:
        return str(path)


def _inventory_issue(
    *, kind: str, host: str, source: str, message: str, blocking: bool
) -> dict[str, Any]:
    return {
        "issue_id": _stable_id("host_issue", kind, host, source, message),
        "kind": kind,
        "host": host,
        "source": source,
        "message": message,
        "blocking": blocking,
    }


def _artifact(
    *, host: str, scope: HostScope, source: str, kind: str, status: str, data: Any = None
) -> dict[str, Any]:
    return {
        "artifact_id": _stable_id("host_artifact", host, scope, source, kind),
        "host": host,
        "scope": scope,
        "path": source,
        "kind": kind,
        "parse_status": status,
        "redacted_sha256": redacted_config_sha256(data) if data is not None else None,
    }


def _grant_base(
    *, host: str, scope: HostScope, source: str, kind: str, identity: str,
    config: Any, access: str, risk: str,
) -> dict[str, Any]:
    return {
        "grant_id": _stable_id("host_grant", host, scope, source, kind, identity),
        "host": host,
        "scope": scope,
        "source": source,
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
) -> tuple[str | None, str | None]:
    lexical_root = Path(os.path.abspath(os.path.normpath(os.fspath(containment_root))))
    lexical_path = Path(os.path.abspath(os.path.normpath(os.fspath(path))))
    try:
        relative = lexical_path.relative_to(lexical_root)
    except ValueError:
        return None, "path is outside the allowlisted static configuration root"
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
        return None, "static host-boundary read exceeded its aggregate resource bound"
    except (OSError, NotImplementedError, ValueError) as exc:
        message = str(exc)
        if "singly-linked regular file" in message:
            return None, "configuration path is not one singly-linked regular file"
        if "file exceeds" in message:
            return None, f"file exceeds the {MAX_HOST_CONFIG_BYTES}-byte static audit limit"
        if "changed" in message:
            return None, "configuration path changed identity while it was read"
        if isinstance(exc, (OSError, NotImplementedError)):
            return None, f"file could not be read as UTF-8 ({exc.__class__.__name__})"
        return None, (
            "symbolic links or filesystem aliases in configuration paths are "
            "not followed"
        )
    try:
        return raw.decode("utf-8", errors="strict"), None
    except UnicodeDecodeError:
        return None, "file could not be read as UTF-8 (UnicodeDecodeError)"


def _load_structured(
    *, path: Path, source: str, host: str, kind: str, scope: HostScope,
    containment_root: Path, cache: HostStaticParseCache,
    artifacts: list[dict[str, Any]], issues: list[dict[str, Any]],
) -> Any:
    data, error_kind, error_message = cache.parse(
        path, containment_root=containment_root
    )
    if error_kind is not None:
        assert error_message is not None
        issues.append(_inventory_issue(
            kind=error_kind,
            host=host,
            source=source,
            message=error_message,
            blocking=True,
        ))
        artifacts.append(_artifact(host=host, scope=scope, source=source, kind=kind, status="failed"))
        return None
    artifacts.append(_artifact(host=host, scope=scope, source=source, kind=kind, status="parsed", data=data))
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


def _mcp_grants(
    data: Any, *, host: str, scope: HostScope, source: str
) -> list[dict[str, Any]]:
    grants: list[dict[str, Any]] = []
    for name, server in sorted(_server_map(data).items()):
        config = server if isinstance(server, dict) else {"value": server}
        base = _grant_base(
            host=host, scope=scope, source=source, kind="mcp_server",
            identity=str(name), config=config, access="external", risk="high",
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
            access = "admin" if wildcard else ("execute" if disposition == "allow" else "none")
            risk = "critical" if wildcard else ("high" if disposition == "allow" else "low")
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


def _hooks_grants(data: Any, *, host: str, scope: HostScope, source: str) -> list[dict[str, Any]]:
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return []
    return [
        {
            **_grant_base(
                host=host, scope=scope, source=source, kind="hook",
                identity=str(event), config=config, access="execute", risk="high",
            ),
            "event": str(event),
        }
        for event, config in sorted(hooks.items())
    ]


def _claude_grants(data: Any, *, scope: HostScope, source: str) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    grants = _permission_rule_grants(data.get("permissions"), host="claude-code", scope=scope, source=source)
    permissions = data.get("permissions") if isinstance(data.get("permissions"), dict) else {}
    mode_keys = (
        "defaultMode", "disableBypassPermissionsMode", "allowManagedPermissionRulesOnly",
        "allowManagedHooksOnly", "skipDangerousModePermissionPrompt",
        "enableAllProjectMcpServers", "disableAllHooks",
    )
    for setting in mode_keys:
        container = permissions if setting in permissions else data
        if setting in container:
            value = container[setting]
            risky = setting in {"skipDangerousModePermissionPrompt", "enableAllProjectMcpServers"} and bool(value)
            grants.append(_setting_grant(
                host="claude-code", scope=scope, source=source, kind="permission_mode",
                setting=setting, value=value, access="admin" if risky else "unknown",
                risk="critical" if risky else "medium",
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


def _workflow_grant(data: Any, *, source: str) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    data = _normalize_workflow_keys(data)
    triggers = sorted(_trigger_names(data.get("on")))
    write_scopes: list[str] = []
    write_all = False

    def collect(perms: Any, where: str) -> None:
        nonlocal write_all
        if perms == "write-all":
            write_all = True
            write_scopes.append(f"{where}: write-all")
        elif isinstance(perms, dict):
            for scope_name, value in sorted(perms.items()):
                if _is_write(value):
                    write_scopes.append(f"{where}: {scope_name}: {value}")

    collect(data.get("permissions"), "<top-level>")
    jobs = data.get("jobs")
    if isinstance(jobs, dict):
        for job_name, job in sorted(jobs.items()):
            if isinstance(job, dict):
                collect(job.get("permissions"), str(job_name))
    pull_target = "pull_request_target" in triggers
    return {
        **_grant_base(
            host="github", scope="repository", source=source, kind="workflow",
            identity=source, config=data,
            access="admin" if write_all else ("write" if write_scopes or pull_target else "read"),
            risk="critical" if write_all or pull_target else ("high" if write_scopes else "low"),
        ),
        "triggers": triggers,
        "pull_request_target": pull_target,
        "write_all": write_all,
        "write_scopes": sorted(write_scopes),
    }


def _instruction_grant(*, host: str, scope: HostScope, source: str, data: str) -> dict[str, Any]:
    redacted_text = _sanitize_sensitive_string(data)
    return {
        **_grant_base(
            host=host, scope=scope, source=source, kind="instruction_trust_root",
            identity=source,
            config={"content_sha256": hashlib.sha256(redacted_text.encode()).hexdigest()},
            access="execute", risk="medium",
        ),
        "path": source,
    }


def _collect_file(
    *, path: Path, source: str, host: str, scope: HostScope, kind: str,
    containment_root: Path, cache: HostStaticParseCache,
    artifacts: list[dict[str, Any]], grants: list[dict[str, Any]], issues: list[dict[str, Any]],
) -> Any:
    if kind == "instructions":
        text, error = cache.read(path, containment_root=containment_root)
        if error:
            issues.append(_inventory_issue(kind="unreadable", host=host, source=source, message=error, blocking=True))
            artifacts.append(_artifact(host=host, scope=scope, source=source, kind=kind, status="failed"))
            return
        assert text is not None
        redacted_text = _sanitize_sensitive_string(text)
        artifacts.append(_artifact(host=host, scope=scope, source=source, kind=kind, status="parsed", data={"sha256": hashlib.sha256(redacted_text.encode()).hexdigest()}))
        grants.append(_instruction_grant(host=host, scope=scope, source=source, data=text))
        return text

    data = _load_structured(
        path=path, source=source, host=host, kind=kind, scope=scope,
        containment_root=containment_root, cache=cache,
        artifacts=artifacts, issues=issues,
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
    elif kind == "workflow":
        grant = _workflow_grant(data, source=source)
        if grant is not None:
            grants.append(grant)
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
    elif host == "claude-code":
        grants.extend(_claude_grants(data, scope=scope, source=source))
    elif host == "cursor":
        grants.extend(_cursor_grants(data, scope=scope, source=source))
    elif kind == "hooks":
        grants.extend(_hooks_grants(data, host=host, scope=scope, source=source))
    return data


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
) -> tuple[list[tuple[Path, str, str, str]], int]:
    """Enumerate repository sources exclusively from the boundary registry."""

    indexed: dict[tuple[str, str], tuple[Path, str, str, str]] = {}
    skipped = {".git", ".hg", ".svn", "node_modules", "site-packages", ".venv", "venv"}
    candidates: list[tuple[Path, str]] = []
    symlink_directories: list[str] = []
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
            raise RuntimeError(
                "repository host-boundary inventory exceeded its static "
                "filesystem-entry bound"
            ) from exc
        except (OSError, NotImplementedError, ValueError) as exc:
            raise RuntimeError(
                "repository host-boundary inventory could not inspect the "
                "workspace safely"
            ) from exc
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
                        symlink_directories.append(relative)
                    continue
                if stat.S_ISDIR(metadata.st_mode):
                    if name not in skipped:
                        child_directories.append(Path(relative))
                    continue
            except (OSError, ValueError) as exc:
                raise RuntimeError(
                    "repository host-boundary inventory could not inspect a "
                    "workspace entry safely"
                ) from exc
            candidates.append((candidate, relative))
        pending.extend(reversed(child_directories))

    for adapter in BOUNDARY_ADAPTERS:
        for expected in adapter.exact_paths:
            if any(
                expected.casefold().startswith(f"{prefix.casefold()}/")
                for prefix in symlink_directories
            ):
                candidates.append((root / expected, expected))

    for path, relative in candidates:
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
                )
    return [indexed[key] for key in sorted(indexed)], visited


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
        issues.append(_inventory_issue(
            kind=error_kind, host="claude-code", source=source,
            message=error, blocking=True,
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
        if host == "vscode" and host_artifacts:
            status = "experimental"
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


def _local_precedence_issues(
    artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fail closed where several static layers require an effective merge.

    The inventory retains every redacted declaration, but it must not claim
    effective-authority coverage until host-specific precedence is projected
    at the individual setting/grant level.
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
                    f"Multiple {host} {kind} layers were observed; their "
                    "runtime-effective precedence is not statically projected."
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

    resource_bound_error: str | None = None
    identity_snapshot_error: str | None = None
    try:
        repository_paths, _inventory_entries = _repository_paths(
            root,
            reader=cache.reader_for(root),
        )
    except (RuntimeError, IdentityReadBudgetExceeded) as exc:
        repository_paths = []
        resource_bound_error = str(exc)
    for path, source, host, kind in repository_paths:
        _collect_file(
            path=path, source=source, host=host, scope="repository", kind=kind,
            containment_root=root, cache=cache,
            artifacts=artifacts, grants=grants, issues=issues,
        )

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
        issues.extend(_local_precedence_issues(artifacts))

    try:
        cache.finish()
    except IdentityReadBudgetExceeded as exc:
        resource_bound_error = cache.resource_bound_error or str(exc)
    except (OSError, NotImplementedError, ValueError) as exc:
        identity_snapshot_error = (
            "static host-boundary inventory changed identity while its bounded "
            f"snapshot was captured ({exc})"
        )
    if cache.resource_bound_error is not None:
        resource_bound_error = cache.resource_bound_error
    if resource_bound_error is not None:
        # Aggregate exhaustion can prevent the final identity pass. Do not
        # retain a partially validated projection as diagnostic grant data.
        artifacts.clear()
        grants.clear()
        for host in ("codex", "claude-code", "cursor", "vscode", "github"):
            issues.append(
                _inventory_issue(
                    kind="unreadable",
                    host=host,
                    source="<repository>",
                    message=resource_bound_error,
                    blocking=True,
                )
            )
    if identity_snapshot_error is not None:
        # No parsed projection is trustworthy when the final exact-name pass
        # cannot bind it to the entries that were opened.
        artifacts.clear()
        grants.clear()
        for host in ("codex", "claude-code", "cursor", "vscode", "github"):
            issues.append(
                _inventory_issue(
                    kind="unreadable",
                    host=host,
                    source="<repository>",
                    message=identity_snapshot_error,
                    blocking=True,
                )
            )

    artifacts.sort(key=lambda item: (item["host"], item["scope"], item["path"], item["kind"]))
    grants.sort(key=lambda item: item["grant_id"])
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
    inventory = HostGrantsInventoryV2.model_validate(payload).model_dump(mode="json")
    return HostBoundarySnapshot(inventory=inventory, cache=cache)


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
    inventory = HostGrantsInventoryV2.model_validate(snapshot.inventory)
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
    return HostGrantsBaselineV2.model_validate(payload).model_dump(mode="json")


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
    if version != HOST_GRANTS_BASELINE_SCHEMA_VERSION:
        raise ValueError(
            f"Host-grants baseline {path} has unsupported schema version "
            f"{version!r}. A human must review migration or replacement."
        )
    try:
        parsed = HostGrantsBaselineV2.model_validate(data).model_dump(mode="json")
    except ValidationError:
        return (
            {
                "host_grants_schema_version": "0.2-invalid",
                "_load_error": "malformed_v0.2_baseline",
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
        if before != after:
            changes.append({"grant_id": grant_id, "baseline": before, "current": after})
    return changes


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
        if before != after:
            changes.append(
                {"artifact_id": artifact_id, "baseline": before, "current": after}
            )
    return changes


def _diff_host_coverage(
    baseline: dict[str, Any], current: dict[str, Any]
) -> list[dict[str, Any]]:
    base_by_host = {item["host"]: item for item in baseline.get("host_coverage", [])}
    current_by_host = {
        item["host"]: item for item in current.get("host_coverage", [])
    }
    changes: list[dict[str, Any]] = []
    for host in sorted(set(base_by_host) | set(current_by_host)):
        before = base_by_host.get(host)
        after = current_by_host.get(host)
        if before != after:
            changes.append({"host": host, "baseline": before, "current": after})
    return changes


def host_grant_expansion_signals(changes: list[dict[str, Any]]) -> list[str]:
    signals: list[str] = []
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
            marker = "wildcard_allow" if after.get("wildcard") else "allow_rule"
            signals.append(f"{marker}_{prefix}: {after['host']}:{after['rule']}")
        elif kind in {"permission_mode", "sandbox", "additional_path", "plugin_or_app", "hook"}:
            signals.append(f"{kind}_{prefix}: {after['host']}:{after['source']}")
        elif kind == "workflow" and (
            after.get("write_all") or after.get("write_scopes") or after.get("pull_request_target")
        ):
            signals.append(f"workflow_write_{prefix}: {after['source']}")
    return sorted(set(signals))


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
    return HostGrantsDriftV2.model_validate(payload).model_dump(mode="json")


def build_host_drift_payload(
    *, baseline: dict[str, Any], inventory: dict[str, Any], baseline_file: str
) -> dict[str, Any]:
    reasons: list[str] = []
    if baseline.get("host_grants_schema_version") == "0.1":
        reasons.append("baseline_schema_v0.1_lacks_typed_grants_and_scope")
    elif baseline.get("host_grants_schema_version") != HOST_GRANTS_BASELINE_SCHEMA_VERSION:
        reasons.append(
            str(baseline.get("_load_error") or "unsupported_baseline_schema")
        )
    if not inventory_is_complete(inventory):
        reasons.append("current_inventory_incomplete")
    baseline_scope = baseline.get("scope")
    if baseline_scope is not None and baseline_scope != inventory.get("scope"):
        reasons.append(f"scope_mismatch:{baseline_scope}->{inventory.get('scope')}")
    if reasons:
        return _incomparable_payload(inventory=inventory, baseline_file=baseline_file, reasons=reasons)

    current = normalized_host_grants(inventory)
    baseline_inventory = baseline["inventory"]
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
    return HostGrantsDriftV2.model_validate(payload).model_dump(mode="json")


def render_host_audit_markdown(inventory: dict[str, Any]) -> str:
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
        if wildcard_rules:
            lines.append("")
            lines.append(
                f"⚠ {len(wildcard_rules)} wildcard allow rule(s); verification "
                "reports `SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW`."
            )
    lines.append("")
    if inventory["issues"]:
        lines.append("## Coverage issues")
        lines.append("")
        for issue in inventory["issues"]:
            marker = "blocking" if issue["blocking"] else "declared exclusion"
            lines.append(f"- `{issue['host']}` `{issue['source']}` ({marker}): {issue['message']}")
        lines.append("")
    lines.append("## Excluded scopes")
    lines.append("")
    for item in inventory["excluded_scopes"]:
        lines.append(f"- {item}")
    lines.extend(["", "---", "Next: `agents-shipgate verify --preview --json` for release gating."])
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
    "build_host_boundary_snapshot",
    "build_host_drift_payload",
    "build_host_grants_baseline",
    "diff_host_grants",
    "host_audit_inventory",
    "host_grant_expansion_signals",
    "host_grants_sha256",
    "inventory_is_complete",
    "load_host_grants_baseline",
    "load_host_grants_baseline_with_text",
    "normalized_host_grants",
    "redacted_config_sha256",
    "render_host_audit_markdown",
    "render_host_drift_markdown",
]
