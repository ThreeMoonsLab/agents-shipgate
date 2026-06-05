from __future__ import annotations

import re

from agents_shipgate.schemas.report import Finding
from agents_shipgate.skill.models import SkillArtifact, SkillReviewConfig
from agents_shipgate.skill.rules_common import (
    has_dry_run,
    has_section,
    mutating_line,
    skill_finding,
)

GENERIC_DESCRIPTION_RE = re.compile(
    r"\b(helps? with code|improves? quality|best practices|general assistant|"
    r"useful for development|development tasks|coding tasks)\b",
    re.IGNORECASE,
)
OVERBROAD_RE = re.compile(
    r"\b(any|all|everything|general|entire codebase|all tasks|any task|all code)\b",
    re.IGNORECASE,
)


def run_lint_rules(
    artifacts: list[SkillArtifact],
    config: SkillReviewConfig,
) -> list[Finding]:
    findings: list[Finding] = []
    for artifact in artifacts:
        if artifact.kind != "agent_skill":
            continue
        findings.extend(_spec_rules(artifact))
        findings.extend(_description_rules(artifact, config))
        findings.extend(_body_rules(artifact))
        findings.extend(_script_rules(artifact))
    return findings


def _spec_rules(artifact: SkillArtifact) -> list[Finding]:
    findings: list[Finding] = []
    if artifact.frontmatter_error:
        findings.append(
            skill_finding(
                artifact=artifact,
                check_id="LINT-SPEC-002",
                title="Skill has invalid YAML frontmatter",
                severity="high",
                category="skill_lint",
                evidence={"error": artifact.frontmatter_error},
                confidence="high",
                recommendation="Fix SKILL.md YAML frontmatter so it is a valid mapping.",
                line=artifact.frontmatter_start_line,
            )
        )
    if not artifact.name:
        findings.append(
            skill_finding(
                artifact=artifact,
                check_id="LINT-SPEC-003",
                title="Skill is missing required name frontmatter",
                severity="high",
                category="skill_lint",
                evidence={"missing_field": "name"},
                confidence="high",
                recommendation="Add a non-empty `name` field to SKILL.md frontmatter.",
                line=artifact.frontmatter_start_line,
            )
        )
    if not artifact.description:
        findings.append(
            skill_finding(
                artifact=artifact,
                check_id="LINT-SPEC-004",
                title="Skill is missing required description frontmatter",
                severity="high",
                category="skill_lint",
                evidence={"missing_field": "description"},
                confidence="high",
                recommendation="Add a clear `description` explaining exactly when agents should use the skill.",
                line=artifact.frontmatter_start_line,
            )
        )
    return findings


def _description_rules(
    artifact: SkillArtifact,
    config: SkillReviewConfig,
) -> list[Finding]:
    description = artifact.description or ""
    if not description:
        return []
    findings: list[Finding] = []
    line = artifact.frontmatter_field_lines.get("description", artifact.frontmatter_start_line)
    too_short = len(description.strip()) < 40
    generic = bool(GENERIC_DESCRIPTION_RE.search(description))
    if too_short or generic:
        findings.append(
            skill_finding(
                artifact=artifact,
                check_id="LINT-DESC-001",
                title="Skill description is too vague to route reliably",
                severity="high",
                category="skill_lint",
                evidence={
                    "description_length": len(description.strip()),
                    "generic_phrase": generic,
                },
                confidence="medium",
                recommendation="Rewrite the description with concrete trigger conditions, domain nouns, and when-to-use guidance.",
                line=line,
                provenance_kind="keyword_heuristic",
            )
        )
    max_chars = config.lint.max_description_chars
    overbroad = bool(OVERBROAD_RE.search(description))
    if overbroad or len(description) > max_chars:
        findings.append(
            skill_finding(
                artifact=artifact,
                check_id="LINT-DESC-003",
                title="Skill description is overbroad and may false-trigger",
                severity="medium",
                category="skill_lint",
                evidence={
                    "description_length": len(description),
                    "max_description_chars": max_chars,
                    "overbroad_phrase": overbroad,
                },
                confidence="medium",
                recommendation="Narrow the description to the specific workflow, files, or domain where this skill should activate.",
                line=line,
                provenance_kind="keyword_heuristic",
            )
        )
    return findings


def _body_rules(artifact: SkillArtifact) -> list[Finding]:
    findings: list[Finding] = []
    if not has_section(artifact, {"procedure", "steps", "workflow"}) and not _has_numbered_steps(
        artifact.body
    ):
        findings.append(
            skill_finding(
                artifact=artifact,
                check_id="LINT-BODY-001",
                title="Skill body lacks a step-by-step procedure",
                severity="medium",
                category="skill_lint",
                evidence={"sections": sorted(artifact.sections)},
                confidence="medium",
                recommendation="Add a Procedure, Steps, or Workflow section with ordered actions an agent can follow.",
                line=artifact.body_start_line,
                provenance_kind="keyword_heuristic",
            )
        )
    if not has_section(
        artifact,
        {
            "output",
            "outputs",
            "deliverable",
            "deliverables",
            "result",
            "results",
            "final response",
            "response",
            "reporting",
            "summary",
            "fast paths",
        },
    ):
        findings.append(
            skill_finding(
                artifact=artifact,
                check_id="LINT-BODY-003",
                title="Skill body lacks an output contract",
                severity="medium",
                category="skill_lint",
                evidence={"sections": sorted(artifact.sections)},
                confidence="medium",
                recommendation="Add an Output section that defines the expected artifact, response shape, or completion criteria.",
                line=artifact.body_start_line,
                provenance_kind="keyword_heuristic",
            )
        )
    if not has_section(
        artifact,
        {
            "verification",
            "quality",
            "tests",
            "checks",
            "acceptance",
            "acceptance criteria",
            "quality gates",
            "failure modes",
            "boundaries",
            "error handling",
            "if something errors out",
            "troubleshooting",
        },
    ):
        findings.append(
            skill_finding(
                artifact=artifact,
                check_id="LINT-BODY-004",
                title="Skill body lacks verification criteria",
                severity="medium",
                category="skill_lint",
                evidence={"sections": sorted(artifact.sections)},
                confidence="medium",
                recommendation="Add verification or acceptance criteria so an agent can tell whether the skill was applied correctly.",
                line=artifact.body_start_line,
                provenance_kind="keyword_heuristic",
            )
        )
    return findings


def _script_rules(artifact: SkillArtifact) -> list[Finding]:
    findings: list[Finding] = []
    body = artifact.body.lower()
    for script in artifact.scripts:
        text = script.text or ""
        documented_help = (
            f"{script.path} --help".lower() in body
            or f"{script.path.split('/')[-1]} --help".lower() in body
            or "--help" in text
        )
        if not documented_help:
            findings.append(
                skill_finding(
                    artifact=artifact,
                    check_id="LINT-SCRIPT-001",
                    title="Skill script lacks documented --help usage",
                    severity="medium",
                    category="skill_lint",
                    evidence={"script_path": script.path},
                    confidence="medium",
                    recommendation="Document safe script invocation in SKILL.md and provide a `--help` mode for agent users.",
                    path=script.path,
                    line=1,
                    provenance_kind="keyword_heuristic",
                )
            )
        if any(mutating_line(line) for line in text.splitlines()) and not has_dry_run(text):
            findings.append(
                skill_finding(
                    artifact=artifact,
                    check_id="LINT-SCRIPT-004",
                    title="Stateful skill script lacks dry-run support",
                    severity="medium",
                    category="skill_lint",
                    evidence={"script_path": script.path},
                    confidence="medium",
                    recommendation="Add `--dry-run` support or document a non-mutating preview path before stateful script execution.",
                    path=script.path,
                    line=1,
                    provenance_kind="keyword_heuristic",
                )
            )
    return findings


def _has_numbered_steps(body: str) -> bool:
    return len(re.findall(r"(?m)^\s*\d+\.\s+\S+", body)) >= 2
