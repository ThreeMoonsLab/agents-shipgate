"""The shared conformance corpus for the MCP registration-idiom readers (#485).

There are two readers. ``agents_shipgate.inputs.mcp_idioms`` is the installed
CLI's; ``tools/shipgate-detect.py`` carries a stdlib-only port of it, because
the zero-install detector is the documented first command run against a
repository that has *not* adopted Shipgate — which is every vendor MCP server —
and a detector that answered "not an agent project" there sent the maintainer
away from the one route the CLI had just gained.

A second implementation of a load-bearing matcher is the recurring bug class in
this repository. This module is the reason the duplication is affordable: every
case either reader has ever been asked about lives here once, and both are
driven through all of it —

* :mod:`tests.test_mcp_idioms` checks the package reader against the expected
  names, omissions and anomalies recorded below;
* :mod:`tests.test_zero_install_detector` checks the two readers against *each
  other* on the same inputs, comparing every field of every site, span
  included, plus the path predicate and both escape grammars.

So a case can only change its answer in one reader if it changes in the other
too. Add a case here, not in either test file: a case added to one test is a
case the other reader was never asked.
"""

from __future__ import annotations


class SourceCase:
    """One source text both readers are asked about, with no expectation.

    The expectation for these lives in the test that owns the regression;
    what the corpus guarantees is that the *input* reaches both readers.
    """

    def __init__(self, case: str, language: str, text: str) -> None:
        self.case = case
        self.language = language
        self.text = text


# --- Positive samples, one per idiom ----------------------------------------

class Sample:
    """One idiom's canonical registration, and the name it must yield."""

    def __init__(self, idiom: str, language: str, text: str, name: str) -> None:
        self.idiom = idiom
        self.language = language
        self.text = text
        self.name = name


POSITIVE_SAMPLES: dict[str, Sample] = {
    "ts_static_tool_name": Sample(
        "ts_static_tool_name",
        "typescript",
        'export class DropDatabaseTool extends MongoDBToolBase {\n'
        '    static toolName = "drop-database";\n'
        '    public description = "Removes the specified database";\n'
        '    static operationType: OperationType = "delete";\n'
        "}\n",
        "drop-database",
    ),
    "ts_sdk_register_tool": Sample(
        "ts_sdk_register_tool",
        "typescript",
        'server.registerTool("search_docs", { inputSchema: shape }, handler);\n',
        "search_docs",
    ),
    "go_must_tool": Sample(
        "go_must_tool",
        "go",
        "var UpdateIncident = mcpgrafana.MustTool(\n"
        '\t"update_incident",\n'
        '\t"Update an incident",\n'
        "\tupdateIncident,\n"
        ")\n",
        "update_incident",
    ),
    "go_new_tool": Sample(
        "go_new_tool",
        "go",
        'tool := mcp.NewTool("list_workspaces", mcp.WithDescription("List them"))\n',
        "list_workspaces",
    ),
    "go_tool_struct": Sample(
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


# --- Adversarial sweep ------------------------------------------------------
#
# Each case names the fail-open or fail-closed it prevents. `expected_names` is
# the complete set the reader may report; `expected_unresolved` the complete
# set of omissions it must record. Both are exact: a case that adds an
# unexpected omission is as wrong as one that loses a tool.

ADVERSARIAL: list[tuple[str, str, str, list[str], list[str]]] = [
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
        "a Go octal escape decodes with Go's grammar, not JavaScript's",
        "go",
        'mcpgrafana.MustTool("delete\\137all", desc, handler)\n',
        ["delete_all"],
        [],
    ),
    (
        "a Go hex and unicode escape decode exactly",
        "go",
        'mcpgrafana.MustTool("\\x61\\u0062c", desc, handler)\n',
        ["abc"],
        [],
    ),
    (
        "an escape Go does not define is refused, not guessed",
        "go",
        'mcpgrafana.MustTool("delete\\qall", desc, handler)\n',
        [],
        ["name_not_literal"],
    ),
    (
        "a truncated Go octal escape is refused",
        "go",
        'mcpgrafana.MustTool("delete\\13", desc, handler)\n',
        [],
        ["name_not_literal"],
    ),
    (
        "a TypeScript hex escape decodes, and octal is refused",
        "typescript",
        'server.registerTool("\\x61bc", {}, h);\n'
        'server.registerTool("de\\137f", {}, h);\n',
        ["abc"],
        ["name_not_literal"],
    ),
    (
        "a regex beginning an if body cannot register a tool",
        "typescript",
        'if (ok) /\\.registerTool("fake", handler)/.test(value);\n'
        'server.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        "a regex beginning a while body cannot register a tool",
        "typescript",
        'while (ok) /\\.registerTool("fake", h)/.test(v);\n'
        'server.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        "division after a call is still division",
        "typescript",
        "const ratio = total(a) / scale(b) / 2;\n"
        'server.registerTool("real", {}, h);\n',
        ["real"],
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

# --- Paths the reader is and is not allowed to open -------------------------
#
# The path predicate decides which files are the surface at all, so a
# disagreement here is a tool one reader can see and the other cannot — before
# any masking happens.

SCANNABLE_PATHS: list[tuple[str, bool]] = [
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
]


# --- Escape grammars --------------------------------------------------------
#
# One decoder shared between two grammars is a silent mistranslation rather
# than a parse error: Go writes an octal escape as three digits, so
# `MustTool("delete\137all", …)` registers `delete_all` and a
# JavaScript-shaped decoder produced `delete137all` — the real action absent
# and an id nobody serves in its place. Anything either grammar does not
# define is refused, because a refusal becomes a recorded omission and a guess
# becomes a wrong tool name.

ESCAPE_CASES: list[tuple[str, str, str | None]] = [
    (r"delete\137all", "go", "delete_all"),
    # The same bytes in TypeScript are a legacy octal whose meaning depends on
    # a strictness mode neither reader tracks, so both refuse.
    (r"delete\137all", "typescript", None),
    (r"\x41\u0042", "go", "AB"),
    (r"\x41\u0042", "typescript", "AB"),
    (r"\u{1F600}", "typescript", "\U0001F600"),
    (r"a\qb", "go", None),
    (r"a\13b", "go", None),
    (r"a\400b", "go", None),
    (r"a\x4", "go", None),
    (r"a\u12", "go", None),
    (r"a\x4", "typescript", None),
    (r"a\u{}", "typescript", None),
    (r"a\1b", "typescript", None),
]


# --- Masking failures -------------------------------------------------------
#
# Past an unterminated string or block comment neither reader can tell code
# from content, so reporting no sites would be indistinguishable from a file
# that registers nothing — the exact ambiguity this input exists to remove.

MASKING_FAILURES: dict[str, SourceCase] = {
    "unterminated_block_comment": SourceCase(
        "unterminated_block_comment",
        "typescript",
        '/* open\nserver.registerTool("real", {}, h);\n',
    ),
    "unterminated_string_resyncs_at_the_line": SourceCase(
        "unterminated_string_resyncs_at_the_line",
        "typescript",
        'const broken = "oops;\n' 'server.registerTool("real", {}, h);\n',
    ),
    "unterminated_go_raw_string": SourceCase(
        "unterminated_go_raw_string",
        "go",
        'var doc = `open\nmcpgrafana.MustTool("real", desc, handler)\n',
    ),
}


# --- Regressions the reader carries scars from ------------------------------
#
# Each of these was a real defect. They are inputs, not assertions: the test
# that owns each one states what it must yield, and the corpus guarantees the
# other reader is asked the same question.

REGRESSIONS: dict[str, SourceCase] = {
    "static_field_outside_a_class": SourceCase(
        "static_field_outside_a_class",
        "typescript",
        'static toolName = "loose";\n',
    ),
    "modifier_between_static_and_the_field": SourceCase(
        "modifier_between_static_and_the_field",
        "typescript",
        'class T { public static readonly toolName: string = "get_response"; }',
    ),
    "class_that_registers_elsewhere": SourceCase(
        "class_that_registers_elsewhere",
        "typescript",
        "export class T extends Base {\n"
        "    static toolName = DYNAMIC_NAME;\n"
        '    register(s) { s.registerTool("inner", {}, h); }\n'
        "}\n",
    ),
    "attribute_assignment_is_not_the_description": SourceCase(
        "attribute_assignment_is_not_the_description",
        "typescript",
        "class T {\n"
        '    constructor() { this.description = "scratch label"; }\n'
        '    static toolName = "t";\n'
        '    public description = "Runs an aggregation";\n'
        "}\n",
    ),
    "masking_preserves_offsets": SourceCase(
        "masking_preserves_offsets",
        "typescript",
        '// comment\n// comment\nclass T { static toolName = "x"; }\n',
    ),
    "no_registration_token": SourceCase(
        "no_registration_token",
        "go",
        "package main\n\nfunc main() {}\n",
    ),
}


# --- Everything both readers are driven through -----------------------------

SOURCE_CASES: tuple[SourceCase, ...] = (
    *(
        SourceCase(f"positive:{idiom}", sample.language, sample.text)
        for idiom, sample in sorted(POSITIVE_SAMPLES.items())
    ),
    *(
        SourceCase(f"adversarial:{case}", language, text)
        for case, language, text, _names, _unresolved in ADVERSARIAL
    ),
    *(
        SourceCase(f"masking:{name}", entry.language, entry.text)
        for name, entry in sorted(MASKING_FAILURES.items())
    ),
    *(
        SourceCase(f"regression:{name}", entry.language, entry.text)
        for name, entry in sorted(REGRESSIONS.items())
    ),
)
