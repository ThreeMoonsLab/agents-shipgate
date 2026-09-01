"""The built-in MCP registration-idiom registry and its reader (#431).

The reader turns source text into tool names, so every test here is really
asking one of two questions: *can it be made to invent a tool?* and *can it be
made to lose one silently?* The second is the reason this input exists — the
gap it closes is three vendor servers reported as "not an agent project" — so a
construct it cannot resolve has to become a recorded omission, never a gap in
the output nobody can see.

The adversarial sweep at the bottom is the probe list #393 requires of anything
that publishes an extraction claim. Eight of its cases were fail-open in the
first draft of this reader.
"""

from __future__ import annotations

import pytest

from agents_shipgate.inputs.mcp_idioms import (
    DIFF_TOKENS,
    IDIOMS,
    IDIOMS_BY_ID,
    LANGUAGE_EXTENSIONS,
    OMISSION_REASONS,
    PREFILTER_TOKEN,
    is_scannable_path,
    language_for_path,
    mask_source,
    scan_source,
)


def _names(text: str, language: str = "typescript") -> list[str]:
    return [site.name for site in scan_source(text, language).sites if site.name]


def _unresolved(text: str, language: str = "typescript") -> list[str]:
    return [
        site.unresolved_reason
        for site in scan_source(text, language).sites
        if site.name is None
    ]


# --- Registry shape ---------------------------------------------------------


def test_every_idiom_has_a_language_this_reader_can_read():
    for idiom in IDIOMS:
        assert idiom.language in LANGUAGE_EXTENSIONS
        assert idiom.id == idiom.id.lower()
        assert idiom.diff_tokens


def test_idiom_ids_are_unique():
    assert len(IDIOMS_BY_ID) == len(IDIOMS)


def test_the_prefilter_cannot_hide_an_idiom_this_reader_matches():
    """``scan_source`` skips a file without the prefilter token.

    That shortcut is only sound while every idiom's pattern requires the token.
    The first draft put the prefilter at the *call site* and keyed it off the
    trigger catalog's ``diff_tokens`` — which do not include the substring
    ``static readonly toolName`` carries — and four MongoDB tools were reported
    by ``scan`` and never by ``detect``.
    """

    for sample in _POSITIVE_SAMPLES.values():
        assert PREFILTER_TOKEN in sample.text.lower(), sample.idiom


def test_jsx_is_out_of_scope_so_prose_never_opens_a_string():
    """JSX puts prose in code position; an apostrophe would read as a quote."""

    assert ".tsx" not in LANGUAGE_EXTENSIONS["typescript"]
    assert ".jsx" not in LANGUAGE_EXTENSIONS["typescript"]
    assert language_for_path("app/Panel.tsx") is None


@pytest.mark.parametrize(
    ("path", "scannable"),
    [
        ("pkg/github/issues.go", True),
        ("src/tools/aggregate.ts", True),
        ("server.mjs", True),
        ("pkg/github/issues_test.go", False),
        ("src/tools/aggregate.test.ts", False),
        ("src/tools/aggregate.spec.ts", False),
        ("tests/helpers/fake.ts", False),
        ("__tests__/fake.ts", False),
        ("node_modules/sdk/index.ts", False),
        ("vendor/other/tools.go", False),
        ("testdata/sample.go", False),
        ("README.md", False),
    ],
)
def test_scannable_paths(path: str, scannable: bool):
    assert is_scannable_path(path) is scannable


# --- Positive samples, one per idiom ----------------------------------------


class _Sample:
    def __init__(self, idiom: str, language: str, text: str, name: str) -> None:
        self.idiom = idiom
        self.language = language
        self.text = text
        self.name = name


_POSITIVE_SAMPLES: dict[str, _Sample] = {
    "ts_static_tool_name": _Sample(
        "ts_static_tool_name",
        "typescript",
        'export class DropDatabaseTool extends MongoDBToolBase {\n'
        '    static toolName = "drop-database";\n'
        '    public description = "Removes the specified database";\n'
        '    static operationType: OperationType = "delete";\n'
        "}\n",
        "drop-database",
    ),
    "ts_sdk_register_tool": _Sample(
        "ts_sdk_register_tool",
        "typescript",
        'server.registerTool("search_docs", { inputSchema: shape }, handler);\n',
        "search_docs",
    ),
    "go_must_tool": _Sample(
        "go_must_tool",
        "go",
        "var UpdateIncident = mcpgrafana.MustTool(\n"
        '\t"update_incident",\n'
        '\t"Update an incident",\n'
        "\tupdateIncident,\n"
        ")\n",
        "update_incident",
    ),
    "go_new_tool": _Sample(
        "go_new_tool",
        "go",
        'tool := mcp.NewTool("list_workspaces", mcp.WithDescription("List them"))\n',
        "list_workspaces",
    ),
    "go_tool_struct": _Sample(
        "go_tool_struct",
        "go",
        "return NewTool(\n"
        "\tToolsetMetadataIssues,\n"
        "\tmcp.Tool{\n"
        '\t\tName:        "issue_read",\n'
        '\t\tDescription: "Get information about an issue",\n'
        "\t\tAnnotations: &mcp.ToolAnnotations{\n"
        '\t\t\tTitle:        "Get issue details",\n'
        "\t\t\tReadOnlyHint: true,\n"
        "\t\t},\n"
        "\t},\n"
        "\thandler,\n"
        ")\n",
        "issue_read",
    ),
}


def test_every_idiom_has_a_positive_sample():
    assert set(_POSITIVE_SAMPLES) == set(IDIOMS_BY_ID)


@pytest.mark.parametrize("idiom_id", sorted(_POSITIVE_SAMPLES))
def test_positive_sample_resolves_exactly_its_own_tool(idiom_id: str):
    sample = _POSITIVE_SAMPLES[idiom_id]
    result = scan_source(sample.text, sample.language)
    resolved = [site for site in result.sites if site.name is not None]
    assert [site.name for site in resolved] == [sample.name]
    assert resolved[0].idiom == idiom_id
    assert result.anomalies == ()


@pytest.mark.parametrize("idiom_id", sorted(_POSITIVE_SAMPLES))
def test_published_diff_tokens_appear_in_the_sample_they_route(idiom_id: str):
    """The trigger catalog routes on these tokens, so they must be real.

    A token nobody can produce routes nothing, and nothing in the catalog file
    would say so — the rule would read as coverage it does not have.
    """

    sample = _POSITIVE_SAMPLES[idiom_id]
    for token in IDIOMS_BY_ID[idiom_id].diff_tokens:
        assert token in sample.text, (idiom_id, token)


def test_ts_static_field_reads_the_sibling_operation_class_and_description():
    sample = _POSITIVE_SAMPLES["ts_static_tool_name"]
    site = scan_source(sample.text, "typescript").sites[0]
    assert site.operation_type == "delete"
    assert site.description == "Removes the specified database"


def test_a_go_struct_description_is_read_only_from_its_own_level():
    sample = _POSITIVE_SAMPLES["go_tool_struct"]
    site = scan_source(sample.text, "go").sites[0]
    assert site.description == "Get information about an issue"


def test_a_static_field_outside_a_class_still_names_its_tool():
    """The enclosing block supplies siblings; its absence is not a failure."""

    sites = scan_source('static toolName = "loose";\n', "typescript").sites
    assert [site.name for site in sites] == ["loose"]
    assert sites[0].operation_type is None


def test_a_modifier_between_static_and_the_field_is_read():
    """``public static readonly toolName: string = "…"`` is MongoDB's spelling
    in four of its packages, and it is not ``static toolName``."""

    text = 'class T { public static readonly toolName: string = "get_response"; }'
    assert _names(text) == ["get_response"]


# --- Adversarial sweep ------------------------------------------------------
#
# Each case names the fail-open or fail-closed it prevents. `expected_names` is
# the complete set the reader may report; `expected_unresolved` the complete
# set of omissions it must record. Both are exact: a case that adds an
# unexpected omission is as wrong as one that loses a tool.

_ADVERSARIAL: list[tuple[str, str, str, list[str], list[str]]] = [
    (
        "a line comment is not code",
        "typescript",
        '// server.registerTool("ghost", {}, h);\n'
        'server.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        "a doc comment example is not a registration",
        "typescript",
        "/**\n"
        " * Define a tool class:\n"
        ' *   static toolName = "my-custom-tool";\n'
        " */\n"
        'class T { static toolName = "real"; }\n',
        ["real"],
        [],
    ),
    (
        "a name inside another string is not a registration",
        "typescript",
        'const doc = \'static toolName = "ghost";\';\n'
        'class T { static toolName = "real"; }\n',
        ["real"],
        [],
    ),
    (
        "a template literal body is not code",
        "typescript",
        "const doc = `server.registerTool(\"ghost\", {}, h)`;\n"
        'server.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        "a name built from a constant is unenumerated, not absent",
        "typescript",
        "class T { static toolName = EXPORT_TOOL_NAME; }\n",
        [],
        ["name_not_literal"],
    ),
    (
        "a template substitution is not a constant name",
        "typescript",
        "server.registerTool(`${prefix}_search`, {}, h);\n",
        [],
        ["name_not_literal"],
    ),
    (
        "a concatenated name is not the literal it starts with",
        "typescript",
        'server.registerTool("search_" + suffix, {}, h);\n',
        [],
        ["name_not_literal"],
    ),
    (
        "a concatenated static field is not the literal it starts with",
        "typescript",
        'class T { static toolName = "search_" + SUFFIX; }\n',
        [],
        ["name_not_literal"],
    ),
    (
        "a one-argument call is a lookup, not a registration",
        "typescript",
        'const t = registry.tool("issues");\n',
        [],
        [],
    ),
    (
        "a one-argument call with a computed key is still a lookup",
        "typescript",
        "const t = registry.tool(key);\n",
        [],
        [],
    ),
    (
        "a literal that is not shaped like a tool name is not one",
        "typescript",
        'panel.tool("Search the web for a query", handler);\n',
        [],
        ["implausible_tool_name"],
    ),
    (
        "a regex literal containing a quote must not desync the lexer",
        "typescript",
        "const quote = /\"/;\n" 'server.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        "a regex literal containing a comment opener must not blank the file",
        "typescript",
        "const path = /a\\/\\/b/;\n" 'server.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        "a URL inside a string is not a line comment",
        "typescript",
        'const url = "https://example.test/x";\n'
        'server.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        "an escaped quote does not end its string",
        "typescript",
        'const s = "he said \\"registerTool\\"";\n'
        'server.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        "a method named toolName is not a field",
        "typescript",
        'class T { toolName() { return "ghost"; } }\n',
        [],
        [],
    ),
    (
        "a wrapper naming its tool one argument later reports one site",
        "go",
        "return NewTool(meta, mcp.Tool{Name: \"issue_read\"}, handler)\n",
        ["issue_read"],
        [],
    ),
    (
        "a wrapper naming nothing reports exactly one omission",
        "go",
        "return NewTool(meta, mcp.Tool{Name: name}, handler)\n",
        [],
        ["name_not_literal"],
    ),
    (
        "a nested annotation Name is not the tool's name",
        "go",
        'mcp.Tool{Annotations: &mcp.ToolAnnotations{Name: "annotation"}}\n',
        [],
        [],
    ),
    (
        "a slice of tool structs names every element",
        "go",
        'tools := []mcp.Tool{{Name: "a", Description: "x"}, {Name: "b"}}\n',
        ["a", "b"],
        [],
    ),
    (
        "a Go raw string names its tool",
        "go",
        "mcpgrafana.MustTool(`raw_name`, desc, handler)\n",
        ["raw_name"],
        [],
    ),
    (
        "a rune literal holding a quote must not desync the lexer",
        "go",
        "if c == '\"' {\n}\n" 'mcpgrafana.MustTool("real", desc, handler)\n',
        ["real"],
        [],
    ),
    (
        "a Go comment is not a registration",
        "go",
        '// mcpgrafana.MustTool("ghost", desc, handler)\n'
        'mcpgrafana.MustTool("real", desc, handler)\n',
        ["real"],
        [],
    ),
    (
        "NewToolResultError is not NewTool",
        "go",
        'return utils.NewToolResultError("boom"), nil\n',
        [],
        [],
    ),
    (
        "a type whose name merely ends in Tool is not a tool struct",
        "go",
        'deps := ToolDependencies{Name: "not_a_tool"}\n',
        [],
        [],
    ),
    (
        "a concatenated Go struct name is not the literal it starts with",
        "go",
        'mcp.Tool{Name: "issue_" + verb, Description: "x"}\n',
        [],
        ["name_not_literal"],
    ),
]


@pytest.mark.parametrize(
    ("case", "language", "text", "expected_names", "expected_unresolved"),
    _ADVERSARIAL,
    ids=[case[0] for case in _ADVERSARIAL],
)
def test_adversarial_constructs(
    case: str,
    language: str,
    text: str,
    expected_names: list[str],
    expected_unresolved: list[str],
):
    result = scan_source(text, language)
    assert sorted(site.name for site in result.sites if site.name) == sorted(
        expected_names
    ), case
    assert sorted(
        site.unresolved_reason for site in result.sites if site.name is None
    ) == sorted(expected_unresolved), case
    for site in result.sites:
        if site.unresolved_reason is not None:
            assert site.unresolved_reason in OMISSION_REASONS


# --- Regressions from review ------------------------------------------------


def test_a_class_that_registers_elsewhere_keeps_its_own_omission():
    """A lookup scope is not a wrapper.

    The unresolved field site needs the class body to find its sibling
    `operationType` and `description` literals. Carrying that body as the
    site's *span* made the containment rule read any registration written
    inside the class as "the same registration", so a class whose `toolName`
    is built at runtime lost its omission the moment its body also called
    `.registerTool(` — neither enumerated nor recorded, which is the silent
    miss this input exists to end.
    """

    text = (
        "export class T extends Base {\n"
        "    static toolName = DYNAMIC_NAME;\n"
        '    register(s) { s.registerTool("inner", {}, h); }\n'
        "}\n"
    )
    result = scan_source(text, "typescript")

    assert sorted(site.name for site in result.sites if site.name) == ["inner"]
    assert [
        site.unresolved_reason for site in result.sites if site.name is None
    ] == ["name_not_literal"]


def test_an_attribute_assignment_is_not_the_description_field():
    """`this.description = …` is not the class's description.

    The lookbehind admitted a leading dot, and `_first_literal_in` returns the
    first match in the body, so an assignment in a constructor won over the
    real field and published a description that is not the tool's — into the
    report and into the declaration questionnaire a reviewer answers from.
    """

    text = (
        "class T {\n"
        '    constructor() { this.description = "scratch label"; }\n'
        '    static toolName = "t";\n'
        '    public description = "Runs an aggregation";\n'
        "}\n"
    )
    site = scan_source(text, "typescript").sites[0]

    assert site.name == "t"
    assert site.description == "Runs an aggregation"


# --- Masking failures -------------------------------------------------------


def test_an_unterminated_block_comment_is_an_anomaly_not_silence():
    """Past an unterminated comment nothing is known, so say so.

    Reporting no sites would be indistinguishable from a file that registers
    nothing — the exact ambiguity this input exists to remove.
    """

    result = scan_source('/* open\nserver.registerTool("real", {}, h);\n', "typescript")
    assert result.sites == ()
    assert result.anomalies == ("unterminated_block_comment",)


def test_an_unterminated_string_is_an_anomaly_and_resyncs_at_the_line():
    result = scan_source(
        'const broken = "oops;\n' 'server.registerTool("real", {}, h);\n',
        "typescript",
    )
    assert "unterminated_string" in result.anomalies
    assert [site.name for site in result.sites] == ["real"]


def test_an_unterminated_go_raw_string_is_an_anomaly():
    result = scan_source(
        'var doc = `open\nmcpgrafana.MustTool("real", desc, handler)\n', "go"
    )
    assert result.anomalies == ("unterminated_string",)
    # And the registration past it is not reported, because past the unclosed
    # raw string this reader cannot tell code from content.
    assert result.sites == ()


def test_masking_preserves_offsets_so_line_numbers_are_the_file_s():
    text = "// comment\n// comment\nclass T { static toolName = \"x\"; }\n"
    masked = mask_source(text, "typescript")
    assert len(masked.masked) == len(text)
    assert scan_source(text, "typescript").sites[0].line == 3


def test_a_file_without_the_prefilter_token_is_answered_without_masking():
    assert scan_source("package main\n\nfunc main() {}\n", "go") == scan_source(
        "", "go"
    )


# --- The published token list ----------------------------------------------


def test_published_diff_tokens_are_the_union_of_the_idioms():
    assert DIFF_TOKENS == tuple(
        sorted({token for idiom in IDIOMS for token in idiom.diff_tokens})
    )
