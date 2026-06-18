from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents_shipgate.checks.verify import TRUST_ROOT_SURFACES
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.agent_controls import FORBIDDEN_SHORTCUTS
from agents_shipgate.core.boundary_diff import parse_unified_diff
from agents_shipgate.core.errors import ConfigError, InputParseError
from agents_shipgate.core.globbing import glob_match
from agents_shipgate.core.host_grants import (
    DEFAULT_BASELINE_FILE,
    build_host_drift_payload,
    host_audit_inventory,
    load_host_grants_baseline,
)
from agents_shipgate.core.lenses.effective_policy import (
    build_effective_policy_snapshot,
)
from agents_shipgate.schemas.preflight import (
    CapabilityRequestV1,
    HostPermissionRequestV1,
    PreflightDriftSummary,
    PreflightNextAction,
    PreflightPlanV1,
    PreflightProtectedSurface,
    PreflightProtectedSurfaceTouch,
    PreflightRequiredEvidence,
    PreflightResultV1,
    PreflightResultV2,
    PreflightSignalV1,
    ProtectedSurfaceScopeType,
    TrustRootGraphV1,
    TrustRootNodeV1,
)
from agents_shipgate.schemas.surfaces import ActionEffect

_FORBIDDEN_EDIT_CLASSES = frozenset({"ci_gate", "agent_instructions", "policy"})
_KEY_LEVEL_CLASSES = frozenset({"manifest", "shipgate_state"})
_CAPABILITY_SURFACE_CLASSES = frozenset(
    {"codex_plugin", "tool_surface_decl", "prompts"}
)
_CODEX_EXTRA_SURFACES: tuple[tuple[str, str], ...] = (
    ("codex_config", "**/.codex/config.toml"),
    ("codex_config", "**/.codex/config.json"),
    ("codex_hooks", "**/.codex/hooks.json"),
    ("codex_hooks", "**/.codex/hooks/**"),
    ("codex_plugin", "**/.codex-plugin/plugin.json"),
)
_CODEX_WHOLE_FILE_CLASSES = frozenset({"codex_config", "codex_hooks"})
_HIGH_RISK_EFFECTS: frozenset[ActionEffect] = frozenset(
    {
        "financial_write",
        "production_operation",
        "destructive",
        "code_execution",
        "identity_access",
        "privileged_data_access",
    }
)
_HIGH_RISK_TAGS = frozenset(
    {
        "financial_action",
        "financial_write",
        "destructive",
        "infrastructure_change",
        "production_operation",
        "production_ops",
        "code_execution",
        "identity_access",
        "privileged_data_access",
        "privileged_data",
        "sensitive_data_access",
        "secret_access",
        "external_write",
    }
)
_SEVERITY_RANK = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}
_TRUST_ROOT_WALK_SKIP_DIRS = frozenset(
    {
        ".cache",
        ".direnv",
        ".git",
        ".hg",
        ".mypy_cache",
        ".next",
        ".nox",
        ".pnpm-store",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".turbo",
        ".venv",
        "__pycache__",
        "agents-shipgate-reports",
        "build",
        "dist",
        "env",
        "node_modules",
        "site-packages",
        "target",
        "venv",
    }
)
_VERIFY_COMMAND = (
    "agents-shipgate verify --workspace . --config shipgate.yaml "
    "--ci-mode advisory --json"
)
_SIGNAL_KIND_RANK = {
    "protected_surface_touch": 0,
    "host_grant_drift": 1,
    "missing_evidence": 2,
    "least_privilege": 3,
    "policy_drift": 4,
    "verify_required": 5,
}
_BROAD_SCOPE_LITERALS = frozenset(
    {"*", "all", "admin", "root", "superuser", "write_all", "read_all"}
)
_HOST_WRITE_TOKENS = frozenset(
    {
        "approve",
        "auto_approve",
        "create",
        "delete",
        "destructive",
        "edit",
        "execute",
        "grant",
        "mcp_server_added",
        "patch",
        "pull_request_target",
        "run",
        "update",
        "write",
        "write-all",
    }
)


@dataclass(frozen=True)
class ProtectedSurfaceSpec:
    kind: str
    pattern: str
    scope_type: ProtectedSurfaceScopeType

    @property
    def description(self) -> str:
        if self.scope_type == "whole_file":
            return (
                "Whole-file protected release surface; coding agents must not "
                "edit it to clear a gate without human review."
            )
        if self.scope_type == "key_level":
            return (
                "Protected release-policy surface; legitimate mechanical edits "
                "may exist, but suppressions, baselines, waivers, and policy "
                "weakening require human review."
            )
        return (
            "Capability or tool-surface declaration under review; changes are "
            "allowed only when routed through Shipgate verification."
        )


def protected_surface_specs() -> tuple[ProtectedSurfaceSpec, ...]:
    """Return the canonical proactive protected-surface catalog.

    Specific Codex hook/config paths are listed before broad inherited patterns
    so preflight classification can name the narrowest surface.
    """

    seen: set[tuple[str, str]] = set()
    out: list[ProtectedSurfaceSpec] = []
    for kind, pattern in [*_CODEX_EXTRA_SURFACES, *TRUST_ROOT_SURFACES]:
        key = (kind, pattern)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            ProtectedSurfaceSpec(
                kind=kind,
                pattern=pattern,
                scope_type=_scope_type_for_kind(kind),
            )
        )
    return tuple(out)


def forbidden_file_edits() -> tuple[str, ...]:
    """Standing deny-list of whole-file trust roots.

    This intentionally mirrors the verifier agent-controller contract: it is a
    deny-list, not an allow-list, and it excludes key-level surfaces such as
    `shipgate.yaml` and `.agents-shipgate/**`.
    """

    patterns = [
        spec.pattern
        for spec in protected_surface_specs()
        if spec.kind in _FORBIDDEN_EDIT_CLASSES
        or spec.kind in _CODEX_WHOLE_FILE_CLASSES
    ]
    return tuple(sorted(set(patterns)))


def build_preflight_result(
    *,
    workspace: Path,
    config: Path = Path("shipgate.yaml"),
    changed_files: list[str] | None = None,
    capability_request: CapabilityRequestV1 | dict[str, Any] | None = None,
    capability_requests: list[CapabilityRequestV1 | dict[str, Any]] | None = None,
    host_permission_requests: list[HostPermissionRequestV1 | dict[str, Any]] | None = None,
    plan: PreflightPlanV1 | dict[str, Any] | None = None,
    base_preflight: PreflightResultV1 | PreflightResultV2 | dict[str, Any] | None = None,
    host_baseline: Path | None = None,
) -> PreflightResultV2:
    root = workspace.resolve()
    config_path = config if config.is_absolute() else root / config
    config_path = config_path.resolve()
    request_plan = _coerce_plan(plan)
    changed_inputs = list(changed_files or [])
    if request_plan is not None:
        changed_inputs.extend(request_plan.changed_files)
        if request_plan.diff_text:
            changed_inputs.extend(_changed_files_from_diff_text(request_plan.diff_text))
    changed = _normalize_changed_files(changed_inputs)
    graph = build_trust_root_graph(root)
    policy_hash, notes = _policy_hash_for_config(config_path)

    surfaces = [
        PreflightProtectedSurface(
            kind=node.kind,
            pattern=node.pattern,
            scope_type=node.scope_type,
            present=bool(node.present_paths),
            present_paths=node.present_paths,
            description=_spec_by_key()[(node.kind, node.pattern)].description,
        )
        for node in graph.nodes
    ]
    touches = classify_protected_touches(changed)
    requests = _coerce_capability_requests(
        capability_request=capability_request,
        capability_requests=capability_requests,
        plan=request_plan,
    )
    host_requests = _coerce_host_permission_requests(
        host_permission_requests=host_permission_requests,
        plan=request_plan,
    )
    required_evidence = required_evidence_for_capability_requests(requests)
    requires_human_review = bool(touches) or any(
        not item.satisfied and item.severity in {"high", "critical"}
        for item in required_evidence
    )
    base = _coerce_base_preflight(base_preflight)
    policy_drift = None
    trust_root_graph_diff = None
    if base is not None:
        policy_drift = _hash_drift(
            base_hash=base.policy_snapshot_hash,
            head_hash=policy_hash,
        )
        trust_root_graph_diff = _graph_drift(base.trust_root_graph, graph)
    host_grant_drift, host_grant_drift_note = _host_grant_drift_payload(
        workspace=root,
        baseline=host_baseline,
    )
    if host_grant_drift_note is not None:
        notes = [*notes, host_grant_drift_note]
    signals = _sorted_signals(
        [
            *signals_for_protected_touches(touches),
            *signals_for_host_grant_drift(host_grant_drift),
            *signals_for_capability_requests(requests),
            *least_privilege_signals(requests),
            *signals_for_host_permission_requests(host_requests),
            *signals_for_policy_drift(policy_drift, trust_root_graph_diff),
        ]
    )
    requires_human_review = requires_human_review or any(
        signal.actor == "human" for signal in signals
    )
    requires_verify = bool(changed or requests or host_requests)
    if requires_verify and not any(signal.kind == "verify_required" for signal in signals):
        signals = _sorted_signals([*signals, _verify_required_signal()])

    first_next_action = _first_next_action(signals=signals)
    allowed_next_commands = (
        [_VERIFY_COMMAND]
        if first_next_action.actor == "coding_agent"
        and first_next_action.kind == "verify"
        else []
    )

    return PreflightResultV2(
        workspace=str(root),
        config=_display_path(config_path, root),
        protected_surfaces=surfaces,
        forbidden_file_edits=list(forbidden_file_edits()),
        forbidden_actions=list(FORBIDDEN_SHORTCUTS),
        required_evidence=required_evidence,
        changed_files=changed,
        protected_surface_touches=touches,
        requires_human_review=requires_human_review,
        policy_snapshot_hash=policy_hash,
        trust_root_graph_hash=graph.graph_hash,
        trust_root_graph=graph,
        policy_drift=policy_drift,
        trust_root_graph_diff=trust_root_graph_diff,
        first_next_action=first_next_action,
        notes=notes,
        signals=signals,
        requires_verify=requires_verify,
        verification_command=_VERIFY_COMMAND if requires_verify else None,
        allowed_next_commands=allowed_next_commands,
        plan_summary=_plan_summary(
            changed=changed,
            capability_requests=requests,
            host_permission_requests=host_requests,
            signals=signals,
        ),
        host_grant_drift=host_grant_drift,
    )


def build_trust_root_graph(workspace: Path) -> TrustRootGraphV1:
    root = workspace.resolve()
    candidate_paths = _walk_trust_root_files(root)
    nodes: list[TrustRootNodeV1] = []
    for spec in sorted(protected_surface_specs(), key=lambda item: (item.kind, item.pattern)):
        present_paths = _present_paths(candidate_paths, spec.pattern)
        nodes.append(
            TrustRootNodeV1(
                id=_node_id(spec.kind, spec.pattern),
                kind=spec.kind,
                pattern=spec.pattern,
                scope_type=spec.scope_type,
                present_paths=present_paths,
                file_hashes={
                    path: _file_sha256(root / path)
                    for path in present_paths
                    if (root / path).is_file()
                },
            )
        )
    graph_hash = _stable_hash([node.model_dump(mode="json") for node in nodes])
    return TrustRootGraphV1(nodes=nodes, graph_hash=graph_hash)


def classify_protected_touches(
    changed_files: list[str],
) -> list[PreflightProtectedSurfaceTouch]:
    touches: list[PreflightProtectedSurfaceTouch] = []
    seen: set[str] = set()
    for raw in changed_files:
        path = raw.replace("\\", "/").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        spec = _classify(path)
        if spec is None:
            continue
        touches.append(
            PreflightProtectedSurfaceTouch(
                path=path,
                kind=spec.kind,
                pattern=spec.pattern,
                scope_type=spec.scope_type,
            )
        )
    return sorted(touches, key=lambda item: (item.kind, item.path, item.pattern))


def required_evidence_for_capability_request(
    request: CapabilityRequestV1,
) -> list[PreflightRequiredEvidence]:
    if not _is_high_risk_request(request):
        return []
    out = [
        _require(
            id_="owner",
            field="evidence.owner",
            satisfied=bool(request.evidence.owner),
            severity="high",
            reason="High-risk capabilities need an accountable owner.",
            recommendation="Declare the business or engineering owner before adding this action.",
        ),
        _require(
            id_="auth_scopes",
            field="scopes",
            satisfied=bool(request.scopes),
            severity="high",
            reason="High-risk capabilities need explicit least-privilege scopes.",
            recommendation="Declare the exact scopes required by the action; avoid wildcard or broad scopes.",
        ),
        _require(
            id_="approval_policy",
            field="controls.approval_required",
            satisfied=request.controls.approval_required is True,
            severity="critical",
            reason="High-risk capabilities need declared approval policy evidence.",
            recommendation="Add a real approval policy reference; a coding agent must not invent approval.",
        ),
        _require(
            id_="audit_log",
            field="controls.safeguard_audit_log",
            satisfied=request.controls.safeguard_audit_log is True,
            severity="high",
            reason="High-risk capabilities need auditability.",
            recommendation="Declare audit-log evidence or keep the capability out of the release.",
        ),
    ]
    if _needs_idempotency(request):
        out.append(
            _require(
                id_="idempotency",
                field="controls.safeguard_idempotency",
                satisfied=request.controls.safeguard_idempotency is True,
                severity="critical",
                reason="Financial, destructive, and external-write capabilities need idempotency evidence.",
                recommendation="Provide an idempotency key, idempotent annotation, or policy-backed evidence.",
            )
        )
    if request.effect in {"financial_write", "destructive", "external_communication"}:
        out.append(
            _require(
                id_="confirmation",
                field="controls.confirmation_required",
                satisfied=request.controls.confirmation_required is True,
                severity="high",
                reason="Externally visible or destructive actions need user/operator confirmation.",
                recommendation="Declare confirmation policy evidence before adding the action.",
            )
        )
    if request.effect in {"production_operation", "destructive", "code_execution"}:
        out.extend(
            [
                _require(
                    id_="runbook",
                    field="evidence.runbook",
                    satisfied=bool(request.evidence.runbook),
                    severity="high",
                    reason="Operational high-risk actions need reviewer-ready operating guidance.",
                    recommendation="Link a runbook or rollback procedure owned by a human team.",
                ),
                _require(
                    id_="rollback",
                    field="controls.safeguard_rollback",
                    satisfied=request.controls.safeguard_rollback is True,
                    severity="high",
                    reason="Production, destructive, and code-execution actions need rollback evidence.",
                    recommendation="Declare rollback evidence or keep the action out of production-like release.",
                ),
                _require(
                    id_="dry_run",
                    field="controls.safeguard_dry_run",
                    satisfied=request.controls.safeguard_dry_run is True,
                    severity="medium",
                    reason="Operational actions should expose a dry-run or preview path.",
                    recommendation="Declare dry-run support where available; otherwise route to human review.",
                ),
            ]
        )
    return sorted(
        out,
        key=lambda item: (_SEVERITY_RANK.get(item.severity, 99), item.id),
    )


def required_evidence_for_capability_requests(
    requests: list[CapabilityRequestV1],
) -> list[PreflightRequiredEvidence]:
    evidence: list[PreflightRequiredEvidence] = []
    for request in requests:
        for item in required_evidence_for_capability_request(request):
            evidence.append(
                item.model_copy(
                    update={
                        "id": f"{_capability_subject(request)}:{item.id}",
                        "field": f"{_capability_subject(request)}.{item.field}",
                    }
                )
            )
    return sorted(
        evidence,
        key=lambda item: (_SEVERITY_RANK.get(item.severity, 99), item.id),
    )


def signals_for_protected_touches(
    touches: list[PreflightProtectedSurfaceTouch],
) -> list[PreflightSignalV1]:
    return [
        PreflightSignalV1(
            id=f"protected_surface:{touch.path}",
            kind="protected_surface_touch",
            severity="critical" if touch.scope_type == "whole_file" else "high",
            actor="human",
            subject=touch.kind,
            path=touch.path,
            reason=(
                f"{touch.path} matches protected surface {touch.pattern}; "
                "a coding agent must not self-approve trust-root edits."
            ),
            recommendation="Route this protected-surface edit to a human before making or relying on it.",
            related_command="agents-shipgate preflight --workspace . --plan - --json",
        )
        for touch in touches
    ]


def signals_for_capability_requests(
    requests: list[CapabilityRequestV1],
) -> list[PreflightSignalV1]:
    signals: list[PreflightSignalV1] = []
    for request in requests:
        subject = _capability_subject(request)
        for item in required_evidence_for_capability_request(request):
            if item.satisfied:
                continue
            signals.append(
                PreflightSignalV1(
                    id=f"missing_evidence:{subject}:{item.id}",
                    kind="missing_evidence",
                    severity=item.severity,
                    actor="human"
                    if item.severity in {"medium", "high", "critical"}
                    else "coding_agent",
                    subject=subject,
                    path=None,
                    reason=item.reason,
                    recommendation=(
                        f"{item.recommendation} Field: {item.field}. "
                        "A coding agent must not invent approval, ownership, "
                        "idempotency, audit, confirmation, runbook, or rollback evidence."
                    ),
                    related_command="agents-shipgate verify --workspace . --config shipgate.yaml --ci-mode advisory --json",
                )
            )
    return signals


def least_privilege_signals(
    requests: list[CapabilityRequestV1],
) -> list[PreflightSignalV1]:
    signals: list[PreflightSignalV1] = []
    for request in requests:
        broad = [scope for scope in request.scopes if _is_broad_scope(scope)]
        if not broad:
            continue
        subject = _capability_subject(request)
        signals.append(
            PreflightSignalV1(
                id=f"least_privilege:{subject}:broad_scope",
                kind="least_privilege",
                severity="high",
                actor="human",
                subject=subject,
                path=None,
                reason=(
                    "Capability request includes broad scope(s): "
                    + ", ".join(sorted(set(broad)))
                ),
                recommendation=(
                    "Replace broad scopes with operation-specific scopes or route "
                    "the expansion to a human reviewer."
                ),
                related_command="agents-shipgate preflight --workspace . --plan - --json",
            )
        )
    return signals


def signals_for_host_permission_requests(
    requests: list[HostPermissionRequestV1],
) -> list[PreflightSignalV1]:
    signals: list[PreflightSignalV1] = []
    for request in requests:
        text = _host_request_text(request)
        subject = request.subject
        common = {
            "actor": "human",
            "subject": subject,
            "path": request.path,
            "related_command": "agents-shipgate preflight --workspace . --plan - --json",
        }
        if _host_request_has_wildcard_allow(text):
            signals.append(
                PreflightSignalV1(
                    id=f"host_permission:{subject}:wildcard_allow",
                    kind="least_privilege",
                    severity="critical",
                    reason="Host permission request grants a wildcard-shaped allow rule.",
                    recommendation="Replace wildcard host access with specific tool or command rules.",
                    **common,
                )
            )
        if _host_request_auto_approves_write(text):
            signals.append(
                PreflightSignalV1(
                    id=f"host_permission:{subject}:auto_approve_write",
                    kind="least_privilege",
                    severity="critical",
                    reason="Host permission request auto-approves write or destructive tools.",
                    recommendation="Require prompting or human review for write/destructive MCP tools.",
                    **common,
                )
            )
        if _host_request_expands_runtime_boundary(text):
            signals.append(
                PreflightSignalV1(
                    id=f"host_permission:{subject}:runtime_boundary",
                    kind="least_privilege",
                    severity="critical",
                    reason="Host permission request expands sandbox, network, workflow, or hook authority.",
                    recommendation="Have a human approve full sandbox/network, write-all, pull_request_target, hooks, or new MCP servers before use.",
                    **common,
                )
            )
        if not any(signal.id.startswith(f"host_permission:{subject}:") for signal in signals):
            signals.append(
                PreflightSignalV1(
                    id=f"host_permission:{subject}:review",
                    kind="least_privilege",
                    severity="high",
                    reason="Host permission request changes coding-agent authority.",
                    recommendation="Have a human review host permission changes before the agent relies on them.",
                    **common,
                )
            )
    return signals


def signals_for_policy_drift(
    policy_drift: PreflightDriftSummary | None,
    trust_root_graph_diff: PreflightDriftSummary | None,
) -> list[PreflightSignalV1]:
    signals: list[PreflightSignalV1] = []
    if policy_drift is not None and policy_drift.changed:
        signals.append(
            PreflightSignalV1(
                id="policy_drift:effective_policy",
                kind="policy_drift",
                severity="high",
                actor="human",
                subject="effective_policy",
                path="shipgate.yaml",
                reason="Effective release policy hash differs from the supplied base preflight.",
                recommendation="Have a human review the policy change; preflight cannot prove it is a safe strengthening.",
                related_command="agents-shipgate verify --workspace . --config shipgate.yaml --ci-mode advisory --json",
            )
        )
    if trust_root_graph_diff is not None and trust_root_graph_diff.changed:
        signals.append(
            PreflightSignalV1(
                id="policy_drift:trust_root_graph",
                kind="policy_drift",
                severity="high",
                actor="human",
                subject="trust_root_graph",
                path=None,
                reason="Trust-root graph differs from the supplied base preflight.",
                recommendation="Have a human review added, removed, or modified trust roots before relying on this change.",
                related_command="agents-shipgate verify --workspace . --config shipgate.yaml --ci-mode advisory --json",
            )
        )
    return signals


def signals_for_host_grant_drift(
    host_grant_drift: dict[str, Any] | None,
) -> list[PreflightSignalV1]:
    if not host_grant_drift or not host_grant_drift.get("has_drift"):
        return []
    reason = "Host grants differ from the acknowledged baseline."
    expansion = host_grant_drift.get("expansion_signals") or []
    if expansion:
        reason += " Expansion signals: " + ", ".join(str(item) for item in expansion[:5])
    return [
        PreflightSignalV1(
            id="host_grant_drift:baseline",
            kind="host_grant_drift",
            severity="high",
            actor="human",
            subject="host_grants",
            path=str(host_grant_drift.get("baseline_file") or ""),
            reason=reason,
            recommendation=(
                "Route the host-grant drift to a human. After review, "
                "re-acknowledge with `agents-shipgate audit --host --save-baseline`."
            ),
            related_command="agents-shipgate audit --host --drift --fail-on-drift",
        )
    ]


def effective_policy_hash_for_config(config_path: Path) -> str | None:
    policy_hash, _notes = _policy_hash_for_config(config_path)
    return policy_hash


def _scope_type_for_kind(kind: str) -> ProtectedSurfaceScopeType:
    if kind in _FORBIDDEN_EDIT_CLASSES or kind in _CODEX_WHOLE_FILE_CLASSES:
        return "whole_file"
    if kind in _KEY_LEVEL_CLASSES:
        return "key_level"
    if kind in _CAPABILITY_SURFACE_CLASSES:
        return "capability_surface"
    return "key_level"


def _spec_by_key() -> dict[tuple[str, str], ProtectedSurfaceSpec]:
    return {(spec.kind, spec.pattern): spec for spec in protected_surface_specs()}


def _classify(path: str) -> ProtectedSurfaceSpec | None:
    for spec in protected_surface_specs():
        if glob_match(spec.pattern, path):
            return spec
    return None


def _normalize_changed_files(paths: list[str]) -> list[str]:
    return sorted({path.replace("\\", "/").strip() for path in paths if path.strip()})


def _walk_trust_root_files(root: Path) -> tuple[str, ...]:
    """Return workspace files considered for trust-root graph presence.

    The trust-root graph must use the same glob semantics as touch
    classification. ``Path.glob("**")`` has Python-version-dependent trailing
    globstar behavior, so walk files once and classify with ``glob_match``.
    """

    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if dirname not in _TRUST_ROOT_WALK_SKIP_DIRS
        ]
        dirnames.sort()
        root_path = Path(dirpath)
        for filename in sorted(filenames):
            path = root_path / filename
            try:
                if not path.is_file():
                    continue
                rel = path.relative_to(root).as_posix()
            except OSError:
                continue
            except ValueError:
                continue
            out.append(rel)
    return tuple(out)


def _present_paths(candidate_paths: tuple[str, ...], pattern: str) -> list[str]:
    return [path for path in candidate_paths if glob_match(pattern, path)]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _stable_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _policy_hash_for_config(config_path: Path) -> tuple[str | None, list[str]]:
    if not config_path.is_file():
        return None, [f"No manifest found at {config_path}; policy snapshot unavailable."]
    try:
        manifest = load_manifest(config_path)
    except (ConfigError, InputParseError):
        raise
    except Exception as exc:  # noqa: BLE001 - normalize loader boundary.
        raise ConfigError(f"Could not load manifest for preflight: {exc}") from exc
    policy = build_effective_policy_snapshot(manifest)
    return _stable_hash(policy.model_dump(mode="json")), []


def _node_id(kind: str, pattern: str) -> str:
    return "tr_" + hashlib.sha256(f"{kind}|{pattern}".encode()).hexdigest()[:16]


def _coerce_capability_request(
    value: CapabilityRequestV1 | dict[str, Any] | None,
) -> CapabilityRequestV1 | None:
    if value is None or isinstance(value, CapabilityRequestV1):
        return value
    try:
        return CapabilityRequestV1.model_validate(value)
    except ValidationError as exc:
        raise ConfigError(f"Invalid capability request: {exc}") from exc


def _coerce_plan(
    value: PreflightPlanV1 | dict[str, Any] | None,
) -> PreflightPlanV1 | None:
    if value is None or isinstance(value, PreflightPlanV1):
        return value
    try:
        return PreflightPlanV1.model_validate(value)
    except ValidationError as exc:
        raise ConfigError(f"Invalid preflight plan: {exc}") from exc


def _changed_files_from_diff_text(diff_text: str) -> list[str]:
    return sorted({item.path for item in parse_unified_diff(diff_text) if item.path})


def _coerce_capability_requests(
    *,
    capability_request: CapabilityRequestV1 | dict[str, Any] | None,
    capability_requests: list[CapabilityRequestV1 | dict[str, Any]] | None,
    plan: PreflightPlanV1 | None,
) -> list[CapabilityRequestV1]:
    out: list[CapabilityRequestV1] = []
    single = _coerce_capability_request(capability_request)
    if single is not None:
        out.append(single)
    for raw in capability_requests or []:
        request = _coerce_capability_request(raw)
        if request is not None:
            out.append(request)
    if plan is not None:
        out.extend(plan.capability_requests)
    return out


def _coerce_host_permission_requests(
    *,
    host_permission_requests: list[HostPermissionRequestV1 | dict[str, Any]] | None,
    plan: PreflightPlanV1 | None,
) -> list[HostPermissionRequestV1]:
    out: list[HostPermissionRequestV1] = []
    for raw in host_permission_requests or []:
        if isinstance(raw, HostPermissionRequestV1):
            out.append(raw)
            continue
        try:
            out.append(HostPermissionRequestV1.model_validate(raw))
        except ValidationError as exc:
            raise ConfigError(f"Invalid host permission request: {exc}") from exc
    if plan is not None:
        out.extend(plan.host_permission_requests)
    return out


def _coerce_base_preflight(
    value: PreflightResultV1 | PreflightResultV2 | dict[str, Any] | None,
) -> PreflightResultV1 | PreflightResultV2 | None:
    if value is None or isinstance(value, (PreflightResultV1, PreflightResultV2)):
        return value
    try:
        if value.get("preflight_schema_version") == "0.2":
            return PreflightResultV2.model_validate(value)
        return PreflightResultV1.model_validate(value)
    except ValidationError as exc:
        raise ConfigError(f"Invalid base preflight result: {exc}") from exc


def _host_grant_drift_payload(
    *,
    workspace: Path,
    baseline: Path | None,
) -> tuple[dict[str, Any] | None, str | None]:
    explicit_baseline = baseline is not None
    if baseline is None:
        baseline_path = workspace / DEFAULT_BASELINE_FILE
        baseline_display = DEFAULT_BASELINE_FILE.as_posix()
        if not baseline_path.is_file():
            return None, None
    else:
        baseline_path = baseline if baseline.is_absolute() else workspace / baseline
        baseline_display = str(baseline)
    try:
        baseline_payload = load_host_grants_baseline(baseline_path)
    except ValueError as exc:
        if not explicit_baseline:
            return (
                None,
                f"Host-grants baseline {baseline_display} could not be loaded; "
                f"host-grant drift skipped: {exc}",
            )
        raise ConfigError(str(exc)) from exc
    return (
        build_host_drift_payload(
            baseline=baseline_payload,
            inventory=host_audit_inventory(workspace),
            baseline_file=baseline_display,
        ),
        None,
    )


def _capability_subject(request: CapabilityRequestV1) -> str:
    parts = [part for part in (request.provider, request.tool_name, request.operation) if part]
    return ".".join(parts) if parts else request.tool_name


def _is_broad_scope(scope: str) -> bool:
    normalized = scope.strip().strip("\"'").lower()
    if normalized in _BROAD_SCOPE_LITERALS:
        return True
    if normalized.endswith(":*") or normalized.endswith("/*"):
        return True
    if normalized in {"write-all", "read-all"}:
        return True
    if normalized.startswith(("admin:", "root:", "superuser:")):
        return True
    return False


def _host_request_text(request: HostPermissionRequestV1) -> str:
    payload = {
        "host": request.host,
        "surface": request.surface,
        "operation": request.operation,
        "path": request.path,
        "subject": request.subject,
        "requested_access": request.requested_access,
        "reason": request.reason,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).lower()


def _host_request_has_wildcard_allow(text: str) -> bool:
    return ("allow" in text or "approve" in text) and "*" in text


def _host_request_auto_approves_write(text: str) -> bool:
    approval = any(
        token in text
        for token in (
            "auto_approve",
            "auto-approve",
            "autoapproved",
            "auto approved",
            "always_allow",
            "always-allow",
        )
    )
    write = any(token in text for token in _HOST_WRITE_TOKENS)
    return approval and write


def _host_request_expands_runtime_boundary(text: str) -> bool:
    runtime_tokens = (
        "danger-full-access",
        "full network",
        "network_access",
        "network access",
        "network:true",
        "sandbox disabled",
        "sandbox\":\"disabled",
        "write-all",
        "pull_request_target",
        "new hook",
        "\"hooks\"",
        "pretooluse",
        "posttooluse",
        "stop hook",
        "mcp server",
        "mcp_server",
    )
    return any(token in text for token in runtime_tokens)


def _sorted_signals(signals: list[PreflightSignalV1]) -> list[PreflightSignalV1]:
    return sorted(
        signals,
        key=lambda item: (
            _SIGNAL_KIND_RANK.get(item.kind, 99),
            _SEVERITY_RANK.get(item.severity, 99),
            item.path or "",
            item.subject,
            item.id,
        ),
    )


def _verify_required_signal() -> PreflightSignalV1:
    return PreflightSignalV1(
        id="verify_required:diff",
        kind="verify_required",
        severity="info",
        actor="coding_agent",
        subject="release_verification",
        path=None,
        reason="The plan includes files, capability requests, or host permission requests that require deterministic verification before completion.",
        recommendation="Run the verifier and read verifier.json plus report.json.release_decision.decision before reporting the work complete.",
        related_command=_VERIFY_COMMAND,
    )


def _plan_summary(
    *,
    changed: list[str],
    capability_requests: list[CapabilityRequestV1],
    host_permission_requests: list[HostPermissionRequestV1],
    signals: list[PreflightSignalV1],
) -> dict[str, Any]:
    severity_counts = {severity: 0 for severity in _SEVERITY_RANK}
    for signal in signals:
        severity_counts[signal.severity] = severity_counts.get(signal.severity, 0) + 1
    return {
        "changed_files_count": len(changed),
        "capability_request_count": len(capability_requests),
        "host_permission_request_count": len(host_permission_requests),
        "signal_count": len(signals),
        "signal_severity_counts": severity_counts,
        "human_signal_count": sum(1 for signal in signals if signal.actor == "human"),
    }


def _is_high_risk_request(request: CapabilityRequestV1) -> bool:
    return request.effect in _HIGH_RISK_EFFECTS or bool(
        set(request.risk_tags) & _HIGH_RISK_TAGS
    )


def _needs_idempotency(request: CapabilityRequestV1) -> bool:
    return request.effect in {"financial_write", "destructive"} or bool(
        set(request.risk_tags)
        & {"financial_action", "financial_write", "destructive", "external_write"}
    )


def _require(
    *,
    id_: str,
    field: str,
    satisfied: bool,
    severity: str,
    reason: str,
    recommendation: str,
) -> PreflightRequiredEvidence:
    return PreflightRequiredEvidence(
        id=id_,
        field=field,
        satisfied=satisfied,
        severity=severity,  # type: ignore[arg-type]
        reason=reason,
        recommendation=recommendation,
    )


def _hash_drift(
    *,
    base_hash: str | None,
    head_hash: str | None,
) -> PreflightDriftSummary:
    return PreflightDriftSummary(
        changed=base_hash != head_hash,
        base_hash=base_hash,
        head_hash=head_hash,
    )


def _graph_drift(
    base: TrustRootGraphV1,
    head: TrustRootGraphV1,
) -> PreflightDriftSummary:
    base_nodes = {node.id: node for node in base.nodes}
    head_nodes = {node.id: node for node in head.nodes}
    added = sorted(set(head_nodes) - set(base_nodes))
    removed = sorted(set(base_nodes) - set(head_nodes))
    modified = sorted(
        node_id
        for node_id in set(base_nodes) & set(head_nodes)
        if base_nodes[node_id].model_dump(mode="json")
        != head_nodes[node_id].model_dump(mode="json")
    )
    return PreflightDriftSummary(
        changed=bool(added or removed or modified),
        base_hash=base.graph_hash,
        head_hash=head.graph_hash,
        added=added,
        removed=removed,
        modified=modified,
    )


def _first_next_action(
    *,
    signals: list[PreflightSignalV1],
) -> PreflightNextAction:
    human_signals = [signal for signal in signals if signal.actor == "human"]
    if human_signals:
        first = human_signals[0]
        kind = "gather_evidence" if first.kind == "missing_evidence" else "review"
        return PreflightNextAction(
            actor="human",
            kind=kind,
            command=None,
            why=f"{first.reason} A coding agent must stop and route this to a human.",
        )
    verify_signal = next(
        (signal for signal in signals if signal.kind == "verify_required"),
        None,
    )
    if verify_signal is not None:
        return PreflightNextAction(
            actor="coding_agent",
            kind="verify",
            command=_VERIFY_COMMAND,
            why=verify_signal.reason,
        )
    return PreflightNextAction(
        actor="coding_agent",
        kind="continue",
        command=None,
        why="No requested protected-surface touch, host drift, or evidence gap was found by preflight.",
    )


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


__all__ = [
    "build_preflight_result",
    "build_trust_root_graph",
    "classify_protected_touches",
    "effective_policy_hash_for_config",
    "forbidden_file_edits",
    "protected_surface_specs",
    "required_evidence_for_capability_request",
    "required_evidence_for_capability_requests",
    "signals_for_capability_requests",
    "signals_for_host_grant_drift",
    "signals_for_host_permission_requests",
    "signals_for_policy_drift",
    "signals_for_protected_touches",
    "least_privilege_signals",
]
