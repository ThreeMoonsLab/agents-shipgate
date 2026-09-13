"""Bounded, private projections of instruction files, never prose judgments.

Keep byte capture separate from the declaration a host actually interprets.
Unknown formats and incomplete text cannot establish unchanged structure.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

import yaml

from agents_shipgate.core.boundary_diff import DiffFile, ResolvedFileText, _apply_hunks

MAX_INSTRUCTION_BYTES = 256 * 1024
MAX_FRONTMATTER_BYTES = 32 * 1024
MAX_FRONTMATTER_TOKENS = 4096
_PLAIN_NAMES = frozenset({"agents.md", "agents.override.md", "claude.md"})
_SKILL_FIELDS = frozenset({
    "name", "description", "license", "compatibility", "metadata",
    "allowed-tools", "argument-hint", "disable-model-invocation",
    "user-invocable", "model", "context", "agent", "hooks",
    # Documented at code.claude.com/docs/en/skills and refused until #722, so a
    # skill using any of them made its whole host inventory partial.
    "when_to_use", "arguments", "disallowed-tools", "effort", "background",
    "paths", "shell",
})
_CURSOR_FIELDS = frozenset({"description", "globs", "alwaysApply"})
#: A Cursor rule's `globs:` as Cursor writes it: a bare pattern such as
#: `*.json` or `**/*.java, **/pom.xml`. A leading `*` is YAML's alias
#: indicator, so the value was refused as invalid YAML and the whole
#: repository's comparison with it (#729). Top-level `globs:` only.
_CURSOR_BARE_GLOBS = re.compile(r"^globs:[ \t]*(\*[^\r\n]*?)[ \t]*$")
_COMMAND_FIELDS = frozenset({
    "description", "allowed-tools", "argument-hint", "model",
    "disable-model-invocation", "hooks",
    # "Custom commands support the same YAML frontmatter as skills"; `name`
    # and `paths` are documented as working differently there, not as absent.
    "user-invocable", "disallowed-tools", "effort", "arguments", "name", "paths",
})
#: Undocumented keys (`version`, `author`, `category`, …) are digested, not
#: refused (#730, owner decision). They enter the structure digest with their
#: values, so changing one is still a change, and none is read as a permission.
#: A Cursor rule keeps refusing a key outside `_CURSOR_FIELDS`.

#: Fields a host reads as a list, written either way the docs allow: a YAML
#: list, or one string it splits. `argument-hint` is documented as a string,
#: but its documented example `[issue-number]` is a YAML list.
_STRING_OR_LIST_FIELDS = frozenset({
    "allowed-tools", "disallowed-tools", "globs", "arguments", "paths", "argument-hint",
})
_BOOLEAN_FIELDS = frozenset({
    "disable-model-invocation", "user-invocable", "alwaysApply", "background",
})
#: Claude Code booleans also accept these spellings (any case); Cursor's
#: `alwaysApply` does not document them and stays an exact boolean.
_CLAUDE_BOOLEAN_STRINGS = frozenset({"true", "false", "yes", "no", "on", "off", "1", "0"})
_ENUM_FIELDS = {
    "effort": frozenset({"low", "medium", "high", "xhigh", "max"}),
    "shell": frozenset({"bash", "powershell"}),
}


@dataclass(frozen=True)
class InstructionStructure:
    profile: str
    status: Literal["guidance", "structured", "unresolved"]
    sha256: str | None
    reason: str

    def projection(self) -> dict[str, str | None]:
        return {
            "profile": self.profile, "status": self.status,
            "sha256": self.sha256, "reason": self.reason,
        }


def instruction_profile(path: str) -> str | None:
    normalized = path.replace("\\", "/").casefold()
    name = PurePosixPath(normalized).name
    # A command/subagent can itself be named AGENTS.md. Directory role wins
    # over a familiar basename, just as an invocation's manifest/policy wins
    # over this entire optional classifier.
    if "/.claude/commands/" in "/" + normalized and name.endswith(".md"):
        return "claude_command/v1"
    if any(marker in "/" + normalized for marker in (
        "/.claude/agents/", "/.claude/rules/", "/.codex/agents/",
    )):
        return "unsupported_instruction_role/v1"
    if name == "skill.md":
        return "skill_instruction/v1"
    if "/.cursor/rules/" in "/" + normalized and name.endswith(".mdc"):
        return "cursor_instruction/v1"
    if name in _PLAIN_NAMES:
        return "plain_instruction/v1"
    return None


def _json_ready(value: object) -> object:
    """A YAML value in a form the digest can encode, without changing what it says.

    An undocumented key is digested as written (#730), and YAML reads an
    unquoted `date: 2026-01-29` as a date object, which JSON cannot encode. Left
    as it was, the refusal came back one layer down as `frontmatter_invalid`,
    on the exact shape #659 measured (110 skills in one repository). A date is
    digested as its ISO text; containers are converted element by element.
    """

    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return value


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def _valid_metadata(metadata: dict, known: frozenset[str]) -> bool:
    for key, value in metadata.items():
        if key not in known:
            # An undocumented key has no documented type to check (#730). Its
            # YAML value was composed without aliases, tags or duplicate keys,
            # and it is digested as written.
            continue
        if value is None:
            # `globs:` with nothing after it is how Cursor writes a rule that
            # is not glob-scoped, and YAML reads that as None. Rejecting it
            # made the canonical Cursor rule file an unresolved structure,
            # which is a *blocking* inventory issue, which made every
            # repository carrying one incomparable in full — `Doist/todoist-mcp`
            # produced no rows for eleven readable host files because two
            # `.cursor/rules/*.mdc` used the format Cursor itself generates.
            # An explicit null is the same statement as an absent key: the
            # field is not set. Where a field is genuinely required, the
            # profile checks below still say so and still fire.
            continue
        if key in _BOOLEAN_FIELDS:
            if not isinstance(value, bool) and not (
                key != "alwaysApply"
                and isinstance(value, str)
                and value.strip().lower() in _CLAUDE_BOOLEAN_STRINGS
            ):
                return False
        elif key in _STRING_OR_LIST_FIELDS:
            if not isinstance(value, str) and not (
                isinstance(value, list) and all(isinstance(item, str) for item in value)
            ):
                return False
        elif key in _ENUM_FIELDS:
            if not isinstance(value, str) or value not in _ENUM_FIELDS[key]:
                return False
        elif key == "hooks":
            if not _valid_hooks(value):
                return False
        elif key == "metadata":
            if not isinstance(value, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in value.items()
            ):
                return False
        elif not isinstance(value, str):
            return False
    return True


def _valid_hooks(value: object) -> bool:
    # A deliberately bounded command-hook profile. Prompt/agent/HTTP hooks
    # and future options remain unresolved rather than accepted as no change.
    if not isinstance(value, dict):
        return False
    for event, groups in value.items():
        if not isinstance(event, str) or event not in {
            "PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest",
            "UserPromptSubmit", "Stop", "SubagentStart", "SubagentStop",
            "PreCompact", "SessionStart", "SessionEnd", "Notification",
            "TeammateIdle", "TaskCompleted", "ConfigChange", "WorktreeCreate",
            "WorktreeRemove",
        } or not isinstance(groups, list):
            return False
        for group in groups:
            if not isinstance(group, dict) or set(group) - {"matcher", "hooks"}:
                return False
            if "matcher" in group and not isinstance(group["matcher"], str):
                return False
            hooks = group.get("hooks")
            if not isinstance(hooks, list):
                return False
            for hook in hooks:
                if not isinstance(hook, dict) or set(hook) - {
                    "type", "command", "timeout", "async", "statusMessage", "once",
                }:
                    return False
                if hook.get("type") != "command" or not isinstance(hook.get("command"), str) or not hook["command"].strip():
                    return False
                if "timeout" in hook and (type(hook["timeout"]) not in {int, float} or hook["timeout"] <= 0):
                    return False
                if any(key in hook and not isinstance(hook[key], bool) for key in ("async", "once")):
                    return False
                if "statusMessage" in hook and not isinstance(hook["statusMessage"], str):
                    return False
    return True


def _preprocessing_commands(body: str) -> list[str] | None:
    # Keep the unsupported multiline execution form visible. Recognizing its
    # absence is sufficient for this bounded inline profile; never confuse an
    # executable fence with a normal shell example.
    if re.search(r"(?m)^[ \t]*(`{3,}|~{3,})!", body):
        return None
    starts = list(re.finditer(r"(?<!\S)!`", body))
    commands = []
    for start in starts:
        match = re.match(r"([^`\n]*)`", body[start.end():])
        if match is None:
            return None
        commands.append(match.group(1))
    return commands


def classify_instruction(path: str, text: str | None) -> InstructionStructure | None:
    profile = instruction_profile(path)
    if profile is None:
        return None

    def unresolved(reason: str) -> InstructionStructure:
        return InstructionStructure(profile, "unresolved", None, reason)

    if profile == "unsupported_instruction_role/v1":
        return unresolved("instruction_role_unsupported")
    if text is None:
        return unresolved("complete_text_unavailable")
    if len(text.encode("utf-8")) > MAX_INSTRUCTION_BYTES:
        return unresolved("instruction_text_limit")
    if "\x00" in text:
        return unresolved("instruction_text_invalid")
    if profile == "plain_instruction/v1":
        # These hosts consume the file as guidance. A shell example, mandatory
        # wording, or its removal is not a parsed permission or executable hook.
        return InstructionStructure(profile, "guidance", _digest([profile]), "prose_guidance")

    lines = text.splitlines()
    has_frontmatter = bool(lines and lines[0].strip() == "---")
    close = (
        next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if has_frontmatter else -1
    )
    if close is None:
        return unresolved("frontmatter_unterminated")
    if profile == "cursor_instruction/v1" and has_frontmatter:
        # Read the bare glob as the literal string Cursor reads it as. Both
        # parses below see the same rewrite, and any other alias still refuses.
        lines = [
            (
                "globs: '" + match.group(1).replace("'", "''") + "'"
                if 0 < index < close and (match := _CURSOR_BARE_GLOBS.match(line))
                else line
            )
            for index, line in enumerate(lines)
        ]
        text = "\n".join(lines)
    header = "\n".join(lines[1:close]) if has_frontmatter else ""
    if len(header.encode()) > MAX_FRONTMATTER_BYTES:
        return unresolved("frontmatter_limit")
    try:
        # The public skill scanner imports the check registry through its
        # input helpers. Defer that import until the domain modules are loaded.
        from agents_shipgate.skill.parser import _split_frontmatter

        # Refuse aliases, tags and duplicate keys before the existing skill
        # parser constructs a value. No alias expansion or recursive payload
        # is needed to show that this comparison is unresolved.
        for count, token in enumerate(yaml.scan(header), 1):
            if count > MAX_FRONTMATTER_TOKENS:
                return unresolved("frontmatter_limit")
            if isinstance(token, (yaml.AliasToken, yaml.AnchorToken, yaml.TagToken)):
                return unresolved("frontmatter_unsupported_yaml")
        node = yaml.compose(header)
        pending = [node] if node is not None else []
        while pending:
            item = pending.pop()
            if isinstance(item, yaml.MappingNode):
                keys = [key.value for key, _ in item.value if isinstance(key, yaml.ScalarNode)]
                if len(keys) != len(item.value) or len(keys) != len(set(keys)):
                    return unresolved("frontmatter_ambiguous_keys")
                pending.extend(value for _, value in item.value)
            elif isinstance(item, yaml.SequenceNode):
                pending.extend(item.value)
        metadata, body, _line, error, _fields = _split_frontmatter(text)
        if error:
            return unresolved("frontmatter_invalid")
        known = (
            _CURSOR_FIELDS if profile == "cursor_instruction/v1"
            else _COMMAND_FIELDS if profile == "claude_command/v1"
            else _SKILL_FIELDS
        )
        if any(not isinstance(key, str) for key in metadata) or (
            profile == "cursor_instruction/v1" and any(key not in known for key in metadata)
        ):
            return unresolved("frontmatter_unknown_fields")
        if not _valid_metadata(metadata, known):
            return unresolved("frontmatter_invalid_structure")
        if profile == "skill_instruction/v1":
            description = metadata.get("description")
            if description is None:
                # "If omitted, uses the first non-empty line of the markdown
                # content" (code.claude.com/docs/en/skills). Frontmatter itself is
                # optional (#730). The default enters the digest, so editing
                # that line is a change.
                first = next((line.strip() for line in body.splitlines() if line.strip()), None)
                if first is not None:
                    description = first
                    metadata = {**metadata, "description": first}
            if not isinstance(description, str) or not description.strip():
                return unresolved("skill_identity_missing")
            name = metadata.get("name")
            if name is None or (isinstance(name, str) and not name.strip()):
                # "name — defaults to directory name" (code.claude.com/docs/en/skills).
                # The default enters the digest, so renaming the directory is a change.
                directory = PurePosixPath(path.replace("\\", "/")).parent.name
                if not directory:
                    return unresolved("skill_identity_missing")
                metadata = {**metadata, "name": directory}
        # Claude skill/command preprocessing runs bang-backtick commands even
        # though ordinary fenced shell examples are just guidance. Preserve
        # command text exactly; an unterminated form cannot be called absent.
        commands = [] if profile == "cursor_instruction/v1" else _preprocessing_commands(body)
        if commands is None:
            return unresolved("preprocessing_unresolved")
        # Optional null fields carry the same declaration as an omitted key.
        # Normalize only after unknown-key/type/required-field validation;
        # nested metadata and actual permission or hook values stay intact.
        metadata = {key: value for key, value in metadata.items() if value is not None}
        structure = _digest([profile, _json_ready(metadata), commands])
    except (yaml.YAMLError, RecursionError, TypeError, ValueError):
        return unresolved("frontmatter_invalid")
    return InstructionStructure(profile, "structured", structure, "declared_structure")


def instruction_pair_is_complete(diff: DiffFile, resolved: ResolvedFileText) -> bool:
    """Prove both text sides without substituting a hunk excerpt for a file."""
    if diff.metadata_changed or resolved.old_text is None or resolved.new_text is None:
        return False
    if not diff.hunks and not (diff.is_rename and resolved.old_text == resolved.new_text):
        return False
    for hunk in diff.hunks:
        if (
            any(kind not in {" ", "+", "-"} for kind, _ in hunk.lines)
            or hunk.old_count != sum(kind in {" ", "-"} for kind, _ in hunk.lines)
            or hunk.new_count != sum(kind in {" ", "+"} for kind, _ in hunk.lines)
        ):
            return False
    if not (diff.is_new or diff.is_deleted) and (
        _apply_hunks(resolved.old_text, diff.hunks, direction="forward") != resolved.new_text
        or _apply_hunks(resolved.new_text, diff.hunks, direction="reverse") != resolved.old_text
    ):
        return False
    if diff.is_new or diff.is_deleted:
        # The generic text resolver also serves positive heuristics and can
        # render a hunk excerpt. Absence of structure needs the entire side.
        if len(diff.hunks) != 1:
            return False
        hunk = diff.hunks[0]
        if diff.is_new:
            complete = hunk.old_start == hunk.old_count == 0 and hunk.new_start == 1
            complete = complete and all(kind == "+" for kind, _ in hunk.lines)
            count = hunk.new_count
        else:
            complete = hunk.new_start == hunk.new_count == 0 and hunk.old_start == 1
            complete = complete and all(kind == "-" for kind, _ in hunk.lines)
            count = hunk.old_count
        if not complete or count != len(hunk.lines):
            return False
        captured = resolved.new_text if diff.is_new else resolved.old_text
        absent = resolved.old_text if diff.is_new else resolved.new_text
        if absent != "" or captured.splitlines() != [line for _, line in hunk.lines]:
            return False
    return True


def unchanged_instruction_structure(diff: DiffFile, resolved: ResolvedFileText) -> bool:
    """Only a complete pair of the same supported format may clear a touch."""
    if not instruction_pair_is_complete(diff, resolved):
        return False
    before_path = diff.old_path or diff.new_path or ""
    after_path = diff.new_path or diff.old_path or ""
    before = classify_instruction(before_path, resolved.old_text)
    after = classify_instruction(after_path, resolved.new_text)
    if before is None or after is None or before.profile != after.profile:
        return False
    if before.status == "unresolved" or after.status == "unresolved":
        return False
    if before.status == "structured" and diff.old_path != diff.new_path:
        return False
    if diff.is_new or diff.is_deleted:
        # Adding/removing a prose file cannot add/remove a parsed grant. Skill
        # and rule registration is structure even when its body is just prose.
        return before.status == after.status == "guidance"
    return before.sha256 == after.sha256


def instruction_review_evidence(diff: DiffFile, resolved: ResolvedFileText) -> tuple[dict, bool]:
    """Describe a required structural review without interpreting prose words."""
    before = None if diff.is_new else classify_instruction(diff.old_path or diff.path, resolved.old_text)
    after = None if diff.is_deleted else classify_instruction(diff.new_path or diff.path, resolved.new_text)
    present = [item for item in (before, after) if item is not None]
    complete = bool(
        instruction_pair_is_complete(diff, resolved) and present
        and all(item.status != "unresolved" for item in present)
        and (before is None or after is None or before.profile == after.profile)
    )
    return {
        "kind": "instruction_structure_changed" if complete else "instruction_structure_unresolved",
        "comparison_complete": complete,
        "before": before.projection() if before is not None else None,
        "after": after.projection() if after is not None else None,
    }, complete
