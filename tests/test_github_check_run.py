from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from scripts import github_check_run
from scripts.github_check_run import (
    CHECK_RUN_POLICIES,
    MAX_ANNOTATIONS,
    PAYLOAD_FILENAME,
    CheckRunPolicyError,
    annotations_from_pr_projection,
    annotations_from_sarif,
    build_check_run_payload,
    conclusion_for,
    normalize_check_run_policy,
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


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        ("mergeable", "success"),
        ("blocked", "failure"),
        ("unknown", "failure"),
        ("human_review_required", "neutral"),
        ("insufficient_evidence", "neutral"),
        ("future", "neutral"),
        # No verdict is the `unknown` this policy fails (#1032).
        (None, "failure"),
        ("", "failure"),
        ("  ", "failure"),
    ],
)
def test_blocked_fails_conclusion_mapping(verdict, expected):
    assert conclusion_for(verdict, policy="blocked-fails") == expected


@pytest.mark.parametrize(
    ("can_merge_without_human", "expected"),
    [
        (True, "success"),
        ("true", "success"),
        (False, "failure"),
        ("false", "failure"),
        (None, "failure"),
    ],
)
def test_require_mergeable_conclusion_mapping(can_merge_without_human, expected):
    assert (
        conclusion_for(
            "mergeable",
            policy="require-mergeable",
            can_merge_without_human=can_merge_without_human,
        )
        == expected
    )


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


def test_payload_uses_pr_projection_annotations_before_sarif():
    report = {
        "release_decision": {"blockers": [{"id": "F1"}]},
        "findings": [
            {
                "id": "F1",
                "check_id": "SHIP-ACTION-APPROVAL-REMOVED",
                "title": "Approval removed",
                "severity": "critical",
                "recommendation": "Restore approval.",
                "source": {"path": "api.yaml", "start_line": 12},
            }
        ],
    }
    verifier = {
        "merge_verdict": "blocked",
        "can_merge_without_human": False,
        "release_decision": {"blockers": [{"id": "F1"}]},
    }
    sarif = _sarif([_result(uri="other.yaml", line=99)])

    payload = build_check_run_payload(
        verifier=verifier,
        report=report,
        sarif=sarif,
        summary_markdown="blocked",
    )

    assert payload["conclusion"] == "failure"
    assert payload["output"]["annotations"][0]["path"] == "api.yaml"
    assert payload["output"]["annotations"][0]["title"] == "SHIP-ACTION-APPROVAL-REMOVED"


def test_pr_projection_annotations_match_action_projection_shape():
    report = {
        "release_decision": {"review_items": [{"id": "F2"}]},
        "findings": [
            {
                "id": "F2",
                "check_id": "SHIP-POLICY-APPROVAL-MISSING",
                "title": "Approval missing",
                "severity": "high",
                "recommendation": "Declare approval.",
                "policy_evidence_source": {"path": "shipgate.yaml", "start_line": 20},
            }
        ],
    }

    annotations = annotations_from_pr_projection(report, {"merge_verdict": "blocked"})

    assert annotations == [
        {
            "path": "shipgate.yaml",
            "start_line": 20,
            "end_line": 20,
            "annotation_level": "failure",
            "message": "Declare approval. Source: shipgate.yaml",
            "title": "SHIP-POLICY-APPROVAL-MISSING",
        }
    ]


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


# --- #1032: an unknown policy is an error, and no verdict fails the strict ones ---

REPO_ROOT = Path(__file__).resolve().parent.parent


def _documented_policies() -> set[str]:
    """Every `check_run_policy` spelling a reader is shown, hyphen or underscore.

    The input's own description, `.well-known`'s enumeration, and every
    `check_run_policy:` value in the README, docs, examples and the workflow
    `init --ci` emits.
    """

    action = yaml.safe_load((REPO_ROOT / "action.yml").read_text(encoding="utf-8"))
    description = action["inputs"]["check_run_policy"]["description"]
    match = re.search(r"policy:\s*([a-z_,\s-]+)\.", description)
    assert match, description
    documented = {
        token.strip().removeprefix("or ").strip()
        for token in match.group(1).split(",")
        if token.strip()
    }
    well_known = json.loads((REPO_ROOT / ".well-known/agents-shipgate.json").read_text("utf-8"))
    documented |= set(well_known["check_run_policies"])
    documented.add(well_known["github_action_pr_workflow"]["branch_protection_check_run_policy"])
    roots = ("README.md", "docs", "examples", "src/agents_shipgate/cli/discovery")
    for root in roots:
        top = REPO_ROOT / root
        for path in [top] if top.is_file() else sorted(top.rglob("*")):
            if path.suffix not in {".md", ".yml", ".yaml", ".py"} or "changelog" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            documented |= set(re.findall(r"check_run_policy:\s*`?([a-z][a-z_-]*)", text))
    return documented


def test_every_documented_policy_spelling_is_accepted_as_itself():
    documented = _documented_policies()
    assert documented == set(CHECK_RUN_POLICIES), (
        "The docs and the script disagree about check_run_policy: only documented "
        f"{sorted(documented - CHECK_RUN_POLICIES)}, only in the script "
        f"{sorted(CHECK_RUN_POLICIES - documented)}."
    )
    for policy in documented:
        assert normalize_check_run_policy(policy) == policy


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "advisory"),
        ("", "advisory"),
        ("  \n", "advisory"),
        (" require-mergeable\n", "require-mergeable"),
        ("Blocked-Fails", "blocked-fails"),
    ],
)
def test_an_unset_policy_is_the_default_and_spacing_or_case_is_not_a_new_value(raw, expected):
    assert normalize_check_run_policy(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["require_mergeable", "blocked_fails", "strict", "requiremergeable", "advisory-only", "true"],
)
def test_an_unknown_policy_raises_instead_of_falling_back_to_advisory(raw):
    with pytest.raises(CheckRunPolicyError, match="Unknown check_run_policy"):
        normalize_check_run_policy(raw)
    # Every entry point that takes a policy refuses it the same way.
    with pytest.raises(CheckRunPolicyError):
        conclusion_for("human_review_required", policy=raw, can_merge_without_human=False)
    with pytest.raises(CheckRunPolicyError):
        build_check_run_payload(
            verifier={"merge_verdict": "human_review_required"},
            summary_markdown="",
            check_run_policy=raw,
        )


@pytest.mark.parametrize(
    ("raw", "suggested"),
    [("require_mergeable", "require-mergeable"), ("blocked_fails", "blocked-fails")],
)
def test_an_underscore_typo_names_the_documented_spelling(raw, suggested):
    with pytest.raises(CheckRunPolicyError, match=f"Did you mean '{suggested}'"):
        normalize_check_run_policy(raw)


def test_a_non_string_policy_raises():
    with pytest.raises(CheckRunPolicyError):
        normalize_check_run_policy(["require-mergeable"])


@pytest.mark.parametrize(
    ("policy", "expected"),
    [("advisory", "neutral"), ("blocked-fails", "failure"), ("require-mergeable", "failure")],
)
def test_a_missing_verifier_fails_under_the_strict_policies(policy, expected):
    payload = build_check_run_payload(
        verifier=None, report=None, sarif=None, summary_markdown="", check_run_policy=policy
    )
    assert payload["conclusion"] == expected
    assert payload["output"]["title"] == "merge_verdict: unknown"
    stated = "No readable `verifier.json` was produced" in payload["output"]["summary"]
    assert stated is (expected == "failure")


@pytest.mark.parametrize("policy", ["blocked-fails", "require-mergeable"])
@pytest.mark.parametrize("verdict", [None, "", "  "])
def test_a_verifier_without_a_merge_verdict_fails_under_the_strict_policies(policy, verdict):
    verifier = {"release_decision": {"blockers": []}}
    if verdict is not None:
        verifier["merge_verdict"] = verdict
    payload = build_check_run_payload(
        verifier=verifier, summary_markdown="Review.", check_run_policy=policy
    )
    assert payload["conclusion"] == "failure"
    # The summary says why, as it does when verifier.json is missing.
    assert payload["output"]["summary"].startswith(
        "_`verifier.json` carries no `merge_verdict`, so this run states no merge verdict. "
        f"The `{policy}` check-run policy fails the check._\n\nReview."
    )


def test_the_no_verdict_note_appears_only_on_a_failing_check():
    """`require-mergeable` decides on `can_merge_without_human`, not the verdict."""

    passing = build_check_run_payload(
        verifier={"can_merge_without_human": True},
        summary_markdown="Review.",
        check_run_policy="require-mergeable",
    )
    assert passing["conclusion"] == "success" and passing["output"]["summary"] == "Review."
    advisory = build_check_run_payload(verifier={}, summary_markdown="Review.")
    assert advisory["conclusion"] == "neutral" and advisory["output"]["summary"] == "Review."
    stated = build_check_run_payload(
        verifier={"merge_verdict": "human_review_required", "can_merge_without_human": False},
        summary_markdown="Review.",
        check_run_policy="require-mergeable",
    )
    assert stated["conclusion"] == "failure" and stated["output"]["summary"] == "Review."


@pytest.mark.parametrize(
    ("mode", "policy", "refused"),
    [
        ("scan", "blocked-fails", True),
        ("scan", "require-mergeable", True),
        (" scan\n", "require-mergeable", True),
        ("scan", "advisory", False),
        ("verify", "blocked-fails", False),
        ("verify", "require-mergeable", False),
        ("", "require-mergeable", False),
        (None, "blocked-fails", False),
    ],
)
def test_a_strict_policy_is_refused_under_scan_mode(mode, policy, refused):
    """A scan writes no verifier.json, so a strict policy would fail every scan run."""

    if refused:
        with pytest.raises(CheckRunPolicyError, match="verify_mode scan does not write"):
            github_check_run.check_policy_for_mode(policy, mode)
    else:
        github_check_run.check_policy_for_mode(policy, mode)


def _main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: str, *argv: str, mode: str = ""
) -> int:
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("CHECK_RUN_POLICY", policy)
    monkeypatch.setenv("VERIFY_MODE", mode)
    monkeypatch.delenv("CHECK_RUN_NAME", raising=False)
    return github_check_run.main(list(argv))


@pytest.mark.parametrize("argv", [(), ("--check-policy",)])
def test_the_action_steps_refuse_a_strict_policy_with_scan_mode(tmp_path, monkeypatch, capsys, argv):
    (tmp_path / "report.json").write_text(
        json.dumps({"release_decision": {"decision": "passed"}}), encoding="utf-8"
    )
    assert _main(tmp_path, monkeypatch, "blocked-fails", *argv, mode="scan") == 2
    assert not (tmp_path / PAYLOAD_FILENAME).exists()
    assert "verify_mode scan does not write" in capsys.readouterr().err
    assert _main(tmp_path, monkeypatch, "advisory", *argv, mode="scan") == 0


@pytest.mark.parametrize("policy", ["blocked-fails", "require-mergeable"])
def test_the_action_step_fails_the_check_when_verify_wrote_no_verifier(
    tmp_path, monkeypatch, policy
):
    assert _main(tmp_path, monkeypatch, policy) == 0
    payload = json.loads((tmp_path / PAYLOAD_FILENAME).read_text(encoding="utf-8"))
    assert payload["conclusion"] == "failure"


def test_the_action_step_refuses_an_unknown_policy_and_writes_no_payload(
    tmp_path, monkeypatch, capsys
):
    (tmp_path / "verifier.json").write_text(
        json.dumps({"merge_verdict": "human_review_required", "can_merge_without_human": False}),
        encoding="utf-8",
    )
    assert _main(tmp_path, monkeypatch, "require_mergeable") == 2
    assert not (tmp_path / PAYLOAD_FILENAME).exists()
    assert "Did you mean 'require-mergeable'" in capsys.readouterr().err


@pytest.mark.parametrize(("policy", "code"), [("require-mergeable", 0), ("", 0), ("strict", 2)])
def test_check_policy_validates_without_reading_or_writing_artifacts(
    tmp_path, monkeypatch, policy, code
):
    (tmp_path / "verifier.json").write_text("not json", encoding="utf-8")
    assert _main(tmp_path, monkeypatch, policy, "--check-policy") == code
    assert sorted(path.name for path in tmp_path.iterdir()) == ["verifier.json"]


def test_action_validates_the_policy_before_the_scan_runs():
    """The input is checked by the script that owns the vocabulary, before use."""

    action = yaml.safe_load((REPO_ROOT / "action.yml").read_text(encoding="utf-8"))
    steps = {step["name"]: index for index, step in enumerate(action["runs"]["steps"])}
    name = "Validate Agents Shipgate check-run policy"
    assert steps[name] < steps["Run Agents Shipgate"]
    validate = action["runs"]["steps"][steps[name]]
    assert validate["if"] == "${{ inputs.check_run == 'true' }}"
    assert validate["env"]["CHECK_RUN_POLICY"] == "${{ inputs.check_run_policy }}"
    assert 'scripts/github_check_run.py" --check-policy' in validate["run"]
    # Both steps that read the policy see the mode it is checked against.
    payload = action["runs"]["steps"][steps["Build Agents Shipgate check-run payload"]]
    for step in (validate, payload):
        assert step["env"]["VERIFY_MODE"] == "${{ inputs.verify_mode }}"
