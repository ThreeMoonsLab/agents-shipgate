from __future__ import annotations

import hashlib
import json
import os
import posixpath
import shlex
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError

from agents_shipgate.checks.verify import TRUST_ROOT_SURFACES
from agents_shipgate.config.loader import load_manifest_text
from agents_shipgate.core.agent_control import derive_agent_control
from agents_shipgate.core.agent_controls import (
    FORBIDDEN_SHORTCUTS,
)
from agents_shipgate.core.agent_controls import (
    verify_command_for as _shared_verify_command_for,
)
from agents_shipgate.core.boundary_diff import parse_unified_diff
from agents_shipgate.core.errors import ConfigError, InputParseError
from agents_shipgate.core.globbing import glob_match_ci
from agents_shipgate.core.host_grants import (
    DEFAULT_BASELINE_FILE,
    INCOMPARABLE_BASELINE_REVIEW,
    build_host_drift_payload,
    host_audit_inventory,
    load_host_grants_baseline,
)
from agents_shipgate.core.lenses.effective_policy import (
    build_effective_policy_snapshot,
)
from agents_shipgate.core.manifest_proposals import (
    assess_coverage_increasing_tool_source_proposal,
)
from agents_shipgate.core.trust_roots import (
    IdentityBoundReadSession,
    IdentityReadBudget,
    IdentityReadBudgetExceeded,
    inspect_lexical_path_identity,
    is_configured_manifest,
    is_portable_repo_path,
    read_identity_bound_text,
)
from agents_shipgate.schemas.agent_control import (
    CodingAgentCommandAction,
    HumanControlAction,
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
    PreflightResultV3,
    PreflightSignalV1,
    ProtectedSurfaceScopeType,
    TrustRootGraphV1,
    TrustRootNodeV1,
)
from agents_shipgate.schemas.surfaces import ActionEffect

_FORBIDDEN_EDIT_CLASSES = frozenset(
    {"ci_gate", "agent_instructions", "policy", "host_boundary"}
)
_KEY_LEVEL_CLASSES = frozenset({"manifest", "shipgate_state"})
_CAPABILITY_SURFACE_CLASSES = frozenset({"codex_plugin", "tool_surface_decl", "prompts"})
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
        "external_communication",
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
        "node_modules",
        "site-packages",
        "venv",
    }
)
_MAX_TRUST_ROOT_INVENTORY_ENTRIES = 100_000
_MAX_TRUST_ROOT_GRAPH_ENTRIES = 200_000
_MAX_TRUST_ROOT_GRAPH_BYTES = 64 * 1024 * 1024
_MAX_TRUST_ROOT_FILE_BYTES = 16 * 1024 * 1024
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_VERIFY_COMMAND = (
    "agents-shipgate verify --workspace . --config shipgate.yaml --ci-mode advisory --json"
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


def _verify_command(workspace: Path | None, config: Path | None) -> str:
    """The verify invocation for *this* preflight's target."""

    if workspace is None or config is None:
        return _VERIFY_COMMAND
    return _shared_verify_command_for(workspace, config, extra=("--ci-mode", "advisory"))


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
        if spec.kind in _FORBIDDEN_EDIT_CLASSES or spec.kind in _CODEX_WHOLE_FILE_CLASSES
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
    diff_text: str | None = None,
    base_preflight: (
        PreflightResultV1 | PreflightResultV2 | PreflightResultV3 | dict[str, Any] | None
    ) = None,
    host_baseline: Path | None = None,
) -> PreflightResultV3:
    root = workspace.resolve()
    config_path = _lexical_config_path(
        root,
        config,
        requested_workspace=workspace,
    )
    request_plan = _coerce_plan(plan)
    _reject_mixed_plan_inputs(
        plan=request_plan,
        changed_files=changed_files,
        capability_request=capability_request,
        capability_requests=capability_requests,
        host_permission_requests=host_permission_requests,
        diff_text=diff_text,
        base_preflight=base_preflight,
    )
    effective_diff_text = request_plan.diff_text if request_plan is not None else diff_text
    changed_inputs = list(changed_files or [])
    if request_plan is not None:
        changed_inputs.extend(request_plan.changed_files)
    if effective_diff_text:
        changed_inputs.extend(_changed_files_from_diff_text(effective_diff_text))
    changed, path_identity_touches = _normalize_planned_paths(root, changed_inputs)
    graph, graph_identity_touches = _build_trust_root_graph(
        root,
        config_path=config_path,
    )
    policy_hash, notes = _policy_hash_for_config(config_path, root=root)

    surfaces = [
        PreflightProtectedSurface(
            kind=node.kind,
            pattern=node.pattern,
            scope_type=node.scope_type,
            present=bool(node.present_paths),
            present_paths=node.present_paths,
            description=(
                _spec_by_key().get((node.kind, node.pattern)).description
                if (node.kind, node.pattern) in _spec_by_key()
                else "The exact configured Agents Shipgate manifest."
            ),
        )
        for node in graph.nodes
    ]
    verify_command = _verify_command(workspace, config)
    identity_paths = {touch.path for touch in path_identity_touches}
    classified_touches = classify_protected_touches(changed, config_path, root)
    touches = [
        *path_identity_touches,
        *(
            touch
            for touch in graph_identity_touches
            if touch.path not in identity_paths
        ),
        *(
            touch
            for touch in classified_touches
            if touch.path
            not in {
                *identity_paths,
                *(item.path for item in graph_identity_touches),
            }
        ),
    ]
    touches = _classify_proposal_safe_manifest_touch(
        workspace=root,
        config_path=config_path,
        touches=touches,
        diff_text=effective_diff_text,
    )
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
    requires_human_review = any(touch.requires_human_review for touch in touches) or any(
        not item.satisfied and item.severity in {"high", "critical"} for item in required_evidence
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
            *signals_for_host_grant_drift(
                host_grant_drift,
                workspace=root,
                baseline=host_baseline,
            ),
            *signals_for_capability_requests(requests, verify_command),
            *least_privilege_signals(requests),
            *signals_for_host_permission_requests(host_requests),
            *signals_for_policy_drift(
                policy_drift,
                trust_root_graph_diff,
                verify_command,
                manifest_path=_display_path(config_path, root),
            ),
        ]
    )
    requires_human_review = requires_human_review or any(
        signal.actor == "human" for signal in signals
    )
    requires_verify = bool(changed or requests or host_requests)
    if requires_verify and not any(signal.kind == "verify_required" for signal in signals):
        signals = _sorted_signals([*signals, _verify_required_signal(verify_command)])

    first_next_action = _first_next_action(signals=signals, verify_command=verify_command)
    allowed_next_commands = (
        [verify_command]
        if first_next_action.actor == "coding_agent" and first_next_action.kind == "verify"
        else []
    )
    control = _derive_preflight_control(
        first_next_action=first_next_action,
        requires_human_review=requires_human_review,
        requires_verify=requires_verify,
        allowed_next_commands=allowed_next_commands,
    )

    return PreflightResultV3(
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
        verification_command=verify_command if requires_verify else None,
        allowed_next_commands=allowed_next_commands,
        plan_summary=_plan_summary(
            changed=changed,
            capability_requests=requests,
            host_permission_requests=host_requests,
            signals=signals,
        ),
        host_grant_drift=host_grant_drift,
        control=control,
    )


def _lexical_config_path(
    root: Path,
    config: Path,
    *,
    requested_workspace: Path | None = None,
) -> Path:
    """Return the configured manifest identity without following aliases."""

    candidate = config if config.is_absolute() else root / config
    if config.is_absolute() and requested_workspace is not None:
        lexical_workspace = Path(
            os.path.abspath(os.path.normpath(os.fspath(requested_workspace)))
        )
        lexical_config = Path(os.path.normpath(os.fspath(config)))
        try:
            requested_tail = lexical_config.relative_to(lexical_workspace)
        except ValueError:
            pass
        else:
            # Preserve an external workspace alias while keeping every
            # repository-relative component lexical for symlink inspection.
            candidate = root / requested_tail
    lexical = Path(os.path.normpath(os.fspath(candidate)))
    try:
        relative = lexical.relative_to(root)
    except ValueError as exc:
        raise ConfigError(f"--config must be inside --workspace: {config}") from exc

    issue = inspect_lexical_path_identity(root, relative)
    if issue is None:
        try:
            metadata = lexical.lstat()
        except FileNotFoundError:
            return lexical
        except OSError as exc:
            raise ConfigError(
                f"--config could not be inspected safely: {config}"
            ) from exc
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ConfigError(
                "--config must identify one singly-linked regular file; "
                f"non-regular or hardlinked manifest refused: {config}"
            )
        return lexical
    requested = _display_path(issue.requested, root)
    if issue.kind == "symlink":
        raise ConfigError(
            f"--config must not contain symlink components: {requested}"
        )
    if issue.kind == "alias":
        actual = (
            _display_path(issue.actual, root)
            if issue.actual is not None
            else "a differently spelled filesystem entry"
        )
        raise ConfigError(
            "--config must use the exact filesystem spelling: "
            f"{requested} resolves to {actual}"
        )
    raise ConfigError(
        f"--config could not be inspected safely: {requested}: {issue.detail}"
    )


def _reject_mixed_plan_inputs(
    *,
    plan: PreflightPlanV1 | None,
    changed_files: list[str] | None,
    capability_request: CapabilityRequestV1 | dict[str, Any] | None,
    capability_requests: list[CapabilityRequestV1 | dict[str, Any]] | None,
    host_permission_requests: list[HostPermissionRequestV1 | dict[str, Any]] | None,
    diff_text: str | None,
    base_preflight: (
        PreflightResultV1 | PreflightResultV2 | PreflightResultV3 | dict[str, Any] | None
    ),
) -> None:
    """Keep plan and direct-input request shapes mutually exclusive."""

    if plan is None:
        return
    mixed = [
        name
        for name, value in (
            ("changed_files", changed_files),
            ("diff_text", diff_text),
            ("capability_request", capability_request),
            ("capability_requests", capability_requests),
            ("host_permission_requests", host_permission_requests),
            ("base_preflight", base_preflight),
        )
        if value is not None
    ]
    if mixed:
        raise ConfigError(
            "plan cannot be combined with "
            + ", ".join(mixed)
            + "; put those inputs in the plan object or omit plan."
        )


def build_trust_root_graph(workspace: Path) -> TrustRootGraphV1:
    root = workspace.resolve()
    graph, _unsafe = _build_trust_root_graph(root)
    return graph


def _build_trust_root_graph(
    root: Path,
    *,
    config_path: Path | None = None,
) -> tuple[TrustRootGraphV1, list[PreflightProtectedSurfaceTouch]]:
    budget = IdentityReadBudget(
        max_entries=_MAX_TRUST_ROOT_GRAPH_ENTRIES,
        max_total_bytes=_MAX_TRUST_ROOT_GRAPH_BYTES,
    )
    reader = IdentityBoundReadSession(root, budget=budget)
    candidate_paths, _inventory_entries = _walk_trust_root_files(
        root,
        reader=reader,
        max_entries=_MAX_TRUST_ROOT_INVENTORY_ENTRIES,
    )
    nodes: list[TrustRootNodeV1] = []
    unsafe: dict[str, PreflightProtectedSurfaceTouch] = {}
    hash_cache: dict[str, str] = {}

    def file_hashes_for(paths: list[str]) -> dict[str, str]:
        file_hashes: dict[str, str] = {}
        for path in paths:
            if path not in hash_cache:
                try:
                    hash_cache[path] = _file_sha256(reader, Path(path))
                except IdentityReadBudgetExceeded as exc:
                    raise ConfigError(
                        "Trust-root graph exceeded its aggregate static "
                        "resource bound."
                    ) from exc
                except (OSError, ValueError, NotImplementedError):
                    hash_cache[path] = "unavailable:path_identity"
                    unsafe[path] = PreflightProtectedSurfaceTouch(
                        path=path,
                        kind="path_identity",
                        pattern="exact-non-symlink-single-link-workspace-path",
                        scope_type="whole_file",
                    )
            file_hashes[path] = hash_cache[path]
        return file_hashes

    specs = list(protected_surface_specs())
    configured_relative = ""
    if config_path is not None:
        try:
            configured_relative = config_path.relative_to(root).as_posix()
        except ValueError:
            configured_relative = ""
    configured_is_catalogued = bool(configured_relative) and any(
        spec.kind == "manifest"
        and (
            glob_match_ci(spec.pattern, configured_relative)
        )
        for spec in specs
    )
    for spec in sorted(specs, key=lambda item: (item.kind, item.pattern)):
        present_paths = _present_paths(root, candidate_paths, spec.pattern)
        nodes.append(
            TrustRootNodeV1(
                id=_node_id(spec.kind, spec.pattern),
                kind=spec.kind,
                pattern=spec.pattern,
                scope_type=spec.scope_type,
                present_paths=present_paths,
                file_hashes=file_hashes_for(present_paths),
            )
        )
    if configured_relative and not configured_is_catalogued:
        # The configured manifest is an exact identity, not a glob. Legal Git
        # filenames may themselves contain ``*``, ``?``, ``[]``, or ``\``.
        present_paths = (
            [configured_relative] if configured_relative in candidate_paths else []
        )
        nodes.append(
            TrustRootNodeV1(
                id=_node_id("manifest", configured_relative),
                kind="manifest",
                pattern=configured_relative,
                scope_type=_scope_type_for_kind("manifest"),
                present_paths=present_paths,
                file_hashes=file_hashes_for(present_paths),
            )
        )
        nodes.sort(key=lambda item: (item.kind, item.pattern))
    try:
        reader.finish()
    except IdentityReadBudgetExceeded as exc:
        raise ConfigError(
            "Trust-root graph exceeded its aggregate static resource bound."
        ) from exc
    except (OSError, ValueError, NotImplementedError) as exc:
        raise ConfigError(
            "Trust-root graph changed identity while its bounded snapshot "
            "was captured."
        ) from exc
    graph_hash = _stable_hash([node.model_dump(mode="json") for node in nodes])
    return (
        TrustRootGraphV1(nodes=nodes, graph_hash=graph_hash),
        sorted(unsafe.values(), key=lambda item: item.path),
    )


def classify_protected_touches(
    changed_files: list[str],
    config_path: Path | None = None,
    workspace: Path | None = None,
) -> list[PreflightProtectedSurfaceTouch]:
    """Classify changed paths against the protected-surface catalog.

    ``config_path`` covers the manifest this run was pointed at. The catalog
    only patterns ``**/shipgate.yaml``, so a repository run with
    ``--config new-gate.yml`` reported no protected touch and
    ``requires_human_review=false`` for a diff that rewrote its own gate.
    """

    touches: list[PreflightProtectedSurfaceTouch] = []
    seen: set[str] = set()
    for raw in changed_files:
        path = raw.replace("\\", "/")
        if not path or path in seen:
            continue
        seen.add(path)
        spec = _classify(path) or _configured_manifest_spec(
            config_path, path, workspace
        )
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
    """Project protected-surface touches into routed signals."""

    return [
        PreflightSignalV1(
            id=f"protected_surface:{touch.path}",
            kind="protected_surface_touch",
            severity="critical" if touch.scope_type == "whole_file" else "high",
            actor="human" if touch.requires_human_review else "coding_agent",
            subject=touch.kind,
            path=touch.path,
            reason=_protected_touch_reason(touch),
            recommendation=_protected_touch_recommendation(touch),
            related_command=None,
        )
        for touch in touches
    ]


def _protected_touch_reason(touch: PreflightProtectedSurfaceTouch) -> str:
    if not touch.requires_human_review:
        return (
            f"{touch.path} contains only an exact append-only proposal for "
            "built-in tool-source coverage; proposal authorship is allowed, "
            "but the concrete protected-surface diff is not approved."
        )
    return (
        f"{touch.path} matches protected surface {touch.pattern}; "
        "a coding agent must not self-approve trust-root edits."
    )


def _protected_touch_recommendation(touch: PreflightProtectedSurfaceTouch) -> str:
    """State the route, and the non-route that reads like one.

    A stop that only says "a human must decide" leaves the coding agent to
    guess how a human decides. The plausible guess is to ask the operator in
    conversation, which preflight does not read: the agent then either stalls
    on an answer nothing consumes, or treats a spoken "yes" as authority and
    proceeds past the gate. Naming the pull request as the route, and chat as
    not a route, closes both.
    """

    if not touch.requires_human_review:
        return (
            "Apply only the exact planned tool-source addition, then run the "
            "required verifier and route its concrete review evidence to a human."
        )
    return (
        "Route this protected-surface edit to a human reviewer through the pull "
        "request. Conversational confirmation does not clear this signal, so do "
        "not ask the operator to approve the edit in chat."
    )


def _classify_proposal_safe_manifest_touch(
    *,
    workspace: Path,
    config_path: Path,
    touches: list[PreflightProtectedSurfaceTouch],
    diff_text: str | None,
) -> list[PreflightProtectedSurfaceTouch]:
    """Mark one exact coverage-increasing manifest proposal as authoring-safe.

    Path-only preflight remains fail-closed.  This exception requires both
    sides of a concrete diff and changes only the existing per-touch routing
    flag; it does not approve the resulting protected-surface edit.
    """

    if not diff_text:
        return touches
    config_display = _display_path(config_path, workspace)
    matching_diff_files = [
        item
        for item in parse_unified_diff(diff_text)
        if item.path.replace("\\", "/") == config_display
    ]
    # A per-record safe result must never clear the path-wide fail-closed
    # routing for another record targeting the same protected manifest.
    if len(matching_diff_files) != 1:
        return touches
    assessment = assess_coverage_increasing_tool_source_proposal(
        workspace=workspace,
        diff_file=matching_diff_files[0],
        manifest_dir=config_path.parent,
    )
    if not assessment.proposal_safe:
        return touches
    return [
        touch.model_copy(update={"requires_human_review": False})
        if touch.kind == "manifest" and touch.path == config_display
        else touch
        for touch in touches
    ]


def signals_for_capability_requests(
    requests: list[CapabilityRequestV1],
    verify_command: str = _VERIFY_COMMAND,
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
                    related_command=verify_command,
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
                    "Capability request includes broad scope(s): " + ", ".join(sorted(set(broad)))
                ),
                recommendation=(
                    "Replace broad scopes with operation-specific scopes or route "
                    "the expansion to a human reviewer."
                ),
                related_command=None,
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
            "related_command": None,
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
    verify_command: str = _VERIFY_COMMAND,
    *,
    manifest_path: str = "shipgate.yaml",
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
                path=manifest_path,
                reason="Effective release policy hash differs from the supplied base preflight.",
                recommendation="Have a human review the policy change; preflight cannot prove it is a safe strengthening.",
                related_command=verify_command,
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
                related_command=verify_command,
            )
        )
    return signals


def signals_for_host_grant_drift(
    host_grant_drift: dict[str, Any] | None,
    *,
    workspace: Path | None = None,
    baseline: Path | None = None,
) -> list[PreflightSignalV1]:
    if not _host_grant_drift_requires_review(host_grant_drift):
        return []
    assert host_grant_drift is not None
    comparable = host_grant_drift.get("comparison_status") == "comparable"
    reason = (
        "Host grants differ from the acknowledged baseline."
        if comparable
        else "Host grants could not be compared completely with the acknowledged baseline."
    )
    incomparable_reasons = host_grant_drift.get("incomparable_reasons") or []
    if incomparable_reasons:
        reason += " Reasons: " + ", ".join(
            str(item) for item in incomparable_reasons[:5]
        )
    expansion = host_grant_drift.get("expansion_signals") or []
    if expansion:
        reason += " Expansion signals: " + ", ".join(str(item) for item in expansion[:5])
    rerun = None
    if comparable and workspace is not None:
        baseline_path = baseline or DEFAULT_BASELINE_FILE
        exact_baseline = (
            baseline_path
            if baseline_path.is_absolute()
            else workspace / baseline_path
        )
        rerun = shlex.join(
            [
                "shipgate",
                "audit",
                "--host",
                "--workspace",
                str(workspace),
                "--scope",
                "repository",
                "--drift",
                "--baseline-file",
                str(exact_baseline),
                "--fail-on-drift",
            ]
        )
    recommendation = (
        "Route the host-grant comparison to a human. After review, "
        f"run `{rerun}`."
        if rerun
        else f"Route the host-grant comparison to a human. {INCOMPARABLE_BASELINE_REVIEW}"
    )
    return [
        PreflightSignalV1(
            id="host_grant_drift:baseline",
            kind="host_grant_drift",
            severity="high",
            actor="human",
            subject="host_grants",
            path=str(host_grant_drift.get("baseline_file") or ""),
            reason=reason,
            recommendation=recommendation,
            related_command=rerun,
        )
    ]


def _host_grant_drift_requires_review(
    host_grant_drift: dict[str, Any] | None,
) -> bool:
    if not host_grant_drift:
        return False
    comparison_status = host_grant_drift.get("comparison_status")
    if comparison_status is not None and comparison_status != "comparable":
        return True
    return host_grant_drift.get("has_drift") is not False


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
        if glob_match_ci(spec.pattern, path):
            return spec
    return None


def _configured_manifest_spec(
    config_path: Path | None, path: str, workspace: Path | None = None
) -> ProtectedSurfaceSpec | None:
    """The manifest this run loaded, classified as one whatever it is called.

    ``pattern`` is the changed path rather than the resolved config path: it
    lands in the preflight artifact, and an absolute path would vary by
    checkout location.
    """

    if not is_configured_manifest(config_path, path, workspace=workspace):
        return None
    return ProtectedSurfaceSpec(
        kind="manifest",
        pattern=path,
        scope_type=_scope_type_for_kind("manifest"),
    )


def _normalize_planned_paths(
    root: Path,
    paths: list[str],
) -> tuple[list[str], list[PreflightProtectedSurfaceTouch]]:
    """Map planned paths to exact entries or route ambiguous writes to humans."""

    normalized: set[str] = set()
    unsafe: dict[str, PreflightProtectedSurfaceTouch] = {}

    def mark_unsafe(path: str) -> None:
        normalized.add(path)
        unsafe[path] = PreflightProtectedSurfaceTouch(
            path=path,
            kind="path_identity",
            pattern="exact-non-symlink-single-link-workspace-path",
            scope_type="whole_file",
        )

    for raw in paths:
        path = raw
        if not path:
            continue
        pure = PurePosixPath(path)
        if (
            not is_portable_repo_path(path)
            or pure.is_absolute()
            or ".." in pure.parts
        ):
            mark_unsafe(path)
            continue
        relative = Path(PurePosixPath(posixpath.normpath(path)).as_posix())
        converged = False
        for _attempt in range(len(relative.parts) + 1):
            issue = inspect_lexical_path_identity(root, relative)
            if issue is None:
                converged = True
                break
            if issue.kind != "alias" or issue.actual is None:
                mark_unsafe(path)
                break
            requested = root / relative
            try:
                suffix = requested.relative_to(issue.requested)
                relative = (issue.actual / suffix).relative_to(root)
            except ValueError:
                mark_unsafe(path)
                break
        if not converged:
            if path not in unsafe:
                mark_unsafe(path)
            continue

        candidate = root / relative
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            normalized.add(relative.as_posix())
            continue
        except OSError:
            mark_unsafe(path)
            continue
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            mark_unsafe(path)
            continue
        normalized.add(relative.as_posix())

    return (
        sorted(normalized),
        sorted(unsafe.values(), key=lambda item: item.path),
    )


def _walk_trust_root_files(
    root: Path,
    *,
    reader: IdentityBoundReadSession,
    max_entries: int,
) -> tuple[tuple[str, ...], int]:
    """Return workspace files considered for trust-root graph presence.

    The trust-root graph must use the same glob semantics as touch
    classification. ``Path.glob("**")`` has Python-version-dependent trailing
    globstar behavior, so walk files once and classify with ``glob_match_ci``.
    ``os.walk`` materializes a complete directory before yielding, which cannot
    enforce a bound on one hostile, very large directory; the explicit scandir
    stack stops as soon as the aggregate graph inventory budget is exhausted.
    """

    if max_entries < 0:
        raise ConfigError(
            "Trust-root graph inventory exceeded its aggregate filesystem "
            "entry bound."
        )
    out: list[str] = []
    visited = 0
    pending = [Path()]
    while pending:
        relative_directory = pending.pop()
        directory = root / relative_directory
        try:
            names = reader.directory_entries(
                relative_directory,
                max_entries=max_entries - visited,
            )
        except IdentityReadBudgetExceeded as exc:
            raise ConfigError(
                "Trust-root graph inventory exceeded its aggregate filesystem "
                "entry bound."
            ) from exc
        except (OSError, NotImplementedError, ValueError) as exc:
            raise ConfigError(
                "Trust-root graph inventory could not inspect the workspace "
                "safely."
            ) from exc
        visited += len(names)
        child_directories: list[Path] = []
        for name in names:
            path = directory / name
            try:
                relative = path.relative_to(root).as_posix()
                metadata = path.lstat()
                if stat.S_ISLNK(metadata.st_mode):
                    if name not in _TRUST_ROOT_WALK_SKIP_DIRS:
                        out.append(relative)
                    continue
                if stat.S_ISDIR(metadata.st_mode):
                    if name not in _TRUST_ROOT_WALK_SKIP_DIRS:
                        child_directories.append(Path(relative))
                    continue
            except (OSError, ValueError) as exc:
                raise ConfigError(
                    "Trust-root graph inventory could not inspect a workspace "
                    "entry safely."
                ) from exc
            out.append(relative)
        pending.extend(reversed(child_directories))
    return tuple(sorted(set(out))), visited


def _present_paths(
    root: Path,
    candidate_paths: tuple[str, ...],
    pattern: str,
) -> list[str]:
    return [
        path
        for path in candidate_paths
        if glob_match_ci(pattern, path)
        or (
            (root / path).is_symlink()
            and _symlink_may_hide_pattern(path, pattern)
        )
    ]


def _symlink_may_hide_pattern(relative: str, pattern: str) -> bool:
    """Whether a traversable symlink directory can hide a matching descendant."""

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


def _file_sha256(reader: IdentityBoundReadSession, relative: Path) -> str:
    """Hash one exact workspace file through a no-symlink descriptor chain."""

    raw = reader.read_bytes(
        relative,
        max_bytes=_MAX_TRUST_ROOT_FILE_BYTES,
    )
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _stable_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _policy_hash_for_config(
    config_path: Path,
    *,
    root: Path | None = None,
) -> tuple[str | None, list[str]]:
    identity_root = root or config_path.parent.resolve()
    try:
        relative = config_path.relative_to(identity_root)
    except ValueError as exc:
        raise ConfigError(
            f"Configured manifest is outside the policy snapshot root: {config_path}"
        ) from exc
    try:
        raw_text = read_identity_bound_text(
            identity_root,
            relative,
            max_bytes=_MAX_MANIFEST_BYTES,
        )
    except FileNotFoundError:
        return None, [f"No manifest found at {config_path}; policy snapshot unavailable."]
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ConfigError(
            f"Could not read manifest for preflight through an identity-bound "
            f"snapshot: {exc}"
        ) from exc
    try:
        manifest = load_manifest_text(raw_text, source=config_path)
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
    """Every path the diff touches, including the source side of a rename.

    Keeping only the destination hid the case that matters most: renaming the
    configured manifest to an unrecognized name left the protected path in
    ``old_path`` only, so preflight reported no protected touch,
    ``requires_human_review=false``, and an agent-action route for a diff that
    moved the gate out from under itself.
    """

    parsed = parse_unified_diff(diff_text)
    if diff_text.strip() and not parsed:
        raise ConfigError(
            "Preflight diff_text is non-empty but is not a complete unified "
            "diff with diff --git file records."
        )
    return sorted(
        {
            path
            for item in parsed
            for path in (item.new_path, item.old_path)
            if path
        }
    )


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
    value: (PreflightResultV1 | PreflightResultV2 | PreflightResultV3 | dict[str, Any] | None),
) -> PreflightResultV1 | PreflightResultV2 | PreflightResultV3 | None:
    if value is None or isinstance(
        value, (PreflightResultV1, PreflightResultV2, PreflightResultV3)
    ):
        return value
    try:
        version = value.get("preflight_schema_version")
        if version == "0.3":
            return PreflightResultV3.model_validate(value)
        if version == "0.2":
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
        # ``Path.is_file`` follows symlinks and treats a broken link as
        # absence. A lexically present default is acknowledged trust evidence;
        # if its identity or bytes are unsafe, preflight must fail closed
        # rather than silently behaving as though no baseline were configured.
        if not os.path.lexists(baseline_path):
            return None, None
    else:
        baseline_path = baseline if baseline.is_absolute() else workspace / baseline
        baseline_display = str(baseline)
    try:
        baseline_payload = load_host_grants_baseline(baseline_path)
    except ValueError as exc:
        if not explicit_baseline:
            return (
                build_host_drift_payload(
                    baseline={
                        "host_grants_schema_version": "unreadable",
                        "_load_error": "baseline_unreadable_or_invalid",
                    },
                    inventory=host_audit_inventory(workspace),
                    baseline_file=baseline_display,
                ),
                f"Host-grants baseline {baseline_display} could not be loaded; "
                f"host-grant drift is incomparable and requires review: {exc}",
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
        'sandbox":"disabled',
        "write-all",
        "pull_request_target",
        "new hook",
        '"hooks"',
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


def _verify_required_signal(verify_command: str = _VERIFY_COMMAND) -> PreflightSignalV1:
    return PreflightSignalV1(
        id="verify_required:diff",
        kind="verify_required",
        severity="info",
        actor="coding_agent",
        subject="release_verification",
        path=None,
        reason="The plan includes files, capability requests, or host permission requests that require deterministic verification before completion.",
        recommendation="Run the verifier and read verifier.json plus report.json.release_decision.decision before reporting the work complete.",
        related_command=verify_command,
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
    return request.effect in _HIGH_RISK_EFFECTS or bool(set(request.risk_tags) & _HIGH_RISK_TAGS)


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
    verify_command: str = _VERIFY_COMMAND,
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
            command=verify_command,
            why=verify_signal.reason,
        )
    return PreflightNextAction(
        actor="coding_agent",
        kind="continue",
        command=None,
        why="No requested protected-surface touch, host drift, or evidence gap was found by preflight.",
    )


def _derive_preflight_control(
    *,
    first_next_action: PreflightNextAction,
    requires_human_review: bool,
    requires_verify: bool,
    allowed_next_commands: list[str],
):
    """Project preflight signals through the shared control derivation."""

    reason = first_next_action.why
    if requires_human_review:
        return derive_agent_control(
            reason=reason,
            next_action=HumanControlAction(kind="stop", why=reason),
            verify_required=requires_verify,
            human_review_required=True,
            human_review_why=reason,
            stop_reason=reason,
        )
    if requires_verify:
        command = first_next_action.command
        if first_next_action.kind != "verify" or not command:
            raise ValueError("preflight verification obligation requires an exact verify command")
        return derive_agent_control(
            reason=reason,
            next_action=CodingAgentCommandAction(
                kind="verify",
                command=command,
                why=reason,
            ),
            verify_required=True,
            allowed_next_commands=allowed_next_commands,
        )
    return derive_agent_control(reason=reason)


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
