"""#839: questions use reader facts and survive a maintained JSON round trip."""

from __future__ import annotations

import json

import pytest

from agents_shipgate.core.host_comparison import compare_host_inventories
from agents_shipgate.core.host_grants import build_host_boundary_snapshot
from agents_shipgate.report.host_comparison import host_comparison_lines, permission_guidance_lines
from agents_shipgate.schemas.host_comparison import HostComparison


def comparison(tmp_path, before, after, **kwargs):
    inventories = []
    for name, data in (("base", before), ("head", after)):
        root = tmp_path / name
        (root / ".claude").mkdir(parents=True)
        (root / ".claude/settings.json").write_text(json.dumps({"permissions": data}))
        inventories.append(build_host_boundary_snapshot(root).inventory)
    return compare_host_inventories(
        *inventories, head_kind="commit", base_commit="a" * 40, head_commit="b" * 40,
        **kwargs,
    )


@pytest.mark.parametrize(("before", "after", "case", "question"), [
    ({"allow": ["Bash(npm test *)"]}, {"allow": ["Bash(npm *)"]},
     "allow_widened", "beyond Bash(npm test *)"),
    ({"allow": ["Bash(npm *)"]}, {"allow": ["Bash(npm test *)"]},
     "allow_narrowed", "Is restricting"),
    ({"deny": ["Bash(npm publish *)"]}, {}, "declaration_removed", "remove the deny"),
    ({}, {"deny": ["Bash(npm publish *)"]}, "declaration_added", "add the deny"),
    ({"allow": ["Bash(npm *)"]}, {"deny": ["Bash(npm *)"]},
     "disposition_moved", "from allow to deny"),
])
def test_questions_and_roundtrip(tmp_path, before, after, case, question):
    result = comparison(tmp_path, before, after)
    guidance = [item.guidance for item in result.review.changes]
    assert len(guidance) == 1
    assert guidance[0].case == case
    assert question in guidance[0].question
    assert guidance[0].source == ".claude/settings.json"
    assert len(guidance[0].choices) == 3
    assert ("not an automatically safe fix" if before else "no previous declaration") in guidance[0].choices[1]
    reloaded = HostComparison.model_validate_json(result.model_dump_json())
    assert [item.guidance for item in reloaded.review.changes] == guidance
    assert host_comparison_lines(result) == host_comparison_lines(reloaded)
    assert host_comparison_lines(result, markdown=True) == host_comparison_lines(reloaded, markdown=True)


@pytest.mark.parametrize("context", [
    {"deny": ["Bash(npm publish *)"]},
    {"ask": ["Bash(npm *)"]},
    {"deny": ["Bash(npm * publish)"]},
    {"defaultMode": "bypassPermissions"},
])
def test_unchanged_context_withholds_choices(tmp_path, context):
    result = comparison(tmp_path,
                        {**context, "allow": ["Bash(npm test *)"]},
                        {**context, "allow": ["Bash(npm *)"]})
    guidance = result.review.changes[0].guidance
    assert guidance.case == "unavailable"
    assert guidance.question is None
    assert not guidance.choices
    assert "inventory contains" in guidance.limitations[0]


def test_unrelated_deny_does_not_hide_question(tmp_path):
    result = comparison(tmp_path,
                        {"allow": ["Bash(npm test *)"], "deny": ["Bash(git push *)"]},
                        {"allow": ["Bash(npm *)"], "deny": ["Bash(git push *)"]})
    assert result.review.changes[0].guidance.case == "allow_widened"


def test_unrelated_mcp_enablement_does_not_hide_shell_question(tmp_path):
    result = comparison(tmp_path,
                        {"allow": ["Bash(npm test *)"], "enableAllProjectMcpServers": True},
                        {"allow": ["Bash(npm *)"], "enableAllProjectMcpServers": True})
    assert result.review.changes[0].guidance.case == "allow_widened"


def test_redaction_never_publishes_raw_rules_in_guidance(tmp_path):
    result = comparison(tmp_path, {"allow": ["Bash(npm test *)"]},
                        {"allow": ["Bash(npm *)"]}, redact_permission_arguments=True)
    for change in result.review.changes:
        assert change.guidance.case == "unavailable"
        assert change.guidance.before_rule is None
        assert change.guidance.after_rule is None
        assert "npm" not in change.guidance.model_dump_json()


def test_no_change_no_guidance(tmp_path):
    result = comparison(tmp_path, {"allow": ["Bash(npm *)"]}, {"allow": ["Bash(npm *)"]})
    assert not result.review.changes


@pytest.mark.parametrize("rule", ["Bash(npm * test)", "Bash(npm test && curl example.invalid)",
                                  "Bash($(touch /tmp/never))", "Bash(npm `whoami`)"])
def test_unsupported_rule_withholds_choices(tmp_path, rule):
    result = comparison(tmp_path, {}, {"allow": [rule]})
    assert result.review.changes[0].guidance.case == "unavailable"
    assert not result.review.changes[0].guidance.choices


def test_guidance_never_displaces_leading_facts_or_coverage(tmp_path):
    result = comparison(tmp_path, {"allow": ["Bash(npm test *)"]}, {"allow": ["Bash(npm *)"]})
    old = host_comparison_lines(result, guidance_max_chars=0)
    new = host_comparison_lines(result, guidance_max_chars=2400)
    assert new[:len(old)] == old
    assert "Question:" in "\n".join(new[len(old):])
    assert new[:5] == old[:5]


def test_many_changes_are_bounded_with_exact_omission(tmp_path):
    result = comparison(tmp_path, {}, {"allow": [f"Bash(command{i} *)" for i in range(20)]})
    lines = permission_guidance_lines(result, max_chars=2400)
    text = "\n".join(lines)
    assert len(text) <= 2400
    shown = sum(line.startswith("- Change ") for line in lines)
    assert 0 < shown <= 2
    assert f"{20 - shown} guidance item(s) omitted" in text
    assert "current control permissions still apply" in text
    assert len(result.review.changes) == 20
    tiny = "\n".join(permission_guidance_lines(result, max_chars=150))
    assert "20 guidance item(s) omitted" in tiny
    assert "Choice:" not in tiny


def test_legacy_guidance_missing_is_not_reconstructed(tmp_path):
    result = comparison(tmp_path, {}, {"allow": ["Bash(npm *)"]})
    data = result.model_dump(mode="json")
    for change in data["review"]["changes"]:
        del change["guidance"]
    reloaded = HostComparison.model_validate(data)
    assert "records no supported raw rule evidence" in "\n".join(permission_guidance_lines(reloaded))
    assert "Question:" not in "\n".join(permission_guidance_lines(reloaded))


def test_guidance_never_changes_rows_or_inventory_digests(tmp_path, monkeypatch):
    from agents_shipgate.core import host_comparison

    first = tmp_path / "with"
    first.mkdir()
    actual = comparison(first, {"allow": ["Bash(npm test *)"]}, {"allow": ["Bash(npm *)"]})
    monkeypatch.setattr(host_comparison, "permission_review_guidance", lambda *args, **kwargs: None)
    second = tmp_path / "without"
    second.mkdir()
    old = comparison(second, {"allow": ["Bash(npm test *)"]}, {"allow": ["Bash(npm *)"]})
    actual_data, old_data = actual.model_dump(mode="json"), old.model_dump(mode="json")
    for change in actual_data["review"]["changes"]:
        change["guidance"] = None
    assert actual_data == old_data


def test_cli_json_pr_and_control_agree(tmp_path, monkeypatch):
    # Reuse the real two-commit fixture and documented CLI invocations, not a
    # model_construct shortcut that bypasses verifier validation.
    from test_host_diff_review_changes import SETTINGS, _diff, _repository, _verify

    from agents_shipgate.core import host_comparison
    from agents_shipgate.report.pr_comment import render_pr_comment
    from agents_shipgate.schemas.verifier import VerifierArtifact

    repo = _repository(tmp_path,
                       {SETTINGS: {"permissions": {"allow": ["Bash(npm test *)"]}}},
                       {SETTINGS: {"permissions": {"allow": ["Bash(npm *)"]}}})
    text, payload = _diff(repo)
    _, _, verifier = _verify(repo, tmp_path / "out")
    assert payload["review"] == verifier["host_comparison"]["review"]
    guidance = payload["review"]["changes"][0]["guidance"]
    question = guidance["question"]
    assert question in text
    comment = (tmp_path / "out/pr-comment.md").read_text()
    reloaded = VerifierArtifact.model_validate(verifier)
    assert render_pr_comment(reloaded, report=None, style="capability-review") == comment
    assert question in comment
    assert comment.index("This comparison grants no merge authority") < comment.index(question)
    assert len(comment) <= 6000
    first_five = comment.splitlines()[:5]
    monkeypatch.setattr(host_comparison, "permission_review_guidance", lambda *args, **kwargs: None)
    _, _, without = _verify(repo, tmp_path / "without")
    for key in ("control", "decision", "release_decision", "merge_verdict", "can_merge_without_human"):
        assert verifier[key] == without[key]
    assert first_five == (tmp_path / "without/pr-comment.md").read_text().splitlines()[:5]
    with pytest.raises(ValueError, match="Legacy verifier cannot claim permission review guidance"):
        VerifierArtifact.model_validate({**verifier, "verifier_schema_version": "0.20"})


def test_unread_input_has_no_question(tmp_path):
    from test_host_diff_review_changes import SETTINGS, _diff, _repository

    repo = _repository(tmp_path, {SETTINGS: {"permissions": {"allow": ["Bash(npm *)"]}}},
                       {SETTINGS: "{broken"})
    text, payload = _diff(repo)
    assert payload["comparison_status"] == "incomparable"
    assert payload["review"] is None
    assert "Permission review guidance:" not in text
    assert "Cannot compare" in text


def test_private_rule_evidence_is_never_republished(tmp_path):
    secret = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    result = comparison(tmp_path, {}, {"allow": [f"Bash(npm {secret})"]})
    # The existing unredacted rule rows are outside this projection. Guidance
    # must not repeat credential-shaped text even on that route.
    assert secret not in result.review.changes[0].guidance.model_dump_json()
    assert result.review.changes[0].guidance.case == "unavailable"


def test_incomplete_context_withholds_even_when_a_row_is_established(tmp_path):
    from agents_shipgate.core.host_comparison import host_comparison_review

    result = comparison(tmp_path, {}, {"allow": ["Bash(npm *)"]})
    limited = host_comparison_review(
        result.rows, base_commit="a" * 40, head_commit="b" * 40, head_kind="commit",
        before_grants=[], after_grants=[], incomplete_context=True,
    )
    assert limited.changes[0].guidance.case == "unavailable"
    assert "resolver is not established" in limited.changes[0].guidance.limitations[0]


def test_retaining_the_same_widening_does_not_confirm_a_correction(tmp_path):
    result = comparison(tmp_path, {"allow": ["Bash(npm test *)"]}, {"allow": ["Bash(npm *)"]})
    guidance = result.review.changes[0].guidance
    assert "delta remains visible" in guidance.choices[0]
    reread = HostComparison.model_validate_json(result.model_dump_json())
    assert reread.review.summary.widenings == result.review.summary.widenings
    assert reread.review.changes[0].guidance == guidance
    assert "complete" not in guidance.model_dump()


def test_fresh_non_shell_permission_changes_print_no_guidance_limit(tmp_path):
    """#839 review: `allow: Read(src/**)` added and `deny: WebFetch` removed are
    permission rows but not shell cases. A fresh comparison records that, so
    diff, verify and the PR comment say nothing about guidance; the legacy limit
    is for an artifact that records no guidance field at all."""

    from test_host_diff_review_changes import SETTINGS, _diff, _invoke, _repository

    from agents_shipgate.report.pr_comment import render_pr_comment
    from agents_shipgate.schemas.verifier import VerifierArtifact

    repo = _repository(
        tmp_path,
        {SETTINGS: {"permissions": {"allow": ["Bash(npm test *)"], "deny": ["WebFetch"]}}},
        {SETTINGS: {"permissions": {"allow": ["Bash(npm test *)", "Read(src/**)"]}}},
    )
    text, payload = _diff(repo)
    assert payload["rows"] and all(row["disposition"] for row in payload["rows"])
    assert [change["guidance"] for change in payload["review"]["changes"]] == [None, None]
    out = tmp_path / "out"
    verify = _invoke([
        "verify", "--workspace", str(repo), "--config", "shipgate.yaml", "--ci-mode", "advisory",
        "--out", str(out), "--pr-comment-style", "capability-review", "--format", "text",
        "--base", "main",
    ])
    comment = (out / "pr-comment.md").read_text(encoding="utf-8")
    reloaded = VerifierArtifact.model_validate_json((out / "verifier.json").read_text(encoding="utf-8"))
    for rendered in (text, verify, comment, render_pr_comment(reloaded, report=None, style="capability-review")):
        assert "Specific permission guidance unavailable" not in rendered
        assert "Permission review guidance:" not in rendered
    assert permission_guidance_lines(reloaded.host_comparison) == []


@pytest.mark.parametrize("rule", ["Bash", "Bash(*)"])
def test_the_whole_shell_tool_gets_the_same_question_either_way(tmp_path, rule):
    guidance = comparison(tmp_path, {"allow": ["Read"]}, {"allow": ["Read", rule]}).review.changes[0].guidance
    assert guidance.case == "declaration_added"
    assert guidance.question == f"Should this source add the allow declaration {rule} for this task?"
    assert guidance.after_rule == rule


def test_withheld_guidance_still_names_its_established_source(tmp_path):
    result = comparison(tmp_path, {}, {"allow": ["Bash(npm test && curl example.invalid)"]})
    guidance = result.review.changes[0].guidance
    assert (guidance.case, guidance.source) == ("unavailable", ".claude/settings.json")
    lines = permission_guidance_lines(result)
    assert "- Change 1: .claude/settings.json" in lines
    assert not any("evidence unavailable" in line for line in lines)
    redacted = comparison(tmp_path / "redacted", {"allow": ["Bash(npm test *)"]},
                          {"allow": ["Bash(npm *)"]}, redact_permission_arguments=True)
    assert {change.guidance.source for change in redacted.review.changes} == {".claude/settings.json"}
