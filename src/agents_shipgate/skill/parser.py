from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from agents_shipgate.inputs.common import MAX_INPUT_FILE_BYTES, manifest_relative_path
from agents_shipgate.skill.models import (
    CommandRef,
    FileSummary,
    MarkdownLink,
    SkillArtifact,
)

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)\s*([A-Za-z0-9_+.-]*)")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
URL_RE = re.compile(r"https?://[^\s)>\]\"']+")
LOCAL_PATH_RE = re.compile(r"\b(?:scripts|references|assets)/[A-Za-z0-9_./@%+=,:-]+")
SHELL_LANGS = {"bash", "sh", "shell", "zsh", "fish", "console", "terminal"}


def parse_skill_file(path: Path, workspace: Path) -> SkillArtifact:
    text = _read_text(path)
    metadata, body, body_start_line, error, field_lines = _split_frontmatter(text)
    sections = _sections(body, body_start_line)
    links = _links(text)
    external_urls = sorted({match.group(0) for match in URL_RE.finditer(text)})
    referenced_paths = sorted(_referenced_paths(text, links))
    root_dir = path.parent
    scripts = _summaries(root_dir / "scripts", workspace, role="script")
    references = _summaries(root_dir / "references", workspace, role="reference")
    assets = _summaries(root_dir / "assets", workspace, role="asset")
    other_files = _other_files(root_dir, workspace)
    return SkillArtifact(
        kind="agent_skill",
        path=manifest_relative_path(str(path.resolve()), workspace),
        root_dir=manifest_relative_path(str(root_dir.resolve()), workspace),
        name=_string(metadata.get("name")),
        description=_string(metadata.get("description")),
        metadata=metadata,
        frontmatter_error=error,
        frontmatter_field_lines=field_lines,
        body_start_line=body_start_line,
        body_line_count=len(body.splitlines()),
        raw_text=text,
        body=body,
        sections=sections,
        links=links,
        external_urls=external_urls,
        referenced_paths=referenced_paths,
        commands_in_markdown=_commands(text, path, workspace),
        allowed_tools=_allowed_tools(metadata),
        scripts=scripts,
        references=references,
        assets=assets,
        other_files=other_files,
    )


def parse_instruction_file(path: Path, workspace: Path) -> SkillArtifact:
    text = _read_text(path)
    return SkillArtifact(
        kind="agent_instruction",
        path=manifest_relative_path(str(path.resolve()), workspace),
        root_dir=manifest_relative_path(str(path.parent.resolve()), workspace),
        name=path.name,
        raw_text=text,
        body=text,
        body_start_line=1,
        body_line_count=len(text.splitlines()),
        sections=_sections(text, 1),
        links=_links(text),
        external_urls=sorted({match.group(0) for match in URL_RE.finditer(text)}),
        referenced_paths=sorted(_referenced_paths(text, _links(text))),
        commands_in_markdown=_commands(text, path, workspace),
    )


def _split_frontmatter(
    text: str,
) -> tuple[dict[str, Any], str, int, str | None, dict[str, int]]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text, 1, None, {}
    close_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            close_index = index
            break
    if close_index is None:
        return {}, "", len(lines) + 1, "frontmatter closing delimiter not found", {}
    frontmatter_text = "\n".join(lines[1:close_index])
    body = "\n".join(lines[close_index + 1 :])
    field_lines = _field_lines(lines[1:close_index], offset=2)
    try:
        raw = yaml.safe_load(frontmatter_text) if frontmatter_text.strip() else {}
    except yaml.YAMLError as exc:
        return {}, body, close_index + 2, str(exc), field_lines
    if raw is None:
        return {}, body, close_index + 2, None, field_lines
    if not isinstance(raw, dict):
        return {}, body, close_index + 2, "frontmatter must be a mapping", field_lines
    return raw, body, close_index + 2, None, field_lines


def _field_lines(lines: list[str], *, offset: int) -> dict[str, int]:
    out: dict[str, int] = {}
    for index, line in enumerate(lines, start=offset):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key = stripped.split(":", 1)[0].strip().strip("'\"")
        if key:
            out.setdefault(key, index)
    return out


def _sections(text: str, start_line: int) -> dict[str, int]:
    sections: dict[str, int] = {}
    for offset, line in enumerate(text.splitlines(), start=start_line):
        match = HEADING_RE.match(line)
        if not match:
            continue
        name = re.sub(r"\s+", " ", match.group(2).strip().lower())
        sections.setdefault(name, offset)
    return sections


def _links(text: str) -> list[MarkdownLink]:
    links: list[MarkdownLink] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for match in MARKDOWN_LINK_RE.finditer(line):
            links.append(
                MarkdownLink(
                    text=match.group(1),
                    target=match.group(2),
                    line=line_number,
                )
            )
    return links


def _referenced_paths(text: str, links: list[MarkdownLink]) -> set[str]:
    paths = {
        link.target
        for link in links
        if not link.target.startswith(("http://", "https://", "#", "mailto:"))
    }
    paths.update(match.group(0) for match in LOCAL_PATH_RE.finditer(text))
    return paths


def _commands(text: str, source_path: Path, workspace: Path) -> list[CommandRef]:
    commands: list[CommandRef] = []
    lines = text.splitlines()
    index = 0
    rel_path = manifest_relative_path(str(source_path.resolve()), workspace)
    while index < len(lines):
        match = FENCE_RE.match(lines[index])
        if not match:
            index += 1
            continue
        fence, language = match.groups()
        start_line = index + 2
        index += 1
        block_lines: list[str] = []
        while index < len(lines) and not lines[index].strip().startswith(fence):
            block_lines.append(lines[index])
            index += 1
        if language.lower() in SHELL_LANGS:
            for offset, command in enumerate(block_lines, start=start_line):
                stripped = command.strip()
                if stripped and not stripped.startswith("#"):
                    commands.append(
                        CommandRef(command=stripped, line=offset, source_path=rel_path)
                    )
        index += 1
    return commands


def _summaries(root: Path, workspace: Path, *, role: str) -> list[FileSummary]:
    if not root.is_dir():
        return []
    out: list[FileSummary] = []
    for path in sorted(child for child in root.rglob("*") if child.is_file()):
        out.append(_file_summary(path, workspace, role=role))
    return out


def _other_files(root: Path, workspace: Path) -> list[FileSummary]:
    out: list[FileSummary] = []
    reserved = {"SKILL.md", "scripts", "references", "assets"}
    for path in sorted(child for child in root.rglob("*") if child.is_file()):
        try:
            rel_parts = path.relative_to(root).parts
        except ValueError:
            continue
        if not rel_parts or rel_parts[0] in reserved:
            continue
        out.append(_file_summary(path, workspace, role="other"))
    return out


def _file_summary(path: Path, workspace: Path, *, role: str) -> FileSummary:
    text = _try_read_text(path)
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return FileSummary(
        path=manifest_relative_path(str(path.resolve()), workspace),
        role=role,
        size_bytes=size,
        line_count=len(text.splitlines()) if text is not None else 0,
        executable=os.access(path, os.X_OK),
        text=text,
    )


def _read_text(path: Path) -> str:
    text = _try_read_text(path)
    if text is None:
        return ""
    return text


def _try_read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_INPUT_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _allowed_tools(metadata: dict[str, Any]) -> list[str]:
    for key in ("allowed-tools", "allowed_tools"):
        value = metadata.get(key)
        if isinstance(value, str):
            return [item.strip() for item in re.split(r"[,\n]", value) if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
    return []
