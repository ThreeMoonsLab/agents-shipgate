"""#824: arbitrary-code launchers are rated without changing containment."""
from __future__ import annotations

import json
import os
import subprocess

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.host_boundary import _allow_rule_id, _safe_rule
from agents_shipgate.core.host_grants import _permission_rule_grants, host_grant_expansion_signals
from agents_shipgate.core.permission_lattice import exec_equivalent_prefix, scoped_risk, subsumes

PREFIXES = [
    "python -c", "python3 -c", "node -e", "ruby -e", "perl -e", "bash -c", "sh -c",
    "npx", "bunx", "pnpm dlx", "uvx", "uv run", "docker exec", "docker run", "xargs", "env",
]
NEGATIVES = [
    "npx prettier --check .", "npm test *", "find *", "make *", "sed *", "gh api *",
    "python3 -m pytest *", "node script.js *", "ruby -c *", "bash -e *", "python -e *",
    "npx prettier *", "env CI=1 npm test *", "xargs grep *", "docker inspect *",
    "./npx *", "/usr/bin/npx *", "NPX *", "npx*", "npx **", "npx * --help",
    "npx  *", "npx\t*", "npx :*", "npx *; echo sentinel", '"npx" *',
]


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("suffix", [" *", ":*"])
def test_launcher_tier(prefix, suffix):
    rule = f"Bash({prefix}{suffix})"
    assert exec_equivalent_prefix(rule) == prefix
    assert scoped_risk(rule) == ("admin", "critical")
    assert _allow_rule_id(rule) == "HOST-PERMISSION-WILDCARD-ALLOW"
    assert _safe_rule(rule) == f"Bash({prefix} *)"
    assert subsumes("Bash(*)", rule) is True
    assert subsumes(rule, "Bash(*)") is False
    assert subsumes(rule, f"Bash({prefix} fixed)") is True
    assert subsumes(rule, "Bash(git status)") is False


@pytest.mark.parametrize("command", NEGATIVES)
def test_exclusions_do_not_claim_exec_equivalence(command):
    rule = f"Bash({command})"
    assert exec_equivalent_prefix(rule) is None
    assert scoped_risk(rule) == ("execute", "medium")
    assert _allow_rule_id(rule) == "HOST-PERMISSION-ALLOW-EXPANDED"
    assert _safe_rule(rule) == "Bash(<redacted-arguments>)"


def test_malformed_and_other_tools_abstain():
    for rule in ["Bash(npx *", "Bash(npx *))", "Read(npx *)", "Shell(npx *)", "Bash(npx *)suffix"]:
        assert exec_equivalent_prefix(rule) is None


def test_ask_and_deny_keep_their_ratings():
    grants = _permission_rule_grants({"ask": ["Bash(npx *)"], "deny": ["Bash(env *)"]}, host="claude-code", scope="project", source=".claude/settings.json")
    assert all((g["access"], g["risk"]) == ("none", "low") for g in grants)


def test_cli_routes_agree_without_exposing_other_arguments(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    def git(*args):
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args], env=env, check=True, capture_output=True)
    def invoke(*args):
        result = CliRunner().invoke(app, [*args, "--workspace", str(repo)])
        assert result.exit_code in (0, 10, 20), result.output
        return result.stdout
    git("init", "-q", "-b", "main")
    path = repo / ".claude/settings.json"
    path.write_text('{"permissions":{"allow":[]}}')
    git("add", "-A")
    git("commit", "-qm", "base")
    git("checkout", "-qb", "change")
    rules = [f"Bash({p} *)" for p in PREFIXES]
    rules += ["Bash(npm test *)", "Bash(npx private-positional-value)"]
    path.write_text(json.dumps({"permissions": {"allow": rules}}))
    git("add", "-A")
    git("commit", "-qm", "head")
    audit = json.loads(invoke("audit", "--host", "--json"))
    diff = json.loads(invoke("diff", "--base", "main", "--json"))
    check_text = invoke("check", "--agent", "codex", "--base", "main", "--format", "agent-boundary-json")
    check = json.loads(check_text)
    verify = json.loads(invoke("verify", "--base", "main", "--head", "HEAD", "--json"))
    assert diff["rows"] == verify["host_comparison"]["rows"]
    for prefix in PREFIXES:
        rule = f"Bash({prefix} *)"
        grant = next(g for g in audit["grants"] if g.get("rule") == rule)
        assert (grant["access"], grant["risk"]) == ("admin", "critical")
        for rows in (diff["rows"], check["rows"]):
            row = next(r for r in rows if rule in r["after"])
            assert row["severity"] == "critical"
            assert "reaches arbitrary code" in row["why"]
        assert any(v.get("evidence", {}).get("rule") == rule for v in check["violations"])
    assert check["decision"] == "block"
    assert "private-positional-value" not in check_text
    comment = (repo / "agents-shipgate-reports/pr-comment.md").read_text()
    assert "reaches arbitrary code" in comment


def test_old_baseline_rerating_does_not_create_expansion():
    current = _permission_rule_grants({"allow": ["Bash(npx *)"]}, host="claude-code", scope="project", source=".claude/settings.json")[0]
    previous = {**current, "risk": "medium", "access": "execute"}
    changes = [{"baseline": previous, "current": current}]
    assert host_grant_expansion_signals(changes) == []
