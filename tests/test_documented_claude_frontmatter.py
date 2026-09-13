"""Documented Claude skill and command frontmatter resolves (#722).

Each shape below is documented at code.claude.com/docs/en/skills or
/docs/en/slash-commands, and each was refused as `unresolved` before this
change. An `unresolved` instruction is a blocking coverage issue, so one such
file made a repository's whole host inventory partial.
"""

from __future__ import annotations

import pytest

from agents_shipgate.core.instruction_structure import classify_instruction

SKILL = ".claude/skills/helper/SKILL.md"
COMMAND = ".claude/commands/review.md"


def _doc(fields: str) -> str:
    return f"---\n{fields}---\n\nBody.\n"


@pytest.mark.parametrize(
    "field",
    [
        "when_to_use: When the user asks for a review\n",
        "arguments: issue branch\n",
        "arguments: [issue, branch]\n",
        "disallowed-tools: AskUserQuestion\n",
        "disallowed-tools: [AskUserQuestion, SendEmail]\n",
        "effort: xhigh\n",
        "background: false\n",
        'background: "yes"\n',
        'paths: "src/**/*.ts,lib/**/*.js"\n',
        "paths: [src/**/*.ts, lib/**/*.js]\n",
        "shell: powershell\n",
        "argument-hint: [issue-number]\n",
    ],
)
def test_a_documented_skill_field_resolves(field: str) -> None:
    resolved = classify_instruction(SKILL, _doc(f"name: helper\ndescription: Helps.\n{field}"))

    assert resolved.status == "structured", (field, resolved.reason)


@pytest.mark.parametrize(
    "field",
    [
        "user-invocable: false\n",
        "disallowed-tools: [Write]\n",
        "effort: low\n",
        "arguments: [pr]\n",
        "name: review\n",
        "paths: [src/**]\n",
        "argument-hint: [--staged | --branch | --pr <url>]\n",
    ],
)
def test_a_documented_command_field_resolves(field: str) -> None:
    resolved = classify_instruction(COMMAND, _doc(f"description: Review.\n{field}"))

    assert resolved.status == "structured", (field, resolved.reason)


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("effort: extreme\n", "frontmatter_invalid_structure"),
        ("shell: zsh\n", "frontmatter_invalid_structure"),
        ("background: sometimes\n", "frontmatter_invalid_structure"),
        ("paths: 7\n", "frontmatter_invalid_structure"),
        ("version: 1.0.0\n", "frontmatter_unknown_fields"),
        ("author: someone\n", "frontmatter_unknown_fields"),
    ],
)
def test_wrong_types_and_undocumented_keys_stay_unresolved(field: str, reason: str) -> None:
    resolved = classify_instruction(SKILL, _doc(f"name: helper\ndescription: Helps.\n{field}"))

    assert resolved.status == "unresolved"
    assert resolved.reason == reason


def test_a_skill_without_a_name_takes_its_directory_name() -> None:
    """ "name — defaults to directory name". The default is part of the digest."""

    implicit = classify_instruction(SKILL, _doc("description: Helps.\n"))
    explicit = classify_instruction(SKILL, _doc("name: helper\ndescription: Helps.\n"))
    renamed = classify_instruction(".claude/skills/other/SKILL.md", _doc("description: Helps.\n"))

    assert implicit.status == explicit.status == "structured"
    assert implicit.sha256 == explicit.sha256
    assert renamed.sha256 != implicit.sha256


def test_cursor_booleans_stay_exact() -> None:
    resolved = classify_instruction(
        ".cursor/rules/demo.mdc", '---\ndescription: d\nalwaysApply: "yes"\n---\nBody.\n'
    )

    assert resolved.status == "unresolved"
    assert resolved.reason == "frontmatter_invalid_structure"


@pytest.mark.parametrize(
    ("field", "before", "after"),
    [
        ("shell", "bash", "powershell"),
        # Quoted: an unquoted `*` opens a YAML alias.
        ("paths", "[src/**]", '["**"]'),
        ("disallowed-tools", "[Write]", "[]"),
        ("allowed-tools", "Read", "Read Bash(*)"),
        ("effort", "low", "max"),
    ],
)
def test_a_change_to_a_capability_bearing_field_changes_the_digest(field, before, after) -> None:
    base = classify_instruction(SKILL, _doc(f"description: Helps.\n{field}: {before}\n"))
    head = classify_instruction(SKILL, _doc(f"description: Helps.\n{field}: {after}\n"))

    assert base.status == head.status == "structured"
    assert base.sha256 != head.sha256


def test_the_cold_start_shapes_that_were_documented_now_resolve() -> None:
    """Two #660 stop points, as written in their repositories."""

    command = (
        "---\n"
        "description: Review code changes with structured grading (A-F)\n"
        "allowed-tools: Bash(git diff:*), Bash(git rev-parse:*), Bash(gh pr diff:*)\n"
        "argument-hint: [--staged | --branch | --pr <url>]\n"
        "---\n\nReview code and provide a structured assessment with grading.\n"
    )
    description_only_skill = "---\ndescription: Review a plan until it is clean.\n---\n\n# Plan review\n"

    assert classify_instruction(COMMAND, command).status == "structured"
    assert classify_instruction(".claude/skills/plan-review/SKILL.md", description_only_skill).status == "structured"
