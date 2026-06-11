from __future__ import annotations

import json

import pytest

from scripts.github_check_run import (
    MAX_ANNOTATIONS,
    annotations_from_sarif,
    build_check_run_payload,
    conclusion_for,
    title_for,
)


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        ("mergeable", "success"),
        ("blocked", "failure"),
        ("human_review_required", "neutral"),
        ("insufficient_evidence", "neutral"),
        ("unknown", "neutral"),
        ("", "neutral"),
        (None, "neutral"),
        ("some_future_verdict", "neutral"),
    ],
)
def test_conclusion_mapping(verdict, expected):
    assert conclusion_for(verdict) == expected


def test_title_includes_blocker_count_when_blocked():
    assert title_for("blocked", blocker_count=2) == "merge_verdict: blocked (2 blockers)"
    assert title_for("blocked", blocker_count=1) == "merge_verdict: blocked (1 blocker)"
    assert title_for("mergeable") == "merge_verdict: mergeable"
    assert title_for(None) == "merge_verdict: unknown"


def _sarif(results: list[dict]) -> dict:
    return {"runs": [{"results": results}]}


def _result(
    *,
    uri: str = "shipgate.yaml",
    line: int = 7,
    level: str = "error",
    rule: str = "SHIP-POLICY-APPROVAL-MISSING",
    text: str = "stripe.create_refund lacks a declared approval policy",
) -> dict:
    return {
        "ruleId": rule,
        "level": level,
        "message": {"text": text},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": uri},
                    "region": {"startLine": line},
                }
            }
        ],
    }


def test_annotations_map_sarif_levels():
    sarif = _sarif(
        [
            _result(level="error"),
            _result(level="warning", line=9),
            _result(level="note", line=11),
        ]
    )
    annotations = annotations_from_sarif(sarif)
    assert [a["annotation_level"] for a in annotations] == [
        "failure",
        "warning",
        "notice",
    ]
    assert annotations[0]["path"] == "shipgate.yaml"
    assert annotations[0]["start_line"] == 7
    assert annotations[0]["end_line"] == 7
    assert annotations[0]["title"] == "SHIP-POLICY-APPROVAL-MISSING"


def test_annotations_capped_at_checks_api_limit():
    sarif = _sarif([_result(line=i + 1) for i in range(MAX_ANNOTATIONS + 25)])
    annotations = annotations_from_sarif(sarif)
    assert len(annotations) == MAX_ANNOTATIONS


def test_annotations_skip_results_without_location_or_message():
    sarif = _sarif(
        [
            {"ruleId": "X", "level": "error", "message": {"text": "no location"}},
            _result(text=""),
            _result(line=3),
        ]
    )
    annotations = annotations_from_sarif(sarif)
    assert len(annotations) == 1
    assert annotations[0]["start_line"] == 3


def test_payload_notes_truncated_annotations_in_summary():
    sarif = _sarif([_result(line=i + 1) for i in range(MAX_ANNOTATIONS + 5)])
    payload = build_check_run_payload(
        verifier={"merge_verdict": "blocked", "release_decision": {"blockers": [{}]}},
        sarif=sarif,
        summary_markdown="## Shipgate verdict\nblocked.",
    )
    assert payload["conclusion"] == "failure"
    assert payload["output"]["title"] == "merge_verdict: blocked (1 blocker)"
    assert f"{MAX_ANNOTATIONS} of {MAX_ANNOTATIONS + 5}" in payload["output"]["summary"]
    assert len(payload["output"]["annotations"]) == MAX_ANNOTATIONS


def test_payload_defaults_safe_when_artifacts_missing():
    payload = build_check_run_payload(
        verifier=None, sarif=None, summary_markdown=""
    )
    assert payload["conclusion"] == "neutral"
    assert payload["output"]["title"] == "merge_verdict: unknown"
    assert payload["output"]["annotations"] == []
    assert payload["output"]["summary"]


def test_payload_is_json_serializable_and_deterministic():
    sarif = _sarif([_result()])
    verifier = {"merge_verdict": "mergeable", "release_decision": {"blockers": []}}
    one = build_check_run_payload(
        verifier=verifier, sarif=sarif, summary_markdown="ok"
    )
    two = build_check_run_payload(
        verifier=verifier, sarif=sarif, summary_markdown="ok"
    )
    assert json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True)
