from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from agents_shipgate.core.errors import ConfigError
from agents_shipgate.skill.models import SkillReviewConfig

DEFAULT_SKILL_PATTERNS = [
    ".github/skills/**/SKILL.md",
    ".claude/skills/**/SKILL.md",
    ".agents/skills/**/SKILL.md",
    ".codex/skills/**/SKILL.md",
    ".cursor/skills/**/SKILL.md",
    ".skills/**/SKILL.md",
    "skills/**/SKILL.md",
]

DEFAULT_INSTRUCTION_PATTERNS = [
    "AGENTS.md",
    "AGENTS.override.md",
    "CLAUDE.md",
    "CODEX.md",
    ".cursorrules",
    ".cursor/rules/**",
    ".github/copilot-instructions.md",
    ".github/instructions/**",
    ".github/prompts/**",
]


def load_skill_review_config(path: Path | None) -> tuple[SkillReviewConfig, Path | None]:
    """Load optional `.shipgate/skill-review.yml` configuration."""
    if path is None:
        return _with_defaults(SkillReviewConfig()), None
    if not path.exists():
        return _with_defaults(SkillReviewConfig()), None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Unable to parse skill review config {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Unable to read skill review config {path}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("Skill review config must be a YAML mapping")
    try:
        config = SkillReviewConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid skill review config {path}: {exc}") from exc
    return _with_defaults(config, raw=raw), path


def infer_workspace(config_path: Path | None) -> Path:
    if config_path is None or not config_path.exists():
        return Path.cwd()
    resolved = config_path.resolve()
    if resolved.parent.name == ".shipgate":
        return resolved.parent.parent
    return resolved.parent


def _with_defaults(
    config: SkillReviewConfig,
    *,
    raw: dict[str, Any] | None = None,
) -> SkillReviewConfig:
    raw_paths = _raw_paths(raw or {})
    skills_provided = "skills" in raw_paths
    instructions_provided = "instructions" in raw_paths
    updates: dict[str, Any] = {}
    if not skills_provided and not config.scan.paths.skills:
        updates.setdefault("scan", config.scan.model_copy(deep=True))
        updates["scan"].paths.skills = list(DEFAULT_SKILL_PATTERNS)
    if not instructions_provided and not config.scan.paths.instructions:
        updates.setdefault("scan", config.scan.model_copy(deep=True))
        updates["scan"].paths.instructions = list(DEFAULT_INSTRUCTION_PATTERNS)
    if updates:
        return config.model_copy(update=updates)
    return config


def _raw_paths(raw: dict[str, Any]) -> dict[str, Any]:
    scan = raw.get("scan")
    if not isinstance(scan, dict):
        return {}
    paths = scan.get("paths")
    return paths if isinstance(paths, dict) else {}
