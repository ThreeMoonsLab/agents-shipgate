"""#829: documentation-derived matching expectations, not host execution.

References/date live beside the bounded table in permission_residual.py.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.permission_lattice import subsumes
from agents_shipgate.core.permission_residual import residual_prefix_note


def grant(rule, disposition="allow", source=".claude/settings.json", host="claude-code"):
    return {"kind": "permission_rule", "host": host, "source": source,
            "rule": rule, "disposition": disposition}


def denies(*rules):
    return [grant(rule, "deny") for rule in rules]


@pytest.mark.parametrize("suffix", [" *", ":*"])
def test_documented_prefix_forms(suffix):
    note = residual_prefix_note(grant(f"Bash(git push{suffix})"), denies(
        f"Bash(git push --force{suffix})", f"Bash(git push -f{suffix})"))
    assert note
    for text in [
        "--delete", "origin :main", "origin +main", "--force-with-lease", "--mirror",
        "origin main --force",
    ]:
        assert text in note
    assert "may still apply" in note
    assert "allowed without a prompt" not in note


@pytest.mark.parametrize("rule", [
    "Bash(git push *)", "Bash(*)", "Bash(git *)", "Read(src/*)",
    "Bash(git push --force)", "Bash(git push --force*)", "Bash(git push * main)",
    "Bash(git push --force *))", "Bash(git push --force *", "Bash(git push && echo *)",
    "Bash(git push ; echo *)", "Bash(git push | cat *)", "Bash(git push\necho *)",
    "Bash(git push $(echo x) *)", "Bash(git push `echo x` *)", "Bash(git push 'x' *)",
])
def test_every_deny_must_be_a_decided_strict_simple_prefix(rule):
    assert residual_prefix_note(grant("Bash(git push *)"), denies(
        "Bash(git push --force *)", rule)) is None


@pytest.mark.parametrize("rule", [
    "Bash(git push)", "Bash(git pushes *)", "Bash(git:* push)", "Bash(git push*)",
    "Bash(git push * origin)", "Bash(git push && echo *)", "Shell(git push *)",
])
def test_allow_shape_refusals(rule):
    assert residual_prefix_note(grant(rule), denies("Bash(git push --force *)")) is None


def test_empty_scope_other_host_and_disposition():
    allow = grant("Bash(git push *)")
    assert residual_prefix_note(allow, []) is None
    assert residual_prefix_note(allow, [grant("Bash(git push --force *)", "deny", source="other")]) is None
    assert residual_prefix_note(allow, [grant("Bash(git push --force *)", "deny", host="codex")]) is None
    assert residual_prefix_note(grant("Bash(git push *)", "ask"), denies("Bash(git push -f *)")) is None


def test_examples_are_filtered_against_all_denies():
    note = residual_prefix_note(grant("Bash(git push *)"), denies("Bash(git push --delete *)"))
    assert note and "--delete" not in note and "origin :main" in note


def test_a_flag_after_the_remote_is_gated_like_every_example():
    note = residual_prefix_note(grant("Bash(git push *)"), denies("Bash(git push origin *)"))
    assert note and "origin main --force" not in note and "--delete origin main" in note
    note = residual_prefix_note(grant("Bash(git push origin *)"), denies("Bash(git push origin main *)"))
    assert note and "origin main --force" not in note and "origin +main" in note


def test_routes_that_redact_rule_arguments_omit_the_note():
    from agents_shipgate.core.capability_diff_rows import capability_diff_rows

    allow = {**grant("Bash(git push *)"), "risk": "medium", "access": "execute", "wildcard": False}
    head = [allow, *denies("Bash(git push --force *)")]
    payload = {"changes": [{"baseline": None, "current": allow}], "expansion_signals": []}
    (shown,) = capability_diff_rows(payload, current_grants=head)
    (redacted,) = capability_diff_rows(payload, redact_permission_arguments=True, current_grants=head)
    assert "origin :main" in shown.why
    assert redacted.after == "Bash(<redacted-arguments>)"
    assert "deny prefixes" not in redacted.why and "git push" not in redacted.why


def test_documented_word_boundary_bare_command_and_flag_order():
    # Claude permission documentation, reviewed 2026-09-27. Reordered flags
    # are matcher evidence only, not a promise of portable Git CLI parsing.
    assert subsumes("Bash(git push *)", "Bash(git push)") is True
    assert subsumes("Bash(git push:*)", "Bash(git push)") is True
    assert subsumes("Bash(git push *)", "Bash(git pushes)") is False
    assert subsumes("Bash(git push --force *)", "Bash(git push origin main --force)") is False
    assert subsumes("Bash(git push --force *)", "Bash(git push --force-with-lease)") is False


def test_real_routes_use_unchanged_head_denies_and_preserve_decisions(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}

    def git(*args):
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=test", "-c",
                        "user.email=test@example.invalid", *args], env=env, check=True, capture_output=True)

    def invoke(*args):
        result = CliRunner().invoke(app, [*args, "--workspace", str(repo)])
        assert result.exit_code in (0, 10, 20), result.output
        return result.stdout

    git("init", "-q", "-b", "main")
    path = repo / ".claude/settings.json"
    permissions = {"deny": ["Bash(git push --force *)", "Bash(git push -f *)"]}
    path.write_text(json.dumps({"permissions": permissions}))
    git("add", "-A")
    git("commit", "-qm", "base")
    git("checkout", "-qb", "change")
    permissions["allow"] = ["Bash(git push *)"]
    path.write_text(json.dumps({"permissions": permissions}))
    git("add", "-A")
    git("commit", "-qm", "head")
    diff = json.loads(invoke("diff", "--base", "main", "--json"))
    verify = json.loads(invoke("verify", "--base", "main", "--head", "HEAD", "--json"))
    assert diff["rows"] == verify["host_comparison"]["rows"]
    row = diff["rows"][0]
    assert "deny prefixes in this source" in row["why"]
    assert (row["direction"], row["expands"], row["severity"]) == ("added", True, "medium")
    assert row["why"] in invoke("diff", "--base", "main")
    comment = (repo / "agents-shipgate-reports/pr-comment.md").read_text()
    assert "deny prefixes in this source" in comment
    check_text = invoke("check", "--agent", "codex", "--base", "main", "--format", "agent-boundary-json")
    check = json.loads(check_text)
    assert check["rows"] and all("deny prefixes" not in r["why"] for r in check["rows"])
    assert "origin :main" not in check_text
