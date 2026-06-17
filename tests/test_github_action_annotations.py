from __future__ import annotations

import json
from pathlib import Path

from scripts.github_action_annotations import build_annotations, emit_github_annotations


def test_annotations_select_source_backed_blockers_and_review_items(
    tmp_path: Path,
    capsys,
) -> None:
    output_dir = tmp_path / "agents-shipgate-reports"
    output_dir.mkdir()
    (output_dir / "report.json").write_text(
        json.dumps(
            {
                "release_decision": {
                    "blockers": [{"id": "F1"}],
                    "review_items": [{"id": "F2"}, {"id": "F3"}],
                },
                "findings": [
                    {
                        "id": "F1",
                        "check_id": "SHIP-ACTION-APPROVAL-REMOVED",
                        "title": "Approval removed",
                        "severity": "critical",
                        "recommendation": "Restore approval.",
                        "source": {
                            "type": "openapi",
                            "path": "api.yaml",
                            "start_line": 12,
                            "pointer": "/paths/~1refunds/post",
                        },
                    },
                    {
                        "id": "F2",
                        "check_id": "SHIP-POLICY-APPROVAL-MISSING",
                        "title": "Approval missing",
                        "severity": "high",
                        "recommendation": "Declare approval.",
                        "source": {"type": "action_surface", "ref": "action"},
                        "policy_evidence_source": {
                            "type": "manifest",
                            "path": "shipgate.yaml",
                            "start_line": 20,
                            "pointer": "/policies/require_approval_for_tools",
                        },
                    },
                    {
                        "id": "F3",
                        "check_id": "SHIP-INVENTORY-NOT-ENUMERABLE",
                        "title": "No source",
                        "severity": "high",
                        "recommendation": "Add a source.",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = build_annotations(output_dir, limit=10)

    assert payload["pr_projection_schema_version"] == "0.1"
    assert payload["source_verifier"].endswith("verifier.json")
    assert [item["check_id"] for item in payload["annotations"]] == [
        "SHIP-ACTION-APPROVAL-REMOVED",
        "SHIP-POLICY-APPROVAL-MISSING",
    ]
    assert payload["annotations"][0]["selector"] == "api.yaml#/paths/~1refunds/post"
    assert payload["annotations"][1]["path"] == "shipgate.yaml"
    assert payload["omitted"]["no_source"] == 1

    emit_github_annotations(payload)
    emitted = capsys.readouterr().out
    assert "::error file=api.yaml,line=12" in emitted
    assert "title=SHIP-ACTION-APPROVAL-REMOVED%3A Approval removed" in emitted
    assert "Source: api.yaml#/paths/~1refunds/post" in emitted


def test_annotations_respect_limit(tmp_path: Path) -> None:
    output_dir = tmp_path / "agents-shipgate-reports"
    output_dir.mkdir()
    findings = [
        {
            "id": f"F{index}",
            "check_id": f"SHIP-TEST-{index}",
            "title": "Finding",
            "severity": "high",
            "recommendation": "Fix.",
            "source": {"type": "mcp", "path": "tools.json", "pointer": f"/{index}"},
        }
        for index in range(3)
    ]
    (output_dir / "report.json").write_text(
        json.dumps({"release_decision": {}, "findings": findings}),
        encoding="utf-8",
    )

    payload = build_annotations(output_dir, limit=2)

    assert len(payload["annotations"]) == 2
    assert payload["omitted"]["limit"] == 1

    zero_payload = build_annotations(output_dir, limit=0)

    assert zero_payload["annotations"] == []
    assert zero_payload["omitted"]["limit"] == 3


def test_annotations_include_source_backed_capability_changes(tmp_path: Path) -> None:
    output_dir = tmp_path / "agents-shipgate-reports"
    output_dir.mkdir()
    (output_dir / "report.json").write_text(
        json.dumps({"release_decision": {}, "findings": []}),
        encoding="utf-8",
    )
    (output_dir / "verifier.json").write_text(
        json.dumps(
            {
                "capability_review": {
                    "top_changes": [
                        {
                            "id": "cap-refund",
                            "change_type": "action_added",
                            "subject_kind": "action",
                            "subject": "stripe.create_refund",
                            "impact": "blocks_release",
                            "rationale": "Action added: stripe.create_refund",
                            "source_path": "api.yaml",
                            "source_start_line": 42,
                            "related_finding_ids": ["F-refund"],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    payload = build_annotations(output_dir, limit=10)

    assert len(payload["annotations"]) == 1
    annotation = payload["annotations"][0]
    assert annotation["check_id"] == "SHIP-CAPABILITY-CHANGE"
    assert annotation["path"] == "api.yaml"
    assert annotation["start_line"] == 42
    assert annotation["level"] == "error"
    assert annotation["merge_impact"] == "blocks_release"
    assert annotation["capability_subject"] == "action:stripe.create_refund"
