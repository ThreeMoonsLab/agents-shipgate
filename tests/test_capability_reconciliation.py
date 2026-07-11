from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from agents_shipgate.core.capability_lock import build_capability_lock, render_capability_lock_json
from agents_shipgate.core.capability_reconciliation import (
    load_capability_intent_policy,
    load_capability_observation_bundle,
    reconcile_capabilities,
    render_capability_reconciliation_json,
)
from agents_shipgate.core.domain import Agent, AuthInfo, Tool
from agents_shipgate.schemas.capabilities import CapabilityLockFileV1
from agents_shipgate.schemas.capability_reconciliation import (
    CapabilityIntentPolicyV1,
    CapabilityObservationBundleV1,
    NetworkTargetV1,
    PrincipalBindingV1,
    ReconciliationInputBindingV1,
)
from agents_shipgate.schemas.manifest import AgentsShipgateManifest

REPO_ROOT = Path(__file__).resolve().parent.parent
SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64


def _manifest() -> AgentsShipgateManifest:
    return AgentsShipgateManifest.model_validate(
        {
            "version": "0.1",
            "project": {"name": "reconciliation"},
            "agent": {
                "name": "support-agent",
                "declared_purpose": ["Support account workflows."],
            },
            "environment": {"target": "local"},
            "tool_sources": [
                {"id": "support_api", "type": "openapi", "path": "support.json"}
            ],
        }
    )


def _tool(
    name: str,
    *,
    provider: str = "support_api",
    operation: str | None = None,
    credential_mode: str = "delegated",
) -> Tool:
    return Tool(
        id=f"tool:{provider}:{name}",
        name=name,
        description=f"{name} test tool",
        source_type="openapi",
        source_id=provider,
        source_ref="support.json",
        operation=operation or name,
        auth=AuthInfo(
            type="oauth",
            credential_mode=credential_mode,
            source="manifest",
            scopes=["cases:read"],
        ),
        extraction_confidence="high",
    )


def _lock(
    tools: list[Tool] | None = None,
    *,
    source_warning_count: int = 0,
    plugins_enabled: bool = True,
) -> CapabilityLockFileV1:
    return build_capability_lock(
        _manifest(),
        agent=Agent(id="agent:reconcile", name="support-agent"),
        tools=tools or [_tool("cases.search")],
        config_path=Path("shipgate.yaml"),
        manifest_dir=Path("."),
        cli_version="test",
        source_count=1,
        source_warning_count=source_warning_count,
        plugins_enabled=plugins_enabled,
    )


def _principal(
    *,
    kind: str = "service_account",
    credential_mode: str = "delegated",
) -> PrincipalBindingV1:
    return PrincipalBindingV1(
        kind=kind,
        credential_mode=credential_mode,
        auth_type="oauth",
        authority_source="manifest",
        authority_mode="scoped",
        scopes=("cases:read",),
    )


def _intent(
    lock: CapabilityLockFileV1,
    *,
    coverage: str = "closed_world",
    rules: list[dict[str, object]] | None = None,
) -> CapabilityIntentPolicyV1:
    fact = lock.capabilities[0]
    default_rules: list[dict[str, object]] = [
        {
            "id": "allow.cases.search",
            "effect": "allow",
            "target": {
                "kind": "tool",
                "capability_id": fact.id,
                "tool_name": fact.identity.tool_name,
            },
            "expected_principal": _principal().model_dump(mode="json"),
        }
    ]
    return CapabilityIntentPolicyV1.model_validate(
        {
            "subject": {"agent_id": "agent:reconcile", "environment": "local"},
            "owner": "platform-security",
            "reason": "Reviewed reconciliation fixture.",
            "coverage": {"tool": coverage},
            "rules": default_rules if rules is None else rules,
        }
    )


def _source(
    *,
    source_id: str = "current",
    policy_sha256: str | None = SHA_B,
    tool_coverage: str = "complete",
    network_coverage: str = "unknown",
    collection_mode: str = "evaluation",
) -> dict[str, object]:
    return {
        "id": source_id,
        "kind": "eval_run",
        "vendor": "fixture",
        "collection_mode": collection_mode,
        "run_id": source_id,
        "run_sha256": SHA_A,
        "policy_sha256": policy_sha256,
        "grant_inventory_coverage": {
            "tool": tool_coverage,
            "network": network_coverage,
        },
        "observation_collection_coverage": "complete_for_run",
    }


def _bundle(
    *,
    sources: list[dict[str, object]] | None = None,
    grants: list[dict[str, object]] | None = None,
    observations: list[dict[str, object]] | None = None,
) -> CapabilityObservationBundleV1:
    return CapabilityObservationBundleV1.model_validate(
        {
            "subject": {"agent_id": "agent:reconcile", "environment": "local"},
            "sources": sources or [_source()],
            "grants": grants or [],
            "observations": observations or [],
        }
    )


def _tool_target(name: str = "cases.search") -> dict[str, object]:
    return {"kind": "tool", "tool_name": name}


def _grant(
    target: dict[str, object] | None = None,
    *,
    row_id: str = "grant-1",
    source_id: str = "current",
    principal: PrincipalBindingV1 | None = None,
) -> dict[str, object]:
    return {
        "id": row_id,
        "source_id": source_id,
        "target": target or _tool_target(),
        "principal": (principal or _principal()).model_dump(mode="json"),
    }


def _observation(
    target: dict[str, object] | None = None,
    *,
    row_id: str = "observation-1",
    source_id: str = "current",
    outcome: str = "allowed",
    sequence: int = 0,
    principal: PrincipalBindingV1 | None = None,
) -> dict[str, object]:
    return {
        "id": row_id,
        "source_id": source_id,
        "sequence": sequence,
        "target": target or _tool_target(),
        "outcome": outcome,
        "principal": (principal or _principal()).model_dump(mode="json"),
    }


def _reconcile(
    lock: CapabilityLockFileV1,
    intent: CapabilityIntentPolicyV1,
    bundle: CapabilityObservationBundleV1,
    *,
    base: CapabilityObservationBundleV1 | None = None,
):
    return reconcile_capabilities(
        lock=lock,
        intent=intent,
        evidence_bundles=[bundle],
        base_evidence_bundles=[] if base is None else [base],
    )


def _result_for_tool(artifact, tool_name: str):
    return next(
        row
        for row in artifact.results
        if row.target.kind in {"tool", "mcp"} and row.target.tool_name == tool_name
    )


def test_strict_schemas_reject_raw_resource_data_invalid_hashes_and_conflicts() -> None:
    with pytest.raises(ValidationError, match="pattern"):
        NetworkTargetV1(domain="https://example.com/path?token=secret")

    with pytest.raises(ValidationError, match="sha256"):
        NetworkTargetV1(
            domain="example.com",
            granularity="exact",
            locator_sha256="/Users/alice/secret.txt",
        )

    lock = _lock()
    fact = lock.capabilities[0]
    with pytest.raises(ValidationError, match="conflicting overlapping intent rules"):
        _intent(
            lock,
            rules=[
                {
                    "id": "allow.search",
                    "effect": "allow",
                    "target": {
                        "kind": "tool",
                        "capability_id": fact.id,
                        "tool_name": "cases.search",
                    },
                },
                {
                    "id": "deny.search",
                    "effect": "deny",
                    "target": {
                        "kind": "tool",
                        "capability_id": fact.id,
                        "tool_name": "cases.search",
                    },
                },
            ],
        )


def test_aligned_result_is_deterministic_and_schema_valid() -> None:
    lock = _lock()
    intent = _intent(lock)
    bundle = _bundle(
        grants=[_grant()],
        observations=[_observation()],
    )

    first = _reconcile(lock, intent, bundle)
    reversed_bundle = _bundle(
        grants=list(reversed(bundle.model_dump(mode="json")["grants"])),
        observations=list(reversed(bundle.model_dump(mode="json")["observations"])),
    )
    second = _reconcile(lock, intent, reversed_bundle)

    assert render_capability_reconciliation_json(first) == (
        render_capability_reconciliation_json(second)
    )
    assert first.results[0].signals == ("aligned",)
    assert first.release_gate_effect is False
    schema = json.loads(
        (REPO_ROOT / "docs/capability-reconciliation-schema.v0.1.json").read_text(
            encoding="utf-8"
        )
    )
    jsonschema.validate(first.model_dump(mode="json"), schema)


def test_excess_authority_is_distinct_from_unclassified_grant() -> None:
    lock = _lock()
    closed = _intent(lock, rules=[])
    partial = _intent(lock, coverage="partial", rules=[])
    bundle = _bundle(grants=[_grant()], observations=[])

    excess = _result_for_tool(_reconcile(lock, closed, bundle), "cases.search")
    unclassified = _result_for_tool(_reconcile(lock, partial, bundle), "cases.search")

    assert "excess_authority" in excess.signals
    assert "declared_unintended" in excess.signals
    assert "policy_unclassified_grant" in unclassified.signals
    assert "excess_authority" not in unclassified.signals


def test_denied_unknown_tool_is_behavioral_drift_but_never_allowlisted() -> None:
    lock = _lock()
    intent = _intent(lock)
    bundle = _bundle(
        observations=[
            _observation(
                _tool_target("shell.execute"),
                outcome="denied",
            )
        ]
    )

    artifact = _reconcile(lock, intent, bundle)
    result = _result_for_tool(artifact, "shell.execute")

    assert {"behavioral_drift", "denied_attempt"} <= set(result.signals)
    actions = {
        proposal.action
        for proposal in artifact.candidate_policy_changes
        if proposal.target.kind == "tool" and proposal.target.tool_name == "shell.execute"
    }
    assert "investigate_runtime_route" in actions
    assert "investigate_denied_attempt" in actions
    assert "consider_minimal_grant" not in actions
    assert all(not proposal.auto_apply for proposal in artifact.candidate_policy_changes)


def test_declared_ungranted_is_broken_only_when_independently_intended() -> None:
    lock = _lock()
    bundle = _bundle()

    intended_artifact = _reconcile(lock, _intent(lock), bundle)
    intended = _result_for_tool(intended_artifact, "cases.search")
    partial_artifact = _reconcile(lock, _intent(lock, coverage="partial", rules=[]), bundle)
    partial = _result_for_tool(partial_artifact, "cases.search")

    assert "declared_ungranted" in intended.signals
    assert intended.disposition == "human_review"
    assert any(
        proposal.action == "consider_minimal_grant"
        for proposal in intended_artifact.candidate_policy_changes
    )
    assert "declared_ungranted" in partial.signals
    assert partial.disposition == "informational"
    assert all(
        proposal.action != "consider_minimal_grant"
        for proposal in partial_artifact.candidate_policy_changes
    )


def test_granted_unobserved_is_informational_and_has_no_removal_proposal() -> None:
    lock = _lock()
    artifact = _reconcile(lock, _intent(lock), _bundle(grants=[_grant()]))
    result = _result_for_tool(artifact, "cases.search")

    assert result.signals == ("granted_unobserved",)
    assert result.disposition == "informational"
    assert not artifact.candidate_policy_changes


def test_allowed_without_complete_grant_is_enforcement_contradiction() -> None:
    lock = _lock()
    bundle = _bundle(
        observations=[_observation(outcome="allowed")],
    )
    artifact = _reconcile(lock, _intent(lock), bundle)
    result = _result_for_tool(artifact, "cases.search")

    assert "allowed_ungranted" in result.signals
    assert "declared_ungranted" in result.signals
    assert any(
        proposal.action == "inspect_enforcement_integrity"
        for proposal in artifact.candidate_policy_changes
    )


def test_incomplete_evidence_suppresses_absence_based_conclusions() -> None:
    lock = _lock(source_warning_count=1)
    intent = _intent(lock, coverage="partial", rules=[])
    bundle = _bundle(
        sources=[_source(tool_coverage="partial")],
        observations=[_observation(_tool_target("unknown.tool"), outcome="allowed")],
    )

    artifact = _reconcile(lock, intent, bundle)
    result = _result_for_tool(artifact, "unknown.tool")

    assert result.membership.intended == "unknown"
    assert result.membership.declared == "unknown"
    assert result.membership.granted == "unknown"
    assert "behavioral_drift" not in result.signals
    assert "allowed_ungranted" not in result.signals
    gap_kinds = {gap.kind for gap in artifact.evidence_gaps}
    assert {
        "incomplete_intent_inventory",
        "incomplete_declared_inventory",
        "incomplete_grant_inventory",
    } <= gap_kinds


def test_missing_policy_hash_isolated_and_cannot_prove_missing_grants() -> None:
    lock = _lock()
    bundle = _bundle(
        sources=[_source(policy_sha256=None, tool_coverage="complete")],
        observations=[_observation()],
    )

    artifact = _reconcile(lock, _intent(lock), bundle)
    result = _result_for_tool(artifact, "cases.search")

    assert result.membership.granted == "unknown"
    assert "allowed_ungranted" not in result.signals
    assert "missing_policy_hash" in {gap.kind for gap in artifact.evidence_gaps}


def test_ambiguous_tool_name_is_not_guessed_or_called_behavioral_drift() -> None:
    lock = _lock(
        [
            _tool("shared.search", provider="one", operation="search-one"),
            _tool("shared.search", provider="two", operation="search-two"),
        ]
    )
    intent = _intent(lock, rules=[])
    bundle = _bundle(
        observations=[_observation(_tool_target("shared.search"), outcome="attempted")]
    )

    artifact = _reconcile(lock, intent, bundle)
    raw = next(
        row
        for row in artifact.results
        if row.target.kind == "tool"
        and row.target.tool_name == "shared.search"
        and row.target.capability_id is None
    )

    assert raw.membership.declared == "unknown"
    assert "behavioral_drift" not in raw.signals
    assert "ambiguous_target" in {gap.kind for gap in artifact.evidence_gaps}


def test_resource_intent_and_grants_reconcile_without_claiming_static_declaration() -> None:
    lock = _lock()
    network_target = {
        "kind": "network",
        "domain": "API.EXAMPLE.COM.",
        "scheme": "https",
        "resource_class": "customer_api",
        "granularity": "class",
    }
    intent = _intent(
        lock,
        rules=[
            {
                "id": "deny.customer-api",
                "effect": "deny",
                "target": network_target,
            }
        ],
    )
    bundle = _bundle(
        sources=[_source(network_coverage="complete")],
        grants=[_grant(network_target)],
    )

    artifact = _reconcile(lock, intent, bundle)
    result = next(row for row in artifact.results if row.target.kind == "network")

    assert result.target.domain == "api.example.com"
    assert result.membership.intended == "absent"
    assert result.membership.granted == "present"
    assert result.membership.declared == "unknown"
    assert "excess_authority" in result.signals
    assert "unsupported_resource_comparison" in {
        gap.kind for gap in artifact.evidence_gaps
    }


def test_principal_change_requires_complete_base_snapshot() -> None:
    lock = _lock()
    current_principal = _principal(kind="agent_principal")
    previous_principal = _principal(kind="service_account")
    intent = _intent(
        lock,
        rules=[
            {
                "id": "allow.cases.search",
                "effect": "allow",
                "target": _tool_target(),
                "expected_principal": current_principal.model_dump(mode="json"),
            }
        ],
    )
    current = _bundle(grants=[_grant(principal=current_principal)])
    base = _bundle(
        sources=[_source(source_id="base", policy_sha256=SHA_C)],
        grants=[
            _grant(
                row_id="base-grant",
                source_id="base",
                principal=previous_principal,
            )
        ],
    )

    without_base = _result_for_tool(_reconcile(lock, intent, current), "cases.search")
    with_base = _result_for_tool(
        _reconcile(lock, intent, current, base=base), "cases.search"
    )

    assert "principal_changed" not in without_base.signals
    assert "principal_changed" in with_base.signals
    assert with_base.authority_delta is not None
    assert with_base.authority_delta.before.kind == "service_account"
    assert with_base.authority_delta.after.kind == "agent_principal"


def test_learning_mode_never_creates_executable_or_observation_only_grant() -> None:
    lock = _lock()
    bundle = _bundle(
        sources=[_source(collection_mode="learning")],
        observations=[
            _observation(_tool_target("unexpected.delete"), outcome="denied")
        ],
    )

    artifact = _reconcile(lock, _intent(lock), bundle)

    assert all(proposal.actor == "human" for proposal in artifact.candidate_policy_changes)
    assert all(proposal.requires_human_review for proposal in artifact.candidate_policy_changes)
    assert all(not proposal.safe_to_attempt for proposal in artifact.candidate_policy_changes)
    assert all(not proposal.auto_apply for proposal in artifact.candidate_policy_changes)
    assert all(
        proposal.action != "consider_minimal_grant"
        for proposal in artifact.candidate_policy_changes
        if proposal.target.kind == "tool"
        and proposal.target.tool_name == "unexpected.delete"
    )


def test_input_contracts_validate_against_generated_schemas() -> None:
    lock = _lock()
    intent = _intent(lock)
    bundle = _bundle(grants=[_grant()], observations=[_observation()])
    intent_schema = json.loads(
        (REPO_ROOT / "docs/capability-intent-schema.v0.1.json").read_text(
            encoding="utf-8"
        )
    )
    observation_schema = json.loads(
        (REPO_ROOT / "docs/capability-observation-schema.v0.1.json").read_text(
            encoding="utf-8"
        )
    )

    jsonschema.validate(intent.model_dump(mode="json"), intent_schema)
    jsonschema.validate(bundle.model_dump(mode="json"), observation_schema)


def test_documented_example_replays_byte_identically() -> None:
    examples = REPO_ROOT / "docs/examples"
    lock = CapabilityLockFileV1.model_validate(
        json.loads((examples / "capability-lock.v0.3.example.json").read_text())
    )
    intent = load_capability_intent_policy(
        examples / "capability-intent.v0.1.example.json"
    )
    evidence = load_capability_observation_bundle(
        examples / "capability-observations.v0.1.example.json"
    )
    base = load_capability_observation_bundle(
        examples / "capability-observations-base.v0.1.example.json"
    )
    expected = json.loads(
        (examples / "capability-reconciliation.v0.1.example.json").read_text()
    )
    input_specs = [
        ("capability_lock", examples / "capability-lock.v0.3.example.json", "0.3"),
        ("intent_policy", examples / "capability-intent.v0.1.example.json", "0.1"),
        ("evidence", examples / "capability-observations.v0.1.example.json", "0.1"),
        (
            "base_evidence",
            examples / "capability-observations-base.v0.1.example.json",
            "0.1",
        ),
    ]
    bindings = [
        ReconciliationInputBindingV1(
            role=role,
            path=path.relative_to(REPO_ROOT).as_posix(),
            sha256=f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}",
            schema_version=version,
        )
        for role, path, version in input_specs
    ]

    artifact = reconcile_capabilities(
        lock=lock,
        intent=intent,
        evidence_bundles=[evidence],
        base_evidence_bundles=[base],
        input_bindings=bindings,
    )

    assert artifact.model_dump(mode="json") == expected
    assert all(
        proposal.auto_apply is False
        and proposal.safe_to_attempt is False
        and proposal.requires_human_review is True
        for proposal in artifact.candidate_policy_changes
    )


def test_prototype_script_writes_local_artifact_and_exits_zero_for_signals(
    tmp_path: Path,
) -> None:
    lock = _lock()
    intent = _intent(lock, rules=[])
    bundle = _bundle(grants=[_grant()])
    lock_path = tmp_path / "capabilities.lock.json"
    intent_path = tmp_path / "intent.json"
    evidence_path = tmp_path / "evidence.json"
    out_path = tmp_path / "result.json"
    lock_path.write_text(render_capability_lock_json(lock), encoding="utf-8")
    intent_path.write_text(intent.model_dump_json(indent=2) + "\n", encoding="utf-8")
    evidence_path.write_text(bundle.model_dump_json(indent=2) + "\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/prototype_capability_reconciliation.py"),
            "--workspace",
            str(tmp_path),
            "--lock",
            lock_path.name,
            "--intent",
            intent_path.name,
            "--evidence",
            evidence_path.name,
            "--out",
            out_path.name,
            "--json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    stdout_payload = json.loads(completed.stdout)
    file_payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert stdout_payload == file_payload
    assert stdout_payload["release_gate_effect"] is False
    assert stdout_payload["summary"]["signal_counts"]["excess_authority"] == 1
    assert all(
        not proposal["auto_apply"]
        for proposal in stdout_payload["candidate_policy_changes"]
    )


def test_prototype_script_rejects_outside_workspace_and_stale_lock(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-intent.json"
    outside.write_text("{}", encoding="utf-8")
    stale_lock = _lock().model_dump(mode="json")
    stale_lock["capability_lock_schema_version"] = "0.2"
    lock_path = tmp_path / "stale.lock.json"
    evidence_path = tmp_path / "evidence.json"
    lock_path.write_text(json.dumps(stale_lock), encoding="utf-8")
    evidence_path.write_text(_bundle().model_dump_json(), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/prototype_capability_reconciliation.py"),
            "--workspace",
            str(tmp_path),
            "--lock",
            lock_path.name,
            "--intent",
            "../outside-intent.json",
            "--evidence",
            evidence_path.name,
            "--out",
            "result.json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 3
    assert "outside manifest directory" in completed.stderr

    intent_path = tmp_path / "intent.json"
    intent_path.write_text(_intent(_lock()).model_dump_json(), encoding="utf-8")
    stale = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/prototype_capability_reconciliation.py"),
            "--workspace",
            str(tmp_path),
            "--lock",
            lock_path.name,
            "--intent",
            intent_path.name,
            "--evidence",
            evidence_path.name,
            "--out",
            "result.json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert stale.returncode == 3
    assert "requires capability lock v0.3" in stale.stderr
    assert "agents-shipgate capability export" in stale.stderr
