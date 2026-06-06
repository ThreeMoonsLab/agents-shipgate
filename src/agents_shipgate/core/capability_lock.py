from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents_shipgate.core.capabilities import build_capability_facts
from agents_shipgate.core.domain import Agent, Tool
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.schemas.capabilities import (
    CapabilityFactV1,
    CapabilityHashName,
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
DEFAULT_CAPABILITY_LOCK_REPORT_PATH = (
    Path("agents-shipgate-reports") / "capabilities.lock.json"
)

SEMANTIC_CAPABILITY_HASH_FIELDS: tuple[CapabilityHashName, ...] = (
    "identity_hash",
    "effect_hash",
    "authority_hash",
    "control_hash",
    "schema_hash",
    "risk_hash",
)
ALL_CAPABILITY_HASH_FIELDS: tuple[CapabilityHashName, ...] = (
    *SEMANTIC_CAPABILITY_HASH_FIELDS,
    "evidence_hash",
)


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
        return CapabilityLockFileV1.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise InputParseError(f"Invalid capability lock file {path}: {exc}") from exc


def diff_capability_locks(
    base: CapabilityLockFileV1,
    head: CapabilityLockFileV1,
    *,
    base_path: Path | None = None,
    head_path: Path | None = None,
) -> CapabilityLockDiffV1:
    base_by_id = {fact.id: fact for fact in base.capabilities}
    head_by_id = {fact.id: fact for fact in head.capabilities}

    raw_added = _sort_facts(
        [
            head_by_id[capability_id]
            for capability_id in head_by_id.keys() - base_by_id.keys()
        ]
    )
    raw_removed = _sort_facts(
        [
            base_by_id[capability_id]
            for capability_id in base_by_id.keys() - head_by_id.keys()
        ]
    )
    reidentified, added, removed = _reidentified_changes(raw_removed, raw_added)
    changed: list[CapabilityLockChangedFact] = []
    evidence_changed: list[CapabilityLockChangedFact] = []
    unchanged_count = 0

    for capability_id in sorted(base_by_id.keys() & head_by_id.keys()):
        before = base_by_id[capability_id]
        after = head_by_id[capability_id]
        changed_hashes = _changed_hashes(before, after)
        if not changed_hashes:
            unchanged_count += 1
            continue
        row = CapabilityLockChangedFact(
            id=capability_id,
            tool_name=after.identity.tool_name,
            operation=after.identity.operation,
            changed_hashes=changed_hashes,
            before=before,
            after=after,
        )
        if any(name in SEMANTIC_CAPABILITY_HASH_FIELDS for name in changed_hashes):
            changed.append(row)
        else:
            evidence_changed.append(row)

    changed.sort(key=_changed_sort_key)
    evidence_changed.sort(key=_changed_sort_key)
    reidentified.sort(key=_changed_sort_key)
    summary = CapabilityLockDiffSummary(
        added=len(added),
        removed=len(removed),
        reidentified=len(reidentified),
        changed=len(changed),
        evidence_changed=len(evidence_changed),
        unchanged=unchanged_count,
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
                    {"id": fact.id, "evidence_hash": fact.hashes.evidence_hash}
                    for fact in facts
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


def _changed_hashes(
    before: CapabilityFactV1,
    after: CapabilityFactV1,
) -> tuple[CapabilityHashName, ...]:
    return tuple(
        name
        for name in ALL_CAPABILITY_HASH_FIELDS
        if getattr(before.hashes, name) != getattr(after.hashes, name)
    )


def _reidentified_changes(
    removed: list[CapabilityFactV1],
    added: list[CapabilityFactV1],
) -> tuple[
    list[CapabilityLockChangedFact],
    list[CapabilityFactV1],
    list[CapabilityFactV1],
]:
    added_by_key: dict[tuple[str, str, str, str, str], list[CapabilityFactV1]] = {}
    for fact in added:
        added_by_key.setdefault(_lineage_key(fact), []).append(fact)
    for facts in added_by_key.values():
        facts.sort(key=capability_fact_sort_key)

    reidentified: list[CapabilityLockChangedFact] = []
    remaining_removed: list[CapabilityFactV1] = []
    consumed_added_ids: set[str] = set()

    for before in removed:
        candidates = added_by_key.get(_lineage_key(before), [])
        after = next((fact for fact in candidates if fact.id not in consumed_added_ids), None)
        if after is None:
            remaining_removed.append(before)
            continue
        consumed_added_ids.add(after.id)
        reidentified.append(
            CapabilityLockChangedFact(
                id=f"{before.id}->{after.id}",
                tool_name=after.identity.tool_name,
                operation=after.identity.operation,
                changed_hashes=_changed_hashes(before, after),
                before=before,
                after=after,
            )
        )

    remaining_added = [fact for fact in added if fact.id not in consumed_added_ids]
    return (
        sorted(reidentified, key=_changed_sort_key),
        _sort_facts(remaining_added),
        _sort_facts(remaining_removed),
    )


def _lineage_key(fact: CapabilityFactV1) -> tuple[str, str, str, str, str]:
    identity = fact.identity
    return (
        identity.agent_id,
        identity.provider,
        identity.operation,
        identity.tool_name,
        identity.subject_kind,
    )


def _sort_facts(facts: list[CapabilityFactV1]) -> list[CapabilityFactV1]:
    return sorted(facts, key=capability_fact_sort_key)


def _changed_sort_key(row: CapabilityLockChangedFact) -> tuple[str, str, str]:
    return (row.tool_name, row.operation, row.id)


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
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


__all__ = [
    "DEFAULT_CAPABILITY_LOCK_PATH",
    "DEFAULT_CAPABILITY_LOCK_REPORT_PATH",
    "diff_capability_locks",
    "build_capability_lock",
    "load_capability_lock",
    "render_capability_lock_diff_json",
    "render_capability_lock_json",
]
