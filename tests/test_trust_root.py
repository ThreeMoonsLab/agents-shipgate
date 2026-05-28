"""SHIP-VERIFY-TRUST-ROOT-TOUCHED — Tier A trust-root protection.

Covers the path classifier, the "only fires with a verification context"
contract, and the end-to-end scan path (findings flow through the
existing release gate, not a second verdict).
"""

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agents_shipgate.checks.verify import CHECK_ID
from agents_shipgate.checks.verify import run as verify_run
from agents_shipgate.cli.main import app
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import Agent
from agents_shipgate.schemas.verification import VerificationContext

runner = CliRunner()


def _context(changed_files: list[str] | None = None, *, verification: bool = True) -> ScanContext:
    manifest = load_manifest(Path("samples/support_refund_agent/shipgate.yaml"))
    vc = (
        VerificationContext(changed_files=changed_files or [])
        if verification
        else None
    )
    return ScanContext(
        manifest=manifest,
        agent=Agent(id="agent:test/test", name="test"),
        tools=[],
        config_path=Path("shipgate.yaml"),
        verification=vc,
    )


def test_plain_scan_without_verification_emits_nothing():
    """Absence of a verification context == plain scan behavior."""
    assert verify_run(_context(verification=False)) == []


def test_empty_changed_files_emits_nothing():
    assert verify_run(_context(changed_files=[])) == []


def test_non_trust_root_files_are_not_flagged():
    findings = verify_run(
        _context(
            changed_files=[
                "src/agent.py",
                "README.md",
                "docs/guide.md",
                "tests/test_agent.py",
                "package.json",
            ]
        )
    )
    assert findings == []


@pytest.mark.parametrize(
    "path,expected_class",
    [
        ("shipgate.yaml", "manifest"),
        ("services/billing/shipgate.yaml", "manifest"),
        (".agents-shipgate/baseline.json", "shipgate_state"),
        ("policies/refund.yaml", "policy"),
        ("prompts/system.md", "prompts"),
        (".github/workflows/agents-shipgate.yml", "ci_gate"),
        (".github/workflows/agents-shipgate.yaml", "ci_gate"),
        ("AGENTS.md", "agent_instructions"),
        ("CLAUDE.md", "agent_instructions"),
        (".claude/commands/shipgate.md", "agent_instructions"),
        (".cursor/rules/agents-shipgate.mdc", "agent_instructions"),
        (".agents/skills/agents-shipgate/reference.md", "agent_instructions"),
        (".codex/config.toml", "agent_instructions"),
        (".codex-plugin/plugin.json", "codex_plugin"),
        ("plugins/browser/.app.json", "tool_surface_decl"),
        ("servers/search/.mcp.json", "tool_surface_decl"),
        ("packages/refund/SKILL.md", "tool_surface_decl"),
    ],
)
def test_trust_root_classification(path, expected_class):
    findings = verify_run(_context(changed_files=[path]))
    assert len(findings) == 1
    finding = findings[0]
    assert finding.check_id == CHECK_ID
    assert finding.severity == "medium"
    assert finding.evidence["changed_file"] == path
    assert finding.evidence["trust_root_class"] == expected_class
    assert finding.source.type == "changed_file"
    assert finding.provenance_kind == "static_declaration"


def test_ci_gate_only_matches_the_shipgate_workflow():
    """A generic workflow change is NOT a trust root — only the Shipgate
    gate workflow is. Keeps target-repo noise low."""
    findings = verify_run(
        _context(changed_files=[".github/workflows/test.yml", ".github/workflows/deploy.yaml"])
    )
    assert findings == []


def test_duplicate_changed_files_are_deduplicated():
    findings = verify_run(_context(changed_files=["shipgate.yaml", "shipgate.yaml"]))
    assert len(findings) == 1


def test_first_match_wins_classification_order():
    """A SKILL.md under .agents/skills/ classifies as agent_instructions
    (ordered earlier) rather than tool_surface_decl — one finding, the
    earlier class."""
    findings = verify_run(
        _context(changed_files=[".agents/skills/agents-shipgate/SKILL.md"])
    )
    assert len(findings) == 1
    assert findings[0].evidence["trust_root_class"] == "agent_instructions"


def test_scan_changed_files_emits_finding_through_the_gate(tmp_path):
    shutil.copytree("samples/clean_read_only_agent", tmp_path / "wk")
    changed = tmp_path / "changed.txt"
    changed.write_text("shipgate.yaml\nsrc/agent.py\nREADME.md\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "scan",
            "-c",
            str(tmp_path / "wk" / "shipgate.yaml"),
            "--changed-files",
            str(changed),
        ],
    )
    assert result.exit_code == 0, result.stdout

    report = json.loads(
        (tmp_path / "wk" / "agents-shipgate-reports" / "report.json").read_text()
    )
    verify_findings = [
        f for f in report["findings"] if f["check_id"] == CHECK_ID
    ]
    assert len(verify_findings) == 1
    assert verify_findings[0]["evidence"]["changed_file"] == "shipgate.yaml"
    # Routed through the one decision engine to the review tier.
    assert report["release_decision"]["decision"] == "review_required"
    assert verify_findings[0]["requires_human_review"] is True


def test_scan_without_changed_files_is_unchanged(tmp_path):
    shutil.copytree("samples/clean_read_only_agent", tmp_path / "wk")
    result = runner.invoke(
        app, ["scan", "-c", str(tmp_path / "wk" / "shipgate.yaml")]
    )
    assert result.exit_code == 0, result.stdout
    report = json.loads(
        (tmp_path / "wk" / "agents-shipgate-reports" / "report.json").read_text()
    )
    assert [f for f in report["findings"] if f["check_id"] == CHECK_ID] == []
