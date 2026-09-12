from __future__ import annotations

import difflib

import pytest

from agents_shipgate.core.boundary_diff import (
    DiffFile,
    DiffHunk,
    ResolvedFileText,
    parse_unified_diff,
)
from agents_shipgate.core.instruction_structure import (
    classify_instruction,
    unchanged_instruction_structure,
)


def _skill(body="Explain the result.", fields=""):
    return f"---\nname: demo\ndescription: Test fixture\n{fields}---\n{body}\n"


def _same_structure(before, after, path=".claude/skills/demo/SKILL.md"):
    text = "".join(difflib.unified_diff(
        (before or "").splitlines(keepends=True), (after or "").splitlines(keepends=True),
        fromfile="a/" + path, tofile="b/" + path,
    ))
    records = parse_unified_diff("diff --git a/" + path + " b/" + path + "\n" + text)
    diff = records[0]
    return unchanged_instruction_structure(diff, ResolvedFileText(before, after, "fixture", None, None))


@pytest.mark.parametrize("path", ["AGENTS.md", "nested/AGENTS.override.md", "CLAUDE.md"])
def test_prose_requirement_and_command_example_do_not_declare_execution(path):
    before = "You must run Shipgate.\n```sh\npython audit.py\n```\n"
    after = "Explain the test result before committing.\n"
    assert _same_structure(before, after, path)
    assert classify_instruction(path, after).status == "guidance"


def test_skill_body_prose_compares_without_command_mention_heuristic():
    assert _same_structure(_skill("Explain."), _skill("Explain the shell and subprocess examples."))
    assert _same_structure(_skill(), _skill("```sh\necho example\n```"))


@pytest.mark.parametrize("field", [
    "allowed-tools: Bash(*)\n",
    "hooks:\n  PreToolUse:\n    - hooks:\n        - type: command\n          command: ./audit.sh\n",
    "context: fork\nagent: Explore\n",
])
def test_added_execution_or_permission_declaration_changes_structure(field):
    assert not _same_structure(_skill(), _skill(fields=field))


def test_bang_backtick_execution_changes_even_inside_a_fenced_example():
    assert not _same_structure(_skill(), _skill("!`echo actual`"))
    assert not _same_structure(_skill(), _skill("```\n!`echo actual`\n```"))
    assert not _same_structure(_skill("!`echo a`"), _skill("!`echo b`"))


def test_multiline_execution_remains_unresolved_and_nonwhitespace_inline_is_literal():
    executable = _skill("```!\necho actual\n```")
    assert classify_instruction("SKILL.md", executable).status == "unresolved"
    assert not _same_structure(executable, executable + "Prose.\n")
    assert _same_structure(_skill("KEY=!`literal a`"), _skill("KEY=!`literal b`"))


@pytest.mark.parametrize("old,new", [
    (".claude/skills/a/SKILL.md", ".claude/skills/b/SKILL.md"),
    (".cursor/rules/a.mdc", "nested/.cursor/rules/a.mdc"),
])
def test_structure_registration_move_remains_reviewable(old, new):
    diff = DiffFile(old_path=old, new_path=new, is_rename=True)
    text = _skill() if old.endswith("SKILL.md") else "---\nalwaysApply: true\n---\nText.\n"
    assert not unchanged_instruction_structure(diff, ResolvedFileText(text, text, "fixture", None, None))


@pytest.mark.parametrize("text", [
    "No frontmatter", "---\nhooks: [\n", "---\nhooks: [\n---\n",
    _skill(fields="future-hook: ./run.sh\n"),
    _skill(fields="allowed-tools: Read\nallowed-tools: Bash\n"),
    _skill(fields="metadata: &meta {x: y}\n"),
    _skill(fields="hooks: invalid\n"),
    _skill(fields="allowed-tools: {shell: true}\n"),
    _skill(fields="disable-model-invocation: maybe\n"),
    _skill(fields="hooks:\n  PreToolUse:\n    - hooks:\n        - command: ./run.sh\n"),
    _skill(body="!`unterminated"),
])
def test_malformed_or_unsupported_structure_cannot_clear_prose_edit(text):
    result = classify_instruction(".claude/skills/demo/SKILL.md", text)
    assert result.status == "unresolved"
    assert not _same_structure(text, text + "Prose addition.\n")


def test_missing_comparison_and_cross_format_rename_are_not_prose_exemptions():
    assert not _same_structure(None, "Text", "AGENTS.md")
    diff = DiffFile(old_path=".mcp.json", new_path="AGENTS.md", is_rename=True)
    assert not unchanged_instruction_structure(diff, ResolvedFileText("{}", "{}", "fixture", None, None))


def test_truncated_new_file_hunk_cannot_establish_a_complete_instruction():
    diff = DiffFile(
        old_path=None, new_path="AGENTS.md", is_new=True,
        hunks=[DiffHunk(0, 0, 1, 10, [("+", "One line of ten")])],
    )
    assert not unchanged_instruction_structure(diff, ResolvedFileText("", "One line of ten\n", "diff_new_file", None, None))


def test_directories_and_structured_config_files_are_never_exempted():
    for path in [".claude/settings.json", ".codex/config.toml", ".agents/skills/demo/hook.py", ".mcp.json"]:
        assert classify_instruction(path, "{}") is None


@pytest.mark.parametrize("path", [".claude/commands/AGENTS.md", ".claude/commands/CLAUDE.md"])
def test_command_directory_wins_over_plain_instruction_basename(path):
    old = "---\nallowed-tools: Read\n---\nText.\n"
    new = "---\nallowed-tools: Bash\n---\nText.\n"
    assert not _same_structure(old, new, path)


@pytest.mark.parametrize("path", [".claude/agents/AGENTS.md", ".claude/rules/AGENTS.md"])
def test_unsupported_structured_directory_cannot_become_plain_guidance(path):
    text = "---\ntools: Read\n---\nText.\n"
    assert classify_instruction(path, text).status == "unresolved"
    assert not _same_structure(text, text + "More prose.\n", path)


def test_mode_change_cannot_hide_behind_a_prose_change():
    before, after = "Old prose.\n", "New prose.\n"
    diff = "diff --git a/AGENTS.md b/AGENTS.md\nold mode 100644\nnew mode 100755\n"
    diff += "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile="a/AGENTS.md", tofile="b/AGENTS.md"))
    parsed = parse_unified_diff(diff)[0]
    assert parsed.metadata_changed
    assert not unchanged_instruction_structure(parsed, ResolvedFileText(before, after, "fixture", None, None))


# --- empty frontmatter values ------------------------------------------------
#
# `globs:` with nothing after it is how Cursor writes a rule that is not
# glob-scoped, and YAML reads that as None. Rejecting it made the canonical
# Cursor rule an unresolved structure — a *blocking* inventory issue — so a
# repository carrying one produced no rows at all. `Doist/todoist-mcp` had
# eleven readable host files and two such rules, and `shipgate diff` answered
# `incomparable` for that whole repository on every step of its history.

_CURSOR_RULE = ".cursor/rules/demo.mdc"


def _cursor(fields: str) -> str:
    return f"---\n{fields}---\nBe concise.\n"


@pytest.mark.parametrize(
    "fields",
    [
        # Exactly what Cursor generates for an always-apply rule.
        "description: \nglobs: \nalwaysApply: true\n",
        # ...and for a described rule that is not glob-scoped.
        "description: Use context7 for library docs\nglobs: \nalwaysApply: false\n",
        "description: \nglobs: \nalwaysApply: \n",
        "globs: \n",
    ],
)
def test_an_empty_cursor_field_is_absent_not_invalid(fields: str) -> None:
    resolved = classify_instruction(_CURSOR_RULE, _cursor(fields))

    assert resolved.status == "structured", resolved.reason


def test_a_populated_cursor_rule_still_resolves() -> None:
    """The fix must not be the only reason anything passes."""

    resolved = classify_instruction(
        _CURSOR_RULE, _cursor('description: TS rules\nglobs: "**/*.ts"\nalwaysApply: false\n')
    )

    assert resolved.status == "structured", resolved.reason


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        # A wrong *type* is still a wrong type. Only "not set" is forgiven.
        ("description: TS\nglobs: 7\nalwaysApply: false\n", "frontmatter_invalid_structure"),
        ("description: TS\nglobs: \nalwaysApply: yes-please\n", "frontmatter_invalid_structure"),
        # An unknown key is still unknown, empty or not.
        ("description: TS\nunexpected: \n", "frontmatter_unknown_fields"),
    ],
)
def test_an_empty_value_does_not_excuse_a_real_structure_problem(
    fields: str, reason: str
) -> None:
    resolved = classify_instruction(_CURSOR_RULE, _cursor(fields))

    assert resolved.status == "unresolved"
    assert resolved.reason == reason


def test_a_skill_still_needs_a_real_name_and_description() -> None:
    """The required-field check is separate and must keep firing.

    Treating an explicit null as absent in the shared type check would be a
    hole if identity were only enforced there.
    """

    for fields in ("name: demo\ndescription: \n", "name: \ndescription: Test\n"):
        resolved = classify_instruction(
            ".agents/skills/demo/SKILL.md", f"---\n{fields}---\nBody.\n"
        )
        assert resolved.status == "unresolved", fields
        assert resolved.reason == "skill_identity_missing", fields
