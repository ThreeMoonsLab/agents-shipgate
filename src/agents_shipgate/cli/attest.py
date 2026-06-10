"""``agents-shipgate attest`` — a durable, local release attestation.

The Attestation Contract (Pattern 9): an agent-capability release should leave a
record, not a memory — *which* capability shipped, under *which* verdict,
acknowledged by *whom*. This command derives a deterministic, local, JSON-first
attestation from ``verifier.json`` (enriched from the sibling ``report.json``
when present).

It introduces no new gate: every field is copied or hashed from the verify run.
It is content-addressed by git SHAs and artifact hashes and carries **no
wall-clock timestamp**, so re-deriving from the same inputs is byte-identical —
consistent with the project's determinism discipline.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import typer

from agents_shipgate import __version__
from agents_shipgate.core.errors import InputParseError
from agents_shipgate.schemas.attestation import ATTESTATION_SCHEMA_VERSION


def _attest_command(
    source: Path = typer.Option(
        Path("agents-shipgate-reports/verifier.json"),
        "--from",
        help="Path to verifier.json.",
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Write the attestation to this path.",
    ),
    redact: bool = typer.Option(
        True,
        "--redact/--no-redact",
        help="Reduce local artifact paths to filenames.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Print the attestation JSON to stdout.",
    ),
) -> None:
    """Derive a local release attestation from verifier.json.

    Deterministic and local-only: no network, no wall-clock timestamp. Records
    the verdict, the capability delta, the declared human-acknowledgement state,
    a policy-snapshot hash, and content hashes of the verify artifacts. It does
    not gate — ``report.json.release_decision.decision`` remains the only gate.
    """
    try:
        verifier = _load_json_object(source, "verifier.json")
    except InputParseError as exc:
        typer.echo(f"Input parsing error: {exc}", err=True)
        raise typer.Exit(3) from exc
    report = _load_sibling_report(source, verifier)
    payload = build_attestation_payload(
        verifier, source=source, redacted=redact, report=report
    )
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
    if json_output or out is None:
        typer.echo(rendered.rstrip())
    else:
        typer.echo(f"Wrote attestation to {out}")


def build_attestation_payload(
    verifier: dict[str, Any],
    *,
    source: Path,
    redacted: bool,
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a verify run onto a deterministic attestation dict.

    ``report`` (the sibling ``report.json``) is optional: when present it adds
    the declared ``human_ack`` state and a policy-snapshot hash; when absent the
    attestation degrades gracefully (``human_ack.satisfied`` is ``null`` and
    ``policy_snapshot_sha256`` is ``null``).
    """
    artifacts = _obj(verifier.get("artifacts"))
    release_decision = _obj(verifier.get("release_decision"))
    capability_review = _obj(verifier.get("capability_review"))
    human_review = _obj(verifier.get("human_review"))
    report = report or {}
    human_ack = _obj(report.get("human_ack"))
    effective_policy = report.get("effective_policy")

    return {
        "attestation_schema_version": ATTESTATION_SCHEMA_VERSION,
        "cli_version": __version__,
        "source_verifier": source.name if redacted else str(source),
        "redacted": redacted,
        "base_ref": verifier.get("base_ref"),
        "head_ref": verifier.get("head_ref"),
        "base_tree_sha": verifier.get("base_tree_sha"),
        "head_tree_sha": verifier.get("head_tree_sha"),
        "mode": verifier.get("mode"),
        "verdict": {
            "merge_verdict": verifier.get("merge_verdict"),
            "decision": verifier.get("decision") or release_decision.get("decision"),
            "applicability": verifier.get("applicability"),
            "can_merge_without_human": bool(verifier.get("can_merge_without_human")),
        },
        "capability": {
            "added": int(capability_review.get("added", 0) or 0),
            "modified": int(capability_review.get("modified", 0) or 0),
            "removed": int(capability_review.get("removed", 0) or 0),
            "trust_root_touched": bool(capability_review.get("trust_root_touched")),
            "policy_weakened": bool(capability_review.get("policy_weakened")),
            "change_ids": _change_ids(capability_review.get("top_changes")),
        },
        "capability_lock": _capability_lock_binding(
            artifacts,
            base_dir=source.parent,
            redacted=redacted,
        ),
        "capability_diff": _capability_diff_binding(
            artifacts,
            base_dir=source.parent,
            redacted=redacted,
        ),
        "human_ack": {
            "required": bool(
                human_ack.get("required", human_review.get("required", False))
            ),
            # ``None`` when no report.json was available to confirm it.
            "satisfied": human_ack.get("satisfied"),
            "outstanding": _str_list(human_ack.get("outstanding")),
        },
        "policy_snapshot_sha256": (
            _canonical_sha256(effective_policy) if effective_policy is not None else None
        ),
        "artifact_sha256": _artifact_hashes(artifacts, base_dir=source.parent),
    }


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InputParseError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise InputParseError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise InputParseError(f"{label} must contain an object: {path}")
    return data


def _load_sibling_report(
    source: Path, verifier: dict[str, Any]
) -> dict[str, Any] | None:
    """Best-effort read of the report.json beside verifier.json (or named in its
    artifacts map). Absence or a parse error is fine — enrichment is optional."""
    artifacts = _obj(verifier.get("artifacts"))
    name = Path(str(artifacts.get("report_json", "report.json"))).name or "report.json"
    candidate = source.parent / name
    if not candidate.is_file():
        return None
    try:
        return _load_json_object(candidate, "report.json")
    except InputParseError:
        return None


def _artifact_hashes(artifacts: Any, *, base_dir: Path) -> dict[str, str]:
    """sha256 of each verify artifact that sits beside verifier.json.

    Artifact paths inside verifier.json may be absolute or workspace-relative,
    so resolve by basename against the verifier.json directory — CWD-independent.
    """
    out: dict[str, str] = {}
    if not isinstance(artifacts, dict):
        return out
    for key, value in artifacts.items():
        candidate = base_dir / Path(str(value)).name
        if candidate.is_file():
            out[str(key)] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    return dict(sorted(out.items()))


def _capability_lock_binding(
    artifacts: dict[str, Any],
    *,
    base_dir: Path,
    redacted: bool,
) -> dict[str, Any]:
    key = "capability_lock"
    candidate = _artifact_candidate(artifacts, key, base_dir=base_dir)
    if candidate is None:
        return {
            "path": None,
            "sha256": None,
            "capability_lock_schema_version": None,
            "semantic_capability_set_hash": None,
            "evidence_set_hash": None,
            "source_set_hash": None,
            "capability_count": None,
        }
    payload = _load_optional_json(candidate)
    hashes = _obj(payload.get("hashes")) if isinstance(payload, dict) else {}
    summary = _obj(payload.get("summary")) if isinstance(payload, dict) else {}
    return {
        "path": _artifact_display_path(artifacts.get(key), redacted=redacted),
        "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "capability_lock_schema_version": (
            payload.get("capability_lock_schema_version")
            if isinstance(payload, dict)
            else None
        ),
        "semantic_capability_set_hash": hashes.get("semantic_capability_set_hash"),
        "evidence_set_hash": hashes.get("evidence_set_hash"),
        "source_set_hash": hashes.get("source_set_hash"),
        "capability_count": summary.get("capability_count"),
    }


def _capability_diff_binding(
    artifacts: dict[str, Any],
    *,
    base_dir: Path,
    redacted: bool,
) -> dict[str, Any] | None:
    key = "capability_lock_diff_json"
    candidate = _artifact_candidate(artifacts, key, base_dir=base_dir)
    if candidate is None:
        return None
    payload = _load_optional_json(candidate)
    if not isinstance(payload, dict):
        return {
            "path": _artifact_display_path(artifacts.get(key), redacted=redacted),
            "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
            "capability_lock_diff_schema_version": None,
            "base_semantic_capability_set_hash": None,
            "head_semantic_capability_set_hash": None,
            "summary": None,
        }
    base = _obj(payload.get("base"))
    head = _obj(payload.get("head"))
    return {
        "path": _artifact_display_path(artifacts.get(key), redacted=redacted),
        "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "capability_lock_diff_schema_version": payload.get(
            "capability_lock_diff_schema_version"
        ),
        "base_semantic_capability_set_hash": base.get(
            "semantic_capability_set_hash"
        ),
        "head_semantic_capability_set_hash": head.get(
            "semantic_capability_set_hash"
        ),
        "summary": _capability_diff_summary(payload.get("summary")),
    }


def _capability_diff_summary(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    return {
        "added": int(value.get("added", 0) or 0),
        "removed": int(value.get("removed", 0) or 0),
        "reidentified": int(value.get("reidentified", 0) or 0),
        "changed": int(value.get("changed", 0) or 0),
        "evidence_changed": int(value.get("evidence_changed", 0) or 0),
        "unchanged": int(value.get("unchanged", 0) or 0),
    }


def _artifact_candidate(
    artifacts: dict[str, Any],
    key: str,
    *,
    base_dir: Path,
) -> Path | None:
    value = artifacts.get(key)
    if value is None:
        return None
    candidate = base_dir / Path(str(value)).name
    if candidate.is_file():
        return candidate
    return None


def _artifact_display_path(value: Any, *, redacted: bool) -> str | None:
    if value is None:
        return None
    text = str(value)
    return Path(text).name if redacted else text


def _load_optional_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _canonical_sha256(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _change_ids(top_changes: Any) -> list[str]:
    if not isinstance(top_changes, list):
        return []
    ids = {
        str(change.get("id"))
        for change in top_changes
        if isinstance(change, dict) and change.get("id")
    }
    return sorted(ids)


def _str_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


__all__ = ["ATTESTATION_SCHEMA_VERSION", "build_attestation_payload"]
