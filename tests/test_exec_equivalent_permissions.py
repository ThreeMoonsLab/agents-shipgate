"""#824: arbitrary-code launchers are rated without changing containment."""
from __future__ import annotations

import json
import os
import subprocess

import pytest
from typer.testing import CliRunner

from agents_shipgate.cli.main import app
from agents_shipgate.core.host_boundary import _allow_rule_id, _safe_rule, _widens_allow
from agents_shipgate.core.host_grants import (
    _permission_rule_grants,
    host_audit_inventory,
    host_grant_expansion_signals,
    render_host_audit_markdown,
)
from agents_shipgate.core.permission_lattice import (
    EXEC_EQUIVALENT_PREFIXES,
    exec_equivalent_argument,
    exec_equivalent_prefix,
    same_grant,
    scoped_risk,
    subsumes,
)

PREFIXES = [
    "python -c", "python3 -c", "node -e", "node --eval", "node -p", "node --print",
    "ruby -e", "perl -e", "perl -E", "php -r", "bash -c", "sh -c", "zsh -c", "pwsh -c",
    "eval", "npx", "bunx", "pnpm dlx", "pnpm exec", "uvx", "uv run", "uv tool run",
    "pipx run", "docker exec", "docker run", "xargs", "env", "sudo",
]
NEGATIVES = [
    "npx prettier --check .", "npm test *", "find *", "make *", "sed *", "gh api *",
    "python3 -m pytest *", "node script.js *", "ruby -c *", "bash -e *", "python -e *",
    "npx prettier *", "env CI=1 npm test *", "xargs grep *", "docker inspect *",
    "./npx *", "/usr/bin/npx *", "NPX *", "npx **", "npx * --help",
    "npx  *", "npx\t*", "npx :*", "npx *; echo sentinel", '"npx" *',
    # Not wider than any entry: `n *` is the command `n`, not a prefix of `npx`.
    "n *", "n:*", "python3 -c foo *", "npm *", "p* -c *",
]
_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _allow_risk(rule: str) -> str:
    """The rating the audit, diff, check and verifier routes all read."""

    (grant,) = _permission_rule_grants(
        {"allow": [rule]}, host="claude-code", scope="project", source=".claude/settings.json"
    )
    return grant["risk"]


def _wider_rules(prefix: str) -> list[str]:
    """Every simple-prefix spelling strictly wider than `Bash(<prefix> *)`."""

    launcher = f"{prefix} "
    rules = {f"Bash({launcher[:end]}*)" for end in range(1, len(launcher))}
    rules |= {
        f"Bash({launcher[:end]}:*)"
        for end in range(1, len(prefix))
        if launcher[end] == " " and launcher[:end] and not launcher[:end].endswith(" ")
    }
    return sorted(rules)


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


def test_the_table_the_tests_pin_is_the_engine_table():
    assert set(PREFIXES) == EXEC_EQUIVALENT_PREFIXES


@pytest.mark.parametrize("prefix", PREFIXES)
def test_a_wider_rule_is_never_rated_below_a_launcher_it_allows(prefix):
    """Widening `Bash(python3 -c *)` to `Bash(python3 *)` cannot clear a block."""

    narrower = f"Bash({prefix} *)"
    wider = _wider_rules(prefix)
    assert wider
    for rule in [*wider, "Bash", "Bash(*)", "*"]:
        assert subsumes(rule, narrower) is True, rule
        assert _ORDER[_allow_risk(rule)] >= _ORDER[_allow_risk(narrower)], rule
        assert _allow_rule_id(rule) == "HOST-PERMISSION-WILDCARD-ALLOW", rule


@pytest.mark.parametrize(
    "rule,shown",
    [
        ("Bash(python3 *)", "Bash(python3 *)"),
        ("Bash(python3:*)", "Bash(python3 *)"),
        ("Bash(python3 -c*)", "Bash(python3 -c*)"),
        ("Bash(npx*)", "Bash(npx*)"),
        ("Bash(n*)", "Bash(n*)"),
        ("bash(docker *)", "Bash(docker *)"),
        ("Bash( uv * )", "Bash(uv *)"),
    ],
)
def test_a_wider_rule_is_shown_in_table_text(rule, shown):
    assert scoped_risk(rule) == ("admin", "critical")
    assert exec_equivalent_prefix(rule) is None
    assert _safe_rule(rule) == shown
    # Whatever is shown is a prefix of a table entry, so it carries no operand.
    stem = exec_equivalent_argument(rule)[:-1]
    assert any(f"{entry} ".startswith(stem) for entry in EXEC_EQUIVALENT_PREFIXES)


@pytest.mark.parametrize(
    "rule",
    [
        "Bash(npx private-package *)",
        "Bash(python3 -c import-secret *)",
        "Bash(sudo -u private-user *)",
        "Bash(npx private-positional-value)",
        "Bash(n *)",
        "Bash(python3 * -c *)",
    ],
)
def test_operands_beyond_table_text_stay_redacted(rule):
    assert exec_equivalent_argument(rule) is None
    assert _safe_rule(rule) == "Bash(<redacted-arguments>)"


def test_the_whole_tool_keeps_its_own_rating_path():
    for rule in ["Bash", "Bash(*)", "Bash(**)", "*"]:
        assert exec_equivalent_argument(rule) is None
        assert _allow_risk(rule) == "critical"
        assert _allow_rule_id(rule) == "HOST-PERMISSION-WILDCARD-ALLOW"


@pytest.mark.parametrize(
    "old,new",
    [
        ("Bash(npx:*)", "Bash(npx *)"),
        ("Bash(npx *)", "Bash(npx:*)"),
        ("Bash(python3 -c:*)", "Bash(python3 -c *)"),
        ("Bash(npm test:*)", "Bash(npm test *)"),
        ("Bash(npx *)", "bash( npx * )"),
    ],
)
def test_respelling_one_rule_grants_nothing_new(old, new):
    assert subsumes(old, new) is False
    assert same_grant(old, new) is True
    assert _widens_allow(new, [old]) is False


@pytest.mark.parametrize(
    "old,new",
    [
        ("Bash(npx *)", "Bash(npx*)"),
        ("Bash(npm :*)", "Bash(npm *)"),
        ("Bash(:*)", "Bash( *)"),
        ("Bash(npx *", "Bash(npx *)"),
        ("Read(src:*)", "Read(src *)"),
        ("mcp__github", "mcp__github__*"),
    ],
)
def test_only_a_documented_respelling_is_the_same_grant(old, new):
    assert same_grant(old, new) is False


def test_the_audit_markdown_names_launcher_rules(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.json").write_text(
        json.dumps({"permissions": {"allow": ["Read", "Bash(npx *)", "Bash(python3 *)", "Bash(npm test *)"]}})
    )
    markdown = render_host_audit_markdown(host_audit_inventory(tmp_path))
    assert (
        "⚠ 2 wildcard allow rule(s) above low risk; verification reports "
        "`SHIP-HOST-BOUNDARY-PERMISSION-WILDCARD-ALLOW`. 2 of them reach arbitrary "
        "code through a launcher. 1 further wildcard rule(s) are read-only and listed above."
    ) in markdown


def test_malformed_and_other_tools_abstain():
    for rule in [
        "Bash(npx *", "Bash(npx *))", "Read(npx *)", "Shell(npx *)", "Bash(npx *)suffix",
        "Bash(python3 *", "Bash(n*)suffix", "Read(p*)",
    ]:
        assert exec_equivalent_prefix(rule) is None
        assert exec_equivalent_argument(rule) is None


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


def test_old_baseline_rerating_of_a_wider_rule_does_not_create_expansion():
    current = _permission_rule_grants({"allow": ["Bash(python3 *)"]}, host="claude-code", scope="project", source=".claude/settings.json")[0]
    previous = {**current, "risk": "medium", "access": "execute"}
    assert host_grant_expansion_signals([{"baseline": previous, "current": current}]) == []


def _check_edit(tmp_path, base, head):
    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}

    def git(*args):
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args], env=env, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    path = repo / ".claude/settings.json"
    for rules, message in ((base, "base"), (head, "head")):
        path.write_text(json.dumps({"permissions": {"allow": rules}}))
        git("add", "-A")
        git("commit", "-qm", message)
    result = CliRunner().invoke(app, ["check", "--agent", "codex", "--base", "HEAD~1", "--workspace", str(repo), "--format", "agent-boundary-json"])
    assert result.exit_code in (0, 10, 20), result.output
    return json.loads(result.stdout)


@pytest.mark.parametrize("base,head", [
    ("Bash(python3 -c *)", "Bash(python3 *)"),
    ("Bash(npx *)", "Bash(npx*)"),
    ("Bash(env *)", "Bash(e*)"),
])
def test_widening_a_launcher_rule_still_blocks(tmp_path, base, head):
    check = _check_edit(tmp_path, [base], [head])
    assert check["decision"] == "block"
    assert [v["evidence"]["rule"] for v in check["violations"]] == [head]
    (added,) = [r for r in check["rows"] if r["after"] == head]
    assert added["severity"] == "critical"


@pytest.mark.parametrize("base,head", [
    ("Bash(npx:*)", "Bash(npx *)"),
    ("Bash(npm test:*)", "Bash(npm test *)"),
])
def test_respelling_a_rule_is_not_a_new_grant(tmp_path, base, head):
    check = _check_edit(tmp_path, [base], [head])
    assert check["decision"] == "allow"
    assert check["violations"] == []
