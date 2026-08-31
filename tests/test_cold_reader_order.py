"""Cold-reader human artifacts lead with capability value (#463)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents_shipgate.cli._helpers import _print_cli_summary, _run_multi_scan
from agents_shipgate.cli.scan import run_scan
from agents_shipgate.cli.scan.human_order import human_artifact_context
from agents_shipgate.cli.verify.orchestrator import run_verify
from agents_shipgate.packet.html import render_packet_html
from agents_shipgate.packet.json_packet import load_packet_json
from agents_shipgate.packet.markdown import render_packet_markdown
from agents_shipgate.report.human_order import (
    HumanArtifactContext,
    cold_reader_lead,
    surface_lead,
)
from agents_shipgate.report.markdown import render_markdown_report
from agents_shipgate.report.pr_comment import render_pr_comment
from agents_shipgate.schemas.capability_change import (
    CapabilityChangeBlock,
    CapabilityChangeMember,
)
from agents_shipgate.schemas.report import ReadinessReport

SAMPLE = Path("samples/google_adk_cold_start_agent")
COLD_REPORT_GOLDEN = SAMPLE / "expected" / "cold-report.md"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _unadopted_sample(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(SAMPLE, repo)
    shutil.rmtree(repo / "expected")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Shipgate Test")
    _git(repo, "config", "user.email", "shipgate@example.invalid")
    _git(repo, "add", "agent.py", "inventories", "specs")
    _git(repo, "commit", "-qm", "base without manifest")
    return repo


def _cold_scan(
    tmp_path: Path,
    monkeypatch,
) -> tuple[Path, ReadinessReport, HumanArtifactContext, str]:
    repo = _unadopted_sample(tmp_path)
    summary_path = tmp_path / "step-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    report, _ = run_scan(
        config_path=repo / "shipgate.yaml",
        output_dir=repo / "reports",
        formats=["markdown", "json"],
        ci_mode="advisory",
    )
    context = human_artifact_context(repo / "shipgate.yaml", None)
    assert context.manifest_committed is False
    return repo, report, context, summary_path.read_text(encoding="utf-8")


def test_unadopted_human_artifacts_lead_with_the_surface(
    tmp_path,
    monkeypatch,
    capsys,
):
    repo, report, context, github = _cold_scan(tmp_path, monkeypatch)
    markdown = (repo / "reports" / "report.md").read_text(encoding="utf-8")
    packet_markdown = (repo / "reports" / "packet.md").read_text(encoding="utf-8")
    packet_html = (repo / "reports" / "packet.html").read_text(encoding="utf-8")

    assert markdown == COLD_REPORT_GOLDEN.read_text(encoding="utf-8")
    assert markdown.index("## Capability Surface") < markdown.index("## Top Findings")
    assert markdown.index("## Top Findings") < markdown.index("## Release Decision")
    assert markdown.count("Decision: insufficient_evidence") == 1
    for heading in (
        "## Tool Surface Summary",
        "## Action Surface Diff",
        "## Tool Surface Diff",
    ):
        assert markdown.index("## Release Decision") < markdown.index(heading)

    _print_cli_summary(report, "advisory", 0, human_context=context)
    console = capsys.readouterr().out
    assert console.index("Surface: 9 tools from 3 sources.") < console.index("Findings by subject")
    assert console.index("Findings by subject") < console.index("Decision: insufficient_evidence")
    assert console.count("Decision: insufficient_evidence") == 1
    assert console.index("Decision: insufficient_evidence") < console.index(
        "Action-surface diff:"
    )
    assert console.index("Decision: insufficient_evidence") < console.index(
        "Tool-surface diff:"
    )

    assert github.index("### Capability surface") < github.index("### Findings")
    assert github.index("### Findings") < github.index("Decision: `insufficient_evidence`")
    assert github.count("Decision: `insufficient_evidence`") == 1
    assert github.index("Decision: `insufficient_evidence`") < github.index(
        "Action-surface diff:"
    )
    assert github.index("Decision: `insufficient_evidence`") < github.index(
        "Tool-surface diff:"
    )

    assert packet_markdown.index("## Capability surface") < packet_markdown.index(
        "## §3 High-risk tool surface"
    )
    assert packet_markdown.index("## Findings by subject") < packet_markdown.index(
        "## §1 Release decision"
    )
    assert packet_markdown.index("## §1 Release decision") < packet_markdown.index(
        "## §1A Evidence matrix"
    )
    assert packet_markdown.count("- Decision: `insufficient_evidence`") == 1
    assert "more finding" not in packet_markdown
    assert "more subject" not in packet_markdown
    assert packet_html.index("<h2>Capability surface</h2>") < packet_html.index(
        "§3 High-risk tool surface"
    )
    assert packet_html.index("Findings by subject") < packet_html.index("§1 Release decision")
    assert packet_html.index("§1 Release decision") < packet_html.index("§1A Evidence matrix")
    assert packet_html.count("Decision: <code>insufficient_evidence</code>") == 1
    assert "more finding" not in packet_html
    assert "more subject" not in packet_html

    report_json = json.loads((repo / "reports" / "report.json").read_text())
    packet_json = json.loads((repo / "reports" / "packet.json").read_text())
    assert "human_context" not in report_json
    assert "human_context" not in packet_json
    assert report_json["release_decision"]["decision"] == "insufficient_evidence"
    assert packet_json["release_decision"]["decision"] == "insufficient_evidence"

    report_bytes = (repo / "reports" / "report.json").read_bytes()
    packet_bytes = (repo / "reports" / "packet.json").read_bytes()
    render_markdown_report(report, human_context=context)
    render_packet_markdown(
        load_packet_json(packet_bytes.decode("utf-8")),
        human_context=context,
        cold_lead=cold_reader_lead(report),
    )
    assert (repo / "reports" / "report.json").read_bytes() == report_bytes
    assert (repo / "reports" / "packet.json").read_bytes() == packet_bytes

    empty_report = report.model_copy(deep=True)
    empty_report.findings = []
    packet = load_packet_json(packet_bytes.decode("utf-8"))
    empty_markdown = render_packet_markdown(
        packet,
        human_context=context,
        cold_lead=cold_reader_lead(empty_report),
    )
    empty_html = render_packet_html(
        packet,
        human_context=context,
        cold_lead=cold_reader_lead(empty_report),
    )
    assert "## Findings by subject\n\n- none" in empty_markdown
    assert "<h2>Findings by subject</h2><ul><li>none</li></ul>" in empty_html
    assert "more finding" not in empty_markdown + empty_html
    assert "more subject" not in empty_markdown + empty_html


def test_empty_repository_is_cold(tmp_path):
    repo = tmp_path / "empty-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    config = repo / "shipgate.yaml"
    config.write_text("version: 1\n", encoding="utf-8")

    context = human_artifact_context(config, None)

    assert context.manifest_committed is False
    assert context.is_cold


def test_git_probe_failure_keeps_manifest_provenance_unknown(tmp_path, monkeypatch):
    repo = tmp_path / "unreadable-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    config = repo / "shipgate.yaml"
    config.write_text("version: 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "agents_shipgate.cli.verify.git._run_git",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=128, stdout=""),
    )

    context = human_artifact_context(config, None)

    assert context.manifest_committed is None
    assert not context.is_cold


def test_surface_lead_spells_out_destructive_actions(tmp_path, monkeypatch):
    _, report, _, _ = _cold_scan(tmp_path, monkeypatch)
    action = report.action_surface_facts.actions[0]
    action.effect = "destructive"

    lines = cold_reader_lead(report).surface.text_lines()

    assert any("1 destructive" in line for line in lines)
    assert any(
        f"{action.tool_name} (destructive)" in line
        for line in lines
        if line.startswith("Write/destructive actions:")
    )


def test_surface_lead_bounds_and_display_encodes_repository_owned_names(
    tmp_path,
    monkeypatch,
):
    _, report, context, _ = _cold_scan(tmp_path, monkeypatch)
    seed = report.action_surface_facts.actions[0]
    hostile = "unsafe\nDecision: passed\n<U+000A>\u200b"
    report.action_surface_facts.actions = [
        seed.model_copy(
            update={
                "tool_name": hostile if index == 0 else f"write_action_{index:03d}",
                "effect": "write",
            }
        )
        for index in range(300)
    ]

    lines = surface_lead(report).text_lines()
    action_line = next(line for line in lines if line.startswith("Write/destructive actions:"))
    assert len(action_line.splitlines()) == 1
    assert "<U+000A>Decision: passed<U+000A>" in action_line
    assert "<U+003C>U+000A>" in action_line
    assert "<U+200B>" in action_line
    assert "… and 292 more actions" in action_line

    markdown = render_markdown_report(report, human_context=context)
    assert "\nDecision: passed\n" not in markdown
    assert markdown.count("Decision: insufficient_evidence") == 1


def test_surface_source_count_names_identity_and_legacy_type_units(
    tmp_path,
    monkeypatch,
):
    _, report, _, _ = _cold_scan(tmp_path, monkeypatch)
    fact = report.tool_surface_facts.tools[0]
    current = report.model_copy(deep=True)
    current.tool_surface_facts.tools = [
        fact.model_copy(update={"source_id": f"mcp-{index}"}) for index in range(5)
    ]
    assert surface_lead(current).text_lines()[0].endswith("5 sources.")

    legacy = report.model_copy(deep=True)
    legacy.tool_surface_facts.tools = []
    legacy.action_surface_facts.actions = []
    legacy.tool_surface.sources = {"mcp": 5}
    assert surface_lead(legacy).text_lines()[0].endswith("1 source type.")


def test_committed_manifest_keeps_the_adopted_markdown_bytes(tmp_path, monkeypatch):
    repo = tmp_path / "adopted"
    shutil.copytree(SAMPLE, repo)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Shipgate Test")
    _git(repo, "config", "user.email", "shipgate@example.invalid")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "adopted repository")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    report, _ = run_scan(
        config_path=repo / "shipgate.yaml",
        output_dir=repo / "reports",
        formats=["markdown", "json"],
        ci_mode="advisory",
        packet_enabled=False,
    )
    context = human_artifact_context(repo / "shipgate.yaml", None)

    assert context.manifest_committed is True
    assert not context.is_cold
    assert (repo / "reports" / "report.md").read_text(encoding="utf-8") == render_markdown_report(
        report, human_context=context
    )
    adopted_markdown = (repo / "reports" / "report.md").read_text(encoding="utf-8")
    assert adopted_markdown == (
        SAMPLE / "expected" / "report.md"
    ).read_text(encoding="utf-8")
    assert "- Decision: insufficient\\_evidence" in adopted_markdown


def test_packet_lead_is_not_built_without_a_cold_human_packet(
    tmp_path,
    monkeypatch,
):
    def unexpected_lead(_report):
        raise AssertionError("cold packet lead should not be built")

    monkeypatch.setattr(
        "agents_shipgate.cli.scan.writing.cold_reader_lead",
        unexpected_lead,
    )

    cold = _unadopted_sample(tmp_path / "cold")
    run_scan(
        config_path=cold / "shipgate.yaml",
        output_dir=cold / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=True,
        packet_formats=["json"],
    )

    adopted = tmp_path / "adopted-packet"
    shutil.copytree(SAMPLE, adopted)
    _git(adopted, "init", "-q")
    _git(adopted, "config", "user.name", "Shipgate Test")
    _git(adopted, "config", "user.email", "shipgate@example.invalid")
    _git(adopted, "add", ".")
    _git(adopted, "commit", "-qm", "adopted repository")
    run_scan(
        config_path=adopted / "shipgate.yaml",
        output_dir=adopted / "reports",
        formats=["json"],
        ci_mode="advisory",
        packet_enabled=True,
        packet_formats=["md"],
    )


def test_archived_adopted_verify_remains_verdict_first(tmp_path):
    repo = tmp_path / "adopted-verify"
    shutil.copytree(SAMPLE, repo)
    shutil.rmtree(repo / "expected")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Shipgate Test")
    _git(repo, "config", "user.email", "shipgate@example.invalid")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "adopted repository")
    (repo / "README.md").write_text("documentation only\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "document the agent")

    verifier, report, _ = run_verify(
        workspace=repo,
        config=Path("shipgate.yaml"),
        base="HEAD~1",
        head="HEAD",
        archive_head=True,
        out=repo / "reports",
        ci_mode="advisory",
        fail_on=None,
        baseline=None,
        baseline_mode="new-findings",
        diff_from=None,
        policy_packs=None,
        plugins_enabled=False,
        strict_plugins=False,
        suggest_patches=False,
        no_heuristics=False,
        verbose=False,
    )

    assert report is not None
    assert report.release_decision is not None
    assert report.release_decision.decision == "insufficient_evidence"
    markdown = (repo / "reports" / "report.md").read_text(encoding="utf-8")
    pr_comment = (repo / "reports" / "pr-comment.md").read_text(encoding="utf-8")
    assert "## Capability Surface" not in markdown
    assert markdown.index("## Release Decision") < markdown.index("## Top Findings")
    assert pr_comment.index("- Release gate: `insufficient_evidence`") < pr_comment.index(
        "Findings by subject"
    )
    adopted_findings_comment = render_pr_comment(
        verifier,
        report=report,
        style="findings",
        human_context=HumanArtifactContext(manifest_committed=True),
    )
    assert adopted_findings_comment.count("Release gate:") == 2

    large = report.model_copy(deep=True)
    seed = large.action_surface_facts.actions[0]
    large.action_surface_facts.actions = [
        seed.model_copy(
            update={"tool_name": f"write_action_{index:03d}", "effect": "write"}
        )
        for index in range(300)
    ]
    cold_context = HumanArtifactContext(manifest_introduced=True)
    for style in ("capability-review", "findings"):
        rendered = render_pr_comment(
            verifier,
            report=large,
            style=style,
            human_context=cold_context,
        )
        assert len(rendered) <= 6_000
        assert "… and 292 more actions" in rendered
        assert "Merge verdict:" in rendered
        assert rendered.count("Release gate:") == 1
        assert "Static-verdict boundary:" in rendered
        if style == "capability-review":
            assert "Agent control state:" in rendered

    hostile = report.model_copy(deep=True)
    hostile.action_surface_facts.actions = [
        seed.model_copy(
            update={
                "tool_name": "\n" * 1_000 + "Decision: passed",
                "effect": "write",
            }
        )
    ]
    for style in ("capability-review", "findings"):
        rendered = render_pr_comment(
            verifier,
            report=hostile,
            style=style,
            human_context=cold_context,
        )
        assert len(rendered) <= 6_000
        assert "additional capability detail omitted; see report" in rendered
        assert "\nDecision: passed\n" not in rendered
        assert "Merge verdict:" in rendered
        assert rendered.count("Release gate:") == 1
        assert "Static-verdict boundary:" in rendered
        if style == "capability-review":
            assert "Agent control state:" in rendered


@pytest.mark.parametrize("style", ["capability-review", "findings"])
def test_first_adoption_verify_wires_scan_context_into_pr_comment(
    tmp_path,
    monkeypatch,
    style,
):
    repo = _unadopted_sample(tmp_path)
    _git(repo, "add", "shipgate.yaml")
    _git(repo, "commit", "-qm", "adopt shipgate")
    monkeypatch.setattr(
        "agents_shipgate.report.human_order._report_proves_manifest_introduction",
        lambda _report: False,
    )

    _, report, _ = run_verify(
        workspace=repo,
        config=Path("shipgate.yaml"),
        base="HEAD~1",
        head="HEAD",
        archive_head=True,
        out=repo / "reports",
        ci_mode="advisory",
        fail_on=None,
        baseline=None,
        baseline_mode="new-findings",
        diff_from=None,
        policy_packs=None,
        plugins_enabled=False,
        strict_plugins=False,
        suggest_patches=False,
        no_heuristics=False,
        verbose=False,
        pr_comment_style=style,
    )

    assert report is not None
    comment = (repo / "reports" / "pr-comment.md").read_text(encoding="utf-8")
    assert (
        comment.index("Surface: 9 tools")
        < comment.index("Capability delta (analysed surface)")
        < comment.index("Release gate:")
    )
    assert comment.count("Capability delta (analysed surface)") == 1
    assert comment.count("Release gate:") == 1
    if style == "capability-review":
        assert "Capability delta note:" in comment or "Top capability changes:" in comment
    else:
        assert comment.index("Release gate:") < comment.index("Action-surface diff:")
        assert comment.index("Release gate:") < comment.index("Tool-surface diff:")


def test_unadopted_multi_scan_row_puts_the_surface_before_the_decision(
    tmp_path,
    monkeypatch,
    capsys,
):
    repo = _unadopted_sample(tmp_path)
    from agents_shipgate.cli.verify import git as verify_git

    original = verify_git.path_committed_at_head
    probes = 0

    def counted_probe(root, relative):
        nonlocal probes
        probes += 1
        return original(root, relative)

    monkeypatch.setattr(verify_git, "path_committed_at_head", counted_probe)

    exit_code = _run_multi_scan(
        config_paths=[repo / "shipgate.yaml"],
        out=repo / "multi-reports",
        formats=["json"],
        ci_mode="advisory",
        fail_on=None,
        baseline=None,
        diff_from=None,
        baseline_mode="new-findings",
        deep_import=False,
        policy_packs=[],
        plugins_enabled=None,
        verbose=False,
        packet_enabled=False,
    )

    output = capsys.readouterr().out
    manifest_lines = [line for line in output.splitlines() if f"{repo / 'shipgate.yaml'}:" in line]
    assert exit_code == 0
    assert "Surface: 9 tools from 3 sources." in manifest_lines[0], manifest_lines
    findings_index = next(
        i for i, line in enumerate(manifest_lines) if "Findings by subject" in line
    )
    decision_index = next(
        i for i, line in enumerate(manifest_lines) if ": insufficient_evidence " in line
    )
    assert findings_index < decision_index
    assert probes == 1


def test_cold_block_tier_content_keeps_verdict_first(tmp_path, monkeypatch):
    repo, report, context, _ = _cold_scan(tmp_path, monkeypatch)
    blocked = report.model_copy(deep=True)
    assert blocked.release_decision is not None
    blocked.release_decision.decision = "blocked"
    blocked.release_decision.blockers = list(blocked.release_decision.review_items)
    blocked.findings[0].blocks_release = True

    markdown = render_markdown_report(blocked, human_context=context)
    assert "## Capability Surface" not in markdown
    assert markdown.index("## Release Decision") < markdown.index("## Tool Surface Summary")
    assert "Decision: blocked" in markdown

    packet = load_packet_json((repo / "reports" / "packet.json").read_text())
    packet.release_decision.decision = "blocked"
    packet.release_decision.verdict = "BLOCKED"
    packet.release_decision.blockers = list(packet.release_decision.review_items)
    rendered_packet = render_packet_markdown(
        packet,
        human_context=context,
        cold_lead=cold_reader_lead(blocked),
    )
    assert "## Capability surface" not in rendered_packet
    assert rendered_packet.index("## §1 Release decision") < rendered_packet.index(
        "## §3 High-risk tool surface"
    )
    assert "- Decision: `blocked`" in rendered_packet

    rendered_html = render_packet_html(
        packet,
        human_context=context,
        cold_lead=cold_reader_lead(blocked),
    )
    assert "<h2>Capability surface</h2>" not in rendered_html
    assert rendered_html.index("§1 Release decision") < rendered_html.index(
        "§3 High-risk tool surface"
    )

    # Fail closed even if the serialized packet decision is inconsistent with
    # the report finding substrate: active block-tier content still wins.
    blocked.release_decision.decision = "review_required"
    blocked.release_decision.blockers = []
    packet.release_decision.decision = "review_required"
    packet.release_decision.blockers = []
    inconsistent_report = render_markdown_report(blocked, human_context=context)
    inconsistent_markdown = render_packet_markdown(
        packet,
        human_context=context,
        cold_lead=cold_reader_lead(blocked),
    )
    inconsistent_html = render_packet_html(
        packet,
        human_context=context,
        cold_lead=cold_reader_lead(blocked),
    )
    assert "## Capability Surface" not in inconsistent_report
    assert "## Capability surface" not in inconsistent_markdown
    assert "<h2>Capability surface</h2>" not in inconsistent_html


def test_cold_delta_is_grouped_by_subject_before_findings_and_gate(
    tmp_path,
    monkeypatch,
):
    repo, report, context, _ = _cold_scan(tmp_path, monkeypatch)
    report.capability_change = CapabilityChangeBlock(
        enabled=True,
        added=[
            CapabilityChangeMember(
                id="alpha-action",
                direction="added",
                subject_kind="action",
                tool="alpha",
                action="alpha.write",
                release_impact="review_required",
            ),
            CapabilityChangeMember(
                id="alpha-scope",
                direction="added",
                subject_kind="scope",
                tool="alpha",
                scope="cases:write",
                release_impact="review_required",
            ),
            CapabilityChangeMember(
                id="beta-action",
                direction="added",
                subject_kind="action",
                tool="beta",
                action="beta.read",
            ),
        ],
    )

    markdown = render_markdown_report(report, human_context=context)
    assert markdown.index("## Capability Surface") < markdown.index(
        "## Capability Delta By Subject"
    )
    assert markdown.index("## Capability Delta By Subject") < markdown.index("## Top Findings")
    assert markdown.index("## Top Findings") < markdown.index("## Release Decision")
    delta = markdown.split("## Capability Delta By Subject", 1)[1].split("## Top Findings", 1)[0]
    assert delta.count("- alpha") == 1
    assert "  - added action alpha.write — review required" in delta
    assert "  - added scope cases:write — review required" in delta
    assert delta.count("- beta") == 1

    packet = load_packet_json((repo / "reports" / "packet.json").read_text())
    packet_markdown = render_packet_markdown(
        packet,
        human_context=context,
        cold_lead=cold_reader_lead(report),
    )
    assert packet_markdown.index("## Capability delta by subject") < packet_markdown.index(
        "## Findings by subject"
    )
    packet_delta = packet_markdown.split("## Capability delta by subject", 1)[1].split(
        "## Findings by subject", 1
    )[0]
    assert packet_delta.count("- `alpha`") == 1
    assert packet_delta.count("- `beta`") == 1
