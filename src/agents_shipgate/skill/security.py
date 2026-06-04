from __future__ import annotations

import re

from agents_shipgate.core.privacy import (
    LABELED_SECRET_PATTERN,
    SECRET_PATTERNS,
    looks_like_secret_value,
)
from agents_shipgate.schemas.report import Finding
from agents_shipgate.skill.models import SkillArtifact, SkillReviewConfig, TextSegment
from agents_shipgate.skill.rules_common import (
    excerpt,
    has_confirmation,
    has_dry_run,
    iter_lines,
    mutating_line,
    skill_finding,
)

PI_OVERRIDE_RE = re.compile(
    r"\b(ignore|override|disregard|replace)\b.{0,60}\b(system|developer|user|previous|prior)\b.{0,40}\b(instructions?|message|prompt|policy)\b|"
    r"\b(ignore|disregard)\b.{0,30}\b(previous|prior)\b.{0,20}\binstructions?\b",
    re.IGNORECASE,
)
PI_HIDE_RE = re.compile(
    r"\b(do not tell|don't tell|do not log|don't log|hide (this|the)|secretly|silently)\b",
    re.IGNORECASE,
)
CREDENTIAL_READ_RE = re.compile(
    r"(\.env\b|~/\.ssh|\.ssh/id_[A-Za-z0-9_-]+|\.aws/credentials|\.npmrc|\.pypirc|gh/hosts\.yml|GITHUB_TOKEN|OPENAI_API_KEY|ANTHROPIC_API_KEY)",
    re.IGNORECASE,
)
REMOTE_SHELL_RE = re.compile(
    r"\b(?:curl|wget)\b[^\n|;]*(?:https?://[^\s|;]+)[^\n|;]*(?:\|\s*(?:bash|sh|zsh|python|ruby|perl)|-\s*O-\s*\|\s*(?:bash|sh))|"
    r"\b(?:bash|sh|zsh)\s+<\s*\(\s*(?:curl|wget)\b[^\n)]*https?://",
    re.IGNORECASE,
)
REMOTE_EXEC_RE = re.compile(
    r"\b(?:source|\.)\s+<\s*\(\s*(?:curl|wget)\b[^\n)]*https?://|"
    r"\bexec\s*\(\s*(?:requests|urllib)[^\n]+https?://|"
    r"\bpython\s+-c\b[^\n]+(?:requests|urllib)[^\n]+exec\s*\(",
    re.IGNORECASE,
)
REMOTE_INSTRUCTION_RE = re.compile(
    r"\b(?:fetch|load|read|pull|download|curl|wget)\b[^\n]{0,120}https?://[^\s)]+[^\n]{0,80}\b(prompt|instruction|skill|system message|developer message)\b|"
    r"https?://[^\s)]*(?:prompt|instruction|SKILL\.md|system-message)[^\s)]*",
    re.IGNORECASE,
)
READ_ONLY_DESC_RE = re.compile(
    r"\b(read-only|only reads?|inspect|review|analy[sz]e|summari[sz]e|report)\b",
    re.IGNORECASE,
)
DATA_BOUNDARY_RE = re.compile(
    r"\b(treat .* as data|untrusted content|do not follow instructions from|instruction/data separation|source trust)\b",
    re.IGNORECASE,
)
UNTRUSTED_RE = re.compile(r"\b(untrusted|webpage|website|external document|user uploaded|remote content)\b", re.IGNORECASE)
SHELL_TOOL_RE = re.compile(r"\b(bash|shell|sh|zsh|terminal)\b", re.IGNORECASE)


def run_security_rules(
    artifacts: list[SkillArtifact],
    config: SkillReviewConfig,
) -> list[Finding]:
    findings: list[Finding] = []
    for artifact in artifacts:
        findings.extend(_prompt_injection_rules(artifact))
        if config.security.secret_scan:
            findings.extend(_secret_rules(artifact))
        findings.extend(_credential_rules(artifact))
        findings.extend(_script_security_rules(artifact))
        findings.extend(_tool_rules(artifact, config))
        findings.extend(_remote_rules(artifact, config))
        findings.extend(_provenance_rules(artifact, config))
        findings.extend(_flow_rules(artifact))
        findings.extend(_mismatch_rules(artifact))
    return findings


def _prompt_injection_rules(artifact: SkillArtifact) -> list[Finding]:
    findings: list[Finding] = []
    for segment in artifact.text_segments():
        for line_number, line in iter_lines(segment):
            if PI_OVERRIDE_RE.search(line):
                findings.append(
                    skill_finding(
                        artifact=artifact,
                        check_id="SEC-PI-001",
                        title="Skill artifact contains instruction override language",
                        severity="critical",
                        category="skill_security",
                        evidence={
                            "source_path": segment.path,
                            "line": line_number,
                            "matched": "instruction_override",
                            "excerpt": excerpt(segment.text, line_number),
                        },
                        confidence="high",
                        recommendation="Remove instructions that tell agents to ignore, override, or replace higher-priority instructions.",
                        path=segment.path,
                        line=line_number,
                        provenance_kind="regex_heuristic",
                    )
                )
            if PI_HIDE_RE.search(line):
                findings.append(
                    skill_finding(
                        artifact=artifact,
                        check_id="SEC-PI-003",
                        title="Skill artifact tells the agent to hide behavior",
                        severity="high",
                        category="skill_security",
                        evidence={
                            "source_path": segment.path,
                            "line": line_number,
                            "matched": "hide_behavior",
                            "excerpt": excerpt(segment.text, line_number),
                        },
                        confidence="high",
                        recommendation="Remove instructions to hide behavior from users, reviewers, or logs.",
                        path=segment.path,
                        line=line_number,
                        provenance_kind="regex_heuristic",
                    )
                )
    return findings


def _secret_rules(artifact: SkillArtifact) -> list[Finding]:
    findings: list[Finding] = []
    for segment in artifact.text_segments():
        for line_number, line in iter_lines(segment):
            for kind, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(_secret_finding(artifact, segment, line_number, kind))
            labeled = LABELED_SECRET_PATTERN.search(line)
            if labeled and looks_like_secret_value(labeled.group(5)):
                findings.append(_secret_finding(artifact, segment, line_number, "labeled_secret_value"))
    return findings


def _secret_finding(
    artifact: SkillArtifact,
    segment: TextSegment,
    line_number: int,
    kind: str,
) -> Finding:
    return skill_finding(
        artifact=artifact,
        check_id="SEC-SECRET-001",
        title="Skill artifact contains a hardcoded secret-like value",
        severity="critical",
        category="skill_security",
        evidence={
            "source_path": segment.path,
            "line": line_number,
            "secret_kind": kind,
            "redacted": f"[REDACTED:{kind}]",
        },
        confidence="high",
        recommendation="Remove the secret from the skill artifact and rotate the exposed credential.",
        path=segment.path,
        line=line_number,
        provenance_kind="regex_heuristic",
    )


def _credential_rules(artifact: SkillArtifact) -> list[Finding]:
    findings: list[Finding] = []
    for segment in artifact.text_segments():
        for line_number, line in iter_lines(segment):
            match = CREDENTIAL_READ_RE.search(line)
            if not match:
                continue
            findings.append(
                skill_finding(
                    artifact=artifact,
                    check_id="SEC-SECRET-003",
                    title="Skill artifact instructs credential or secret-file access",
                    severity="high",
                    category="skill_security",
                    evidence={
                        "source_path": segment.path,
                        "line": line_number,
                        "credential_reference": match.group(1),
                    },
                    confidence="high",
                    recommendation="Remove broad credential-file access or replace it with explicit placeholder-based credential guidance.",
                    path=segment.path,
                    line=line_number,
                    provenance_kind="regex_heuristic",
                )
            )
    return findings


def _script_security_rules(artifact: SkillArtifact) -> list[Finding]:
    findings: list[Finding] = []
    for segment in artifact.text_segments():
        for line_number, line in iter_lines(segment):
            if REMOTE_SHELL_RE.search(line):
                findings.append(
                    skill_finding(
                        artifact=artifact,
                        check_id="SEC-SCRIPT-001",
                        title="Remote content is piped to a shell or interpreter",
                        severity="critical",
                        category="skill_security",
                        evidence={
                            "source_path": segment.path,
                            "line": line_number,
                            "excerpt": excerpt(segment.text, line_number),
                        },
                        confidence="high",
                        recommendation="Vendor the script or pin and verify remote content instead of piping it directly to an interpreter.",
                        path=segment.path,
                        line=line_number,
                        provenance_kind="regex_heuristic",
                    )
                )
            if mutating_line(line) and not (has_dry_run(segment.text) or has_confirmation(segment.text)):
                findings.append(
                    skill_finding(
                        artifact=artifact,
                        check_id="SEC-SCRIPT-002",
                        title="Destructive or stateful command lacks guardrails",
                        severity="critical",
                        category="skill_security",
                        evidence={
                            "source_path": segment.path,
                            "line": line_number,
                            "excerpt": excerpt(segment.text, line_number),
                        },
                        confidence="medium",
                        recommendation="Add dry-run, confirmation, path validation, or remove destructive commands from the skill workflow.",
                        path=segment.path,
                        line=line_number,
                        provenance_kind="regex_heuristic",
                    )
                )
            if REMOTE_EXEC_RE.search(line):
                findings.append(
                    skill_finding(
                        artifact=artifact,
                        check_id="SEC-REMOTE-002",
                        title="Remote content is fetched and executed",
                        severity="critical",
                        category="skill_security",
                        evidence={
                            "source_path": segment.path,
                            "line": line_number,
                            "excerpt": excerpt(segment.text, line_number),
                        },
                        confidence="high",
                        recommendation="Remove runtime remote-code execution or pin, verify, and sandbox the content explicitly.",
                        path=segment.path,
                        line=line_number,
                        provenance_kind="regex_heuristic",
                    )
                )
    return findings


def _tool_rules(artifact: SkillArtifact, config: SkillReviewConfig) -> list[Finding]:
    if artifact.kind != "agent_skill" or config.security.allow_shell_preapproval:
        return []
    if not any(SHELL_TOOL_RE.search(tool) for tool in artifact.allowed_tools):
        return []
    text = f"{artifact.raw_text}\n{artifact.body}"
    if re.search(r"\b(justification|reviewed|sandbox|allowlist)\b", text, re.IGNORECASE):
        return []
    return [
        skill_finding(
            artifact=artifact,
            check_id="SEC-TOOL-001",
            title="Skill pre-approves shell or bash without justification",
            severity="high",
            category="skill_security",
            evidence={"allowed_tools": artifact.allowed_tools},
            confidence="high",
            recommendation="Remove shell/bash preapproval or add reviewed-script and sandbox justification.",
            line=artifact.frontmatter_field_lines.get("allowed-tools", artifact.frontmatter_start_line),
            provenance_kind="static_declaration",
        )
    ]


def _remote_rules(artifact: SkillArtifact, config: SkillReviewConfig) -> list[Finding]:
    if config.security.allow_remote_instruction_fetch:
        return []
    findings: list[Finding] = []
    for segment in artifact.text_segments():
        for line_number, line in iter_lines(segment):
            if REMOTE_INSTRUCTION_RE.search(line):
                findings.append(
                    skill_finding(
                        artifact=artifact,
                        check_id="SEC-REMOTE-001",
                        title="Skill fetches remote instruction content",
                        severity="medium",
                        category="skill_security",
                        evidence={
                            "source_path": segment.path,
                            "line": line_number,
                            "excerpt": excerpt(segment.text, line_number),
                        },
                        confidence="medium",
                        recommendation="Avoid fetching mutable remote instructions at runtime or document source trust and pinning.",
                        path=segment.path,
                        line=line_number,
                        provenance_kind="regex_heuristic",
                    )
                )
    return findings


def _provenance_rules(artifact: SkillArtifact, config: SkillReviewConfig) -> list[Finding]:
    if artifact.kind != "agent_skill" or not config.security.require_provenance_for_third_party_skills:
        return []
    third_party = artifact.metadata.get("third_party") is True or artifact.metadata.get("third-party") is True
    metadata = artifact.metadata.get("metadata")
    shipgate_metadata = metadata.get("shipgate") if isinstance(metadata, dict) else None
    if not third_party or isinstance(shipgate_metadata, dict) and shipgate_metadata.get("source"):
        return []
    return [
        skill_finding(
            artifact=artifact,
            check_id="SEC-PROV-001",
            title="Third-party skill lacks provenance metadata",
            severity="high",
            category="skill_security",
            evidence={"third_party": True},
            confidence="high",
            recommendation="Add metadata.shipgate.source, source_ref, owner, and review metadata for third-party skills.",
            line=artifact.frontmatter_start_line,
            provenance_kind="static_declaration",
        )
    ]


def _flow_rules(artifact: SkillArtifact) -> list[Finding]:
    combined = "\n".join(segment.text for segment in artifact.text_segments())
    if not UNTRUSTED_RE.search(combined):
        return []
    has_outbound_or_secret = bool(
        re.search(r"\b(curl|wget|requests\.post|slack|email|github|jira|linear|\.env|secret|token)\b", combined, re.IGNORECASE)
    )
    if not has_outbound_or_secret or DATA_BOUNDARY_RE.search(combined):
        return []
    return [
        skill_finding(
            artifact=artifact,
            check_id="SEC-FLOW-004",
            title="Skill lacks data and instruction separation guidance",
            severity="medium",
            category="skill_security",
            evidence={"untrusted_content": True, "outbound_or_secret_access": True},
            confidence="medium",
            recommendation="Add guidance that untrusted content is data only and must not supply agent instructions.",
            line=artifact.body_start_line,
            provenance_kind="keyword_heuristic",
        )
    ]


def _mismatch_rules(artifact: SkillArtifact) -> list[Finding]:
    if artifact.kind != "agent_skill" or not artifact.description:
        return []
    if not READ_ONLY_DESC_RE.search(artifact.description):
        return []
    mutating_scripts = [
        script.path
        for script in artifact.scripts
        if script.text and any(mutating_line(line) for line in script.text.splitlines())
    ]
    if not mutating_scripts:
        return []
    return [
        skill_finding(
            artifact=artifact,
            check_id="SEC-MISMATCH-001",
            title="Skill declares read-only behavior but bundled scripts mutate state",
            severity="high",
            category="skill_security",
            evidence={"mutating_scripts": mutating_scripts},
            confidence="high",
            recommendation="Update the declared purpose to include state changes or remove mutating script behavior.",
            line=artifact.frontmatter_field_lines.get("description", artifact.frontmatter_start_line),
            provenance_kind="static_declaration",
        )
    ]
