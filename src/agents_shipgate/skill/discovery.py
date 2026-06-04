from __future__ import annotations

import fnmatch
import hashlib
from pathlib import Path

from agents_shipgate.core.errors import InputParseError
from agents_shipgate.inputs.common import manifest_relative_path
from agents_shipgate.skill.models import SkillArtifact, SkillReviewConfig
from agents_shipgate.skill.parser import parse_instruction_file, parse_skill_file


def discover_skill_artifacts(
    *,
    workspace: Path,
    config: SkillReviewConfig,
    paths: list[Path] | None = None,
    changed_files: Path | None = None,
) -> tuple[list[SkillArtifact], list[str]]:
    workspace = workspace.resolve()
    changed = _load_changed_files(changed_files) if changed_files else None
    skill_files, instruction_files = _candidate_files(
        workspace=workspace,
        config=config,
        paths=paths or [],
    )
    warnings: list[str] = []
    artifacts: list[SkillArtifact] = []
    ignored = config.scan.ignore

    for path in sorted(skill_files):
        rel = manifest_relative_path(str(path.resolve()), workspace)
        if _ignored(rel, ignored) or not _changed_skill(path, workspace, changed):
            continue
        artifacts.append(parse_skill_file(path, workspace))

    for path in sorted(instruction_files):
        if not path.is_file():
            continue
        rel = manifest_relative_path(str(path.resolve()), workspace)
        if _ignored(rel, ignored) or (changed is not None and rel not in changed):
            continue
        artifacts.append(parse_instruction_file(path, workspace))

    return _dedupe_artifacts(artifacts), warnings


def _candidate_files(
    *,
    workspace: Path,
    config: SkillReviewConfig,
    paths: list[Path],
) -> tuple[set[Path], set[Path]]:
    if paths:
        return _explicit_candidate_files(workspace, paths)
    skill_files = _glob_files(workspace, config.scan.paths.skills)
    instruction_files = _glob_files(workspace, config.scan.paths.instructions)
    return skill_files, instruction_files


def _explicit_candidate_files(workspace: Path, paths: list[Path]) -> tuple[set[Path], set[Path]]:
    skill_files: set[Path] = set()
    instruction_files: set[Path] = set()
    for raw_path in paths:
        path = _resolve_workspace_path(workspace, raw_path)
        if path.is_file():
            skill = _containing_skill_file(path)
            if skill is not None:
                skill_files.add(skill)
            else:
                instruction_files.add(path)
            continue
        if not path.exists():
            raise InputParseError(f"Skill scan path not found: {raw_path}")
        if not path.is_dir():
            continue
        if (path / "SKILL.md").is_file():
            skill_files.add(path / "SKILL.md")
        skill_files.update(child for child in path.rglob("SKILL.md") if child.is_file())
        instruction_files.update(
            child
            for child in path.rglob("*")
            if child.is_file() and _looks_like_instruction(child)
        )
    return skill_files, instruction_files


def _glob_files(workspace: Path, patterns: list[str]) -> set[Path]:
    files: set[Path] = set()
    for pattern in patterns:
        for path in workspace.glob(pattern):
            if path.is_file():
                files.add(path)
    return files


def _resolve_workspace_path(workspace: Path, raw_path: Path) -> Path:
    path = raw_path if raw_path.is_absolute() else workspace / raw_path
    resolved = path.resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise InputParseError(
            f"Skill scan path {raw_path!s} resolves outside workspace: {resolved}"
        ) from exc
    return resolved


def _containing_skill_file(path: Path) -> Path | None:
    if path.name == "SKILL.md":
        return path
    for parent in (path.parent, *path.parents):
        candidate = parent / "SKILL.md"
        if candidate.is_file():
            return candidate
    return None


def _looks_like_instruction(path: Path) -> bool:
    if path.name in {"AGENTS.md", "AGENTS.override.md", "CLAUDE.md", "CODEX.md", ".cursorrules"}:
        return True
    parts = path.parts
    if ".cursor" in parts:
        cursor_index = parts.index(".cursor")
        return len(parts) > cursor_index + 1 and parts[cursor_index + 1] == "rules"
    if ".github" in parts:
        github_index = parts.index(".github")
        rest = parts[github_index + 1 :]
        return rest == ("copilot-instructions.md",) or bool(
            rest and rest[0] in {"instructions", "prompts"}
        )
    return False


def _load_changed_files(path: Path) -> set[str]:
    try:
        return {
            line.strip().replace("\\", "/")
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    except OSError as exc:
        raise InputParseError(f"Unable to read --changed-files path {path}: {exc}") from exc


def _changed_skill(path: Path, workspace: Path, changed: set[str] | None) -> bool:
    if changed is None:
        return True
    root = manifest_relative_path(str(path.parent.resolve()), workspace)
    skill_path = manifest_relative_path(str(path.resolve()), workspace)
    return any(item == skill_path or item.startswith(f"{root}/") for item in changed)


def _ignored(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _dedupe_artifacts(artifacts: list[SkillArtifact]) -> list[SkillArtifact]:
    by_path = {artifact.path: artifact for artifact in artifacts}
    seen_skill_content: set[str] = set()
    out: list[SkillArtifact] = []
    for path in sorted(by_path):
        artifact = by_path[path]
        if artifact.kind == "agent_skill":
            digest = hashlib.sha256(artifact.raw_text.encode("utf-8")).hexdigest()
            content_key = f"{artifact.name or ''}:{digest}"
            if content_key in seen_skill_content:
                continue
            seen_skill_content.add(content_key)
        out.append(artifact)
    return out
