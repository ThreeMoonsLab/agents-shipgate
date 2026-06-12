from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents_shipgate.checks.verify import TRUST_ROOT_SURFACES
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.agent_controls import FORBIDDEN_SHORTCUTS
from agents_shipgate.core.errors import ConfigError, InputParseError
from agents_shipgate.core.globbing import glob_match
from agents_shipgate.core.lenses.effective_policy import (
    build_effective_policy_snapshot,
)
from agents_shipgate.schemas.preflight import (
    CapabilityRequestV1,
    PreflightDriftSummary,
    PreflightNextAction,
    PreflightProtectedSurface,
    PreflightProtectedSurfaceTouch,
    PreflightRequiredEvidence,
    PreflightResultV1,
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
    base_preflight: PreflightResultV1 | dict[str, Any] | None = None,
) -> PreflightResultV1:
    root = workspace.resolve()
    config_path = config if config.is_absolute() else root / config
    config_path = config_path.resolve()
    changed = _normalize_changed_files(changed_files or [])
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
    request = _coerce_capability_request(capability_request)
    required_evidence = (
        required_evidence_for_capability_request(request) if request is not None else []
    )
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

    return PreflightResultV1(
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
        first_next_action=_first_next_action(
            touches=touches,
            required_evidence=required_evidence,
        ),
        notes=notes,
    )


def build_trust_root_graph(workspace: Path) -> TrustRootGraphV1:
    root = workspace.resolve()
    nodes: list[TrustRootNodeV1] = []
    for spec in sorted(protected_surface_specs(), key=lambda item: (item.kind, item.pattern)):
        present_paths = _present_paths(root, spec.pattern)
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
    return sorted(out, key=lambda item: (item.severity, item.id))


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


def _present_paths(root: Path, pattern: str) -> list[str]:
    try:
        matches = root.glob(pattern)
    except ValueError:
        return []
    out: list[str] = []
    for path in matches:
        if path.is_dir():
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        out.append(rel)
    return sorted(set(out))


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


def _coerce_base_preflight(
    value: PreflightResultV1 | dict[str, Any] | None,
) -> PreflightResultV1 | None:
    if value is None or isinstance(value, PreflightResultV1):
        return value
    try:
        return PreflightResultV1.model_validate(value)
    except ValidationError as exc:
        raise ConfigError(f"Invalid base preflight result: {exc}") from exc


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
    touches: list[PreflightProtectedSurfaceTouch],
    required_evidence: list[PreflightRequiredEvidence],
) -> PreflightNextAction:
    if touches:
        first = touches[0]
        return PreflightNextAction(
            actor="human",
            kind="review",
            command=None,
            why=(
                f"{first.path} matches protected surface {first.pattern}; a "
                "coding agent must stop for human review before editing or "
                "claiming this trust-root change is safe."
            ),
        )
    missing = [
        item
        for item in required_evidence
        if not item.satisfied and item.severity in {"high", "critical"}
    ]
    if missing:
        first = missing[0]
        return PreflightNextAction(
            actor="human",
            kind="gather_evidence",
            command=None,
            why=(
                f"Capability request is missing {first.field}: {first.reason} "
                "A coding agent must not invent this evidence."
            ),
        )
    return PreflightNextAction(
        actor="coding_agent",
        kind="continue",
        command=None,
        why=(
            "No requested protected-surface touch or high-risk evidence gap "
            "was found by preflight. Run verify before reporting completion."
        ),
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
]
