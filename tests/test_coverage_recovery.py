"""#561: parser facts identify a recovery, never a new verdict or authority."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from typer.testing import CliRunner

from agents_shipgate.ci.release_decision import _evidence_gaps, evidence_below_ie_threshold
from agents_shipgate.cli._helpers import _print_cli_summary
from agents_shipgate.cli.main import app
from agents_shipgate.cli.scan import inputs, run_scan
from agents_shipgate.cli.scan.sanitization import _sanitize_source_recovery_evidence
from agents_shipgate.cli.verify.fix_task import _insufficient_evidence_remedies, build_fix_task
from agents_shipgate.core.domain import LoadedToolSource
from agents_shipgate.core.privacy import RedactionStats, redact_data
from agents_shipgate.packet.builder import build_packet_from_report
from agents_shipgate.packet.html import render_packet_html
from agents_shipgate.packet.markdown import render_packet_markdown
from agents_shipgate.report.markdown import render_markdown_report
from agents_shipgate.report.pr_comment import render_pr_comment
from agents_shipgate.schemas.coverage_recovery import CoverageRecovery, SourceRecoveryEvidence
from agents_shipgate.schemas.report import EvidenceGap
from agents_shipgate.schemas.verifier import VerifierCapabilityReview

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    (None, "input_unavailable", "sdk_entrypoint_not_found", "Restore the existing entrypoint"),
    (
        "[read_tool] + [other_tool]", "reader_limitation",
        "sdk_literal_tool_list_concatenation_unsupported", "needs a reader repair",
    ),
    (
        "get_tools()", "unresolved", "sdk_tools_expression_unresolved",
        "before choosing a remedy",
    ),
]


def _project(project: Path, expression: str | None) -> Path:
    project.mkdir(exist_ok=True)
    (project / "shipgate.yaml").write_text(
        "version: '0.1'\n"
        "project: {name: recovery}\n"
        "agent: {name: recovery, declared_purpose: [read local data]}\n"
        "environment: {target: local}\n"
        "tool_sources:\n"
        "  - {id: sdk, type: openai_agents_sdk, path: agent.py, optional: true}\n",
        encoding="utf-8",
    )
    if expression is not None:
        (project / "agent.py").write_text(
            "from agents import Agent, function_tool\n"
            "@function_tool\n"
            "def read_tool() -> str:\n"
            '    return "a"\n'
            "@function_tool\n"
            "def other_tool() -> str:\n"
            '    return "b"\n'
            f'agent = Agent(name="recovery", tools={expression})\n',
            encoding="utf-8",
        )
    return project


def _scan(project: Path, output: Path):
    report, _ = run_scan(
        config_path=project / "shipgate.yaml", output_dir=output,
        formats=["json"], ci_mode="advisory", packet_enabled=False,
    )
    return report


def _without_recovery(monkeypatch):
    load_sources = inputs._load_sources

    def unclassified(*args, **kwargs):
        sources, artifacts = load_sources(*args, **kwargs)
        return [s.model_copy(update={"recovery_evidence": []}) for s in sources], artifacts

    monkeypatch.setattr(inputs, "_load_sources", unclassified)


@pytest.mark.parametrize("expression,kind,reason,remedy", CASES)
def test_real_loader_recovery_preserves_decision_and_declaration_authorship(
    tmp_path, monkeypatch, expression, kind, reason, remedy,
):
    project = _project(tmp_path / "project", expression)
    report = _scan(project, tmp_path / "reports")
    gaps = report.release_decision.evidence_coverage.evidence_gaps
    gap = next(g for g in gaps if g.recovery is not None)
    assert gap.recovery == CoverageRecovery(kind=kind, reason=reason)
    assert gap.source_type == "openai_agents_sdk"
    assert gap.source_ref == ("agent.py:8" if expression else "agent.py")
    assert gap.next_action.path == "agent.py"
    assert remedy in gap.next_action.expects
    assert gap.next_action.kind == "review_warning"
    assert gap.next_action.authorable_by == "human"
    assert gap.next_action.command is None
    assert gap.next_action.declaration_template is None

    _without_recovery(monkeypatch)
    original = _scan(project, tmp_path / "original")
    before = original.release_decision
    after = report.release_decision
    assert after.decision == before.decision == "insufficient_evidence"
    assert report.findings == original.findings
    assert report.source_warnings == original.source_warnings
    assert evidence_below_ie_threshold(after.evidence_coverage, tool_count=report.tool_surface.total_tools) == evidence_below_ie_threshold(
        before.evidence_coverage, tool_count=original.tool_surface.total_tools,
    )
    assert after.evidence_coverage.model_dump(exclude={"evidence_gaps"}) == (
        before.evidence_coverage.model_dump(exclude={"evidence_gaps"})
    )
    old_gaps = before.evidence_coverage.evidence_gaps
    assert len(gaps) == len(old_gaps)
    for new, old in zip(gaps, old_gaps, strict=True):
        assert (new.kind, new.subject) == (old.kind, old.subject)
        # Only the explanation/location is enriched; all operational and
        # declaration fields remain the exact preceding values.
        assert new.next_action.model_dump(exclude={"path", "why", "expects"}) == (
            old.next_action.model_dump(exclude={"path", "why", "expects"})
        )
        assert "recovery" not in old.model_dump(mode="json")


@pytest.mark.parametrize("expression,kind,reason,remedy", CASES)
@pytest.mark.parametrize("verbose", [False, True])
def test_actual_scan_surfaces_keep_named_remedy(tmp_path, capsys, expression, kind, reason, remedy, verbose):
    report = _scan(_project(tmp_path / "project", expression), tmp_path / "reports")
    _print_cli_summary(report, "advisory", 0, verbose=verbose)
    console = capsys.readouterr().out
    packet = build_packet_from_report(report)
    for surface in (console, render_markdown_report(report), render_packet_markdown(packet), render_packet_html(packet)):
        assert "agent.py" in surface
        assert remedy in surface
    assert kind in packet.model_dump_json()


@pytest.mark.parametrize("expression,kind,reason,remedy", CASES)
def test_committed_verify_pr_remedy_and_control_permissions_do_not_drift(
    tmp_path, monkeypatch, expression, kind, reason, remedy,
):
    from test_verify import _commit_all, _init_repo

    repo = _project(_init_repo(tmp_path), expression)
    _commit_all(repo, "SDK coverage example")
    runner = CliRunner()

    def verify():
        result = runner.invoke(app, [
            "verify", "--workspace", str(repo), "--config", "shipgate.yaml",
            "--base", "HEAD", "--head", "HEAD", "--ci-mode", "advisory",
            "--format", "json",
        ])
        assert result.exit_code == 0, result.output
        directory = repo / "agents-shipgate-reports"
        return json.loads((directory / "verifier.json").read_text())

    published = verify()
    assert published["decision"] == "insufficient_evidence"
    assert remedy in (repo / "agents-shipgate-reports" / "pr-comment.md").read_text()
    assert kind in json.dumps(published["release_decision"]["evidence_coverage"])
    _without_recovery(monkeypatch)
    unclassified = verify()
    for key in ("decision", "merge_verdict", "can_merge_without_human"):
        assert published[key] == unclassified[key]
    assert published["control"]["state"] == unclassified["control"]["state"]
    assert published["control"]["permissions"] == unclassified["control"]["permissions"]


def _fact(warning: str, source_id="sdk", **updates):
    return SourceRecoveryEvidence(
        warning=warning, source_id=source_id, source_type="openai_agents_sdk",
        source_ref="agent.py:8", path="agent.py",
        recovery=CoverageRecovery(kind="reader_limitation", reason="observed_reader_gap"),
    ).model_copy(update=updates)


@pytest.mark.parametrize("collision", ["same_text", "redacted", "unclassified", "duplicate_fact"])
def test_ambiguous_public_warning_cannot_borrow_a_repair_owner(tmp_path, collision):
    if collision in {"redacted", "unclassified"}:
        warning_a = "SDK failed: api_key=aaaaaaaaaaaaaaaaaaaaaaaa"
        warning_b = "SDK failed: api_key=bbbbbbbbbbbbbbbbbbbbbbbb"
        assert redact_data(warning_a) == redact_data(warning_b)
    else:
        warning_a = warning_b = "same SDK warning"
    a = LoadedToolSource(
        source_id="a", source_type="openai_agents_sdk", warnings=[warning_a],
        recovery_evidence=[_fact(warning_a, "a")],
    )
    b = LoadedToolSource(
        source_id="b", source_type="openai_agents_sdk", warnings=[warning_b],
        recovery_evidence=[] if collision == "unclassified" else [_fact(warning_b, "b")],
    )
    sources = [a, b]
    if collision == "duplicate_fact":
        a.recovery_evidence.append(a.recovery_evidence[0])
        sources = [a]
    facts = _sanitize_source_recovery_evidence(sources, RedactionStats())
    report = _scan(_project(tmp_path / "project", None), tmp_path / "reports")
    report.source_warnings = [redact_data(warning_a)]  # public output deduplicates
    gap = _evidence_gaps(report, [], source_recovery_evidence=facts)[-1]
    assert gap.recovery == CoverageRecovery(kind="unresolved", reason="ambiguous_warning_identity")
    assert gap.source_ref is None
    assert gap.next_action.path is None
    assert "before assigning a repair owner" in gap.next_action.expects
    report.release_decision.evidence_coverage.evidence_gaps = [gap]
    assert "before assigning a repair owner" in "\n".join(_insufficient_evidence_remedies(report))
    from test_evidence_gap_ranking import _verifier_with

    task = build_fix_task(
        report, merge_verdict="insufficient_evidence", capability_review=VerifierCapabilityReview(),
        base_ref="main", head_ref="HEAD",
    )
    for style in ("findings", "capability-review"):
        comment = render_pr_comment(_verifier_with(task, report), report=report, style=style)
        assert "before assigning a repair owner" in comment


@pytest.mark.parametrize("change", [
    {"source_id": "other"}, {"source_type": "mcp"}, {"warning": "other warning"},
])
def test_evidence_must_belong_to_its_raw_source_and_warning(change):
    source = LoadedToolSource(
        source_id="sdk", source_type="openai_agents_sdk", warnings=["own warning"],
        recovery_evidence=[_fact("own warning").model_copy(update=change)],
    )
    assert _sanitize_source_recovery_evidence([source], RedactionStats()) == []


def test_redaction_keeps_location_and_warning_private():
    secret = "aaaaaaaaaaaaaaaaaaaaaaaa"
    warning = f"SDK failed: api_key={secret}"
    source = LoadedToolSource(
        source_id="sdk", source_type="openai_agents_sdk", warnings=[warning],
        recovery_evidence=[_fact(warning, source_ref=f"agent.py?api_key={secret}")],
    )
    fact = _sanitize_source_recovery_evidence([source], RedactionStats())[0]
    assert secret not in fact.model_dump_json()
    assert fact.recovery.kind == "reader_limitation"


@pytest.mark.parametrize("expression", ["[read_tool, other_tool]", "[read_tool]", "[]"])
def test_supported_syntax_does_not_manufacture_a_recovery(tmp_path, expression):
    report = _scan(_project(tmp_path / "project", expression), tmp_path / "reports")
    assert all(gap.recovery is None for gap in report.release_decision.evidence_coverage.evidence_gaps)


def test_required_sdk_source_uses_the_shared_input_error_contract(tmp_path, monkeypatch):
    """Recovery metadata must not change the execution contract — the
    invariant this test was written for — and #585 decided what that
    contract is: a required source whose path is absent is an input error
    before any adapter runs, not an advisory scan that finished.

    Optional sources keep the warning-and-recovery route; every other case
    in this module covers it.
    """

    project = _project(tmp_path / "project", None)
    manifest = project / "shipgate.yaml"
    manifest.write_text(manifest.read_text().replace("optional: true", "optional: false"))
    monkeypatch.chdir(project)
    result = CliRunner().invoke(app, ["scan", "--config", str(manifest)])
    _without_recovery(monkeypatch)
    original = CliRunner().invoke(app, ["scan", "--config", str(manifest)])
    assert result.exit_code == original.exit_code == 3
    assert "Required tool source unavailable" in result.output
    assert "Required tool source unavailable" in original.output


def test_recovery_does_not_rewrite_space_bearing_source_locations(tmp_path, capsys):
    from test_evidence_gap_ranking import _verifier_with

    project = _project(tmp_path / "project", "[read_tool] + [other_tool]")
    filename = "agent  tools.py"
    (project / "agent.py").rename(project / filename)
    manifest = project / "shipgate.yaml"
    manifest.write_text(manifest.read_text().replace("agent.py", filename))
    report = _scan(project, tmp_path / "reports")
    gap = next(g for g in report.release_decision.evidence_coverage.evidence_gaps if g.recovery)
    assert gap.source_ref == f"{filename}:8"
    assert gap.next_action.path == filename
    _print_cli_summary(report, "advisory", 0)
    packet = build_packet_from_report(report)
    for surface in (
        capsys.readouterr().out, render_markdown_report(report),
        render_packet_markdown(packet), render_packet_html(packet),
    ):
        assert f"Source: {filename}:8" in surface
    task = build_fix_task(
        report, merge_verdict="insufficient_evidence", capability_review=VerifierCapabilityReview(),
        base_ref="main", head_ref="HEAD",
    )
    for style in ("findings", "capability-review"):
        comment = render_pr_comment(_verifier_with(task, report), report=report, style=style)
        assert f"Source: `{filename}:8`" in comment


@pytest.mark.parametrize("schema", [
    "report-schema.v0.43.json", "packet-schema.v0.18.json", "verifier-schema.v0.16.json",
])
def test_recovery_uses_existing_open_evidence_gap_extension(schema):
    document = json.loads((ROOT / "docs" / schema).read_text())
    old_gap = json.loads(json.dumps(document["$defs"]["EvidenceGap"]))
    old_gap["properties"].pop("recovery", None)
    assert old_gap.get("additionalProperties", True) is True
    assert "recovery" not in old_gap.get("required", [])
    # Check against the preceding gap grammar, including its old action
    # vocabulary. Its openness is what permits this same-version addition.
    old_gap["$defs"] = document["$defs"]
    gap = EvidenceGap(
        kind="source_warning", subject="SDK", why="A source degraded.",
        next_action={"kind": "review_warning", "why": "Review source.", "expects": "Rerun."},
        recovery=CoverageRecovery(kind="unresolved", reason="sdk_tools_expression_unresolved"),
    )
    jsonschema.validate(gap.model_dump(mode="json"), old_gap)
    assert EvidenceGap.model_validate(gap.model_dump(mode="json")) == gap
