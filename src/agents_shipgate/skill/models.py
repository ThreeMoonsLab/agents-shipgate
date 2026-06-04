from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_shipgate.schemas.common import Severity
from agents_shipgate.schemas.report import Finding

SkillCommand = Literal["lint", "security", "review"]
ArtifactKind = Literal["agent_skill", "agent_instruction"]
GateVerdict = Literal["pass", "warn", "block"]


class SkillScanPaths(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skills: list[str] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)


class SkillScanConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paths: SkillScanPaths = Field(default_factory=SkillScanPaths)
    ignore: list[str] = Field(default_factory=list)


class SkillPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "default"
    fail_on: list[Severity] | None = None
    warn_on: list[Severity] = Field(default_factory=lambda: ["medium"])
    allow_low_confidence_block: bool = False


class SkillLintConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    max_skill_md_lines: int = 500
    max_description_chars: int = 1024
    require_sections_for_high_risk_skills: list[str] = Field(default_factory=list)
    require_eval_stub_for_new_skill: bool = False


class SkillSecurityConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    allow_shell_preapproval: bool = False
    allow_remote_instruction_fetch: bool = False
    allow_remote_code_execution: bool = False
    require_provenance_for_third_party_skills: bool = True
    require_dry_run_for_stateful_scripts: bool = True
    allowed_domains: list[str] = Field(default_factory=list)
    denied_domains: list[str] = Field(default_factory=list)
    secret_scan: bool = True
    llm_semantic_scan: bool = False


class SkillSuppression(BaseModel):
    model_config = ConfigDict(extra="allow")

    rule_id: str
    path: str | None = None
    reason: str
    approved_by: str | None = None
    expires_at: str | None = None


class SkillReviewConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: int | str = 1
    scan: SkillScanConfig = Field(default_factory=SkillScanConfig)
    policy: SkillPolicyConfig = Field(default_factory=SkillPolicyConfig)
    lint: SkillLintConfig = Field(default_factory=SkillLintConfig)
    security: SkillSecurityConfig = Field(default_factory=SkillSecurityConfig)
    suppressions: list[SkillSuppression] = Field(default_factory=list)


class FileSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    role: str
    size_bytes: int = 0
    line_count: int = 0
    executable: bool = False
    text: str | None = Field(default=None, exclude=True)


class MarkdownLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str
    line: int
    text: str | None = None


class CommandRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str
    line: int
    source_path: str
    context: str = ""


class TextSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    text: str
    start_line: int = 1
    role: str


class SkillArtifact(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: ArtifactKind
    path: str
    root_dir: str
    name: str | None = None
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    frontmatter_error: str | None = None
    frontmatter_start_line: int = 1
    frontmatter_field_lines: dict[str, int] = Field(default_factory=dict)
    body_start_line: int = 1
    body_line_count: int = 0
    raw_text: str = Field(default="", exclude=True)
    body: str = Field(default="", exclude=True)
    sections: dict[str, int] = Field(default_factory=dict)
    links: list[MarkdownLink] = Field(default_factory=list)
    external_urls: list[str] = Field(default_factory=list)
    referenced_paths: list[str] = Field(default_factory=list)
    commands_in_markdown: list[CommandRef] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    scripts: list[FileSummary] = Field(default_factory=list)
    references: list[FileSummary] = Field(default_factory=list)
    assets: list[FileSummary] = Field(default_factory=list)
    other_files: list[FileSummary] = Field(default_factory=list)

    def text_segments(self, *, include_related: bool = True) -> list[TextSegment]:
        segments = [
            TextSegment(
                path=self.path,
                text=self.raw_text,
                start_line=1,
                role=self.kind,
            )
        ]
        if not include_related:
            return segments
        for role, files in (
            ("skill_script", self.scripts),
            ("skill_reference", self.references),
            ("skill_asset", self.assets),
            ("skill_other_file", self.other_files),
        ):
            for item in files:
                if item.text:
                    segments.append(
                        TextSegment(
                            path=item.path,
                            text=item.text,
                            start_line=1,
                            role=role,
                        )
                    )
        return segments


class SkillReviewSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: SkillCommand
    verdict: GateVerdict
    artifact_count: int = 0
    finding_count: int = 0
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    info_count: int = 0
    suppressed_count: int = 0


class SkillArtifactSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ArtifactKind
    path: str
    root_dir: str
    name: str | None = None
    description: str | None = None
    script_count: int = 0
    reference_count: int = 0
    asset_count: int = 0
    external_urls: list[str] = Field(default_factory=list)


class SkillReviewReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = "0.1"
    report_type: str
    command: SkillCommand
    workspace: str
    config_path: str | None = None
    ci_mode: str = "advisory"
    fail_on: list[Severity] = Field(default_factory=list)
    summary: SkillReviewSummary
    artifacts: list[SkillArtifactSummary] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    generated_reports: dict[str, str] = Field(default_factory=dict)
    source_warnings: list[str] = Field(default_factory=list)
