from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents_shipgate.core.capabilities import build_capability_facts
from agents_shipgate.core.capability_delta import (
    CapabilityDeltaRow,
    diff_capability_fact_sets,
)
from agents_shipgate.core.domain import Agent, Tool
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.schemas.capabilities import (
    CAPABILITY_LOCK_SCHEMA_VERSION,
    CAPABILITY_STANDARD_VERSION,
    CapabilityFactV1,
    CapabilityLockChangedFact,
    CapabilityLockDiffSummary,
    CapabilityLockDiffV1,
    CapabilityLockFileV1,
    CapabilityLockHashes,
    CapabilityLockRef,
    CapabilityLockSource,
    CapabilityLockSummary,
    capability_fact_sort_key,
)
from agents_shipgate.schemas.manifest import AgentsShipgateManifest

DEFAULT_CAPABILITY_LOCK_PATH = Path(".agents-shipgate") / "capabilities.lock.json"
DEFAULT_CAPABILITY_LOCK_REPORT_PATH = Path("agents-shipgate-reports") / "capabilities.lock.json"


def build_capability_lock(
    manifest: AgentsShipgateManifest,
    *,
    agent: Agent,
    tools: list[Tool],
    config_path: Path,
    manifest_dir: Path,
    cli_version: str,
    source_count: int = 0,
    source_warning_count: int = 0,
    toolkit_bound_count: int = 0,
    plugins_enabled: bool = True,
) -> CapabilityLockFileV1:
    facts = build_capability_facts(manifest, agent_id=agent.id, tools=tools)
    source = CapabilityLockSource(
        config_path=_manifest_relative_path(config_path, manifest_dir),
        manifest_dir=".",
        project_name=manifest.project.name,
        agent_id=agent.id,
        agent_name=agent.name,
        environment_target=manifest.environment.target,
        tool_count=len(tools),
        toolkit_bound_count=toolkit_bound_count,
        source_count=source_count,
        source_warning_count=source_warning_count,
        plugins_enabled=plugins_enabled,
    )
    return CapabilityLockFileV1(
        cli_version=cli_version,
        source=source,
        summary=_lock_summary(facts),
        hashes=_lock_hashes(source, facts),
        capabilities=facts,
    )


def render_capability_lock_json(lock: CapabilityLockFileV1) -> str:
    return lock.model_dump_json(indent=2, exclude_none=False) + "\n"


def render_capability_lock_diff_json(diff: CapabilityLockDiffV1) -> str:
    return diff.model_dump_json(indent=2, exclude_none=False) + "\n"


def load_capability_lock(path: Path) -> CapabilityLockFileV1:
    if not path.exists():
        raise InputParseError(f"Capability lock not found: {path}")
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputParseError(f"Invalid capability lock file {path}: {exc}") from exc
    return load_capability_lock_json(
        content,
        source=str(path),
    )


def load_capability_lock_json(content: str, *, source: str) -> CapabilityLockFileV1:
    try:
        payload = json.loads(content)
        source_schema_version: str | None = None
        if isinstance(payload, dict):
            raw_version = payload.get("capability_lock_schema_version")
            source_schema_version = raw_version if isinstance(raw_version, str) else None
            payload = _normalize_capability_lock_payload(payload)
        lock = CapabilityLockFileV1.model_validate(payload)
        # Normalization keeps old locks readable, but a lock diff must still
        # know which capability standard produced each side.  Store the
        # source version as a private runtime attribute: it never enters the
        # public lock JSON or its hashes.
        if source_schema_version is not None:
            lock._source_capability_lock_schema_version = source_schema_version  # type: ignore[attr-defined]
        return lock
    except (ValidationError, ValueError, TypeError) as exc:
        raise InputParseError(f"Invalid capability lock file {source}: {exc}") from exc


def diff_capability_locks(
    base: CapabilityLockFileV1,
    head: CapabilityLockFileV1,
    *,
    base_path: Path | None = None,
    head_path: Path | None = None,
) -> CapabilityLockDiffV1:
    _require_comparable_capability_standards(
        base,
        head,
        base_path=base_path,
        head_path=head_path,
    )
    semantic_diff = diff_capability_fact_sets(base.capabilities, head.capabilities)
    added = _sort_facts([ctx.fact for ctx in semantic_diff.added])
    removed = _sort_facts([ctx.fact for ctx in semantic_diff.removed])
    reidentified = [_lock_changed_fact(row) for row in semantic_diff.reidentified]
    changed = [_lock_changed_fact(row) for row in semantic_diff.changed]
    evidence_changed = [_lock_changed_fact(row) for row in semantic_diff.evidence_changed]
    summary = CapabilityLockDiffSummary(
        added=len(added),
        removed=len(removed),
        reidentified=len(reidentified),
        changed=len(changed),
        evidence_changed=len(evidence_changed),
        unchanged=semantic_diff.unchanged_count,
    )
    return CapabilityLockDiffV1(
        base=_lock_ref(base, path=base_path),
        head=_lock_ref(head, path=head_path),
        summary=summary,
        added=added,
        removed=removed,
        reidentified=reidentified,
        changed=changed,
        evidence_changed=evidence_changed,
    )


def _lock_summary(facts: list[CapabilityFactV1]) -> CapabilityLockSummary:
    return CapabilityLockSummary(
        capability_count=len(facts),
        high_risk_count=sum(1 for fact in facts if fact.effect.high_risk),
        broad_scope_count=sum(1 for fact in facts if fact.authority.broad_scopes),
        write_count=sum(1 for fact in facts if fact.effect.effect == "write"),
        external_communication_count=sum(
            1 for fact in facts if fact.effect.effect == "external_communication"
        ),
        financial_count=sum(1 for fact in facts if fact.effect.financial),
        code_execution_count=sum(1 for fact in facts if fact.effect.code_execution),
    )


def _lock_hashes(
    source: CapabilityLockSource,
    facts: list[CapabilityFactV1],
) -> CapabilityLockHashes:
    return CapabilityLockHashes(
        semantic_capability_set_hash=_sha256(
            {
                "capabilities": [
                    {
                        "id": fact.id,
                        "identity_hash": fact.hashes.identity_hash,
                        "effect_hash": fact.hashes.effect_hash,
                        "authority_hash": fact.hashes.authority_hash,
                        "control_hash": fact.hashes.control_hash,
                        "schema_hash": fact.hashes.schema_hash,
                        "risk_hash": fact.hashes.risk_hash,
                    }
                    for fact in facts
                ]
            }
        ),
        evidence_set_hash=_sha256(
            {
                "capabilities": [
                    {"id": fact.id, "evidence_hash": fact.hashes.evidence_hash} for fact in facts
                ]
            }
        ),
        source_set_hash=_sha256(
            {
                "source": source.model_dump(
                    mode="json",
                    exclude={"source_warning_count"},
                ),
                "capability_sources": [
                    {
                        "id": fact.id,
                        "source_type": fact.evidence.source_type,
                        "source_id": fact.evidence.source_id,
                        "source_ref": fact.evidence.source_ref,
                        "source_path": fact.evidence.source_path,
                        "source_pointer": fact.evidence.source_pointer,
                    }
                    for fact in facts
                ],
            }
        ),
    )


def _lock_ref(lock: CapabilityLockFileV1, *, path: Path | None) -> CapabilityLockRef:
    return CapabilityLockRef(
        path=_lock_ref_path(path),
        capability_lock_schema_version=lock.capability_lock_schema_version,
        semantic_capability_set_hash=lock.hashes.semantic_capability_set_hash,
        evidence_set_hash=lock.hashes.evidence_set_hash,
        source_set_hash=lock.hashes.source_set_hash,
        capability_count=lock.summary.capability_count,
    )


def _normalize_capability_lock_payload(payload: dict[str, Any]) -> dict[str, Any]:
    version = payload.get("capability_lock_schema_version")
    if version in {"0.1", "0.2", "0.3"}:
        normalized = dict(payload)
        normalized["capability_lock_schema_version"] = CAPABILITY_LOCK_SCHEMA_VERSION
        normalized["experimental"] = False
        return normalized
    return payload


_CAPABILITY_STANDARD_BY_LOCK_SCHEMA = {
    "0.1": "0.1",
    "0.2": "0.1",
    "0.3": "0.2",
    CAPABILITY_LOCK_SCHEMA_VERSION: CAPABILITY_STANDARD_VERSION,
}


def _require_comparable_capability_standards(
    base: CapabilityLockFileV1,
    head: CapabilityLockFileV1,
    *,
    base_path: Path | None,
    head_path: Path | None,
) -> None:
    base_standard = _source_capability_standard(base)
    head_standard = _source_capability_standard(head)
    if base_standard == head_standard:
        return

    if base_standard != CAPABILITY_STANDARD_VERSION:
        stale_side = "base"
        stale_lock = base
        stale_path = base_path
    else:
        stale_side = "head"
        stale_lock = head
        stale_path = head_path

    output_path = stale_path or DEFAULT_CAPABILITY_LOCK_PATH
    command = " ".join(
        [
            "agents-shipgate capability export",
            "--config",
            shlex.quote(stale_lock.source.config_path),
            "--out",
            shlex.quote(str(output_path)),
            "--no-report-copy",
        ]
    )
    raise InputParseError(
        "Mixed capability-standard lock diff is not comparable "
        f"(base={base_standard}, head={head_standard}). Re-export the "
        f"{stale_side} lock from its source workspace with the current engine "
        f"using exactly: `{command}`. Then rerun the capability diff."
    )


def _source_capability_standard(lock: CapabilityLockFileV1) -> str:
    source_schema_version = getattr(
        lock,
        "_source_capability_lock_schema_version",
        lock.capability_lock_schema_version,
    )
    return _CAPABILITY_STANDARD_BY_LOCK_SCHEMA.get(
        source_schema_version,
        CAPABILITY_STANDARD_VERSION,
    )


def _lock_changed_fact(row: CapabilityDeltaRow) -> CapabilityLockChangedFact:
    return CapabilityLockChangedFact(
        id=row.id,
        tool_name=row.after.identity.tool_name,
        operation=row.after.identity.operation,
        changed_hashes=row.changed_hashes,
        semantic_direction=row.semantic_direction,
        semantic_changes=row.semantic_changes,
        before=row.before,
        after=row.after,
    )


def _sort_facts(facts: list[CapabilityFactV1]) -> list[CapabilityFactV1]:
    return sorted(facts, key=capability_fact_sort_key)


def _manifest_relative_path(path: Path, manifest_dir: Path) -> str:
    try:
        relative = path.resolve().relative_to(manifest_dir.resolve())
        return str(relative) if str(relative) else path.name
    except (OSError, ValueError):
        return path.name


def _lock_ref_path(path: Path | None) -> str | None:
    if path is None:
        return None
    if path.is_absolute():
        return path.name
    return str(path)


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


__all__ = [
    "DEFAULT_CAPABILITY_LOCK_PATH",
    "DEFAULT_CAPABILITY_LOCK_REPORT_PATH",
    "diff_capability_locks",
    "build_capability_lock",
    "load_capability_lock",
    "load_capability_lock_json",
    "render_capability_lock_diff_json",
    "render_capability_lock_json",
]
