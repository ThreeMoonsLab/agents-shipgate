import json
from collections import Counter
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parent.parent


def _yaml(path: str):
    return yaml.safe_load((REPO_ROOT / path).read_text(encoding="utf-8"))


def test_root_self_gate_is_advisory_and_scans_codex_marketplace():
    manifest = _yaml("shipgate.yaml")

    assert manifest["project"]["name"] == "agents-shipgate"
    assert manifest["environment"]["target"] == "production_like"
    assert manifest["ci"]["mode"] == "advisory"
    assert manifest["ci"]["pr_comment"] is False
    assert manifest["ci"]["upload_artifact"] is True
    assert manifest["tool_sources"] == [
        {
            "id": "codex_plugin_marketplace",
            "type": "codex_plugin",
            "mode": "marketplace",
            "path": ".agents/plugins/marketplace.json",
        }
    ]


def test_self_gate_workflow_uses_local_action_in_advisory_verify_mode():
    text = (REPO_ROOT / ".github/workflows/agents-shipgate.yml").read_text(
        encoding="utf-8"
    )

    assert "fetch-depth: 0" in text
    assert "uses: ./" in text
    assert "config: shipgate.yaml" in text
    assert "ci_mode: advisory" in text
    assert 'upload_artifact: "true"' in text
    assert 'pr_comment: "false"' in text
    assert "permissions:\n  contents: read" in text


def test_governance_benchmark_catalog_covers_major_risk_classes():
    catalog = _yaml("benchmark/agent-pr-governance/cases.yaml")
    cases = catalog["cases"]

    assert catalog["schema_version"] == "0.1"
    assert len(cases) == 50

    counts = Counter(case["category"] for case in cases)
    assert counts == {
        "ci_workflow_privilege_expansion": 8,
        "dependency_supply_chain": 8,
        "mcp_tool_surface_risk": 8,
        "secret_data_exfiltration": 8,
        "agent_instruction_trust_root": 6,
        "infra_iam_expansion": 6,
        "benign_control": 6,
    }

    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids))
    valid_decisions = {"blocked", "review_required", "insufficient_evidence", "passed"}
    valid_verdicts = {
        "blocked",
        "human_review_required",
        "insufficient_evidence",
        "mergeable",
        "unknown",
    }
    valid_actors = {"human", "coding_agent"}
    for case in cases:
        assert case["decision"] in valid_decisions
        assert case["merge_verdict"] in valid_verdicts
        assert case["next_actor"] in valid_actors
        assert case["artifacts"], case["id"]
        assert case["evidence"], case["id"]


def test_agent_trace_event_schema_validates_observable_event():
    schema = json.loads(
        (REPO_ROOT / "docs/agent-trace-event-schema.v0.1.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)

    required = set(schema["required"])
    assert {
        "event_id",
        "run_id",
        "sequence",
        "event_type",
        "actor",
        "summary",
        "resource_effects",
        "artifact_refs",
    } <= required
    assert "chain_of_thought" not in schema["properties"]
    assert "thought" not in schema["properties"]

    sample = {
        "event_id": "evt-001",
        "run_id": "run-001",
        "sequence": 1,
        "event_type": "verification",
        "actor": "coding_agent",
        "summary": "Ran agents-shipgate verify and produced verifier.json.",
        "resource_effects": [{"verb": "read", "object": "shipgate.yaml"}],
        "artifact_refs": ["agents-shipgate-reports/verifier.json"],
    }
    Draft202012Validator(schema).validate(sample)


def test_agent_workflow_evidence_bundle_schema_validates_replay_intake():
    schema = json.loads(
        (REPO_ROOT / "docs/agent-workflow-evidence-bundle-schema.v0.1.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)

    sample = {
        "bundle_schema_version": "0.1",
        "id": "ci-oidc-write-added",
        "base_ref": "origin/main",
        "head_ref": "HEAD",
        "shipgate_manifest": "shipgate.yaml",
        "agent_trace": "agent-trace.jsonl",
        "verify_artifacts": {
            "verifier_json": "agents-shipgate-reports/verifier.json",
            "report_json": "agents-shipgate-reports/report.json",
            "pr_comment": "agents-shipgate-reports/pr-comment.md",
        },
        "expected": {
            "decision": "review_required",
            "merge_verdict": "human_review_required",
            "next_actor": "human",
        },
        "redaction_note": "No secrets or private chain-of-thought included.",
    }
    Draft202012Validator(schema).validate(sample)


def test_policy_pack_docs_require_tests_and_explain_contribution_rules():
    text = (REPO_ROOT / "docs/policy-packs.md").read_text(encoding="utf-8")

    assert "positive fixture" in text
    assert "negative fixture" in text
    assert "release_decision.contribution_rules[]" in text
    assert "Do not use an LLM to decide whether a policy pack passed" in text


def test_runtime_inventory_doc_preserves_static_default_boundary():
    text = (REPO_ROOT / "docs/runtime-inventory.md").read_text(encoding="utf-8")
    normalized = " ".join(text.split())

    assert "does not ship a runtime inventory command" in text
    assert "agents-shipgate scan" in text
    assert "agents-shipgate verify" in text
    assert "must never call runtime inventory implicitly" in normalized
    assert "must remain opt-in" in text


def test_gap_closure_doc_links_every_major_gap_artifact():
    text = (REPO_ROOT / "docs/product-hardening-gap-closure.md").read_text(
        encoding="utf-8"
    )

    for needle in (
        "../shipgate.yaml",
        "../.github/workflows/agents-shipgate.yml",
        "../benchmark/agent-pr-governance/",
        "policy-packs.md",
        "agent-trace-event-schema.v0.1.json",
        "agent-workflow-evidence-bundle-schema.v0.1.json",
        "runtime-inventory.md",
    ):
        assert needle in text
