"""SHIP-SCOPE-TOOLKIT-UNBOUNDED — name the unbounded-toolkit blind spot.

The check turns a dynamically-loaded toolkit mounted with no configuration
allowlist (the pilot's stripe/ai #232 pattern, and the dominant real-world
insufficient_evidence cause from the W24/W25 mining) into a named,
human-review finding instead of a silent pass.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agents_shipgate.checks import toolkit_bounds
from agents_shipgate.config.loader import load_manifest
from agents_shipgate.core.context import ScanContext
from agents_shipgate.core.domain import Agent, ToolkitScopeBound

REPO_ROOT = Path(__file__).resolve().parent.parent
HEAD_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "stripe_pr232" / "head"


def _ctx(bounds: list[ToolkitScopeBound]) -> ScanContext:
    config = HEAD_FIXTURE / "shipgate.yaml"
    return ScanContext(
        manifest=load_manifest(config),
        agent=Agent(id="agent:test/test", name="test"),
        tools=[],
        config_path=config,
        toolkit_bounds=list(bounds),
    )


def _bound(*, bounded: bool, source_line: int = 27) -> ToolkitScopeBound:
    return ToolkitScopeBound(
        provider="stripe",
        constructor="StripeAgentToolkit",
        bounded=bounded,
        scopes=["customers:read"] if bounded else [],
        binding="stripe_agent_toolkit",
        source_ref="support_agent.py",
        source_line=source_line,
    )


def test_unbounded_toolkit_emits_finding() -> None:
    findings = toolkit_bounds.run(_ctx([_bound(bounded=False)]))
    assert len(findings) == 1
    finding = findings[0]
    assert finding.check_id == "SHIP-SCOPE-TOOLKIT-UNBOUNDED"
    assert finding.severity == "high"
    assert finding.category == "scope"
    assert finding.evidence["provider"] == "stripe"
    assert finding.evidence["constructor"] == "StripeAgentToolkit"
    # The actionable location is the constructor (source), not the manifest.
    assert finding.source is not None
    assert finding.source.path == "support_agent.py"
    assert finding.source.start_line == 27
    # The line is NOT in fingerprinted evidence (see churn test below).
    assert finding.evidence["source_ref"] == "support_agent.py"


def test_fingerprint_is_stable_across_harmless_line_moves() -> None:
    from agents_shipgate.core.findings.identity import finding_fingerprint

    at_27 = toolkit_bounds.run(_ctx([_bound(bounded=False, source_line=27)]))[0]
    at_28 = toolkit_bounds.run(_ctx([_bound(bounded=False, source_line=28)]))[0]
    # Moving the constructor down a line must not churn baselines/accepted debt.
    assert finding_fingerprint(at_27) == finding_fingerprint(at_28)
    # …but the surfaced location still reflects the real line.
    assert at_28.source is not None and at_28.source.start_line == 28


def test_bounded_toolkit_does_not_emit() -> None:
    # An explicit resource:verb allowlist is least privilege — no finding.
    assert toolkit_bounds.run(_ctx([_bound(bounded=True)])) == []


def test_no_toolkit_bounds_no_finding() -> None:
    assert toolkit_bounds.run(_ctx([])) == []


def test_scan_of_stripe_head_fixture_names_the_unbounded_toolkit(tmp_path: Path) -> None:
    """End-to-end: the pilot's #232 head (full Stripe toolkit, no bound) — a
    silent pass before — now surfaces SHIP-SCOPE-TOOLKIT-UNBOUNDED on a plain
    scan, not just on a base→head diff.
    """
    out = tmp_path / "reports"
    env = dict(__import__("os").environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["AGENTS_SHIPGATE_AGENT_MODE"] = "0"
    result = subprocess.run(
        [
            sys.executable, "-m", "agents_shipgate", "scan",
            "--config", str(HEAD_FIXTURE / "shipgate.yaml"),
            "--out", str(out), "--format", "json", "--ci-mode", "advisory",
        ],
        capture_output=True, text=True, timeout=180, env=env, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    matches = [f for f in report.get("findings", []) if f["check_id"] == "SHIP-SCOPE-TOOLKIT-UNBOUNDED"]
    assert matches, "the unbounded-toolkit finding did not fire on the Stripe head fixture"
    # The finding must point at the constructor, not shipgate.yaml.
    source = matches[0].get("source") or {}
    assert (source.get("path") or "").endswith("support_agent.py"), source
    # The toolkit remains only possibly reachable because its binding graph is
    # incomplete. The named finding stays visible, while the verdict remains
    # fail-closed on missing binding evidence.
    assert report["release_decision"]["decision"] == "insufficient_evidence"
