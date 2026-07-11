#!/usr/bin/env python3
"""Local, non-gating prototype for four-set capability reconciliation.

This developer-only script reads explicit local artifacts. It never connects to
a sandbox, runs an agent, calls tools, or mutates an allowlist.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

from agents_shipgate.core.capability_reconciliation import (  # noqa: E402
    load_capability_intent_policy,
    load_capability_observation_bundle,
    reconcile_capabilities,
    render_capability_reconciliation_json,
)
from agents_shipgate.core.errors import InputParseError  # noqa: E402
from agents_shipgate.inputs.common import load_structured_file, resolve_input_path  # noqa: E402
from agents_shipgate.schemas.capabilities import (  # noqa: E402
    CAPABILITY_LOCK_SCHEMA_VERSION,
    CapabilityLockFileV1,
)
from agents_shipgate.schemas.capability_reconciliation import (  # noqa: E402
    ReconciliationInputBindingV1,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prototype_capability_reconciliation",
        description=(
            "Reconcile reviewed intent, a static capability lock, sandbox grants, "
            "and sampled observations into a deterministic non-gating artifact."
        ),
    )
    parser.add_argument("--workspace", type=Path, default=Path("."))
    parser.add_argument("--lock", type=str, required=True)
    parser.add_argument("--intent", type=str, required=True)
    parser.add_argument("--evidence", type=str, action="append", required=True)
    parser.add_argument("--base-evidence", type=str, action="append", default=[])
    parser.add_argument("--out", type=str, required=True)
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Also print the reconciliation artifact to stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    workspace = args.workspace.resolve()
    try:
        lock_path = resolve_input_path(workspace, args.lock)
        intent_path = resolve_input_path(workspace, args.intent)
        evidence_paths = [resolve_input_path(workspace, value) for value in args.evidence]
        base_paths = [
            resolve_input_path(workspace, value) for value in args.base_evidence
        ]
        out_path = resolve_input_path(workspace, args.out)
        input_paths = [lock_path, intent_path, *evidence_paths, *base_paths]
        for path in [*input_paths, out_path]:
            if path.suffix.lower() != ".json":
                raise InputParseError(
                    f"Capability reconciliation artifacts must use .json paths: {path}"
                )
        if out_path in input_paths:
            raise InputParseError("Output path must not overwrite an input artifact")

        lock = _load_current_lock(lock_path)
        intent = load_capability_intent_policy(intent_path)
        evidence = [load_capability_observation_bundle(path) for path in evidence_paths]
        base_evidence = [load_capability_observation_bundle(path) for path in base_paths]
        bindings = [
            _binding(
                lock_path,
                workspace,
                role="capability_lock",
                schema_version=lock.capability_lock_schema_version,
            ),
            _binding(
                intent_path,
                workspace,
                role="intent_policy",
                schema_version=intent.capability_intent_schema_version,
            ),
        ]
        bindings.extend(
            _binding(
                path,
                workspace,
                role="evidence",
                schema_version=bundle.capability_observation_schema_version,
            )
            for path, bundle in zip(evidence_paths, evidence, strict=True)
        )
        bindings.extend(
            _binding(
                path,
                workspace,
                role="base_evidence",
                schema_version=bundle.capability_observation_schema_version,
            )
            for path, bundle in zip(base_paths, base_evidence, strict=True)
        )

        artifact = reconcile_capabilities(
            lock=lock,
            intent=intent,
            evidence_bundles=evidence,
            base_evidence_bundles=base_evidence,
            input_bindings=bindings,
        )
        rendered = render_capability_reconciliation_json(artifact)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
    except InputParseError as exc:
        print(f"Input parsing error: {exc}", file=sys.stderr)
        return 3
    except OSError as exc:
        print(f"Input/output error: {exc}", file=sys.stderr)
        return 4
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        print(f"Internal error: {exc}", file=sys.stderr)
        return 4

    if args.json_output:
        print(rendered, end="")
    else:
        print(f"Wrote capability reconciliation artifact to {_display_path(out_path, workspace)}")
        print(
            "Targets: "
            f"{artifact.summary.target_count}  "
            f"Evidence gaps: {artifact.summary.evidence_gap_count}  "
            f"Manual proposals: {artifact.summary.candidate_policy_change_count}"
        )
    return 0


def _load_current_lock(path: Path) -> CapabilityLockFileV1:
    payload = load_structured_file(path)
    if not isinstance(payload, dict):
        raise InputParseError(f"Capability lock must be a JSON object: {path}")
    version = payload.get("capability_lock_schema_version")
    if version != CAPABILITY_LOCK_SCHEMA_VERSION:
        raise InputParseError(
            "Capability reconciliation requires capability lock "
            f"v{CAPABILITY_LOCK_SCHEMA_VERSION}; found {version!r}. Re-export with exactly: "
            f"`agents-shipgate capability export --config shipgate.yaml --out {path} "
            "--no-report-copy`."
        )
    try:
        return CapabilityLockFileV1.model_validate(payload)
    except ValidationError as exc:
        raise InputParseError(f"Invalid capability lock file {path}: {exc}") from exc


def _binding(
    path: Path,
    workspace: Path,
    *,
    role: str,
    schema_version: str,
) -> ReconciliationInputBindingV1:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return ReconciliationInputBindingV1(
        role=role,
        path=_display_path(path, workspace),
        sha256=f"sha256:{digest}",
        schema_version=schema_version,
    )


def _display_path(path: Path, workspace: Path) -> str:
    return path.resolve().relative_to(workspace.resolve()).as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
