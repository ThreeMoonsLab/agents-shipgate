"""Conservative comparison of declared OpenShell authority, never runtime state.

Finite partitions prove exact REST/L4 comparisons while retaining each
binary × host × port × request relationship. Unsupported shapes are unknown.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

MAX_COMPARISON_CELLS = 100_000
_OTHER = object()
_READ = {"GET", "HEAD", "OPTIONS"}
_WRITE = _READ | {"POST", "PUT", "PATCH"}
_BASELINE_PATHS = {"/bin", "/usr", "/lib", "/proc", "/dev/urandom", "/etc", "/var/log", "/tmp", "/dev/null"}


@dataclass(frozen=True)
class OpenShellComparison:
    widened: tuple[str, ...] = ()
    narrowed: tuple[str, ...] = ()
    limits: tuple[str, ...] = ()

    @property
    def direction(self) -> str:
        if self.limits:
            return "unknown"
        if self.widened and self.narrowed:
            return "mixed"
        return "widened" if self.widened else "narrowed" if self.narrowed else "equivalent"

    @property
    def explanation(self) -> str:
        parts = [*(f"widens {item}" for item in self.widened),
                 *(f"narrows {item}" for item in self.narrowed), *self.limits]
        if self.direction in {"mixed", "unknown"}:
            parts.append("a single authority direction is unknown")
        return "; ".join(parts) or "equivalent declared authority in the supported static comparison"


def _canonical(value: Any) -> str:
    """Declaration collections are sets; labels and ordering grant no authority."""
    def normalize(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in sorted(item.items())}
        if isinstance(item, list):
            return sorted({json.dumps(normalize(child), sort_keys=True) for child in item})
        return item
    return json.dumps(normalize(value), sort_keys=True)


def _network(policy: dict) -> list[dict]:
    return [{key: value for key, value in rule.items() if key != "name"}
            for rule in policy.get("network_policies", {}).values()]


def _literal_path(path: str) -> bool:
    return bool(path.startswith("/") and PurePosixPath(path).as_posix() == path
                and not set(path.split("/")) & {".", ".."}
                and not any(char in path for char in "*?[]{}\\"))


def compare_openshell_grants(before: dict | None, after: dict | None) -> OpenShellComparison:
    if not before or not after:
        return OpenShellComparison(limits=("selected document added or removed; replacement runtime policy is unestablished",))
    left, right = before["facts"], after["facts"]
    if any(left.get(key) != right.get(key) for key in ("runtime_version", "role", "policy_schema_version")):
        return OpenShellComparison(limits=("policy version or input role changed",))
    old, new = left["policy"], right["policy"]
    widened: list[str] = []
    narrowed: list[str] = []
    limits: list[str] = []

    def record(label: str, grows: bool, shrinks: bool) -> None:
        if grows:
            widened.append(label)
        if shrinks:
            narrowed.append(label)

    old_fs, new_fs = old.get("filesystem_policy"), new.get("filesystem_policy")
    if _canonical(old_fs) != _canonical(new_fs):
        if old_fs is None or new_fs is None:
            limits.append("filesystem omission selects runtime defaults")
        elif old_fs["include_workdir"] != new_fs["include_workdir"]:
            limits.append("workdir identity and filesystem reach are runtime-dependent")
        else:
            old_read = set(old_fs["read_only"]) | set(old_fs["read_write"])
            new_read = set(new_fs["read_only"]) | set(new_fs["read_write"])
            old_write, new_write = set(old_fs["read_write"]), set(new_fs["read_write"])
            changed = (old_read ^ new_read) | (old_write ^ new_write)
            all_paths = old_read | new_read
            # Whether a path is an existing file, directory or symlink is not
            # established. Ancestor/descendant replacements need that context.
            nested = any(a != b and b.startswith(a.rstrip("/") + "/")
                         for a in all_paths for b in all_paths)
            baseline_active = bool(old.get("network_policies")) or bool(new.get("network_policies"))
            if any(not _literal_path(path) for path in all_paths) or nested:
                limits.append("filesystem path resolution or ancestor overlap is unproven")
            elif baseline_active and changed & _BASELINE_PATHS:
                limits.append("changed filesystem path overlaps the runtime-added baseline")
            else:
                record("filesystem read paths", bool(new_read - old_read), bool(old_read - new_read))
                record("filesystem write paths", bool(new_write - old_write), bool(old_write - new_write))
    if bool(old.get("network_policies")) != bool(new.get("network_policies")):
        limits.append("network policy presence changes the runtime-added filesystem baseline")
    old_landlock = (old.get("landlock") or {}).get("compatibility", "best_effort")
    new_landlock = (new.get("landlock") or {}).get("compatibility", "best_effort")
    record("Landlock compatibility", old_landlock == "hard_requirement" and new_landlock == "best_effort",
           old_landlock == "best_effort" and new_landlock == "hard_requirement")
    if old.get("process") != new.get("process"):
        limits.append("process identity comparison requires image and driver context")
    if _canonical(_network(old)) != _canonical(_network(new)):
        try:
            grows, shrinks = _compare_network(old, new)
            record("exact binary/destination/request grants", grows, shrinks)
        except _Unsupported as exc:
            limits.append(str(exc))
    return OpenShellComparison(tuple(widened), tuple(narrowed), tuple(limits))


class _Unsupported(ValueError):
    pass


def _groups(policy: dict) -> dict[tuple[str, str, int], list[dict]]:
    groups: dict[tuple[str, str, int], list[dict]] = {}
    references = 0
    for rule in policy.get("network_policies", {}).values():
        for binary in rule["binaries"]:
            if not _literal_path(binary["path"]):
                raise _Unsupported("wildcard or unresolved executable selector is outside exact comparison")
            for endpoint in rule["endpoints"]:
                host = endpoint["host"]
                if not host or not re.fullmatch(r"[A-Za-z0-9.-]+", host) or endpoint["allowed_ips"]:
                    raise _Unsupported("host wildcard, address constraint or unresolved destination is outside exact comparison")
                if endpoint["path"] or endpoint["protocol"] not in {"", "tcp", "rest"}:
                    raise _Unsupported("endpoint path routing or protocol is outside exact REST/L4 comparison")
                if endpoint["tls"] or endpoint["allow_encoded_slash"] or any(
                    endpoint[key] for key in ("credential_signing", "signing_service", "signing_region",
                                              "credential_binding", "allow_uninspected_credentials",
                                              "websocket_credential_rewrite", "request_body_credential_rewrite")
                ):
                    raise _Unsupported("transport or credential semantics are outside exact comparison")
                if (endpoint["mcp"] or endpoint["json_rpc"] or endpoint["graphql_persisted_queries"]
                    or endpoint["persisted_queries"] not in {"", "deny"}
                    or endpoint["graphql_max_body_bytes"] != 65536):
                    raise _Unsupported("protocol-specific options are outside exact comparison")
                if endpoint["protocol"] != "rest" and (endpoint["rules"] or endpoint["deny_rules"] or endpoint["access"]):
                    raise _Unsupported("request fields on an uninspected endpoint are outside exact comparison")
                for matcher in [*(item["allow"] for item in endpoint["rules"]), *endpoint["deny_rules"]]:
                    if any(matcher[key] for key in ("command", "query", "operation_type", "operation_name", "fields", "tool", "params")):
                        raise _Unsupported("request constraints are outside exact method/path comparison")
                    if not matcher["method"] or (matcher["method"] != "*" and not re.fullmatch(r"[A-Z]+", matcher["method"])):
                        raise _Unsupported("method matcher is outside exact comparison")
                    if not _literal_path(matcher["path"]):
                        raise _Unsupported("request path wildcard or omission is outside exact comparison")
                for port in set(endpoint["ports"] or [endpoint["port"]]):
                    groups.setdefault((binary["path"], host.lower(), port), []).append(endpoint)
                    references += 1
                    if references > MAX_COMPARISON_CELLS:
                        raise _Unsupported("network comparison exceeds its cell limit")
    return groups


def _matches(matcher: dict, method: Any, path: Any) -> bool:
    return matcher["method"] in {"*", method} and matcher["path"] == path


def _allows(endpoints: list[dict], method: Any, path: Any) -> bool:
    if not endpoints:
        return False
    inspected = [endpoint for endpoint in endpoints if endpoint["protocol"] == "rest"]
    if not inspected:
        return True
    modes = {endpoint["enforcement"] or "audit" for endpoint in inspected}
    if len(modes) != 1:
        raise _Unsupported("overlapping inspected endpoints disagree on enforcement")
    if modes == {"audit"}:
        return True  # Well-formed requests only; malformed requests remain denied.
    if any(_matches(deny, method, path) for endpoint in inspected for deny in endpoint["deny_rules"]):
        return False
    return any(
        endpoint["access"] == "full"
        or method in (_READ if endpoint["access"] == "read-only" else _WRITE if endpoint["access"] == "read-write" else set())
        or any(_matches(item["allow"], method, path) for item in endpoint["rules"])
        for endpoint in inspected
    )


def _compare_network(old: dict, new: dict) -> tuple[bool, bool]:
    before, after = _groups(old), _groups(new)
    grows = shrinks = False
    cells = 0
    for host, port in sorted({key[1:] for key in before.keys() | after.keys()}):
        binaries = sorted({key[0] for key in before.keys() | after.keys() if key[1:] == (host, port)})
        if len(binaries) > 8:
            raise _Unsupported("network comparison exceeds eight exact binary selectors per destination")
        endpoints = [endpoint for groups in (before, after)
                     for key, entries in groups.items() if key[1:] == (host, port)
                     for endpoint in entries]
        methods: set[Any] = _WRITE | {"DELETE", "TRACE", "CONNECT", _OTHER}
        paths: set[Any] = {_OTHER}
        for endpoint in endpoints:
            for matcher in [*(item["allow"] for item in endpoint["rules"]), *endpoint["deny_rules"]]:
                if matcher["method"] != "*":
                    methods.add(matcher["method"])
                paths.add(matcher["path"])
        combinations = (1 << len(binaries)) - 1
        cells += combinations * len(methods) * len(paths)
        if cells > MAX_COMPARISON_CELLS:
            raise _Unsupported("network comparison exceeds its cell limit")
        # A process and any ancestor may independently match a binary path.
        # Evaluate every nonempty matching set: denial and inspection apply
        # across all those rules, not just to each binary in isolation.
        for mask in range(1, combinations + 1):
            matched = [binary for index, binary in enumerate(binaries) if mask & (1 << index)]
            left = [endpoint for binary in matched for endpoint in before.get((binary, host, port), [])]
            right = [endpoint for binary in matched for endpoint in after.get((binary, host, port), [])]
            for method in methods:
                for path in paths:
                    was, now = _allows(left, method, path), _allows(right, method, path)
                    grows |= now and not was
                    shrinks |= was and not now
    return grows, shrinks
