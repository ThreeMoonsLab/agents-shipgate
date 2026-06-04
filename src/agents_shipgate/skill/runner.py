from __future__ import annotations

import fnmatch
import os
from collections import Counter
from pathlib import Path

from agents_shipgate.ci.exit_policy import GATE_FAILURE_EXIT_CODE
from agents_shipgate.core.errors import ConfigError
from agents_shipgate.core.findings.identity import assign_finding_ids, dedupe_findings
from agents_shipgate.schemas.common import Severity
from agents_shipgate.schemas.report import Finding
from agents_shipgate.skill.config import infer_workspace, load_skill_review_config
from agents_shipgate.skill.discovery import discover_skill_artifacts
from agents_shipgate.skill.lint import run_lint_rules
from agents_shipgate.skill.models import (
    GateVerdict,
    SkillArtifact,
    SkillArtifactSummary,
    SkillCommand,
    SkillReviewReport,
    SkillReviewSummary,
)
from agents_shipgate.skill.report import (
    write_skill_json_report,
    write_skill_markdown_report,
    write_skill_sarif_report,
)
from agents_shipgate.skill.security import run_security_rules

REPORT_BASENAME = {
    "lint": "skill-lint",
    "security": "skill-security",
    "review": "skill-review",
}


def run_skill_review(
    *,
    command: SkillCommand,
    paths: list[Path] | None = None,
    config_path: Path | None = None,
    formats: list[str] | None = None,
    output_dir: Path | None = None,
    ci_mode: str = "advisory",
    fail_on: list[Severity] | None = None,
    changed_files: Path | None = None,
) -> tuple[SkillReviewReport, int]:
    if ci_mode not in {"advisory", "strict"}:
        raise ConfigError("--ci-mode must be advisory or strict")
    config, loaded_config_path = load_skill_review_config(config_path)
    workspace = (
        _infer_workspace_from_paths(paths)
        if loaded_config_path is None and paths
        else infer_workspace(loaded_config_path or config_path)
    )
    artifacts, warnings = discover_skill_artifacts(
        workspace=workspace,
        config=config,
        paths=paths,
        changed_files=changed_files,
    )
    findings = _run_rules(command, artifacts, config)
    findings = dedupe_findings(findings)
    _apply_suppressions(findings, config.suppressions)
    assign_finding_ids(findings)

    resolved_fail_on = _effective_fail_on(
        command=command,
        ci_mode=ci_mode,
        cli_fail_on=fail_on,
        config_fail_on=config.policy.fail_on,
    )
    report = SkillReviewReport(
        report_type=REPORT_BASENAME[command],
        command=command,
        workspace=str(workspace),
        config_path=str(loaded_config_path) if loaded_config_path else None,
        ci_mode=ci_mode,
        fail_on=resolved_fail_on,
        summary=_summary(command, artifacts, findings),
        artifacts=[_artifact_summary(artifact) for artifact in artifacts],
        findings=findings,
        source_warnings=warnings,
    )
    out_dir = output_dir or workspace / "agents-shipgate-reports"
    _write_reports(report, out_dir, formats or ["markdown", "json"])
    exit_code = _exit_code(report, resolved_fail_on)
    return report, exit_code


def _run_rules(command: SkillCommand, artifacts: list[SkillArtifact], config) -> list[Finding]:
    findings: list[Finding] = []
    if command in {"lint", "review"}:
        findings.extend(run_lint_rules(artifacts, config))
    if command in {"security", "review"}:
        findings.extend(run_security_rules(artifacts, config))
    return findings


def _effective_fail_on(
    *,
    command: SkillCommand,
    ci_mode: str,
    cli_fail_on: list[Severity] | None,
    config_fail_on: list[Severity] | None,
) -> list[Severity]:
    if cli_fail_on is not None:
        return cli_fail_on
    if config_fail_on is not None:
        return config_fail_on
    if ci_mode == "strict":
        return ["critical", "high"]
    return []


def _summary(
    command: SkillCommand,
    artifacts: list[SkillArtifact],
    findings: list[Finding],
) -> SkillReviewSummary:
    active = [finding for finding in findings if not finding.suppressed]
    counts = Counter(finding.severity for finding in active)
    return SkillReviewSummary(
        command=command,
        verdict=_verdict(active),
        artifact_count=len(artifacts),
        finding_count=len(active),
        critical_count=counts["critical"],
        high_count=counts["high"],
        medium_count=counts["medium"],
        low_count=counts["low"],
        info_count=counts["info"],
        suppressed_count=len(findings) - len(active),
    )


def _verdict(active: list[Finding]) -> GateVerdict:
    if any(finding.severity in {"critical", "high"} for finding in active):
        return "block"
    if active:
        return "warn"
    return "pass"


def _artifact_summary(artifact: SkillArtifact) -> SkillArtifactSummary:
    return SkillArtifactSummary(
        kind=artifact.kind,
        path=artifact.path,
        root_dir=artifact.root_dir,
        name=artifact.name,
        description=artifact.description,
        script_count=len(artifact.scripts),
        reference_count=len(artifact.references),
        asset_count=len(artifact.assets),
        external_urls=artifact.external_urls,
    )


def _write_reports(
    report: SkillReviewReport,
    output_dir: Path,
    formats: list[str],
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    basename = REPORT_BASENAME[report.command]
    generated = {
        key: str(output_dir / f"{basename}.{suffix}")
        for key, suffix in (
            ("json", "json"),
            ("markdown", "md"),
            ("sarif", "sarif"),
        )
        if key in formats
    }
    report.generated_reports = generated
    if "json" in formats:
        path = Path(generated["json"])
        write_skill_json_report(report, path)
    if "markdown" in formats:
        path = Path(generated["markdown"])
        write_skill_markdown_report(report, path)
    if "sarif" in formats:
        path = Path(generated["sarif"])
        write_skill_sarif_report(report, path)
    return generated


def _exit_code(report: SkillReviewReport, fail_on: list[Severity]) -> int:
    if fail_on and any(
        finding.severity in fail_on and not finding.suppressed
        for finding in report.findings
    ):
        return GATE_FAILURE_EXIT_CODE
    return 0


def _apply_suppressions(findings: list[Finding], suppressions) -> None:
    for suppression in suppressions:
        if not suppression.reason.strip():
            raise ConfigError("Skill review suppressions require a non-empty reason")
        for finding in findings:
            if finding.check_id != suppression.rule_id:
                continue
            if suppression.path and not _matches_path(finding, suppression.path):
                continue
            finding.suppressed = True
            finding.suppression_reason = suppression.reason


def _matches_path(finding: Finding, pattern: str) -> bool:
    candidates = []
    if finding.source and finding.source.path:
        candidates.append(finding.source.path)
    artifact_path = finding.evidence.get("artifact_path")
    if isinstance(artifact_path, str):
        candidates.append(artifact_path)
    return any(fnmatch.fnmatch(candidate, pattern) for candidate in candidates)


def _infer_workspace_from_paths(paths: list[Path]) -> Path:
    if all(not path.is_absolute() for path in paths):
        return Path.cwd()
    candidates: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved.is_file():
            candidates.append(resolved.parent)
        else:
            candidates.append(resolved)
    if not candidates:
        return Path.cwd()
    try:
        return Path(os.path.commonpath([str(path) for path in candidates]))
    except ValueError:
        return candidates[0]
