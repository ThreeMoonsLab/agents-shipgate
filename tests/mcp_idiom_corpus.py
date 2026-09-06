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

* :mod:`tests.test_mcp_idioms` checks the package reader against what each
  case must yield — the adversarial sweep records its expected names and
  omissions inline, and the named tests own the rest;
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
    "py_fastmcp_decorator": Sample(
        "py_fastmcp_decorator",
        "python",
        "from mcp.server.fastmcp import FastMCP\n"
        "\n"
        'mcp = FastMCP("Redis MCP Server")\n'
        "\n"
        "\n"
        "@mcp.tool()\n"
        "async def dbsize() -> int:\n"
        '    """Get the number of keys stored in the Redis database"""\n'
        "    return 0\n",
        "dbsize",
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
        # A Go raw string is not escape-processed: this registers the literal
        # name `raw\137name`, which is not a tool-name shape, so the site is an
        # omission. Decoding it the way an interpreted string is decoded yields
        # `raw_name` — a name the server does not serve, entered into the
        # catalog as though it did.
        "a Go raw string is not escape-processed",
        "go",
        "mcpgrafana.MustTool(`raw\\137name`, desc, handler)\n",
        [],
        ["implausible_tool_name"],
    ),
    (
        # A `/` inside a character class does not end the regex. Stop tracking
        # the class and the pattern ends early, leaving `']/;` as code — an
        # apostrophe that opens a string and swallows the line, which is how
        # one regex costs the registrations after it.
        "a slash inside a regex character class does not end it",
        "typescript",
        "const sep = /[/\']/;\n" 'server.registerTool("real", {}, h);\n',
        ["real"],
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
        # `mcpgrafana.MustTool(` matches because a `.` is not a word character;
        # a helper of the repository's own whose name merely *ends* in
        # `MustTool` is a different function, and reading it registers a tool
        # nobody serves.
        "an identifier ending in MustTool is not MustTool",
        "go",
        'registerMustTool("ghost", desc, handler)\n',
        [],
        [],
    ),
    (
        "an identifier ending in NewTool is not NewTool",
        "go",
        'buildNewTool("ghost", desc, handler)\n',
        [],
        [],
    ),
    (
        # Same boundary on the struct idiom, and the case the existing
        # `ToolDependencies` sample does not reach: that name has text between
        # `Tool` and the brace, so it never matched. A type whose name *ends*
        # in `Tool` does.
        "a type whose name ends in Tool is not mcp.Tool",
        "go",
        'handler := RegistryTool{Name: "ghost", Description: "x"}\n',
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
        # Semicolon-less TypeScript, where the statement ends at the line
        # break. It is the only shape in which `_literal_is_whole_value` ever
        # reaches the character *after* the literal on a CRLF checkout, so
        # without it the reader's `\r` handling is untested and a Windows
        # checkout silently loses the tool.
        "a static field with no semicolon is still the whole value",
        "typescript",
        'class T {\n    static toolName = "loose_no_semicolon"\n}\n',
        ["loose_no_semicolon"],
        [],
    ),
    (
        # A `${…}` holds code, so a brace inside a string is not a structural
        # brace. Counting it left the substitution open and consumed the rest
        # of the file as one unterminated template: every registration after
        # this line silently gone, and a workspace declaring an MCP dependency
        # reported as "not an agent project" over a brace in a string.
        "a brace inside a template substitution is not a structural brace",
        "typescript",
        'const msg = `Literal brace: ${"{"}`;\n'
        'server.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        # The brace has to be an *opening* one for these four to discriminate.
        # A `}` inside a comment or a regex closes the substitution early and
        # the reader then lands on the same closing backtick anyway, so the
        # first drafts of these cases passed with the branch deleted.
        "a template nested in a substitution closes with its own backtick",
        "typescript",
        'const m = `${`}`}`;\n' 'server.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        "a regex inside a substitution can hold an unbalanced brace",
        "typescript",
        'const m = `${s.replace(/[{]/g, "")}`;\n'
        'server.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        "a block comment inside a substitution can hold an unbalanced brace",
        "typescript",
        'const m = `${x /* { */}`;\n' 'server.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        "a line comment inside a substitution can hold an unbalanced brace",
        "typescript",
        "const m = `${\n  x // {\n}`;\n" 'server.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        # A line break ends the statement only when what follows cannot
        # continue the expression. Accepting the first literal published
        # `safe` for a tool the server registers as `safe_delete` — a name
        # nobody serves, at `medium` confidence.
        "a concatenation continued on the next line is not the first literal",
        "typescript",
        "class T {\n"
        '  static toolName = "safe"\n'
        '    + "_delete";\n'
        "}\n",
        [],
        ["name_not_literal"],
    ),
    (
        "the same continuation in a Go struct field is refused too",
        "go",
        "mcp.Tool{\n" '\tName: "issue_"\n' "\t\t+ verb,\n" "}\n",
        [],
        ["name_not_literal"],
    ),
    (
        # The regex heuristic resolves the keyword in front of the slash from
        # the *masked* source. Read from the raw text, a comment between `if`
        # and its condition hid the keyword, the slash was read as division,
        # and the pattern was scanned as code — a tool invented out of a regex
        # body, which is the one outcome masking exists to make impossible.
        "a comment between if and its condition does not hide the keyword",
        "typescript",
        'if /*comment*/ (ok) /\\.registerTool("ghost", {}, h)/.test(x);\n'
        'server.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        "a comment after typeof does not hide the keyword either",
        "typescript",
        'const t = typeof /*c*/ /\\.registerTool("ghost", {}, h)/;\n'
        'server.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        # A backslash before a line terminator is a continuation, and the CRLF
        # sweep only exercises it through this case: the same file resolved
        # `my_tool` with LF endings and lost the whole registration with CRLF,
        # which JavaScript reads identically.
        "a line continuation inside a name is one escape",
        "typescript",
        'server.registerTool("my\\\n_tool", {}, h);\n',
        ["my_tool"],
        [],
    ),
    (
        "a concatenated Go struct name is not the literal it starts with",
        "go",
        'mcp.Tool{Name: "issue_" + verb, Description: "x"}\n',
        [],
        ["name_not_literal"],
    ),
    # --- Grammar sweep -----------------------------------------------------
    #
    # Constructs neither reader had ever been shown, enumerated from the
    # lexical grammar rather than from a defect. Maintainer review of #485
    # found four lexer defects that six rounds of *code* perturbation had
    # missed, for the reason those rounds could not reach: mutating a branch
    # only re-asks the questions the corpus already holds, so it measures the
    # corpus rather than extending it. Asking "what construct has this reader
    # never been shown?" is the exercise that finds the next one.
    #
    # All of these passed on the first run. They are recorded so the next
    # change to the masker has to keep them passing.
    (
        'a regex holding a backtick does not open a template',
        'typescript',
        'const r = /`/;\nserver.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        'a regex holding a template opener does not open one',
        'typescript',
        'const r = /`${/;\nserver.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        'a string holding a backtick does not open a template',
        'typescript',
        'const s = "`";\nserver.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        'a template holding a line-comment opener is not a comment',
        'typescript',
        'const t = `//`;\nserver.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        'a template holding a block-comment opener is not a comment',
        'typescript',
        'const t = `/*`;\nserver.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        'a comment holding a backtick does not open a template',
        'typescript',
        '// `\nserver.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        'a comment holding an apostrophe does not open a string',
        'typescript',
        '// don\'t\nserver.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        'a block comment holding a quote does not open a string',
        'typescript',
        '/* " */\nserver.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        'a dollar not followed by a brace is template text',
        'typescript',
        'const t = `price: $5`;\nserver.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        'an escaped backtick does not close its template',
        'typescript',
        'const t = `a\\`b`;\nserver.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        'a regex beginning a statement after a block cannot register',
        'typescript',
        'if (a) {} /\\.registerTool("ghost", {}, h)/.test(b);\nserver.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        'division after an index is still division',
        'typescript',
        'const q = a[0] / 2 / b;\nserver.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        'a private static field is not the tool name field',
        'typescript',
        'class T { static #toolName = "ghost"; }\nserver.registerTool("real", {}, h);\n',
        ["real"],
        [],
    ),
    (
        'a Go rune holding a backslash must not desync the lexer',
        'go',
        'if c == \'\\\\\' {\n}\nmcpgrafana.MustTool("real", desc, handler)\n',
        ["real"],
        [],
    ),
    (
        'a Go rune holding an escaped quote must not desync the lexer',
        'go',
        'if c == \'\\\'\' {\n}\nmcpgrafana.MustTool("real", desc, handler)\n',
        ["real"],
        [],
    ),
    (
        'a Go raw string holding a quote is not a string boundary',
        'go',
        'var d = `he said "x"`\nmcpgrafana.MustTool("real", desc, handler)\n',
        ["real"],
        [],
    ),
    (
        'a Go raw string holding a comment opener is not a comment',
        'go',
        'var d = `/*`\nmcpgrafana.MustTool("real", desc, handler)\n',
        ["real"],
        [],
    ),
    (
        'a Go interpreted string holding a backtick is not a raw string',
        'go',
        'var d = "`"\nmcpgrafana.MustTool("real", desc, handler)\n',
        ["real"],
        [],
    ),
    (
        'a python decorator on an alias of the tool method',
        "python",
        'from fastmcp import FastMCP\n'
        '\n'
        'mcp = FastMCP("s")\n'
        'register = mcp.tool\n'
        '\n'
        '\n'
        '@register\n'
        'def aliased() -> None:\n'
        '    pass\n',
        ['aliased'],
        [],
    ),
    (
        'a python tool name built at runtime',
        "python",
        'from fastmcp import FastMCP\n'
        '\n'
        'mcp = FastMCP("s")\n'
        '\n'
        '\n'
        '@mcp.tool(name=NAMESPACE + "delete_instance")\n'
        'def delete_instance() -> None:\n'
        '    pass\n',
        [],
        ['name_not_literal'],
    ),
    (
        'a python decorator applied inside a factory takes its argument',
        "python",
        'from fastmcp import FastMCP\n'
        '\n'
        '\n'
        'def register_all(server: FastMCP) -> None:\n'
        '    @server.tool()\n'
        '    def injected() -> None:\n'
        '        pass\n',
        [],
        ['server_binding_not_proven'],
    ),
    (
        'a python server constructed inside a factory is still a server',
        "python",
        'from fastmcp.server import FastMCP\n'
        '\n'
        '\n'
        'def create_mcp_server(namespace: str = "") -> FastMCP:\n'
        '    mcp: FastMCP = FastMCP("mcp-neo4j-cypher")\n'
        '\n'
        '    @mcp.tool()\n'
        '    def get_neo4j_schema() -> str:\n'
        '        return ""\n'
        '\n'
        '    return mcp\n',
        ['get_neo4j_schema'],
        [],
    ),
    (
        'functools.wraps is not a registration',
        "python",
        'import functools\n'
        '\n'
        'from fastmcp import FastMCP\n'
        '\n'
        'mcp = FastMCP("s")\n'
        '\n'
        '\n'
        'def audited(fn):\n'
        '    @functools.wraps(fn)\n'
        '    def wrapper(*args, **kwargs):\n'
        '        return fn(*args, **kwargs)\n'
        '\n'
        '    return wrapper\n'
        '\n'
        '\n'
        '@mcp.tool()\n'
        '@audited\n'
        'def wrapped() -> None:\n'
        '    pass\n',
        ['wrapped'],
        [],
    ),
    (
        'a conditional python registration is reported like any other',
        "python",
        'from fastmcp import FastMCP\n'
        '\n'
        'mcp = FastMCP("s")\n'
        '\n'
        'if FEATURE_ENABLED:\n'
        '\n'
        '    @mcp.tool()\n'
        '    def gated() -> None:\n'
        '        pass\n',
        ['gated'],
        [],
    ),
    (
        'a re-bound python module symbol is not proven',
        "python",
        'from fastmcp import FastMCP\n'
        '\n'
        'mcp = FastMCP("s")\n'
        'mcp = wrap(mcp)\n'
        '\n'
        '\n'
        '@mcp.tool()\n'
        'def rebound() -> None:\n'
        '    pass\n',
        [],
        ['server_binding_not_proven'],
    ),
    (
        'a decorator on an object that is not a FastMCP server',
        "python",
        'import click\n'
        '\n'
        'app = click.Group()\n'
        '\n'
        '\n'
        '@app.tool()\n'
        'def not_ours() -> None:\n'
        '    pass\n',
        [],
        ['server_binding_not_proven'],
    ),
    (
        'a python decorator reaching through an attribute',
        "python",
        'from mcp.server.fastmcp import FastMCP\n'
        '\n'
        '\n'
        'class RabbitMQModule:\n'
        '    def __init__(self, mcp: FastMCP) -> None:\n'
        '        self.mcp = mcp\n'
        '\n'
        '    def register(self) -> None:\n'
        '        @self.mcp.tool()\n'
        '        def rabbitmq_broker_list_queues() -> list:\n'
        '            return []\n',
        [],
        ['server_binding_not_proven'],
    ),
    (
        'a python star import makes every binding unprovable',
        "python",
        'from fastmcp import FastMCP\n'
        'from plugins import *\n'
        '\n'
        'mcp = FastMCP("s")\n'
        '\n'
        '\n'
        '@mcp.tool()\n'
        'def shadowed() -> None:\n'
        '    pass\n',
        [],
        ['server_binding_not_proven'],
    ),
    (
        'a python resource or prompt decorator is not a tool',
        "python",
        'from fastmcp import FastMCP\n'
        '\n'
        'mcp = FastMCP("s")\n'
        '\n'
        '\n'
        '@mcp.resource("resource://schema/node")\n'
        'def node_schema() -> dict:\n'
        '    return {}\n'
        '\n'
        '\n'
        '@mcp.prompt()\n'
        'def summarise() -> str:\n'
        '    return ""\n'
        '\n'
        '\n'
        '@mcp.tool()\n'
        'def real_tool() -> None:\n'
        '    pass\n',
        ['real_tool'],
        [],
    ),
    (
        'a python parameter shadows the module-level server',
        "python",
        'from fastmcp import FastMCP\n'
        '\n'
        'mcp = FastMCP("s")\n'
        '\n'
        '\n'
        'def outer(mcp):\n'
        '    @mcp.tool()\n'
        '    def inner() -> None:\n'
        '        pass\n',
        [],
        ['server_binding_not_proven'],
    ),
    (
        'a python name a loop also binds is not proven',
        "python",
        'from fastmcp import FastMCP\n'
        '\n'
        'mcp = FastMCP("s")\n'
        '\n'
        'for mcp in servers:\n'
        '    pass\n'
        '\n'
        '\n'
        '@mcp.tool()\n'
        'def looped() -> None:\n'
        '    pass\n',
        [],
        ['server_binding_not_proven'],
    ),
    (
        'a python function name that is not shaped like a tool name',
        "python",
        'from fastmcp import FastMCP\n'
        '\n'
        'mcp = FastMCP("s")\n'
        '\n'
        '\n'
        '@mcp.tool()\n'
        'def _private_helper() -> None:\n'
        '    pass\n',
        [],
        ['implausible_tool_name'],
    ),
    (
        'a python decorator written without parentheses',
        "python",
        'from fastmcp import FastMCP\n'
        '\n'
        'mcp = FastMCP("s")\n'
        '\n'
        '\n'
        '@mcp.tool\n'
        'def bare() -> None:\n'
        '    pass\n',
        ['bare'],
        [],
    ),
    (
        'a positional python tool name',
        "python",
        'from fastmcp import FastMCP\n'
        '\n'
        'mcp = FastMCP("s")\n'
        '\n'
        '\n'
        '@mcp.tool("explicit-name")\n'
        'def positional() -> None:\n'
        '    pass\n',
        ['explicit-name'],
        [],
    ),
    (
        'a nested python registration keeps the outer omission',
        "python",
        'from fastmcp import FastMCP\n'
        '\n'
        'mcp = FastMCP("s")\n'
        '\n'
        '\n'
        '@mcp.tool(name=RUNTIME)\n'
        'def outer() -> None:\n'
        '    @mcp.tool()\n'
        '    def inner() -> None:\n'
        '        pass\n',
        ['inner'],
        ['name_not_literal'],
    ),
    (
        'a python alias for the FastMCP class',
        "python",
        'from fastmcp import FastMCP as Server\n'
        '\n'
        'mcp = Server("s")\n'
        '\n'
        '\n'
        '@mcp.tool()\n'
        'def aliased_class() -> None:\n'
        '    pass\n',
        ['aliased_class'],
        [],
    ),
    (
        'a python alias for the FastMCP package',
        "python",
        'import fastmcp\n'
        '\n'
        'server = fastmcp.FastMCP("s")\n'
        '\n'
        '\n'
        '@server.tool()\n'
        'def module_alias() -> None:\n'
        '    pass\n',
        ['module_alias'],
        [],
    ),
    (
        'a FastMCP-shaped class from some other package',
        "python",
        'from mypackage.fastmcp import FastMCP\n'
        '\n'
        'mcp = FastMCP("s")\n'
        '\n'
        '\n'
        '@mcp.tool()\n'
        'def foreign() -> None:\n'
        '    pass\n',
        [],
        ['server_binding_not_proven'],
    ),
    (
        'a python registration written inside a docstring',
        "python",
        'from fastmcp import FastMCP\n'
        '\n'
        'mcp = FastMCP("s")\n'
        '\n'
        '\n'
        'def documented() -> None:\n'
        '    """Register one like this:\n'
        '\n'
        '    @mcp.tool()\n'
        '    def ghost() -> None:\n'
        '        ...\n'
        '    """\n',
        [],
        [],
    ),
    (
        'a python tool decorator on a class registers nothing',
        "python",
        'from fastmcp import FastMCP\n'
        '\n'
        'mcp = FastMCP("s")\n'
        '\n'
        '\n'
        '@mcp.tool()\n'
        'class NotAFunction:\n'
        '    pass\n',
        [],
        [],
    ),
    (
        'a python server bound by tuple unpacking is not proven',
        "python",
        'from fastmcp import FastMCP\n'
        '\n'
        'mcp, sidecar = FastMCP("s"), None\n'
        '\n'
        '\n'
        '@mcp.tool()\n'
        'def unpacked() -> None:\n'
        '    pass\n',
        [],
        ['server_binding_not_proven'],
    ),
    (
        'a python global declaration is not a proof',
        "python",
        'from fastmcp import FastMCP\n'
        '\n'
        '\n'
        'def build() -> None:\n'
        '    global mcp\n'
        '    mcp = FastMCP("s")\n'
        '\n'
        '    @mcp.tool()\n'
        '    def declared() -> None:\n'
        '        pass\n',
        [],
        ['server_binding_not_proven'],
    ),
    (
        'the official SDK v2 renamed the server class',
        "python",
        'from mcp.server.mcpserver import MCPServer\n'
        '\n'
        'mcp = MCPServer("awslabs.amazon-kendra-index-mcp-server")\n'
        '\n'
        '\n'
        '@mcp.tool(name="KendraListIndexesTool")\n'
        'async def kendra_list_indexes_tool(region: str = "") -> dict:\n'
        '    """List all Amazon Kendra indexes in the specified region."""\n'
        '    return {}\n',
        ['KendraListIndexesTool'],
        [],
    ),
    (
        'a server built by a factory call is not proven',
        "python",
        'from mcp.server.mcpserver import MCPServer\n'
        '\n'
        '\n'
        'def create_server() -> MCPServer:\n'
        '    return MCPServer("s")\n'
        '\n'
        '\n'
        'app = create_server()\n'
        '\n'
        '\n'
        '@app.tool\n'
        'def from_a_factory() -> None:\n'
        '    pass\n',
        [],
        ['server_binding_not_proven'],
    ),
    (
        "the v1 class name is not the v2 module's",
        "python",
        'from mcp.server.mcpserver import FastMCP\n'
        '\n'
        'mcp = FastMCP("s")\n'
        '\n'
        '\n'
        '@mcp.tool()\n'
        'def wrong_pairing() -> None:\n'
        '    pass\n',
        [],
        ['server_binding_not_proven'],
    ),
    (
        'a Context parameter is not a tool parameter',
        "python",
        'from mcp.server.mcpserver import Context, MCPServer\n'
        '\n'
        'mcp = MCPServer("s")\n'
        '\n'
        '\n'
        '@mcp.tool()\n'
        'async def with_context(x: int, ctx: Context) -> str:\n'
        '    return str(x)\n',
        ['with_context'],
        [],
    ),
    (
        'a python parameter named like the server does not shadow it',
        "python",
        'from fastmcp import FastMCP\n'
        '\n'
        'mcp = FastMCP("s")\n'
        '\n'
        '\n'
        '@mcp.tool()\n'
        'def configure(mcp: str) -> None:\n'
        '    pass\n',
        ['configure'],
        [],
    ),
    (
        'a function-local import of a server is an import of a server',
        "python",
        'from fastmcp import FastMCP\n'
        '\n'
        'mcp = FastMCP("s")\n'
        '\n'
        '\n'
        'def register() -> None:\n'
        '    from elsewhere import unrelated\n'
        '\n'
        '    @unrelated.tool()\n'
        '    def nested() -> None:\n'
        '        pass\n',
        [],
        ['server_binding_not_proven'],
    ),
    (
        'a python server unpacked into two names is not proven',
        "python",
        'from fastmcp import FastMCP\n'
        '\n'
        'mcp, sidecar = FastMCP("s")\n'
        '\n'
        '\n'
        '@mcp.tool()\n'
        'def unpacked_from_a_call() -> None:\n'
        '    pass\n',
        [],
        ['server_binding_not_proven'],
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
    ("src/tools/hash.py", True),
    ("servers/cypher/src/mcp_neo4j_cypher/server.py", True),
    # Python spells a test file with a prefix and pytest collects it that way,
    # so a suffix-only rule read `test_server.py` as the server itself.
    ("tests/test_server.py", False),
    ("src/tools/test_hash.py", False),
    ("src/tools/hash_test.py", False),
    ("conftest.py", False),
    ("src/conftest.py", False),
    (".venv/lib/python3.12/site-packages/fastmcp/server.py", False),
    ("build/lib/server.py", False),
    (".tox/py312/lib/server.py", False),
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
    # Refused by a guard rather than by falling through: drop the hex check and
    # `int(digits, 16)` raises out of the scan instead of recording an
    # omission, and the caller loses the whole file rather than one name.
    (r"a\u{zz}", "typescript", None),
    (r"a\u{110000}", "typescript", None),
    (r"a\U00110000", "go", None),
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


# --- Source neither reader can read to the end ------------------------------
#
# The Python analogue of a masking failure. Nothing about the file is known
# past a syntax error, so reporting no sites would be indistinguishable from a
# module that registers nothing — the exact ambiguity this input removes.

PARSE_FAILURES: dict[str, SourceCase] = {
    "unparseable_python": SourceCase(
        "unparseable_python",
        "python",
        "from fastmcp import FastMCP\n"
        'mcp = FastMCP("s")\n'
        "\n"
        "@mcp.tool(\n"
        "def truncated() -> None:\n"
        "    pass\n",
    ),
}


# --- Modules whose binding evidence lives in another file -------------------
#
# `redis/mcp-redis` constructs its server in `src/common/server.py` and applies
# all 53 of its decorators in `src/tools/*.py`, so a reader that only ever
# looked at one file at a time would prove nothing about the largest Python
# server in the survey. These are whole trees: the index is built from every
# module, then each is scanned against it.
#
# `names` and `unresolved` are keyed by module path and are exact. `depends_on`
# is what each module reported as the module its proof rested on — the value
# discovery adds to the route so `scan` can repeat the proof `detect` made.


class PythonTree:
    """One tree of modules, and what each must yield when read together."""

    def __init__(
        self,
        case: str,
        modules: dict[str, str],
        names: dict[str, list[str]],
        unresolved: dict[str, list[str]],
        depends_on: dict[str, list[str]],
    ) -> None:
        self.case = case
        self.modules = modules
        self.names = names
        self.unresolved = unresolved
        self.depends_on = depends_on


_SERVER_MODULE = (
    "from mcp.server.fastmcp import FastMCP\n"
    "\n"
    'mcp = FastMCP("Redis MCP Server")\n'
)
_TOOL_MODULE = (
    "from src.common.server import mcp\n"
    "\n"
    "\n"
    "@mcp.tool()\n"
    "async def dbsize() -> int:\n"
    '    """Get the number of keys stored in the Redis database"""\n'
    "    return 0\n"
)
_RELATIVE_TOOL_MODULE = (
    "from .server import mcp\n"
    "\n"
    "\n"
    "@mcp.tool()\n"
    "def sibling() -> None:\n"
    "    pass\n"
)

PYTHON_TREES: list[PythonTree] = [
    PythonTree(
        "the import path is anchored above the scanned root",
        {"src/common/server.py": _SERVER_MODULE, "src/tools/mgmt.py": _TOOL_MODULE},
        {"src/tools/mgmt.py": ["dbsize"]},
        {},
        {"src/tools/mgmt.py": ["src/common/server.py"]},
    ),
    PythonTree(
        # The same repository read from `src/`, which is where `detect`'s
        # common-ancestor route points. One import, two path spellings.
        "the scanned root is inside the import path",
        {"common/server.py": _SERVER_MODULE, "tools/mgmt.py": _TOOL_MODULE},
        {"tools/mgmt.py": ["dbsize"]},
        {},
        {"tools/mgmt.py": ["common/server.py"]},
    ),
    PythonTree(
        # The import is inside the function that registers, which is how a
        # module avoids an import cycle with the one that builds the server.
        "a server imported inside a function is still a server",
        {
            "pkg/server.py": _SERVER_MODULE,
            "pkg/tools.py": (
                "def register() -> None:\n"
                "    from .server import mcp\n"
                "\n"
                "    @mcp.tool()\n"
                "    def deferred() -> None:\n"
                "        pass\n"
            ),
        },
        {"pkg/tools.py": ["deferred"]},
        {},
        {"pkg/tools.py": ["pkg/server.py"]},
    ),
    PythonTree(
        "a relative import resolves against its own package",
        {"pkg/server.py": _SERVER_MODULE, "pkg/tools.py": _RELATIVE_TOOL_MODULE},
        {"pkg/tools.py": ["sibling"]},
        {},
        {"pkg/tools.py": ["pkg/server.py"]},
    ),
    PythonTree(
        # Two modules in the tree could be the one imported, and picking either
        # is a guess about whether the decorator registers a tool at all.
        #
        # Both candidates have to *match*, which is the trap: a first draft
        # used `a/common/server.py` and `b/common/server.py` against an import
        # of `src.common.server`, where neither side's segments are a suffix of
        # the other's — so the case proved "no match" and passed with the
        # uniqueness rule deleted.
        "an ambiguous import proves nothing",
        {
            "src/common/server.py": _SERVER_MODULE,
            "vendored/src/common/server.py": _SERVER_MODULE,
            "src/tools/mgmt.py": _TOOL_MODULE,
        },
        {},
        {"src/tools/mgmt.py": ["server_binding_not_proven"]},
        {},
    ),
    PythonTree(
        "an import from a module that exports no server proves nothing",
        {
            "src/common/server.py": "mcp = object()\n",
            "src/tools/mgmt.py": _TOOL_MODULE,
        },
        {},
        {"src/tools/mgmt.py": ["server_binding_not_proven"]},
        {},
    ),
    PythonTree(
        # The module *is* indexed and does export a server — under a different
        # name. Resolving the module is only half the question, and this is
        # the half a case whose target module exports nothing cannot reach.
        "an import of a name that module does not export as a server",
        {
            "src/common/server.py": _SERVER_MODULE,
            "src/tools/mgmt.py": (
                "from src.common.server import helper\n"
                "\n"
                "\n"
                "@helper.tool()\n"
                "def borrowed() -> None:\n"
                "    pass\n"
            ),
        },
        {},
        {"src/tools/mgmt.py": ["server_binding_not_proven"]},
        {},
    ),
    PythonTree(
        # A factory's local is a server, and it is not reachable by name from
        # another module — so it must not enter the index and lend its name to
        # an import of something else entirely.
        "a server built inside a factory is not an export",
        {
            "src/common/server.py": (
                "from fastmcp import FastMCP\n"
                "\n"
                "\n"
                "def build() -> FastMCP:\n"
                '    mcp = FastMCP("s")\n'
                "    return mcp\n"
            ),
            "src/tools/mgmt.py": _TOOL_MODULE,
        },
        {},
        {"src/tools/mgmt.py": ["server_binding_not_proven"]},
        {},
    ),
]


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
        "    constructor() {\n"
        '        this.description = "scratch label";\n'
        '        this.operationType = "delete";\n'
        "    }\n"
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
    # `col_offset` is a UTF-8 *byte* offset into the line while every other
    # offset this reader publishes counts characters, so a non-ASCII character
    # before the decorator moves the two apart.
    "python_offsets_are_characters_not_bytes": SourceCase(
        "python_offsets_are_characters_not_bytes",
        "python",
        "from fastmcp import FastMCP\n"
        "\n"
        'mcp = FastMCP("s")\n'
        "\n"
        "\n"
        # On the decorator's *own* line: `col_offset` counts bytes within one
        # line, so a non-ASCII character anywhere else in the file moves
        # nothing and a case that put it three lines up passed either way.
        '@mcp.tool(description="süß — naïve café")\n'
        "def measured() -> None:\n"
        "    pass\n",
    ),
    # An empty signature and a signature this idiom does not read are two
    # different claims, and the adapter turns them into two different schemas.
    # ``*args``/``**kwargs`` are not schema properties, and the adversarial
    # sweep asserts names and omissions only — so this lives here, where a
    # named test can state what the signature must come back as.
    "python_variadic_signature": SourceCase(
        "python_variadic_signature",
        "python",
        "from fastmcp import FastMCP\n"
        "\n"
        'mcp = FastMCP("s")\n'
        "\n"
        "\n"
        "@mcp.tool()\n"
        "def forwarding(query: str, *filters: str, **options: str) -> str:\n"
        "    return query\n",
    ),
    "python_tool_with_no_parameters": SourceCase(
        "python_tool_with_no_parameters",
        "python",
        "from fastmcp import FastMCP\n"
        "\n"
        'mcp = FastMCP("s")\n'
        "\n"
        "\n"
        "@mcp.tool()\n"
        "def nullary() -> None:\n"
        "    pass\n",
    ),
    "python_description_beats_the_docstring": SourceCase(
        "python_description_beats_the_docstring",
        "python",
        "from fastmcp import FastMCP\n"
        "\n"
        'mcp = FastMCP("s")\n'
        "\n"
        "\n"
        '@mcp.tool(description="What the server publishes")\n'
        "def described() -> None:\n"
        '    """What the author wrote for a reader."""\n',
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
        SourceCase(f"parse:{name}", entry.language, entry.text)
        for name, entry in sorted(PARSE_FAILURES.items())
    ),
    *(
        SourceCase(f"regression:{name}", entry.language, entry.text)
        for name, entry in sorted(REGRESSIONS.items())
    ),
)
