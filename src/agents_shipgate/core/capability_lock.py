from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents_shipgate.core.capabilities import (
    build_capability_facts,
    redacted_capability_facts,
)
from agents_shipgate.core.capability_delta import (
    CapabilityDefinitionDelta,
    CapabilityDeltaRow,
    diff_capability_fact_sets,
)
from agents_shipgate.core.domain import Agent, Tool
from agents_shipgate.core.errors import InputParseError, format_validation_error
from agents_shipgate.core.privacy import REDACTION_MARKER_RE, redact_data, redact_text
from agents_shipgate.invocation import retarget_command
from agents_shipgate.schemas.capabilities import (
    CAPABILITY_LOCK_SCHEMA_VERSION,
    CAPABILITY_STANDARD_VERSION,
    CapabilityFactV1,
    CapabilityLockChangedFact,
    CapabilityLockDefinitionChange,
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
_SHA256_IDENTIFIER = re.compile(r"(?:cap_|sha256:)?[0-9a-f]{64}\Z")


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
    # Redacted here, where the lock is assembled, and before any lock-level
    # digest is taken: everything downstream - the lock file, both lock-diff
    # files and the delta attestations - is built from these values, so every
    # string the family publishes is redacted and every digest computed from
    # here on is taken over the redacted text (#1079). The readers' own
    # ``tool_v2_``/``obs_v1_`` ids stay digests of the source text.
    facts = redacted_capability_facts(
        build_capability_facts(manifest, agent_id=agent.id, tools=tools)
    )
    source = _redacted_source(
        CapabilityLockSource(
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
    )
    return CapabilityLockFileV1(
        cli_version=redact_text(cli_version) or "",
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
        raise InputParseError(f"Capability lock not found: {_diagnostic_text(str(path))}")
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise _invalid_lock_file(str(path), exc) from exc
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
        if any(
            value is not None and not _SHA256_IDENTIFIER.fullmatch(value) and redact_text(value) != value
            for value in _stored_lock_identifiers(lock)
        ):
            raise ValueError("A capability identifier or digest contains sensitive text")
        lock = _redacted_lock(lock)
        # Normalization keeps old locks readable, but a lock diff must still
        # know which capability standard produced each side.  Store the
        # source version as a private runtime attribute: it never enters the
        # public lock JSON or its hashes.
        if source_schema_version is not None:
            lock._source_capability_lock_schema_version = source_schema_version  # type: ignore[attr-defined]
        return lock
    except (ValidationError, ValueError, TypeError) as exc:
        raise _invalid_lock_file(source, exc) from exc


def _stored_lock_identifiers(lock: CapabilityLockFileV1) -> Iterator[str | None]:
    """Stored identifiers and hashes that redaction otherwise preserves."""

    yield from lock.hashes.model_dump().values()
    for fact in lock.capabilities:
        yield fact.id
        yield from fact.hashes.model_dump().values()
        if fact.definition is not None:
            yield from fact.definition.model_dump().values()


def _diagnostic_text(value: str) -> str:
    from agents_shipgate.core.agent_control_envelope import single_line_text

    return single_line_text(redact_text(value) or "")


def _invalid_lock_file(source: str, exc: Exception) -> InputParseError:
    label = f"Invalid capability lock file {_diagnostic_text(source)}"
    if isinstance(exc, ValidationError):
        return InputParseError(format_validation_error(exc, label=label))
    return InputParseError(f"{label}: {_diagnostic_text(str(exc))}")


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
    definition_changed = [
        _lock_definition_change(delta) for delta in semantic_diff.definition_changed
    ]
    summary = CapabilityLockDiffSummary(
        added=len(added),
        removed=len(removed),
        reidentified=len(reidentified),
        changed=len(changed),
        evidence_changed=len(evidence_changed),
        unchanged=semantic_diff.unchanged_count,
        definition_changed=len(definition_changed),
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
        definition_changed=definition_changed,
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
                        "binding_hash": fact.hashes.binding_hash,
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
        definition_set_hash=_sha256(
            {
                "capabilities": [
                    {
                        "id": fact.id,
                        "definition": (
                            fact.definition.model_dump(mode="json")
                            if fact.definition is not None
                            else None
                        ),
                    }
                    for fact in facts
                ]
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
        definition_set_hash=lock.hashes.definition_set_hash,
        capability_count=lock.summary.capability_count,
    )


#: Prior lock schemas whose payload shape is identical to the current one, so a
#: lock written under them is readable as-is once its version identifier is
#: advanced. A version lands here when the only delta was additive inside an
#: embedded union — v0.6 → v0.7 and v0.7 → v0.8 each widened the semantic-issue
#: enum and changed no field. Without it, bumping the lock schema makes every
#: committed ``capabilities.lock.json`` unloadable, which is a far larger break
#: than the bump itself repairs.
#:
#: **Every bump has to add the version it just froze.** v0.7 was readable only
#: because it *was* the current constant; the moment v0.8 shipped, every
#: committed v0.7 lock started failing to load. The guard is
#: ``test_every_lock_schema_of_the_current_standard_still_loads``, which walks
#: the published ``docs/capability-lock-schema.v*.json`` set rather than a
#: hardcoded version, so it cannot be left behind by the next bump the way the
#: previous test — pinned to ``0.6`` — was.
#: v0.8 joined at v0.9: the only delta was the optional ``definition`` block on
#: each fact and the optional ``definition_set_hash``, both absent (``None``) on
#: a v0.8 lock. It loads, and its standard row below still refuses comparison
#: with a v0.9 lock, so a pre-definition lock is re-exported, never diffed as
#: if every definition were unknown.
_STRUCTURALLY_CURRENT_LOCK_SCHEMAS = frozenset({"0.6", "0.7", "0.8"})


def _normalize_capability_lock_payload(payload: dict[str, Any]) -> dict[str, Any]:
    version = payload.get("capability_lock_schema_version")
    if version in _STRUCTURALLY_CURRENT_LOCK_SCHEMAS:
        return {
            **payload,
            "capability_lock_schema_version": CAPABILITY_LOCK_SCHEMA_VERSION,
        }
    if version in {"0.1", "0.2", "0.3", "0.4"}:
        normalized = dict(payload)
        normalized["capability_lock_schema_version"] = CAPABILITY_LOCK_SCHEMA_VERSION
        normalized["experimental"] = False
        normalized["capabilities"] = [
            {
                **fact,
                "hashes": {
                    **fact.get("hashes", {}),
                    "binding_hash": fact.get("hashes", {}).get(
                        "binding_hash", "legacy_binding_unknown"
                    ),
                },
            }
            for fact in payload.get("capabilities", [])
            if isinstance(fact, dict)
        ]
        return normalized
    return payload


_CAPABILITY_STANDARD_BY_LOCK_SCHEMA = {
    "0.1": "0.1",
    "0.2": "0.1",
    "0.3": "0.2",
    "0.4": "0.3",
    "0.5": "0.4",
    # Pinned to the literal standard v0.6 shipped under, never to the current
    # constant. Lock v0.6 and v0.7 happen to describe the same standard today,
    # but binding a *historical* row to a mutable constant means the next
    # capability-standard bump silently relabels every v0.6 lock as current and
    # lets it be compared instead of re-exported — the exact opposite of what
    # this table exists to prevent (PR #413 review 5).
    "0.6": "0.5",
    # Same rule, same reason: pinned to the literal standard v0.7 shipped
    # under. It was covered by the constant key below until v0.8 took that
    # key, at which point a v0.7 lock fell through to the *default* — which
    # returns the current standard and would have relabelled a historical lock
    # as comparable.
    "0.7": "0.5",
    # And again for v0.8, frozen when v0.9 added tool-definition digests
    # (capability standard v0.6).
    "0.8": "0.5",
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

    config_path = stale_lock.source.config_path
    output_path = str(stale_path or DEFAULT_CAPABILITY_LOCK_PATH)
    message = (
        "Mixed capability-standard lock diff is not comparable "
        f"(base={base_standard}, head={head_standard}). Re-export the "
        f"{stale_side} lock from its source workspace with the current engine"
    )
    if any(
        redact_text(path) != path or REDACTION_MARKER_RE.search(path) is not None
        for path in (config_path, output_path)
    ):
        raise InputParseError(
            f"{message}. Supply its original manifest and output paths locally, then rerun the capability diff."
        )
    command = retarget_command(
        " ".join(
            [
                "agents-shipgate capability export",
                "--config",
                shlex.quote(config_path),
                "--out",
                shlex.quote(output_path),
                "--no-report-copy",
            ]
        )
    )
    raise InputParseError(
        f"{message} using exactly: `{command}`. Then rerun the capability diff."
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


def _lock_definition_change(
    delta: CapabilityDefinitionDelta,
) -> CapabilityLockDefinitionChange:
    before = delta.before.definition
    after = delta.after.definition
    if before is None or after is None:
        # ``changed_definition_parts`` returns parts only when both sides
        # carry a definition; a delta without one is a programming error.
        raise ValueError(f"definition delta {delta.id} lacks a definition on one side")
    return CapabilityLockDefinitionChange(
        id=delta.id,
        tool_name=delta.after.identity.tool_name,
        provider=delta.after.identity.provider,
        operation=delta.after.identity.operation,
        parts=delta.parts,
        before=before,
        after=after,
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
        return redact_text(path.name)
    return redact_text(str(path))


def _redacted_source(source: CapabilityLockSource) -> CapabilityLockSource:
    dumped = source.model_dump(mode="python")
    redacted = redact_data(dumped)
    if redacted == dumped:
        return source
    return CapabilityLockSource.model_validate(redacted)


def _redacted_lock(lock: CapabilityLockFileV1) -> CapabilityLockFileV1:
    """A lock read from disk, made publishable the way an assembled one is.

    A lock written before #1079 - a committed ``capabilities.lock.json``, a
    cached base lock - can carry a credential verbatim, and ``verify`` and
    ``capability diff`` republish what they read: the base lock file, both
    lock-diff files and the attestations. Redacting on read publishes none of
    it, and puts both sides of a comparison in one space: an old lock and a
    fresh export of the same tools get the same ids, so upgrading does not read
    as a reidentified capability. A lock with nothing to redact is returned as
    it was read. Otherwise each lock-level digest whose input moved is
    recomputed over the redacted content, and one whose input did not keeps its
    stored value, so a digest an older engine wrote under an older recipe is
    not rewritten without cause.
    """

    facts = redacted_capability_facts(lock.capabilities)
    source = _redacted_source(lock.source)
    cli_version = redact_text(lock.cli_version) or ""
    if (
        source is lock.source
        and cli_version == lock.cli_version
        and all(
            published is read
            for published, read in zip(
                facts,
                sorted(lock.capabilities, key=capability_fact_sort_key),
                strict=True,
            )
        )
    ):
        return lock
    stored = lock.hashes
    before = _lock_hashes(lock.source, lock.capabilities)
    after = _lock_hashes(source, facts)
    hashes = CapabilityLockHashes(
        semantic_capability_set_hash=_moved(
            stored.semantic_capability_set_hash,
            before.semantic_capability_set_hash,
            after.semantic_capability_set_hash,
        ),
        evidence_set_hash=_moved(
            stored.evidence_set_hash, before.evidence_set_hash, after.evidence_set_hash
        ),
        source_set_hash=_moved(
            stored.source_set_hash, before.source_set_hash, after.source_set_hash
        ),
        definition_set_hash=(
            None
            if stored.definition_set_hash is None
            else _moved(
                stored.definition_set_hash,
                before.definition_set_hash or "",
                after.definition_set_hash or "",
            )
        ),
    )
    return lock.model_copy(
        update={
            "cli_version": cli_version,
            "source": source,
            "hashes": hashes,
            "capabilities": facts,
        }
    )


def _moved(stored: str, before: str, after: str) -> str:
    """``stored``, unless redaction moved the content this digest covers."""

    return stored if before == after else after


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
