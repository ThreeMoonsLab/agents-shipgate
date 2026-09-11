"""#658: a Go tool's description, when it is not a bare literal in a struct.

`github/github-mcp-server` declares 114 tools through `mark3labs/mcp-go`,
whose registration shape is a call with option functions:

    mcp.NewTool("get_job",
        mcp.WithDescription(t("TOOL_GET_JOB_DESCRIPTION", "Get details …")),
    )

Two separate reasons this read nothing. The description is an *argument to
an option*, not a `Description:` struct field, and the only Go description
extractor looked for the field — so even a plain literal was missed. And
every description in that repository is wrapped in a translation helper,
so finding the literal means looking through the call.

The result was 114 tools with no description and 114
`SHIP-DOC-MISSING-DESCRIPTION` findings on a repository that documents
every tool it ships. The cases below are the read, and — as much as they
are the read — the refusals: this must not invent a description out of any
nested call that happens to contain a string.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents_shipgate.inputs.mcp_server_source import load_mcp_server_source
from agents_shipgate.schemas.manifest import ToolSourceConfig

GO_MOD = "module github.com/example/srv\nrequire github.com/mark3labs/mcp-go v0.1.0\n"


def _tools(tmp_path: Path, body: str) -> dict[str, str | None]:
    (tmp_path / "go.mod").write_text(GO_MOD, encoding="utf-8")
    package = tmp_path / "pkg"
    package.mkdir(exist_ok=True)
    (package / "tools.go").write_text(
        'package pkg\n\nimport "github.com/mark3labs/mcp-go/mcp"\n\n' + body,
        encoding="utf-8",
    )
    loaded = load_mcp_server_source(
        ToolSourceConfig(id="srv", type="mcp_server_source", path="."), tmp_path
    )
    return {tool.name: getattr(tool, "description", None) for tool in loaded.tools}


#: (case, Go expression for the description argument, what must be read).
#: `None` means the reader must decline — every one of these is a shape
#: where a literal is reachable but is not the tool's description.
CASES: tuple[tuple[str, str, str | None], ...] = (
    ("plain_literal", '"A plain literal."', "A plain literal."),
    (
        "screaming_snake_key",
        't("TOOL_GET_JOB_DESCRIPTION", "Get details of a workflow job.")',
        "Get details of a workflow job.",
    ),
    (
        "dotted_key",
        't("tools.get_job.description", "Dotted key default.")',
        "Dotted key default.",
    ),
    # --- refusals ---------------------------------------------------------
    # Two literals, but the first is prose, so this is not a lookup with a
    # fallback. Reading it would publish `invented` as documentation the
    # tool's author never wrote.
    ("prose_first_argument", 'wrap("some prose here", "invented")', None),
    # A bare word is not a key either.
    ("bare_word_key", 'wrap("a", "b")', None),
    # The key is computed, so nothing establishes that the second literal is
    # this tool's default rather than some other tool's.
    ("variable_key", "t(keyVar, \"Default for a key we cannot see.\")", None),
    # Three arguments is not the `t(key, default)` shape.
    ("three_arguments", 't("TOOL_F_DESCRIPTION", "One.", "Two.")', None),
    # No literal at all.
    ("computed", "buildDesc(cfg)", None),
    # A qualified call is not a bare translation helper.
    ("qualified_call", 'fmt.Sprintf("%s tool", name)', None),
    # The literal is part of a larger expression, so it is not the value.
    ("concatenation", '"Part one " + suffix', None),
)


@pytest.mark.parametrize(
    ("case", "expression", "expected"), CASES, ids=[case[0] for case in CASES]
)
def test_description_is_read_or_refused(
    tmp_path: Path, case: str, expression: str, expected: str | None
) -> None:
    body = f'func F() mcp.Tool {{ return mcp.NewTool("{case}", mcp.WithDescription({expression})) }}\n'

    assert _tools(tmp_path, body) == {case: expected}


def test_the_github_mcp_server_shape_end_to_end(tmp_path: Path) -> None:
    """The reported repository's actual registration, reproduced."""

    body = """
func GetJob(t TranslationHelperFunc) mcp.Tool {
	return mcp.NewTool("get_job",
		mcp.WithDescription(t("TOOL_GET_JOB_DESCRIPTION", "Get details of a specific workflow job.")),
		mcp.WithReadOnlyHintAnnotation(true),
	)
}

func DeleteJob(t TranslationHelperFunc) mcp.Tool {
	return mcp.NewTool("delete_job",
		mcp.WithDescription(t("TOOL_DELETE_JOB_DESCRIPTION", "Delete one workflow job.")),
	)
}
"""

    assert _tools(tmp_path, body) == {
        "get_job": "Get details of a specific workflow job.",
        "delete_job": "Delete one workflow job.",
    }


def test_every_tool_in_a_translated_server_is_documented(tmp_path: Path) -> None:
    """The headline: no tool in this shape lacks a description.

    `SHIP-DOC-MISSING-DESCRIPTION` fires per undocumented tool, so this is
    the 114-findings defect stated as a property.
    """

    body = "".join(
        f'func F{index}() mcp.Tool {{ return mcp.NewTool("tool_{index}", '
        f'mcp.WithDescription(t("TOOL_{index}_DESCRIPTION", "Tool {index} does a thing."))) }}\n'
        for index in range(12)
    )

    described = _tools(tmp_path, body)

    assert len(described) == 12
    assert [name for name, text in described.items() if not text] == []


def test_the_struct_shape_still_reads_its_field(tmp_path: Path) -> None:
    """The other Go idiom is untouched; this change adds a reader, not a
    replacement."""

    body = """
func Register(server *mcp.Server) {
	server.AddTool(&mcp.Tool{
		Name:        "read_dashboard",
		Description: "Read one dashboard.",
	}, readDashboard)
}
"""

    assert _tools(tmp_path, body) == {"read_dashboard": "Read one dashboard."}
