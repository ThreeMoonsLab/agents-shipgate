"""#729: a Cursor rule's globs, written the way Cursor writes them.

`globs: *.json` and `globs: **/*.java, **/pom.xml` are unquoted, and a leading
`*` is YAML's alias indicator, so the rule was refused as invalid YAML. An
unresolved instruction structure is a blocking inventory issue, so the whole
repository's comparison was refused with it. #659 measured it on
`vtex/openapi-schemas#1583` and on six rules in `dotCMS/core#36281`.
"""

from __future__ import annotations

import pytest

from agents_shipgate.core.instruction_structure import classify_instruction

RULE = ".cursor/rules/openapi-standards.mdc"


def _rule(globs_line: str) -> str:
    return f"---\ndescription: OpenAPI standards\n{globs_line}\nalwaysApply: false\n---\nBody.\n"


@pytest.mark.parametrize(
    "line",
    [
        "globs: *.json",
        "globs: **/*.mdc,CLAUDE.md,docs/**/*",
        "globs: **/*.java, **/pom.xml, dotCMS/src/**/*",
        'globs: "*.json"',
        "globs: src/**/*.ts,src/**/*.tsx",
    ],
)
def test_the_spellings_cursor_writes_are_structured(line: str) -> None:
    result = classify_instruction(RULE, _rule(line))

    assert result is not None
    assert result.status == "structured", result.reason


def test_a_bare_glob_is_read_as_its_literal_string() -> None:
    bare = classify_instruction(RULE, _rule("globs: *.json"))
    quoted = classify_instruction(RULE, _rule('globs: "*.json"'))

    assert bare is not None and quoted is not None
    assert bare.sha256 == quoted.sha256


def test_editing_a_bare_glob_is_still_a_change() -> None:
    before = classify_instruction(RULE, _rule("globs: *.json"))
    after = classify_instruction(RULE, _rule("globs: *.yaml"))

    assert before is not None and after is not None
    assert before.sha256 != after.sha256


def test_an_alias_anywhere_else_still_refuses() -> None:
    result = classify_instruction(RULE, "---\ndescription: *ref\nglobs: *.json\n---\nBody.\n")

    assert result is not None
    assert result.status == "unresolved"


def test_the_rewrite_is_cursor_only() -> None:
    """A skill keeps its YAML: the same line is still refused before its unknown key is."""

    result = classify_instruction(
        ".claude/skills/demo/SKILL.md", "---\nname: demo\ndescription: d\nglobs: *.json\n---\nBody.\n"
    )

    assert result is not None
    assert result.reason == "frontmatter_invalid"
